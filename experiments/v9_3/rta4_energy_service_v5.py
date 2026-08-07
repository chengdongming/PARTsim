"""RTA V5 binding for the shared exact service-curve contract."""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping, Sequence

from experiments.common.exact_service_curve import (
    EXACT_LINEAR_SERVICE_CURVE_V1,
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
    ExactServiceCurve,
    ExactServiceCurveError,
    ExactServiceMaterial,
    domain_hash,
    fraction_text,
    materialize_exact_service_curve,
    normalize_exact_service_curve,
    scale_exact_service_curve,
)


RTA4_ENERGY_SERVICE_BINDING_V5 = (
    "ASAP_BLOCK_V9_3_RTA4_SHARED_EXACT_SERVICE_CURVE_BINDING_V5"
)
RTA4_CORE3_SIMULATION_PROJECTION_DOMAIN_V5 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_SIMULATION_PROJECTION:v5"
)
RTA4_CORE3_SIMULATION_PROJECTION_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_SIMULATION_PROJECTION:v6"
)


class RTA4EnergyServiceV5Error(ExactServiceCurveError):
    """RTA-facing name for a shared exact service contract failure."""


def normalize_energy_service_v5(raw: object) -> ExactServiceCurve:
    try:
        return normalize_exact_service_curve(raw)
    except ExactServiceCurveError as exc:
        raise RTA4EnergyServiceV5Error(str(exc)) from exc


def exact_service_material_v5(
    service: ExactServiceCurve, maximum_length: int,
) -> ExactServiceMaterial:
    try:
        return materialize_exact_service_curve(service, maximum_length)
    except ExactServiceCurveError as exc:
        raise RTA4EnergyServiceV5Error(str(exc)) from exc


def core3_simulation_projection_v5(
    *,
    exact_service_material_identity: str,
    harvest_trace: Sequence[Fraction],
    simulation_tick_ms: int,
) -> Mapping[str, Any]:
    """Bind exact per-tick energy to the simulator's millisecond power input.

    The service curve and its beta remain expressed in abstract integer ticks.
    Only this CORE-3 runtime projection maps one tick to wall-clock time.  All
    values remain exact rationals here; binary64 conversion is deferred until
    the unchanged simulator YAML is emitted.
    """

    if (
        type(exact_service_material_identity) is not str
        or len(exact_service_material_identity) != 64
        or any(
            character not in "0123456789abcdef"
            for character in exact_service_material_identity
        )
    ):
        raise RTA4EnergyServiceV5Error(
            "exact service material identity must be a lowercase SHA-256"
        )
    if type(simulation_tick_ms) is not int or simulation_tick_ms <= 0:
        raise RTA4EnergyServiceV5Error(
            "simulation_tick_ms must be a positive plain integer"
        )
    trace = tuple(harvest_trace)
    if any(type(value) is not Fraction or value < 0 for value in trace):
        raise RTA4EnergyServiceV5Error(
            "CORE-3 harvest trace must contain nonnegative Fractions"
        )
    segments: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(trace) + 1):
        if index == len(trace) or trace[index] != trace[start]:
            energy_per_tick = trace[start]
            power_w = energy_per_tick * 1000 / simulation_tick_ms
            segments.append({
                "start_tick": start,
                "end_tick": index,
                "start_ms": start * simulation_tick_ms,
                "end_ms": index * simulation_tick_ms,
                "energy_per_tick_j": fraction_text(energy_per_tick),
                "power_w": fraction_text(power_w),
            })
            start = index
    material = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_PROJECTION_V5",
        "exact_service_material_identity": exact_service_material_identity,
        "simulation_tick_ms": simulation_tick_ms,
        "conversion": "power_w=energy_per_tick_j*1000/simulation_tick_ms",
        "interval_contract": "tick_t_maps_to_[t*q,(t+1)*q)_milliseconds",
        "segments": segments,
    }
    return {
        **material,
        "simulation_projection_identity": domain_hash(
            RTA4_CORE3_SIMULATION_PROJECTION_DOMAIN_V5, material,
        ),
    }


