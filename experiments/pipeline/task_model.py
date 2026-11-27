"""Exact sequential-task data and service-prefix validation shared by the simulator."""

from __future__ import annotations


from collections.abc import Sequence as SequenceABC


from dataclasses import dataclass


from decimal import Decimal


from enum import Enum


from fractions import Fraction


from typing import (
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)


ExactInput = Union[int, Fraction, Decimal, str]


class V93Error(Exception):
    """Base exception for the isolated v9.3 mathematical core."""


class V93InputError(V93Error, ValueError):
    """Raised when an input violates the v9.3 model preconditions."""


class V93NumericError(V93Error, ArithmeticError):
    """Raised when a value cannot enter an exact theorem-backed comparison."""


def exact_fraction_v9_3(value: ExactInput, label: str = "energy") -> Fraction:
    """Convert an exact input to ``Fraction`` without binary-float rounding.

    Integers, ``Fraction``, finite ``Decimal``, and rational/decimal strings
    are accepted.  ``float`` is intentionally rejected, including finite
    floats, so a theorem-backed caller must make its scaling choice explicit.
    """

    if isinstance(value, bool) or isinstance(value, float):
        raise V93NumericError(
            "{} must be exact; ordinary float/bool values are unsupported".format(
                label
            )
        )
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise V93NumericError("{} must be finite".format(label))
        return Fraction(value)
    if isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise V93NumericError(
                "{} is not an exact finite rational".format(label)
            ) from exc
    raise V93NumericError(
        "{} must be int, Fraction, Decimal, or an exact string".format(label)
    )


def _require_int(value: int, label: str, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise V93InputError("{} must be an integer".format(label))
    if minimum is not None and value < minimum:
        raise V93InputError("{} must be at least {}".format(label, minimum))
    return value


@dataclass(frozen=True)
class V93Task:
    """Restricted-deadline sequential sporadic task used by the v9.3 core."""

    name: str
    wcet: int
    deadline: int
    period: int
    power: ExactInput

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise V93InputError("task name must be a non-empty string")
        c_i = _require_int(self.wcet, "{}.wcet".format(self.name), 1)
        d_i = _require_int(self.deadline, "{}.deadline".format(self.name), 1)
        t_i = _require_int(self.period, "{}.period".format(self.name), 1)
        if not c_i <= d_i <= t_i:
            raise V93InputError(
                "{} must satisfy C <= D <= T".format(self.name)
            )
        try:
            exact_power = exact_fraction_v9_3(
                self.power, "{}.power".format(self.name)
            )
        except V93NumericError as exc:
            raise V93InputError(str(exc)) from exc
        if exact_power <= 0:
            raise V93InputError("{}.power must be positive".format(self.name))
        object.__setattr__(self, "power", exact_power)


ServiceCurve = Union[Callable[[int], ExactInput], Sequence[ExactInput]]


def validate_service_curve_v9_3(
    beta: ServiceCurve, required_horizon: int
) -> Tuple[Fraction, ...]:
    """Validate and freeze a theorem-backed service curve prefix.

    ``required_horizon`` is the largest service index that the caller may
    inspect.  Every value is read twice, converted through the approved exact
    numeric domain, and compared across the two passes.  Returning the frozen
    tuple prevents a stateful callback from changing after validation.
    """

    required_horizon = _require_int(
        required_horizon, "service-curve required horizon", 0
    )
    is_callback = callable(beta)
    if not is_callback and (
        isinstance(beta, (str, bytes, bytearray))
        or not isinstance(beta, SequenceABC)
    ):
        raise V93NumericError(
            "service curve must be a callback or a finite exact sequence"
        )
    if not is_callback and len(beta) <= required_horizon:
        raise V93NumericError(
            "service curve is undefined through required index {}".format(
                required_horizon
            )
        )

    def read_pass() -> Tuple[Fraction, ...]:
        values = []
        for length in range(required_horizon + 1):
            try:
                raw = beta(length) if is_callback else beta[length]
            except Exception as exc:
                raise V93NumericError(
                    "service curve callback/access failed at index {}".format(
                        length
                    )
                ) from exc
            values.append(
                exact_fraction_v9_3(raw, "beta({})".format(length))
            )
        return tuple(values)

    first = read_pass()
    second = read_pass()
    if first != second:
        raise V93NumericError(
            "service curve callback is non-deterministic on the required prefix"
        )
    if first[0] != 0:
        raise V93NumericError("service curve must satisfy beta(0) == 0")
    previous = first[0]
    for length, value in enumerate(first):
        if value < 0:
            raise V93NumericError(
                "beta({}) must be non-negative".format(length)
            )
        if length and value < previous:
            raise V93NumericError(
                "service curve must be monotone non-decreasing"
            )
        previous = value
    return first
