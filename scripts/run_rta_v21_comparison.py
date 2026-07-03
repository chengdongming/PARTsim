#!/usr/bin/env python3
"""Run isolated ASAP-BLOCK v20.4 versus v21-local-window comparisons."""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance


V20_VERSION = "v20.4"
V21_VERSION = "v21-local-window"
V21_TOOL = PROJECT_ROOT / "asap_block_rta_v21_local_window.py"
RESULT_FILENAME = "rta_v21_comparison_results.csv"
MANIFEST_FILENAME = "rta_v21_comparison_manifest.csv"

RESULT_FIELDS = [
    "experiment_name", "seed_base", "taskset_seed",
    "normalized_utilization", "task_idx", "taskset_id", "E0",
    "accepted", "simulation_status", "simulated_response_time",
    "deadline_miss_time", "first_missed_task", "taskset_path",
    "trace_path", "v20p4_rta_version", "v20p4_status",
    "v20p4_proven", "v20p4_error", "v20p4_reason", "v20p4_bound",
    "v20p4_tightness", "v21_rta_version", "v21_status",
    "v21_proven", "v21_error", "v21_reason", "v21_bound",
    "v21_tightness", "v21_minus_v20p4_bound",
    "v21_bound_lt_v20p4", "v21_bound_eq_v20p4",
    "v21_bound_gt_v20p4", "v21_proven_v20p4_unproven",
    "v20p4_proven_v21_unproven", "both_proven", "both_unproven",
    "v21_soundness_proven_but_rejected",
    "v21_soundness_observed_exceeds_bound",
    "v20p4_soundness_proven_but_rejected",
    "v20p4_soundness_observed_exceeds_bound",
]

MANIFEST_FIELDS = [
    "experiment_name", "run_dir", "results_file", "seed_base",
    "utilizations", "num_tasksets", "task_n", "battery",
    "initial_energy", "solar_time_ms", "E0_values",
    "rta_v20p4_timeout", "rta_v21_timeout", "status", "return_code",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare frozen v20.4 and experimental v21-local-window RTA "
            "on one shared ASAP-BLOCK simulation per taskset"
        )
    )
    parser.add_argument("--output-root", default="acceptance_ratio_runs_v21")
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument(
        "--utilizations", nargs="+", type=float, default=[0.1, 0.2, 0.3]
    )
    parser.add_argument("--num-tasksets", type=int, default=50)
    parser.add_argument("--task-n", type=int, default=10)
    parser.add_argument("--task-p-min", type=int, default=40)
    parser.add_argument("--task-p-max", type=int, default=400)
    parser.add_argument("--simulation-time", type=int, default=30000)
    parser.add_argument("--battery", type=float, default=20.0)
    parser.add_argument("--initial-energy", type=float, default=1.0)
    parser.add_argument("--solar-time-ms", type=int, default=21975000)
    parser.add_argument("--e0-values", nargs="+", type=float, required=True)
    parser.add_argument("--seed-base", type=int, default=424242)
    parser.add_argument("--rta-horizon-ms", type=int, default=30000)
    parser.add_argument("--rta-v20p4-timeout", type=int, default=60)
    parser.add_argument("--rta-v21-timeout", type=int, default=60)
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if "v21" not in args.experiment_name.lower():
        parser.error("--experiment-name must identify this as a v21 experiment")
    for name in (
        "num_tasksets", "task_n", "task_p_min", "task_p_max",
        "simulation_time", "rta_horizon_ms", "rta_v20p4_timeout",
        "rta_v21_timeout", "max_workers",
    ):
        if getattr(args, name) <= 0:
            parser.error("--{} must be positive".format(name.replace("_", "-")))
    if args.task_p_min > args.task_p_max:
        parser.error("--task-p-min cannot exceed --task-p-max")
    if args.battery <= 0 or not math.isfinite(args.battery):
        parser.error("--battery must be finite and positive")
    if not 0 <= args.initial_energy <= 1:
        parser.error("--initial-energy must be a ratio in [0, 1]")
    if any(not 0 < value <= 1 for value in args.utilizations):
        parser.error("--utilizations must be in (0, 1]")
    if any(not math.isfinite(value) or value < 0 for value in args.e0_values):
        parser.error("--e0-values must be finite and non-negative")
    forbidden = "rta-e0-sensitivity-v20p4"
    if forbidden in str(Path(args.output_root)).lower():
        parser.error("v21 comparisons cannot use the frozen v20.4 output root")


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _first_deadline_miss(events: Sequence[Mapping[str, Any]]) -> tuple:
    misses = [
        event for event in events
        if isinstance(event, dict) and event.get("event_type") == "dline_miss"
    ]
    if not misses:
        return "", ""
    event = min(misses, key=lambda item: float(item.get("time", math.inf)))
    return event.get("time", ""), event.get("task_name", "")


