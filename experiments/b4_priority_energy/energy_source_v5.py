"""Configurable B4-PE V5 energy-source material without scheduler changes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Mapping

import yaml


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
if str(B4_DIR) not in sys.path:
    # The historical B4 modules use sibling absolute imports.  Adding only
    # their own directory lets this new wrapper reuse them unchanged.
    sys.path.insert(0, str(B4_DIR))

from experiments.common.exact_service_curve import (  # noqa: E402
    ExactServiceCurve,
    canonical_nonnegative_rational,
    fraction_text,
    materialize_exact_service_curve,
    normalize_exact_service_curve,
    scale_exact_service_curve,
)

import materialization_common as legacy  # noqa: E402


B4_PE_THREE_STAGE_SOURCE_V1 = "B4_PE_THREE_STAGE_SOURCE_V1"
B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1 = (
    "B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1"
)
B4_PE_ENERGY_SOURCE_SCHEMA_V5 = "B4_PE_ENERGY_SOURCE_SCHEMA_V5"
B4_PE_ENERGY_SOURCE_CONFIG_DOMAIN_V5 = "B4-PE:ENERGY_SOURCE_CONFIG:v5"
B4_PE_SOURCE_MATERIAL_DOMAIN_V5 = "B4-PE:SOURCE_MATERIAL:v5"
B4_PE_SOURCE_ID_DOMAIN_V5 = "B4-PE:SOURCE_ID:v5"
B4_PE_ENERGY_BOUNDS_DOMAIN_V5 = "B4-PE:ENERGY_BOUNDS:v5"
B4_PE_TASK_ENERGY_MATERIAL_DOMAIN_V5 = "B4-PE:TASK_ENERGY_MATERIAL:v5"
B4_PE_CONFIGURED_ENERGY_SYSTEM_DOMAIN_V5 = (
    "B4-PE:CONFIGURED_ENERGY_SYSTEM:v5"
)
B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1 = (
    "SUPPLY_DEMAND_ENERGY_BOUNDS_V1"
)
B4_PE_RUNTIME_CONVERSION_V1 = (
    "EXACT_J_PER_TICK_TO_BINARY64_W_AT_EXPLICIT_TICK_MS_V1"
)
FROZEN_THREE_STAGE_LAMBDA = {"0.70", "0.85", "1.00", "1.15"}


class B4EnergySourceV5Error(ValueError):
    """Raised when a V5 source cannot be materialized unambiguously."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _field_set(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise B4EnergySourceV5Error(
            f"{label} field set mismatch; missing={sorted(expected-actual)}, "
            f"unknown={sorted(actual-expected)}"
        )
    return value


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise B4EnergySourceV5Error(f"{label} must be a positive integer")
    return value


def _positive_rational(value: Any, label: str) -> str:
    try:
        text = canonical_nonnegative_rational(value, label)
    except ValueError as exc:
        raise B4EnergySourceV5Error(str(exc)) from exc
    if Fraction(text) <= 0:
        raise B4EnergySourceV5Error(f"{label} must be positive")
    return text


