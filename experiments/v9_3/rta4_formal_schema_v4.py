"""Independent schema identity for exact versioned RTA4 V4 campaigns."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .rta4_energy_service_v4 import (
    EXACT_LINEAR_SERVICE_V1,
    VERIFIED_SHARED_ENERGY_MATERIAL_V1,
)
from .rta4_formal_config import domain_hash
from .rta4_formal_config_v4 import (
    RTA4_FORMAL_PROFILE_V4,
    RTA4_FORMAL_SCHEMA_VERSION_V4,
)
from .rta4_physical_core_slots_v3 import PHYSICAL_CORE_EXECUTION_BACKEND_V3
from .rta4_task_source_v4 import (
    EXPLICIT_TASKSET_MANIFEST,
    GENERAL_RANDOM_CONSTRAINED_V1,
    GENERATED_FAMILY,
    T10_BALANCED_V1,
)


RTA4_FORMAL_SCHEMA_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_SCHEMA:v4"


def formal_schema_material_v4() -> dict[str, Any]:
    return {
        "profile": RTA4_FORMAL_PROFILE_V4,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V4,
        "campaign_contract": {
            "unknown_fields": "REJECT",
            "missing_task_source": "REJECT",
            "missing_energy_service": "REJECT",
            "scientific_floats": "REJECT",
            "scientific_rationals": "CANONICAL_FRACTION_STRINGS",
            "task_source_modes": [
                EXPLICIT_TASKSET_MANIFEST, GENERATED_FAMILY,
            ],
            "formal_campaign_authorized": False,
        },
        "task_source_contract": {
            "explicit_manifest_file_bytes_bound": True,
            "explicit_manifest_semantics_bound": True,
            "task_order_bound": True,
            "per_taskset_content_certificate": True,
            "runtime_revalidation_required": True,
            "registered_family_only": True,
            "registered_families": [
                GENERAL_RANDOM_CONSTRAINED_V1, T10_BALANCED_V1,
            ],
            "implicit_family_defaults": False,
        },
        "energy_service_contract": {
            "explicit_model_required": True,
            "registered_models": [
                EXACT_LINEAR_SERVICE_V1,
                VERIFIED_SHARED_ENERGY_MATERIAL_V1,
            ],
            "exact_linear_service": EXACT_LINEAR_SERVICE_V1,
            "verified_shared_material_requires_runtime_binding": True,
            "legacy_binary64_formal_eligible": False,
            "implicit_solar_fallback": False,
        },
        "identity_bindings": [
            "raw_campaign_file_sha256",
            "normalized_scientific_config_sha256",
            "task_source_identity",
            "task_source_content_certificate_identity",
            "energy_service_identity",
            "source_closure_identity",
            "plan_sha256",
            "taskset_store_identity",
            "prepared_config_identity",
            "infrastructure_authorization_identity",
        ],
        "execution_backend": PHYSICAL_CORE_EXECUTION_BACKEND_V3,
        "method_family_independence_required": True,
        "legacy_profiles_accepted": False,
        "v3_namespace_reuse_allowed": False,
    }


@lru_cache(maxsize=1)
def formal_schema_hash_v4() -> str:
    return domain_hash(RTA4_FORMAL_SCHEMA_DOMAIN_V4, formal_schema_material_v4())


def formal_schema_manifest_v4() -> dict[str, Any]:
    material = formal_schema_material_v4()
    return {**material, "schema_sha256": formal_schema_hash_v4()}


__all__ = [
    "RTA4_FORMAL_SCHEMA_DOMAIN_V4", "formal_schema_hash_v4",
    "formal_schema_manifest_v4", "formal_schema_material_v4",
]
