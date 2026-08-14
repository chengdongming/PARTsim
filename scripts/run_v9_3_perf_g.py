#!/usr/bin/env python3
"""Plan or execute the minimal PERF-G paired-simulation pipeline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.v9_3 import perf_g
from experiments.v9_3.performance_outcome import evaluate_outcome
from experiments.v9_3.simulation_engine import SimulationStatus, run_paired_simulation


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _write_json(path, {}) if False else None
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object: {path}")
            rows.append(value)
    return rows


def _semantic_config(mode: str, *, simulator: Path, workers: int) -> dict[str, Any]:
    return {
        "mode": mode, "workers": workers, "simulator_binary": str(simulator),
        "formal_horizon_ms": perf_g.FORMAL_HORIZON_MS,
        "formal_timeout_seconds": perf_g.FORMAL_TIMEOUT_SECONDS,
        "formal_retry_timeout_seconds": perf_g.FORMAL_RETRY_TIMEOUT_SECONDS,
        "task_generation": {
            "M": perf_g.PROCESSORS, "n": perf_g.TASK_COUNT,
            "period_min_ms": perf_g.PERIOD_MIN_MS, "period_max_ms": perf_g.PERIOD_MAX_MS,
            "utilization_tolerance": str(perf_g.UTILIZATION_TOLERANCE),
            "workloads": list(perf_g.WORKLOADS), "synchronous_release": True,
        },
    }


def _check_resume_config(root: Path, requested: Mapping[str, Any]) -> None:
    path = root / "run_config.json"
    if not path.is_file():
        raise ValueError("resume configuration mismatch: run_config.json is missing")
    existing = json.loads(path.read_text(encoding="utf-8"))
    if existing != dict(requested):
        raise ValueError("resume configuration mismatch")


def _result_row(
    request: Mapping[str, Any], taskset: Any, energy: Mapping[str, Any],
    execution: Any, outcome: Mapping[str, Any], attempt_count: int,
) -> dict[str, Any]:
    result = execution.result
    metrics = dict(result.metrics)
    metrics.update({
        "energy_blocked_ticks": metrics.get("energy_blocked_ticks", 0),
        "harvested_energy_j": metrics.get("harvested_energy_j"),
        "consumed_energy_j": metrics.get("consumed_energy_j"),
        "battery_min_j": metrics.get("battery_minimum_j"),
        "battery_max_j": metrics.get("battery_maximum_j"),
    })
    return {
        **dict(request), "taskset_hash": taskset.semantic_hash,
        "taskset_seed": taskset.seed,
        "actual_U_norm": str(taskset.actual_utilization / perf_g.PROCESSORS),
        "energy": dict(energy), "attempt_count": attempt_count,
        "runtime_seconds": execution.runtime_seconds,
        "simulation_status": result.status.value,
        "simulation_reason": result.reason,
        "technical_error": None,
        "outcome": dict(outcome), "taskset_pass": outcome.get("taskset_pass"),
        "metrics": metrics, "jobs": [asdict(job) for job in result.jobs],
    }


def _execute_requests(
    *,
    root: Path,
    mode: str,
    namespace: str,
    utilizations: Sequence[Fraction],
    taskset_count: int,
    conditions: Sequence[Mapping[str, Any]],
    schedulers: Sequence[str],
    horizon: int,
    simulator: Path,
    resume: bool,
    workers: int,
) -> dict[str, Any]:
    requested_config = _semantic_config(mode, simulator=simulator, workers=workers)
    requested_config.update({
        "namespace": namespace, "utilizations": [str(value) for value in utilizations],
        "taskset_count": taskset_count, "conditions": list(conditions),
        "schedulers": list(schedulers), "horizon_ms": horizon,
    })
    if resume:
        _check_resume_config(root, requested_config)
    else:
        _write_json(root / "run_config.json", requested_config)

    material_root = root / "material"
    tasksets, service = perf_g.materialize_tasksets(
        material_root, namespace, utilizations, taskset_count,
    )
    raw_trace = perf_g.build_raw_trace(service)
    taskset_by_id = {taskset.taskset_id: taskset for taskset in tasksets}
    taskset_rows = [taskset.generated_row() for taskset in tasksets]
    _write_jsonl(root / "tasksets.jsonl", taskset_rows)
    taskset_plan_rows = [
        {"taskset_id": taskset.taskset_id,
         "U_norm": str(taskset.target_utilization / perf_g.PROCESSORS),
         "taskset_index": taskset.taskset_index}
        for taskset in tasksets
    ]
    requests = perf_g._request_rows(taskset_plan_rows, conditions, schedulers,
                                    horizon_ms=horizon, kind=mode.upper())
    pairing = perf_g.validate_pairing(requests, schedulers)
    _write_jsonl(root / "requests.jsonl", requests)
    result_path = root / "results.jsonl"
    existing = _read_jsonl(result_path) if resume else []
    existing_ids = [str(row.get("request_id")) for row in existing]
    if len(existing_ids) != len(set(existing_ids)):
        raise ValueError("duplicate result request_id")
    expected_ids = {str(row["request_id"]) for row in requests}
    if not set(existing_ids) <= expected_ids:
        raise ValueError("unexpected request in persisted results")
    completed = set(existing_ids)
    energy_by_name = {str(row["name"]): row for row in conditions}
    for request in requests:
        if request["request_id"] in completed:
            continue
        taskset = taskset_by_id[str(request["taskset_id"])]
        energy = energy_by_name[str(request["energy_condition"])]
        material = perf_g.energy_material(taskset, energy, raw_trace)
        sim_config = {
            "simulator_bin": str(simulator), "horizon": horizon,
            "maximum_horizon": horizon, "horizon_extension_policy": "none",
            "warmup": 0, "minimum_jobs_per_task": 1,
            "trace_mode": "semantic", "trace_on_failure": True,
            "timeout_seconds": 30 if mode == "SMOKE" else perf_g.FORMAL_TIMEOUT_SECONDS,
        }
        energy_config = {
            "simulation_initial_battery": material["initial_energy_j"],
            "battery_capacity": material["battery_capacity_j"],
            "allow_harvest_clipping": True,
            "service_curve": {
                "solar_scale": material["solar_scale"],
                "use_real_solar_data": True,
                "require_real_solar_data": True,
            },
        }
        execution = None
        attempt_count = 1
        technical_error = None
        try:
            execution = run_paired_simulation(
                simulation_id_value=str(request["request_id"]),
                base_system_path=service.system_path,
                run_root=root / "simulations" / str(request["request_id"]),
                task_payload=taskset.task_payload,
                taskset_hash=taskset.semantic_hash,
                processors=perf_g.PROCESSORS,
                exact_e0=Fraction(material["initial_energy_j"]),
                energy_config=energy_config,
                simulation_config=sim_config,
                scheduler_id=perf_g.SCHEDULER_CLI[str(request["scheduler"])],
            )
            if execution.result.status is SimulationStatus.RUNTIME_TIMEOUT:
                attempt_count = 2
                sim_config["timeout_seconds"] = 60 if mode == "SMOKE" else perf_g.FORMAL_RETRY_TIMEOUT_SECONDS
                execution = run_paired_simulation(
                    simulation_id_value=str(request["request_id"]) + "-retry",
                    base_system_path=service.system_path,
                    run_root=root / "simulations" / str(request["request_id"]) / "retry",
                    task_payload=taskset.task_payload,
                    taskset_hash=taskset.semantic_hash,
                    processors=perf_g.PROCESSORS,
                    exact_e0=Fraction(material["initial_energy_j"]),
                    energy_config=energy_config,
                    simulation_config=sim_config,
                    scheduler_id=perf_g.SCHEDULER_CLI[str(request["scheduler"])],
                )
        except Exception as exc:  # persist a technical failure instead of inventing an outcome
            technical_error = f"{type(exc).__name__}: {exc}"
        if execution is None:
            outcome = evaluate_outcome([], [str(item["task_id"]) for item in taskset.task_payload],
                                       horizon=horizon, minimum_adjudicable_jobs=1,
                                       simulation_completed=False, technical_error=technical_error)
            row = {**dict(request), "taskset_hash": taskset.semantic_hash,
                   "taskset_seed": taskset.seed,
                   "actual_U_norm": str(taskset.actual_utilization / perf_g.PROCESSORS),
                   "energy": {**dict(energy), **material}, "attempt_count": attempt_count,
                   "runtime_seconds": 0.0, "simulation_status": "TECHNICAL_FAILURE",
                   "simulation_reason": technical_error, "technical_error": technical_error,
                   "outcome": outcome, "taskset_pass": None, "metrics": {}, "jobs": []}
        else:
            technical = execution.result.status in {
                SimulationStatus.INTERNAL_ERROR, SimulationStatus.RUNTIME_TIMEOUT,
            }
            technical_error = execution.result.reason if technical else None
            outcome = evaluate_outcome(
                [asdict(job) for job in execution.result.jobs],
                [str(item["task_id"]) for item in taskset.task_payload],
                horizon=horizon, minimum_adjudicable_jobs=1,
                simulation_completed=execution.result.simulation_completed,
                technical_error=technical_error,
            )
            row = _result_row(request, taskset, {**dict(energy), **material}, execution, outcome, attempt_count)
            row["technical_error"] = technical_error
        _append_jsonl(result_path, row)
        completed.add(str(request["request_id"]))
    return {
        "mode": mode, "requests": len(requests), "processed": len(completed),
        "pending": len(expected_ids - completed), "pairing": pairing,
        "duplicate": 0, "missing": len(expected_ids - completed),
        "technical_failures": sum(row.get("technical_error") is not None for row in _read_jsonl(result_path)),
    }


def _load_selection(root: Path) -> dict[str, Any]:
    path = root / "calibration_selection.json"
    if not path.is_file():
        raise ValueError("formal execution requires calibration_selection.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(name in value for name in ("LOW", "TRANSITION", "HIGH")):
        raise ValueError("calibration_selection.json is incomplete")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("plan-cal", "plan-formal", "smoke", "run-cal", "run-cal-confirm", "run-formal"):
        modes.add_argument(f"--{mode}", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--simulator-binary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.plan_cal:
        plan = perf_g.cal_plan()
        _write_json(args.output / "plan_cal.json", {key: value for key, value in plan.items() if key not in {"tasksets", "requests_rows"}})
        print(json.dumps({"simulator_invoked": False, **{key: plan[key] for key in ("unique_tasksets", "energy_cells", "schedulers", "requests", "pairing")}}, sort_keys=True))
        return 0
    if args.plan_formal:
        plan = perf_g.formal_plan()
        _write_json(args.output / "plan_formal.json", {key: value for key, value in plan.items() if key not in {"tasksets", "requests_rows"}})
        print(json.dumps({"simulator_invoked": False, "executable_formal": False, **{key: plan[key] for key in ("unique_tasksets", "energy_cells", "schedulers", "requests", "pairing")}}, sort_keys=True))
        return 0
    simulator = (args.simulator_binary or (REPO_ROOT / "build" / "rtsim" / "rtsim")).resolve()
    if args.smoke:
        summary = _execute_requests(
            root=args.output, mode="SMOKE", namespace="SMOKE",
            utilizations=(Fraction("3/10"),), taskset_count=1,
            conditions=[perf_g.condition(**row) for row in perf_g.SMOKE_CONDITIONS],
            schedulers=perf_g.FORMAL_SCHEDULERS, horizon=2000,
            simulator=simulator, resume=args.resume, workers=args.workers,
        )
    elif args.run_cal:
        summary = _execute_requests(
            root=args.output, mode="CAL", namespace="CAL",
            utilizations=perf_g.CAL_UTILIZATIONS, taskset_count=perf_g.CAL_TASKSETS_PER_UTILIZATION,
            conditions=[perf_g.condition(f"k{k}-e{e}", k, e) for k in ("10", "50", "200") for e in ("1/2", "3/4", "1", "5/4", "3/2")],
            schedulers=perf_g.CAL_SCHEDULERS, horizon=perf_g.CAL_INITIAL_HORIZON_MS,
            simulator=simulator, resume=args.resume, workers=args.workers,
        )
    elif args.run_cal_confirm:
        selection = _load_selection(args.output)
        conditions = [perf_g.condition(name, selection[name]["kappa"], selection[name]["eta"]) for name in ("LOW", "TRANSITION", "HIGH")]
        summary = _execute_requests(
            root=args.output, mode="CAL_CONFIRM", namespace="CAL",
            utilizations=perf_g.CAL_UTILIZATIONS, taskset_count=perf_g.CAL_TASKSETS_PER_UTILIZATION,
            conditions=conditions, schedulers=perf_g.CAL_SCHEDULERS, horizon=perf_g.CAL_CONFIRMATION_HORIZON_MS,
            simulator=simulator, resume=args.resume, workers=args.workers,
        )
    else:
        selection = _load_selection(args.output)
        plan = perf_g.formal_plan(selection)
        summary = _execute_requests(
            root=args.output, mode="FORMAL", namespace="FORMAL",
            utilizations=perf_g.FORMAL_UTILIZATIONS, taskset_count=perf_g.FORMAL_TASKSETS_PER_UTILIZATION,
            conditions=[perf_g.condition(name, selection[name]["kappa"], selection[name]["eta"]) for name in ("LOW", "TRANSITION", "HIGH")],
            schedulers=perf_g.FORMAL_SCHEDULERS, horizon=perf_g.FORMAL_HORIZON_MS,
            simulator=simulator, resume=args.resume, workers=args.workers,
        )
        summary["planned_requests"] = plan["requests"]
    _write_json(args.output / "run_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