def _sha(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise B4EnergySourceV5Error(f"{label} must be a lowercase SHA-256")
    return value


def validate_relative_path(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise B4EnergySourceV5Error(f"{label} must be a relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute() or ".." in path.parts or "." in path.parts
        or str(path) != value or "//" in value
    ):
        raise B4EnergySourceV5Error(f"{label} is not canonical and relative")
    return value


@dataclass(frozen=True)
class B4TasksetBindingV5:
    taskset_id: str
    taskset_identity: str
    base_taskset_path: str
    execution_taskset_path: str

    def material(self) -> dict[str, Any]:
        return {
            "taskset_id": self.taskset_id,
            "taskset_identity": self.taskset_identity,
            "base_taskset_path": self.base_taskset_path,
            "execution_taskset_path": self.execution_taskset_path,
        }


@dataclass(frozen=True)
class B4EnergySourceConfigV5:
    model: str
    horizon_ms: int
    tick_ms: int
    normalized_config: Mapping[str, Any]
    configuration_identity: str
    configured_curve: ExactServiceCurve | None
    effective_curve: ExactServiceCurve | None


@dataclass(frozen=True)
class B4SourceMaterialV5:
    taskset_identity: str
    source_id: str
    source_identity: str
    configured_energy_system_identity: str
    configured_energy_system_identity_scope: str
    configured_energy_system_descriptor: Mapping[str, Any]
    service_curve_identity: str | None
    energy_bounds_identity: str
    energy_bounds_descriptor: Mapping[str, Any]
    trace_sha256: str
    horizon_ms: int
    tick_ms: int
    initial_energy: Fraction
    max_energy: Fraction
    task_energy_scale: Fraction
    exact_segments: tuple[Mapping[str, Any], ...]
    runtime_source: Mapping[str, Any]
    descriptor: Mapping[str, Any]


@dataclass(frozen=True)
class B4TaskEnergyMaterialV5:
    """V5 demand-side task material, separate from harvested supply."""

    source_taskset_identity: str
    task_energy_scale: Fraction
    task_energy_material_identity: str
    artifact_relpath: str
    artifact_sha256: str
    artifact_bytes: bytes
    descriptor: Mapping[str, Any]


def normalize_taskset_binding_v5(raw: Any) -> B4TasksetBindingV5:
    row = _field_set(raw, {
        "taskset_id", "taskset_identity", "base_taskset_path",
        "execution_taskset_path",
    }, "taskset")
    taskset_id = row["taskset_id"]
    if (
        type(taskset_id) is not str
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", taskset_id) is None
    ):
        raise B4EnergySourceV5Error("taskset.taskset_id is not stable lowercase")
    return B4TasksetBindingV5(
        taskset_id,
        _sha(row["taskset_identity"], "taskset.taskset_identity"),
        validate_relative_path(row["base_taskset_path"], "base_taskset_path"),
        validate_relative_path(
            row["execution_taskset_path"], "execution_taskset_path"
        ),
    )


def normalize_energy_source_v5(
    raw: Any, *, horizon_ms: int, tick_ms: int,
) -> B4EnergySourceConfigV5:
    horizon_ms = _positive_int(horizon_ms, "horizon_ms")
    tick_ms = _positive_int(tick_ms, "tick_ms")
    if horizon_ms % tick_ms:
        raise B4EnergySourceV5Error("horizon_ms must be divisible by tick_ms")
    if not isinstance(raw, Mapping):
        raise B4EnergySourceV5Error("energy_source must be a mapping")
    model = raw.get("model")
    configured_curve: ExactServiceCurve | None = None
    effective_curve: ExactServiceCurve | None = None
    if model == B4_PE_THREE_STAGE_SOURCE_V1:
        row = _field_set(raw, {"model", "lambda_E"}, "energy_source")
        if horizon_ms != 30000 or tick_ms != 1:
            raise B4EnergySourceV5Error(
                "frozen three-stage source requires horizon_ms=30000 and tick_ms=1"
            )
        if row["lambda_E"] not in FROZEN_THREE_STAGE_LAMBDA:
            raise B4EnergySourceV5Error(
                "three-stage lambda_E is outside the frozen V4 levels"
            )
        normalized = {
            "schema": B4_PE_ENERGY_SOURCE_SCHEMA_V5,
            "model": B4_PE_THREE_STAGE_SOURCE_V1,
            "version": "1",
            "lambda_E": row["lambda_E"],
            "horizon_ms": horizon_ms,
            "tick_ms": tick_ms,
            "legacy_semantics": "B4_PE_V4_SOURCE_ENERGY_CONTRACT_UNMODIFIED",
            "automatic_scaling": False,
        }
    elif model == B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1:
        row = _field_set(raw, {
            "model", "service_curve", "service_scale", "task_energy_scale",
            "initial_energy", "max_energy",
        }, "energy_source")
        try:
            configured_curve = normalize_exact_service_curve(row["service_curve"])
            service_scale = _positive_rational(
                row["service_scale"], "energy_source.service_scale"
            )
            effective_curve = scale_exact_service_curve(
                configured_curve, service_scale,
            )
        except ValueError as exc:
            raise B4EnergySourceV5Error(str(exc)) from exc
        task_energy_scale = _positive_rational(
            row["task_energy_scale"], "energy_source.task_energy_scale"
        )
        initial = canonical_nonnegative_rational(
            row["initial_energy"], "energy_source.initial_energy"
        )
        maximum = canonical_nonnegative_rational(
            row["max_energy"], "energy_source.max_energy"
        )
        if Fraction(maximum) < Fraction(initial):
            raise B4EnergySourceV5Error("max_energy is below initial_energy")
        normalized = {
            "schema": B4_PE_ENERGY_SOURCE_SCHEMA_V5,
            "model": B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
            "version": "1",
            "configured_service_curve": dict(
                configured_curve.normalized_config
            ),
            "configured_service_curve_identity": configured_curve.identity,
            "service_scale": service_scale,
            "effective_service_curve": dict(effective_curve.normalized_config),
            "effective_service_curve_identity": effective_curve.identity,
            "task_energy_scale": task_energy_scale,
            "initial_energy": initial,
            "max_energy": maximum,
            "horizon_ms": horizon_ms,
            "tick_ms": tick_ms,
            "automatic_scaling": False,
            "runtime_conversion": B4_PE_RUNTIME_CONVERSION_V1,
        }
    else:
        raise B4EnergySourceV5Error("missing or unknown energy_source.model")
    return B4EnergySourceConfigV5(
        str(model),
        horizon_ms,
        tick_ms,
        normalized,
        _domain_hash(B4_PE_ENERGY_SOURCE_CONFIG_DOMAIN_V5, normalized),
        configured_curve,
        effective_curve,
    )


def _binary64_conversion(value: Fraction) -> dict[str, Any]:
    decimal_text = legacy.canonical_decimal(value)
    runtime = float(decimal_text)
    if runtime < 0 or runtime == float("inf"):
        raise B4EnergySourceV5Error("runtime conversion is not finite/nonnegative")
    return {
        "exact": fraction_text(value),
        "decimal_text": decimal_text,
        "binary64_hex": runtime.hex(),
        "runtime_value": runtime,
    }


def _segments_from_trace(
    trace: tuple[Fraction, ...], tick_ms: int,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    if not trace:
        raise B4EnergySourceV5Error("source trace must not be empty")
    runs: list[tuple[int, int, Fraction]] = []
    start = 0
    current = trace[0]
    for index, value in enumerate(trace[1:], start=1):
        if value != current:
            runs.append((start, index, current))
            start, current = index, value
    runs.append((start, len(trace), current))
    exact_segments = []
    runtime_segments = []
    for start_tick, end_tick, energy_per_tick in runs:
        power_w = energy_per_tick * 1000 / tick_ms
        conversion = _binary64_conversion(power_w)
        exact_segments.append({
            "start_time_ms": start_tick * tick_ms,
            "end_time_ms": end_tick * tick_ms,
            "energy_per_tick_j": fraction_text(energy_per_tick),
            "power_w": fraction_text(power_w),
            "runtime_power_w_decimal": conversion["decimal_text"],
            "runtime_power_w_binary64_hex": conversion["binary64_hex"],
        })
        runtime_segments.append({
            "start_ms": start_tick * tick_ms,
            "end_ms": end_tick * tick_ms,
            "multiplier": conversion["runtime_value"],
        })
    return tuple(exact_segments), {
        "source": "scaled_piecewise",
        "scaled_piecewise": {
            "scale_w": 1.0,
            "segments": runtime_segments,
        },
    }


def _trace_sha256(trace: tuple[Fraction, ...]) -> str:
    return hashlib.sha256(b"".join(
        (fraction_text(value) + "\n").encode("ascii") for value in trace
    )).hexdigest()


def _load_base_document(binding: B4TasksetBindingV5) -> Mapping[str, Any]:
    path = REPO_ROOT.joinpath(*PurePosixPath(binding.base_taskset_path).parts)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise B4EnergySourceV5Error(
            f"cannot read three-stage base taskset: {binding.base_taskset_path}"
        ) from exc
    try:
        return legacy.validate_base_taskset(document)
    except Exception as exc:
        raise B4EnergySourceV5Error(str(exc)) from exc


def build_task_energy_material_v5(
    taskset: B4TasksetBindingV5,
    task_energy_scale: str,
) -> B4TaskEnergyMaterialV5:
    """Multiply frozen rho-specific factors in the V5 materialization layer.

    The exact scale and exact products are scientific inputs.  Decimal and
    binary64 spellings are runtime projections only; schedulers remain
    unchanged and continue consuming their established task_energy_factor.
    """

    if type(taskset) is not B4TasksetBindingV5:
        raise B4EnergySourceV5Error("taskset has not been normalized")
    scale_text = _positive_rational(
        task_energy_scale, "energy_source.task_energy_scale"
    )
    scale = Fraction(scale_text)
    path = REPO_ROOT.joinpath(
        *PurePosixPath(taskset.execution_taskset_path).parts
    )
    try:
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != taskset.taskset_identity:
            raise B4EnergySourceV5Error(
                "execution taskset content identity drift"
            )
        document = yaml.safe_load(payload.decode("utf-8"))
    except B4EnergySourceV5Error:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise B4EnergySourceV5Error(
            "cannot read V5 execution taskset"
        ) from exc
    tasks = document.get("taskset") if isinstance(document, Mapping) else None
    if not isinstance(tasks, list) or not tasks:
        raise B4EnergySourceV5Error("execution taskset has no tasks")
    task_rows: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise B4EnergySourceV5Error("execution task entry is invalid")
        try:
            params = legacy.parse_canonical_task_params(
                task.get("params"), require_factor=True,
            )
            base = Fraction(params["task_energy_factor"])
        except Exception as exc:
            raise B4EnergySourceV5Error(str(exc)) from exc
        effective = base * scale
        runtime_text = legacy.canonical_decimal(effective)
        runtime_value = float(runtime_text)
        if runtime_value <= 0 or runtime_value == float("inf"):
            raise B4EnergySourceV5Error(
                "scaled task energy factor is outside runtime range"
            )
        updated = dict(params)
        updated["task_energy_factor"] = runtime_text
        task["params"] = ",".join(
            f"{key}={updated[key]}" for key in legacy.EXECUTION_PARAM_KEYS
        )
        task_rows.append({
            "task_index": index,
            "task_name": str(task.get("name")),
            "base_task_energy_factor": fraction_text(base),
            "task_energy_scale": scale_text,
            "effective_task_energy_factor": fraction_text(effective),
            "runtime_decimal": runtime_text,
            "runtime_binary64_hex": runtime_value.hex(),
        })
    material = {
        "schema": "B4_PE_TASK_ENERGY_MATERIAL_V5",
        "source_taskset_identity": taskset.taskset_identity,
        "task_energy_scale": scale_text,
        "tasks": task_rows,
        "automatic_scaling": False,
        "scale_source": "EXPLICIT_CAMPAIGN_FIELD_ONLY",
    }
    identity = _domain_hash(B4_PE_TASK_ENERGY_MATERIAL_DOMAIN_V5, material)
    rendered = legacy.canonical_yaml_bytes(document)
    artifact_sha = hashlib.sha256(rendered).hexdigest()
    relative = f"artifacts/b4_pe_v5/tasksets/{identity}.yml"
    descriptor = {
        **material,
        "task_energy_material_identity": identity,
        "artifact_relpath": relative,
        "artifact_sha256": artifact_sha,
    }
    return B4TaskEnergyMaterialV5(
        taskset.taskset_identity,
        scale,
        identity,
        relative,
        artifact_sha,
        rendered,
        descriptor,
    )


def build_source_material_v5(
    source: B4EnergySourceConfigV5,
    taskset: B4TasksetBindingV5,
    *,
    base_document: Mapping[str, Any] | None = None,
) -> B4SourceMaterialV5:
    if type(source) is not B4EnergySourceConfigV5:
        raise B4EnergySourceV5Error("source has not been normalized")
    if type(taskset) is not B4TasksetBindingV5:
        raise B4EnergySourceV5Error("taskset has not been normalized")
    tick_count = source.horizon_ms // source.tick_ms
    if source.model == B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1:
        curve = source.effective_curve
        if type(curve) is not ExactServiceCurve:
            raise B4EnergySourceV5Error("exact source lost its service curve")
        exact = materialize_exact_service_curve(curve, tick_count)
        trace = exact.harvest_trace
        trace_sha = exact.trace_sha256
        initial = Fraction(source.normalized_config["initial_energy"])
        maximum = Fraction(source.normalized_config["max_energy"])
        task_scale = Fraction(source.normalized_config["task_energy_scale"])
        model_material = {
            "configured_service_curve_identity": source.normalized_config[
                "configured_service_curve_identity"
            ],
            "effective_service_curve_identity": curve.identity,
            "exact_service_material_identity": exact.identity,
            "service_scale": source.normalized_config["service_scale"],
        }
        service_identity: str | None = curve.identity
    else:
        document = _load_base_document(taskset) if base_document is None else base_document
        try:
            energy = legacy.source_energy_contract(
                document, source.normalized_config["lambda_E"]
            )
        except Exception as exc:
            raise B4EnergySourceV5Error(str(exc)) from exc
        alpha = Fraction(energy["alpha_w"])
        trace = tuple(
            alpha * multiplier / 1000
            for count, multiplier in (
                (5000, Fraction(1)),
                (10000, Fraction(1, 5)),
                (15000, Fraction(1)),
            )
            for _ in range(count)
        )
        trace_sha = legacy.offered_harvest_trace_sha256(alpha)
        initial = Fraction(energy["E0_j"])
        maximum = Fraction(energy["Emax_j"])
        task_scale = Fraction(1)
        model_material = {
            "lambda_E": source.normalized_config["lambda_E"],
            "alpha_w": fraction_text(alpha),
            "nominal_demand_j": fraction_text(energy["nominal_demand_j"]),
            "legacy_trace_sha256": trace_sha,
            "base_taskset_semantic_sha256": hashlib.sha256(
                legacy.canonical_yaml_bytes(document)
            ).hexdigest(),
        }
        service_identity = None
    exact_segments, runtime_source = _segments_from_trace(trace, source.tick_ms)
    if source.model == B4_PE_THREE_STAGE_SOURCE_V1:
        # Preserve the V4 offered-trace identity while also recording the V5
        # canonical-token trace used by the general renderer.
        model_material["v5_canonical_trace_sha256"] = _trace_sha256(trace)
    bounds = {
        "initial_energy_j": fraction_text(initial),
        "max_energy_j": fraction_text(maximum),
    }
    bounds_identity = _domain_hash(B4_PE_ENERGY_BOUNDS_DOMAIN_V5, bounds)
    bounds_descriptor = {
        "schema": "B4_PE_ENERGY_BOUNDS_MATERIAL_V5",
        **bounds,
        "initial_energy_runtime": _binary64_conversion(initial),
        "max_energy_runtime": _binary64_conversion(maximum),
        "energy_bounds_identity": bounds_identity,
        "automatic_scaling": False,
    }
    supply_material = {
        "schema": "B4_PE_SOURCE_MATERIAL_V5",
        "source_model": source.model,
        "horizon_ms": source.horizon_ms,
        "tick_ms": source.tick_ms,
        "trace_sha256": trace_sha,
        "exact_segments": list(exact_segments),
        "runtime_source": runtime_source,
        "runtime_conversion": B4_PE_RUNTIME_CONVERSION_V1,
        "model_material": model_material,
        "automatic_scaling": False,
    }
    source_identity = _domain_hash(
        B4_PE_SOURCE_MATERIAL_DOMAIN_V5, supply_material
    )
    source_id = "src-v5-" + _domain_hash(B4_PE_SOURCE_ID_DOMAIN_V5, {
        "source_identity": source_identity,
    })
    descriptor = {
        **supply_material,
        "source_id": source_id,
        "source_identity": source_identity,
    }
    configured_energy_system_material = {
        "schema": "B4_PE_CONFIGURED_ENERGY_SYSTEM_V5",
        "version": "1",
        "identity_scope": B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1,
        "energy_source_configuration": deepcopy(dict(source.normalized_config)),
        "energy_source_configuration_identity": source.configuration_identity,
        "service_curve_identity": service_identity,
        "source_identity": source_identity,
        "task_energy_scale": fraction_text(task_scale),
        "energy_bounds_identity": bounds_identity,
        "initial_energy_j": fraction_text(initial),
        "max_energy_j": fraction_text(maximum),
        "horizon_ms": source.horizon_ms,
        "tick_ms": source.tick_ms,
        "algorithm_excluded": True,
    }
    configured_energy_system_identity = _domain_hash(
        B4_PE_CONFIGURED_ENERGY_SYSTEM_DOMAIN_V5,
        configured_energy_system_material,
    )
    configured_energy_system_descriptor = {
        **configured_energy_system_material,
        "configured_energy_system_identity": (
            configured_energy_system_identity
        ),
    }
    return B4SourceMaterialV5(
        taskset.taskset_identity,
        source_id,
        source_identity,
        configured_energy_system_identity,
        B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1,
        configured_energy_system_descriptor,
        service_identity,
        bounds_identity,
        bounds_descriptor,
        trace_sha,
        source.horizon_ms,
        source.tick_ms,
        initial,
        maximum,
        task_scale,
        exact_segments,
        runtime_source,
        descriptor,
    )


def render_system_config_v5(
    material: B4SourceMaterialV5, algorithm_cli: str,
) -> bytes:
    """Render the already-supported generic harvesting path for one algorithm."""

    if type(material) is not B4SourceMaterialV5:
        raise B4EnergySourceV5Error("source material is not normalized")
    if type(algorithm_cli) is not str or not algorithm_cli:
        raise B4EnergySourceV5Error("algorithm CLI identifier is required")
    try:
        document = yaml.safe_load(
            legacy.SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise B4EnergySourceV5Error("cannot read frozen system template") from exc
    document.pop("priority_energy", None)
    energy = document["energy_management"]
    for field in (
        "day_of_year", "time_of_day_ms", "base_harvesting_rate",
        "harvesting_scale", "use_real_solar_data", "solar_data_file",
        "pv_efficiency", "pv_area_m2", "start_offset_minutes",
    ):
        energy.pop(field, None)
    energy["initial_energy"] = float(legacy.canonical_decimal(material.initial_energy))
    energy["max_energy"] = float(legacy.canonical_decimal(material.max_energy))
    document["cpu_islands"][0]["kernel"]["scheduler"] = algorithm_cli
    document["harvesting"] = deepcopy(dict(material.runtime_source))
    try:
        from energy_manager import _normalise_harvest_source
        parsed = _normalise_harvest_source(document, {"enabled": False})
    except Exception as exc:
        raise B4EnergySourceV5Error(
            f"rendered generic harvest source is rejected: {exc}"
        ) from exc
    if parsed["kind"] != "scaled_piecewise":
        raise B4EnergySourceV5Error("rendered source kind drifted")
    return legacy.canonical_yaml_bytes(document)


__all__ = [
    "B4EnergySourceConfigV5",
    "B4EnergySourceV5Error",
    "B4SourceMaterialV5",
    "B4TaskEnergyMaterialV5",
    "B4TasksetBindingV5",
    "B4_PE_ENERGY_SOURCE_SCHEMA_V5",
    "B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1",
    "B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1",
    "B4_PE_THREE_STAGE_SOURCE_V1",
    "build_source_material_v5",
    "build_task_energy_material_v5",
    "normalize_energy_source_v5",
    "normalize_taskset_binding_v5",
    "render_system_config_v5",
    "validate_relative_path",
]
