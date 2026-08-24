#!/usr/bin/env python3
"""Run the paired normalized-slack deadline-profile sensitivity screen.

This is an exploratory runner for one N=20 cell.  It intentionally prepares
one canonical base taskset population and projects deadline profiles from it;
it never changes the formal scheduler LOAD-CROSS runner or its defaults.
"""

from __future__ import annotations

from argparse import ArgumentParser
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from fractions import Fraction
import json
import multiprocessing
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g, scheduler_load_cross as load_cross
from experiments.v9_3.config import canonical_json, domain_hash, fraction_text
from experiments.v9_3.deadline_profile_sensitivity import (
    PROFILE_ORDER,
    ProjectedTaskset,
    project_profiles,
)
from experiments.v9_3.parallel_prepare import validate_workers
from experiments.v9_3.simulation_engine import SimulationStatus
from experiments.v9_3.performance_outcome import evaluate_outcome
from scripts.run_scheduler_load_cross import (
    _initialize_simulation_worker,
    _persisted_metrics,
    _run_simulation_job,
)


BASE_UC = Fraction(3, 10)
BASE_UE = Fraction(7, 10)
BASE_SEED = 20260823
BASE_COUNT = 20
BASE_PROCESSORS = 4
BASE_TASKS = 10
BASE_PERIOD_MIN = 40
BASE_PERIOD_MAX = 200
BASE_KAPPA = Fraction(10)
SENSITIVITY_SCHEDULERS = (
    "ASAP-BLOCK", "ASAP-NONBLOCK", "ST-NONBLOCK",
)
SENSITIVITY_POLICIES = ("RM", "DM")
SENSITIVITY_HORIZON = 60000


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


def _projected_row(projected: ProjectedTaskset) -> dict[str, Any]:
    return projected.row()


def build_requests(
    projected: Sequence[ProjectedTaskset],
    *,
    horizon_ms: int = SENSITIVITY_HORIZON,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in projected:
        for policy in SENSITIVITY_POLICIES:
            for scheduler in SENSITIVITY_SCHEDULERS:
                identity = {
                    "base_taskset_id": item.base_taskset_id,
                    "projected_taskset_hash": item.projected_taskset_hash,
                    "deadline_profile": item.deadline_profile,
                    "priority_policy": policy,
                    "scheduler": scheduler,
                }
                rows.append({
                    "request_id": "deadline-profile-sensitivity-" + domain_hash(
                        "ASAP_BLOCK:V9.3:DEADLINE_PROFILE_REQUEST:v1", identity,
                    )[:32],
                    "base_taskset_id": item.base_taskset_id,
                    "base_taskset_hash": item.base_taskset_hash,
                    "projected_taskset_id": item.projected_taskset_id,
                    "projected_taskset_hash": item.projected_taskset_hash,
                    "taskset_id": item.projected_taskset_id,
                    "taskset_hash": item.projected_taskset_hash,
                    "taskset_index": item.taskset_index,
                    "generation_seed": item.seed,
                    "deadline_profile": item.deadline_profile,
                    "deadline_lambda": fraction_text(item.deadline_lambda),
                    "target_uc": fraction_text(BASE_UC),
                    "target_ue": fraction_text(BASE_UE),
                    "eta": fraction_text(Fraction(1, 1) / BASE_UE),
                    "scheduler": scheduler,
                    "scheduler_cli": perf_g.SCHEDULER_CLI[scheduler],
                    "priority_policy": policy,
                    "horizon_ms": horizon_ms,
                })
    expected = len(projected) * len(SENSITIVITY_POLICIES) * len(SENSITIVITY_SCHEDULERS)
    if len(rows) != expected or len({row["request_id"] for row in rows}) != len(rows):
        raise RuntimeError("sensitivity request plan is not complete and unique")
    return rows


def _build_energy_material(
    profiles: Sequence[ProjectedTaskset],
    raw_trace: Sequence[Fraction],
    raw_trace_id: str,
) -> dict[str, Any]:
    first = profiles[0]
    materials = [
        load_cross.energy_material(
            profile, BASE_UE, raw_trace, kappa=BASE_KAPPA,
            raw_trace_id=raw_trace_id,
        )
        for profile in profiles
    ]
    if any(canonical_json(material) != canonical_json(materials[0]) for material in materials[1:]):
        raise RuntimeError("deadline projection changed energy material")
    return {
        "base_taskset_id": first.base_taskset_id,
        "material": materials[0],
    }


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
    simulation = {
        "simulator_bin": str(simulator),
        "horizon": SENSITIVITY_HORIZON,
        "maximum_horizon": SENSITIVITY_HORIZON,
        "horizon_extension_policy": "none",
        "priority_policy": str(request["priority_policy"]),
        "warmup": 0,
        "minimum_jobs_per_task": 1,
        "trace_mode": "semantic",
        "trace_on_failure": keep_traces,
        "retain_trace": keep_traces,
        "timeout_seconds": timeout_seconds,
        "cleanup_transient_artifacts": True,
    }
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
        "simulation_config": simulation,
        "scheduler_id": request["scheduler_cli"],
    }


