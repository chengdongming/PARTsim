"""Strict, isolated configuration contract for v9.3 B4 (EXT-1P/PERF-G)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import yaml

from .config import ConfigError, canonical_json, domain_hash, exact_fraction, fraction_text
from .scheduler_registry import SCHEDULER_IDS, audited_scheduler_registry


CONFIG_SCHEMA = "ASAP_BLOCK_V9_3_B4_CONFIG_V1"
CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:B4:CONFIG:v1"
CONTRACT_VERSION = "ASAP_BLOCK_V9_3_B4_PERF_G_R1"
OUTCOME_VERSION = "PERF_OUTCOME_V2"
NORMALIZATION_HORIZON_MS = 60000

ALL_SCHEDULERS = tuple(SCHEDULER_IDS)
PRIMARY_SCHEDULERS = (
    "gpfp_asap_block",
    "gpfp_asap_nonblock",
    "gpfp_asap_sync",
    "gpfp_alap_block",
    "gpfp_st_block",
)
CONFIRMATORY_COMPARISONS = (
    ("gpfp_asap_block", "gpfp_asap_nonblock"),
    ("gpfp_asap_block", "gpfp_asap_sync"),
    ("gpfp_asap_block", "gpfp_alap_block"),
    ("gpfp_asap_block", "gpfp_st_block"),
)
FORMAL_UTILIZATIONS = (
    "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5",
)
CAL_UTILIZATIONS = ("3/10", "1/2", "7/10")
INITIAL_KAPPAS = ("10", "50", "200")
INITIAL_ETAS = ("1/2", "3/4", "1", "5/4", "3/2")
EXTENDED_ETAS = ("1/4", "1/2", "3/4", "1", "5/4", "3/2", "2")
WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")

KNOWN_STAGES = {"CALIBRATION", "HORIZON_GATE", "FORMAL", "SMOKE"}
TOP_LEVEL_KEYS = {
    "schema", "experiment_id", "stage", "parameter_status", "seed_space",
    "platform", "generation", "energy", "grid", "simulation", "execution",
    "statistics", "scheduler_ids", "primary_scheduler_ids",
    "confirmatory_comparisons",
}
PLATFORM_KEYS = {
    "cores", "task_count", "scheduling", "preemptive", "migrative",
    "priority_policy", "time_unit_ms",
}
GENERATION_KEYS = {
    "deadline_mode", "period_min", "period_max", "wcet_rounding",
    "utilization_tolerance_total", "min_task_util", "max_task_util",
    "dag_enabled", "arrival_offset", "workload_candidates",
    "system_template", "generator_timeout_seconds", "structural_retry_limit",
    "generation_contract_version",
}
ENERGY_KEYS = {
    "normalization_horizon_ms", "kappa_values", "eta_values",
    "selected_conditions", "allow_harvest_clipping", "solar_phase",
    "system_template", "selection_seal",
}
GRID_KEYS = {
    "utilization_points", "tasksets_per_utilization", "selected_tasksets_per_utilization",
    "base_seed", "taskset_index_start",
}
SIMULATION_KEYS = {
    "horizons_ms", "warmup_ms", "minimum_adjudicable_jobs_per_task",
    "timeout_seconds", "retry_timeout_seconds", "trace_mode",
    "trace_sample_fraction", "simulator_bin",
}
EXECUTION_KEYS = {
    "worker_count", "checkpoint_every", "output_root", "taskset_store",
    "resume", "calibration_seal", "horizon_seal", "gate_results_root",
}
STATISTICS_KEYS = {
    "bootstrap_seed", "permutation_seed", "resamples", "confidence_level",
}


def _mapping(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _keys(value: Mapping[str, Any], allowed: set, label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"unknown {label} key(s): {unknown}")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{label} must be a positive integer")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a boolean")
    return value


def _exact_list(value: Any, label: str) -> list:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{label} must be a non-empty list")
    return [fraction_text(exact_fraction(item, f"{label}[{index}]")) for index, item in enumerate(value)]


def _require_order(observed: Any, expected: Sequence[Any], label: str) -> list:
    if not isinstance(observed, list) or tuple(observed) != tuple(expected):
        raise ConfigError(f"{label} must use the frozen order {list(expected)}")
    return list(observed)


def normalize_performance_config(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a B4 config without importing or mutating EXT-1B contracts."""

    if not isinstance(raw, Mapping):
        raise ConfigError("B4 config must be a mapping")
    config = deepcopy(dict(raw))
    _keys(config, TOP_LEVEL_KEYS, "top-level")
    if config.get("schema") != CONFIG_SCHEMA:
        raise ConfigError(f"schema must be {CONFIG_SCHEMA}")
    if not isinstance(config.get("experiment_id"), str) or not config["experiment_id"]:
        raise ConfigError("experiment_id must be a non-empty string")
    stage = config.get("stage")
    if stage not in KNOWN_STAGES:
        raise ConfigError(f"unknown B4 stage: {stage!r}")

    platform = _mapping(config.get("platform"), "platform")
    generation = _mapping(config.get("generation"), "generation")
    energy = _mapping(config.get("energy"), "energy")
    grid = _mapping(config.get("grid"), "grid")
    simulation = _mapping(config.get("simulation"), "simulation")
    execution = _mapping(config.get("execution"), "execution")
    statistics = _mapping(config.get("statistics"), "statistics")
    _keys(platform, PLATFORM_KEYS, "platform")
    _keys(generation, GENERATION_KEYS, "generation")
    _keys(energy, ENERGY_KEYS, "energy")
    _keys(grid, GRID_KEYS, "grid")
    _keys(simulation, SIMULATION_KEYS, "simulation")
    _keys(execution, EXECUTION_KEYS, "execution")
    _keys(statistics, STATISTICS_KEYS, "statistics")

    if _positive_int(platform.get("cores"), "platform.cores") != 4:
        raise ConfigError("B4 platform.cores must be 4")
    if _positive_int(platform.get("task_count"), "platform.task_count") != 10:
        raise ConfigError("B4 platform.task_count must be 10")
    frozen_platform = {
        "scheduling": "global", "preemptive": True, "migrative": True,
        "priority_policy": "RM", "time_unit_ms": 1,
    }
    for key, expected in frozen_platform.items():
        if platform.get(key) != expected:
            raise ConfigError(f"platform.{key} must be {expected!r}")

    frozen_generation = {
        "deadline_mode": "constrained", "period_min": 40, "period_max": 200,
        "wcet_rounding": "compensated", "dag_enabled": False,
        "arrival_offset": False,
    }
    for key, expected in frozen_generation.items():
        if generation.get(key) != expected:
            raise ConfigError(f"generation.{key} must be {expected!r}")
    generation["utilization_tolerance_total"] = fraction_text(exact_fraction(
        generation.get("utilization_tolerance_total"), "generation.utilization_tolerance_total"
    ))
    generation["min_task_util"] = fraction_text(exact_fraction(
        generation.get("min_task_util"), "generation.min_task_util"
    ))
    generation["max_task_util"] = fraction_text(exact_fraction(
        generation.get("max_task_util"), "generation.max_task_util"
    ))
    if generation["utilization_tolerance_total"] != "1/100":
        raise ConfigError("B4 utilization tolerance must be 1/100")
    if generation["min_task_util"] != "1/100" or generation["max_task_util"] != "4/5":
        raise ConfigError("B4 per-task utilization range must be [1/100,4/5]")
    _require_order(generation.get("workload_candidates"), WORKLOADS, "generation.workload_candidates")
    _positive_int(generation.get("generator_timeout_seconds"), "generation.generator_timeout_seconds")
    _positive_int(generation.get("structural_retry_limit"), "generation.structural_retry_limit")

    if energy.get("normalization_horizon_ms") != NORMALIZATION_HORIZON_MS:
        raise ConfigError("energy.normalization_horizon_ms must be 60000")
    if energy.get("allow_harvest_clipping") is not True:
        raise ConfigError("B4 finite battery requires allow_harvest_clipping=true")
    energy["kappa_values"] = _exact_list(energy.get("kappa_values"), "energy.kappa_values")
    energy["eta_values"] = _exact_list(energy.get("eta_values"), "energy.eta_values")
    selected = energy.get("selected_conditions", [])
    if not isinstance(selected, list):
        raise ConfigError("energy.selected_conditions must be a list")
    if selected and selected != ["low", "transition", "high"]:
        raise ConfigError("selected energy conditions must be [low, transition, high]")

    grid["utilization_points"] = _exact_list(grid.get("utilization_points"), "grid.utilization_points")
    _positive_int(grid.get("tasksets_per_utilization"), "grid.tasksets_per_utilization")
    _positive_int(grid.get("base_seed"), "grid.base_seed")
    if grid.get("taskset_index_start") != 0:
        raise ConfigError("grid.taskset_index_start must be zero")
    if "selected_tasksets_per_utilization" in grid:
        _positive_int(grid["selected_tasksets_per_utilization"], "grid.selected_tasksets_per_utilization")

    horizons = simulation.get("horizons_ms")
    if not isinstance(horizons, list) or not horizons:
        raise ConfigError("simulation.horizons_ms must be a non-empty list")
    for index, horizon in enumerate(horizons):
        _positive_int(horizon, f"simulation.horizons_ms[{index}]")
    if simulation.get("warmup_ms") != 0:
        raise ConfigError("simulation.warmup_ms must be zero")
    _positive_int(simulation.get("minimum_adjudicable_jobs_per_task"), "simulation.minimum_adjudicable_jobs_per_task")
    _positive_int(simulation.get("timeout_seconds"), "simulation.timeout_seconds")
    _positive_int(simulation.get("retry_timeout_seconds"), "simulation.retry_timeout_seconds")
    if simulation.get("trace_mode") != "job":
        raise ConfigError("B4 simulation.trace_mode must be job")
    simulation["trace_sample_fraction"] = fraction_text(exact_fraction(
        simulation.get("trace_sample_fraction"), "simulation.trace_sample_fraction"
    ))
    if simulation["trace_sample_fraction"] != "1/20":
        raise ConfigError("B4 trace sample fraction must be 1/20")

    _positive_int(execution.get("worker_count"), "execution.worker_count")
    _positive_int(execution.get("checkpoint_every"), "execution.checkpoint_every")
    _bool(execution.get("resume"), "execution.resume")
    for key in ("bootstrap_seed", "permutation_seed", "resamples"):
        _positive_int(statistics.get(key), f"statistics.{key}")
    if statistics["resamples"] != 10000:
        raise ConfigError("B4 inference uses exactly 10000 resamples")
    statistics["confidence_level"] = fraction_text(exact_fraction(
        statistics.get("confidence_level"), "statistics.confidence_level"
    ))
    if statistics["confidence_level"] != "19/20":
        raise ConfigError("B4 confidence level must be 19/20")

    audited_scheduler_registry()
    _require_order(config.get("scheduler_ids"), ALL_SCHEDULERS, "scheduler_ids")
    _require_order(config.get("primary_scheduler_ids"), PRIMARY_SCHEDULERS, "primary_scheduler_ids")
    comparisons = config.get("confirmatory_comparisons")
    expected_comparisons = [list(item) for item in CONFIRMATORY_COMPARISONS]
    if comparisons != expected_comparisons:
        raise ConfigError("confirmatory comparisons must be the frozen four-comparison family")

    expected = {
        "CALIBRATION": (CAL_UTILIZATIONS, 30, 981201),
        "HORIZON_GATE": (FORMAL_UTILIZATIONS, 200, 982201),
        "FORMAL": (FORMAL_UTILIZATIONS, 200, 982201),
        "SMOKE": (("1/10",), 1, 984201),
    }[stage]
    if tuple(grid["utilization_points"]) != tuple(expected[0]):
        raise ConfigError(f"{stage} utilization grid is not frozen")
    if grid["tasksets_per_utilization"] != expected[1] or grid["base_seed"] != expected[2]:
        raise ConfigError(f"{stage} taskset count/base seed is not frozen")
    if stage == "CALIBRATION":
        if tuple(energy["kappa_values"]) != INITIAL_KAPPAS or tuple(energy["eta_values"]) != INITIAL_ETAS:
            raise ConfigError("CAL initial kappa/eta grid is not frozen")
        if simulation["horizons_ms"] != [10000, 30000]:
            raise ConfigError("CAL horizons must be [10000,30000]")
    elif stage == "HORIZON_GATE":
        if grid.get("selected_tasksets_per_utilization") != 50:
            raise ConfigError("horizon gate selects 50 tasksets per utilization")
        if simulation["horizons_ms"] != [30000, 60000]:
            raise ConfigError("gate horizons must be [30000,60000]")
    elif stage == "FORMAL":
        if selected != ["low", "transition", "high"]:
            raise ConfigError("formal template requires three named energy conditions")
        if len(simulation["horizons_ms"]) != 1 or simulation["horizons_ms"][0] not in {30000, 60000}:
            raise ConfigError("formal horizon must be selected as 30000 or 60000")
    elif stage == "SMOKE" and simulation["minimum_adjudicable_jobs_per_task"] > 2:
        raise ConfigError("smoke minimum job threshold must remain tiny")

    config["contract_version"] = CONTRACT_VERSION
    config["config_hash"] = domain_hash(CONFIG_DOMAIN, {
        key: value for key, value in config.items() if key not in {"config_hash"}
    })
    return config


