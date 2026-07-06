#!/usr/bin/env python3
"""Generate v20.4 RTA-only utilization/release-time-E0 sensitivity samples."""

import argparse
import hashlib
import math
import subprocess
import sys
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
MANIFEST_FILENAME = "rta_parameter_sensitivity_manifest.csv"
RESULTS_FILENAME = "rta_parameter_sensitivity_results.csv"
DEFAULT_SYSTEM_TEMPLATE = PROJECT_ROOT / acceptance.CONFIG_TEMPLATE
HARVESTING_PROFILE = "synthetic_piecewise_default"
E0_SEMANTICS = (
    "conditional_lower_bound_at_each_analyzed_job_release_joules"
)

PARAMETER_FIELDS = [
    "experiment_name",
    "sweep_name",
    "sweep_parameter",
    "sweep_value",
    "config_id",
    "taskset_id",
    "taskset_family_id",
    "seed",
    "task_n",
    "M",
    "normalized_utilization",
    "total_utilization",
    "rta_initial_energy",
    "rta_initial_energy_semantics",
    "task_p_min",
    "task_p_max",
    "rta_horizon_ms",
    "rta_timeout_sec",
    "rta_assume_no_overflow",
    "profile_rta",
    "harvesting_profile",
    "harvesting_profile_fixed",
    "use_real_solar_data",
    "harvesting_scale",
    "max_workers",
    "dry_run",
    "task_file",
    "task_file_sha256",
    "config_file",
]

MANIFEST_FIELDS = PARAMETER_FIELDS + [
    "status",
    "error",
]

RESULT_FIELDS = PARAMETER_FIELDS + [
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
    "rta_profile_enabled",
    "rta_profile_task_time_sum_sec",
    "rta_profile_task_count",
    "result_status",
    "result_error",
]


def _comma_floats(value: str) -> List[float]:
    try:
        values = [
            float(item.strip()) for item in value.split(",") if item.strip()
        ]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected a comma-separated numeric list"
        ) from exc
    if not values or any(not math.isfinite(item) for item in values):
        raise argparse.ArgumentTypeError("values must be finite numbers")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("values must not contain duplicates")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one OFAT utilization or release-time energy lower-bound E0 "
            "sensitivity sweep using only the default v20.4 ASAP-BLOCK RTA. "
            "No simulation or v21 analysis is executed."
        )
    )
    parser.add_argument(
        "--output-root", default="results/rta_parameter_sensitivity"
    )
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument(
        "--sweep", required=True, choices=("utilization", "e0")
    )
    parser.add_argument("--utilizations", type=_comma_floats)
    parser.add_argument(
        "--fixed-e0",
        type=float,
        help=(
            "fixed RTA analysis-window/job-release energy lower bound E0 in "
            "joules for a utilization sweep. E0 is not the simulation "
            "initial battery energy at t=0"
        ),
    )
    parser.add_argument(
        "--e0-values",
        type=_comma_floats,
        help=(
            "comma-separated RTA analysis-window/job-release energy lower "
            "bounds in joules. E0 is not the simulation initial battery "
            "energy at t=0; E0>0 is a conditional assumption"
        ),
    )
    parser.add_argument("--fixed-utilization", type=float)
    parser.add_argument("--task-n", type=int, default=8)
    parser.add_argument("--M", type=int, default=4)
    parser.add_argument("--num-tasksets", type=int, default=20)
    parser.add_argument("--task-p-min", type=int, default=40)
    parser.add_argument("--task-p-max", type=int, default=200)
    parser.add_argument("--rta-horizon-ms", type=int, required=True)
    parser.add_argument("--rta-timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="parallel RTA subprocesses; keep 1 for paper runtime results",
    )
    parser.add_argument("--rta-assume-no-overflow", action="store_true")
    parser.add_argument(
        "--profile-rta",
        action="store_true",
        help="enable internal per-task RTA profiling; disabled by default",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "write the complete manifest and a header-only results CSV "
            "without generating configs/tasksets or invoking RTA"
        ),
    )
    return parser


