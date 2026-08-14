"""Independent schema identity for selectable-service RTA V5 campaigns."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from experiments.common.exact_service_curve import (
    EXACT_LINEAR_SERVICE_CURVE_V1,
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
)

from .rta4_formal_config import domain_hash
from .rta4_formal_config_v5 import (
    CORE5A_TIME_SERVICE_SEMANTICS_V5,
    RTA4_FORMAL_PROFILE_V5,
    RTA4_FORMAL_SCHEMA_VERSION_V5,
)
from .rta4_task_source_v4 import (
    EXPLICIT_TASKSET_MANIFEST,
    GENERATED_FAMILY,
)


RTA4_FORMAL_SCHEMA_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_SCHEMA:v5"


def formal_schema_material_v5() -> dict[str, Any]:
    return {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V5,
        "campaign_contract": {
            "supported_cores": [
                "CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A",
                "CORE-5B",
            ],
            "unknown_fields": "REJECT",
            "scientific_floats": "REJECT",
            "scientific_rationals": "CANONICAL_FRACTION_STRINGS",
            "direct_local_execution": True,
            "resume_configuration_checked": True,
        },
        "reuse_contract": {
            "plan_grid": "RTA4_FORMAL_PLAN_V3_UNMODIFIED",
            "task_source": "RTA4_TASK_SOURCE_V4_UNMODIFIED",
            "rta_math_kernel": "UNMODIFIED",
            "legacy_profiles_accepted": False,
            "legacy_identity_domains_reused": False,
        },
        "service_curve_contract": {
            "common_schema": "PARTSIM_EXACT_SERVICE_CURVE_V1",
            "models": [
                EXACT_LINEAR_SERVICE_CURVE_V1,
                EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
            ],
            "time_unit": "tick",
            "arbitrary_window_lower_bound_required": True,
            "float_conversion_is_scientific_identity": False,
            "implicit_default": False,
        },
        "task_source_contract": {
            "modes": [EXPLICIT_TASKSET_MANIFEST, GENERATED_FAMILY],
            "content_and_order_hashes_bound": True,
            "all_six_cores_bound": True,
            "core5a_per_axis_source_required": True,
        },
        "core_contracts": {
            "CORE-2": "V3_SOURCE_PLUS_V4_CONTENT_PLUS_SERVICE_DEPENDENCY",
            "CORE-3": {
                "paired_track_service": (
                    "SAME_EXACT_PREFIX_AND_TRACE_FOR_PAIRED_TRACKS"
                ),
                "simulation_tick_ms": (
                    "REQUIRED_EXPLICIT_POSITIVE_PLAIN_INTEGER"
                ),
                "runtime_conversion": (
                    "power_w=energy_per_tick_j*1000/simulation_tick_ms"
                ),
                "beta_scaled_by_simulation_tick_ms": False,
            },
            "CORE-4": "V3_ONE_FACTOR_AT_A_TIME",
            "CORE-5A": {
                "time_service_semantics": list(
                    CORE5A_TIME_SERVICE_SEMANTICS_V5
                ),
                "task_power_scaled_with_time": False,
            },
            "CORE-5B": {
                "worker_count": "EXCLUDED_FROM_MATH_IDENTITY",
                "source_baseline_exact_e0": (
                    "REQUIRED_EXPLICIT_CANONICAL_NONNEGATIVE_RATIONAL"
                ),
            },
        },
        "automatic_scaling_forbidden": [
            "task_power", "E0", "battery_capacity",
        ],
        "execution_changes": {
            "cpp": False,
            "scheduler_algorithms": False,
            "rta_math": False,
            "formal_campaign_started_by_local_mode": True,
        },
    }


@lru_cache(maxsize=1)
def formal_schema_hash_v5() -> str:
    # Preserve the established schema identity while removing governance
    # controls from the material exposed to the direct local runner.
    material = formal_schema_material_v5()
    contract = dict(material["campaign_contract"])
    contract.pop("direct_local_execution")
    contract.pop("resume_configuration_checked")
    contract.update({
        "formal_campaign_authorized": False,
        "local_not_for_paper_execution_allowed": True,
        "explicit_not_for_paper_acknowledgement_required": True,
    })
    material["campaign_contract"] = contract
    material["execution_changes"] = {
        **material["execution_changes"],
        "formal_campaign_started_by_local_mode": False,
    }
    return domain_hash(RTA4_FORMAL_SCHEMA_DOMAIN_V5, material)


def formal_schema_manifest_v5() -> dict[str, Any]:
    material = formal_schema_material_v5()
    return {**material, "schema_sha256": formal_schema_hash_v5()}


__all__ = [
    "RTA4_FORMAL_SCHEMA_DOMAIN_V5",
    "formal_schema_hash_v5",
    "formal_schema_manifest_v5",
    "formal_schema_material_v5",
]
