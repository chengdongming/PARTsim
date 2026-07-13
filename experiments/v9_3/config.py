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
KNOWN_SEED_MODES = {"generation_dimensions", "utilization_index_taskset_index"}
KNOWN_HORIZON_EXTENSION_POLICIES = {"none", "double"}
KNOWN_TRACE_MODES = {"semantic"}


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
    if core not in {"CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5"}:
        raise ConfigError("core must be CORE-1, CORE-2, CORE-3, CORE-4, or CORE-5")
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
    if core == "CORE-3":
        initial_battery = exact_fraction(
            energy.get("simulation_initial_battery"),
            "energy.simulation_initial_battery",
        )
        if initial_battery > capacity:
            raise ConfigError(
                "energy.simulation_initial_battery must not exceed battery_capacity"
            )
        if initial_battery < max(Fraction(value) for value in exact_values):
            raise ConfigError(
                "simulation initial battery must cover every configured release-time E0"
            )
        energy["simulation_initial_battery"] = fraction_text(initial_battery)
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
    seed_mode = grid.get("seed_mode", "generation_dimensions")
    if seed_mode not in KNOWN_SEED_MODES:
        raise ConfigError("unknown grid.seed_mode")
    taskset_index_start = grid.get("taskset_index_start", 0)
    if (
        isinstance(taskset_index_start, bool)
        or not isinstance(taskset_index_start, int)
        or taskset_index_start < 0
    ):
        raise ConfigError("grid.taskset_index_start must be a non-negative integer")
    cell_filter = grid.get("cell_filter")
    if cell_filter is not None:
        if not isinstance(cell_filter, list) or not cell_filter:
            raise ConfigError("grid.cell_filter must be a non-empty list")
        normalized_filter = []
        for index, item in enumerate(cell_filter):
            if not isinstance(item, dict) or set(item) != {"utilization", "exact_e0"}:
                raise ConfigError(
                    "each grid.cell_filter entry requires utilization and exact_e0"
                )
            utilization = exact_fraction(
                item["utilization"], f"grid.cell_filter[{index}].utilization"
            )
            e0 = exact_fraction(item["exact_e0"], f"grid.cell_filter[{index}].exact_e0")
            if utilization <= 0 or utilization > 1:
                raise ConfigError("filtered utilization must satisfy 0 < U <= 1")
            normalized_filter.append({
                "utilization": fraction_text(utilization),
                "exact_e0": fraction_text(e0),
            })
        if len({(row["utilization"], row["exact_e0"]) for row in normalized_filter}) != len(normalized_filter):
            raise ConfigError("grid.cell_filter contains duplicates")
        allowed_u = set(grid["utilization_points"])
        allowed_e0 = set(energy["initial_energy_values"])
        if any(row["utilization"] not in allowed_u or row["exact_e0"] not in allowed_e0 for row in normalized_filter):
            raise ConfigError("grid.cell_filter must be a subset of the configured U/E0 grid")
        grid["cell_filter"] = normalized_filter

    if core == "CORE-3":
        rta = _require_mapping(config, "rta")
        methods = rta.get("methods")
        if methods != ["CW_THETA_CW", "LOC_THETA_LOC"]:
            raise ConfigError(
                "CORE-3 rta.methods must equal ['CW_THETA_CW', 'LOC_THETA_LOC']"
            )
        initial_timeout = _positive_number(
            rta.get("timeout_seconds"), "rta.timeout_seconds"
        )
        retry_timeout = rta.get("retry_timeout_seconds")
        if retry_timeout is not None:
            retry_timeout = _positive_number(
                retry_timeout, "rta.retry_timeout_seconds"
            )
            if retry_timeout < initial_timeout:
                raise ConfigError("retry timeout must not be smaller than initial timeout")
        retry_policy = rta.get(
            "retry_policy", "timeout_once" if retry_timeout is not None else "none"
        )
        if retry_policy not in KNOWN_RETRY_POLICIES:
            raise ConfigError("unknown rta.retry_policy")
        if retry_policy == "timeout_once" and retry_timeout is None:
            raise ConfigError("timeout_once requires rta.retry_timeout_seconds")
        execution_preview = _require_mapping(config, "execution")
        worker_count = _positive_int(
            execution_preview.get("worker_count"), "execution.worker_count"
        )
        numerical_mode = rta.get("numerical_mode", "EXACT_RATIONAL")
        config["analysis"] = {
            "variants": list(methods),
            "timeout_seconds": initial_timeout,
            "retry_timeout_seconds": retry_timeout,
            "retry_policy": retry_policy,
            "worker_count": worker_count,
            "numerical_mode": numerical_mode,
        }

    analysis = _require_mapping(config, "analysis")
    variants = analysis.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ConfigError("analysis.variants must be a non-empty list")
    if len(variants) != len(set(variants)):
        raise ConfigError("analysis.variants contains duplicates")
    if any(item not in KNOWN_VARIANTS for item in variants):
        raise ConfigError("analysis.variants contains an unknown variant")
    expected = None
    if core in {"CORE-1", "CORE-3"}:
        expected = ["CW_THETA_CW", "LOC_THETA_LOC"]
    elif core == "CORE-2":
        expected = ["CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"]
    if expected is not None and variants != expected:
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

    if core == "CORE-4":
        sensitivity = _require_mapping(config, "sensitivity")
        axes = _require_mapping(sensitivity, "axes")
        if set(axes) != {"initial_energy", "service_curve", "power_scale", "method"}:
            raise ConfigError(
                "sensitivity.axes must contain exactly initial_energy, service_curve, "
                "power_scale, and method"
            )
        e0_axis = _require_mapping(axes, "initial_energy")
        e0_values = [
            fraction_text(exact_fraction(value, f"sensitivity.axes.initial_energy.values[{index}]"))
            for index, value in enumerate(
                _as_list(e0_axis.get("values"), "sensitivity.axes.initial_energy.values")
            )
        ]
        if len(e0_values) != len(set(e0_values)):
            raise ConfigError("initial-energy sensitivity levels contain duplicates")
        if e0_values != sorted(e0_values, key=Fraction):
            raise ConfigError("initial-energy sensitivity levels must be exactly ordered")
        e0_axis["values"] = e0_values

        power_axis = _require_mapping(axes, "power_scale")
        power_values = []
        for index, value in enumerate(
            _as_list(power_axis.get("values"), "sensitivity.axes.power_scale.values")
        ):
            scale = exact_fraction(value, f"sensitivity.axes.power_scale.values[{index}]")
            if scale <= 0:
                raise ConfigError("power-scale sensitivity levels must be positive")
            power_values.append(fraction_text(scale))
        if len(power_values) != len(set(power_values)):
            raise ConfigError("power-scale sensitivity levels contain duplicates")
        if power_values != sorted(power_values, key=Fraction):
            raise ConfigError("power-scale sensitivity levels must be exactly ordered")
        power_axis["values"] = power_values

        method_axis = _require_mapping(axes, "method")
        method_values = _as_list(method_axis.get("variants"), "sensitivity.axes.method.variants")
        if len(method_values) != len(set(method_values)) or any(
            value not in KNOWN_VARIANTS for value in method_values
        ):
            raise ConfigError("method sensitivity variants must be unique known variants")
        if variants != method_values:
            raise ConfigError("analysis.variants must equal sensitivity method variants")
        method_axis["variants"] = list(method_values)

        service_axis = _require_mapping(axes, "service_curve")
        service_values = _as_list(
            service_axis.get("variants"), "sensitivity.axes.service_curve.variants"
        )
        normalized_services = []
        service_ids = set()
        for index, value in enumerate(service_values):
            if not isinstance(value, dict):
                raise ConfigError(f"service-curve variant {index} must be a mapping")
            service_id = value.get("id")
            if not isinstance(service_id, str) or not service_id:
                raise ConfigError(f"service-curve variant {index} requires a non-empty id")
            if service_id in service_ids:
                raise ConfigError("service-curve variant IDs must be unique")
            service_ids.add(service_id)
            availability = value.get("availability", "AVAILABLE")
            if availability not in {"AVAILABLE", "UNAVAILABLE"}:
                raise ConfigError("service-curve availability must be AVAILABLE or UNAVAILABLE")
            normalized = dict(value)
            normalized["availability"] = availability
            if availability == "AVAILABLE":
                template = normalized.get("system_template")
                if not isinstance(template, str) or not template:
                    raise ConfigError("available service-curve variants require system_template")
                normalized["horizon"] = _positive_int(
                    normalized.get("horizon"),
                    f"sensitivity.axes.service_curve.variants[{index}].horizon",
                )
            else:
                reason = normalized.get("reason")
                if not isinstance(reason, str) or not reason:
                    raise ConfigError("unavailable service-curve variants require a reason")
            normalized_services.append(normalized)
        service_axis["variants"] = normalized_services

    if core == "CORE-5":
        scalability = _require_mapping(config, "scalability")
        for key in ("task_counts", "core_counts", "worker_counts"):
            values = [
                _positive_int(value, f"scalability.{key}")
                for value in _as_list(scalability.get(key), f"scalability.{key}")
            ]
            if len(values) != len(set(values)):
                raise ConfigError(f"scalability.{key} contains duplicates")
            scalability[key] = values
        utilities = _validate_ratios(
            _as_list(scalability.get("utilization_points"), "scalability.utilization_points"),
            "scalability.utilization_points",
        )
        if len(utilities) != len(set(utilities)):
            raise ConfigError("scalability.utilization_points contains duplicates")
        scalability["utilization_points"] = utilities
        scale_variants = _as_list(scalability.get("variants"), "scalability.variants")
        if len(scale_variants) != len(set(scale_variants)) or any(
            value not in KNOWN_VARIANTS for value in scale_variants
        ):
            raise ConfigError("scalability.variants must contain unique known variants")
        if variants != scale_variants:
            raise ConfigError("analysis.variants must equal scalability.variants")
        scalability["variants"] = list(scale_variants)
        period_ranges = _as_list(
            scalability.get("period_ranges"), "scalability.period_ranges"
        )
        normalized_ranges = []
        range_ids = set()
        for index, value in enumerate(period_ranges):
            if not isinstance(value, dict) or set(value) != {"id", "min", "max"}:
                raise ConfigError("each period range requires exactly id, min, and max")
            range_id = value["id"]
            if not isinstance(range_id, str) or not range_id or range_id in range_ids:
                raise ConfigError("period-range IDs must be non-empty and unique")
            range_ids.add(range_id)
            lower = _positive_int(value["min"], f"scalability.period_ranges[{index}].min")
            upper = _positive_int(value["max"], f"scalability.period_ranges[{index}].max")
            if lower > upper:
                raise ConfigError("period-range min must not exceed max")
            normalized_ranges.append({"id": range_id, "min": lower, "max": upper})
        scalability["period_ranges"] = normalized_ranges
        hard_limit = scalability.get("max_analyses", 20)
        scalability["max_analyses"] = _positive_int(
            hard_limit, "scalability.max_analyses"
        )

    if core == "CORE-3":
        simulation = _require_mapping(config, "simulation")
        simulation["horizon"] = _positive_int(
            simulation.get("horizon"), "simulation.horizon"
        )
        warmup = simulation.get("warmup", 0)
        if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
            raise ConfigError("simulation.warmup must be a non-negative integer")
        simulation["warmup"] = warmup
        simulation["minimum_jobs_per_task"] = _positive_int(
            simulation.get("minimum_jobs_per_task"),
            "simulation.minimum_jobs_per_task",
        )
        simulation["maximum_horizon"] = _positive_int(
            simulation.get("maximum_horizon"), "simulation.maximum_horizon"
        )
        if simulation["maximum_horizon"] < simulation["horizon"]:
            raise ConfigError("simulation.maximum_horizon must cover horizon")
        if simulation["warmup"] >= simulation["maximum_horizon"]:
            raise ConfigError("simulation.warmup must be below maximum_horizon")
        if simulation.get("horizon_extension_policy") not in KNOWN_HORIZON_EXTENSION_POLICIES:
            raise ConfigError("unknown simulation.horizon_extension_policy")
        if simulation.get("trace_mode") not in KNOWN_TRACE_MODES:
            raise ConfigError("unknown simulation.trace_mode")
        for key in ("deadline_miss_fail_fast", "trace_on_failure"):
            if not isinstance(simulation.get(key), bool):
                raise ConfigError(f"simulation.{key} must be boolean")
        _positive_number(simulation.get("timeout_seconds"), "simulation.timeout_seconds")
        simulator_bin = simulation.get("simulator_bin", "./build/rtsim/rtsim")
        if not isinstance(simulator_bin, str) or not simulator_bin:
            raise ConfigError("simulation.simulator_bin must be a non-empty path")
        simulation["simulator_bin"] = simulator_bin

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
