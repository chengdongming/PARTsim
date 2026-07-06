#!/usr/bin/env python3
"""Run isolated ASAP-BLOCK v20.4 versus v21-local-window comparisons."""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance


V20_VERSION = "v20.4"
V21_VERSION = "v21-local-window"
V21_TOOL = PROJECT_ROOT / "asap_block_rta_v21_local_window.py"
RESULT_FILENAME = "rta_v21_comparison_results.csv"
MANIFEST_FILENAME = "rta_v21_comparison_manifest.csv"
E0_UNCONDITIONAL_SCOPE = "unconditional_zero_lower_bound"
E0_CONDITIONAL_SCOPE = "conditional_release_time_lower_bound"
E0_UNVERIFIED_REASON = "e0_release_energy_assumption_not_verified"

V20_THEORY_METADATA = {
    "v20_theory_family": "complete_window",
    "v20_closure_method": "fixed_point_complete_window",
    "v20_uses_local_window": False,
    "v20_uses_delta_closure": False,
    "v20_empty_state_guard": True,
}

V21_THEORY_DEFAULTS = {
    "v21_theory_family": "local_window_closure",
    "v21_closure_method": "delta_closure",
    "v21_empty_set_guard": True,
    "v21_fallback_guard": True,
    "v21_consistency_guard": True,
    "v21_certified_carry_in_source": "v21_recursive_certification",
    "v21_uses_local_window": True,
    "v21_uses_delta_closure": True,
    "v21_uses_parallel_u_compression": False,
}

V21_PROFILE_SUM_COUNTERS = (
    "delta_iterations",
    "g_loc_calls",
    "omega_feasibility_calls",
    "empty_omega_count",
    "no_closure_count",
    "closed_prefix_count",
    "delta_cap_exceeded_count",
    "delta_jump_count",
)
V21_PROFILE_MAX_COUNTERS = ("max_delta_cap", "max_delta_seen")

RESULT_FIELDS = [
    "experiment_name", "seed_base", "taskset_seed",
    "normalized_utilization", "target_normalized_utilization",
    "target_total_utilization", "actual_total_utilization",
    "actual_normalized_utilization", "utilization_error_total",
    "task_util_min", "task_util_max", "wcet_rounding", "deadline_mode",
    "actual_utilization_tolerance_total",
    "M", "task_idx", "taskset_id", "E0",
    "e0_assumption_scope", "release_energy_assumption_verified",
    "accepted", "simulation_status", "simulated_response_time",
    "deadline_miss_time", "first_missed_task", "taskset_path",
    "trace_path", "v20p4_rta_version", "v20_rta_version",
    "v20_theory_family", "v20_closure_method",
    "v20_uses_local_window", "v20_uses_delta_closure",
    "v20_empty_state_guard", "v20p4_status",
    "v20p4_proven", "v20p4_error", "v20p4_reason", "v20p4_bound",
    "v20p4_tightness", "pessimism_v20", "v21_rta_version", "v21_status",
    "v21_proven", "v21_error", "v21_reason", "v21_bound",
    "v21_theory_family", "v21_closure_method", "v21_empty_set_guard",
    "v21_fallback_guard", "v21_consistency_guard",
    "v21_certified_carry_in_source", "v21_uses_local_window",
    "v21_uses_delta_closure", "v21_uses_parallel_u_compression",
    "v21_delta_iterations", "v21_g_loc_calls",
    "v21_omega_feasibility_calls", "v21_empty_omega_count",
    "v21_no_closure_count", "v21_closed_prefix_count",
    "v21_delta_cap_exceeded_count", "v21_max_delta_cap",
    "v21_max_delta_seen", "v21_delta_jump_count",
    "v21_no_closure_observed", "v21_timeout_or_horizon_failure",
    "v21_fallback_used", "v21_fallback_reason", "v21_failure_reason",
    "v21_certificate_status",
    "v21_tightness", "pessimism_v21",
    "intersection_pessimism_v20", "intersection_pessimism_v21",
    "intersection_pessimism_improvement", "v21_minus_v20p4_bound",
    "runtime_v20_sec", "runtime_v21_sec",
    "runtime_slowdown_v21_over_v20",
    "v21_bound_lt_v20p4", "v21_bound_eq_v20p4",
    "v21_bound_gt_v20p4", "v21_bound_gt_v20",
    "v21_bound_gt_v20_reason", "v21_proven_v20p4_unproven",
    "v20p4_proven_v21_unproven", "both_proven", "both_unproven",
    "v20_only_proven", "v21_only_proven", "both_rejected",
    "v21_soundness_proven_but_rejected",
    "v21_soundness_observed_exceeds_bound",
    "v20p4_soundness_proven_but_rejected",
    "v20p4_soundness_observed_exceeds_bound",
    "v20_sim_rejected_violation", "v20_observed_bound_violation",
    "v21_sim_rejected_violation", "v21_observed_bound_violation",
    "v20_conditional_proven_but_sim_rejected",
    "v21_conditional_proven_but_sim_rejected",
    "v20_conditional_observed_exceeds_bound",
    "v21_conditional_observed_exceeds_bound",
    "v20_soundness_violation",
    "v21_soundness_violation",
    "soundness_valid",
    "soundness_excluded_reason",
]