def core3_simulation_projection_v6(
    *,
    exact_service_material_identity: str,
    harvest_trace: Sequence[Fraction],
    simulation_tick_ms: int,
    model_energy_unit_joules: str,
) -> Mapping[str, Any]:
    """Project abstract CORE-3 service units into physical simulator power."""

    if (
        type(exact_service_material_identity) is not str
        or len(exact_service_material_identity) != 64
        or any(
            character not in "0123456789abcdef"
            for character in exact_service_material_identity
        )
    ):
        raise RTA4EnergyServiceV5Error(
            "exact service material identity must be a lowercase SHA-256"
        )
    if simulation_tick_ms != 1:
        raise RTA4EnergyServiceV5Error(
            "CORE-3 V7 simulation_tick_ms must equal 1"
        )
    if type(model_energy_unit_joules) is not str:
        raise RTA4EnergyServiceV5Error(
            "model_energy_unit_joules must be a rational string"
        )
    try:
        scale = Fraction(model_energy_unit_joules)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4EnergyServiceV5Error(
            "model_energy_unit_joules is not rational"
        ) from exc
    if scale <= 0 or fraction_text(scale) != model_energy_unit_joules:
        raise RTA4EnergyServiceV5Error(
            "model_energy_unit_joules must be canonical and positive"
        )
    trace = tuple(harvest_trace)
    if any(type(value) is not Fraction or value < 0 for value in trace):
        raise RTA4EnergyServiceV5Error(
            "CORE-3 harvest trace must contain nonnegative Fractions"
        )
    segments: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(trace) + 1):
        if index == len(trace) or trace[index] != trace[start]:
            model_energy = trace[start]
            physical_energy = model_energy * scale
            power_w = physical_energy * 1000 / simulation_tick_ms
            segments.append({
                "start_tick": start,
                "end_tick": index,
                "start_ms": start * simulation_tick_ms,
                "end_ms": index * simulation_tick_ms,
                "model_energy_per_tick": fraction_text(model_energy),
                "model_energy_unit_joules": model_energy_unit_joules,
                "physical_energy_per_tick_j": fraction_text(physical_energy),
                "power_w": fraction_text(power_w),
            })
            start = index
    material = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_PROJECTION_V6",
        "exact_service_material_identity": exact_service_material_identity,
        "simulation_tick_ms": simulation_tick_ms,
        "model_energy_unit_joules": model_energy_unit_joules,
        "conversion": {
            "physical_energy_per_tick_j": (
                "model_energy_per_tick*model_energy_unit_joules"
            ),
            "power_w": (
                "physical_energy_per_tick_j*1000/simulation_tick_ms"
            ),
        },
        "interval_contract": (
            "tick_t_maps_to_[t*q,(t+1)*q)_milliseconds"
        ),
        "segments": segments,
    }
    return {
        **material,
        "simulation_projection_identity": domain_hash(
            RTA4_CORE3_SIMULATION_PROJECTION_DOMAIN_V6, material,
        ),
    }


__all__ = [
    "EXACT_LINEAR_SERVICE_CURVE_V1",
    "EXACT_RATE_LATENCY_SERVICE_CURVE_V1",
    "ExactServiceCurve",
    "ExactServiceMaterial",
    "RTA4_ENERGY_SERVICE_BINDING_V5",
    "RTA4_CORE3_SIMULATION_PROJECTION_DOMAIN_V5",
    "RTA4_CORE3_SIMULATION_PROJECTION_DOMAIN_V6",
    "RTA4EnergyServiceV5Error",
    "exact_service_material_v5",
    "core3_simulation_projection_v5",
    "core3_simulation_projection_v6",
    "normalize_energy_service_v5",
    "scale_exact_service_curve",
]