def load_performance_config(path: Path) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load B4 config: {exc}") from exc
    return normalize_performance_config(raw)


def plan_counts(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Return static request counts without touching a store or simulator."""

    stage = str(config["stage"])
    u_count = len(config["grid"]["utilization_points"])
    tasksets = u_count * int(config["grid"]["tasksets_per_utilization"])
    if stage == "CALIBRATION":
        return {
            "stage": stage, "unique_tasksets": 90, "energy_cells": 15,
            "primary_schedulers": 5, "requests": 6750,
            "confirmation_requests": 1350, "simulator_invoked": False,
        }
    if stage == "HORIZON_GATE":
        return {
            "stage": stage, "source_formal_tasksets": tasksets,
            "selected_tasksets": 400, "energy_cells": 1,
            "primary_schedulers": 5, "horizons": 2, "requests": 4000,
            "simulator_invoked": False,
        }
    if stage == "FORMAL":
        return {
            "stage": stage, "unique_tasksets": tasksets, "energy_cells": 3,
            "schedulers": 9, "formal_requests": 43200,
            "reusable_gate_requests": 2000, "requests_after_gate": 41200,
            "simulator_invoked": False,
        }
    schedulers = len(config["scheduler_ids"])
    energy_cells = max(1, len(config["energy"].get("selected_conditions", [])))
    return {
        "stage": stage, "unique_tasksets": tasksets,
        "energy_cells": energy_cells, "schedulers": schedulers,
        "requests": tasksets * energy_cells * schedulers,
        "simulator_invoked": False,
    }


def assert_execution_seals(config: Mapping[str, Any]) -> None:
    if config["stage"] not in {"HORIZON_GATE", "FORMAL"}:
        return
    calibration = str(config["execution"].get("calibration_seal", "")).strip()
    if not calibration or not Path(calibration).is_file():
        raise ConfigError("execution requires an existing CAL selection seal")
    if config["stage"] == "FORMAL":
        horizon = str(config["execution"].get("horizon_seal", "")).strip()
        if not horizon or not Path(horizon).is_file():
            raise ConfigError("formal execution requires an existing horizon selection seal")


def semantic_config_material(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Exclude paths, workers and checkpoint controls from simulation identity."""

    return {
        "contract_version": CONTRACT_VERSION,
        "platform": config["platform"],
        "generation": config["generation"],
        "normalization_horizon_ms": config["energy"]["normalization_horizon_ms"],
        "allow_harvest_clipping": config["energy"]["allow_harvest_clipping"],
        "warmup_ms": config["simulation"]["warmup_ms"],
        "minimum_adjudicable_jobs_per_task": config["simulation"]["minimum_adjudicable_jobs_per_task"],
        "trace_mode": config["simulation"]["trace_mode"],
        "outcome_version": OUTCOME_VERSION,
    }


def config_summary(config: Mapping[str, Any]) -> str:
    return canonical_json({"config_hash": config["config_hash"], **plan_counts(config)})
