"""Versioned numeric contract for formal RTA4 shared-energy V2."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from . import exact_energy
from .rta4_formal_config import domain_hash
from .rta4_shared_energy import (
    BETA_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    TASK_ENERGY_MATERIAL_SCHEMA,
)


RTA4_NUMERIC_CONTRACT_V2_NAME = "ASAP_BLOCK_V9_3_RTA4_SHARED_ENERGY_NUMERIC"
RTA4_NUMERIC_CONTRACT_V2_VERSION = "2"
RTA4_NUMERIC_CONTRACT_V2_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_NUMERIC_CONTRACT:v2"


RTA4_NUMERIC_CONTRACT_V2_MATERIAL: Dict[str, Any] = {
    "numeric_contract_name": RTA4_NUMERIC_CONTRACT_V2_NAME,
    "numeric_contract_version": RTA4_NUMERIC_CONTRACT_V2_VERSION,
    "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
    "g1_exact_energy_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
    "task_energy_material_schema": TASK_ENERGY_MATERIAL_SCHEMA,
    "task_energy_unit": "J/tick",
    "task_energy_source": "CANONICAL_SYSTEM_AND_WORKLOAD_OPERANDS",
    "task_energy_operation_order": (
        "binary64 base_power * workload_coefficient * frequency_ratio; "
        "binary64 (WCET * 0.001); multiply energy coefficient; divide by WCET; "
        "materialize with Fraction.from_float"
    ),
    "task_total_energy_divided_by_seconds_forbidden": True,
    "legacy_actual_power_field_forbidden_in_formal_v2": True,
    "service_material_schema": SERVICE_MATERIAL_SCHEMA,
    "service_unit": "J",
    "horizon_unit": "ticks",
    "service_source": "VERIFIED_CANONICAL_REAL_SOLAR_REPLAY",
    "beta_contract_version": BETA_CONTRACT_VERSION,
    "beta_rule": (
        "minimum binary64-left-to-right accumulated harvested joules over "
        "every complete arbitrary window of the queried tick length"
    ),
    "linear_service_scale_times_length_forbidden": True,
    "exact_fraction_materialization": "Fraction.from_float(binary64_value)",
    "e0_semantics": exact_energy.E0_ROUNDING_MODE,
    "float_decision_path": False,
}

RTA4_NUMERIC_CONTRACT_V2_SHA256 = domain_hash(
    RTA4_NUMERIC_CONTRACT_V2_DOMAIN,
    RTA4_NUMERIC_CONTRACT_V2_MATERIAL,
)


def numeric_contract_v2_metadata() -> Dict[str, Any]:
    return {
        **RTA4_NUMERIC_CONTRACT_V2_MATERIAL,
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
    }


def validate_numeric_contract_v2(value: Mapping[str, Any]) -> None:
    if dict(value) != numeric_contract_v2_metadata():
        raise ValueError("RTA4 V2 numeric contract mismatch")


__all__ = [
    "RTA4_NUMERIC_CONTRACT_V2_DOMAIN", "RTA4_NUMERIC_CONTRACT_V2_MATERIAL",
    "RTA4_NUMERIC_CONTRACT_V2_NAME", "RTA4_NUMERIC_CONTRACT_V2_SHA256",
    "RTA4_NUMERIC_CONTRACT_V2_VERSION", "numeric_contract_v2_metadata",
    "validate_numeric_contract_v2",
]
