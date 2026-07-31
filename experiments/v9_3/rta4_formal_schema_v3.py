"""Independent schema identity for parameterized RTA4 V3 campaigns."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict

from .rta4_formal_config import domain_hash
from .rta4_formal_config_v3 import (
    RTA4_FORMAL_PROFILE_V3,
    RTA4_FORMAL_SCHEMA_VERSION_V3,
)


RTA4_FORMAL_SCHEMA_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_SCHEMA:v3"


def formal_schema_material_v3() -> Dict[str, Any]:
    return {
        "profile": RTA4_FORMAL_PROFILE_V3,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V3,
        "campaign_contract": {
            "unknown_fields": "REJECT",
            "scientific_floats": "REJECT",
            "scientific_rationals": "CANONICAL_FRACTION_STRINGS",
            "method_order": "OFFICIAL_ALLOWLIST_ORDER",
            "scientific_runtime_separation": True,
            "external_campaign_in_production_source_closure": False,
        },
        "identity_bindings": [
            "raw_campaign_file_sha256",
            "normalized_scientific_config_sha256",
            "plan_sha256",
            "ordered_stream_digest",
            "production_build_manifest_identity",
            "prepared_config_id",
            "authorization_id",
            "taskset_store_identity",
            "source_campaign_identity",
        ],
        "dynamic_count_contract": "EXPECTED_EQUALS_ENUMERATED_FAIL_CLOSED",
        "legacy_profiles_accepted": False,
    }


@lru_cache(maxsize=1)
def formal_schema_hash_v3() -> str:
    return domain_hash(RTA4_FORMAL_SCHEMA_DOMAIN_V3, formal_schema_material_v3())


def formal_schema_manifest_v3() -> Dict[str, Any]:
    material = formal_schema_material_v3()
    return {**material, "schema_sha256": formal_schema_hash_v3()}


__all__ = [
    "RTA4_FORMAL_SCHEMA_DOMAIN_V3", "formal_schema_hash_v3",
    "formal_schema_manifest_v3", "formal_schema_material_v3",
]
