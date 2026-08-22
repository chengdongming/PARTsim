#!/usr/bin/env python3
"""Run the independent priority-energy correlated Scheduler LOAD-CROSS study."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from fractions import Fraction
import json
import multiprocessing
import os
import re
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g
from experiments.v9_3 import scheduler_priority_energy_load_cross as experiment
from experiments.v9_3 import simulation_engine
from experiments.v9_3.parallel_prepare import validate_workers
from experiments.v9_3.performance_outcome import evaluate_outcome
from experiments.v9_3.simulation_engine import SimulationStatus, run_paired_simulation


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_simulation_job(job: dict[str, Any]) -> tuple[Any, str | None]:
    try:
        execution = run_paired_simulation(
            simulation_id_value=str(job["simulation_id"]),
            base_system_path=Path(job["base_system_path"]),
            run_root=Path(job["run_root"]),
            task_payload=job["task_payload"],
            taskset_hash=str(job["material_hash"]),
            processors=int(job["processors"]),
            exact_e0=Fraction(job["exact_e0"]),
            energy_config=job["energy_config"],
            simulation_config=job["simulation_config"],
            scheduler_id=str(job["scheduler_id"]),
            task_energy_factors=job["task_energy_factors"],
            expected_task_power_j_per_tick=job["expected_task_power_j_per_tick"],
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return execution, None


def _initialize_worker(semaphore: Any) -> None:
    simulation_engine._set_trace_parse_semaphore(semaphore)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


_ATTEMPT_DIR_RE = re.compile(r"^attempt_(\d+)$")
_NORMAL_SCIENTIFIC_STATUSES = {
    SimulationStatus.PASS_OBSERVED.value,
    SimulationStatus.DEADLINE_MISS.value,
}


def _read_pid(path: Path) -> int | None:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("pid="):
            try:
                return int(line[4:])
            except ValueError:
                return None
    return None


def _assert_no_live_attempt_lock(request_root: Path) -> None:
    if not request_root.is_dir():
        return
    for lock in request_root.rglob("*.lock"):
        pid = _read_pid(lock)
        if pid is None:
            raise RuntimeError(f"cannot determine lock owner: {lock}")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        raise RuntimeError(f"request has an active execution lock: {lock} (pid={pid})")


def _next_attempt_root(root: Path, request_id: str, attempts: list[dict[str, Any]]) -> tuple[int, Path]:
    request_root = root / "simulations" / request_id
    _assert_no_live_attempt_lock(request_root)
    used: set[int] = set()
    if request_root.is_dir():
        for child in request_root.iterdir():
            match = _ATTEMPT_DIR_RE.match(child.name)
            if match and child.is_dir():
                used.add(int(match.group(1)))
    for row in attempts:
        if str(row.get("request_id")) == request_id:
            used.add(int(row["attempt_index"]))
    index = 1
    while index in used:
        index += 1
    path = request_root / f"attempt_{index:04d}"
    path.mkdir(parents=True, exist_ok=False)
    return index, path


def _is_scientific(status: str) -> bool:
    return status in _NORMAL_SCIENTIFIC_STATUSES


def _scientific_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items()
            if key not in {"execution", "status", "telemetry"}}


def resume_configuration_matches(stored: dict[str, Any], requested: dict[str, Any]) -> bool:
    return _scientific_config(stored) == _scientific_config(requested)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--prepare-workers", type=int, default=None)
    parser.add_argument("--samples-per-cell", type=int, default=1)
    parser.add_argument("--cells")
    parser.add_argument("--schedulers")
    parser.add_argument("--priority-energy-ratios", default="1,2")
    parser.add_argument("--priority-energy-reference-ratio", default="2")
    parser.add_argument("--processors", type=int, default=experiment.PROCESSORS)
    parser.add_argument("--tasks", type=int, default=experiment.TASK_COUNT)
    parser.add_argument("--period-min", type=int, default=perf_g.PERIOD_MIN_MS)
    parser.add_argument("--period-max", type=int, default=perf_g.PERIOD_MAX_MS)
    parser.add_argument("--min-task-util", default=str(perf_g.MIN_TASK_UTILIZATION))
    parser.add_argument("--max-task-util", default=str(perf_g.MAX_TASK_UTILIZATION))
    parser.add_argument("--util-tolerance-total", default=str(perf_g.UTILIZATION_TOLERANCE))
    parser.add_argument("--simulation-horizon", type=int, default=perf_g.FORMAL_HORIZON_MS)
    parser.add_argument("--timeout-seconds", type=int, default=perf_g.FORMAL_TIMEOUT_SECONDS)
    parser.add_argument("--kappa", default=str(experiment.DEFAULT_KAPPA))
    parser.add_argument("--simulator", type=Path, default=ROOT / "build/rtsim/rtsim")
    parser.add_argument("--parse-concurrency", type=int, default=1)
    parser.add_argument("--keep-traces", action="store_true")
    parser.add_argument("--uc-figure-fixed-ue")
    parser.add_argument("--ue-figure-fixed-uc")
    parser.add_argument("--resume", action="store_true")
    return parser


def _technical(status: str, technical_error: Any) -> bool:
    return technical_error is not None or status not in {
        SimulationStatus.PASS_OBSERVED.value,
        SimulationStatus.DEADLINE_MISS.value,
    }


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    prepare_workers = args.workers if args.prepare_workers is None else args.prepare_workers
    validate_workers(prepare_workers, "prepare-workers")
    validate_workers(args.workers, "workers")
    if args.samples_per_cell < 1 or args.processors < 1 or args.tasks < 1:
        raise SystemExit("samples, processors, and tasks must be positive")
    if args.processors != experiment.PROCESSORS or args.tasks != experiment.TASK_COUNT:
        raise SystemExit("priority-energy LOAD-CROSS freezes processors=4 and tasks=10")
    if args.simulation_horizon <= 0 or args.timeout_seconds <= 0 or args.parse_concurrency < 1:
        raise SystemExit("horizon, timeout, and parse-concurrency must be positive")
    cells = experiment.parse_cells(args.cells)
    schedulers = experiment.parse_schedulers(args.schedulers)
    ratios = experiment.parse_ratios(args.priority_energy_ratios)
    reference_ratio = experiment.parse_fraction(args.priority_energy_reference_ratio, "reference ratio")
    if reference_ratio != experiment.REFERENCE_RATIO:
        raise SystemExit("priority-energy reference ratio is frozen at 2")
    min_util = experiment.parse_fraction(args.min_task_util, "min-task-util")
    max_util = experiment.parse_fraction(args.max_task_util, "max-task-util")
    tolerance = experiment.parse_fraction(args.util_tolerance_total, "util-tolerance-total")
    kappa = experiment.parse_fraction(args.kappa, "kappa")
    if kappa != experiment.DEFAULT_KAPPA:
        raise SystemExit("priority-energy LOAD-CROSS freezes kappa=10")
    try:
        figure_slices = experiment.resolve_figure_slices(
            cells,
            fixed_ue=(experiment.parse_fraction(args.uc_figure_fixed_ue, "uc-figure-fixed-ue")
                      if args.uc_figure_fixed_ue is not None else None),
            fixed_uc=(experiment.parse_fraction(args.ue_figure_fixed_uc, "ue-figure-fixed-uc")
                      if args.ue_figure_fixed_uc is not None else None),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    root = args.output
    config = {
        "experiment": "scheduler-priority-energy-load-cross-v1",
        "domain": experiment.DOMAIN, "seed": args.seed,
        "samples_per_cell": args.samples_per_cell,
        "cells": [[str(uc), str(ue)] for uc, ue in cells],
        "schedulers": list(schedulers),
        "priority_energy_ratios": [str(value) for value in ratios],
        "priority_energy_reference_ratio": str(reference_ratio),
        "processors": args.processors, "tasks": args.tasks,
        "period_min": args.period_min, "period_max": args.period_max,
        "min_task_util": str(min_util), "max_task_util": str(max_util),
        "util_tolerance_total": str(tolerance), "kappa": str(kappa),
        "simulation_horizon_ms": args.simulation_horizon,
        "figure_slices": figure_slices,
        "workload_contract": "all base tasks projected to workload=hash",
        "initial_energy_rule": "fixed reference battery / 2",
        "battery_rule": "kappa * E_burst_ref(rho=2)",
        "energy_control": "SERVICE_ONLY_SCALING",
        "execution": {
            "workers": args.workers, "prepare_workers": prepare_workers,
            "parse_concurrency": args.parse_concurrency,
            "keep_traces": bool(args.keep_traces),
            "timeout_seconds": args.timeout_seconds, "simulator": str(args.simulator),
        },
    }
    run_config = root / "run_config.json"
    if args.resume:
        if not run_config.is_file():
            raise SystemExit("resume requires run_config.json")
        stored = json.loads(run_config.read_text(encoding="utf-8"))
        if not resume_configuration_matches(stored, config):
            raise SystemExit("resume configuration mismatch")
    elif run_config.exists() or (root / "results.jsonl").exists():
        raise SystemExit("output exists; use --resume or choose a new output")
    else:
        write_json(run_config, config)

    started = time.perf_counter()
    unique_ucs = tuple(dict.fromkeys(uc for uc, _ in cells))
    tasksets, service = experiment.materialize_tasksets(
        root / "material", seed=args.seed, utilizations=unique_ucs,
        count=args.samples_per_cell, processors=args.processors, tasks=args.tasks,
        period_min=args.period_min, period_max=args.period_max,
        min_task_util=min_util, max_task_util=max_util, tolerance=tolerance,
        prepare_workers=prepare_workers,
    )
    taskset_rows = [experiment.taskset_row(item) for item in tasksets]
    write_jsonl(root / "tasksets.jsonl", taskset_rows)
    requests = experiment.request_rows(
        tasksets, cells, ratios, schedulers, args.simulation_horizon,
        system_path=service.system_path,
    )
    write_jsonl(root / "requests.jsonl", requests)
    raw_trace = experiment.raw_trace_for_service(service)
    taskset_by_id = {item.base.taskset_id: item for item in tasksets}
    material_by_hash: dict[str, dict[str, Any]] = {}
    for item in tasksets:
        for ratio in ratios:
            material = experiment.runtime_material(
                experiment.priority_energy_material(item, ratio, reference_ratio=reference_ratio),
                item.task_payload, service.system_path,
            )
            material_by_hash[material["material_hash"]] = material

    existing = read_jsonl(root / "results.jsonl") if args.resume else []
    attempts_path = root / "attempts.jsonl"
    attempts = read_jsonl(attempts_path) if args.resume else []
    for attempt in attempts:
        if not attempt.get("request_id") or "attempt_index" not in attempt:
            raise SystemExit("attempt history contains an invalid row")
    results_by_id = {str(row["request_id"]): row for row in existing}
    expected_ids = {str(row["request_id"]) for row in requests}
    if len(results_by_id) != len(existing) or not set(results_by_id) <= expected_ids:
        raise SystemExit("persisted results contain duplicate or unexpected request IDs")
    if any(_technical(str(row.get("simulation_status")), row.get("technical_error"))
           for row in existing):
        raise SystemExit("active results contain a technical row")
    pending = []
    for request in requests:
        request_id = str(request["request_id"])
        if request_id in results_by_id:
            continue
        taskset = taskset_by_id[str(request["taskset_id"])]
        material = material_by_hash[str(request["material_hash"])]
        energy = experiment.energy_material(material, Fraction(request["target_ue"]), raw_trace, kappa=kappa)
        attempt_index, attempt_root = _next_attempt_root(root, request_id, attempts)
        runtime_powers = experiment.runtime_task_powers(
            taskset.task_payload, material["task_energy_factors"], service.system_path,
        )
        pending.append({
            "request": request, "request_id": request_id,
            "simulation_id": request_id, "run_root": str(attempt_root),
            "attempt_index": attempt_index,
            "base_system_path": str(service.system_path),
            "task_payload": taskset.task_payload,
            "material_hash": material["material_hash"],
            "task_energy_factors": material["task_energy_factors"],
            "expected_task_power_j_per_tick": {
                task_id: info["runtime_power_float"]
                for task_id, info in runtime_powers.items()
            },
            "processors": args.processors, "exact_e0": energy["initial_energy_j"],
            "energy": energy, "material": material,
            "scheduler_id": request["scheduler_cli"],
            "energy_config": {
                "simulation_initial_battery": energy["initial_energy_j"],
                "battery_capacity": energy["battery_capacity_j"],
                "allow_harvest_clipping": True,
                "service_curve": {"solar_scale": energy["solar_scale"], "use_real_solar_data": True},
            },
            "simulation_config": {
                "simulator_bin": str(args.simulator), "horizon": args.simulation_horizon,
                "maximum_horizon": args.simulation_horizon,
                "horizon_extension_policy": "none", "warmup": 0,
                "minimum_jobs_per_task": 1, "trace_mode": "semantic",
                "trace_on_failure": args.keep_traces, "retain_trace": args.keep_traces,
                "timeout_seconds": args.timeout_seconds,
                "cleanup_transient_artifacts": True,
            },
        })

    context = multiprocessing.get_context("fork")
    semaphore = context.Semaphore(args.parse_concurrency)
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=context,
                             initializer=_initialize_worker, initargs=(semaphore,)) as executor:
        future_jobs = {executor.submit(_run_simulation_job, job): job for job in pending}
        for future in as_completed(future_jobs):
            job = future_jobs[future]
            request = job["request"]
            try:
                execution, technical = future.result()
            except Exception as exc:
                execution, technical = None, f"worker failure: {type(exc).__name__}: {exc}"
            task_ids = [str(row["task_id"]) for row in job["task_payload"]]
            if execution is None:
                status = "TECHNICAL_FAILURE"
                reason = technical or "worker failure"
                outcome = evaluate_outcome([], task_ids, horizon=args.simulation_horizon,
                                           minimum_adjudicable_jobs=1,
                                           simulation_completed=False, technical_error=reason)
                runtime = 0.0
                metrics = {}
                retained = None
                technical_error = reason
            else:
                status = execution.result.status.value
                reason = execution.result.reason
                technical_error = reason if not _is_scientific(status) else None
                outcome = evaluate_outcome(
                    [asdict(item) for item in execution.result.jobs], task_ids,
                    horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                    simulation_completed=execution.result.simulation_completed,
                    technical_error=technical_error,
                )
                runtime = execution.runtime_seconds
                metrics = dict(execution.result.metrics)
                retained = str(execution.retained_trace_path) if execution.retained_trace_path else None
            row = {
                **request, "material": job["material"], "energy": job["energy"],
                "simulation_status": status, "simulation_reason": reason,
                "technical_error": None if status in {SimulationStatus.PASS_OBSERVED.value,
                                                       SimulationStatus.DEADLINE_MISS.value} else reason,
                "schedulable": outcome.get("taskset_pass"),
                "taskset_pass": outcome.get("taskset_pass"),
                "deadline_miss": status == SimulationStatus.DEADLINE_MISS.value,
                "runtime_seconds": runtime, "metrics": metrics,
                "outcome": outcome, "retained_trace_path": retained,
            }
            attempt_row = {
                **request, "attempt_index": job["attempt_index"],
                "attempt_root": str(Path(job["run_root"]).relative_to(root)),
                "simulation_status": status, "simulation_reason": reason,
                "technical_error": technical_error, "runtime_seconds": runtime,
                "metrics": metrics, "retained_trace_path": retained,
            }
            _append_jsonl(attempts_path, attempt_row)
            attempts.append(attempt_row)
            if _is_scientific(status):
                _append_jsonl(root / "results.jsonl", row)
                results_by_id[request["request_id"]] = row
            else:
                print(
                    f"priority-energy technical execution failure for {request['request_id']}: {reason}",
                    file=sys.stderr,
                )

    if set(results_by_id) == expected_ids and len(results_by_id) == len(requests):
        write_jsonl(root / "results.jsonl", [results_by_id[str(row["request_id"])] for row in requests])
    report = {
        "complete": set(results_by_id) == expected_ids and len(results_by_id) == len(requests),
        "expected_results": len(requests), "observed_results": len(results_by_id),
        "missing_results": len(expected_ids - set(results_by_id)),
        "duplicate_request_ids": len(existing) - len(set(str(row["request_id"]) for row in existing)),
        "technical_attempt_count": sum(not _is_scientific(str(row.get("simulation_status"))) for row in attempts),
        "technical_result_count": 0,
        "all_hash_workloads": all(row.get("all_workloads_hash") is True for row in taskset_rows),
        "rho_pair_count": len(ratios),
        "elapsed_seconds": time.perf_counter() - started,
    }
    write_json(root / "invariant_report.json", report)
    stored = json.loads(run_config.read_text(encoding="utf-8"))
    stored["status"] = "complete" if report["complete"] else "incomplete"
    stored["telemetry"] = {"elapsed_seconds": report["elapsed_seconds"], "requests": len(requests)}
    write_json(run_config, stored)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