def _result_row(job: Mapping[str, Any], execution: Any, technical: str | None) -> dict[str, Any]:
    request = dict(job["request"])
    task_payload = job["task_payload"]
    if execution is None:
        outcome = evaluate_outcome(
            [], [str(row["task_id"]) for row in task_payload],
            horizon=SENSITIVITY_HORIZON, minimum_adjudicable_jobs=1,
            simulation_completed=False, technical_error=technical,
            strict_wholepass=True,
        )
        return {
            **request, "simulation_status": "TECHNICAL_FAILURE",
            "technical_error": technical,
            "wholepass": outcome.get("wholepass"),
            "taskset_pass": outcome.get("taskset_pass"),
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
        horizon=SENSITIVITY_HORIZON, minimum_adjudicable_jobs=1,
        simulation_completed=execution.result.simulation_completed,
        technical_error=technical_error, strict_wholepass=True,
    )
    if outcome.get("technical_failure"):
        technical_error = outcome.get("reason") or "wholepass_outcome_unavailable"
        status = "TECHNICAL_FAILURE"
    return {
        **request,
        "energy": job["energy"],
        "simulation_status": status,
        "simulation_reason": execution.result.reason,
        "technical_error": technical_error,
        "runtime_seconds": execution.runtime_seconds,
        "metrics": _persisted_metrics(execution.result.metrics),
        "outcome": outcome,
        "wholepass": outcome.get("wholepass", outcome.get("taskset_pass")),
        "taskset_pass": outcome.get("taskset_pass"),
        "deadline_miss": status == SimulationStatus.DEADLINE_MISS.value,
    }


def validate_implicit_outcomes(results: Sequence[Mapping[str, Any]]) -> None:
    groups: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = {}
    for row in results:
        if row.get("deadline_profile") != "IMPLICIT":
            continue
        key = (str(row["base_taskset_id"]), str(row["scheduler"]))
        groups.setdefault(key, {})[str(row["priority_policy"])] = row
    expected = BASE_COUNT * len(SENSITIVITY_SCHEDULERS)
    if len(groups) != expected or any(set(rows) != set(SENSITIVITY_POLICIES) for rows in groups.values()):
        raise RuntimeError("IMPLICIT RM/DM result pairing is incomplete")
    for rows in groups.values():
        rm, dm = rows["RM"], rows["DM"]
        if rm.get("wholepass") != dm.get("wholepass"):
            raise RuntimeError("IMPLICIT RM/DM wholepass invariant failed")
        if rm.get("deadline_miss") != dm.get("deadline_miss"):
            raise RuntimeError("IMPLICIT RM/DM deadline-miss invariant failed")


