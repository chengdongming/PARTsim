#!/usr/bin/env python3
"""Generate RTA-only E3 ablation/refinement samples.

This runner keeps the RTA implementations isolated.  A0-A2 are delegated to
``asap_block_rta_ablation_variants.py``, A3 uses the official frozen v20.4
tool, and A4 uses the frozen v21 local-window closure tool.
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
ABLATION_TOOL = "asap_block_rta_ablation_variants.py"
V20_VERSION = "v20.4"
V21_VERSION = "v21-local-window"
ALL_VARIANT_ORDER = [
    "baseline_safe",
    "carry_in_certified",
    "capacity_coupled",
    "v20p4_full",
    "v21_local_window_closure",
]


VARIANT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "baseline_safe": {
        "variant": "baseline_safe",
        "variant_name": "baseline_safe",
        "variant_canonical": "baseline_safe",
        "variant_stage": "A0",
        "variant_label": "A0 baseline safe",
        "variant_group": "safe_chain",
        "formal_variant": True,
        "theory_family": "complete_window_component",
        "closure_method": "none_component_baseline",
        "variant_safety_label": "safe_under_v20p4_assumptions",
        "variant_description": "A0 safe deadline-workload baseline",
        "variant_is_default": False,
        "variant_is_experimental": False,
        "proof_claim_eligible": True,
        "diagnostic_only": False,
        "rta_tool": ABLATION_TOOL,
        "expected_rta_version": "e3-a0-baseline-safe",
        "ablation_variant": "baseline_safe",
        "uses_certified_carry_in": False,
        "uses_processor_capacity_coupling": False,
        "uses_window_level_task_capacity": False,
        "uses_window_level_u_capacity": False,
        "uses_local_window": False,
        "uses_delta_closure": False,
        "uses_parallel_u_compression": False,
        "certificate_policy": "none_deadline_workload",
        "empty_state_guard": True,
        "empty_set_guard": False,
        "fallback_guard": True,
        "consistency_guard": True,
    },
    "carry_in_certified": {
        "variant": "carry_in_certified",
        "variant_name": "carry_in_certified",
        "variant_canonical": "carry_in_certified",
        "variant_stage": "A1",
        "variant_label": "A1 certified carry-in",
        "variant_group": "safe_chain",
        "formal_variant": True,
        "theory_family": "complete_window_component",
        "closure_method": "none_component_certified_carry_in",
        "variant_safety_label": "safe_under_v20p4_assumptions",
        "variant_description": "A1 complete-window component with certified carry-in",
        "variant_is_default": False,
        "variant_is_experimental": False,
        "proof_claim_eligible": True,
        "diagnostic_only": False,
        "rta_tool": ABLATION_TOOL,
        "expected_rta_version": "e3-a1-carry-in-certified",
        "ablation_variant": "carry_in_certified",
        "uses_certified_carry_in": True,
        "uses_processor_capacity_coupling": False,
        "uses_window_level_task_capacity": False,
        "uses_window_level_u_capacity": False,
        "uses_local_window": False,
        "uses_delta_closure": False,
        "uses_parallel_u_compression": False,
        "certificate_policy": "strict_variant_specific",
        "empty_state_guard": True,
        "empty_set_guard": False,
        "fallback_guard": True,
        "consistency_guard": True,
    },
    "capacity_coupled": {
        "variant": "capacity_coupled",
        "variant_name": "capacity_coupled",
        "variant_canonical": "capacity_coupled",
        "variant_stage": "A2",
        "variant_label": "A2 capacity coupled",
        "variant_group": "safe_chain",
        "formal_variant": True,
        "theory_family": "complete_window_component",
        "closure_method": "processor_capacity_coupled",
        "variant_safety_label": "safe_under_v20p4_assumptions",
        "variant_description": "A2 complete-window component with processor capacity coupling",
        "variant_is_default": False,
        "variant_is_experimental": False,
        "proof_claim_eligible": True,
        "diagnostic_only": False,
        "rta_tool": ABLATION_TOOL,
        "expected_rta_version": "e3-a2-capacity-coupled",
        "ablation_variant": "capacity_coupled",
        "uses_certified_carry_in": True,
        "uses_processor_capacity_coupling": True,
        "uses_window_level_task_capacity": False,
        "uses_window_level_u_capacity": False,
        "uses_local_window": False,
        "uses_delta_closure": False,
        "uses_parallel_u_compression": False,
        "certificate_policy": "strict_variant_specific",
        "empty_state_guard": True,
        "empty_set_guard": False,
        "fallback_guard": True,
        "consistency_guard": True,
    },
    "v20p4_full": {
        "variant": "v20p4_full",
        "variant_name": "v20p4_full",
        "variant_canonical": "v20p4_full",
        "variant_stage": "A3",
        "variant_label": "v20.4 full RTA",
        "variant_group": "safe_chain",
        "formal_variant": True,
        "theory_family": "complete_window",
        "closure_method": "fixed_point_complete_window",
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
        "ablation_variant": "",
        "uses_certified_carry_in": True,
        "uses_processor_capacity_coupling": True,
        "uses_window_level_task_capacity": True,
        "uses_window_level_u_capacity": True,
        "uses_local_window": False,
        "uses_delta_closure": False,
        "uses_parallel_u_compression": False,
        "certificate_policy": "strict_variant_specific",
        "empty_state_guard": True,
        "empty_set_guard": False,
        "fallback_guard": True,
        "consistency_guard": True,
    },
    "v21_local_window_closure": {
        "variant": "v21_local_window_closure",
        "variant_name": "v21_local_window_closure",
        "variant_canonical": "v21_local_window_closure",
        "variant_stage": "A4",
        "variant_label": "A4 v21 local-window closure RTA",
        "variant_group": "local_window_refinement",
        "formal_variant": True,
        "theory_family": "local_window_closure",
        "closure_method": "delta_closure",
        "variant_safety_label": "safe_under_v21_local_window_assumptions",
        "variant_description": (
            "formal v21 local-window closure refinement RTA"
        ),
        "variant_is_default": False,
        "variant_is_experimental": False,
        "proof_claim_eligible": True,
        "diagnostic_only": False,
        "rta_tool": V21_TOOL,
        "expected_rta_version": V21_VERSION,
        "ablation_variant": "",
        "uses_certified_carry_in": True,
        "uses_processor_capacity_coupling": True,
        "uses_window_level_task_capacity": True,
        "uses_window_level_u_capacity": True,
        "uses_local_window": True,
        "uses_delta_closure": True,
        "uses_parallel_u_compression": False,
        "certificate_policy": "v21_recursive_certification",
        "empty_state_guard": True,
        "empty_set_guard": True,
        "fallback_guard": True,
        "consistency_guard": True,
    },
}

VARIANT_ALIASES = {
    "a0": "baseline_safe",
    "baseline_safe": "baseline_safe",
    "a1": "carry_in_certified",
    "carry_in_certified": "carry_in_certified",
    "a2": "capacity_coupled",
    "capacity_coupled": "capacity_coupled",
    "a3": "v20p4_full",
    "v20p4_full": "v20p4_full",
    "a4": "v21_local_window_closure",
    "v21_local_window_closure": "v21_local_window_closure",
    "v21_experimental": "v21_local_window_closure",
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
    "target_normalized_utilization",
    "target_total_utilization",
    "actual_total_utilization",
    "actual_normalized_utilization",
    "utilization_error_total",
    "task_util_min",
    "task_util_max",
    "wcet_rounding",
    "deadline_mode",
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
    "variant",
    "input_variant",
    "requested_variant",
    "variant_name",
    "variant_canonical",
    "variant_stage",
    "variant_group",
    "formal_variant",
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
    "schedulable",
    "response_time_bound",
    "rta_status",
    "rta_error",
    "failure_reason",
    "rta_proven",
    "rta_schedulable",
    "rta_response_time_bound",
    "rta_response_bound",
    "theory_family",
    "closure_method",
    "uses_certified_carry_in",
    "uses_processor_capacity_coupling",
    "uses_window_level_task_capacity",
    "uses_window_level_u_capacity",
    "uses_local_window",
    "uses_delta_closure",
    "uses_parallel_u_compression",
    "certificate_policy",
    "certified_carry_in_source",
    "certificate_status",
    "proof_claim_allowed",
    "proof_claim_succeeded",
    "fallback_used",
    "fallback_reason",
    "empty_state_guard",
    "empty_set_guard",
    "fallback_guard",
    "consistency_guard",
    "v21_delta_iterations",
    "v21_g_loc_calls",
    "v21_omega_feasibility_calls",
    "v21_empty_omega_count",
    "v21_no_closure_count",
    "v21_closed_prefix_count",
    "v21_delta_cap_exceeded_count",
    "v21_max_delta_cap",
    "v21_max_delta_seen",
    "v21_delta_jump_count",
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

PAYLOAD_METADATA_FIELDS = (
    "variant_label",
    "variant_group",
    "variant_safety_label",
    "variant_description",
    "variant_is_default",
    "variant_is_experimental",
    "formal_variant",
    "proof_claim_eligible",
    "diagnostic_only",
    "theory_family",
    "closure_method",
    "uses_certified_carry_in",
    "uses_processor_capacity_coupling",
    "uses_window_level_task_capacity",
    "uses_window_level_u_capacity",
    "uses_local_window",
    "uses_delta_closure",
    "uses_parallel_u_compression",
    "certificate_policy",
    "certificate_status",
    "certified_carry_in_source",
    "proof_claim_allowed",
    "proof_claim_succeeded",
    "fallback_used",
    "fallback_reason",
    "empty_state_guard",
    "empty_set_guard",
    "fallback_guard",
    "consistency_guard",
)

MANIFEST_PAYLOAD_OVERRIDE_FIELDS = (
    "variant_label",
    "variant_group",
    "variant_safety_label",
    "variant_description",
    "variant_is_default",
    "variant_is_experimental",
    "formal_variant",
    "proof_claim_eligible",
    "diagnostic_only",
)


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


def _canonical_variant_name(name: str) -> str:
    normalized = str(name).strip()
    if normalized.lower() == "all":
        return "all"
    try:
        return VARIANT_ALIASES[normalized.lower()]
    except KeyError:
        raise ValueError("unknown RTA ablation variant: {}".format(name))


def expand_variants(requested: Sequence[str]) -> List[Dict[str, str]]:
    expanded: List[Dict[str, str]] = []
    for item in requested:
        canonical = _canonical_variant_name(item)
        if canonical == "all":
            expanded.extend(
                {"requested_variant": variant, "canonical": variant}
                for variant in ALL_VARIANT_ORDER
            )
        else:
            expanded.append({"requested_variant": item, "canonical": canonical})
    seen = set()
    result = []
    for entry in expanded:
        canonical = entry["canonical"]
        if canonical in seen:
            raise ValueError("variant list contains duplicate canonical variant: {}".format(canonical))
        seen.add(canonical)
        result.append(entry)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run RTA-only E3 ablation/refinement samples for formal A0-A4 "
            "variants. It does not run scheduler simulation, observed "
            "pessimism, or acceptance ratios."
        )
    )
    parser.add_argument("--output-root", default="results/rta_ablation")
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument(
        "--variants",
        type=_comma_variants,
        default=["v20p4_full"],
        help=(
            "comma-separated variants or aliases: A0,A1,A2,A3,A4, all, "
            "or canonical names; legacy v21_experimental maps to A4"
        ),
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
    parser.add_argument("--min-task-util", type=float, default=0.01)
    parser.add_argument("--max-task-util", type=float, default=0.8)
    parser.add_argument(
        "--wcet-rounding",
        choices=("floor", "round", "ceil"),
        default="floor",
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
        help="parallel RTA subprocesses; keep 1 for paper runtime results",
    )
    parser.add_argument("--rta-assume-no-overflow", action="store_true")
    parser.add_argument(
        "--profile-rta",
        action="store_true",
        help=(
            "request internal RTA profiling where the endpoint supports it; "
            "A0-A2 and A4 expose task-level profiles"
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
    if args.min_task_util < 0 or args.max_task_util <= 0:
        parser.error("--min-task-util/--max-task-util must be positive bounds")
    if args.min_task_util > args.max_task_util:
        parser.error("--min-task-util must be <= --max-task-util")
    if args.max_task_util > 1.0:
        parser.error("--max-task-util must be <= 1.0 for sequential tasks")

    try:
        args.variant_requests = expand_variants(args.variants)
    except ValueError as exc:
        parser.error(str(exc))


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
                "target_normalized_utilization": float(normalized_utilization),
                "target_total_utilization": total_utilization,
                "actual_total_utilization": "",
                "actual_normalized_utilization": "",
                "utilization_error_total": "",
                "task_util_min": args.min_task_util,
                "task_util_max": args.max_task_util,
                "wcet_rounding": args.wcet_rounding,
                "deadline_mode": (
                    "constrained" if args.constrained_deadlines else "implicit"
                ),
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
            variant_requests = getattr(
                args, "variant_requests", expand_variants(args.variants)
            )
            for variant_request in variant_requests:
                requested_variant = variant_request["requested_variant"]
                canonical = variant_request["canonical"]
                variant = VARIANT_REGISTRY[canonical]
                variant_fields = {field: variant[field] for field in VARIANT_FIELDS if field in variant}
                variant_fields.update({
                    "input_variant": requested_variant,
                    "requested_variant": requested_variant,
                })
                spec = {
                    "_index": index,
                    **base,
                    **variant,
                    **variant_fields,
                    "_rta_tool": variant["rta_tool"],
                    "_expected_rta_version": variant["expected_rta_version"],
                    "_ablation_variant": variant.get("ablation_variant", ""),
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
        "--min-task-util", str(spec["task_util_min"]),
        "--max-task-util", str(spec["task_util_max"]),
        "--wcet-rounding", str(spec["wcet_rounding"]),
    ]
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _base_rta_result(expected_version: str, status: str = "rta_error") -> Dict[str, Any]:
    result = {
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
        "failure_reason": "",
        "certificate_status": "",
        "proof_claim_allowed": False,
        "proof_claim_succeeded": False,
        "fallback_used": False,
        "fallback_reason": "",
    }
    for name in (
        "v21_delta_iterations",
        "v21_g_loc_calls",
        "v21_omega_feasibility_calls",
        "v21_empty_omega_count",
        "v21_no_closure_count",
        "v21_closed_prefix_count",
        "v21_delta_cap_exceeded_count",
        "v21_max_delta_cap",
        "v21_max_delta_seen",
        "v21_delta_jump_count",
    ):
        result[name] = 0
    return result


def _blank_rta_result(spec: Mapping[str, Any], error: str) -> Dict[str, Any]:
    result = _base_rta_result(spec["_expected_rta_version"], status="rta_error")
    result["rta_error"] = error
    return result


def _finite_number(value: Any):
    number = acceptance._extract_number(value)
    if number is None or not math.isfinite(number):
        return None
    return float(number)


def _profile_metrics(tasks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    total_time = 0.0
    profile_count = 0
    v21_sums = {
        "v21_delta_iterations": 0,
        "v21_g_loc_calls": 0,
        "v21_omega_feasibility_calls": 0,
        "v21_empty_omega_count": 0,
        "v21_no_closure_count": 0,
        "v21_closed_prefix_count": 0,
        "v21_delta_cap_exceeded_count": 0,
        "v21_delta_jump_count": 0,
    }
    v21_max = {
        "v21_max_delta_cap": 0,
        "v21_max_delta_seen": 0,
    }
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        profile = task.get("rta_profile")
        if not isinstance(profile, Mapping):
            continue
        profile_count += 1
        task_time = _finite_number(profile.get("total_time_sec"))
        if task_time is not None and task_time >= 0:
            total_time += task_time
        for field in v21_sums:
            source = field[4:]
            value = _finite_number(profile.get(source))
            if value is not None and value >= 0:
                v21_sums[field] += int(value)
        for field in v21_max:
            source = field[4:]
            value = _finite_number(profile.get(source))
            if value is not None and value >= 0:
                v21_max[field] = max(v21_max[field], int(value))
    return {
        "rta_profile_enabled": bool(profile_count),
        "rta_profile_task_time_sum_sec": total_time if profile_count else None,
        "rta_profile_task_count": profile_count,
        **v21_sums,
        **v21_max,
    }


def _first_failure_reason(tasks: Sequence[Mapping[str, Any]]) -> str:
    for task in tasks:
        if isinstance(task, Mapping) and task.get("failure_reason"):
            return str(task.get("failure_reason"))
    return ""


def _copy_payload_metadata(
    payload: Mapping[str, Any], result: Dict[str, Any]
) -> None:
    for field in PAYLOAD_METADATA_FIELDS:
        if field in payload and payload[field] is not None:
            result[field] = payload[field]


def _metadata_value(
    rta_result: Mapping[str, Any],
    spec: Mapping[str, Any],
    field: str,
    default: Any = "",
) -> Any:
    if field in rta_result and rta_result[field] is not None:
        return rta_result[field]
    if field in spec and spec[field] is not None:
        return spec[field]
    return default


def _metadata_bool(
    rta_result: Mapping[str, Any],
    spec: Mapping[str, Any],
    field: str,
    default: bool = False,
) -> bool:
    value = _metadata_value(rta_result, spec, field, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


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
    metrics = _profile_metrics(tasks)
    result = {
        "rta_version": expected_version,
        "rta_status": (
            "proven_under_assumptions" if proven else "rta_unproven"
        ),
        "rta_error": None,
        "rta_proven_under_assumptions": proven,
        "rta_bound": max(bounds) if bounds else None,
        "rta_report": dict(payload),
        "failure_reason": payload.get("failure_reason") or _first_failure_reason(tasks),
        **metrics,
    }
    _copy_payload_metadata(payload, result)
    return result


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


def _run_ablation_module_rta(spec: Mapping[str, Any]) -> Dict[str, Any]:
    result = _base_rta_result(spec["_expected_rta_version"], status="rta_error")
    command = [
        sys.executable,
        str(PROJECT_ROOT / ABLATION_TOOL),
        "--config", str(spec["config_file"]),
        "--tasks", str(spec["task_file"]),
        "--variant", str(spec["_ablation_variant"]),
        "--horizon-ms", str(spec["rta_horizon_ms"]),
        "--rta-initial-energy", str(spec["rta_initial_energy"]),
    ]
    if spec["rta_assume_no_overflow"]:
        command.append("--assume-no-overflow")
    if spec["profile_rta"]:
        command.append("--profile-rta")

    result.update({
        "rta_attempted": True,
        "rta_runtime_source": "subprocess_wall_clock_perf_counter",
        "rta_timeout_sec": spec["rta_timeout_sec"],
        "rta_profile_enabled": bool(spec["profile_rta"]),
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
        error = (completed.stderr or completed.stdout or "ablation RTA failed").strip()
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
    command.append("--profile-rta")
    command.append("--json")

    result.update({
        "rta_attempted": True,
        "rta_runtime_source": "subprocess_wall_clock_perf_counter",
        "rta_timeout_sec": spec["rta_timeout_sec"],
        "rta_profile_enabled": True,
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
    if spec["variant_name"] in {
        "baseline_safe",
        "carry_in_certified",
        "capacity_coupled",
    }:
        return _run_ablation_module_rta(spec)
    if spec["variant_name"] == "v20p4_full":
        return _run_v20_rta(spec)
    if spec["variant_name"] == "v21_local_window_closure":
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
    certificate_status = (
        rta_result.get("certificate_status")
        or ("available" if proven else "")
    )
    proof_eligible = _metadata_bool(
        rta_result, spec, "proof_claim_eligible", False
    )
    proof_allowed = _metadata_bool(
        rta_result, spec, "proof_claim_allowed", proof_eligible
    )
    proof_succeeded = _metadata_bool(
        rta_result, spec, "proof_claim_succeeded", proven and proof_allowed
    )

    row = {field: spec.get(field, "") for field in MANIFEST_FIELDS}
    for field in MANIFEST_PAYLOAD_OVERRIDE_FIELDS:
        row[field] = _metadata_value(rta_result, spec, field, row.get(field, ""))
    row.update({
        "rta_version": rta_result.get("rta_version", ""),
        "rta_tool": spec["_rta_tool"],
        "expected_rta_version": spec["_expected_rta_version"],
        "schedulable": proven,
        "response_time_bound": "" if bound is None else bound,
        "rta_status": status,
        "rta_error": rta_result.get("rta_error") or "",
        "failure_reason": (
            rta_result.get("failure_reason")
            or rta_result.get("rta_error")
            or ""
        ),
        "rta_proven": proven,
        "rta_schedulable": proven,
        "rta_response_time_bound": "" if bound is None else bound,
        "rta_response_bound": "" if bound is None else bound,
        "theory_family": _metadata_value(rta_result, spec, "theory_family"),
        "closure_method": _metadata_value(rta_result, spec, "closure_method"),
        "uses_certified_carry_in": _metadata_bool(
            rta_result, spec, "uses_certified_carry_in"
        ),
        "uses_processor_capacity_coupling": _metadata_bool(
            rta_result, spec, "uses_processor_capacity_coupling"
        ),
        "uses_window_level_task_capacity": _metadata_bool(
            rta_result, spec, "uses_window_level_task_capacity"
        ),
        "uses_window_level_u_capacity": _metadata_bool(
            rta_result, spec, "uses_window_level_u_capacity"
        ),
        "uses_local_window": _metadata_bool(rta_result, spec, "uses_local_window"),
        "uses_delta_closure": _metadata_bool(
            rta_result, spec, "uses_delta_closure"
        ),
        "uses_parallel_u_compression": _metadata_bool(
            rta_result, spec, "uses_parallel_u_compression"
        ),
        "certificate_policy": _metadata_value(
            rta_result, spec, "certificate_policy"
        ),
        "certified_carry_in_source": _metadata_value(
            rta_result, spec, "certified_carry_in_source"
        ),
        "certificate_status": certificate_status,
        "proof_claim_allowed": proof_allowed,
        "proof_claim_succeeded": proof_succeeded,
        "fallback_used": bool(rta_result.get("fallback_used", False)),
        "fallback_reason": rta_result.get("fallback_reason", ""),
        "empty_state_guard": _metadata_bool(
            rta_result, spec, "empty_state_guard"
        ),
        "empty_set_guard": _metadata_bool(rta_result, spec, "empty_set_guard"),
        "fallback_guard": _metadata_bool(rta_result, spec, "fallback_guard"),
        "consistency_guard": _metadata_bool(
            rta_result, spec, "consistency_guard"
        ),
        "v21_delta_iterations": int(rta_result.get("v21_delta_iterations", 0)),
        "v21_g_loc_calls": int(rta_result.get("v21_g_loc_calls", 0)),
        "v21_omega_feasibility_calls": int(
            rta_result.get("v21_omega_feasibility_calls", 0)
        ),
        "v21_empty_omega_count": int(rta_result.get("v21_empty_omega_count", 0)),
        "v21_no_closure_count": int(rta_result.get("v21_no_closure_count", 0)),
        "v21_closed_prefix_count": int(
            rta_result.get("v21_closed_prefix_count", 0)
        ),
        "v21_delta_cap_exceeded_count": int(
            rta_result.get("v21_delta_cap_exceeded_count", 0)
        ),
        "v21_max_delta_cap": int(rta_result.get("v21_max_delta_cap", 0)),
        "v21_max_delta_seen": int(rta_result.get("v21_max_delta_seen", 0)),
        "v21_delta_jump_count": int(rta_result.get("v21_delta_jump_count", 0)),
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
        utilization_metadata = acceptance.load_taskset_utilization_metadata(
            representative["task_file"],
            target_normalized_utilization=representative[
                "target_normalized_utilization"
            ],
            target_total_utilization=representative[
                "target_total_utilization"
            ],
            num_cores=representative["M"],
            task_util_min=representative["task_util_min"],
            task_util_max=representative["task_util_max"],
            wcet_rounding=representative["wcet_rounding"],
            deadline_mode=representative["deadline_mode"],
        )
        manifest_utilization_metadata = {
            field: value
            for field, value in utilization_metadata.items()
            if field in MANIFEST_FIELDS
        }
        for spec in family_specs:
            spec.update(utilization_metadata)
            spec["task_file_sha256"] = task_hash
            spec["status"] = "task_ready"
            manifest_rows[spec["_index"]].update(
                **manifest_utilization_metadata,
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