def _run_simulation(job: Mapping[str, Any]) -> Dict[str, Any]:
    trace_path = Path(job["trace_path"])
    env = os.environ.copy()
    lib_path = str((PROJECT_ROOT / "build/librtsim").resolve())
    env["LD_LIBRARY_PATH"] = lib_path + ":" + env.get("LD_LIBRARY_PATH", "")
    command = [
        str((PROJECT_ROOT / acceptance.SIMULATOR).resolve()),
        str(job["config_path"]),
        str(job["taskset_path"]),
        str(job["simulation_time"]),
        "-t", str(trace_path),
    ]
    result = {
        "accepted": False,
        "simulation_status": "simulation_error",
        "simulation_error": "",
        "max_response_by_task": {},
        "simulated_response_time": "",
        "deadline_miss_time": "",
        "first_missed_task": "",
        "trace_path": "",
    }
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            result["simulation_error"] = (
                completed.stderr or completed.stdout or "simulator failed"
            ).strip()
            return result
        parser = acceptance.TraceParser(str(trace_path))
        accepted = parser.get_acceptance_ratio(job["simulation_time"]) == 1.0
        responses = parser.get_max_response_times_by_task()
        miss_time, missed_task = _first_deadline_miss(parser.events)
        result.update({
            "accepted": accepted,
            "simulation_status": "accepted" if accepted else "rejected",
            "max_response_by_task": responses,
            "simulated_response_time": max(responses.values()) if responses else "",
            "deadline_miss_time": miss_time,
            "first_missed_task": missed_task,
        })
        return result
    except subprocess.TimeoutExpired:
        result["simulation_status"] = "simulation_timeout"
        result["simulation_error"] = "simulation timed out after 120 seconds"
        return result
    finally:
        try:
            trace_path.unlink()
        except FileNotFoundError:
            pass


def _parse_report(
    version: str, payload: Optional[Mapping[str, Any]], error: str = "",
    timed_out: bool = False,
) -> Dict[str, Any]:
    if payload is None:
        return {
            "version": version,
            "status": "rta_timeout" if timed_out else "rta_error",
            "proven": False,
            "error": error,
            "reason": error,
            "bound": None,
            "report": None,
        }
    if payload.get("rta_version") != version:
        raise ValueError(
            "expected {} report, got {!r}".format(version, payload.get("rta_version"))
        )
    proven = bool(payload.get("proven_under_assumptions", False))
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    bounds = []
    reasons = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        bound = acceptance._extract_number(task.get("response_time_bound"))
        if bool(task.get("proven_under_assumptions", task.get("proven", False))):
            if bound is not None:
                bounds.append(bound)
        elif task.get("failure_reason"):
            reasons[str(task.get("task_name", "<unknown>"))] = str(
                task["failure_reason"]
            )
    return {
        "version": version,
        "status": "proven_under_assumptions" if proven else "rta_unproven",
        "proven": proven,
        "error": "",
        "reason": json.dumps(reasons, sort_keys=True) if reasons else "",
        "bound": max(bounds) if bounds else None,
        "report": dict(payload),
    }