def _valid_normalized_utilization(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 < number <= 1


def _valid_e0(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0


def validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if RTA_VERSION != EXPECTED_RTA_VERSION or RTA_TOOL != EXPECTED_RTA_TOOL:
        parser.error(
            "E4 sensitivity requires the default v20.4 asap_block_rta.py path"
        )
    try:
        experiment_runner.safe_experiment_name(args.experiment_name)
    except ValueError as exc:
        parser.error(str(exc))
    for name in (
        "task_n",
        "M",
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

    if args.sweep == "utilization":
        if not args.utilizations:
            parser.error("--utilizations is required for utilization sweep")
        if args.fixed_e0 is None:
            parser.error("--fixed-e0 is required for utilization sweep")
        if args.e0_values is not None:
            parser.error("--e0-values is only valid for e0 sweep")
        if any(
            not _valid_normalized_utilization(value)
            for value in args.utilizations
        ):
            parser.error(
                "--utilizations values must be normalized values in (0, 1]"
            )
        if not _valid_e0(args.fixed_e0):
            parser.error("--fixed-e0 must be finite and non-negative")
    else:
        if not args.e0_values:
            parser.error("--e0-values is required for e0 sweep")
        if args.utilizations is not None:
            parser.error("--utilizations is only valid for utilization sweep")
        if args.fixed_utilization is None:
            parser.error("--fixed-utilization is required for e0 sweep")
        if not _valid_normalized_utilization(args.fixed_utilization):
            parser.error(
                "--fixed-utilization must be a normalized value in (0, 1]"
            )
        if any(not _valid_e0(value) for value in args.e0_values):
            parser.error("--e0-values must be finite and non-negative")


def _number_token(value: float) -> str:
    return format(float(value), ".15g").replace("-", "m").replace(".", "p")


def taskset_family_id_for(
    processors: int,
    task_n: int,
    normalized_utilization: float,
    taskset_index: int,
) -> str:
    return "m{}_n{}_u{}_t{:04d}".format(
        processors,
        task_n,
        _number_token(normalized_utilization),
        taskset_index,
    )


def config_id_for(
    sweep: str,
    processors: int,
    task_n: int,
    normalized_utilization: float,
    initial_energy: float,
    horizon_ms: int,
) -> str:
    return "{}_m{}_n{}_u{}_e0{}_h{}".format(
        sweep,
        processors,
        task_n,
        _number_token(normalized_utilization),
        _number_token(initial_energy),
        horizon_ms,
    )


def seed_for(
    master_seed: int,
    processors: int,
    task_n: int,
    normalized_utilization: float,
    taskset_index: int,
) -> int:
    material = "{}|{}|{}|{}|{}".format(
        master_seed,
        processors,
        task_n,
        format(float(normalized_utilization), ".15g"),
        taskset_index,
    )
    digest = hashlib.sha256(material.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % 2147483647


def _sweep_values(args: argparse.Namespace):
    if args.sweep == "utilization":
        return [
            (float(utilization), float(args.fixed_e0), float(utilization))
            for utilization in args.utilizations
        ]
    return [
        (
            float(args.fixed_utilization),
            float(initial_energy),
            float(initial_energy),
        )
        for initial_energy in args.e0_values
    ]


def build_specs(
    args: argparse.Namespace, run_dir: Path
) -> List[Dict[str, Any]]:
    specs = []
    index = 0
    for normalized_utilization, initial_energy, sweep_value in _sweep_values(
        args
    ):
        total_utilization = float(
            format(normalized_utilization * args.M, ".15g")
        )
        config_id = config_id_for(
            args.sweep,
            args.M,
            args.task_n,
            normalized_utilization,
            initial_energy,
            args.rta_horizon_ms,
        )
        config_file = run_dir / "configs" / "{}.yml".format(config_id)
        for taskset_index in range(args.num_tasksets):
            family_id = taskset_family_id_for(
                args.M,
                args.task_n,
                normalized_utilization,
                taskset_index,
            )
            task_file = run_dir / "tasks" / "{}.yml".format(family_id)
            specs.append({
                "_index": index,
                "experiment_name": args.experiment_name,
                "sweep_name": args.sweep,
                "sweep_parameter": args.sweep,
                "sweep_value": sweep_value,
                "config_id": config_id,
                "taskset_id": family_id,
                "taskset_family_id": family_id,
                "seed": seed_for(
                    args.seed,
                    args.M,
                    args.task_n,
                    normalized_utilization,
                    taskset_index,
                ),
                "task_n": args.task_n,
                "M": args.M,
                "normalized_utilization": normalized_utilization,
                "total_utilization": total_utilization,
                "rta_initial_energy": initial_energy,
                "rta_initial_energy_semantics": E0_SEMANTICS,
                "task_p_min": args.task_p_min,
                "task_p_max": args.task_p_max,
                "rta_horizon_ms": args.rta_horizon_ms,
                "rta_timeout_sec": args.rta_timeout,
                "rta_assume_no_overflow": args.rta_assume_no_overflow,
                "profile_rta": args.profile_rta,
                "harvesting_profile": HARVESTING_PROFILE,
                "harvesting_profile_fixed": True,
                "use_real_solar_data": False,
                "harvesting_scale": 1.0,
                "max_workers": args.max_workers,
                "dry_run": args.dry_run,
                "task_file": str(task_file),
                "task_file_sha256": "",
                "config_file": str(config_file),
                "status": "dry_run" if args.dry_run else "planned",
                "error": "",
            })
            index += 1
    return specs


def write_system_config(
    template_path: Path, output_path: Path, processors: int
) -> None:
    with Path(template_path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    islands = config.get("cpu_islands") if isinstance(config, dict) else None
    if (
        not isinstance(islands, list)
        or not islands
        or not isinstance(islands[0], dict)
    ):
        raise ValueError("system template must define cpu_islands[0]")
    islands[0]["numcpus"] = int(processors)
    kernel = islands[0].setdefault("kernel", {})
    if not isinstance(kernel, dict):
        raise ValueError(
            "system template cpu_islands[0].kernel must be a mapping"
        )
    kernel["scheduler"] = acceptance.ASAP_BLOCK_ALGORITHM

    energy = config.setdefault("energy_management", {})
    if not isinstance(energy, dict):
        raise ValueError("system template energy_management must be a mapping")
    # This fixed label corresponds to the synthetic profile configured here.
    energy["use_real_solar_data"] = False
    energy["harvesting_scale"] = 1.0

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
        "-u", format(float(spec["total_utilization"]), ".15g"),
        "-p", str(spec["task_p_min"]),
        "-P", str(spec["task_p_max"]),
        "-c", str(spec["M"]),
        "--seed", str(spec["seed"]),
        "-s", str(spec["config_file"]),
        "-o", str(task_file),
    ]
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _blank_rta_result(error: str) -> Dict[str, Any]:
    result = acceptance._base_rta_result(status="rta_error")
    result.update({
        "rta_enabled": True,
        "rta_error": error,
    })
    return result


def _result_status(rta_result: Mapping[str, Any]) -> str:
    if bool(rta_result.get("rta_timed_out", False)):
        return "rta_timeout"
    if rta_result.get("rta_error"):
        return "rta_error"
    if str(rta_result.get("rta_status", "")).strip().lower() in {
        "rta_error",
        "error",
        "failed",
    }:
        return "rta_error"
    return "completed"


def _result_row(
    spec: Mapping[str, Any],
    rta_result: Mapping[str, Any],
    result_status: str = None,
    result_error: str = None,
) -> Dict[str, Any]:
    status = str(rta_result.get("rta_status", "rta_error"))
    proven = bool(
        rta_result.get("rta_proven_under_assumptions", False)
        and status in {"proven_under_assumptions", "rta_proven"}
    )
    bound = rta_result.get("rta_bound")
    attempted = bool(rta_result.get("rta_attempted", False))
    if result_status is None:
        result_status = _result_status(rta_result)
    if result_error is None:
        result_error = rta_result.get("rta_error") or ""
    row = {
        field: spec.get(field, "")
        for field in PARAMETER_FIELDS
    }
    row.update({
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
    })
    timeout_value = rta_result.get("rta_timeout_sec")
    if attempted and timeout_value is not None:
        row["rta_timeout_sec"] = timeout_value
    return row


def _run_rta(spec: Mapping[str, Any]) -> Dict[str, Any]:
    result = acceptance.run_asap_block_rta(
        algorithm=acceptance.ASAP_BLOCK_ALGORITHM,
        system_config=spec["config_file"],
        task_file=spec["task_file"],
        horizon_ms=spec["rta_horizon_ms"],
        assume_no_overflow=spec["rta_assume_no_overflow"],
        timeout=spec["rta_timeout_sec"],
        initial_energy=spec["rta_initial_energy"],
        profile_rta=spec["profile_rta"],
    )
    if not isinstance(result, dict):
        raise TypeError("run_asap_block_rta must return a dictionary")
    return result


def _write_csv(
    path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    experiment_runner.write_manifest(path, fields, rows)


def _unique_by(specs, key):
    unique = {}
    for spec in specs:
        unique.setdefault(spec[key], spec)
    return unique


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
    manifest_rows = [
        {field: spec.get(field, "") for field in MANIFEST_FIELDS}
        for spec in specs
    ]
    _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)
    _write_csv(results_path, RESULT_FIELDS, [])
    if args.dry_run:
        return results_path

    completed_rows: Dict[int, Dict[str, Any]] = {}
    config_errors = {}
    for config_file, spec in _unique_by(specs, "config_file").items():
        try:
            write_system_config(
                DEFAULT_SYSTEM_TEMPLATE,
                Path(config_file),
                spec["M"],
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            config_errors[config_file] = "config generation failed: {}".format(
                exc
            )

    runnable_specs = []
    for spec in specs:
        error = config_errors.get(spec["config_file"])
        if not error:
            runnable_specs.append(spec)
            continue
        spec["status"] = "config_error"
        spec["error"] = error
        manifest_rows[spec["_index"]].update(status=spec["status"], error=error)
        completed_rows[spec["_index"]] = _result_row(
            spec,
            _blank_rta_result(error),
            result_status="config_error",
            result_error=error,
        )

    families = {}
    for spec in runnable_specs:
        families.setdefault(spec["taskset_family_id"], []).append(spec)
    rta_specs = []
    for family_specs in families.values():
        representative = family_specs[0]
        generation_error = _generate_taskset(representative)
        if generation_error:
            for spec in family_specs:
                spec["status"] = "task_generation_error"
                spec["error"] = generation_error
                manifest_rows[spec["_index"]].update(
                    status=spec["status"],
                    error=generation_error,
                )
                completed_rows[spec["_index"]] = _result_row(
                    spec,
                    _blank_rta_result(generation_error),
                    result_status="task_generation_error",
                    result_error=generation_error,
                )
            continue
        task_hash = _sha256(Path(representative["task_file"]))
        for spec in family_specs:
            spec["task_file_sha256"] = task_hash
            spec["status"] = "task_ready"
            manifest_rows[spec["_index"]].update(
                task_file_sha256=task_hash,
                status="task_ready",
                error="",
            )
            rta_specs.append(spec)

    _write_csv(
        results_path,
        RESULT_FIELDS,
        [
            completed_rows[index]
            for index in sorted(completed_rows)
        ],
    )
    _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(_run_rta, spec): spec
            for spec in rta_specs
        }
        for future in as_completed(futures):
            spec = futures[future]
            try:
                rta_result = future.result()
                status = _result_status(rta_result)
                error = rta_result.get("rta_error") or ""
            except Exception as exc:
                error = "RTA runner exception: {}".format(exc)
                rta_result = _blank_rta_result(error)
                status = "runner_error"
            spec["status"] = status
            spec["error"] = error
            manifest_rows[spec["_index"]].update(status=status, error=error)
            completed_rows[spec["_index"]] = _result_row(
                spec,
                rta_result,
                result_status=status,
                result_error=error,
            )
            _write_csv(
                results_path,
                RESULT_FIELDS,
                [
                    completed_rows[index]
                    for index in sorted(completed_rows)
                ],
            )
            _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)

    return results_path


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    results_path = run(args)
    print("RTA parameter sensitivity results: {}".format(results_path))
    return results_path


if __name__ == "__main__":
    main()
