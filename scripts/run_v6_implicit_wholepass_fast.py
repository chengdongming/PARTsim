"""Run the read-only v6 RM implicit WholePass compact-result overlay."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
from pathlib import Path
import time
from fractions import Fraction
from typing import Any, Mapping

from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3 import simulation_engine
from experiments.v9_3.implicit_wholepass_fast import (
    FAST_MODE,
    FastWholePassError,
    validate_fast_document,
)
from experiments.v9_3.task_identity import runtime_task_name_for_source_id


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line at {path}:{number}")
        value = json.loads(line, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object at {path}:{number}")
        rows.append(value)
    return rows


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
        handle.flush()


def _assert_request_match(row: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    for key, value in expected.items():
        if row.get(key) != value:
            raise ValueError(
                f"request identity mismatch for {expected['request_id']}: {key}"
            )


def _technical(row: Mapping[str, Any]) -> bool:
    return (
        row.get("technical_error") is not None
        or row.get("taskset_pass") is None
        or str(row.get("simulation_status", "")) in {
            "TECHNICAL_FAILURE", "SIM_INTERNAL_ERROR", "SIM_RUNTIME_TIMEOUT",
            "SIM_HORIZON_INSUFFICIENT",
        }
    )


def _partition_baseline_ids(
    baseline_ids: set[str], expected_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Partition mixed baseline IDs using canonical request deadline modes."""
    expected_implicit_ids = {
        request_id for request_id, request in expected_by_id.items()
        if request.get("deadline_mode") == "implicit"
    }
    expected_constrained_ids = {
        request_id for request_id, request in expected_by_id.items()
        if request.get("deadline_mode") == "constrained"
    }
    if baseline_ids - (expected_implicit_ids | expected_constrained_ids):
        raise ValueError("baseline contains an unknown deadline-mode request ID")
    baseline_implicit_ids = baseline_ids & expected_implicit_ids
    baseline_constrained_ids = baseline_ids & expected_constrained_ids
    return (
        expected_implicit_ids, expected_constrained_ids,
        baseline_implicit_ids, baseline_constrained_ids,
    )


def _validate_overlay_ids(
    overlay_ids: set[str], expected_implicit_ids: set[str],
) -> None:
    if not overlay_ids <= expected_implicit_ids:
        raise ValueError("overlay contains a non-implicit request ID")


def _validate_overlay_overlap(
    baseline_implicit_ids: set[str], overlay_ids: set[str],
) -> None:
    if baseline_implicit_ids & overlay_ids:
        raise ValueError("formal baseline and fast overlay overlap")


def _fast_worker(job: Mapping[str, Any]) -> dict[str, Any]:
    request = job["request"]
    taskset = job["taskset"]
    energy = job["energy"]
    simulation_config = {
        "simulator_bin": str(job["simulator"]),
        "horizon": int(request["horizon_ms"]),
        "maximum_horizon": int(request["horizon_ms"]),
        "horizon_extension_policy": "none",
        "priority_policy": "RM",
        "deadline_mode": "implicit",
        "campaign": "v6",
        "wholepass_mode": "hard-rt",
        "warmup": 0,
        "minimum_jobs_per_task": 1,
        "trace_mode": "none",
        "timeout_seconds": int(job["timeout_seconds"]),
        "cleanup_transient_artifacts": True,
    }
    execution = simulation_engine.run_paired_simulation(
        simulation_id_value=str(request["request_id"]),
        base_system_path=Path(str(job["system_path"])),
        run_root=Path(str(job["run_root"])),
        task_payload=tuple(taskset["task_payload"]),
        taskset_hash=str(request["taskset_hash"]),
        processors=int(taskset["processors"]),
        exact_e0=Fraction(energy["initial_energy_j"]),
        energy_config={
            "simulation_initial_battery": energy["initial_energy_j"],
            "battery_capacity": energy["battery_capacity_j"],
            "allow_harvest_clipping": True,
            "service_curve": {
                "solar_scale": energy["solar_scale"],
                "use_real_solar_data": False,
                "require_real_solar_data": False,
                **experiment.HARVEST_MODEL_IDENTITY,
            },
        },
        simulation_config=simulation_config,
        scheduler_id=str(request["scheduler_cli"]),
        implicit_wholepass_fast=True,
    )
    result = dict(execution.result)
    if type(result.get("taskset_pass")) is not bool:
        raise RuntimeError("fast result does not contain a scientific boolean")
    return {
        "request": dict(request),
        "fast_result": result,
        "runtime_seconds": execution.runtime_seconds,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--overlay-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=None)
    parser.add_argument("--simulator", type=Path, default=Path("build/rtsim/rtsim"))
    return parser


