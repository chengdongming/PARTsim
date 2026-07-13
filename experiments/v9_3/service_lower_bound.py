"""Finite-segment conservative service lower-bound construction and audit."""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Sequence, Tuple

from .config import fraction_text


def construct_window_minimum_bound(
    interval_energy: Sequence[Fraction], applicable_horizon: int
) -> Tuple[Fraction, ...]:
    if applicable_horizon <= 0 or applicable_horizon > len(interval_energy):
        raise ValueError("applicable horizon must lie within the trace")
    if any(value < 0 for value in interval_energy):
        raise ValueError("service samples must be non-negative")
    bound = [Fraction(0)]
    for length in range(1, applicable_horizon + 1):
        windows = [
            sum(interval_energy[start:start + length], Fraction(0))
            for start in range(0, len(interval_energy) - length + 1)
        ]
        bound.append(min(windows))
    return tuple(bound)


def validate_service_lower_bound(
    interval_energy: Sequence[Fraction],
    lower_bound: Sequence[Fraction],
    applicable_horizon: int,
) -> Dict[str, Any]:
    if len(lower_bound) <= applicable_horizon:
        raise ValueError("service lower bound does not cover applicable horizon")
    checked = 0
    violations = 0
    minimum_slack = None
    for length in range(0, applicable_horizon + 1):
        for start in range(0, len(interval_energy) - length + 1):
            actual = sum(interval_energy[start:start + length], Fraction(0))
            slack = actual - lower_bound[length]
            checked += 1
            minimum_slack = slack if minimum_slack is None else min(minimum_slack, slack)
            if slack < 0:
                violations += 1
    return {
        "construction_method": "WINDOW_MINIMUM_FINITE_SEGMENT_V1",
        "applicable_horizon_intervals": applicable_horizon,
        "validated_interval_count": checked,
        "minimum_slack_j": fraction_text(minimum_slack or Fraction(0)),
        "violation_count": violations,
        "status": "CERTIFIED_FINITE_SEGMENT_SERVICE_BOUND" if violations == 0 else "SERVICE_BOUND_VIOLATION",
    }
