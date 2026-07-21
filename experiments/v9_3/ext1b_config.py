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
from .ext1b_capacity_contract import (
    B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION,
)
from .ext1b_b3_target_trace import (
    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
)
from .result_writer import atomic_write_text
from .scheduler_registry import SCHEDULER_IDS


PARAMETER_STATUSES = {
    "SMOKE",
    "PILOT",
    "CALIBRATION",
    "FORMAL",
}
SCENARIO_KINDS = {"BYPASS_STRESS", "SYNC_BATCH_STRESS", "TIMING_STRESS"}
TIMING_SUBTYPES = {
    "POSITIVE_SLACK_ENERGY_AVAILABLE",
    "SLACK_LIMITED_CHARGING",
}
SEED_SPACES = {
    "EXT1B_SMOKE",
    "EXT1B_PILOT_WORKLOAD_CONTRACT_V2",
    "EXT1B1_ENERGY_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
    "EXT1B2_SYNC_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
    "EXT1B3_TIMING_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
    "EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3",
    "EXT1B1_FORMAL_R1_WORKLOAD_CONTRACT_V2",
    "EXT1B2_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2",
    (
        "EXT1B3_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2_"
        "CAPACITY_CONTRACT_V1"
    ),
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
B3_V2_REQUIRED_OUTPUTS = (
    *B3_REQUIRED_OUTPUTS,
    "b3_calibration_summary.csv",
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
    "generator_timeout_seconds", "workload_candidates", "workload_contract",
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
    "timing_cells", "capacity_feasibility_contract", "scenario_contract_id",
    "calibration_grid",
}
TIMING_CELL_KEYS = {
    "id", "subtype", "deadline_ratio_min", "deadline_ratio_max",
    "nominal_energy_supply_ratio", "initial_energy_policy",
}
STATISTICS_KEYS = {"bootstrap_seed", "bootstrap_resamples", "top_m"}
CALIBRATION_GRID_KEYS = {
    "recovery_margin_ticks",
    "interpolation_rhos",
    "nominal_energy_supply_ratios",
}


def _formal_common_frozen() -> Dict[str, Any]:
    """Return the shared normalized, result-independent formal contract."""

    return {
        "platform": {"cores": [4], "task_count": [10]},
        "generation": {
            "deadline_mode": "constrained",
            "constrained_deadline": {
                "d_over_t_values": [],
                "d_over_t_min": "0",
                "d_over_t_max": "1",
                "distribution": "generator_uniform_integer",
            },
            "period_min": 40,
            "period_max": 200,
            "wcet_rounding": "compensated",
            "utilization_tolerance": "1/100",
            "min_task_util": "1/100",
            "max_task_util": "4/5",
            "priority_policy": "RM",
            "power_mode": "generator_default_heterogeneous",
            "generator_timeout_seconds": 120,
            "workload_candidates": [
                "bzip2", "control", "decrypt", "encrypt", "hash",
            ],
            "workload_contract": {
                "version": "REAL_TIME_TASK_WORKLOAD_CONTRACT_V2",
                "idle_system_state_reserved": True,
                "ordered_candidates": [
                    "bzip2", "control", "decrypt", "encrypt", "hash",
                ],
                "candidate_identity": (
                    "482ea49bc149a1a8563ed0cd9bc2be0506d44026fd01f1b65316743b7c12fa75"
                ),
                "power_model": [
                    {"workload": "bzip2", "energy_per_tick": "279/500000"},
                    {
                        "workload": "control",
                        "energy_per_tick": (
                            "9300000000000001/200000000000000000000"
                        ),
                    },
                    {"workload": "decrypt", "energy_per_tick": "279/400000"},
                    {"workload": "encrypt", "energy_per_tick": "279/400000"},
                    {
                        "workload": "hash",
                        "energy_per_tick": (
                            "9300000000000001/25000000000000000000"
                        ),
                    },
                ],
                "power_model_identity": (
                    "5624a208979f3bd453aec86a4b85800f26d7c04e30a9c1597abefd82a2a63f59"
                ),
                "contract_identity": (
                    "ab37bb9ecb2b7d45ed4d3106dae65f3abb549c2e5b4932a56c6d7d52733c71a9"
                ),
            },
        },
        "energy": {
            "initial_energy_values": ["0"],
            "simulation_initial_battery": "1",
            "exact_rational_encoding": "canonical_fraction",
            "service_curve": {
                "id": "ext1b-generation-service",
                "system_template": "system_config_unified_template.yml",
                "horizon": 1000,
            },
            "battery_mode": "finite",
            "battery_capacity": "100",
        },
        "rta": {
            "methods": ["CW_THETA_CW", "LOC_THETA_LOC"],
            "timeout_seconds": 2,
            "retry_timeout_seconds": 4,
            "retry_policy": "timeout_once",
            "numerical_mode": "EXACT_RATIONAL",
        },
        "simulation": {
            "horizon": 400,
            "warmup": 0,
            "minimum_jobs_per_task": 2,
            "maximum_horizon": 400,
            "horizon_extension_policy": "none",
            "deadline_miss_fail_fast": True,
            "timeout_seconds": 30,
            "trace_mode": "semantic",
            "trace_on_failure": True,
            "retain_trace": True,
            "simulator_bin": "./build/rtsim/rtsim",
        },
        "plots": {},
    }


def _formal_profile(
    profile_id: str,
    *,
    experiment_id: str,
    scheduler_ids: tuple[str, ...],
    required_outputs: tuple[str, ...],
    base_seed: int,
    output_root: str,
    taskset_store: str,
    bootstrap_seed: int,
    scenario: Mapping[str, Any],
) -> Dict[str, Any]:
    frozen = _formal_common_frozen()
    # Scenario is intentionally first so cross-profile identity misuse reports
    # the scientific mismatch before secondary scheduler/output differences.
    frozen = {
        "scenario": deepcopy(dict(scenario)),
        "scheduler_ids": list(scheduler_ids),
        "required_outputs": list(required_outputs),
        "experiment_id": experiment_id,
        "core": "CORE-3",
        "extension": "EXT-1B",
        **frozen,
        "grid": {
            "utilization_points": ["1/5", "2/5"],
            "tasksets_per_cell": 200,
            "base_seed": base_seed,
            "seed_mode": "generation_dimensions",
            "taskset_index_start": 0,
        },
        "execution": {
            "worker_count": 1,
            "checkpoint_every": 5,
            "output_root": output_root,
            "taskset_store": taskset_store,
            "resume": False,
            "fail_fast_on_p0": True,
            "preserve_attempt_history": True,
        },
        "statistics": {
            "bootstrap_seed": bootstrap_seed,
            "bootstrap_resamples": 2000,
            "top_m": 4,
        },
    }
    return {"profile_id": profile_id, "frozen": frozen}


_B1_FORMAL_SCENARIO = {
    "kind": "BYPASS_STRESS",
    "subtype": "B1",
    "structural_retry_limit": 16,
    "activation_policy": "REPORT_STRUCTURAL_AND_RUNTIME",
    "priority_power_profile": "HIGH_PRIORITY_HIGH_POWER",
    "affordable_prefix_length": 1,
    "deadline_ratio_min": "0",
    "deadline_ratio_max": "1",
    "nominal_energy_supply_ratios": ["1/4", "1/2", "3/4"],
    "initial_energy_policy": "STRUCTURAL_MIDPOINT",
    "release_pattern": "SYNCHRONOUS",
    "harvest_phase_policy": "PEAK_SYNTHETIC",
    "interpolation_rho": "1/2",
    "timing_cells": [],
}
_B2_FORMAL_SCENARIO = {
    **_B1_FORMAL_SCENARIO,
    "kind": "SYNC_BATCH_STRESS",
    "subtype": "B2",
}
_B3_FORMAL_SCENARIO = {
    "kind": "TIMING_STRESS",
    "capacity_feasibility_contract": (
        B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION
    ),
    "subtype": "MULTI_CELL",
    "structural_retry_limit": 16,
    "activation_policy": "REPORT_STRUCTURAL_AND_RUNTIME",
    "priority_power_profile": "ACTUAL_GENERATOR_ORDER",
    "affordable_prefix_length": 1,
    "deadline_ratio_min": "1/2",
    "deadline_ratio_max": "1",
    "nominal_energy_supply_ratios": ["0", "1/2"],
    "initial_energy_policy": "TOP_M_AFFORDABLE",
    "release_pattern": "SYNCHRONOUS",
    "harvest_phase_policy": "PEAK_SYNTHETIC",
    "interpolation_rho": "1/2",
    "timing_cells": [
        {
            "id": "positive-slack-energy-available",
            "subtype": "POSITIVE_SLACK_ENERGY_AVAILABLE",
            "deadline_ratio_min": "1/2",
            "deadline_ratio_max": "3/4",
            "nominal_energy_supply_ratio": "0",
            "initial_energy_policy": "TOP_M_AFFORDABLE",
        },
        {
            "id": "slack-limited-charging",
            "subtype": "SLACK_LIMITED_CHARGING",
            "deadline_ratio_min": "3/4",
            "deadline_ratio_max": "1",
            "nominal_energy_supply_ratio": "1/2",
            "initial_energy_policy": "HALF_TARGET",
        },
    ],
}


FORMAL_PROFILE_BY_SEED_SPACE = {
    "EXT1B1_FORMAL_R1_WORKLOAD_CONTRACT_V2": _formal_profile(
        "B1",
        experiment_id=(
            "asap-block-v9.3-ext1b1-formal-r1-workload-contract-v2"
        ),
        scheduler_ids=("gpfp_asap_block", "gpfp_asap_nonblock"),
        required_outputs=B1_REQUIRED_OUTPUTS,
        base_seed=951201,
        output_root="artifacts/v9_3_ext1b1_formal_r1_workload_contract_v2",
        taskset_store=(
            "artifacts/v9_3_ext1b1_taskset_store_formal_r1_workload_contract_v2"
        ),
        bootstrap_seed=9312901,
        scenario=_B1_FORMAL_SCENARIO,
    ),
    "EXT1B2_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2": _formal_profile(
        "B2",
        experiment_id=(
            "asap-block-v9.3-ext1b2-sync-formal-r1-workload-contract-v2"
        ),
        scheduler_ids=B2_SCHEDULER_IDS,
        required_outputs=B2_REQUIRED_OUTPUTS,
        base_seed=971201,
        output_root=(
            "artifacts/v9_3_ext1b2_sync_formal_r1_workload_contract_v2"
        ),
        taskset_store=(
            "artifacts/v9_3_ext1b2_taskset_store_sync_formal_r1_"
            "workload_contract_v2"
        ),
        bootstrap_seed=9712902,
        scenario=_B2_FORMAL_SCENARIO,
    ),
    (
        "EXT1B3_FORMAL_MECHANISM_R1_WORKLOAD_CONTRACT_V2_"
        "CAPACITY_CONTRACT_V1"
    ): _formal_profile(
        "B3",
        experiment_id=(
            "asap-block-v9.3-ext1b3-timing-formal-r1-workload-contract-v2-"
            "capacity-contract-v1"
        ),
        scheduler_ids=B3_PRIMARY_SCHEDULER_IDS,
        required_outputs=B3_REQUIRED_OUTPUTS,
        base_seed=971301,
        output_root=(
            "artifacts/v9_3_ext1b3_timing_formal_r1_workload_contract_v2_"
            "capacity_contract_v1"
        ),
        taskset_store=(
            "artifacts/v9_3_ext1b3_taskset_store_timing_formal_r1_"
            "workload_contract_v2_capacity_contract_v1"
        ),
        bootstrap_seed=9712903,
        scenario=_B3_FORMAL_SCENARIO,
    ),
}


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"unknown {label} fields: {sorted(unknown)}")


