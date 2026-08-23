#!/usr/bin/env python3
"""Run the paired scheduler LOAD-CROSS experiment locally."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import json
from fractions import Fraction
import multiprocessing
from pathlib import Path
import os
import re
import shutil
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g, scheduler_load_cross as experiment
from experiments.v9_3.parallel_prepare import run_prepare_jobs, validate_workers
from experiments.v9_3 import simulation_engine
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


def _run_simulation_job(job: dict[str, Any]) -> tuple[Any, str | None]:
    """Run one independent simulation in a worker process."""

    try:
        execution = run_paired_simulation(
            simulation_id_value=str(job["simulation_id"]),
            base_system_path=Path(job["base_system_path"]),
            run_root=Path(job["run_root"]),
            task_payload=job["task_payload"],
            taskset_hash=str(job["taskset_hash"]),
            processors=int(job["processors"]),
            exact_e0=job["exact_e0"],
            energy_config=job["energy_config"],
            simulation_config=job["simulation_config"],
            scheduler_id=str(job["scheduler_id"]),
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return execution, None


def _initialize_simulation_worker(parse_semaphore: Any) -> None:
    simulation_engine._set_trace_parse_semaphore(parse_semaphore)


def _print_progress(
    *, completed: int, total: int, outstanding_requests: int,
    started: float, completed_at_start: int, parse_concurrency: int,
) -> None:
    elapsed = max(0.0, time.perf_counter() - started)
    completed_run = completed - completed_at_start
    throughput = completed_run / elapsed * 60.0 if elapsed else 0.0
    print(
        "scheduler-load-cross progress: "
        f"completed={completed} total={total} "
        f"outstanding_requests={outstanding_requests} "
        f"elapsed_seconds={elapsed:.1f} "
        f"throughput_requests_per_min={throughput:.2f} "
        f"parse_concurrency={parse_concurrency}",
        flush=True,
    )


def _progress_due(
    *, completed: int, completed_at_start: int, total: int,
    interval: int,
) -> bool:
    completed_run = completed - completed_at_start
    return completed_run > 0 and (
        completed_run % interval == 0 or completed == total
    )


_ATTEMPT_DIR_RE = re.compile(r"^attempt_(\d+)$")
_NORMAL_SCIENTIFIC_STATUSES = {
    SimulationStatus.PASS_OBSERVED.value,
    SimulationStatus.DEADLINE_MISS.value,
}
_TECHNICAL_STATUSES = {
    "SIM_INTERNAL_ERROR", "TECHNICAL_FAILURE", "RUNTIME_TIMEOUT",
    SimulationStatus.INTERNAL_ERROR.value,
    SimulationStatus.RUNTIME_TIMEOUT.value,
    SimulationStatus.HORIZON_INSUFFICIENT.value,
}


def _is_technical_result(row: dict[str, Any]) -> bool:
    status = str(row.get("simulation_status", ""))
    return row.get("technical_error") is not None or status in _TECHNICAL_STATUSES


def _read_pid(lock_path: Path) -> int | None:
    for line in lock_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("pid="):
            try:
                return int(line[4:])
            except ValueError:
                return None
    return None


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _assert_no_live_attempt_lock(request_root: Path) -> None:
    for lock_path in request_root.rglob("*.lock") if request_root.is_dir() else ():
        pid = _read_pid(lock_path)
        if pid is None:
            raise RuntimeError(f"cannot determine lock owner: {lock_path}")
        if _pid_is_alive(pid):
            raise RuntimeError(f"request has an active execution lock: {lock_path} (pid={pid})")


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
        if str(row.get("request_id")) != request_id:
            continue
        try:
            used.add(int(row["attempt_index"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid attempt history for {request_id}") from exc
    attempt_index = 1
    while attempt_index in used:
        attempt_index += 1
    attempt_root = request_root / f"attempt_{attempt_index:04d}"
    try:
        attempt_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        # A concurrent invocation won the allocation race.  Do not overwrite
        # or reuse its directory; fail closed and let the caller retry.
        raise RuntimeError(f"attempt directory allocation raced: {attempt_root}")
    return attempt_index, attempt_root


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _persisted_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return the compact result metrics without mutating the execution result."""
    persisted = dict(metrics)
    persisted.pop("battery_trajectory", None)
    return persisted


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--prepare-workers", type=int, default=None,
        help="bounded workers for deterministic preparation (default: --workers)",
    )
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
    parser.add_argument("--uc-figure-fixed-ue")
    parser.add_argument("--ue-figure-fixed-uc")
    parser.add_argument(
        "--parse-concurrency", type=int, default=1,
        help="maximum concurrent trace parsers (default: 1)",
    )
    parser.add_argument(
        "--keep-traces", action="store_true",
        help="retain complete simulator traces for debugging",
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    prepare_workers = args.workers if args.prepare_workers is None else args.prepare_workers
    try:
        validate_workers(prepare_workers, "prepare-workers")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if min(args.workers, args.samples_per_cell, args.processors, args.tasks) < 1:
        raise SystemExit("workers, samples, processors, and tasks must be positive")
    if args.simulation_horizon <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("simulation horizon and timeout must be positive")
    if args.parse_concurrency < 1:
        raise SystemExit("parse-concurrency must be positive")
    cells = experiment.parse_cells(args.cells)
    schedulers = experiment.parse_schedulers(
        args.schedulers if args.schedulers is not None
        else ",".join(perf_g.FORMAL_SCHEDULERS)
    )
    is_frozen_main_figure = tuple(cells) == tuple(experiment.FORMAL_CELLS)
    try:
        figure_slices = experiment.resolve_figure_slices(
            cells,
            fixed_ue=(
                experiment.parse_fraction(args.uc_figure_fixed_ue, "uc-figure-fixed-ue")
                if args.uc_figure_fixed_ue is not None else None
            ),
            fixed_uc=(
                experiment.parse_fraction(args.ue_figure_fixed_uc, "ue-figure-fixed-uc")
                if args.ue_figure_fixed_uc is not None else None
            ),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    min_util = experiment.parse_fraction(args.min_task_util, "min-task-util")
    max_util = experiment.parse_fraction(args.max_task_util, "max-task-util")
    tolerance = experiment.parse_fraction(args.util_tolerance_total, "util-tolerance-total")
    kappa = experiment.parse_fraction(args.kappa, "kappa")
    if kappa != experiment.DEFAULT_KAPPA:
        raise SystemExit("scheduler LOAD-CROSS freezes kappa=10")
    if is_frozen_main_figure:
        try:
            experiment.validate_frozen_main_figure(
                cells, schedulers, horizon_ms=args.simulation_horizon,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
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
        "keep_traces": args.keep_traces,
        "parse_concurrency": args.parse_concurrency,
        "figure_slices": figure_slices,
        "campaign_contract": {
            "name": "ordinary-general-random-nine-scheduler-main-figures-v1"
            if is_frozen_main_figure else "scheduler-load-cross-custom",
            "wholepass": "all_adjudicable_jobs_on_time_taskset_level",
            "frozen_main_figure": is_frozen_main_figure,
            "unique_cell_count": len(set(cells)),
            "panel_groups": {
                name: list(group) for name, group in experiment.PANEL_GROUPS.items()
            },
        },
        "execution": {
            "workers": args.workers,
            "prepare_workers": prepare_workers,
            "parse_concurrency": args.parse_concurrency,
            "keep_traces": bool(args.keep_traces),
        },
    }
    run_config = root / "run_config.json"
    if args.resume:
        stored_config = json.loads(run_config.read_text(encoding="utf-8")) if run_config.is_file() else None
        runtime_keys = {"execution", "status", "telemetry"}
        stored_comparable = {
            key: value for key, value in (stored_config or {}).items()
            if key not in runtime_keys
        }
        requested_comparable = {
            key: value for key, value in config.items() if key not in runtime_keys
        }
        legacy_config = {key: value for key, value in requested_comparable.items() if key != "figure_slices"}
        stored_legacy_comparable = {
            key: value for key, value in stored_comparable.items()
            if key != "figure_slices"
        }
        legacy_figure_slices = experiment.resolve_figure_slices(cells)
        legacy_resume = (
            stored_legacy_comparable == legacy_config
            and figure_slices == legacy_figure_slices
        )
        if stored_comparable != requested_comparable and not legacy_resume:
            raise SystemExit("resume configuration mismatch")
        if legacy_resume:
            write_json(run_config, config)
    elif run_config.exists() or (root / "results.jsonl").exists():
        raise SystemExit("output exists; use --resume or choose a new output")
    else:
        write_json(run_config, config)
    total_started = time.perf_counter()
    material = root / "material"
    unique_ucs = tuple(dict.fromkeys(uc for uc, _ue in cells))
    prepare_tasksets_started = time.perf_counter()
    tasksets, service = experiment.materialize_tasksets(
        material, seed=args.seed, utilizations=unique_ucs, count=args.samples_per_cell,
        processors=args.processors, tasks=args.tasks, period_min=args.period_min,
        period_max=args.period_max, min_task_util=min_util, max_task_util=max_util,
        tolerance=tolerance, prepare_workers=prepare_workers,
    )
    prepare_tasksets_seconds = time.perf_counter() - prepare_tasksets_started
    print(
        f"phase=prepare stage=scheduler-load-cross prepare-tasksets "
        f"elapsed_seconds={prepare_tasksets_seconds:.3f} workers={prepare_workers} "
        f"items={len(tasksets)}", flush=True,
    )
    rows = [experiment.taskset_row(taskset, args.processors) for taskset in tasksets]
    write_jsonl(root / "tasksets.jsonl", rows)
    request_started = time.perf_counter()
    requests = experiment.request_rows(tasksets, cells, schedulers, args.simulation_horizon)
    write_jsonl(root / "requests.jsonl", requests)
    request_build_seconds = time.perf_counter() - request_started
    raw_trace = tuple(experiment.construct_paired_harvest_trace(
        service.system_path, experiment.FORMAL_NORMALIZATION_HORIZON,
    ))
    raw_trace_id = experiment.harvest_trace_identity(raw_trace)
    experiment.set_prepare_raw_trace(raw_trace)
    results_path = root / "results.jsonl"
    attempts_path = root / "attempts.jsonl"
    existing = read_jsonl(results_path) if args.resume else []
    existing_ids = [str(row.get("request_id")) for row in existing]
    expected_ids = {str(row["request_id"]) for row in requests}
    if len(existing_ids) != len(set(existing_ids)) or not set(existing_ids) <= expected_ids:
        raise SystemExit("persisted results contain duplicate or unexpected request IDs")
    if any(_is_technical_result(row) for row in existing):
        raise SystemExit(
            "active results contain a technical row; migration/recovery is required"
        )
    attempts = read_jsonl(attempts_path) if args.resume else []
    for attempt in attempts:
        if not attempt.get("request_id") or "attempt_index" not in attempt:
            raise SystemExit("attempt history contains an invalid row")
    results_by_id = {str(row["request_id"]): row for row in existing}
    completed_ids = set(existing_ids)
    taskset_by_id = {taskset.taskset_id: taskset for taskset in tasksets}
    pending_jobs: list[dict[str, Any]] = []
    energy_jobs: dict[tuple[str, str], dict[str, Any]] = {}
    for request in requests:
        request_id = str(request["request_id"])
        if request_id in completed_ids:
            continue
        taskset = taskset_by_id[request["taskset_id"]]
        try:
            attempt_index, attempt_root = _next_attempt_root(root, request_id, attempts)
        except RuntimeError as exc:
            print(f"scheduler-load-cross resume blocked: {exc}", file=sys.stderr)
            return 2
        energy_key = (taskset.taskset_id, str(request["target_ue"]))
        energy_jobs.setdefault(energy_key, {
            "taskset_id": taskset.taskset_id,
            "target_ue": request["target_ue"],
            "task_payload": tuple(taskset.task_payload),
            "processors": taskset.processors,
            "task_count": taskset.task_count,
            "kappa": kappa,
            "raw_trace_id": raw_trace_id,
        })
        simulation = {
            "simulator_bin": str(args.simulator), "horizon": args.simulation_horizon,
            "maximum_horizon": args.simulation_horizon, "horizon_extension_policy": "none",
            "warmup": 0, "minimum_jobs_per_task": 1, "trace_mode": "semantic",
            "trace_on_failure": args.keep_traces,
            "retain_trace": args.keep_traces,
            "timeout_seconds": args.timeout_seconds,
            "cleanup_transient_artifacts": True,
        }
        pending_jobs.append({
            "request": request,
            "request_id": request_id,
            "simulation_id": request_id,
            "attempt_index": attempt_index,
            "attempt_root": str(attempt_root),
            "run_root": str(attempt_root),
            "taskset_id": taskset.taskset_id,
            "taskset_hash": taskset.semantic_hash,
            "task_payload": taskset.task_payload,
            "energy_key": energy_key,
            "base_system_path": str(service.system_path),
            "processors": args.processors,
            "exact_e0": None,
            "energy_config": None,
            "simulation_config": simulation,
            "scheduler_id": request["scheduler_cli"],
        })

    prepare_energy_started = time.perf_counter()
    prepared_energy = run_prepare_jobs(
        energy_jobs.values(), experiment.prepare_energy_material,
        workers=prepare_workers, phase="scheduler-load-cross prepare-energy",
        key=lambda row: (row["taskset_id"], row["target_ue"]),
    )
    energy_by_key = {
        key: value["material"] for key, value in prepared_energy.items()
    }
    prepare_energy_seconds = time.perf_counter() - prepare_energy_started
    for job in pending_jobs:
        job["energy"] = energy_by_key[job.pop("energy_key")]
        energy = job["energy"]
        simulation = job["simulation_config"]
        job["exact_e0"] = Fraction(energy["initial_energy_j"])
        simulation["trace_on_failure"] = args.keep_traces
        simulation["retain_trace"] = args.keep_traces
        job["energy_config"] = {
            "simulation_initial_battery": energy["initial_energy_j"],
            "battery_capacity": energy["battery_capacity_j"], "allow_harvest_clipping": True,
            "service_curve": {"solar_scale": energy["solar_scale"], "use_real_solar_data": True},
        }

    execution_started = time.perf_counter()
    mp_context = multiprocessing.get_context("fork")
    parse_semaphore = mp_context.Semaphore(args.parse_concurrency)
    with ProcessPoolExecutor(
        max_workers=args.workers,
        mp_context=mp_context,
        initializer=_initialize_simulation_worker,
        initargs=(parse_semaphore,),
    ) as executor:
        future_to_job = {
            executor.submit(_run_simulation_job, job): job
            for job in pending_jobs
        }
        progress_started = time.perf_counter()
        completed_at_start = len(existing)
        completed_count = completed_at_start
        progress_interval = max(1, min(50, len(pending_jobs) // 20 or 1))
        for future in as_completed(future_to_job):
            job = future_to_job.pop(future)
            request = job["request"]
            request_id = str(job["request_id"])
            task_payload = job["task_payload"]
            try:
                execution, technical = future.result()
            except Exception as exc:
                execution = None
                technical = f"worker failure: {type(exc).__name__}: {exc}"
            if execution is None:
                outcome = evaluate_outcome(
                    [], [str(row["task_id"]) for row in task_payload],
                    horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                    simulation_completed=False, technical_error=technical,
                    strict_wholepass=True,
                )
                status = "TECHNICAL_FAILURE"
                reason = technical
                runtime_seconds = 0.0
                metrics: dict[str, Any] = {}
                stdout_tail = ""
                stderr_tail = ""
                retained_trace_path = None
            else:
                status = execution.result.status
                is_technical = status.value not in _NORMAL_SCIENTIFIC_STATUSES
                technical_error = execution.result.reason if is_technical else None
                outcome = evaluate_outcome(
                    [asdict(observation) for observation in execution.result.jobs],
                    [str(row["task_id"]) for row in task_payload],
                    horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                    simulation_completed=execution.result.simulation_completed,
                    technical_error=technical_error,
                    strict_wholepass=True,
                )
                reason = execution.result.reason
                status = status.value
                if outcome.get("technical_failure"):
                    technical_error = outcome.get("reason") or "wholepass_outcome_unavailable"
                    status = "TECHNICAL_FAILURE"
                runtime_seconds = execution.runtime_seconds
                metrics = _persisted_metrics(execution.result.metrics)
                stdout_tail = execution.stdout_tail
                stderr_tail = execution.stderr_tail
                retained_trace_path = str(execution.retained_trace_path) if execution.retained_trace_path else None
                row = {**request, "energy": job["energy"], "simulation_status": status,
                       "simulation_reason": reason,
                       "technical_error": technical_error,
                       "schedulable": outcome.get("taskset_pass"),
                       "deadline_miss": status == SimulationStatus.DEADLINE_MISS.value,
                       "runtime_seconds": runtime_seconds,
                       "metrics": metrics, "outcome": outcome,
                       "taskset_pass": outcome.get("taskset_pass"),
                       "wholepass": outcome.get("wholepass", outcome.get("taskset_pass"))}
            attempt_row = {
                **request,
                "request_id": request_id,
                "attempt_index": job["attempt_index"],
                "attempt_root": str(Path(job["attempt_root"]).relative_to(root)),
                "taskset_id": job["taskset_id"],
                "taskset_hash": job["taskset_hash"],
                "target_uc": request["target_uc"],
                "actual_uc": request["actual_uc"],
                "target_ue": request["target_ue"],
                "eta": request["eta"],
                "simulation_status": status,
                "technical_error": technical if execution is None else technical_error,
                "runtime_seconds": runtime_seconds,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
                "retained_trace_path": retained_trace_path,
                "simulation_reason": reason,
            }
            _append_jsonl(attempts_path, attempt_row)
            attempts.append(attempt_row)
            completed_count += 1
            if _progress_due(
                completed=completed_count,
                completed_at_start=completed_at_start,
                total=len(requests), interval=progress_interval,
            ):
                _print_progress(
                    completed=completed_count, total=len(requests),
                    outstanding_requests=sum(
                        not item.done() for item in future_to_job
                    ), started=progress_started,
                    completed_at_start=completed_at_start,
                    parse_concurrency=args.parse_concurrency,
                )
            if status not in _NORMAL_SCIENTIFIC_STATUSES:
                print(
                    f"scheduler-load-cross technical execution failure for "
                    f"{request_id}: {status}: {reason}",
                    file=sys.stderr,
                )
                return 2
            _append_jsonl(results_path, row)
            results_by_id[request_id] = row
            completed_ids.add(request_id)
    observed_ids = list(results_by_id)
    if len(results_by_id) == len(requests) and set(results_by_id) == expected_ids:
        canonical_results = [results_by_id[str(request["request_id"])] for request in requests]
        write_jsonl(results_path, canonical_results)
    execution_seconds = time.perf_counter() - execution_started
    report = {
        "expected_results": len(requests), "observed_results": len(results_by_id),
        "missing_results": len(expected_ids - set(observed_ids)),
        "duplicate_request_ids": len(observed_ids) - len(set(observed_ids)),
        "missing": len(expected_ids - set(observed_ids)),
        "duplicate": len(observed_ids) - len(set(observed_ids)),
        "unexpected": len(set(observed_ids) - expected_ids),
        "technical": 0,
        "actual_ue_exact": all(Fraction(row["energy"]["target_ue"]) * Fraction(row["energy"]["eta"]) == 1 for row in results_by_id.values()),
        "canonical_task_power": all(row.get("canonical_task_power") for row in rows),
        "scheduler_input_hashes_stable": all(len({row["taskset_hash"] for row in requests if row["taskset_id"] == taskset.taskset_id}) == 1 for taskset in tasksets),
        "complete": len(results_by_id) == len(requests) and len(observed_ids) == len(set(observed_ids)),
    }
    write_json(root / "invariant_report.json", report)
    config_document = json.loads(run_config.read_text(encoding="utf-8"))
    config_document["status"] = "complete" if report["complete"] else "incomplete"
    config_document["telemetry"] = {
        "prepare_tasksets_seconds": prepare_tasksets_seconds,
        "prepare_energy_seconds": prepare_energy_seconds,
        "request_build_seconds": request_build_seconds,
        "execution_seconds": execution_seconds,
        "total_seconds": time.perf_counter() - total_started,
    }
    write_json(run_config, config_document)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
