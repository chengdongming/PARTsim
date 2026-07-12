#!/usr/bin/env python3
"""Generate v20.4 ASAP-BLOCK RTA-only scalability measurements."""

import argparse
import hashlib
import math
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from scripts import experiment_runner


EXPECTED_RTA_VERSION = "v20.4"
EXPECTED_RTA_TOOL = "asap_block_rta.py"
RTA_VERSION = acceptance.RTA_VERSION
RTA_TOOL = Path(acceptance.RTA_TOOL).name
MANIFEST_FILENAME = "rta_scalability_manifest.csv"
RESULTS_FILENAME = "rta_scalability_results.csv"
DEFAULT_SYSTEM_TEMPLATE = PROJECT_ROOT / acceptance.CONFIG_TEMPLATE

MANIFEST_FIELDS = [
    "config_id",
    "taskset_id",
    "seed",
    "task_n",
    "M",
    "utilization",
    "utilization_mode",
    "target_normalized_utilization",
    "target_total_utilization",
    "actual_total_utilization",
    "actual_normalized_utilization",
    "utilization_error_total",
    "task_util_min",
    "task_util_max",
    "wcet_rounding",
    "deadline_mode",
    "actual_utilization_tolerance_total",
    "task_p_min",
    "task_p_max",
    "rta_horizon_ms",
    "rta_timeout_sec",
    "rta_initial_energy",
    "rta_assume_no_overflow",
    "profile_rta",
    "max_workers",
    "dry_run",
    "expected_rows",
    "actual_rows",
    "strict_fail_on_error",
    "rta_error_count",
    "result_error_count",
    "rta_timeout_count",
    "bad_config_counts",
    "failure_reason",
    "config_file",
    "task_file",
    "status",
    "error",
]

RESULT_FIELDS = [
    "config_id",
    "taskset_id",
    "seed",
    "task_n",
    "M",
    "utilization",
    "utilization_mode",
    "target_normalized_utilization",
    "target_total_utilization",
    "actual_total_utilization",
    "actual_normalized_utilization",
    "utilization_error_total",
    "task_util_min",
    "task_util_max",
    "wcet_rounding",
    "deadline_mode",
    "actual_utilization_tolerance_total",
    "task_p_min",
    "task_p_max",
    "rta_horizon_ms",
    "rta_initial_energy",
    "rta_assume_no_overflow",
    "profile_rta",
    "rta_version",
    "rta_tool",
    "rta_status",
    "rta_error",
    "rta_proven",
    "rta_schedulable",
    "rta_response_time_bound",
    "rta_response_bound",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_runtime_source",
    "rta_timed_out",
    "rta_timeout_sec",
    "rta_profile_enabled",
    "rta_profile_task_time_sum_sec",
    "rta_profile_task_count",
    "result_status",
    "result_error",
    "task_file",
    "config_file",
]


def _comma_ints(value: str) -> List[int]:
    try:
        values = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive integers")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("values must not contain duplicates")
    return values