def make_parser() -> ArgumentParser:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--prepare-workers", type=int, default=None)
    parser.add_argument("--simulator", type=Path, default=ROOT / "build/rtsim/rtsim")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--parse-concurrency", type=int, default=1)
    parser.add_argument("--keep-traces", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    prepare_workers = args.workers if args.prepare_workers is None else args.prepare_workers
    validate_workers(prepare_workers, "prepare-workers")
    if args.workers < 1 or args.timeout_seconds <= 0 or args.parse_concurrency < 1:
        raise SystemExit("workers, timeout-seconds and parse-concurrency must be positive")
    output = args.output
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output exists and is not empty; choose a new exploratory run root")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    config = {
        "schema": "ASAP_BLOCK_V9_3_DEADLINE_PROFILE_SENSITIVITY_V1",
        "exploratory": True,
        "base_uc": fraction_text(BASE_UC), "base_ue": fraction_text(BASE_UE),
        "seed": BASE_SEED, "tasksets": BASE_COUNT, "processors": BASE_PROCESSORS,
        "task_n": BASE_TASKS, "period_min": BASE_PERIOD_MIN,
        "period_max": BASE_PERIOD_MAX, "kappa": fraction_text(BASE_KAPPA),
        "horizon_ms": SENSITIVITY_HORIZON, "profiles": list(PROFILE_ORDER),
        "priority_policies": list(SENSITIVITY_POLICIES),
        "schedulers": list(SENSITIVITY_SCHEDULERS), "status": "preparing",
    }
    write_json(output / "run_config.json", config)

    material = output / "material"
    tasksets, service = load_cross.materialize_tasksets(
        material, seed=BASE_SEED, utilizations=[BASE_UC], count=BASE_COUNT,
        processors=BASE_PROCESSORS, tasks=BASE_TASKS,
        period_min=BASE_PERIOD_MIN, period_max=BASE_PERIOD_MAX,
        min_task_util=perf_g.MIN_TASK_UTILIZATION,
        max_task_util=perf_g.MAX_TASK_UTILIZATION,
        tolerance=perf_g.UTILIZATION_TOLERANCE, prepare_workers=prepare_workers,
    )
    if len(tasksets) != BASE_COUNT:
        raise RuntimeError("base taskset count is not 20")
    base_rows = [load_cross.taskset_row(taskset, BASE_PROCESSORS) for taskset in tasksets]
    write_jsonl(output / "base_tasksets.jsonl", base_rows)

    profile_by_key: dict[tuple[str, str], ProjectedTaskset] = {}
    projected_rows: list[dict[str, Any]] = []
    for taskset in tasksets:
        profiles = project_profiles(taskset)
        for profile in profiles:
            profile_by_key[(profile.base_taskset_id, profile.deadline_profile)] = profile
            projected_rows.append(_projected_row(profile))
    write_jsonl(output / "projected_tasksets.jsonl", projected_rows)
    projected = list(profile_by_key.values())
    if len(projected) != BASE_COUNT * len(PROFILE_ORDER):
        raise RuntimeError("projected profile count is not 80")
    requests = build_requests(projected)
    write_jsonl(output / "requests.jsonl", requests)

    raw_trace = load_cross.construct_paired_harvest_trace(
        service.system_path, load_cross.FORMAL_NORMALIZATION_HORIZON,
    )
    raw_trace_id = load_cross.harvest_trace_identity(raw_trace)
    energy_by_base: dict[str, dict[str, Any]] = {}
    for taskset in tasksets:
        family = [profile_by_key[(taskset.taskset_id, name)] for name in PROFILE_ORDER]
        energy_by_base[taskset.taskset_id] = _build_energy_material(
            family, raw_trace, raw_trace_id,
        )

    jobs = [
        _make_job(
            output, request, profile_by_key[(request["base_taskset_id"], request["deadline_profile"])],
            energy_by_base[request["base_taskset_id"]], service.system_path,
            args.simulator, args.timeout_seconds, args.keep_traces,
        )
        for request in requests
    ]
    multiprocessing_context = multiprocessing.get_context("fork")
    parse_semaphore = multiprocessing_context.Semaphore(args.parse_concurrency)
    results_by_id: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(
        max_workers=args.workers, mp_context=multiprocessing_context,
        initializer=_initialize_simulation_worker, initargs=(parse_semaphore,),
    ) as executor:
        future_to_job = {
            executor.submit(_run_simulation_job, job): job for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                execution, technical = future.result()
            except Exception as exc:
                execution, technical = None, f"{type(exc).__name__}: {exc}"
            job["energy"] = energy_by_base[job["request"]["base_taskset_id"]]["material"]
            row = _result_row(job, execution, technical)
            results_by_id[str(row["request_id"])] = row
            if row.get("technical_error"):
                raise RuntimeError(f"technical execution failure: {row['technical_error']}")
    results = [results_by_id[row["request_id"]] for row in requests]
    validate_implicit_outcomes(results)
    write_jsonl(output / "results.jsonl", results)
    report = {
        "expected_results": len(requests), "observed_results": len(results),
        "base_tasksets": len(tasksets), "profiles": len(PROFILE_ORDER),
        "priority_policies": len(SENSITIVITY_POLICIES),
        "schedulers": len(SENSITIVITY_SCHEDULERS),
        "implicit_rm_dm_wholepass_equal": True,
        "energy_material_paired": True, "complete": True,
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
