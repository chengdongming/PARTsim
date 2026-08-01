#!/usr/bin/env python3
"""Replay the frozen T10 spotcheck entry in its original source checkout.

This helper is intentionally process-isolated from the current formal adapter.
Its Python import root is the evidence-declared source commit, and its entry
functions come from the frozen spotcheck script.  It writes only an explicitly
requested non-formal audit JSONL path.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from fractions import Fraction
import importlib.util
import json
import multiprocessing
from pathlib import Path
import sys
import traceback
from typing import Any


_SPOT: Any = None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _initialize(frozen_repo: str, spot_script: str) -> None:
    global _SPOT
    sys.path.insert(0, frozen_repo)
    spec = importlib.util.spec_from_file_location(
        "rta4_frozen_recursive_theta_spotcheck_v2", spot_script,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen spotcheck module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _SPOT = module


def _task_projection(row: Any) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "priority_rank": row.priority_rank,
        "solver_status": row.solver_status.value,
        "kernel_solver_status": row.kernel_solver_status,
        "certification_status": row.certification_status.value,
        "candidate_response_time": row.candidate_response_time,
        "closing_w": row.closing_w,
        "carry_in_values_used": [list(value) for value in row.carry_in_values_used],
        "witness_h": row.witness_h,
        "processor_progress_a": row.processor_progress_a,
        "maximum_blocking_h": row.maximum_blocking_h,
        "witness_sequence": list(row.witness_sequence),
        "checked_w_count": row.checked_w_count,
        "checked_h_count": row.checked_h_count,
        "checked_q_count": row.checked_q_count,
        "envelope_call_count": row.envelope_call_count,
        "solver_call_count": row.solver_call_count,
        "failure_reason": row.failure_reason,
    }


def _method_projection(result: Any) -> dict[str, Any]:
    tasks = [_task_projection(row) for row in result.task_results]
    return {
        "method_id": result.method_id.value,
        "kernel": result.kernel.value,
        "carry_policy": result.carry_policy.value,
        "solver_status": result.solver_status.value,
        "certification_status": result.analysis_certification_status.value,
        "taskset_proven": bool(result.taskset_proven),
        "first_failed_task": result.first_failed_task,
        "failure_reason": result.failure_reason,
        "exact_input_identity": result.exact_input_identity,
        "response_vector": [
            row["candidate_response_time"] for row in tasks
            if row["candidate_response_time"] is not None
        ],
        "carry_trace": [
            {
                "task_id": entry.task_id,
                "priority_rank": entry.priority_rank,
                "theta_by_task": [list(value) for value in entry.theta_by_task],
            }
            for entry in result.carry_trace
        ],
        "task_results": tasks,
    }


def _evidence_projection(method: dict[str, Any]) -> dict[str, Any]:
    return {
        "method_id": method["method_id"],
        "kernel": method["kernel"],
        "carry_policy": method["carry_policy"],
        "solver_status": method["solver_status"],
        "certification_status": method["certification_status"],
        "taskset_proven": method["taskset_proven"],
        "first_failed_task": method["first_failed_task"],
        "failure_reason": method["failure_reason"],
        "response_vector": method["response_vector"],
        "task_results": [
            {
                "task_id": row["task_id"],
                "priority_rank": row["priority_rank"],
                "solver_status": row["solver_status"],
                "candidate_response_time": row["candidate_response_time"],
                "closing_w": row["closing_w"],
                "carry_in_values_used": row["carry_in_values_used"],
                "witness_h": row["witness_h"],
                "witness_sequence": row["witness_sequence"],
                "distinct_h_count": (
                    len(set(row["witness_sequence"]))
                    if row["witness_sequence"] else None
                ),
                "failure_reason": row["failure_reason"],
            }
            for row in method["task_results"]
        ],
    }


def _run_job(job: dict[str, Any]) -> dict[str, Any]:
    if _SPOT is None:
        raise RuntimeError("frozen worker was not initialized")
    key = str(job["cell_key"])
    try:
        record = job["record"]
        e0 = Fraction(str(job["e0"]))
        _SPOT.E0 = e0
        _SPOT.REQUEST_TIMEOUT_SECONDS = int(job["timeout_seconds"])
        tasks = _SPOT.build_tasks(record)
        maximum_deadline = max(task.deadline for task in tasks)
        beta = _SPOT.build_service_curve(maximum_deadline)
        context = _SPOT.build_context(
            taskset_index=int(record["taskset_index"]), tasks=tasks, beta=beta,
        )
        analysis_input = _SPOT.taskset.TasksetAnalysisInput(
            tasks=tasks,
            processors=_SPOT.PROCESSORS,
            e0=e0,
            beta=beta,
            dependency_context=context,
            timeout_seconds=int(job["timeout_seconds"]),
        )
        methods = {}
        evidence = {}
        for label, method_id in _SPOT.METHODS:
            raw = _SPOT.taskset.analyze_method_taskset_v9_3(
                analysis_id=f"frozen-t10-parity:{record['taskset_index']}:{e0}:{label}",
                method_spec=method_id,
                analysis_input=analysis_input,
            )
            projected = _method_projection(raw)
            methods[label] = projected
            evidence[label] = _evidence_projection(projected)
        task_material = [
            {
                "name": task.name,
                "C": task.wcet,
                "D": task.deadline,
                "T": task.period,
                "power": _SPOT.exact_energy.fraction_text(task.power),
            }
            for task in tasks
        ]
        return {
            "cell_key": key,
            "status": "COMPLETED",
            "input": {
                "processors": _SPOT.PROCESSORS,
                "priority_policy": "RM_STRICT_PERIOD_ASCENDING",
                "tasks": task_material,
                "task_order": [task.name for task in tasks],
                "E0": _SPOT.exact_energy.fraction_text(e0),
                "service_prefix": [
                    _SPOT.exact_energy.fraction_text(value) for value in beta
                ],
                "semantic_service_identity": _SPOT.canonical_hash([
                    _SPOT.exact_energy.fraction_text(value) for value in beta
                ]),
                "semantic_power_vector_identity": _SPOT.canonical_hash([
                    (task.name, _SPOT.exact_energy.fraction_text(task.power))
                    for task in tasks
                ]),
                "exact_input_identity": context.exact_input_identity,
                "native_taskset_identity": context.taskset_identity,
                "native_task_definitions_identity": context.task_definitions_identity,
                "native_priority_order_identity": context.priority_order_identity,
                "native_service_curve_identity": context.service_curve_identity,
                "native_power_vector_identity": context.power_vector_identity,
                "numerical_mode": context.numerical_mode,
            },
            "methods": methods,
            "evidence_projection": evidence,
        }
    except Exception as exc:
        return {
            "cell_key": key,
            "status": "SCRIPT_FAILURE",
            "failure": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        }


def _load_jobs(path: Path) -> list[dict[str, Any]]:
    jobs = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RuntimeError(f"job {line_number} is not an object")
            jobs.append(value)
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-repo", type=Path, required=True)
    parser.add_argument("--spot-script", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    frozen_repo = args.frozen_repo.resolve(strict=True)
    spot_script = args.spot_script.resolve(strict=True)
    jobs = _load_jobs(args.jobs.resolve(strict=True))
    if not jobs or args.workers < 1:
        raise RuntimeError("frozen replay requires jobs and positive workers")
    results = []
    if args.workers == 1:
        _initialize(str(frozen_repo), str(spot_script))
        results = [_run_job(job) for job in jobs]
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=args.workers,
            mp_context=context,
            initializer=_initialize,
            initargs=(str(frozen_repo), str(spot_script)),
        ) as pool:
            futures = {pool.submit(_run_job, job): job["cell_key"] for job in jobs}
            for completed, future in enumerate(as_completed(futures), 1):
                results.append(future.result())
                if completed == 1 or completed % 25 == 0 or completed == len(futures):
                    print(
                        f"frozen_replay_progress={completed}/{len(futures)}",
                        flush=True,
                    )
    results.sort(key=lambda row: row["cell_key"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for result in results:
            stream.write(_canonical_json(result) + "\n")
    failures = sum(result["status"] != "COMPLETED" for result in results)
    print(f"frozen_replay_count={len(results)}")
    print(f"frozen_replay_failure_count={failures}")


if __name__ == "__main__":
    main()