def _comma_floats(value: str) -> List[float]:
    try:
        values = [float(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated numbers") from exc
    if not values or any(not math.isfinite(item) or item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be finite and positive")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("values must not contain duplicates")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run v20.4 ASAP-BLOCK RTA-only scalability samples. No scheduler "
            "simulation or v21 analysis is executed."
        )
    )
    parser.add_argument("--output-root", default="results/rta_scalability")
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument("--task-n-values", type=_comma_ints, default=[4, 8])
    parser.add_argument("--m-values", type=_comma_ints, default=[2, 4])
    parser.add_argument(
        "--utilizations", type=_comma_floats, default=[0.2, 0.4]
    )
    parser.add_argument(
        "--utilization-mode",
        choices=("normalized", "total"),
        default="normalized",
        help=(
            "interpret --utilizations as normalized load U/M by default; "
            "use total to preserve the legacy absolute total-utilization mode"
        ),
    )
    parser.add_argument("--num-tasksets", type=int, default=3)
    parser.add_argument("--task-p-min", type=int, default=40)
    parser.add_argument("--task-p-max", type=int, default=200)
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
    parser.add_argument("--rta-horizon-ms", type=int, required=True)
    parser.add_argument("--rta-timeout", type=float, default=30.0)
    parser.add_argument(
        "--rta-initial-energy",
        type=float,
        default=0.0,
        help=(
            "RTA analysis-window/job-release energy lower bound E0 in joules. "
            "E0 is not the simulation initial battery energy at t=0"
        ),
    )
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="parallel RTA subprocesses; keep 1 for paper runtime measurements",
    )
    parser.add_argument("--rta-assume-no-overflow", action="store_true")
    parser.add_argument(
        "--profile-rta",
        action="store_true",
        help="enable internal per-task profiling; disabled by default",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "write the full manifest and a header-only results CSV without "
            "generating configs/tasksets or invoking RTA"
        ),
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help=(
            "after writing results and manifest, exit nonzero if rows are "
            "missing, any RTA/result error occurred, any RTA timed out, or any "
            "configuration has the wrong number of tasksets"
        ),
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if RTA_VERSION != EXPECTED_RTA_VERSION or RTA_TOOL != EXPECTED_RTA_TOOL:
        parser.error(
            "E5 scalability requires the default v20.4 asap_block_rta.py path"
        )
    try:
        experiment_runner.safe_experiment_name(args.experiment_name)
    except ValueError as exc:
        parser.error(str(exc))
    for name in (
        "num_tasksets",
        "task_p_min",
        "task_p_max",
        "rta_horizon_ms",
        "max_workers",
    ):
        if getattr(args, name) <= 0:
            parser.error("--{} must be positive".format(name.replace("_", "-")))
    if args.task_p_min > args.task_p_max:
        parser.error("--task-p-min cannot exceed --task-p-max")
    if not math.isfinite(args.rta_timeout) or args.rta_timeout <= 0:
        parser.error("--rta-timeout must be finite and positive")
    if (
        not math.isfinite(args.rta_initial_energy)
        or args.rta_initial_energy < 0
    ):
        parser.error("--rta-initial-energy must be finite and non-negative")
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
    for utilization in args.utilizations:
        if not math.isfinite(utilization) or utilization <= 0:
            parser.error("--utilizations values must be finite and positive")
        if args.utilization_mode == "normalized" and utilization > 1:
            parser.error(
                "--utilizations must be in (0, 1] in normalized mode"
            )
        for processors in args.m_values:
            total_utilization = (
                utilization * processors
                if args.utilization_mode == "normalized"
                else utilization
            )
            if total_utilization > processors:
                parser.error(
                    "total utilization {} exceeds M={}".format(
                        total_utilization, processors
                    )
                )


def _number_token(value: float) -> str:
    return format(float(value), ".15g").replace("-", "m").replace(".", "p")


def config_id_for(
    task_n: int,
    processors: int,
    utilization: float,
    initial_energy: float,
    horizon_ms: int,
) -> str:
    return "n{}_m{}_u{}_e0{}_h{}".format(
        task_n,
        processors,
        _number_token(utilization),
        _number_token(initial_energy),
        horizon_ms,
    )


