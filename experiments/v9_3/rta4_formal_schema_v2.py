"""Independent result schema for RTA4 formal shared-energy V2."""

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

from .rta4_formal_config import domain_hash
from .rta4_formal_config_v2 import RTA4_FORMAL_SCHEMA_VERSION_V2
from .rta4_formal_schema import FORMAL_TABLES as V1_FORMAL_TABLES


RTA4_FORMAL_SCHEMA_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_SCHEMA:v2"
RTA4_FORMAL_SCHEMA_MANIFEST_V2 = "formal_schema_manifest_v2_shared_energy.json"
SHARED_BINDINGS = (
    "production_build_manifest_identity",
    "task_energy_material_identity",
    "service_material_identity",
    "beta_material_identity",
)
V2_RESULT_ROW_REQUIRED_FIELDS = (
    "row_schema", "profile", "schema_sha256", "numeric_contract_sha256",
    "theory_document_sha256", "config_identity", "plan_identity",
    "plan_record_identity", "execution_identity",
    "production_build_manifest_identity", "source_commit", "source_tree",
    "taskset_source_sha256", "taskset_identity",
    "task_energy_material_identity", "service_material_identity",
    "beta_material_identity", "method", "exact_e0", "status",
    "response_result", "timeout_seconds", "attempts",
    "retry_resume_identity", "result_identity",
)
V2_ATTEMPT_REQUIRED_FIELDS = (
    "attempt_index", "timeout_seconds", "status",
    "runtime_wall_seconds", "runtime_cpu_seconds", "peak_rss_bytes",
    "error_classification", "analysis_identity", "taskset_identity",
    "task_energy_material_identity", "service_material_identity",
    "beta_material_identity", "production_build_manifest_identity",
)


def _v2_columns(name: str, columns: Tuple[str, ...]) -> Tuple[str, ...]:
    result = list(columns)
    if "schema_version" in result:
        insertion = result.index("config_semantic_hash") + 1
        result[insertion:insertion] = list(SHARED_BINDINGS)
    replacements = {
        "P_exact": (
            "workload", "energy_j_per_tick", "task_energy_source_identity",
        ),
        "power_vector_hash": ("task_energy_material_identity_ref",),
        "service_identity": (
            "service_material_identity_ref", "beta_material_identity_ref",
        ),
        "service_harvest_identity": (
            "semantic_service_source_identity",
            "service_material_identity_ref",
        ),
    }
    rewritten = []
    for column in result:
        rewritten.extend(replacements.get(column, (column,)))
    # Avoid duplicate identity columns introduced by replacements/common binding.
    unique = []
    for column in rewritten:
        if column not in unique:
            unique.append(column)
    return tuple(unique)


_TABLES_V2: Dict[str, Tuple[str, ...]] = {
    name: _v2_columns(name, columns)
    for name, columns in V1_FORMAL_TABLES.items()
}
FORMAL_TABLES_V2: Mapping[str, Tuple[str, ...]] = MappingProxyType(_TABLES_V2)


def formal_schema_material_v2() -> Dict[str, Any]:
    return {
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V2,
        "ordered_tables": [
            {"filename": name, "ordered_columns": list(columns)}
            for name, columns in FORMAL_TABLES_V2.items()
        ],
        "unit_contract": {
            "energy_demand": "J/tick",
            "service": "J",
            "horizon": "ticks",
        },
        "required_shared_bindings": list(SHARED_BINDINGS),
        "result_row_required_fields": list(V2_RESULT_ROW_REQUIRED_FIELDS),
        "attempt_required_fields": list(V2_ATTEMPT_REQUIRED_FIELDS),
        "result_lifecycle": {
            "atomic_terminal_json": True,
            "duplicate_consistency": True,
            "resume_revalidates_frozen_identities": True,
            "attempt_order": "STRICT_ZERO_BASED",
        },
        "legacy_v1_rows_accepted": False,
        "legacy_actual_power_column_accepted": False,
        "linear_beta_material_accepted": False,
        "missing_telemetry_encoding": "NA_WITH_TELEMETRY_STATUS",
    }


@lru_cache(maxsize=1)
def formal_schema_hash_v2() -> str:
    return domain_hash(RTA4_FORMAL_SCHEMA_DOMAIN_V2, formal_schema_material_v2())


def formal_schema_manifest_v2() -> Dict[str, Any]:
    material = formal_schema_material_v2()
    return {**material, "schema_sha256": formal_schema_hash_v2()}


def validate_formal_schema_manifest_v2(value: Mapping[str, Any]) -> None:
    if dict(value) != formal_schema_manifest_v2():
        raise ValueError("RTA4 formal V2 schema mismatch")


__all__ = [
    "FORMAL_TABLES_V2", "RTA4_FORMAL_SCHEMA_DOMAIN_V2",
    "RTA4_FORMAL_SCHEMA_MANIFEST_V2", "SHARED_BINDINGS",
    "V2_ATTEMPT_REQUIRED_FIELDS", "V2_RESULT_ROW_REQUIRED_FIELDS",
    "formal_schema_hash_v2", "formal_schema_manifest_v2",
    "formal_schema_material_v2", "validate_formal_schema_manifest_v2",
]
