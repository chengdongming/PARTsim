"""Fail-closed numeric contract for v9.3 energy decisions.

The production simulator materializes configured energy values as IEEE-754
binary64 numbers.  The RTA represents those materialized values exactly with
``Fraction.from_float`` before any schedulability arithmetic.  No decimal
round-trip is used to invent a nearby rational value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


THEORY_DOCUMENT_PATH = (
    "docs/asap_block_rta_四版本统一理论_CW_LOC_PH_SEQ_v2_复审修订.md"
)
THEORY_DOCUMENT_SHA256 = (
    "f8baf809e26852464447c8ca78e0b777c642b338b4ba928699b0d7cc6bfb5b2e"
)

NUMERIC_CONTRACT_NAME = "ASAP_BLOCK_V9_3_CONSERVATIVE_EXACT_ENERGY"
NUMERIC_CONTRACT_VERSION = "1"
SOURCE_NUMERIC_MODEL = "SIMULATOR_MATERIALIZED_IEEE754_BINARY64"
DEMAND_ROUNDING_MODE = "EXACT_MATERIALIZED_VALUE"
SUPPLY_ROUNDING_MODE = (
    "BINARY64_LEFT_TO_RIGHT_INTERVAL_ACCUMULATION_EXACT_MATERIALIZATION"
)
E0_ROUNDING_MODE = "EXACT_RATIONAL_LOWER_BOUND"
FLOAT_DECISION_PATH = False


class ExactEnergyError(ValueError):
    """Raised when an energy value cannot satisfy the frozen contract."""


class EnergyDirection(str, Enum):
    ENERGY_DEMAND_UPPER_BOUND = "ENERGY_DEMAND_UPPER_BOUND"
    ENERGY_SUPPLY_LOWER_BOUND = "ENERGY_SUPPLY_LOWER_BOUND"
    EXACT_MATERIALIZED_VALUE = "EXACT_MATERIALIZED_VALUE"


def fraction_text(value: Fraction) -> str:
    return (
        str(value.numerator)
        if value.denominator == 1
        else f"{value.numerator}/{value.denominator}"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


NUMERIC_CONTRACT_MATERIAL = {
    "numeric_contract_name": NUMERIC_CONTRACT_NAME,
    "numeric_contract_version": NUMERIC_CONTRACT_VERSION,
    "source_numeric_model": SOURCE_NUMERIC_MODEL,
    "task_demand_semantics": DEMAND_ROUNDING_MODE,
    "service_supply_semantics": SUPPLY_ROUNDING_MODE,
    "e0_semantics": E0_ROUNDING_MODE,
    "binary64_exact_conversion": "Fraction.from_float",
    "task_demand_accumulation": (
        "C++ order: base_power * workload_coefficient * frequency_ratio; "
        "multiply by (C * 0.001) and energy_coefficient; divide by C"
    ),
    "task_energy_coefficient": "binary64 1.0 scheduler default",
    "service_accumulation": (
        "for each candidate interval: binary64 zero, then left-to-right addition; "
        "Fraction.from_float of the materialized total"
    ),
    "demand_direction": EnergyDirection.ENERGY_DEMAND_UPPER_BOUND.value,
    "supply_direction": EnergyDirection.ENERGY_SUPPLY_LOWER_BOUND.value,
    "float_decision_path": FLOAT_DECISION_PATH,
}
NUMERIC_CONTRACT_SHA256 = hashlib.sha256(
    _canonical_json(NUMERIC_CONTRACT_MATERIAL).encode("utf-8")
).hexdigest()


def numeric_contract_metadata() -> dict[str, Any]:
    return {
        **NUMERIC_CONTRACT_MATERIAL,
        "numeric_contract_sha256": NUMERIC_CONTRACT_SHA256,
        "theory_document_path": THEORY_DOCUMENT_PATH,
        "theory_document_sha256": THEORY_DOCUMENT_SHA256,
        "demand_rounding_mode": DEMAND_ROUNDING_MODE,
        "supply_rounding_mode": SUPPLY_ROUNDING_MODE,
        "e0_rounding_mode": E0_ROUNDING_MODE,
    }


def verify_theory_document(project_root: Path) -> None:
    path = project_root / THEORY_DOCUMENT_PATH
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ExactEnergyError(
            f"cannot read frozen theory document: {path}"
        ) from exc
    if digest != THEORY_DOCUMENT_SHA256:
        raise ExactEnergyError(
            "frozen theory document SHA-256 mismatch: "
            f"expected {THEORY_DOCUMENT_SHA256}, got {digest}"
        )


@dataclass(frozen=True)
class MaterializedEnergy:
    exact_value: Fraction
    binary64_hex: str
    direction: EnergyDirection
    label: str

    def audit_material(self) -> dict[str, str]:
        return {
            "label": self.label,
            "binary64_hex": self.binary64_hex,
            "exact_value": fraction_text(self.exact_value),
            "direction": self.direction.value,
            "rounding": EXACT_MATERIALIZED_VALUE,
        }


def _materialized_binary64(
    value: Any,
    *,
    label: str,
    direction: EnergyDirection,
) -> MaterializedEnergy:
    if isinstance(value, bool) or type(value) is not float:
        raise ExactEnergyError(f"{label} must be a materialized binary64 float")
    if not math.isfinite(value):
        raise ExactEnergyError(f"{label} must be finite")
    if value < 0:
        raise ExactEnergyError(f"{label} must be non-negative")
    return MaterializedEnergy(
        Fraction.from_float(value), value.hex(), direction, label,
    )


def materialize_demand_upper_bound(value: Any, label: str) -> MaterializedEnergy:
    """Represent one simulator binary64 demand exactly; it cannot decrease."""

    return _materialized_binary64(
        value,
        label=label,
        direction=EnergyDirection.ENERGY_DEMAND_UPPER_BOUND,
    )


def materialize_task_demand_upper_bound(
    *,
    base_power: Any,
    workload_coefficient: Any,
    frequency_ratio: Any,
    wcet: Any,
    energy_coefficient: Any = 1.0,
    label: str,
) -> MaterializedEnergy:
    """Materialize the scheduler's per-task unit-energy operation order."""

    operands = {
        "base_power": base_power,
        "workload_coefficient": workload_coefficient,
        "frequency_ratio": frequency_ratio,
        "energy_coefficient": energy_coefficient,
    }
    for name, operand in operands.items():
        _materialized_binary64(
            operand,
            label=f"{label} {name}",
            direction=EnergyDirection.EXACT_MATERIALIZED_VALUE,
        )
    if (
        isinstance(wcet, bool)
        or not isinstance(wcet, int)
        or wcet <= 0
        or wcet > 2_147_483_647
    ):
        raise ExactEnergyError(
            f"{label} wcet must fit the positive C++ int domain"
        )

    # Keep these statements separate: this is the production C++ evaluation
    # order in ASAPBlockScheduler::calculateTotalEnergyForTask/addTask.
    power = base_power * workload_coefficient * frequency_ratio
    wcet_seconds = float(wcet) * 0.001
    total_energy = power * wcet_seconds
    total_energy *= energy_coefficient
    unit_energy = total_energy / float(wcet)
    return materialize_demand_upper_bound(unit_energy, label)


