from __future__ import annotations

from fractions import Fraction

import pytest

from experiments.common.exact_service_curve import (
    EXACT_LINEAR_SERVICE_CURVE_V1,
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
    ExactServiceCurveError,
    materialize_exact_service_curve,
    normalize_exact_service_curve,
    scale_exact_service_curve,
)


def test_exact_linear_curve_and_material_are_fraction_only():
    curve = normalize_exact_service_curve({
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": "1/10",
        "time_unit": "tick",
    })
    assert curve.beta(0) == 0
    assert curve.beta(3) == Fraction(3, 10)
    material = materialize_exact_service_curve(curve, 4)
    assert material.beta_prefix == (
        Fraction(0), Fraction(1, 10), Fraction(1, 5),
        Fraction(3, 10), Fraction(2, 5),
    )
    assert material.harvest_trace == (Fraction(1, 10),) * 4
    assert len(material.trace_sha256) == 64
    assert len(material.identity) == 64


def test_rate_latency_trace_proves_every_window_lower_bound():
    curve = normalize_exact_service_curve({
        "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
        "rate": "2/3",
        "latency": "3/2",
        "time_unit": "tick",
    })
    trace = curve.harvest_trace(12)
    assert trace[:4] == (
        Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(2, 3),
    )
    for length in range(13):
        for start in range(13 - length):
            assert sum(trace[start:start + length], Fraction(0)) >= curve.beta(length)


def test_scaling_changes_identity_but_not_latency_or_tick_semantics():
    curve = normalize_exact_service_curve({
        "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
        "rate": "1/10",
        "latency": "2",
        "time_unit": "tick",
    })
    scaled = scale_exact_service_curve(curve, "3/2")
    assert scaled.rate == Fraction(3, 20)
    assert scaled.latency == 2
    assert scaled.identity != curve.identity


@pytest.mark.parametrize("raw", [
    {
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": 0.1,
        "time_unit": "tick",
    },
    {
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": "2/4",
        "time_unit": "tick",
    },
    {
        "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
        "rate": "1",
        "latency": "-1",
        "time_unit": "tick",
    },
    {
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": "1",
        "time_unit": "ms",
    },
    {
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": "1",
        "time_unit": "tick",
        "implicit_default": True,
    },
])
def test_curve_contract_fails_closed(raw):
    with pytest.raises(ExactServiceCurveError):
        normalize_exact_service_curve(raw)


def test_boolean_lengths_are_rejected_as_non_plain_integers():
    curve = normalize_exact_service_curve({
        "model": EXACT_LINEAR_SERVICE_CURVE_V1,
        "rate": "0",
        "time_unit": "tick",
    })
    with pytest.raises(ExactServiceCurveError):
        curve.beta(True)
