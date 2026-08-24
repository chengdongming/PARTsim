#!/usr/bin/env python3
"""Run an isolated, fully parameterized deadline sensitivity experiment.

The default projection is ``relax-original``: every task keeps its canonical
deadline at alpha=0 and is relaxed toward its own period.  The runner creates
one canonical base population per U_C and reuses it across all U_E, deadline,
policy, and scheduler conditions.  It does not alter the formal LOAD-CROSS
runner or any production scheduler/RM/DM implementation.
"""

from __future__ import annotations

from argparse import ArgumentParser
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g, scheduler_load_cross as load_cross
from experiments.v9_3.config import canonical_json, domain_hash, fraction_text
from experiments.v9_3.deadline_profile_sensitivity import (
    ProjectedTaskset,
    project_profiles_from_original_deadline,
    project_profiles_with_fixed_lambdas,
    fixed_profile_name_for_lambda,
)
from experiments.v9_3.parallel_prepare import validate_workers
from experiments.v9_3.simulation_engine import (
    SimulationStatus,
    normalize_scheduler_priority_policy,
)
from experiments.v9_3.performance_outcome import evaluate_outcome
from scripts.run_scheduler_load_cross import (
    _initialize_simulation_worker,
    _persisted_metrics,
    _run_simulation_job,
)


DEFAULT_CELLS = "3/10:7/10"
DEFAULT_ALPHAS = "0,1/3,2/3,1"
DEFAULT_SAMPLES_PER_CELL = 20
DEFAULT_SEED = 20260823
DEFAULT_PROCESSORS = 4
DEFAULT_TASKS = 10
DEFAULT_PERIOD_MIN = 40
DEFAULT_PERIOD_MAX = 200
DEFAULT_KAPPA = Fraction(10)
DEFAULT_HORIZON = 60000
DEFAULT_SCHEDULERS = ("ASAP-BLOCK", "ASAP-NONBLOCK", "ST-NONBLOCK")
DEFAULT_POLICIES = ("RM", "DM")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_alphas(text: str | None) -> tuple[Fraction, ...]:
    if not text:
        text = DEFAULT_ALPHAS
    values: list[Fraction] = []
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            raise ValueError("alpha list contains an empty value")
        try:
            value = load_cross.parse_fraction(item, "alpha", positive=False)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if value > 1:
            raise ValueError("alpha must be in [0, 1]")
        if values and value <= values[-1]:
            raise ValueError("alphas must be unique and strictly increasing")
        values.append(value)
    if not values:
        raise ValueError("at least one alpha is required")
    return tuple(values)


def parse_priority_policies(text: str | None) -> tuple[str, ...]:
    values = DEFAULT_POLICIES if not text else tuple(item.strip() for item in text.split(","))
    if not values or any(not item for item in values) or len(set(values)) != len(values):
        raise ValueError("priority policy list must be non-empty and unique")
    normalized: list[str] = []
    for item in values:
        try:
            normalized.append(normalize_scheduler_priority_policy(item))
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
    return tuple(normalized)


def _fraction_arg(value: str, label: str, *, positive: bool = True) -> Fraction:
    try:
        return load_cross.parse_fraction(value, label, positive=positive)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _profile_name(alpha: Fraction) -> str:
    if alpha == 0:
        return "ORIGINAL"
    if alpha == 1:
        return "IMPLICIT"
    return f"RELAX_{alpha.numerator}_{alpha.denominator}"


def _projected_row(projected: ProjectedTaskset) -> dict[str, Any]:
    return projected.row()


def _as_projected_by_uc(
    projected: Mapping[Fraction, Sequence[ProjectedTaskset]]
    | Sequence[ProjectedTaskset],
) -> dict[Fraction, tuple[ProjectedTaskset, ...]]:
    if isinstance(projected, Mapping):
        return {Fraction(key): tuple(value) for key, value in projected.items()}
    # Backward-compatible convenience for the original one-cell test helper.
    return {Fraction(3, 10): tuple(projected)}