MANIFEST_FIELDS = [
    "experiment_name", "run_dir", "results_file", "seed_base",
    "utilizations", "target_normalized_utilization",
    "target_total_utilization", "M", "num_tasksets", "task_n", "battery",
    "initial_energy", "solar_time_ms", "E0_values",
    "task_util_min", "task_util_max", "wcet_rounding", "deadline_mode",
    "actual_utilization_tolerance_total",
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
    parser.add_argument("--M", type=int, default=4)
    parser.add_argument("--task-p-min", type=int, default=40)
    parser.add_argument("--task-p-max", type=int, default=400)
    parser.add_argument("--simulation-time", type=int, default=30000)
    parser.add_argument("--battery", type=float, default=20.0)
    parser.add_argument(
        "--initial-energy",
        type=float,
        default=1.0,
        help=(
            "simulation initial battery-energy ratio at t=0 in [0, 1]; "
            "this does not certify energy available at later job releases"
        ),
    )
    parser.add_argument("--solar-time-ms", type=int, default=21975000)
    parser.add_argument(
        "--e0-values",
        nargs="+",
        type=float,
        required=True,
        help=(
            "RTA analysis-window/job-release energy lower bound E0 in joules. "
            "E0 is not the simulation initial battery energy at t=0. E0>0 "
            "produces conditional RTA results unless release-time energy is "
            "certified"
        ),
    )
    parser.add_argument("--seed-base", type=int, default=424242)
    parser.add_argument("--rta-horizon-ms", type=int, default=30000)
    parser.add_argument("--rta-v20p4-timeout", type=int, default=60)
    parser.add_argument("--rta-v21-timeout", type=int, default=60)
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--min-task-util", type=float, default=0.01)
    parser.add_argument("--max-task-util", type=float, default=0.8)
    parser.add_argument(
        "--wcet-rounding",
        choices=("floor", "round", "ceil", "compensated"),
        default="floor",
    )
    parser.add_argument(
        "--actual-utilization-tolerance-total",
        type=float,
        default=None,
        help=(
            "absolute total-utilization error tolerance after integer WCET "
            "rounding; when set, tasksets outside the tolerance are discarded "
            "and regenerated"
        ),
    )
    parser.add_argument(
        "--constrained-deadlines",
        action="store_true",
        help="generate constrained deadlines C_i<=D_i<=T_i; default is implicit D_i=T_i",
    )
    parser.add_argument(
        "--soundness-mode",
        choices=("fail_fast", "audit"),
        default="fail_fast",
        help=(
            "soundness violation handling: fail_fast raises immediately; "
            "audit records violation fields in the CSV"
        ),
    )
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    for name in (
        "num_tasksets", "task_n", "M", "task_p_min", "task_p_max",
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
    if args.min_task_util < 0 or args.max_task_util <= 0:
        parser.error("--min-task-util/--max-task-util must be positive bounds")
    if args.min_task_util > args.max_task_util:
        parser.error("--min-task-util must be <= --max-task-util")
    if args.max_task_util > 1.0:
        parser.error("--max-task-util must be <= 1.0 for sequential tasks")
    if args.actual_utilization_tolerance_total is not None and (
        not math.isfinite(args.actual_utilization_tolerance_total)
        or args.actual_utilization_tolerance_total < 0
    ):
        parser.error(
            "--actual-utilization-tolerance-total must be finite and non-negative"
        )
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


def _finite_number(value: Any) -> Optional[float]:
    number = acceptance._extract_number(value)
    if number is None or not math.isfinite(number):
        return None
    return float(number)


def _metadata_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    return default


def _v21_metadata(analysis: Mapping[str, Any]) -> Dict[str, Any]:
    report = analysis.get("report")
    if not isinstance(report, Mapping):
        report = {}
    metadata = {}
    for field, default in V21_THEORY_DEFAULTS.items():
        source = field[4:] if field.startswith("v21_") else field
        value = report.get(source, default)
        metadata[field] = (
            _metadata_bool(value, default)
            if isinstance(default, bool)
            else str(value or default)
        )
    return metadata


def _aggregate_v21_profile(analysis: Mapping[str, Any]) -> Dict[str, Any]:
    counters: Dict[str, Any] = {}
    for name in V21_PROFILE_SUM_COUNTERS:
        counters["v21_{}".format(name)] = 0
    for name in V21_PROFILE_MAX_COUNTERS:
        counters["v21_{}".format(name)] = 0

    report = analysis.get("report")
    if not isinstance(report, Mapping):
        return counters
    tasks = report.get("tasks")
    if not isinstance(tasks, list):
        return counters
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        profile = task.get("rta_profile")
        if not isinstance(profile, Mapping):
            continue
        for name in V21_PROFILE_SUM_COUNTERS:
            value = _finite_number(profile.get(name))
            if value is not None and value >= 0:
                counters["v21_{}".format(name)] += int(value)
        for name in V21_PROFILE_MAX_COUNTERS:
            value = _finite_number(profile.get(name))
            if value is not None and value >= 0:
                counters["v21_{}".format(name)] = max(
                    counters["v21_{}".format(name)],
                    int(value),
                )
    return counters


def _v21_audit_fields(
    analysis: Mapping[str, Any], counters: Mapping[str, Any]
) -> Dict[str, Any]:
    reason = str(
        analysis.get("reason")
        or analysis.get("error")
        or ""
    )
    normalized = reason.lower()
    no_closure_terms = (
        "no closure",
        "not closed",
        "service insufficient",
        "empty omega",
        "empty local omega",
        "a_k^theta",
        "exceeds candidate",
    )
    no_closure = (
        not bool(analysis.get("proven"))
        and (
            any(term in normalized for term in no_closure_terms)
            or int(counters.get("v21_no_closure_count", 0)) > 0
        )
    )
    timeout_or_horizon = (
        analysis.get("status") == "rta_timeout"
        or "timeout" in normalized
        or "timed out" in normalized
        or "horizon" in normalized
    )
    if analysis.get("proven"):
        certificate_status = "certified"
    elif "certif" in normalized or "higher-priority" in normalized:
        certificate_status = "missing_or_invalid_certificate"
    else:
        certificate_status = "unproven_or_not_available"
    return {
        "v21_no_closure_observed": int(no_closure),
        "v21_timeout_or_horizon_failure": int(timeout_or_horizon),
        "v21_fallback_used": 0,
        "v21_fallback_reason": "",
        "v21_failure_reason": reason,
        "v21_certificate_status": certificate_status,
    }


def _run_v20(config_path: str, taskset_path: str, args, e0: float) -> Dict[str, Any]:
    started = time.perf_counter()
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
        result = _parse_report(
            V20_VERSION,
            None,
            error,
            timed_out="timed out" in error.lower(),
        )
        result["runtime_sec"] = time.perf_counter() - started
        return result
    result = _parse_report(V20_VERSION, raw.get("rta_report"))
    result["runtime_sec"] = time.perf_counter() - started
    return result


def _run_v21(config_path: str, taskset_path: str, args, e0: float) -> Dict[str, Any]:
    started = time.perf_counter()
    command = [
        sys.executable,
        str(V21_TOOL),
        "--system", config_path,
        "--tasks", taskset_path,
        "--horizon-ms", str(args.rta_horizon_ms),
        "--rta-initial-energy", str(e0),
        "--assume-no-overflow",
        "--profile-rta",
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
        result = _parse_report(
            V21_VERSION,
            None,
            "RTA timed out after {} seconds".format(args.rta_v21_timeout),
            timed_out=True,
        )
        result["runtime_sec"] = time.perf_counter() - started
        return result
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "v21 RTA failed").strip()
        result = _parse_report(V21_VERSION, None, error)
        result["runtime_sec"] = time.perf_counter() - started
        return result
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        result = _parse_report(V21_VERSION, None, "invalid v21 JSON: {}".format(exc))
        result["runtime_sec"] = time.perf_counter() - started
        return result
    result = _parse_report(V21_VERSION, payload)
    result["runtime_sec"] = time.perf_counter() - started
    return result


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


def _pessimism(
    analysis: Mapping[str, Any], simulation: Mapping[str, Any]
) -> Optional[float]:
    if not analysis["proven"] or not simulation.get("accepted"):
        return None
    bound = acceptance._extract_number(analysis.get("bound"))
    observed = acceptance._extract_number(
        simulation.get("simulated_response_time")
    )
    if (
        bound is None
        or observed is None
        or not math.isfinite(bound)
        or not math.isfinite(observed)
        or observed <= 0
    ):
        return None
    return bound / observed


def _release_energy_assumption(
    e0: float, verified: Optional[bool] = None
) -> tuple:
    """Return the E0 scope and whether it is valid for soundness comparison."""
    if e0 <= 0:
        return E0_UNCONDITIONAL_SCOPE, True
    return E0_CONDITIONAL_SCOPE, bool(verified)


def _soundness(
    analysis: Mapping[str, Any], accepted: bool,
    responses: Mapping[str, float], label: str,
    context: Mapping[str, Any], mode: str = "fail_fast",
    release_energy_assumption_verified: bool = True,
) -> tuple:
    simulation_classification = acceptance.classify_soundness_observation(
        analysis["proven"],
        accepted,
        context.get("simulation_status"),
    )
    diagnostic_rejected = bool(
        simulation_classification["soundness_violation"]
    )
    diagnostic_observed = False
    if analysis["proven"] and simulation_classification["soundness_valid"]:
        for name, bound in _bounds_by_task(analysis.get("report")).items():
            if name in responses and responses[name] > bound:
                diagnostic_observed = True
                break
    if release_energy_assumption_verified:
        classification = dict(simulation_classification)
        rejected = diagnostic_rejected
        observed = diagnostic_observed
    else:
        classification = {
            "soundness_valid": False,
            "soundness_excluded_reason": E0_UNVERIFIED_REASON,
            "soundness_violation": False,
        }
        rejected = False
        observed = False
    if (rejected or observed) and mode == "fail_fast":
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
    return (
        rejected,
        observed,
        classification,
        diagnostic_rejected,
        diagnostic_observed,
    )


def _comparison_row(
    base: Mapping[str, Any], simulation: Mapping[str, Any],
    v20_result: Mapping[str, Any], v21_result: Mapping[str, Any], e0: float,
    soundness_mode: str = "fail_fast",
    release_energy_assumption_verified: Optional[bool] = None,
) -> Dict[str, Any]:
    responses = simulation["max_response_by_task"]
    e0_scope, release_energy_verified = _release_energy_assumption(
        e0, release_energy_assumption_verified
    )
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
    (
        v20_bad_rejected,
        v20_bad_bound,
        v20_soundness,
        v20_conditional_rejected,
        v20_conditional_bound,
    ) = _soundness(
        v20_result,
        simulation["accepted"],
        responses,
        V20_VERSION,
        soundness_context,
        soundness_mode,
        release_energy_verified,
    )
    (
        v21_bad_rejected,
        v21_bad_bound,
        v21_soundness,
        v21_conditional_rejected,
        v21_conditional_bound,
    ) = _soundness(
        v21_result,
        simulation["accepted"],
        responses,
        V21_VERSION,
        soundness_context,
        soundness_mode,
        release_energy_verified,
    )
    conditional_unverified = (
        e0_scope == E0_CONDITIONAL_SCOPE and not release_energy_verified
    )
    both_proven = bool(v20_result["proven"] and v21_result["proven"])
    v20_only_proven = bool(v20_result["proven"] and not v21_result["proven"])
    v21_only_proven = bool(v21_result["proven"] and not v20_result["proven"])
    both_rejected = bool(
        v20_result["status"] == "rta_unproven"
        and v21_result["status"] == "rta_unproven"
    )
    pessimism_v20 = _pessimism(v20_result, simulation)
    pessimism_v21 = _pessimism(v21_result, simulation)
    intersection_pessimism_v20 = None
    intersection_pessimism_v21 = None
    intersection_pessimism_improvement = None
    if (
        both_proven
        and pessimism_v20 is not None
        and pessimism_v21 is not None
    ):
        intersection_pessimism_v20 = pessimism_v20
        intersection_pessimism_v21 = pessimism_v21
        intersection_pessimism_improvement = (
            pessimism_v20 - pessimism_v21
        )
    runtime_v20 = v20_result.get("runtime_sec")
    runtime_v21 = v21_result.get("runtime_sec")
    slowdown = ""
    if runtime_v20 is not None and runtime_v21 is not None:
        if runtime_v20 > 0:
            slowdown = runtime_v21 / runtime_v20
        else:
            slowdown = math.inf
    delta = (
        v21_result["bound"] - v20_result["bound"]
        if both_proven
        and v21_result["bound"] is not None
        and v20_result["bound"] is not None
        else None
    )
    v21_bound_gt_v20 = bool(delta is not None and delta > 0)
    if v21_bound_gt_v20:
        v21_bound_gt_v20_reason = "both_proven_v21_bound_larger"
    elif v20_result["proven"] and not v21_result["proven"]:
        v21_bound_gt_v20_reason = "v21_unproven_v20_proven"
    elif v21_result["proven"] and not v20_result["proven"]:
        v21_bound_gt_v20_reason = "v21_proven_v20_unproven"
    else:
        v21_bound_gt_v20_reason = ""
    v21_profile_counters = _aggregate_v21_profile(v21_result)
    v21_audit = _v21_audit_fields(v21_result, v21_profile_counters)
    normalized_utilization = float(base.get("normalized_utilization", 0.0))
    processors = int(base.get("M", 4))
    row = {
        "experiment_name": base.get("experiment_name", ""),
        "seed_base": base.get("seed_base", ""),
        "taskset_seed": base.get("taskset_seed", ""),
        "normalized_utilization": normalized_utilization,
        "target_normalized_utilization": base.get(
            "target_normalized_utilization",
            normalized_utilization,
        ),
        "target_total_utilization": base.get(
            "target_total_utilization",
            normalized_utilization * processors,
        ),
        "actual_total_utilization": base.get("actual_total_utilization", ""),
        "actual_normalized_utilization": base.get(
            "actual_normalized_utilization", ""
        ),
        "utilization_error_total": base.get("utilization_error_total", ""),
        "task_util_min": base.get("task_util_min", 0.01),
        "task_util_max": base.get("task_util_max", 0.8),
        "wcet_rounding": base.get("wcet_rounding", "floor"),
        "deadline_mode": base.get("deadline_mode", "implicit"),
        "actual_utilization_tolerance_total": base.get(
            "actual_utilization_tolerance_total", ""
        ),
        "M": processors,
        "task_idx": base.get("task_idx", ""),
        "taskset_id": base.get("taskset_id", ""),
        "taskset_path": base.get("taskset_path", ""),
    }
    row.update({
        "E0": e0,
        "e0_assumption_scope": e0_scope,
        "release_energy_assumption_verified": int(release_energy_verified),
        "accepted": int(simulation["accepted"]),
        "simulation_status": simulation["simulation_status"],
        "simulated_response_time": simulation["simulated_response_time"],
        "deadline_miss_time": simulation["deadline_miss_time"],
        "first_missed_task": simulation["first_missed_task"],
        "trace_path": simulation["trace_path"],
        "v20p4_rta_version": v20_result.get("version", V20_VERSION),
        "v20_rta_version": v20_result.get("version", V20_VERSION),
        **{
            field: int(value) if isinstance(value, bool) else value
            for field, value in V20_THEORY_METADATA.items()
        },
        "v20p4_status": v20_result["status"],
        "v20p4_proven": int(v20_result["proven"]),
        "v20p4_error": v20_result["error"],
        "v20p4_reason": v20_result["reason"],
        "v20p4_bound": "" if v20_result["bound"] is None else v20_result["bound"],
        # Legacy tightness fields are pessimism-ratio aliases retained for
        # backward compatibility. Their direction is bound / observed.
        "v20p4_tightness": (
            "" if pessimism_v20 is None else pessimism_v20
        ),
        "pessimism_v20": "" if pessimism_v20 is None else pessimism_v20,
        "v21_rta_version": v21_result.get("version", V21_VERSION),
        "v21_status": v21_result["status"],
        "v21_proven": int(v21_result["proven"]),
        "v21_error": v21_result["error"],
        "v21_reason": v21_result["reason"],
        "v21_bound": "" if v21_result["bound"] is None else v21_result["bound"],
        **{
            field: int(value) if isinstance(value, bool) else value
            for field, value in _v21_metadata(v21_result).items()
        },
        **v21_profile_counters,
        **v21_audit,
        "v21_tightness": "" if pessimism_v21 is None else pessimism_v21,
        "pessimism_v21": "" if pessimism_v21 is None else pessimism_v21,
        "intersection_pessimism_v20": (
            ""
            if intersection_pessimism_v20 is None
            else intersection_pessimism_v20
        ),
        "intersection_pessimism_v21": (
            ""
            if intersection_pessimism_v21 is None
            else intersection_pessimism_v21
        ),
        "intersection_pessimism_improvement": (
            ""
            if intersection_pessimism_improvement is None
            else intersection_pessimism_improvement
        ),
        "v21_minus_v20p4_bound": "" if delta is None else delta,
        "runtime_v20_sec": "" if runtime_v20 is None else runtime_v20,
        "runtime_v21_sec": "" if runtime_v21 is None else runtime_v21,
        "runtime_slowdown_v21_over_v20": slowdown,
        "v21_bound_lt_v20p4": int(delta is not None and delta < 0),
        "v21_bound_eq_v20p4": int(delta is not None and delta == 0),
        "v21_bound_gt_v20p4": int(delta is not None and delta > 0),
        "v21_bound_gt_v20": int(v21_bound_gt_v20),
        "v21_bound_gt_v20_reason": v21_bound_gt_v20_reason,
        "v21_proven_v20p4_unproven": int(v21_result["proven"] and not v20_result["proven"]),
        "v20p4_proven_v21_unproven": int(v20_result["proven"] and not v21_result["proven"]),
        "both_proven": int(both_proven),
        "both_unproven": int(not v20_result["proven"] and not v21_result["proven"]),
        "v20_only_proven": int(v20_only_proven),
        "v21_only_proven": int(v21_only_proven),
        "both_rejected": int(both_rejected),
        "v21_soundness_proven_but_rejected": int(v21_bad_rejected),
        "v21_soundness_observed_exceeds_bound": int(v21_bad_bound),
        "v20p4_soundness_proven_but_rejected": int(v20_bad_rejected),
        "v20p4_soundness_observed_exceeds_bound": int(v20_bad_bound),
        "v20_sim_rejected_violation": int(v20_bad_rejected),
        "v20_observed_bound_violation": int(v20_bad_bound),
        "v21_sim_rejected_violation": int(v21_bad_rejected),
        "v21_observed_bound_violation": int(v21_bad_bound),
        "v20_conditional_proven_but_sim_rejected": int(
            conditional_unverified and v20_conditional_rejected
        ),
        "v21_conditional_proven_but_sim_rejected": int(
            conditional_unverified and v21_conditional_rejected
        ),
        "v20_conditional_observed_exceeds_bound": int(
            conditional_unverified and v20_conditional_bound
        ),
        "v21_conditional_observed_exceeds_bound": int(
            conditional_unverified and v21_conditional_bound
        ),
        "v20_soundness_violation": int(v20_bad_rejected or v20_bad_bound),
        "v21_soundness_violation": int(v21_bad_rejected or v21_bad_bound),
        "soundness_valid": int(v20_soundness["soundness_valid"]),
        "soundness_excluded_reason": (
            v20_soundness["soundness_excluded_reason"]
            or v21_soundness["soundness_excluded_reason"]
        ),
    })
    return row


def _run_taskset(job: Mapping[str, Any], args) -> List[Dict[str, Any]]:
    simulation = _run_simulation(job)
    if (
        simulation["simulation_status"] in {
            "simulation_error",
            "simulation_timeout",
        }
        and args.soundness_mode != "audit"
    ):
        raise RuntimeError(
            "simulation failed for {}: {}".format(
                job["taskset_id"], simulation["simulation_error"]
            )
        )
    rows = []
    for e0 in args.e0_values:
        v20_result = _run_v20(job["config_path"], job["taskset_path"], args, e0)
        v21_result = _run_v21(job["config_path"], job["taskset_path"], args, e0)
        rows.append(
            _comparison_row(
                job,
                simulation,
                v20_result,
                v21_result,
                e0,
                soundness_mode=args.soundness_mode,
            )
        )
    return rows


def _manifest_row(args, run_dir: Path, status: str, return_code: int = 0) -> Dict[str, Any]:
    target_totals = [
        float(format(float(value) * args.M, ".15g"))
        for value in args.utilizations
    ]
    return {
        "experiment_name": args.experiment_name,
        "run_dir": str(run_dir),
        "results_file": str(run_dir / RESULT_FILENAME),
        "seed_base": args.seed_base,
        "utilizations": " ".join(map(str, args.utilizations)),
        "target_normalized_utilization": " ".join(map(str, args.utilizations)),
        "target_total_utilization": " ".join(map(str, target_totals)),
        "M": args.M,
        "num_tasksets": args.num_tasksets,
        "task_n": args.task_n,
        "battery": args.battery,
        "initial_energy": args.initial_energy,
        "solar_time_ms": args.solar_time_ms,
        "E0_values": " ".join(map(str, args.e0_values)),
        "task_util_min": args.min_task_util,
        "task_util_max": args.max_task_util,
        "wcet_rounding": args.wcet_rounding,
        "deadline_mode": (
            "constrained" if args.constrained_deadlines else "implicit"
        ),
        "actual_utilization_tolerance_total": (
            ""
            if args.actual_utilization_tolerance_total is None
            else args.actual_utilization_tolerance_total
        ),
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
        system_cores=args.M,
        max_workers=args.max_workers,
        seed_base=args.seed_base,
        task_util_min=args.min_task_util,
        task_util_max=args.max_task_util,
        wcet_rounding=args.wcet_rounding,
        constrained_deadlines=args.constrained_deadlines,
        actual_utilization_tolerance_total=(
            args.actual_utilization_tolerance_total
        ),
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
                target_total_utilization = float(
                    format(float(utilization) * args.M, ".15g")
                )
                taskset_metadata = acceptance.load_taskset_utilization_metadata(
                    taskset_path,
                    target_normalized_utilization=float(utilization),
                    target_total_utilization=target_total_utilization,
                    num_cores=args.M,
                    task_util_min=args.min_task_util,
                    task_util_max=args.max_task_util,
                    wcet_rounding=args.wcet_rounding,
                    deadline_mode=(
                        "constrained"
                        if args.constrained_deadlines else "implicit"
                    ),
                    actual_utilization_tolerance_total=(
                        ""
                        if args.actual_utilization_tolerance_total is None
                        else args.actual_utilization_tolerance_total
                    ),
                )
                jobs.append({
                    "experiment_name": args.experiment_name,
                    "seed_base": args.seed_base,
                    "taskset_seed": seed,
                    "normalized_utilization": utilization,
                    "target_normalized_utilization": taskset_metadata[
                        "target_normalized_utilization"
                    ],
                    "target_total_utilization": taskset_metadata[
                        "target_total_utilization"
                    ],
                    "actual_total_utilization": taskset_metadata[
                        "actual_total_utilization"
                    ],
                    "actual_normalized_utilization": taskset_metadata[
                        "actual_normalized_utilization"
                    ],
                    "utilization_error_total": taskset_metadata[
                        "utilization_error_total"
                    ],
                    "task_util_min": taskset_metadata["task_util_min"],
                    "task_util_max": taskset_metadata["task_util_max"],
                    "wcet_rounding": taskset_metadata["wcet_rounding"],
                    "deadline_mode": taskset_metadata["deadline_mode"],
                    "actual_utilization_tolerance_total": taskset_metadata[
                        "actual_utilization_tolerance_total"
                    ],
                    "M": args.M,
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
        violation_count = sum(
            int(row["v21_soundness_proven_but_rejected"])
            + int(row["v21_soundness_observed_exceeds_bound"])
            + int(row["v20p4_soundness_proven_but_rejected"])
            + int(row["v20p4_soundness_observed_exceeds_bound"])
            for row in rows
        )
        print(
            "RTA comparison soundness violations recorded: {}".format(
                violation_count
            )
        )
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