def seed_for(
    master_seed: int,
    task_n: int,
    processors: int,
    utilization: float,
    taskset_index: int,
) -> int:
    material = "{}|{}|{}|{}|{}".format(
        master_seed,
        task_n,
        processors,
        format(float(utilization), ".15g"),
        taskset_index,
    )
    digest = hashlib.sha256(material.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % 2147483647


def build_specs(args: argparse.Namespace, run_dir: Path) -> List[Dict[str, Any]]:
    specs = []
    index = 0
    for task_n in args.task_n_values:
        for processors in args.m_values:
            for utilization in args.utilizations:
                if args.utilization_mode == "normalized":
                    target_normalized_utilization = float(utilization)
                    target_total_utilization = float(
                        format(float(utilization) * processors, ".15g")
                    )
                else:
                    target_total_utilization = float(utilization)
                    target_normalized_utilization = float(
                        format(float(utilization) / processors, ".15g")
                    )
                config_id = config_id_for(
                    task_n,
                    processors,
                    utilization,
                    args.rta_initial_energy,
                    args.rta_horizon_ms,
                )
                config_file = run_dir / "configs" / "system_m{}.yml".format(
                    processors
                )
                for taskset_index in range(args.num_tasksets):
                    taskset_id = "{}_t{:04d}".format(config_id, taskset_index)
                    task_file = run_dir / "tasks" / "{}.yml".format(taskset_id)
                    specs.append({
                        "_index": index,
                        "config_id": config_id,
                        "taskset_id": taskset_id,
                        "seed": seed_for(
                            args.seed,
                            task_n,
                            processors,
                            utilization,
                            taskset_index,
                        ),
                        "task_n": task_n,
                        "M": processors,
                        "utilization": utilization,
                        "utilization_mode": args.utilization_mode,
                        "target_normalized_utilization": (
                            target_normalized_utilization
                        ),
                        "target_total_utilization": target_total_utilization,
                        "actual_total_utilization": "",
                        "actual_normalized_utilization": "",
                        "utilization_error_total": "",
                        "task_util_min": args.min_task_util,
                        "task_util_max": args.max_task_util,
                        "wcet_rounding": args.wcet_rounding,
                        "deadline_mode": (
                            "constrained"
                            if args.constrained_deadlines else "implicit"
                        ),
                        "actual_utilization_tolerance_total": (
                            ""
                            if args.actual_utilization_tolerance_total is None
                            else args.actual_utilization_tolerance_total
                        ),
                        "task_p_min": args.task_p_min,
                        "task_p_max": args.task_p_max,
                        "rta_horizon_ms": args.rta_horizon_ms,
                        "rta_timeout_sec": args.rta_timeout,
                        "rta_initial_energy": args.rta_initial_energy,
                        "rta_assume_no_overflow": args.rta_assume_no_overflow,
                        "profile_rta": args.profile_rta,
                        "max_workers": args.max_workers,
                        "dry_run": args.dry_run,
                        "expected_rows": "",
                        "actual_rows": "",
                        "strict_fail_on_error": args.fail_on_error,
                        "rta_error_count": "",
                        "result_error_count": "",
                        "rta_timeout_count": "",
                        "bad_config_counts": "",
                        "failure_reason": "",
                        "config_file": str(config_file),
                        "task_file": str(task_file),
                        "status": "dry_run" if args.dry_run else "planned",
                        "error": "",
                    })
                    index += 1
    return specs


def write_system_config(template_path: Path, output_path: Path, processors: int) -> None:
    with Path(template_path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    islands = config.get("cpu_islands") if isinstance(config, dict) else None
    if not isinstance(islands, list) or not islands or not isinstance(islands[0], dict):
        raise ValueError("system template must define cpu_islands[0]")
    islands[0]["numcpus"] = int(processors)
    kernel = islands[0].setdefault("kernel", {})
    if not isinstance(kernel, dict):
        raise ValueError("system template cpu_islands[0].kernel must be a mapping")
    kernel["scheduler"] = acceptance.ASAP_BLOCK_ALGORITHM
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)


def _generate_taskset(spec: Mapping[str, Any]) -> str:
    task_file = Path(spec["task_file"])
    task_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str((PROJECT_ROOT / acceptance.TASK_GENERATOR).resolve()),
        "-n", str(spec["task_n"]),
        "-u", format(float(spec["target_total_utilization"]), ".15g"),
        "-p", str(spec["task_p_min"]),
        "-P", str(spec["task_p_max"]),
        "-c", str(spec["M"]),
        "--seed", str(spec["seed"]),
        "-s", str(spec["config_file"]),
        "-o", str(task_file),
        "--min-task-util", str(spec["task_util_min"]),
        "--max-task-util", str(spec["task_util_max"]),
        "--wcet-rounding", str(spec["wcet_rounding"]),
    ]
    if spec.get("actual_utilization_tolerance_total") not in (None, ""):
        command.extend([
            "--actual-utilization-tolerance-total",
            str(spec["actual_utilization_tolerance_total"]),
        ])
    if str(spec.get("deadline_mode")) == "constrained":
        command.append("--constrained-deadlines")
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "task generation timed out after 60 seconds"
    except OSError as exc:
        return "task generation failed: {}".format(exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return "task generation exited with code {}{}".format(
            completed.returncode,
            ": {}".format(detail) if detail else "",
        )
    if not task_file.is_file():
        return "task generator reported success without creating {}".format(
            task_file
        )
    return ""


def _blank_rta_result(error: str) -> Dict[str, Any]:
    result = acceptance._base_rta_result(status="rta_error")
    result.update({
        "rta_enabled": True,
        "rta_error": error,
    })
    return result


def _result_row(
    spec: Mapping[str, Any],
    rta_result: Mapping[str, Any],
    result_status: str = "",
    result_error: str = "",
) -> Dict[str, Any]:
    status = str(rta_result.get("rta_status", "rta_error"))
    proven = bool(
        rta_result.get("rta_proven_under_assumptions", False)
        and status in {"proven_under_assumptions", "rta_proven"}
    )
    bound = rta_result.get("rta_bound")
    attempted = bool(rta_result.get("rta_attempted", False))
    return {
        "config_id": spec["config_id"],
        "taskset_id": spec["taskset_id"],
        "seed": spec["seed"],
        "task_n": spec["task_n"],
        "M": spec["M"],
        "utilization": spec["utilization"],
        "utilization_mode": spec["utilization_mode"],
        "target_normalized_utilization": spec[
            "target_normalized_utilization"
        ],
        "target_total_utilization": spec["target_total_utilization"],
        "actual_total_utilization": spec.get("actual_total_utilization", ""),
        "actual_normalized_utilization": spec.get(
            "actual_normalized_utilization", ""
        ),
        "utilization_error_total": spec.get("utilization_error_total", ""),
        "task_util_min": spec["task_util_min"],
        "task_util_max": spec["task_util_max"],
        "wcet_rounding": spec["wcet_rounding"],
        "deadline_mode": spec["deadline_mode"],
        "actual_utilization_tolerance_total": spec.get(
            "actual_utilization_tolerance_total", ""
        ),
        "task_p_min": spec["task_p_min"],
        "task_p_max": spec["task_p_max"],
        "rta_horizon_ms": spec["rta_horizon_ms"],
        "rta_initial_energy": spec["rta_initial_energy"],
        "rta_assume_no_overflow": spec["rta_assume_no_overflow"],
        "profile_rta": spec["profile_rta"],
        "rta_version": rta_result.get("rta_version", RTA_VERSION),
        "rta_tool": RTA_TOOL,
        "rta_status": status,
        "rta_error": rta_result.get("rta_error") or "",
        "rta_proven": proven,
        "rta_schedulable": proven,
        "rta_response_time_bound": "" if bound is None else bound,
        "rta_response_bound": "" if bound is None else bound,
        "rta_attempted": attempted,
        "rta_runtime_sec": (
            ""
            if rta_result.get("rta_runtime_sec") is None
            else rta_result.get("rta_runtime_sec")
        ),
        "rta_runtime_source": rta_result.get("rta_runtime_source", ""),
        "rta_timed_out": bool(rta_result.get("rta_timed_out", False)),
        "rta_timeout_sec": spec["rta_timeout_sec"] if attempted else "",
        "rta_profile_enabled": bool(
            rta_result.get("rta_profile_enabled", False)
        ),
        "rta_profile_task_time_sum_sec": (
            ""
            if rta_result.get("rta_profile_task_time_sum_sec") is None
            else rta_result.get("rta_profile_task_time_sum_sec")
        ),
        "rta_profile_task_count": int(
            rta_result.get("rta_profile_task_count", 0)
        ),
        "result_status": result_status,
        "result_error": result_error,
        "task_file": spec["task_file"],
        "config_file": spec["config_file"],
    }


def _run_spec(spec: Mapping[str, Any]) -> Dict[str, Any]:
    generation_error = _generate_taskset(spec)
    if generation_error:
        return {
            "result": _result_row(
                spec,
                _blank_rta_result(generation_error),
                result_status="task_generation_error",
                result_error=generation_error,
            ),
            "status": "task_generation_error",
            "error": generation_error,
        }
    utilization_metadata = acceptance.load_taskset_utilization_metadata(
        spec["task_file"],
        target_normalized_utilization=spec["target_normalized_utilization"],
        target_total_utilization=spec["target_total_utilization"],
        num_cores=spec["M"],
        task_util_min=spec["task_util_min"],
        task_util_max=spec["task_util_max"],
        wcet_rounding=spec["wcet_rounding"],
        deadline_mode=spec["deadline_mode"],
        actual_utilization_tolerance_total=spec.get(
            "actual_utilization_tolerance_total", ""
        ),
    )
    if isinstance(spec, dict):
        spec.update(utilization_metadata)
    rta_result = acceptance.run_asap_block_rta(
        algorithm=acceptance.ASAP_BLOCK_ALGORITHM,
        system_config=spec["config_file"],
        task_file=spec["task_file"],
        horizon_ms=spec["rta_horizon_ms"],
        assume_no_overflow=spec["rta_assume_no_overflow"],
        timeout=spec["rta_timeout_sec"],
        initial_energy=spec["rta_initial_energy"],
        profile_rta=spec["profile_rta"],
    )
    if not isinstance(rta_result, dict):
        raise TypeError("run_asap_block_rta must return a dictionary")
    result_error = rta_result.get("rta_error") or ""
    if rta_result.get("rta_timed_out", False):
        result_status = "rta_timeout"
    elif result_error:
        result_status = "rta_error"
    else:
        result_status = "completed"
    return {
        "result": _result_row(
            spec,
            rta_result,
            result_status=result_status,
            result_error=result_error,
        ),
        "status": result_status,
        "error": result_error,
    }


def _write_csv(
    path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    experiment_runner.write_manifest(path, fields, rows)


def _nonempty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _format_bad_config_counts(counts: Mapping[str, int]) -> str:
    return ";".join(
        "{}={}".format(config_id, counts[config_id])
        for config_id in sorted(counts)
    )


def validate_scalability_results_strict(
    rows: Sequence[Mapping[str, Any]],
    expected_config_ids: Sequence[str],
    num_tasksets: int,
    expected_rows: int,
) -> Dict[str, Any]:
    """Return run-level consistency/error counts for E5 scalability results."""
    actual_rows = len(rows)
    rta_error_count = sum(
        1 for row in rows if _nonempty(row.get("rta_error", ""))
    )
    result_error_count = sum(
        1 for row in rows if _nonempty(row.get("result_error", ""))
    )
    rta_timeout_count = sum(
        1 for row in rows if _is_true(row.get("rta_timed_out", False))
    )

    expected_config_set = set(expected_config_ids)
    observed_counts = Counter(str(row.get("config_id", "")) for row in rows)
    bad_config_counts: Dict[str, int] = {}
    for config_id in expected_config_ids:
        count = int(observed_counts.get(config_id, 0))
        if count != num_tasksets:
            bad_config_counts[config_id] = count
    for config_id, count in observed_counts.items():
        if config_id not in expected_config_set:
            bad_config_counts[config_id] = int(count)

    reasons = []
    if actual_rows != expected_rows:
        reasons.append("actual_rows != expected_rows")
    if rta_error_count > 0:
        reasons.append("rta_error_count > 0")
    if result_error_count > 0:
        reasons.append("result_error_count > 0")
    if rta_timeout_count > 0:
        reasons.append("rta_timeout_count > 0")
    if bad_config_counts:
        reasons.append("bad_config_counts")

    return {
        "expected_rows": expected_rows,
        "actual_rows": actual_rows,
        "rta_error_count": rta_error_count,
        "result_error_count": result_error_count,
        "rta_timeout_count": rta_timeout_count,
        "bad_config_counts": _format_bad_config_counts(bad_config_counts),
        "failure_reason": "; ".join(reasons),
        "failed": bool(reasons),
    }


def _apply_run_summary_to_manifest(
    manifest_rows: Sequence[Dict[str, Any]],
    summary: Mapping[str, Any],
    fail_on_error: bool,
) -> None:
    run_status = "failed" if fail_on_error and summary.get("failed") else "completed"
    for row in manifest_rows:
        row["expected_rows"] = summary.get("expected_rows", "")
        row["actual_rows"] = summary.get("actual_rows", "")
        row["strict_fail_on_error"] = fail_on_error
        row["rta_error_count"] = summary.get("rta_error_count", "")
        row["result_error_count"] = summary.get("result_error_count", "")
        row["rta_timeout_count"] = summary.get("rta_timeout_count", "")
        row["bad_config_counts"] = summary.get("bad_config_counts", "")
        row["failure_reason"] = summary.get("failure_reason", "")
        if row.get("status") != "dry_run":
            row["status"] = run_status


def _print_strict_failure(summary: Mapping[str, Any], results_path: Path) -> None:
    print("ERROR: strict scalability experiment failed", file=sys.stderr)
    print("results_path = {}".format(results_path), file=sys.stderr)
    for key in (
        "expected_rows",
        "actual_rows",
        "rta_error_count",
        "result_error_count",
        "rta_timeout_count",
        "bad_config_counts",
        "failure_reason",
    ):
        print("{} = {}".format(key, summary.get(key, "")), file=sys.stderr)


def run(args: argparse.Namespace) -> Path:
    name = experiment_runner.safe_experiment_name(args.experiment_name)
    run_dir = Path(args.output_root).resolve() / name
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(
            "refusing to overwrite non-empty run directory: {}".format(run_dir)
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / MANIFEST_FILENAME
    results_path = run_dir / RESULTS_FILENAME
    specs = build_specs(args, run_dir)
    expected_rows = len(specs)
    expected_config_ids = sorted({str(spec["config_id"]) for spec in specs})
    manifest_rows = [
        {field: spec.get(field, "") for field in MANIFEST_FIELDS}
        for spec in specs
    ]
    initial_summary = {
        "expected_rows": expected_rows,
        "actual_rows": 0,
        "rta_error_count": 0,
        "result_error_count": 0,
        "rta_timeout_count": 0,
        "bad_config_counts": "",
        "failure_reason": "",
        "failed": False,
    }
    _apply_run_summary_to_manifest(
        manifest_rows, initial_summary, args.fail_on_error
    )
    _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
    _write_csv(results_path, RESULT_FIELDS, [])
    if args.dry_run:
        return results_path

    for processors in args.m_values:
        path = run_dir / "configs" / "system_m{}.yml".format(processors)
        write_system_config(DEFAULT_SYSTEM_TEMPLATE, path, processors)

    results = []
    manifest_by_id = {
        row["taskset_id"]: row for row in manifest_rows
    }
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(_run_spec, spec): spec
            for spec in specs
        }
        for future in as_completed(futures):
            spec = futures[future]
            outcome = future.result()
            results.append((spec["_index"], outcome["result"]))
            manifest_row = manifest_by_id[spec["taskset_id"]]
            for field in (
                "actual_total_utilization",
                "actual_normalized_utilization",
                "utilization_error_total",
            ):
                manifest_row[field] = spec.get(field, manifest_row.get(field, ""))
            manifest_row["status"] = outcome["status"]
            manifest_row["error"] = outcome["error"]
            ordered_results = [
                row for _, row in sorted(results, key=lambda item: item[0])
            ]
            _write_csv(results_path, RESULT_FIELDS, ordered_results)
            _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)

    ordered_results = [
        row for _, row in sorted(results, key=lambda item: item[0])
    ]
    summary = validate_scalability_results_strict(
        ordered_results,
        expected_config_ids=expected_config_ids,
        num_tasksets=args.num_tasksets,
        expected_rows=expected_rows,
    )
    _apply_run_summary_to_manifest(
        manifest_rows, summary, args.fail_on_error
    )
    _write_csv(results_path, RESULT_FIELDS, ordered_results)
    _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
    if args.fail_on_error and summary["failed"]:
        _print_strict_failure(summary, results_path)
        raise SystemExit(1)

    experiment_runner.write_primary_analysis_artifact_attestation(
        results_path,
        companion_paths=[manifest_path],
        config_ids=expected_config_ids,
    )

    return results_path


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    results_path = run(args)
    print("RTA scalability results: {}".format(results_path))
    return results_path


if __name__ == "__main__":
    main()