def _run_v20(config_path: str, taskset_path: str, args, e0: float) -> Dict[str, Any]:
    raw = acceptance.run_asap_block_rta(
        acceptance.ASAP_BLOCK_ALGORITHM,
        config_path,
        taskset_path,
        args.rta_horizon_ms,
        assume_no_overflow=True,
        timeout=args.rta_v20p4_timeout,
        initial_energy=e0,
    )
    if raw.get("rta_error"):
        error = str(raw["rta_error"])
        return _parse_report(
            V20_VERSION,
            None,
            error,
            timed_out="timed out" in error.lower(),
        )
    return _parse_report(V20_VERSION, raw.get("rta_report"))


def _run_v21(config_path: str, taskset_path: str, args, e0: float) -> Dict[str, Any]:
    command = [
        sys.executable,
        str(V21_TOOL),
        "--system", config_path,
        "--tasks", taskset_path,
        "--horizon-ms", str(args.rta_horizon_ms),
        "--rta-initial-energy", str(e0),
        "--assume-no-overflow",
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=args.rta_v21_timeout,
        )
    except subprocess.TimeoutExpired:
        return _parse_report(
            V21_VERSION,
            None,
            "RTA timed out after {} seconds".format(args.rta_v21_timeout),
            timed_out=True,
        )
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "v21 RTA failed").strip()
        return _parse_report(V21_VERSION, None, error)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _parse_report(V21_VERSION, None, "invalid v21 JSON: {}".format(exc))
    return _parse_report(V21_VERSION, payload)