def build_requests(
    projected: Mapping[Fraction, Sequence[ProjectedTaskset]]
    | Sequence[ProjectedTaskset],
    cells: Sequence[tuple[Fraction, Fraction]] | None = None,
    *,
    policies: Sequence[str] = DEFAULT_POLICIES,
    schedulers: Sequence[str] = DEFAULT_SCHEDULERS,
    horizon_ms: int = DEFAULT_HORIZON,
    kappa: Fraction = DEFAULT_KAPPA,
) -> list[dict[str, Any]]:
    projected_by_uc = _as_projected_by_uc(projected)
    if cells is None:
        cells = ((Fraction(3, 10), Fraction(7, 10)),)
    rows: list[dict[str, Any]] = []
    for target_uc, target_ue in cells:
        if target_uc not in projected_by_uc:
            raise ValueError(f"no base tasksets prepared for U_C={target_uc}")
        for item in projected_by_uc[target_uc]:
            for policy in policies:
                normalized_policy = normalize_scheduler_priority_policy(policy)
                for scheduler in schedulers:
                    identity = {
                        "projection_mode": item.projection_mode,
                        "projected_taskset_hash": item.projected_taskset_hash,
                        "target_uc": fraction_text(target_uc),
                        "target_ue": fraction_text(target_ue),
                        "kappa": fraction_text(kappa),
                        "horizon": int(horizon_ms),
                        "deadline_profile": item.deadline_profile,
                        "deadline_alpha": (
                            fraction_text(item.deadline_alpha)
                            if item.deadline_alpha is not None else None
                        ),
                        "priority_policy": normalized_policy,
                        "scheduler": scheduler,
                    }
                    rows.append({
                        "request_id": "deadline-sensitivity-" + domain_hash(
                            "ASAP_BLOCK:V9.3:DEADLINE_REQUEST:v2", identity,
                        )[:32],
                        "base_taskset_id": item.base_taskset_id,
                        "base_taskset_hash": item.base_taskset_hash,
                        "projected_taskset_id": item.projected_taskset_id,
                        "projected_taskset_hash": item.projected_taskset_hash,
                        "taskset_id": item.projected_taskset_id,
                        "taskset_hash": item.projected_taskset_hash,
                        "taskset_index": item.taskset_index,
                        "generation_seed": item.seed,
                        "projection_mode": item.projection_mode,
                        "deadline_profile": item.deadline_profile,
                        "deadline_alpha": (
                            fraction_text(item.deadline_alpha)
                            if item.deadline_alpha is not None else None
                        ),
                        "deadline_lambda": (
                            fraction_text(item.deadline_lambda)
                            if item.deadline_lambda is not None else None
                        ),
                        "target_uc": fraction_text(target_uc),
                        "target_ue": fraction_text(target_ue),
                        "eta": fraction_text(Fraction(1, 1) / target_ue),
                        "kappa": fraction_text(kappa),
                        "scheduler": scheduler,
                        "scheduler_cli": perf_g.SCHEDULER_CLI[scheduler],
                        "priority_policy": normalized_policy,
                        "horizon": int(horizon_ms),
                        "horizon_ms": int(horizon_ms),
                    })
    expected = sum(
        len(projected_by_uc[target_uc]) * len(policies) * len(schedulers)
        for target_uc, _target_ue in cells
    )
    if len(rows) != expected or len({row["request_id"] for row in rows}) != len(rows):
        raise RuntimeError("sensitivity request plan is not complete and unique")
    return rows


def _build_energy_material(
    profiles: Sequence[ProjectedTaskset],
    target_ue: Fraction,
    kappa: Fraction,
    raw_trace: Sequence[Fraction],
    raw_trace_id: str,
) -> dict[str, Any]:
    first = profiles[0]
    materials = [
        load_cross.energy_material(
            profile, target_ue, raw_trace, kappa=kappa, raw_trace_id=raw_trace_id,
        )
        for profile in profiles
    ]
    if any(canonical_json(material) != canonical_json(materials[0]) for material in materials[1:]):
        raise RuntimeError("deadline projection changed energy material")
    return {"base_taskset_id": first.base_taskset_id, "material": materials[0]}


