"""Exact, configurable service curves shared by RTA V5 and B4-PE V5.

The public contract is intentionally small.  Scientific values are canonical
``fractions.Fraction`` strings and are never accepted as binary64 numbers.
Runtime-specific conversions are performed by the caller and must retain the
exact material and its identity next to any converted representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import re
from typing import Any, Mapping


EXACT_LINEAR_SERVICE_CURVE_V1 = "EXACT_LINEAR_SERVICE_CURVE_V1"
EXACT_RATE_LATENCY_SERVICE_CURVE_V1 = "EXACT_RATE_LATENCY_SERVICE_CURVE_V1"
EXACT_SERVICE_CURVE_SCHEMA_V1 = "PARTSIM_EXACT_SERVICE_CURVE_V1"
EXACT_SERVICE_CURVE_DOMAIN_V1 = "PARTSIM:EXACT_SERVICE_CURVE:v1"
EXACT_SERVICE_MATERIAL_DOMAIN_V1 = "PARTSIM:EXACT_SERVICE_MATERIAL:v1"
EXACT_SERVICE_TRACE_DOMAIN_V1 = "PARTSIM:EXACT_SERVICE_TRACE:v1"
EXACT_SERVICE_TIME_UNIT_V1 = "tick"

_CANONICAL_RATIONAL = re.compile(r"(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?")


class ExactServiceCurveError(ValueError):
    """Raised when a service curve or its exact material is ambiguous."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def domain_hash(domain: str, value: Any) -> str:
    if type(domain) is not str or not domain:
        raise ExactServiceCurveError("identity domain must be non-empty")
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def fraction_text(value: Fraction | int) -> str:
    exact = value if isinstance(value, Fraction) else Fraction(value)
    return (
        str(exact.numerator)
        if exact.denominator == 1
        else f"{exact.numerator}/{exact.denominator}"
    )


def canonical_nonnegative_rational(value: Any, label: str) -> str:
    """Return a canonical nonnegative rational string or fail closed."""

    if type(value) is not str or _CANONICAL_RATIONAL.fullmatch(value) is None:
        raise ExactServiceCurveError(
            f"{label} must be a canonical nonnegative rational string"
        )
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ExactServiceCurveError(f"{label} is not rational") from exc
    canonical = fraction_text(exact)
    if exact < 0 or canonical != value:
        raise ExactServiceCurveError(f"{label} must be canonical: {canonical}")
    return canonical


def _plain_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ExactServiceCurveError(
            f"{label} must be a nonnegative plain integer"
        )
    return value