def _prepare_requests(config: Mapping[str, Any], overlay_root: Path) -> tuple[
    list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any], Any,
]:
    if config.get("experiment") != experiment.V6_EXPERIMENT:
        raise ValueError("fast overlay requires the v6 experiment")
    if config.get("domain") != experiment.V6_DOMAIN:
        raise ValueError("fast overlay requires the v6 domain")
    if config.get("priority_policy") != "RM":
        raise ValueError("fast overlay requires RM")
    if experiment.run_identity(config) != config.get("run_identity"):
        raise ValueError("formal run_config identity is invalid")
    cells = tuple(
        (Fraction(str(row[0])), Fraction(str(row[1])))
        for row in config.get("cells", ())
    )
    schedulers = tuple(config.get("schedulers", ()))
    if schedulers != tuple(experiment.perf_g.FORMAL_SCHEDULERS):
        raise ValueError("fast overlay requires all canonical schedulers")
    if not cells or config.get("deadline_modes") != ["constrained", "implicit"]:
        raise ValueError("formal v6 RM mode plan is invalid")
    kwargs = {
        "seed": int(config["seed"]),
        "utilizations": tuple(dict.fromkeys(uc for uc, _ in cells)),
        "count": int(config["samples_per_cell"]),
        "processors": int(config["processors"]),
        "tasks": int(config["tasks"]),
        "period_min": int(config["period_min"]),
        "period_max": int(config["period_max"]),
        "min_task_util": Fraction(str(config["min_task_util"])),
        "max_task_util": Fraction(str(config["max_task_util"])),
        "tolerance": Fraction(str(config["util_tolerance_total"])),
        "prepare_workers": 1,
    }
    tasksets_by_mode: dict[str, list[Any]] = {}
    service = None
    for mode in ("constrained", "implicit"):
        tasksets, mode_service = experiment.materialize_tasksets(
            overlay_root / "material" / mode, deadline_mode=mode, **kwargs
        )
        if service is not None and mode_service.identity != service.identity:
            raise ValueError("v6 modes do not share service identity")
        service = mode_service
        tasksets_by_mode[mode] = tasksets
    assert service is not None
    common = {
        "horizon": int(config["simulation_horizon_ms"]),
        "priority_policy": "RM",
        "experiment_name": experiment.V6_EXPERIMENT,
    }
    requests: list[dict[str, Any]] = []
    for mode in ("constrained", "implicit"):
        requests.extend(experiment.request_rows(
            tasksets_by_mode[mode], cells, schedulers,
            deadline_mode=mode, **common,
        ))
    expected = int(config["expected_request_count"])
    if len(requests) != expected:
        raise ValueError(f"request count mismatch: {len(requests)} != {expected}")
    return (
        requests,
        {str(item["request_id"]): item for item in requests},
        {taskset.taskset_id: taskset
         for values in tasksets_by_mode.values() for taskset in values},
        service,
    )


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.workers < 1 or (
            args.timeout_seconds is not None and args.timeout_seconds < 1):
        raise SystemExit("workers and timeout-seconds must be positive")
    formal_root = args.formal_root
    overlay_root = args.overlay_root
    config = _load_json(formal_root / "run_config.json")
    requests, expected_by_id, tasksets_by_id, service = _prepare_requests(
        config, overlay_root
    )
    del requests
    all_formal = _read_jsonl(formal_root / "results.jsonl")
    baseline: dict[str, dict[str, Any]] = {}
    for row in all_formal:
        request_id = str(row.get("request_id", ""))
        if request_id in baseline or request_id not in expected_by_id:
            raise ValueError("formal results contain duplicate or unknown request ID")
        _assert_request_match(row, expected_by_id[request_id])
        if _technical(row) or type(row.get("taskset_pass")) is not bool:
            raise ValueError("formal results contain a technical or incomplete row")
        baseline[request_id] = row
    (
        implicit_ids, constrained_ids, baseline_implicit_ids,
        baseline_constrained_ids,
    ) = _partition_baseline_ids(set(baseline), expected_by_id)
    if baseline_constrained_ids != constrained_ids:
        raise ValueError("constrained formal result set is not complete")
    overlay_path = overlay_root / "implicit_wholepass_fast_results.jsonl"
    overlay_rows = _read_jsonl(overlay_path)
    overlay: dict[str, dict[str, Any]] = {}
    raw_trace = tuple(experiment.construct_paired_harvest_trace(
        service.system_path, experiment.FORMAL_NORMALIZATION_HORIZON,
    ))
    raw_trace_id = experiment.harvest_trace_identity(raw_trace)
    experiment.set_prepare_raw_trace(raw_trace)
    for row in overlay_rows:
        request_id = str(row.get("request_id", ""))
        if request_id in overlay or request_id not in implicit_ids:
            raise ValueError("overlay contains duplicate, unknown, or constrained ID")
        _assert_request_match(row, expected_by_id[request_id])
        if row.get("fast_mode") != FAST_MODE or row.get("technical_error") is not None:
            raise ValueError("overlay row is not a valid fast scientific row")
        taskset = tasksets_by_id[expected_by_id[request_id]["taskset_id"]]
        fast_result = row.get("fast_result")
        try:
            validated = validate_fast_document(
                fast_result,
                expected_run_id=f"v93-{request_id[:16]}-h{config['simulation_horizon_ms']}",
                expected_taskset_hash=str(expected_by_id[request_id]["taskset_hash"]),
                expected_scheduler=str(expected_by_id[request_id]["scheduler_cli"]),
                expected_processors=int(taskset.processors),
                expected_task_ids=[
                    runtime_task_name_for_source_id(item["task_id"])
                    for item in taskset.task_payload
                ],
                expected_horizon=int(config["simulation_horizon_ms"]),
            )
        except (FastWholePassError, TypeError) as exc:
            raise ValueError(f"invalid overlay compact result: {exc}") from exc
        if row.get("taskset_pass") != validated["taskset_pass"]:
            raise ValueError("overlay WholePass does not match compact result")
        overlay[request_id] = row
    overlay_ids = set(overlay)
    _validate_overlay_ids(overlay_ids, implicit_ids)
    _validate_overlay_overlap(baseline_implicit_ids, overlay_ids)
    missing = implicit_ids - baseline_implicit_ids - overlay_ids
    simulator = args.simulator.resolve()
    if not simulator.is_file():
        raise SystemExit(f"simulator binary not found: {simulator}")
    timeout = args.timeout_seconds or int(config.get("timeout_seconds", 3600))
    jobs = []
    for request_id in sorted(missing):
        request = expected_by_id[request_id]
        taskset = tasksets_by_id[request["taskset_id"]]
        energy = experiment.energy_material(
            taskset, Fraction(request["target_ue"]), raw_trace,
            kappa=Fraction(str(config["kappa"])), raw_trace_id=raw_trace_id,
        )
        jobs.append({
            "request": request,
            "taskset": {
                "task_payload": tuple(taskset.task_payload),
                "processors": taskset.processors,
            },
            "energy": energy,
            "system_path": str(service.system_path),
            "run_root": str(overlay_root / "simulations" / request_id),
            "simulator": str(simulator),
            "timeout_seconds": timeout,
        })
    started = time.perf_counter()
    overlay_root.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=context) as pool:
        futures = {pool.submit(_fast_worker, job): job for job in jobs}
        for future in as_completed(futures):
            result = future.result()
            request = result["request"]
            fast = result["fast_result"]
            taskset = tasksets_by_id[request["taskset_id"]]
            validate_fast_document(
                fast,
                expected_run_id=f"v93-{request['request_id'][:16]}-h{config['simulation_horizon_ms']}",
                expected_taskset_hash=str(request["taskset_hash"]),
                expected_scheduler=str(request["scheduler_cli"]),
                expected_processors=int(taskset.processors),
                expected_task_ids=[
                    runtime_task_name_for_source_id(item["task_id"])
                    for item in taskset.task_payload
                ],
                expected_horizon=int(config["simulation_horizon_ms"]),
            )
            row = {
                **request,
                "fast_mode": FAST_MODE,
                "simulation_status": (
                    "PASS_OBSERVED" if fast["taskset_pass"] else "DEADLINE_MISS"
                ),
                "simulation_reason": fast["completion_reason"],
                "technical_error": None,
                "runtime_seconds": result["runtime_seconds"],
                "schedulable": fast["taskset_pass"],
                "taskset_pass": fast["taskset_pass"],
                "wholepass": fast["taskset_pass"],
                "fast_result": fast,
            }
            _append_jsonl(overlay_path, row)
            overlay[request["request_id"]] = row
    overlay_ids = set(overlay)
    complete_implicit_ids = baseline_implicit_ids | overlay_ids
    if complete_implicit_ids & constrained_ids:
        raise ValueError("constrained ID entered the implicit completion union")
    union = complete_implicit_ids
    if union != implicit_ids or len(union) != len(implicit_ids):
        raise ValueError("formal/overlay implicit union is incomplete or duplicated")
    elapsed = time.perf_counter() - started
    print(json.dumps({
        "expected_implicit": len(implicit_ids),
        "baseline_implicit": len(baseline_implicit_ids),
        "baseline_constrained": len(baseline_constrained_ids),
        "fast_overlay": len(overlay_ids),
        "union": len(union),
        "elapsed_seconds": elapsed,
        "technical_fast_rows": 0,
        "constrained_overlay_rows": 0,
        "metric": "WHOLE_TASKSET_PASS_RATIO",
        "deadline_mode": "implicit",
        "hard_real_time": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
