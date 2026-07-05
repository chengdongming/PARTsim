#!/usr/bin/env python3
"""Generate RTA-only ablation/refinement endpoint samples.

This runner is a framework for E3 experiments.  The first implementation only
contains existing endpoint variants: the default formal v20.4 RTA and the
experimental v21 local-window endpoint.  It intentionally does not implement
A0/A1/A2 component-level mathematical variants.
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance
from scripts import experiment_runner


MANIFEST_FILENAME = "rta_ablation_manifest.csv"
RESULTS_FILENAME = "rta_ablation_results.csv"
DEFAULT_SYSTEM_TEMPLATE = PROJECT_ROOT / acceptance.CONFIG_TEMPLATE
E0_SEMANTICS = (
    "conditional_lower_bound_at_each_analyzed_job_release_joules"
)
HARVESTING_PROFILE = "synthetic_piecewise_default"
V20_TOOL = "asap_block_rta.py"
V21_TOOL = "asap_block_rta_v21_local_window.py"
V20_VERSION = "v20.4"
V21_VERSION = "v21-local-window"


VARIANT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "v20p4_full": {
        "variant_name": "v20p4_full",
        "variant_label": "v20.4 full RTA",
        "variant_safety_label": "safe_under_v20p4_assumptions",
        "variant_description": (
            "official v20.4 deadline-parameterized ASAP-BLOCK sufficient RTA"
        ),
        "variant_is_default": True,
        "variant_is_experimental": False,
        "proof_claim_eligible": True,
        "diagnostic_only": False,
        "rta_tool": V20_TOOL,
        "expected_rta_version": V20_VERSION,
    },
    "v21_experimental": {
        "variant_name": "v21_experimental",
        "variant_label": "v21 local-window experimental RTA",
        "variant_safety_label": "experimental_sufficient_candidate",
        "variant_description": (
            "experimental local-window refinement endpoint, not default formal RTA"
        ),
        "variant_is_default": False,
        "variant_is_experimental": True,
        "proof_claim_eligible": False,
        "diagnostic_only": False,
        "rta_tool": V21_TOOL,
        "expected_rta_version": V21_VERSION,
    },
}

DISABLED_COMPONENT_VARIANTS = {
    "baseline_safe",
    "a0",
    "A0",
    "a1",
    "A1",
    "a2",
    "A2",
}

PARAMETER_FIELDS = [
    "experiment_name",
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
    "max_workers",
    "dry_run",
]

VARIANT_FIELDS = [
    "variant_name",
    "variant_label",
    "variant_safety_label",
    "variant_description",
    "variant_is_default",
    "variant_is_experimental",
    "proof_claim_eligible",
    "diagnostic_only",
]

ENERGY_FIELDS = [
    "harvesting_profile",
    "harvesting_profile_fixed",
    "use_real_solar_data",
    "harvesting_scale",
]

PATH_STATUS_FIELDS = [
    "task_file",
    "task_file_sha256",
    "config_file",
    "status",
    "error",
]

MANIFEST_FIELDS = (
    PARAMETER_FIELDS + VARIANT_FIELDS + ENERGY_FIELDS + PATH_STATUS_FIELDS
)

RESULT_FIELDS = MANIFEST_FIELDS + [
    "rta_version",
    "rta_tool",
    "expected_rta_version",
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
            float(item.strip()) for item in str(value).split(",") if item.strip()
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


def _comma_variants(value: str) -> List[str]:
    variants = [item.strip() for item in str(value).split(",") if item.strip()]
    if not variants:
        raise argparse.ArgumentTypeError("at least one variant is required")
    if len(set(variants)) != len(variants):
        raise argparse.ArgumentTypeError("variants must not contain duplicates")
    return variants


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run RTA-only ablation/refinement endpoint samples. The current "
            "framework only supports existing v20.4 and experimental v21 "
            "endpoints; it does not implement A0/A1/A2 component variants, "
            "scheduler simulation, observed pessimism, or acceptance ratios."
        )
    )
    parser.add_argument("--output-root", default="results/rta_ablation")
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument(
        "--variants",
        type=_comma_variants,
        default=["v20p4_full"],
        help="comma-separated endpoint variants: v20p4_full,v21_experimental",
    )
    parser.add_argument(
        "--utilizations",
        type=_comma_floats,
        default=[0.2],
        help="comma-separated normalized utilization values in (0, 1]",
    )
    parser.add_argument("--task-n", type=int, default=8)
    parser.add_argument("--M", type=int, default=4)
    parser.add_argument("--num-tasksets", type=int, default=20)
    parser.add_argument("--task-p-min", type=int, default=40)
    parser.add_argument("--task-p-max", type=int, default=200)
    parser.add_argument("--rta-horizon-ms", type=int, required=True)
    parser.add_argument("--rta-timeout", type=float, default=30.0)
    parser.add_argument("--rta-initial-energy", type=float, default=0.0)
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
        help=(
            "request internal RTA profiling where the endpoint supports it; "
            "v21 currently reports only subprocess wall-clock runtime"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "write the full manifest and a header-only results CSV without "
            "generating configs/tasksets or invoking RTA"
        ),
    )
    return parser


def _valid_normalized_utilization(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 < number <= 1


def validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if acceptance.RTA_VERSION != V20_VERSION or Path(acceptance.RTA_TOOL).name != V20_TOOL:
        parser.error(
            "E3 ablation framework requires default v20.4 asap_block_rta.py path"
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
    if (
        not math.isfinite(args.rta_initial_energy)
        or args.rta_initial_energy < 0
    ):
        parser.error("--rta-initial-energy must be finite and non-negative")
    if any(not _valid_normalized_utilization(value) for value in args.utilizations):
        parser.error("--utilizations values must be normalized values in (0, 1]")

    unknown = [variant for variant in args.variants if variant not in VARIANT_REGISTRY]
    if unknown:
        disabled = [item for item in unknown if item in DISABLED_COMPONENT_VARIANTS]
        if disabled:
            parser.error(
                "A0/A1/A2 component variants are not runnable in Phase E-1: "
                + ", ".join(disabled)
            )
        parser.error("unknown RTA ablation variant: {}".format(", ".join(unknown)))


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
    processors: int,
    task_n: int,
    normalized_utilization: float,
    initial_energy: float,
    horizon_ms: int,
) -> str:
    return "e3_m{}_n{}_u{}_e0{}_h{}".format(
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


def build_specs(
    args: argparse.Namespace, run_dir: Path
) -> List[Dict[str, Any]]:
    specs = []
    index = 0
    for normalized_utilization in args.utilizations:
        total_utilization = float(
            format(float(normalized_utilization) * args.M, ".15g")
        )
        config_id = config_id_for(
            args.M,
            args.task_n,
            normalized_utilization,
            args.rta_initial_energy,
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
            seed = seed_for(
                args.seed,
                args.M,
                args.task_n,
                normalized_utilization,
                taskset_index,
            )
            base = {
                "experiment_name": args.experiment_name,
                "config_id": config_id,
                "taskset_id": family_id,
                "taskset_family_id": family_id,
                "seed": seed,
                "task_n": args.task_n,
                "M": args.M,
                "normalized_utilization": float(normalized_utilization),
                "total_utilization": total_utilization,
                "rta_initial_energy": float(args.rta_initial_energy),
                "rta_initial_energy_semantics": E0_SEMANTICS,
                "task_p_min": args.task_p_min,
                "task_p_max": args.task_p_max,
                "rta_horizon_ms": args.rta_horizon_ms,
                "rta_timeout_sec": args.rta_timeout,
                "rta_assume_no_overflow": args.rta_assume_no_overflow,
                "profile_rta": args.profile_rta,
                "max_workers": args.max_workers,
                "dry_run": args.dry_run,
                "harvesting_profile": HARVESTING_PROFILE,
                "harvesting_profile_fixed": True,
                "use_real_solar_data": False,
                "harvesting_scale": 1.0,
                "task_file": str(task_file),
                "task_file_sha256": "",
                "config_file": str(config_file),
                "status": "dry_run" if args.dry_run else "planned",
                "error": "",
            }
            for variant_name in args.variants:
                variant = VARIANT_REGISTRY[variant_name]
                spec = {
                    "_index": index,
                    **base,
                    **{field: variant[field] for field in VARIANT_FIELDS},
                    "_rta_tool": variant["rta_tool"],
                    "_expected_rta_version": variant["expected_rta_version"],
                }
                specs.append(spec)
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


def _base_rta_result(expected_version: str, status: str = "rta_error") -> Dict[str, Any]:
    return {
        "rta_version": expected_version,
        "rta_status": status,
        "rta_error": None,
        "rta_proven_under_assumptions": False,
        "rta_bound": None,
        "rta_attempted": False,
        "rta_runtime_sec": None,
        "rta_runtime_source": "",
        "rta_timed_out": False,
        "rta_timeout_sec": None,
        "rta_profile_enabled": False,
        "rta_profile_task_time_sum_sec": None,
        "rta_profile_task_count": 0,
        "rta_report": None,
    }


def _blank_rta_result(spec: Mapping[str, Any], error: str) -> Dict[str, Any]:
    result = _base_rta_result(spec["_expected_rta_version"], status="rta_error")
    result["rta_error"] = error
    return result


def _parse_rta_payload(
    payload: Mapping[str, Any],
    expected_version: str,
    assume_no_overflow: bool,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("RTA output must be a JSON object")
    if payload.get("rta_version") != expected_version:
        raise ValueError(
            "expected {} report, got {!r}".format(
                expected_version, payload.get("rta_version")
            )
        )
    if "proven_under_assumptions" not in payload:
        raise ValueError("RTA JSON is missing proven_under_assumptions")
    reported_proven = payload.get("proven_under_assumptions")
    if not isinstance(reported_proven, bool):
        raise ValueError("RTA proven_under_assumptions must be boolean")

    bounds = []
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("RTA JSON tasks must be a list")
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("RTA JSON task entries must be objects")
        if bool(task.get("proven_under_assumptions", task.get("proven", False))):
            bound = acceptance._extract_number(task.get("response_time_bound"))
            if bound is not None:
                bounds.append(bound)

    proven = bool(reported_proven and assume_no_overflow)
    return {
        "rta_version": expected_version,
        "rta_status": (
            "proven_under_assumptions" if proven else "rta_unproven"
        ),
        "rta_error": None,
        "rta_proven_under_assumptions": proven,
        "rta_bound": max(bounds) if bounds else None,
        "rta_report": dict(payload),
    }


def _normalize_version(
    spec: Mapping[str, Any], rta_result: Mapping[str, Any]
) -> Dict[str, Any]:
    result = dict(rta_result)
    expected = spec["_expected_rta_version"]
    actual = result.get("rta_version")
    if not result.get("rta_error") and actual != expected:
        result.update({
            "rta_version": actual or "",
            "rta_status": "rta_error",
            "rta_error": "expected {} report, got {!r}".format(expected, actual),
            "rta_proven_under_assumptions": False,
            "rta_bound": None,
        })
    return result


def _run_v20_rta(spec: Mapping[str, Any]) -> Dict[str, Any]:
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
    return _normalize_version(spec, result)


def _run_v21_rta(spec: Mapping[str, Any]) -> Dict[str, Any]:
    result = _base_rta_result(spec["_expected_rta_version"], status="rta_error")
    command = [
        sys.executable,
        str(PROJECT_ROOT / V21_TOOL),
        "--system", str(spec["config_file"]),
        "--tasks", str(spec["task_file"]),
        "--horizon-ms", str(spec["rta_horizon_ms"]),
        "--rta-initial-energy", str(spec["rta_initial_energy"]),
    ]
    if spec["rta_assume_no_overflow"]:
        command.append("--assume-no-overflow")
    command.append("--json")

    result.update({
        "rta_attempted": True,
        "rta_runtime_source": "subprocess_wall_clock_perf_counter",
        "rta_timeout_sec": spec["rta_timeout_sec"],
        "rta_profile_enabled": False,
    })
    started = time.perf_counter()
    try:
        try:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=spec["rta_timeout_sec"],
            )
        finally:
            result["rta_runtime_sec"] = time.perf_counter() - started
    except subprocess.TimeoutExpired:
        result["rta_timed_out"] = True
        result["rta_error"] = "RTA timed out after {} seconds".format(
            spec["rta_timeout_sec"]
        )
        return result
    except OSError as exc:
        result["rta_error"] = str(exc)
        return result

    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "v21 RTA failed").strip()
        result["rta_error"] = error
        return result
    try:
        payload = json.loads(completed.stdout)
        result.update(
            _parse_rta_payload(
                payload,
                spec["_expected_rta_version"],
                spec["rta_assume_no_overflow"],
            )
        )
    except (json.JSONDecodeError, ValueError) as exc:
        result["rta_error"] = str(exc)
    return _normalize_version(spec, result)


def _run_rta(spec: Mapping[str, Any]) -> Dict[str, Any]:
    if spec["variant_name"] == "v20p4_full":
        return _run_v20_rta(spec)
    if spec["variant_name"] == "v21_experimental":
        return _run_v21_rta(spec)
    raise ValueError("unsupported variant {}".format(spec["variant_name"]))


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
    if result_status is None:
        result_status = _result_status(rta_result)
    if result_error is None:
        result_error = rta_result.get("rta_error") or ""

    row = {field: spec.get(field, "") for field in MANIFEST_FIELDS}
    row.update({
        "rta_version": rta_result.get("rta_version", ""),
        "rta_tool": spec["_rta_tool"],
        "expected_rta_version": spec["_expected_rta_version"],
        "rta_status": status,
        "rta_error": rta_result.get("rta_error") or "",
        "rta_proven": proven,
        "rta_schedulable": proven,
        "rta_response_time_bound": "" if bound is None else bound,
        "rta_response_bound": "" if bound is None else bound,
        "rta_attempted": bool(rta_result.get("rta_attempted", False)),
        "rta_runtime_sec": (
            ""
            if rta_result.get("rta_runtime_sec") is None
            else rta_result.get("rta_runtime_sec")
        ),
        "rta_runtime_source": rta_result.get("rta_runtime_source", ""),
        "rta_timed_out": bool(rta_result.get("rta_timed_out", False)),
        "rta_timeout_sec": (
            ""
            if rta_result.get("rta_timeout_sec") is None
            else rta_result.get("rta_timeout_sec")
        ),
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
    return row


def _write_csv(
    path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    experiment_runner.write_manifest(path, fields, rows)


def _unique_by(specs: Sequence[Mapping[str, Any]], key: str):
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
            _blank_rta_result(spec, error),
            result_status="config_error",
            result_error=error,
        )

    families: Dict[str, List[Dict[str, Any]]] = {}
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
                    _blank_rta_result(spec, generation_error),
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
        [completed_rows[index] for index in sorted(completed_rows)],
    )
    _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {executor.submit(_run_rta, spec): spec for spec in rta_specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                rta_result = future.result()
                status = _result_status(rta_result)
                error = rta_result.get("rta_error") or ""
            except Exception as exc:
                error = "RTA runner exception: {}".format(exc)
                rta_result = _blank_rta_result(spec, error)
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
                [completed_rows[index] for index in sorted(completed_rows)],
            )
            _write_csv(manifest_path, MANIFEST_FIELDS, manifest_rows)

    return results_path


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    results_path = run(args)
    print("RTA ablation/refinement results: {}".format(results_path))
    return results_path


if __name__ == "__main__":
    main()
