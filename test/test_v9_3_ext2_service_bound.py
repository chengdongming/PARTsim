from fractions import Fraction

from experiments.v9_3.service_lower_bound import (
    construct_window_minimum_bound, validate_service_lower_bound,
)


def test_window_minimum_service_bound_is_conservative():
    energy = [Fraction(value) for value in (2, 1, 3, 2)]
    bound = construct_window_minimum_bound(energy, 3)
    result = validate_service_lower_bound(energy, bound, 3)
    assert bound == (0, 1, 3, 6)
    assert result["violation_count"] == 0
    assert result["status"] == "CERTIFIED_FINITE_SEGMENT_SERVICE_BOUND"


def test_service_bound_violation_is_never_silently_accepted():
    energy = [Fraction(1), Fraction(1)]
    result = validate_service_lower_bound(energy, [Fraction(0), Fraction(2)], 1)
    assert result["violation_count"] > 0
    assert result["status"] == "SERVICE_BOUND_VIOLATION"