def materialize_supply_lower_bound(value: Any, label: str) -> MaterializedEnergy:
    """Represent one simulator binary64 supply exactly; it cannot increase."""

    return _materialized_binary64(
        value,
        label=label,
        direction=EnergyDirection.ENERGY_SUPPLY_LOWER_BOUND,
    )


def exact_e0_lower_bound(value: Any, label: str = "E0") -> Fraction:
    """Parse the theorem's exact release-energy guarantee without floats."""

    if isinstance(value, bool) or isinstance(value, float):
        raise ExactEnergyError(f"{label} must not be a binary float")
    if isinstance(value, Fraction):
        exact = value
    elif isinstance(value, int):
        exact = Fraction(value)
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ExactEnergyError(f"{label} must be finite")
        exact = Fraction(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ExactEnergyError(f"{label} must not be empty")
        try:
            exact = Fraction(text)
        except (ValueError, ZeroDivisionError) as exc:
            raise ExactEnergyError(f"{label} is not exact rational text") from exc
    else:
        raise ExactEnergyError(f"{label} must be exact rational data")
    if exact < 0:
        raise ExactEnergyError(f"{label} must be non-negative")
    return exact


def parse_persisted_fraction(value: Any, label: str) -> Fraction:
    """Read canonical JSON/CSV/YAML rational text without accepting floats."""

    exact = exact_e0_lower_bound(value, label)
    if isinstance(value, str) and value != fraction_text(exact):
        raise ExactEnergyError(f"{label} is not canonical fraction text")
    return exact


def service_curve_lower_bound(
    trace: Sequence[Fraction], maximum_delta: int,
) -> tuple[Fraction, ...]:
    """Build beta_l from the simulator's binary64 interval accumulation.

    Each candidate interval is accumulated from binary64 zero in chronological
    order, exactly matching the simulator's ``total += harvested`` operation.
    The resulting binary64 value is then represented without loss as a
    ``Fraction``.  All later service-curve and RTA arithmetic is rational.
    """

    if isinstance(maximum_delta, bool) or not isinstance(maximum_delta, int):
        raise ExactEnergyError("service maximum_delta must be an integer")
    if maximum_delta < 0 or maximum_delta > len(trace):
        raise ExactEnergyError("service maximum_delta is outside the trace")
    if any(type(value) is not Fraction or value < 0 for value in trace):
        raise ExactEnergyError(
            "service trace must contain non-negative exact Fractions"
        )

    binary64_trace: list[float] = []
    for index, value in enumerate(trace):
        materialized = float(value)
        if (
            not math.isfinite(materialized)
            or Fraction.from_float(materialized) != value
        ):
            raise ExactEnergyError(
                f"service trace value {index} is not an exact binary64 value"
            )
        binary64_trace.append(materialized)

    def interval_sum(start: int, delta: int) -> Fraction:
        total = 0.0
        for value in binary64_trace[start:start + delta]:
            total += value
            if not math.isfinite(total):
                raise ExactEnergyError("service interval accumulation overflowed")
        return Fraction.from_float(total)

    if all(left <= right for left, right in zip(trace, trace[1:])):
        result = tuple(
            interval_sum(0, delta)
            for delta in range(0, maximum_delta + 1)
        )
    else:
        values = [Fraction(0)]
        horizon = len(trace)
        for delta in range(1, maximum_delta + 1):
            values.append(min(
                interval_sum(start, delta)
                for start in range(0, horizon - delta + 1)
            ))
        result = tuple(values)

    if (
        not result
        or result[0] != 0
        or any(value < 0 for value in result)
        or any(left > right for left, right in zip(result, result[1:]))
    ):
        raise ExactEnergyError("constructed service curve violates its contract")
    return result


def exact_input_identity(
    *,
    task_powers: Iterable[tuple[str, Fraction]],
    e0: Fraction,
    service_prefix: Sequence[Fraction],
) -> str:
    material = {
        "numeric_contract_sha256": NUMERIC_CONTRACT_SHA256,
        "task_powers": [
            {"task_id": str(task_id), "P": fraction_text(power)}
            for task_id, power in task_powers
        ],
        "E0": fraction_text(exact_e0_lower_bound(e0)),
        "service_prefix": [fraction_text(value) for value in service_prefix],
    }
    return hashlib.sha256(
        b"ASAP_BLOCK:V9.3:EXACT_INPUT:v1\0"
        + _canonical_json(material).encode("utf-8")
    ).hexdigest()


def validate_numeric_contract_material(value: Mapping[str, Any]) -> None:
    expected = numeric_contract_metadata()
    if dict(value) != expected:
        raise ExactEnergyError("numeric contract metadata mismatch")
