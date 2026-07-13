"""Configuration loading and fail-closed validation for v9.3 experiments."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when an experiment configuration is unsafe or ambiguous."""


KNOWN_VARIANTS = {
    "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC",
}
KNOWN_DEADLINE_MODES = {"implicit", "constrained"}
KNOWN_POWER_MODES = {"generator_default_heterogeneous"}
KNOWN_PRIORITY_POLICIES = {"RM"}
KNOWN_NUMERICAL_MODES = {"EXACT_RATIONAL"}
KNOWN_WCET_ROUNDING = {"floor", "round", "ceil", "compensated"}
KNOWN_RETRY_POLICIES = {"none", "timeout_once"}
KNOWN_CONSTRAINED_DISTRIBUTIONS = {"generator_uniform_integer"}


def exact_fraction(value: Any, label: str) -> Fraction:
    """Parse an exact non-negative rational without accepting binary floats."""

    if isinstance(value, bool) or isinstance(value, float):
        raise ConfigError(f"{label} must be an integer or exact decimal/rational string")
    if isinstance(value, int):
        result = Fraction(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ConfigError(f"{label} must not be empty")
        try:
            result = Fraction(text)
        except (ValueError, ZeroDivisionError) as exc:
            raise ConfigError(f"invalid exact value for {label}: {value!r}") from exc
    elif isinstance(value, Fraction):
        result = value
    else:
        raise ConfigError(f"{label} must be an integer or exact decimal/rational string")
    if result < 0:
        raise ConfigError(f"{label} must be non-negative")
    return result


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _canonical(value: Any) -> Any:
    if isinstance(value, Fraction):
        return fraction_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError("configuration contains NaN or Inf")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def _require_mapping(parent: Mapping[str, Any], key: str) -> Dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ConfigError(f"{label} must be a positive finite number")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{label} must be a positive integer")
    return value


def _as_list(value: Any, label: str) -> list:
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ConfigError(f"{label} must not be empty")
    return values


def _validate_ratios(values: Iterable[Any], label: str) -> list[str]:
    result = []
    for index, raw in enumerate(values):
        ratio = exact_fraction(raw, f"{label}[{index}]")
        if ratio <= 0 or ratio > 1:
            raise ConfigError(f"{label}[{index}] must satisfy 0 < D/T <= 1")
        result.append(fraction_text(ratio))
    return result


def validate_config(raw: Mapping[str, Any], *, expected_core: str | None = None) -> Dict[str, Any]:
    config = deepcopy(dict(raw))
    experiment_id = config.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ConfigError("experiment_id must be a non-empty string")
    core = config.get("core")
    if core not in {"CORE-1", "CORE-2"}:
        raise ConfigError("core must be CORE-1 or CORE-2")
    if expected_core is not None and core != expected_core:
        raise ConfigError(f"runner requires {expected_core}, got {core!r}")

    platform = _require_mapping(config, "platform")
    platform["cores"] = [_positive_int(item, "platform.cores") for item in _as_list(platform.get("cores"), "platform.cores")]
    platform["task_count"] = [_positive_int(item, "platform.task_count") for item in _as_list(platform.get("task_count"), "platform.task_count")]

    generation = _require_mapping(config, "generation")
    if generation.get("deadline_mode") not in KNOWN_DEADLINE_MODES:
        raise ConfigError("unknown generation.deadline_mode")
    if generation.get("power_mode") not in KNOWN_POWER_MODES:
        raise ConfigError("unknown generation.power_mode")
    if generation.get("priority_policy") not in KNOWN_PRIORITY_POLICIES:
        raise ConfigError("unknown generation.priority_policy")
    if generation.get("wcet_rounding") not in KNOWN_WCET_ROUNDING:
        raise ConfigError("unknown generation.wcet_rounding")
    generation["period_min"] = _positive_int(generation.get("period_min"), "generation.period_min")
    generation["period_max"] = _positive_int(generation.get("period_max"), "generation.period_max")
    if generation["period_min"] > generation["period_max"]:
        raise ConfigError("generation.period_min must not exceed period_max")
    tolerance = exact_fraction(generation.get("utilization_tolerance"), "generation.utilization_tolerance")
    generation["utilization_tolerance"] = fraction_text(tolerance)
    for label in ("min_task_util", "max_task_util"):
        value = exact_fraction(generation.get(label), f"generation.{label}")
        if value <= 0 or value > 1:
            raise ConfigError(f"generation.{label} must satisfy 0 < value <= 1")
        generation[label] = fraction_text(value)
    if Fraction(generation["min_task_util"]) > Fraction(generation["max_task_util"]):
        raise ConfigError("generation.min_task_util must not exceed max_task_util")
    constrained = _require_mapping(generation, "constrained_deadline")
    distribution = constrained.get("distribution")
    if distribution not in KNOWN_CONSTRAINED_DISTRIBUTIONS:
        raise ConfigError("unknown constrained-deadline distribution")
    constrained["d_over_t_values"] = _validate_ratios(
        constrained.get("d_over_t_values", []), "generation.constrained_deadline.d_over_t_values"
    )
    lower = exact_fraction(constrained.get("d_over_t_min"), "generation.constrained_deadline.d_over_t_min")
    upper = exact_fraction(constrained.get("d_over_t_max"), "generation.constrained_deadline.d_over_t_max")
    if lower < 0 or upper > 1 or lower > upper:
        raise ConfigError("constrained D/T bounds must satisfy 0 <= min <= max <= 1")
    constrained["d_over_t_min"] = fraction_text(lower)
    constrained["d_over_t_max"] = fraction_text(upper)
    if generation["deadline_mode"] == "constrained":
        # The existing generator owns this distribution. It currently has no
        # interface for filtering/fixing arbitrary D/T values or subranges.
        if constrained["d_over_t_values"] or lower != 0 or upper != 1:
            raise ConfigError(
                "the production generator only supports its existing full-range "
                "generator_uniform_integer constrained-deadline distribution"
            )

    energy = _require_mapping(config, "energy")
    exact_values = [
        fraction_text(exact_fraction(item, f"energy.initial_energy_values[{index}]"))
        for index, item in enumerate(_as_list(energy.get("initial_energy_values"), "energy.initial_energy_values"))
    ]
    if len(exact_values) != len(set(exact_values)):
        raise ConfigError("energy.initial_energy_values contains duplicates")
    energy["initial_energy_values"] = exact_values
    if energy.get("exact_rational_encoding") != "canonical_fraction":
        raise ConfigError("energy.exact_rational_encoding must be canonical_fraction")
    if energy.get("battery_mode") not in {"finite", "unbounded"}:
        raise ConfigError("unknown energy.battery_mode")
    capacity = exact_fraction(energy.get("battery_capacity"), "energy.battery_capacity")
    if energy["battery_mode"] == "finite" and capacity <= 0:
        raise ConfigError("finite battery capacity must be positive")
    energy["battery_capacity"] = fraction_text(capacity)
    service = _require_mapping(energy, "service_curve")
    if not isinstance(service.get("id"), str) or not service["id"]:
        raise ConfigError("energy.service_curve.id must be non-empty")
    _positive_int(service.get("horizon"), "energy.service_curve.horizon")

    grid = _require_mapping(config, "grid")
    utilities = _validate_ratios(_as_list(grid.get("utilization_points"), "grid.utilization_points"), "grid.utilization_points")
    grid["utilization_points"] = utilities
    grid["tasksets_per_cell"] = _positive_int(grid.get("tasksets_per_cell"), "grid.tasksets_per_cell")
    if isinstance(grid.get("base_seed"), bool) or not isinstance(grid.get("base_seed"), int):
        raise ConfigError("grid.base_seed must be an integer")

    analysis = _require_mapping(config, "analysis")
    variants = analysis.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ConfigError("analysis.variants must be a non-empty list")
    if len(variants) != len(set(variants)):
        raise ConfigError("analysis.variants contains duplicates")
    if any(item not in KNOWN_VARIANTS for item in variants):
        raise ConfigError("analysis.variants contains an unknown variant")
    expected = (
        ["CW_THETA_CW", "LOC_THETA_LOC"] if core == "CORE-1" else
        ["CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"]
    )
    if variants != expected:
        raise ConfigError(f"{core} variants must equal {expected}")
    initial_timeout = _positive_number(analysis.get("timeout_seconds"), "analysis.timeout_seconds")
    retry_timeout = analysis.get("retry_timeout_seconds")
    if retry_timeout is not None:
        retry_timeout = _positive_number(retry_timeout, "analysis.retry_timeout_seconds")
        if retry_timeout < initial_timeout:
            raise ConfigError("retry timeout must not be smaller than initial timeout")
    if analysis.get("retry_policy") not in KNOWN_RETRY_POLICIES:
        raise ConfigError("unknown analysis.retry_policy")
    if analysis["retry_policy"] == "timeout_once" and retry_timeout is None:
        raise ConfigError("timeout_once requires analysis.retry_timeout_seconds")
    analysis["worker_count"] = _positive_int(analysis.get("worker_count"), "analysis.worker_count")
    if analysis.get("numerical_mode") not in KNOWN_NUMERICAL_MODES:
        raise ConfigError("unknown analysis.numerical_mode")

    execution = _require_mapping(config, "execution")
    execution["checkpoint_every"] = _positive_int(execution.get("checkpoint_every"), "execution.checkpoint_every")
    for key in ("output_root", "taskset_store"):
        if not isinstance(execution.get(key), str) or not execution[key]:
            raise ConfigError(f"execution.{key} must be a non-empty path")
    for key in ("resume", "fail_fast_on_p0", "preserve_attempt_history"):
        if not isinstance(execution.get(key), bool):
            raise ConfigError(f"execution.{key} must be boolean")
    if not execution["preserve_attempt_history"]:
        raise ConfigError("formal runs require preserve_attempt_history=true")
    return config


def load_config(path: Path | str, *, expected_core: str | None = None) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a YAML mapping")
    return validate_config(raw, expected_core=expected_core)


def config_hash(config: Mapping[str, Any]) -> str:
    # Resume is a runtime choice, not an experiment semantic.
    semantic = deepcopy(dict(config))
    semantic.get("execution", {}).pop("resume", None)
    return domain_hash("ASAP_BLOCK:V9.3:FORMAL_CONFIG:v1", semantic)


def dump_config(config: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(_canonical(config), allow_unicode=True, sort_keys=False), encoding="utf-8")