def _bounds_by_task(report: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    if not isinstance(report, dict):
        return {}
    result = {}
    for task in report.get("tasks", []):
        if not isinstance(task, dict):
            continue
        proven = bool(task.get("proven_under_assumptions", task.get("proven", False)))
        bound = acceptance._extract_number(task.get("response_time_bound"))
        name = task.get("task_name")
        if proven and name and bound is not None and bound > 0:
            result[str(name)] = bound
    return result


def _tightness(analysis: Mapping[str, Any], responses: Mapping[str, float]) -> Optional[float]:
    if not analysis["proven"]:
        return None
    values = [
        bound / responses[name]
        for name, bound in _bounds_by_task(analysis.get("report")).items()
        if name in responses and responses[name] > 0
    ]
    return mean(values) if values else None


def _soundness(
    analysis: Mapping[str, Any], accepted: bool,
    responses: Mapping[str, float], label: str,
    context: Mapping[str, Any],
) -> tuple:
    rejected = bool(analysis["proven"] and not accepted)
    observed = False
    if analysis["proven"]:
        for name, bound in _bounds_by_task(analysis.get("report")).items():
            if name in responses and responses[name] > bound:
                observed = True
                break
    if rejected or observed:
        context_text = ", ".join(
            "{}={!r}".format(field, context.get(field))
            for field in (
                "seed_base", "taskset_seed", "normalized_utilization",
                "task_idx", "taskset_id", "E0", "taskset_path",
                "accepted", "simulation_status",
                "simulated_response_time", "deadline_miss_time",
                "first_missed_task", "v21_status", "v21_proven",
                "v21_bound", "v21_error", "v20p4_status",
                "v20p4_proven", "v20p4_bound", "v20p4_error",
            )
        )
        raise RuntimeError(
            "SEVERE {} soundness violation: proven_but_rejected={}, "
            "observed_exceeds_bound={}; {}".format(
                label, rejected, observed, context_text
            )
        )
    return rejected, observed


def _comparison_row(
    base: Mapping[str, Any], simulation: Mapping[str, Any],
    v20_result: Mapping[str, Any], v21_result: Mapping[str, Any], e0: float,
) -> Dict[str, Any]:
    responses = simulation["max_response_by_task"]
    soundness_context = {
        "seed_base": base.get("seed_base"),
        "taskset_seed": base.get("taskset_seed"),
        "normalized_utilization": base.get("normalized_utilization"),
        "task_idx": base.get("task_idx"),
        "taskset_id": base.get("taskset_id"),
        "E0": e0,
        "taskset_path": base.get("taskset_path"),
        "accepted": simulation.get("accepted"),
        "simulation_status": simulation.get("simulation_status"),
        "simulated_response_time": simulation.get(
            "simulated_response_time"
        ),
        "deadline_miss_time": simulation.get("deadline_miss_time"),
        "first_missed_task": simulation.get("first_missed_task"),
        "v21_status": v21_result.get("status"),
        "v21_proven": v21_result.get("proven"),
        "v21_bound": v21_result.get("bound"),
        "v21_error": v21_result.get("error"),
        "v20p4_status": v20_result.get("status"),
        "v20p4_proven": v20_result.get("proven"),
        "v20p4_bound": v20_result.get("bound"),
        "v20p4_error": v20_result.get("error"),
    }
    v20_bad_rejected, v20_bad_bound = _soundness(
        v20_result,
        simulation["accepted"],
        responses,
        V20_VERSION,
        soundness_context,
    )
    v21_bad_rejected, v21_bad_bound = _soundness(
        v21_result,
        simulation["accepted"],
        responses,
        V21_VERSION,
        soundness_context,
    )
    both_proven = bool(v20_result["proven"] and v21_result["proven"])
    delta = (
        v21_result["bound"] - v20_result["bound"]
        if both_proven
        and v21_result["bound"] is not None
        and v20_result["bound"] is not None
        else None
    )
    row = {
        key: base[key]
        for key in (
            "experiment_name", "seed_base", "taskset_seed",
            "normalized_utilization", "task_idx", "taskset_id",
            "taskset_path",
        )
    }
    row.update({
        "E0": e0,
        "accepted": int(simulation["accepted"]),
        "simulation_status": simulation["simulation_status"],
        "simulated_response_time": simulation["simulated_response_time"],
        "deadline_miss_time": simulation["deadline_miss_time"],
        "first_missed_task": simulation["first_missed_task"],
        "trace_path": simulation["trace_path"],
        "v20p4_rta_version": V20_VERSION,
        "v20p4_status": v20_result["status"],
        "v20p4_proven": int(v20_result["proven"]),
        "v20p4_error": v20_result["error"],
        "v20p4_reason": v20_result["reason"],
        "v20p4_bound": "" if v20_result["bound"] is None else v20_result["bound"],
        "v20p4_tightness": _tightness(v20_result, responses) or "",
        "v21_rta_version": V21_VERSION,
        "v21_status": v21_result["status"],
        "v21_proven": int(v21_result["proven"]),
        "v21_error": v21_result["error"],
        "v21_reason": v21_result["reason"],
        "v21_bound": "" if v21_result["bound"] is None else v21_result["bound"],
        "v21_tightness": _tightness(v21_result, responses) or "",
        "v21_minus_v20p4_bound": "" if delta is None else delta,
        "v21_bound_lt_v20p4": int(delta is not None and delta < 0),
        "v21_bound_eq_v20p4": int(delta is not None and delta == 0),
        "v21_bound_gt_v20p4": int(delta is not None and delta > 0),
        "v21_proven_v20p4_unproven": int(v21_result["proven"] and not v20_result["proven"]),
        "v20p4_proven_v21_unproven": int(v20_result["proven"] and not v21_result["proven"]),
        "both_proven": int(both_proven),
        "both_unproven": int(not v20_result["proven"] and not v21_result["proven"]),
        "v21_soundness_proven_but_rejected": int(v21_bad_rejected),
        "v21_soundness_observed_exceeds_bound": int(v21_bad_bound),
        "v20p4_soundness_proven_but_rejected": int(v20_bad_rejected),
        "v20p4_soundness_observed_exceeds_bound": int(v20_bad_bound),
    })
    return row


def _run_taskset(job: Mapping[str, Any], args) -> List[Dict[str, Any]]:
    simulation = _run_simulation(job)
    if simulation["simulation_status"] in {"simulation_error", "simulation_timeout"}:
        raise RuntimeError(
            "simulation failed for {}: {}".format(
                job["taskset_id"], simulation["simulation_error"]
            )
        )
    rows = []
    for e0 in args.e0_values:
        v20_result = _run_v20(job["config_path"], job["taskset_path"], args, e0)
        v21_result = _run_v21(job["config_path"], job["taskset_path"], args, e0)
        rows.append(_comparison_row(job, simulation, v20_result, v21_result, e0))
    return rows


def _manifest_row(args, run_dir: Path, status: str, return_code: int = 0) -> Dict[str, Any]:
    return {
        "experiment_name": args.experiment_name,
        "run_dir": str(run_dir),
        "results_file": str(run_dir / RESULT_FILENAME),
        "seed_base": args.seed_base,
        "utilizations": " ".join(map(str, args.utilizations)),
        "num_tasksets": args.num_tasksets,
        "task_n": args.task_n,
        "battery": args.battery,
        "initial_energy": args.initial_energy,
        "solar_time_ms": args.solar_time_ms,
        "E0_values": " ".join(map(str, args.e0_values)),
        "rta_v20p4_timeout": args.rta_v20p4_timeout,
        "rta_v21_timeout": args.rta_v21_timeout,
        "status": status,
        "return_code": return_code,
    }


def run(args) -> Path:
    output_root = Path(args.output_root).resolve()
    run_dir = output_root / args.experiment_name
    results_path = run_dir / RESULT_FILENAME
    manifest_path = run_dir / MANIFEST_FILENAME
    if results_path.is_file() and args.skip_existing:
        return results_path
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(
            "refusing to overwrite non-empty v21 comparison directory: {}".format(
                run_dir
            )
        )
    run_dir.mkdir(parents=True)
    _write_csv(manifest_path, MANIFEST_FIELDS, [_manifest_row(args, run_dir, "dry_run" if args.dry_run else "running")])
    if args.dry_run:
        _write_csv(results_path, RESULT_FIELDS, [])
        return results_path

    runner = acceptance.ExperimentRunner(
        output_dir=run_dir,
        utilization_points=args.utilizations,
        num_tasksets=args.num_tasksets,
        task_n=args.task_n,
        task_p_min=args.task_p_min,
        task_p_max=args.task_p_max,
        simulation_time=args.simulation_time,
        battery_capacity=args.battery,
        initial_energy_ratio=args.initial_energy,
        solar_start_time_ms=args.solar_time_ms,
        use_real_solar_data=False,
        max_workers=args.max_workers,
        seed_base=args.seed_base,
    )
    config_path = runner.modify_config(acceptance.ASAP_BLOCK_ALGORITHM)
    jobs = []
    try:
        for utilization in args.utilizations:
            for task_idx in range(args.num_tasksets):
                seed = runner.taskset_seed(utilization, task_idx)
                taskset_path = runner.generate_taskset(
                    utilization,
                    task_idx,
                    seed,
                    system_config_file=config_path,
                )
                if not taskset_path:
                    raise RuntimeError(
                        "taskset generation failed for U={} idx={}".format(
                            utilization, task_idx
                        )
                    )
                jobs.append({
                    "experiment_name": args.experiment_name,
                    "seed_base": args.seed_base,
                    "taskset_seed": seed,
                    "normalized_utilization": utilization,
                    "task_idx": task_idx,
                    "taskset_id": runner.taskset_id(utilization, task_idx),
                    "taskset_path": str(Path(taskset_path).resolve()),
                    "config_path": str(Path(config_path).resolve()),
                    "simulation_time": args.simulation_time,
                    "trace_path": str(
                        (runner.trace_dir / "comparison_u{:.2f}_{:03d}.json".format(
                            utilization, task_idx
                        )).resolve()
                    ),
                })

        rows = []
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = [executor.submit(_run_taskset, job, args) for job in jobs]
            for future in as_completed(futures):
                rows.extend(future.result())
                rows.sort(key=lambda row: (
                    float(row["E0"]),
                    float(row["normalized_utilization"]),
                    int(row["task_idx"]),
                ))
                _write_csv(results_path, RESULT_FIELDS, rows)
    except Exception:
        _write_csv(manifest_path, MANIFEST_FIELDS, [_manifest_row(args, run_dir, "failed", 1)])
        raise
    finally:
        try:
            Path(config_path).unlink()
        except FileNotFoundError:
            pass

    _write_csv(manifest_path, MANIFEST_FIELDS, [_manifest_row(args, run_dir, "completed")])
    return results_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    path = run(args)
    print("Comparison results: {}".format(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