def _mapping(parent: Mapping[str, Any], key: str) -> Dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _first_mismatch_path(observed: Any, expected: Any, path: str) -> str | None:
    if isinstance(expected, Mapping):
        if not isinstance(observed, Mapping):
            return path
        if set(observed) != set(expected):
            missing = sorted(set(expected) - set(observed))
            extra = sorted(set(observed) - set(expected))
            if missing:
                return f"{path}.{missing[0]}"
            if extra:
                return f"{path}.{extra[0]}"
        for key, expected_value in expected.items():
            mismatch = _first_mismatch_path(
                observed[key], expected_value, f"{path}.{key}",
            )
            if mismatch is not None:
                return mismatch
        return None
    if isinstance(expected, list):
        if not isinstance(observed, list) or len(observed) != len(expected):
            return path
        for index, expected_value in enumerate(expected):
            mismatch = _first_mismatch_path(
                observed[index], expected_value, f"{path}[{index}]",
            )
            if mismatch is not None:
                return mismatch
        return None
    return None if observed == expected else path


def _validate_formal_profile(config: Mapping[str, Any]) -> None:
    seed_space = config.get("seed_space")
    profile = FORMAL_PROFILE_BY_SEED_SPACE.get(seed_space)
    if profile is None:
        raise ConfigError(
            "FORMAL requires seed_space in "
            f"{sorted(FORMAL_PROFILE_BY_SEED_SPACE)}"
        )
    profile_id = profile["profile_id"]
    for field, expected in profile["frozen"].items():
        mismatch = _first_mismatch_path(config.get(field), expected, field)
        if mismatch is not None:
            if mismatch in {"simulation.horizon", "simulation.maximum_horizon"}:
                mismatch = "simulation.horizon and maximum_horizon"
            raise ConfigError(
                f"EXT-1B FORMAL profile {profile_id} {mismatch} "
                "does not match its frozen value"
            )


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