def _make_job(
    output: Path,
    request: Mapping[str, Any],
    profile: ProjectedTaskset,
    energy: Mapping[str, Any],
    service_path: Path,
    simulator: Path,
    timeout_seconds: int,
    keep_traces: bool,
) -> dict[str, Any]:
    request_id = str(request["request_id"])
    attempt_root = output / "simulations" / request_id / "attempt_0001"
    attempt_root.mkdir(parents=True, exist_ok=False)
    material = energy["material"]
    horizon = int(request["horizon"])
    return {
        "request": dict(request),
        "request_id": request_id,
        "simulation_id": request_id,
        "run_root": str(attempt_root),
        "base_system_path": str(service_path),
        "task_payload": profile.task_payload,
        "taskset_hash": profile.projected_taskset_hash,
        "processors": profile.processors,
        "exact_e0": Fraction(material["initial_energy_j"]),
        "energy_config": {
            "simulation_initial_battery": material["initial_energy_j"],
            "battery_capacity": material["battery_capacity_j"],
            "allow_harvest_clipping": True,
            "service_curve": {
                "solar_scale": material["solar_scale"],
                "use_real_solar_data": True,
            },
        },
        "simulation_config": {
            "simulator_bin": str(simulator), "horizon": horizon,
            "maximum_horizon": horizon, "horizon_extension_policy": "none",
            "priority_policy": str(request["priority_policy"]), "warmup": 0,
            "minimum_jobs_per_task": 1, "trace_mode": "semantic",
            "trace_on_failure": keep_traces, "retain_trace": keep_traces,
            "timeout_seconds": timeout_seconds,
            "cleanup_transient_artifacts": True,
        },
        "scheduler_id": request["scheduler_cli"],
    }


def _result_row(job: Mapping[str, Any], execution: Any, technical: str | None) -> dict[str, Any]:
    request = dict(job["request"])
    task_payload = job["task_payload"]
    horizon = int(request["horizon"])
    if execution is None:
        outcome = evaluate_outcome(
            [], [str(row["task_id"]) for row in task_payload],
            horizon=horizon, minimum_adjudicable_jobs=1,
            simulation_completed=False, technical_error=technical,
            strict_wholepass=True,
        )
        return {
            **request, "simulation_status": "TECHNICAL_FAILURE",
            "technical_error": technical, "wholepass": outcome.get("wholepass"),
            "taskset_pass": outcome.get("taskset_pass"), "deadline_miss": False,
            "outcome": outcome,
        }
    status = execution.result.status.value
    technical_error = (
        execution.result.reason
        if status not in {SimulationStatus.PASS_OBSERVED.value, SimulationStatus.DEADLINE_MISS.value}
        else None
    )
    outcome = evaluate_outcome(
        [asdict(observation) for observation in execution.result.jobs],
        [str(row["task_id"]) for row in task_payload],
        horizon=horizon, minimum_adjudicable_jobs=1,
        simulation_completed=execution.result.simulation_completed,
        technical_error=technical_error, strict_wholepass=True,
    )
    if outcome.get("technical_failure"):
        technical_error = outcome.get("reason") or "wholepass_outcome_unavailable"
        status = "TECHNICAL_FAILURE"
    return {
        **request, "energy": job["energy"], "simulation_status": status,
        "simulation_reason": execution.result.reason,
        "technical_error": technical_error, "runtime_seconds": execution.runtime_seconds,
        "metrics": _persisted_metrics(execution.result.metrics), "outcome": outcome,
        "wholepass": outcome.get("wholepass", outcome.get("taskset_pass")),
        "taskset_pass": outcome.get("taskset_pass"),
        "deadline_miss": status == SimulationStatus.DEADLINE_MISS.value,
    }


