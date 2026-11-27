"""Scale-aware energy conservation validation for schema-3 traces."""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping

from .canonical_identity import domain_hash, fraction_text


CORE3_ENERGY_CONSERVATION_MODEL_V1 = (
    "ABSOLUTE_PLUS_RELATIVE_SCALE_AWARE_V1"
)
CORE3_ENERGY_CONSERVATION_SCALE_V1 = "MAX_ONE_ABS_LHS_ABS_RHS"
CORE3_ENERGY_CONSERVATION_ABSOLUTE_TOLERANCE_J_V1 = "1/100000000"
CORE3_ENERGY_CONSERVATION_RELATIVE_TOLERANCE_V1 = "1/1000000000000"
CORE3_ENERGY_CONSERVATION_RULE_DOMAIN_V1 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_ENERGY_CONSERVATION_RULE:v1"
)
CORE3_SIMULATION_CONTRACT_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_SIMULATION_CONTRACT:v6"
)

class RTA4Core3ContractV6Error(ValueError):
    """Raised before an ambiguous CORE-3 V6 contract can be issued."""


def _canonical_positive_fraction(
    value: Any, label: str, *, maximum: Fraction,
) -> str:
    if type(value) is not str:
        raise RTA4Core3ContractV6Error(
            f"{label} must be a canonical positive rational string"
        )
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4Core3ContractV6Error(f"{label} is not rational") from exc
    if exact <= 0 or exact > maximum or fraction_text(exact) != value:
        raise RTA4Core3ContractV6Error(
            f"{label} must be canonical, positive, and no greater than "
            f"{fraction_text(maximum)}"
        )
    return value


def normalize_core3_energy_conservation_rule_v1(
    value: Any,
) -> dict[str, Any]:
    """Validate and identity-bind the two-equation conservation rule."""

    fields = {
        "model", "absolute_tolerance_j", "relative_tolerance", "scale",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RTA4Core3ContractV6Error(
            "CORE-3 energy conservation rule field set mismatch"
        )
    if value["model"] != CORE3_ENERGY_CONSERVATION_MODEL_V1:
        raise RTA4Core3ContractV6Error(
            "unsupported CORE-3 energy conservation model"
        )
    if value["scale"] != CORE3_ENERGY_CONSERVATION_SCALE_V1:
        raise RTA4Core3ContractV6Error(
            "unsupported CORE-3 energy conservation scale"
        )
    material = {
        "model": CORE3_ENERGY_CONSERVATION_MODEL_V1,
        "absolute_tolerance_j": _canonical_positive_fraction(
            value["absolute_tolerance_j"],
            "energy conservation absolute_tolerance_j",
            maximum=Fraction(
                CORE3_ENERGY_CONSERVATION_ABSOLUTE_TOLERANCE_J_V1
            ),
        ),
        "relative_tolerance": _canonical_positive_fraction(
            value["relative_tolerance"],
            "energy conservation relative_tolerance",
            maximum=Fraction(
                CORE3_ENERGY_CONSERVATION_RELATIVE_TOLERANCE_V1
            ),
        ),
        "scale": CORE3_ENERGY_CONSERVATION_SCALE_V1,
    }
    return {
        **material,
        "rule_identity": domain_hash(
            CORE3_ENERGY_CONSERVATION_RULE_DOMAIN_V1, material,
        ),
    }


def require_normalized_core3_energy_conservation_rule_v1(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4Core3ContractV6Error(
            "CORE-3 energy conservation rule is not a mapping"
        )
    raw = {key: item for key, item in value.items() if key != "rule_identity"}
    normalized = normalize_core3_energy_conservation_rule_v1(raw)
    if dict(value) != normalized:
        raise RTA4Core3ContractV6Error(
            "CORE-3 energy conservation rule identity drift"
        )
    return normalized


def core3_energy_conservation_close_v1(
    lhs: float, rhs: float, rule: Mapping[str, Any],
) -> bool:
    """Apply the scale-aware rule only where the caller selects it."""

    normalized = require_normalized_core3_energy_conservation_rule_v1(rule)
    absolute = float(Fraction(normalized["absolute_tolerance_j"]))
    relative = float(Fraction(normalized["relative_tolerance"]))
    scale = max(1.0, abs(lhs), abs(rhs))
    return abs(lhs - rhs) <= absolute + relative * scale
