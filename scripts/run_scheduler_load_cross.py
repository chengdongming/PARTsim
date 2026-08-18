#!/usr/bin/env python3
"""Run the paired scheduler LOAD-CROSS experiment locally."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from fractions import Fraction
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g, scheduler_load_cross as experiment
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
    temporary.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--samples-per-cell", type=int, default=1)
    parser.add_argument("--cells")
    parser.add_argument("--schedulers")
    parser.add_argument("--processors", type=int, default=perf_g.PROCESSORS)
    parser.add_argument("--tasks", type=int, default=perf_g.TASK_COUNT)
    parser.add_argument("--period-min", type=int, default=perf_g.PERIOD_MIN_MS)
    parser.add_argument("--period-max", type=int, default=perf_g.PERIOD_MAX_MS)
    parser.add_argument("--min-task-util", default=str(perf_g.MIN_TASK_UTILIZATION))
    parser.add_argument("--max-task-util", default=str(perf_g.MAX_TASK_UTILIZATION))
    parser.add_argument("--util-tolerance-total", default=str(perf_g.UTILIZATION_TOLERANCE))
    parser.add_argument("--rho", default="11/2")
    parser.add_argument("--latency", default="2/5")
    parser.add_argument("--simulation-horizon", type=int, default=perf_g.FORMAL_HORIZON_MS)
    parser.add_argument("--timeout-seconds", type=int, default=perf_g.FORMAL_TIMEOUT_SECONDS)
    parser.add_argument("--kappa", default=str(experiment.DEFAULT_KAPPA))
    parser.add_argument("--simulator", type=Path, default=ROOT / "build/rtsim/rtsim")
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if min(args.workers, args.samples_per_cell, args.processors, args.tasks) < 1:
        raise SystemExit("workers, samples, processors, and tasks must be positive")
    if args.simulation_horizon <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("simulation horizon and timeout must be positive")
    cells = experiment.parse_cells(args.cells)
    schedulers = experiment.parse_schedulers(args.schedulers)
    min_util = experiment.parse_fraction(args.min_task_util, "min-task-util")
    max_util = experiment.parse_fraction(args.max_task_util, "max-task-util")
    tolerance = experiment.parse_fraction(args.util_tolerance_total, "util-tolerance-total")
    kappa = experiment.parse_fraction(args.kappa, "kappa")
    if kappa != experiment.DEFAULT_KAPPA:
        raise SystemExit("scheduler LOAD-CROSS freezes kappa=10")
    # rho/latency are retained as explicit provenance controls for parity with
    # the RTA generation interface; simulator P remains canonical and untouched.
    rho = experiment.parse_fraction(args.rho, "rho")
    latency = experiment.parse_fraction(args.latency, "latency")
    root = args.output
    config = {
        "experiment": "scheduler-load-cross-v2", "seed": args.seed, "workers": args.workers,
        "samples_per_cell": args.samples_per_cell, "cells": [[str(uc), str(ue)] for uc, ue in cells],
        "schedulers": list(schedulers), "processors": args.processors, "tasks": args.tasks,
        "period_min": args.period_min, "period_max": args.period_max,
        "min_task_util": str(min_util), "max_task_util": str(max_util),
        "util_tolerance_total": str(tolerance), "rho": str(rho), "latency": str(latency),
        "kappa": str(kappa), "initial_energy_rule": "battery_capacity/2",
        "normalization_horizon_ms": experiment.FORMAL_NORMALIZATION_HORIZON,
        "simulation_horizon_ms": args.simulation_horizon,
        "release_semantics": "synchronous arrival_offset=0",
        "energy_control": "SERVICE_ONLY_SCALING", "energy_unit": "J/tick exact canonical P",
        "simulator": str(args.simulator), "canonical_taskset_source": "PERF-G TasksetStore",
    }
    run_config = root / "run_config.json"
    if args.resume:
        if not run_config.is_file() or json.loads(run_config.read_text(encoding="utf-8")) != config:
            raise SystemExit("resume configuration mismatch")
    elif run_config.exists() or (root / "results.jsonl").exists():
        raise SystemExit("output exists; use --resume or choose a new output")
    else:
        write_json(run_config, config)
    material = root / "material"
    unique_ucs = tuple(dict.fromkeys(uc for uc, _ue in cells))
    tasksets, service = experiment.materialize_tasksets(
        material, seed=args.seed, utilizations=unique_ucs, count=args.samples_per_cell,
        processors=args.processors, tasks=args.tasks, period_min=args.period_min,
        period_max=args.period_max, min_task_util=min_util, max_task_util=max_util,
        tolerance=tolerance,
    )
    rows = [experiment.taskset_row(taskset, args.processors) for taskset in tasksets]
    write_jsonl(root / "tasksets.jsonl", rows)
    requests = experiment.request_rows(tasksets, cells, schedulers, args.simulation_horizon)
    write_jsonl(root / "requests.jsonl", requests)
    raw_trace = tuple(experiment.construct_paired_harvest_trace(
        service.system_path, experiment.FORMAL_NORMALIZATION_HORIZON,
    ))
    existing = read_jsonl(root / "results.jsonl") if args.resume else []
    existing_ids = [str(row.get("request_id")) for row in existing]
    expected_ids = {str(row["request_id"]) for row in requests}
    if len(existing_ids) != len(set(existing_ids)) or not set(existing_ids) <= expected_ids:
        raise SystemExit("persisted results contain duplicate or unexpected request IDs")
    results = list(existing)
    taskset_by_id = {taskset.taskset_id: taskset for taskset in tasksets}
    for request in requests:
        if request["request_id"] in set(existing_ids):
            continue
        taskset = taskset_by_id[request["taskset_id"]]
        energy = experiment.energy_material(
            taskset, Fraction(request["target_ue"]), raw_trace, kappa=kappa,
        )
        simulation = {
            "simulator_bin": str(args.simulator), "horizon": args.simulation_horizon,
            "maximum_horizon": args.simulation_horizon, "horizon_extension_policy": "none",
            "warmup": 0, "minimum_jobs_per_task": 1, "trace_mode": "semantic",
            "trace_on_failure": True, "timeout_seconds": args.timeout_seconds,
        }
        energy_config = {
            "simulation_initial_battery": energy["initial_energy_j"],
            "battery_capacity": energy["battery_capacity_j"], "allow_harvest_clipping": True,
            "service_curve": {"solar_scale": energy["solar_scale"], "use_real_solar_data": True},
        }
        execution = None
        technical = None
        try:
            execution = run_paired_simulation(
                simulation_id_value=request["request_id"], base_system_path=service.system_path,
                run_root=root / "simulations" / request["request_id"], task_payload=taskset.task_payload,
                taskset_hash=taskset.semantic_hash, processors=args.processors,
                exact_e0=Fraction(energy["initial_energy_j"]), energy_config=energy_config,
                simulation_config=simulation, scheduler_id=request["scheduler_cli"],
            )
        except Exception as exc:
            technical = f"{type(exc).__name__}: {exc}"
        if execution is None:
            outcome = evaluate_outcome(
                [], [str(row["task_id"]) for row in taskset.task_payload],
                horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                simulation_completed=False, technical_error=technical,
            )
            row = {**request, "energy": energy, "simulation_status": "TECHNICAL_FAILURE",
                   "simulation_reason": technical, "technical_error": technical,
                   "schedulable": None, "deadline_miss": None, "runtime_seconds": 0.0,
                   "outcome": outcome, "taskset_pass": None}
        else:
            status = execution.result.status
            is_technical = status in {SimulationStatus.RUNTIME_TIMEOUT, SimulationStatus.INTERNAL_ERROR, SimulationStatus.HORIZON_INSUFFICIENT}
            technical_error = execution.result.reason if is_technical else None
            outcome = evaluate_outcome(
                [asdict(job) for job in execution.result.jobs],
                [str(row["task_id"]) for row in taskset.task_payload],
                horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                simulation_completed=execution.result.simulation_completed,
                technical_error=technical_error,
            )
            row = {**request, "energy": energy, "simulation_status": status.value,
                   "simulation_reason": execution.result.reason,
                   "technical_error": technical_error,
                   "schedulable": outcome.get("taskset_pass"),
                   "deadline_miss": status is SimulationStatus.DEADLINE_MISS,
                   "runtime_seconds": execution.runtime_seconds,
                   "metrics": dict(execution.result.metrics), "outcome": outcome,
                   "taskset_pass": outcome.get("taskset_pass")}
        results.append(row)
        write_jsonl(root / "results.jsonl", results)
    observed_ids = [str(row["request_id"]) for row in results]
    report = {
        "expected_results": len(requests), "observed_results": len(results),
        "missing_results": len(expected_ids - set(observed_ids)),
        "duplicate_request_ids": len(observed_ids) - len(set(observed_ids)),
        "actual_ue_exact": all(Fraction(row["energy"]["target_ue"]) * Fraction(row["energy"]["eta"]) == 1 for row in results),
        "canonical_task_power": all(row.get("canonical_task_power") for row in rows),
        "scheduler_input_hashes_stable": all(len({row["taskset_hash"] for row in requests if row["taskset_id"] == taskset.taskset_id}) == 1 for taskset in tasksets),
        "complete": len(results) == len(requests) and len(observed_ids) == len(set(observed_ids)),
    }
    write_json(root / "invariant_report.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