def validate_implicit_outcomes(
    results: Sequence[Mapping[str, Any]], *,
    expected_base_tasksets: int | None = None,
    expected_target_ues: Sequence[str] | None = None,
    schedulers: Sequence[str] | None = None,
    policies: Sequence[str] | None = None,
) -> str:
    implicit = [row for row in results if row.get("deadline_profile") == "IMPLICIT"]
    if not implicit:
        return "NOT_REQUESTED"
    active_policies = set(policies or {str(row["priority_policy"]) for row in implicit})
    if not {"RM", "DM"}.issubset(active_policies):
        return "NOT_APPLICABLE_POLICY_SUBSET"
    groups: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = {}
    for row in implicit:
        key = (
            str(row["base_taskset_id"]),
            str(row.get("target_ue", "")),
            str(row["scheduler"]),
        )
        groups.setdefault(key, {})[str(row["priority_policy"])] = row
    target_ues = set(expected_target_ues or {key[1] for key in groups})
    expected = (
        (expected_base_tasksets or len({key[0] for key in groups}))
        * len(target_ues) * len(schedulers or {key[2] for key in groups})
    )
    if len(groups) != expected or any(set(rows) != {"RM", "DM"} for rows in groups.values()):
        raise RuntimeError("IMPLICIT RM/DM result pairing is incomplete")
    for rows in groups.values():
        rm, dm = rows["RM"], rows["DM"]
        if rm.get("wholepass") != dm.get("wholepass"):
            raise RuntimeError("IMPLICIT RM/DM wholepass invariant failed")
        if rm.get("deadline_miss") != dm.get("deadline_miss"):
            raise RuntimeError("IMPLICIT RM/DM deadline-miss invariant failed")
    return "PASS"


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _worktree_clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_parser() -> ArgumentParser:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--projection-mode", choices=("relax-original", "fixed-common-lambda"), default="relax-original")
    parser.add_argument("--cells", default=DEFAULT_CELLS)
    parser.add_argument("--alphas", default=DEFAULT_ALPHAS)
    parser.add_argument("--samples-per-cell", type=int, default=DEFAULT_SAMPLES_PER_CELL)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--processors", type=int, default=DEFAULT_PROCESSORS)
    parser.add_argument("--tasks", type=int, default=DEFAULT_TASKS)
    parser.add_argument("--period-min", type=int, default=DEFAULT_PERIOD_MIN)
    parser.add_argument("--period-max", type=int, default=DEFAULT_PERIOD_MAX)
    parser.add_argument("--min-task-util", default=str(perf_g.MIN_TASK_UTILIZATION))
    parser.add_argument("--max-task-util", default=str(perf_g.MAX_TASK_UTILIZATION))
    parser.add_argument("--util-tolerance-total", default=str(perf_g.UTILIZATION_TOLERANCE))
    parser.add_argument("--kappa", default=str(DEFAULT_KAPPA))
    parser.add_argument("--simulation-horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--schedulers", default=",".join(DEFAULT_SCHEDULERS))
    parser.add_argument("--priority-policies", default=",".join(DEFAULT_POLICIES))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--prepare-workers", type=int, default=None)
    parser.add_argument("--parse-concurrency", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--simulator", type=Path, default=ROOT / "build/rtsim/rtsim")
    parser.add_argument("--keep-traces", action="store_true")
    return parser


def _profiles_for_mode(taskset: Any, mode: str, alphas: Sequence[Fraction]) -> tuple[ProjectedTaskset, ...]:
    if mode == "relax-original":
        return project_profiles_from_original_deadline(taskset, alphas)
    return project_profiles_with_fixed_lambdas(taskset, alphas)


def _profile_names_for_mode(mode: str, values: Sequence[Fraction]) -> tuple[str, ...]:
    if mode == "relax-original":
        return tuple(_profile_name(value) for value in values)
    return tuple(fixed_profile_name_for_lambda(value) for value in values)


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        cells = load_cross.parse_cells(args.cells)
        alphas = parse_alphas(args.alphas)
        policies = parse_priority_policies(args.priority_policies)
        schedulers = load_cross.parse_schedulers(args.schedulers)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if min(args.workers, args.samples_per_cell, args.processors, args.tasks) < 1:
        raise SystemExit("workers, samples-per-cell, processors and tasks must be positive")
    if args.period_min <= 0 or args.period_max < args.period_min:
        raise SystemExit("period range is invalid")
    if args.simulation_horizon <= 0 or args.timeout_seconds <= 0 or args.parse_concurrency < 1:
        raise SystemExit("simulation-horizon, timeout-seconds and parse-concurrency must be positive")
    try:
        validate_workers(args.workers if args.prepare_workers is None else args.prepare_workers, "prepare-workers")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    prepare_workers = args.workers if args.prepare_workers is None else args.prepare_workers
    min_util = _fraction_arg(args.min_task_util, "min-task-util")
    max_util = _fraction_arg(args.max_task_util, "max-task-util")
    tolerance = _fraction_arg(args.util_tolerance_total, "util-tolerance-total")
    kappa = _fraction_arg(args.kappa, "kappa")
    if min_util > max_util:
        raise SystemExit("min-task-util must not exceed max-task-util")
    output = args.output
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output exists and is not empty; choose a new exploratory run root")
    if not args.simulator.is_file():
        raise SystemExit(f"simulator does not exist: {args.simulator}")
    if not _worktree_clean():
        raise SystemExit("actual sensitivity execution requires a clean worktree")

    unique_ucs = tuple(dict.fromkeys(uc for uc, _ue in cells))
    expected_count = sum(
        args.samples_per_cell * len(alphas) * len(policies) * len(schedulers)
        for _uc, _ue in cells
    )
    config = {
        "schema": "ASAP_BLOCK_V9_3_DEADLINE_SENSITIVITY_V2",
        "exploratory": True, "projection_mode": args.projection_mode,
        "cells": [{"target_uc": fraction_text(uc), "target_ue": fraction_text(ue)} for uc, ue in cells],
        "alphas": [fraction_text(alpha) for alpha in alphas],
        "profiles": list(_profile_names_for_mode(args.projection_mode, alphas)),
        "samples_per_cell": args.samples_per_cell, "seed": args.seed,
        "processors": args.processors, "tasks": args.tasks,
        "period_min": args.period_min, "period_max": args.period_max,
        "min_task_util": fraction_text(min_util), "max_task_util": fraction_text(max_util),
        "util_tolerance_total": fraction_text(tolerance), "kappa": fraction_text(kappa),
        "simulation_horizon": args.simulation_horizon,
        "schedulers": list(schedulers), "priority_policies": list(policies),
        "workers": args.workers, "prepare_workers": prepare_workers,
        "parse_concurrency": args.parse_concurrency, "timeout_seconds": args.timeout_seconds,
        "simulator": str(args.simulator), "simulator_sha256": _sha256(args.simulator),
        "git_head": _git_head(), "git_worktree_clean": _worktree_clean(),
        "expected_request_count": expected_count, "status": "preparing",
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "run_config.json", config)
    started = time.perf_counter()
    material = output / "material"
    tasksets, service = load_cross.materialize_tasksets(
        material, seed=args.seed, utilizations=unique_ucs, count=args.samples_per_cell,
        processors=args.processors, tasks=args.tasks, period_min=args.period_min,
        period_max=args.period_max, min_task_util=min_util, max_task_util=max_util,
        tolerance=tolerance, prepare_workers=prepare_workers,
    )
    tasksets_by_uc: dict[Fraction, list[Any]] = {uc: [] for uc in unique_ucs}
    for taskset in tasksets:
        actual_uc = Fraction(taskset.target_utilization, taskset.processors)
        if actual_uc not in tasksets_by_uc:
            raise RuntimeError(f"unexpected base taskset U_C={actual_uc}")
        tasksets_by_uc[actual_uc].append(taskset)
    if any(len(values) != args.samples_per_cell for values in tasksets_by_uc.values()):
        raise RuntimeError("base taskset count per unique U_C is incorrect")
    write_jsonl(output / "base_tasksets.jsonl", [
        load_cross.taskset_row(taskset, args.processors)
        for uc in unique_ucs for taskset in tasksets_by_uc[uc]
    ])

    projected_by_uc: dict[Fraction, tuple[ProjectedTaskset, ...]] = {}
    profile_by_key: dict[tuple[Fraction, str, str], ProjectedTaskset] = {}
    projected_rows: list[dict[str, Any]] = []
    for uc in unique_ucs:
        for taskset in tasksets_by_uc[uc]:
            profiles = _profiles_for_mode(taskset, args.projection_mode, alphas)
            projected_by_uc[uc] = projected_by_uc.get(uc, ()) + profiles
            for profile in profiles:
                profile_by_key[(uc, profile.base_taskset_id, profile.deadline_profile)] = profile
                projected_rows.append(_projected_row(profile))
    write_jsonl(output / "projected_tasksets.jsonl", projected_rows)
    expected_projected = len(unique_ucs) * args.samples_per_cell * len(alphas)
    if len(projected_rows) != expected_projected:
        raise RuntimeError("projected taskset count is incorrect")
    requests = build_requests(
        projected_by_uc, cells, policies=policies, schedulers=schedulers,
        horizon_ms=args.simulation_horizon, kappa=kappa,
    )
    if len(requests) != expected_count:
        raise RuntimeError("request count does not match experiment contract")
    write_jsonl(output / "requests.jsonl", requests)

    raw_trace = load_cross.construct_paired_harvest_trace(
        service.system_path, load_cross.FORMAL_NORMALIZATION_HORIZON,
    )
    raw_trace_id = load_cross.harvest_trace_identity(raw_trace)
    energy_by_key: dict[tuple[Fraction, str, str], dict[str, Any]] = {}
    for uc in unique_ucs:
        for taskset in tasksets_by_uc[uc]:
            family = [
                profile for profile in projected_by_uc[uc]
                if profile.base_taskset_id == taskset.taskset_id
            ]
            for cell_uc, target_ue in cells:
                if cell_uc == uc:
                    energy_by_key[(uc, taskset.taskset_id, fraction_text(target_ue))] = _build_energy_material(
                        family, target_ue, kappa, raw_trace, raw_trace_id,
                    )

    jobs = []
    for request in requests:
        uc = Fraction(request["target_uc"])
        profile = profile_by_key[(uc, request["base_taskset_id"], request["deadline_profile"])]
        energy = energy_by_key[(uc, request["base_taskset_id"], request["target_ue"])]
        jobs.append(_make_job(
            output, request, profile, energy, service.system_path,
            args.simulator, args.timeout_seconds, args.keep_traces,
        ))
    multiprocessing_context = multiprocessing.get_context("fork")
    parse_semaphore = multiprocessing_context.Semaphore(args.parse_concurrency)
    results_by_id: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(
        max_workers=args.workers, mp_context=multiprocessing_context,
        initializer=_initialize_simulation_worker, initargs=(parse_semaphore,),
    ) as executor:
        future_to_job = {executor.submit(_run_simulation_job, job): job for job in jobs}
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                execution, technical = future.result()
            except Exception as exc:
                execution, technical = None, f"{type(exc).__name__}: {exc}"
            request = job["request"]
            key = (Fraction(request["target_uc"]), request["base_taskset_id"], request["target_ue"])
            job["energy"] = energy_by_key[key]["material"]
            row = _result_row(job, execution, technical)
            results_by_id[str(row["request_id"])] = row
    results = [results_by_id[row["request_id"]] for row in requests]
    technical_failures = sum(bool(row.get("technical_error")) for row in results)
    duplicate_request_ids = len(results) - len({row["request_id"] for row in results})
    if len(results) != expected_count or technical_failures or duplicate_request_ids:
        write_jsonl(output / "results.jsonl", results)
        failure_report = {
            "complete": False,
            "expected_results": expected_count,
            "observed_results": len(results),
            "technical_failures": technical_failures,
            "duplicate_request_ids": duplicate_request_ids,
        }
        write_json(output / "invariant_report.json", failure_report)
        config["status"] = "failed"
        write_json(output / "run_config.json", config)
        raise RuntimeError("sensitivity execution failed closed before completion")
    implicit_status = (
        validate_implicit_outcomes(
            results, expected_base_tasksets=len(tasksets),
            expected_target_ues=sorted({fraction_text(ue) for _uc, ue in cells}),
            schedulers=schedulers, policies=policies,
        ) if 1 in alphas else "NOT_REQUESTED"
    )
    write_jsonl(output / "results.jsonl", results)
    report = {
        "complete": True, "expected_results": expected_count,
        "observed_results": len(results),
        "technical_failures": technical_failures,
        "duplicate_request_ids": duplicate_request_ids,
        "unique_base_tasksets": len(tasksets), "unique_projected_tasksets": len(projected_rows),
        "cells": [{"target_uc": fraction_text(uc), "target_ue": fraction_text(ue)} for uc, ue in cells],
        "alpha_count": len(alphas), "policy_count": len(policies), "scheduler_count": len(schedulers),
        "energy_material_paired": True, "profile_pairing_valid": True,
        "original_profile_exact_base_payload": True if 0 in alphas else "NOT_REQUESTED",
        "implicit_priority_order_equal": True if 1 in alphas else "NOT_REQUESTED",
        "implicit_rm_dm_wholepass_equal": implicit_status if 1 in alphas else "NOT_REQUESTED",
        "implicit_rm_dm_deadline_miss_equal": implicit_status if 1 in alphas else "NOT_REQUESTED",
        "elapsed_seconds": time.perf_counter() - started,
    }
    write_json(output / "invariant_report.json", report)
    config["status"] = "complete"
    config["telemetry"] = {"total_seconds": report["elapsed_seconds"]}
    write_json(output / "run_config.json", config)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