def _validate_b3_v2_calibration_grid(scenario: Dict[str, Any]) -> None:
    raw = scenario.get("calibration_grid")
    if not isinstance(raw, dict):
        raise ConfigError("B3-v2 requires scenario.calibration_grid")
    _reject_unknown(raw, CALIBRATION_GRID_KEYS, "scenario.calibration_grid")
    if set(raw) != CALIBRATION_GRID_KEYS:
        raise ConfigError(
            "B3-v2 calibration_grid requires recovery_margin_ticks, "
            "interpolation_rhos, and nominal_energy_supply_ratios"
        )

    margins = raw["recovery_margin_ticks"]
    if not isinstance(margins, list) or len(margins) < 2:
        raise ConfigError("B3-v2 requires at least two recovery margin candidates")
    normalized_margins = []
    for index, value in enumerate(margins):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(
                f"calibration_grid.recovery_margin_ticks[{index}] must be "
                "a non-negative integer"
            )
        normalized_margins.append(value)
    if len(set(normalized_margins)) != len(normalized_margins):
        raise ConfigError("B3-v2 recovery margin candidates must be unique")

    def exact_candidates(key: str) -> list[str]:
        values = raw[key]
        if not isinstance(values, list) or len(values) < 2:
            raise ConfigError(f"B3-v2 requires at least two {key} candidates")
        normalized = []
        for index, value in enumerate(values):
            parsed = exact_fraction(value, f"calibration_grid.{key}[{index}]")
            valid = (
                0 < parsed < 1
                if key == "interpolation_rhos"
                else 0 < parsed <= 1
            )
            if not valid:
                raise ConfigError(
                    f"calibration_grid.{key}[{index}] must satisfy "
                    + (
                        "0 < value < 1"
                        if key == "interpolation_rhos"
                        else "0 < value <= 1"
                    )
                )
            normalized.append(fraction_text(parsed))
        if len(set(normalized)) != len(normalized):
            raise ConfigError(f"B3-v2 {key} candidates must be unique")
        return normalized

    scenario["calibration_grid"] = {
        "recovery_margin_ticks": normalized_margins,
        "interpolation_rhos": exact_candidates("interpolation_rhos"),
        "nominal_energy_supply_ratios": exact_candidates(
            "nominal_energy_supply_ratios"
        ),
    }


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
            "EXT1B_PILOT_WORKLOAD_CONTRACT_V2",
            "EXT1B1_ENERGY_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
            "EXT1B2_SYNC_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
            "EXT1B3_TIMING_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2",
        },
        "CALIBRATION": {
            "EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3",
        },
        "FORMAL": set(FORMAL_PROFILE_BY_SEED_SPACE),
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
        seed_space == "EXT1B2_SYNC_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2"
        and tuple(scheduler_ids) != B2_SCHEDULER_IDS
    ):
        raise ConfigError(
            "EXT1B2_SYNC_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2 "
            "scheduler_ids must equal "
            "['gpfp_asap_block', 'gpfp_asap_sync'] in that order"
        )
    if (
        seed_space == "EXT1B3_TIMING_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2"
        and tuple(scheduler_ids) != B3_PRIMARY_SCHEDULER_IDS
    ):
        raise ConfigError(
            "EXT1B3_TIMING_CALIBRATION_PILOT_WORKLOAD_CONTRACT_V2 "
            "scheduler_ids must equal "
            "['gpfp_asap_block', 'gpfp_alap_block', 'gpfp_st_block'] "
            "in that order"
        )
    if (
        seed_space == "EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3"
        and tuple(scheduler_ids) != B3_PRIMARY_SCHEDULER_IDS
    ):
        raise ConfigError(
            "EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3 "
            "scheduler_ids must equal "
            "['gpfp_asap_block', 'gpfp_alap_block', 'gpfp_st_block'] "
            "in that order"
        )
    config["scheduler_ids"] = list(scheduler_ids)
    expected_outputs = {
        "BYPASS_STRESS": list(B1_REQUIRED_OUTPUTS),
        "SYNC_BATCH_STRESS": list(B2_REQUIRED_OUTPUTS),
        "TIMING_STRESS": list(
            B3_V2_REQUIRED_OUTPUTS
            if scenario.get("scenario_contract_id")
            == B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
            else B3_REQUIRED_OUTPUTS
        ),
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
    workload_candidates = config["generation"].get("workload_candidates")
    if (
        scenario["priority_power_profile"] == "ACTUAL_GENERATOR_ORDER"
        and workload_candidates is None
    ):
        raise ConfigError(
            "ACTUAL_GENERATOR_ORDER requires generation.workload_candidates"
        )
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
        capacity_contract = scenario.get("capacity_feasibility_contract")
        if capacity_contract is None:
            raise ConfigError(
                "legacy B3 config lacks the capacity feasibility contract; "
                "regenerate the B3 config/store/output"
            )
        if capacity_contract != (
            B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION
        ):
            raise ConfigError(
                "unknown B3 capacity feasibility contract; regenerate the "
                "B3 config/store/output"
            )
        if scenario.get("subtype") != "MULTI_CELL":
            raise ConfigError("TIMING_STRESS requires subtype: MULTI_CELL")
        _validate_timing_cells(scenario)
        scenario_contract = scenario.get("scenario_contract_id")
        if scenario_contract is None:
            if "calibration_grid" in scenario:
                raise ConfigError(
                    "legacy B3 config cannot declare a B3-v2 calibration grid"
                )
        elif scenario_contract == B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2:
            if status != "CALIBRATION" or seed_space != (
                "EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3"
            ):
                raise ConfigError(
                    "B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2 is isolated "
                    "to the CALIBRATION v3 seed space"
                )
            _validate_b3_v2_calibration_grid(scenario)
        else:
            raise ConfigError("unknown B3 scenario contract ID")
    else:
        if "capacity_feasibility_contract" in scenario:
            raise ConfigError(
                "capacity_feasibility_contract is only valid for TIMING_STRESS"
            )
        expected = "B1" if kind == "BYPASS_STRESS" else "B2"
        if scenario.get("subtype") != expected:
            raise ConfigError(f"{kind} requires subtype: {expected}")
        if scenario.get("timing_cells") != []:
            raise ConfigError("non-timing scenarios require timing_cells: []")
        if "scenario_contract_id" in scenario or "calibration_grid" in scenario:
            raise ConfigError(
                "B3 target-trace fields are only valid for TIMING_STRESS"
            )

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
        _validate_formal_profile(normalized)
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
    return domain_hash("ASAP_BLOCK:V9.3:EXT1B:CONFIG:v2", semantic)


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