def _field_set(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise ExactServiceCurveError(
            f"{label} field set mismatch; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return value


@dataclass(frozen=True)
class ExactServiceCurve:
    """A normalized exact lower service curve over integer tick lengths."""

    model: str
    rate: Fraction
    latency: Fraction
    normalized_config: Mapping[str, Any]
    identity: str

    def beta(self, length: int) -> Fraction:
        length = _plain_nonnegative_int(length, "service length")
        available = Fraction(length) - self.latency
        return self.rate * max(Fraction(0), available)

    def prefix(self, maximum_length: int) -> tuple[Fraction, ...]:
        maximum_length = _plain_nonnegative_int(
            maximum_length, "maximum service length"
        )
        return tuple(self.beta(length) for length in range(maximum_length + 1))

    def harvest_trace(self, horizon: int) -> tuple[Fraction, ...]:
        """Return exact increments available at decision ticks ``1..horizon``.

        For both registered curves these increments are nondecreasing.  Hence
        every length-L window contains at least the first L increments, whose
        sum is beta(L).  This is the arbitrary-window lower-bound proof used by
        the shared contract, not merely a prefix-only observation.
        """

        prefix = self.prefix(horizon)
        trace = tuple(right - left for left, right in zip(prefix, prefix[1:]))
        if any(value < 0 for value in trace):
            raise ExactServiceCurveError("service trace contains negative energy")
        if any(left > right for left, right in zip(trace, trace[1:])):
            raise ExactServiceCurveError(
                "registered service no longer proves every-window dominance"
            )
        return trace


@dataclass(frozen=True)
class ExactServiceMaterial:
    curve_identity: str
    maximum_length: int
    beta_prefix: tuple[Fraction, ...]
    harvest_trace: tuple[Fraction, ...]
    trace_sha256: str
    identity: str

    def material(self) -> dict[str, Any]:
        return {
            "schema": "PARTSIM_EXACT_SERVICE_MATERIAL_V1",
            "curve_identity": self.curve_identity,
            "maximum_length": self.maximum_length,
            "beta_prefix": [fraction_text(value) for value in self.beta_prefix],
            "harvest_trace": [fraction_text(value) for value in self.harvest_trace],
            "trace_sha256": self.trace_sha256,
            "material_identity": self.identity,
        }


def normalize_exact_service_curve(raw: Any) -> ExactServiceCurve:
    if not isinstance(raw, Mapping):
        raise ExactServiceCurveError("service_curve must be a mapping")
    model = raw.get("model")
    if model == EXACT_LINEAR_SERVICE_CURVE_V1:
        row = _field_set(raw, {"model", "rate", "time_unit"}, "service_curve")
        latency_text = "0"
    elif model == EXACT_RATE_LATENCY_SERVICE_CURVE_V1:
        row = _field_set(
            raw, {"model", "rate", "latency", "time_unit"}, "service_curve"
        )
        latency_text = canonical_nonnegative_rational(
            row["latency"], "service_curve.latency"
        )
    else:
        raise ExactServiceCurveError("missing or unknown service_curve.model")
    if row["time_unit"] != EXACT_SERVICE_TIME_UNIT_V1:
        raise ExactServiceCurveError("service_curve.time_unit must equal 'tick'")
    rate_text = canonical_nonnegative_rational(row["rate"], "service_curve.rate")
    normalized = {
        "schema": EXACT_SERVICE_CURVE_SCHEMA_V1,
        "model": model,
        "version": "1",
        "rate": rate_text,
        "latency": latency_text,
        "time_unit": EXACT_SERVICE_TIME_UNIT_V1,
        "value_unit": "exact_energy",
        "beta_contract": "rate*max(0,integer_length-latency)",
        "arbitrary_window_contract": "NONDECREASING_INCREMENT_SEQUENCE_V1",
        "scientific_float_inputs_allowed": False,
    }
    return ExactServiceCurve(
        str(model),
        Fraction(rate_text),
        Fraction(latency_text),
        normalized,
        domain_hash(EXACT_SERVICE_CURVE_DOMAIN_V1, normalized),
    )


def scale_exact_service_curve(
    curve: ExactServiceCurve, factor: Any,
) -> ExactServiceCurve:
    """Scale service energy only; latency and tick semantics remain fixed."""

    if type(curve) is not ExactServiceCurve:
        raise ExactServiceCurveError("curve must already be normalized")
    factor_text = canonical_nonnegative_rational(factor, "service scale")
    scaled_rate = fraction_text(curve.rate * Fraction(factor_text))
    raw: dict[str, Any] = {
        "model": curve.model,
        "rate": scaled_rate,
        "time_unit": EXACT_SERVICE_TIME_UNIT_V1,
    }
    if curve.model == EXACT_RATE_LATENCY_SERVICE_CURVE_V1:
        raw["latency"] = fraction_text(curve.latency)
    return normalize_exact_service_curve(raw)


def materialize_exact_service_curve(
    curve: ExactServiceCurve, maximum_length: int,
) -> ExactServiceMaterial:
    if type(curve) is not ExactServiceCurve:
        raise ExactServiceCurveError("curve must already be normalized")
    maximum_length = _plain_nonnegative_int(
        maximum_length, "maximum service length"
    )
    prefix = curve.prefix(maximum_length)
    trace = curve.harvest_trace(maximum_length)
    trace_bytes = b"".join(
        (fraction_text(value) + "\n").encode("ascii") for value in trace
    )
    trace_sha = hashlib.sha256(trace_bytes).hexdigest()
    base = {
        "schema": "PARTSIM_EXACT_SERVICE_MATERIAL_V1",
        "curve_identity": curve.identity,
        "maximum_length": maximum_length,
        "beta_prefix": [fraction_text(value) for value in prefix],
        "harvest_trace": [fraction_text(value) for value in trace],
        "trace_sha256": trace_sha,
    }
    return ExactServiceMaterial(
        curve.identity,
        maximum_length,
        prefix,
        trace,
        trace_sha,
        domain_hash(EXACT_SERVICE_MATERIAL_DOMAIN_V1, base),
    )


__all__ = [
    "EXACT_LINEAR_SERVICE_CURVE_V1",
    "EXACT_RATE_LATENCY_SERVICE_CURVE_V1",
    "EXACT_SERVICE_CURVE_DOMAIN_V1",
    "EXACT_SERVICE_CURVE_SCHEMA_V1",
    "EXACT_SERVICE_MATERIAL_DOMAIN_V1",
    "EXACT_SERVICE_TIME_UNIT_V1",
    "ExactServiceCurve",
    "ExactServiceCurveError",
    "ExactServiceMaterial",
    "canonical_json",
    "canonical_nonnegative_rational",
    "domain_hash",
    "fraction_text",
    "materialize_exact_service_curve",
    "normalize_exact_service_curve",
    "scale_exact_service_curve",
]
