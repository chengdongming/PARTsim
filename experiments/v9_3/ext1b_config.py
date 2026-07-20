"""Strict configuration contract for the EXT-1B mechanism-stress experiment."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

from .config import (
    ConfigError,
    canonical_json,
    domain_hash,
    exact_fraction,
    fraction_text,
    validate_config,
)
from .result_writer import atomic_write_text
from .scheduler_registry import SCHEDULER_IDS


PARAMETER_STATUSES = {
    "SMOKE",
    "PILOT",
    "FORMAL",
}
SCENARIO_KINDS = {"BYPASS_STRESS", "SYNC_BATCH_STRESS", "TIMING_STRESS"}
TIMING_SUBTYPES = {
    "POSITIVE_SLACK_ENERGY_AVAILABLE",
    "SLACK_LIMITED_CHARGING",
}
SEED_SPACES = {
    "EXT1B_SMOKE",
    "EXT1B_PILOT",
    "EXT1B1_ENERGY_CALIBRATION_PILOT",
    "EXT1B2_SYNC_CALIBRATION_PILOT",
    "EXT1B3_TIMING_CALIBRATION_PILOT",
    "EXT1B1_FORMAL_R1",
}

B2_SCHEDULER_IDS = (
    "gpfp_asap_block",
    "gpfp_asap_sync",
)
B3_PRIMARY_SCHEDULER_IDS = (
    "gpfp_asap_block",
    "gpfp_alap_block",
    "gpfp_st_block",
)
B1_REQUIRED_OUTPUTS = (
    "b1_bypass_episodes.csv",
    "b1_task_effects.csv",
    "b1_paired_effects.csv",
    "b1_summary.csv",
)
B2_REQUIRED_OUTPUTS = (
    "b2_batch_decisions.csv",
    "b2_summary.csv",
)
B3_REQUIRED_OUTPUTS = (
    "b3_timing_events.csv",
    "b3_summary.csv",
)

TOP_LEVEL_KEYS = {
    "experiment_id", "core", "extension", "parameter_status", "seed_space",
    "platform", "generation", "energy", "grid", "rta", "simulation",
    "execution", "scenario", "statistics", "plots", "scheduler_ids",
    "required_outputs",
}
PLATFORM_KEYS = {"cores", "task_count"}
GENERATION_KEYS = {
    "deadline_mode", "constrained_deadline", "period_min", "period_max",
    "wcet_rounding", "utilization_tolerance", "min_task_util",
    "max_task_util", "priority_policy", "power_mode",
    "generator_timeout_seconds",
}
CONSTRAINED_KEYS = {
    "d_over_t_values", "d_over_t_min", "d_over_t_max", "distribution",
}
ENERGY_KEYS = {
    "initial_energy_values", "simulation_initial_battery",
    "exact_rational_encoding", "service_curve", "battery_mode",
    "battery_capacity",
}
SERVICE_KEYS = {"id", "system_template", "horizon"}
GRID_KEYS = {
    "utilization_points", "tasksets_per_cell", "base_seed", "seed_mode",
    "taskset_index_start",
}
RTA_KEYS = {
    "methods", "timeout_seconds", "retry_timeout_seconds", "retry_policy",
    "numerical_mode",
}
SIMULATION_KEYS = {
    "horizon", "warmup", "minimum_jobs_per_task", "maximum_horizon",
    "horizon_extension_policy", "deadline_miss_fail_fast", "timeout_seconds",
    "trace_mode", "trace_on_failure", "retain_trace", "simulator_bin",
}
EXECUTION_KEYS = {
    "worker_count", "checkpoint_every", "output_root", "taskset_store",
    "resume", "fail_fast_on_p0", "preserve_attempt_history",
}
SCENARIO_KEYS = {
    "kind", "subtype", "structural_retry_limit", "activation_policy",
    "priority_power_profile", "affordable_prefix_length",
    "deadline_ratio_min", "deadline_ratio_max",
    "nominal_energy_supply_ratios", "initial_energy_policy",
    "release_pattern", "harvest_phase_policy", "interpolation_rho",
    "timing_cells",
}
TIMING_CELL_KEYS = {
    "id", "subtype", "deadline_ratio_min", "deadline_ratio_max",
    "nominal_energy_supply_ratio", "initial_energy_policy",
}
STATISTICS_KEYS = {"bootstrap_seed", "bootstrap_resamples", "top_m"}


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"unknown {label} fields: {sorted(unknown)}")


def _mapping(parent: Mapping[str, Any], key: str) -> Dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{label} must be a positive integer")
    return value


def _ratio(value: Any, label: str, *, allow_zero: bool = False) -> str:
    result = exact_fraction(value, label)
    lower_ok = result >= 0 if allow_zero else result > 0
    if not lower_ok or result > 1:
        boundary = "0 <= value <= 1" if allow_zero else "0 < value <= 1"
        raise ConfigError(f"{label} must satisfy {boundary}")
    return fraction_text(result)


def _strict_common_shape(raw: Mapping[str, Any]) -> None:
    _reject_unknown(raw, TOP_LEVEL_KEYS, "top-level")
    nested = (
        ("platform", PLATFORM_KEYS),
        ("generation", GENERATION_KEYS),
        ("energy", ENERGY_KEYS),
        ("grid", GRID_KEYS),
        ("rta", RTA_KEYS),
        ("simulation", SIMULATION_KEYS),
        ("execution", EXECUTION_KEYS),
        ("scenario", SCENARIO_KEYS),
        ("statistics", STATISTICS_KEYS),
    )
    for label, allowed in nested:
        _reject_unknown(_mapping(raw, label), allowed, label)
    _reject_unknown(
        _mapping(_mapping(raw, "generation"), "constrained_deadline"),
        CONSTRAINED_KEYS,
        "generation.constrained_deadline",
    )
    _reject_unknown(
        _mapping(_mapping(raw, "energy"), "service_curve"),
        SERVICE_KEYS,
        "energy.service_curve",
    )
    plots = _mapping(raw, "plots")
    if plots:
        raise ConfigError("EXT-1B plots must be an empty mapping; analyzer owns plot data")


def _validate_timing_cells(scenario: Dict[str, Any]) -> None:
    raw_cells = scenario.get("timing_cells")
    if not isinstance(raw_cells, list) or len(raw_cells) < 2:
        raise ConfigError("TIMING_STRESS requires at least two timing_cells")
    normalized = []
    seen = set()
    seen_subtypes = set()
    for index, raw in enumerate(raw_cells):
        if not isinstance(raw, dict):
            raise ConfigError(f"scenario.timing_cells[{index}] must be a mapping")
        _reject_unknown(raw, TIMING_CELL_KEYS, f"scenario.timing_cells[{index}]")
        if set(raw) != TIMING_CELL_KEYS:
            missing = sorted(TIMING_CELL_KEYS - set(raw))
            raise ConfigError(f"scenario.timing_cells[{index}] missing fields: {missing}")
        cell_id = raw["id"]
        if not isinstance(cell_id, str) or not cell_id or cell_id in seen:
            raise ConfigError("timing cell IDs must be non-empty and unique")
        seen.add(cell_id)
        subtype = raw["subtype"]
        if subtype not in TIMING_SUBTYPES:
            raise ConfigError("unknown timing subtype")
        seen_subtypes.add(subtype)
        lower = _ratio(raw["deadline_ratio_min"], f"timing_cells[{index}].deadline_ratio_min")
        upper = _ratio(raw["deadline_ratio_max"], f"timing_cells[{index}].deadline_ratio_max")
        if Fraction(lower) > Fraction(upper):
            raise ConfigError("timing-cell deadline ratio min must not exceed max")
        eta = exact_fraction(
            raw["nominal_energy_supply_ratio"],
            f"timing_cells[{index}].nominal_energy_supply_ratio",
        )
        if subtype == "SLACK_LIMITED_CHARGING" and eta <= 0:
            raise ConfigError("SLACK_LIMITED_CHARGING requires positive nominal supply")
        policy = raw["initial_energy_policy"]
        expected = (
            "TOP_M_AFFORDABLE"
            if subtype == "POSITIVE_SLACK_ENERGY_AVAILABLE"
            else "HALF_TARGET"
        )
        if policy != expected:
            raise ConfigError(f"{subtype} requires initial_energy_policy: {expected}")
        normalized.append({
            "id": cell_id,
            "subtype": subtype,
            "deadline_ratio_min": lower,
            "deadline_ratio_max": upper,
            "nominal_energy_supply_ratio": fraction_text(eta),
            "initial_energy_policy": policy,
        })
    if seen_subtypes != TIMING_SUBTYPES:
        raise ConfigError("TIMING_STRESS must include both required subtypes")
    scenario["timing_cells"] = normalized


def validate_ext1b_config(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize an EXT-1B config and reject every unknown field."""

    if not isinstance(raw, Mapping):
        raise ConfigError("configuration must be a YAML mapping")
    _strict_common_shape(raw)
    config = deepcopy(dict(raw))
    if config.get("core") != "CORE-3" or config.get("extension") != "EXT-1B":
        raise ConfigError("EXT-1B runner requires core: CORE-3 and extension: EXT-1B")
    status = config.get("parameter_status")
    if status not in PARAMETER_STATUSES:
        raise ConfigError("unknown EXT-1B parameter_status")
    seed_space = config.get("seed_space")
    allowed_seed_spaces = {
        "SMOKE": {"EXT1B_SMOKE"},
        "PILOT": {
            "EXT1B_PILOT",
            "EXT1B1_ENERGY_CALIBRATION_PILOT",
            "EXT1B2_SYNC_CALIBRATION_PILOT",
            "EXT1B3_TIMING_CALIBRATION_PILOT",
        },
        "FORMAL": {"EXT1B1_FORMAL_R1"},
    }[status]
    if seed_space not in allowed_seed_spaces:
        raise ConfigError(
            f"{status} requires seed_space in {sorted(allowed_seed_spaces)}"
        )

    scenario = _mapping(config, "scenario")
    kind = scenario.get("kind")
    if kind not in SCENARIO_KINDS:
        raise ConfigError("unknown EXT-1B scenario kind")
    scheduler_ids = config.get("scheduler_ids", list(SCHEDULER_IDS))
    if not isinstance(scheduler_ids, list) or not scheduler_ids:
        raise ConfigError("EXT-1B scheduler_ids must be a non-empty list")
    if any(not isinstance(item, str) or not item for item in scheduler_ids):
        raise ConfigError("EXT-1B scheduler_ids must contain non-empty strings")
    if len(scheduler_ids) != len(set(scheduler_ids)):
        raise ConfigError("EXT-1B scheduler_ids contains duplicates")
    unknown_schedulers = sorted(set(scheduler_ids) - set(SCHEDULER_IDS))
    if unknown_schedulers:
        raise ConfigError(
            f"EXT-1B scheduler_ids contains unknown schedulers: {unknown_schedulers}"
        )
    if kind == "SYNC_BATCH_STRESS" and not set(B2_SCHEDULER_IDS).issubset(
        scheduler_ids
    ):
        raise ConfigError(
            "SYNC_BATCH_STRESS scheduler_ids must include "
            "gpfp_asap_block and gpfp_asap_sync"
        )
    if (
        seed_space == "EXT1B2_SYNC_CALIBRATION_PILOT"
        and tuple(scheduler_ids) != B2_SCHEDULER_IDS
    ):
        raise ConfigError(
            "EXT1B2_SYNC_CALIBRATION_PILOT scheduler_ids must equal "
            "['gpfp_asap_block', 'gpfp_asap_sync'] in that order"
        )
    if (
        seed_space == "EXT1B3_TIMING_CALIBRATION_PILOT"
        and tuple(scheduler_ids) != B3_PRIMARY_SCHEDULER_IDS
    ):
        raise ConfigError(
            "EXT1B3_TIMING_CALIBRATION_PILOT scheduler_ids must equal "
            "['gpfp_asap_block', 'gpfp_alap_block', 'gpfp_st_block'] "
            "in that order"
        )
    config["scheduler_ids"] = list(scheduler_ids)
    expected_outputs = {
        "BYPASS_STRESS": list(B1_REQUIRED_OUTPUTS),
        "SYNC_BATCH_STRESS": list(B2_REQUIRED_OUTPUTS),
        "TIMING_STRESS": list(B3_REQUIRED_OUTPUTS),
    }[kind]
    required_outputs = config.get("required_outputs", expected_outputs)
    if required_outputs != expected_outputs:
        raise ConfigError(
            f"{kind} required_outputs must equal {expected_outputs}"
        )
    config["required_outputs"] = list(required_outputs)
    scenario["structural_retry_limit"] = _positive_int(
        scenario.get("structural_retry_limit"), "scenario.structural_retry_limit"
    )
    if scenario.get("activation_policy") != "REPORT_STRUCTURAL_AND_RUNTIME":
        raise ConfigError("unknown scenario.activation_policy")
    if scenario.get("priority_power_profile") not in {
        "HIGH_PRIORITY_HIGH_POWER", "ACTUAL_GENERATOR_ORDER",
    }:
        raise ConfigError("unknown scenario.priority_power_profile")
    scenario["affordable_prefix_length"] = _positive_int(
        scenario.get("affordable_prefix_length"),
        "scenario.affordable_prefix_length",
    )
    lower = _ratio(
        scenario.get("deadline_ratio_min"), "scenario.deadline_ratio_min",
        allow_zero=True,
    )
    upper = _ratio(
        scenario.get("deadline_ratio_max"), "scenario.deadline_ratio_max"
    )
    if Fraction(lower) > Fraction(upper):
        raise ConfigError("scenario deadline ratio min must not exceed max")
    scenario["deadline_ratio_min"] = lower
    scenario["deadline_ratio_max"] = upper
    ratios = scenario.get("nominal_energy_supply_ratios")
    if not isinstance(ratios, list) or not ratios:
        raise ConfigError("scenario.nominal_energy_supply_ratios must be non-empty")
    scenario["nominal_energy_supply_ratios"] = [
        fraction_text(exact_fraction(value, f"nominal_energy_supply_ratios[{index}]"))
        for index, value in enumerate(ratios)
    ]
    if scenario.get("initial_energy_policy") not in {
        "STRUCTURAL_MIDPOINT", "TOP_M_AFFORDABLE", "HALF_TARGET",
    }:
        raise ConfigError("unknown scenario.initial_energy_policy")
    if scenario.get("release_pattern") != "SYNCHRONOUS":
        raise ConfigError("EXT-1B currently requires synchronous releases")
    if scenario.get("harvest_phase_policy") != "PEAK_SYNTHETIC":
        raise ConfigError("EXT-1B currently requires PEAK_SYNTHETIC harvesting")
    rho = exact_fraction(scenario.get("interpolation_rho"), "scenario.interpolation_rho")
    if not 0 < rho < 1:
        raise ConfigError("scenario.interpolation_rho must satisfy 0 < rho < 1")
    scenario["interpolation_rho"] = fraction_text(rho)

    if kind == "TIMING_STRESS":
        if scenario.get("subtype") != "MULTI_CELL":
            raise ConfigError("TIMING_STRESS requires subtype: MULTI_CELL")
        _validate_timing_cells(scenario)
    else:
        expected = "B1" if kind == "BYPASS_STRESS" else "B2"
        if scenario.get("subtype") != expected:
            raise ConfigError(f"{kind} requires subtype: {expected}")
        if scenario.get("timing_cells") != []:
            raise ConfigError("non-timing scenarios require timing_cells: []")

    stats = _mapping(config, "statistics")
    stats["bootstrap_seed"] = _positive_int(
        stats.get("bootstrap_seed"), "statistics.bootstrap_seed"
    )
    stats["bootstrap_resamples"] = _positive_int(
        stats.get("bootstrap_resamples"), "statistics.bootstrap_resamples"
    )
    stats["top_m"] = _positive_int(stats.get("top_m"), "statistics.top_m")
    retain_trace = config["simulation"].get("retain_trace")
    if not isinstance(retain_trace, bool):
        raise ConfigError("simulation.retain_trace must be a boolean")
    if kind == "BYPASS_STRESS" and not retain_trace:
        raise ConfigError("BYPASS_STRESS requires retained semantic traces")
    if kind == "TIMING_STRESS" and not retain_trace:
        raise ConfigError("TIMING_STRESS requires retained semantic traces")
    if config["parameter_status"] == "SMOKE" and config["grid"]["tasksets_per_cell"] > 2:
        raise ConfigError("EXT-1B smoke tasksets_per_cell must not exceed 2")

    # Reuse the frozen v9.3 validation for the common execution contract.
    normalized = validate_config(config, expected_core="CORE-3")
    normalized["scenario"] = scenario
    normalized["statistics"] = stats
    if status == "FORMAL":
        expected_schedulers = [
            "gpfp_asap_block",
            "gpfp_asap_nonblock",
        ]
        if kind != "BYPASS_STRESS" or scenario.get("subtype") != "B1":
            raise ConfigError("EXT-1B FORMAL currently requires BYPASS_STRESS subtype B1")
        if normalized["scheduler_ids"] != expected_schedulers:
            raise ConfigError(
                f"EXT-1B1 FORMAL scheduler_ids must equal {expected_schedulers}"
            )
        if normalized["grid"]["utilization_points"] != ["1/5", "2/5"]:
            raise ConfigError(
                "EXT-1B1 FORMAL utilization_points must equal ['1/5', '2/5']"
            )
        if scenario["nominal_energy_supply_ratios"] != ["1/4", "1/2", "3/4"]:
            raise ConfigError(
                "EXT-1B1 FORMAL nominal_energy_supply_ratios must equal "
                "['1/4', '1/2', '3/4']"
            )
        if normalized["grid"]["tasksets_per_cell"] != 200:
            raise ConfigError("EXT-1B1 FORMAL tasksets_per_cell must equal 200")
        if normalized["grid"]["base_seed"] != 951201:
            raise ConfigError("EXT-1B1 FORMAL base_seed must equal 951201")
        if normalized["execution"]["worker_count"] != 1:
            raise ConfigError("EXT-1B1 FORMAL worker_count must equal 1")
        simulation = normalized["simulation"]
        if simulation["horizon"] != 400 or simulation["maximum_horizon"] != 400:
            raise ConfigError(
                "EXT-1B1 FORMAL horizon and maximum_horizon must both equal 400"
            )
    return normalized


def load_ext1b_config(path: Path | str) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a YAML mapping")
    return validate_ext1b_config(raw)


def ext1b_config_hash(config: Mapping[str, Any]) -> str:
    semantic = deepcopy(dict(config))
    semantic.get("execution", {}).pop("resume", None)
    return domain_hash("ASAP_BLOCK:V9.3:EXT1B:CONFIG:v1", semantic)


def dump_ext1b_config(config: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``analysis`` is a deterministic compatibility projection synthesized by
    # the common CORE-3 validator from ``rta``; it is not part of the public
    # EXT-1B YAML schema and is recreated when the persisted config is loaded.
    public = deepcopy(dict(config))
    public.pop("analysis", None)
    canonical = yaml.safe_load(canonical_json(public))
    atomic_write_text(
        path,
        yaml.safe_dump(canonical, allow_unicode=True, sort_keys=False),
    )
