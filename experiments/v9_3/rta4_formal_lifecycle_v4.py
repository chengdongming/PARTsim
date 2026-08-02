"""Pure preparation and infrastructure-only authorization for RTA4 V4.

Formal campaign execution intentionally remains unavailable.  These functions
bind identities and support dry-run inspection without creating output or
taskset-store namespaces.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .rta4_formal_config import domain_hash
from .rta4_formal_config_v4 import (
    LoadedCampaignV4,
    RTA4_FORMAL_CAMPAIGN_AUTHORIZATION_STATUS_V4,
    RTA4_FORMAL_PROFILE_V4,
    formal_taskset_store_header_v4,
    formal_taskset_store_identity_v4,
    rta4_formal_config_hash_v4,
    source_closure_identity_v4,
)
from .rta4_formal_plan_v4 import describe_formal_plan_v4, iter_formal_plan_v4
from .rta4_formal_schema_v4 import formal_schema_hash_v4
from .rta4_physical_core_slots_v3 import PHYSICAL_CORE_EXECUTION_BACKEND_V3
from .rta4_task_source_v4 import revalidate_task_source_v4


RTA4_PREPARED_CONFIG_SCHEMA_V4 = (
    "ASAP_BLOCK_V9_3_RTA4_PREPARED_CONFIG_V4_EXACT_TASK_SOURCE"
)
RTA4_PREPARED_CONFIG_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:PREPARED_CONFIG:v4"
RTA4_INFRASTRUCTURE_AUTHORIZATION_SCHEMA_V4 = (
    "ASAP_BLOCK_V9_3_RTA4_INFRASTRUCTURE_AUTHORIZATION_V4"
)
RTA4_INFRASTRUCTURE_AUTHORIZATION_DOMAIN_V4 = (
    "ASAP_BLOCK:V9.3:RTA4:INFRASTRUCTURE_AUTHORIZATION:v4"
)
RTA4_EXECUTION_BACKEND_V4 = PHYSICAL_CORE_EXECUTION_BACKEND_V3


class RTA4FormalLifecycleV4Error(ValueError):
    """Raised when a V4 lifecycle identity is absent or inconsistent."""


def _sha(value: Any, label: str, *, length: int = 64) -> str:
    if (
        type(value) is not str or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4FormalLifecycleV4Error(
            f"{label} must be lowercase hexadecimal length {length}"
        )
    return value


def _operational(runtime: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(runtime))
    for field in ("output_root", "taskset_store", "log_path"):
        if field in result:
            result[field] = str(Path(result[field]).expanduser().resolve())
    return result


def build_prepared_config_v4(
    campaign: LoadedCampaignV4, *, repository_commit: str,
    repository_tree: str, production_build_manifest_identity: str,
) -> dict[str, Any]:
    if type(campaign) is not LoadedCampaignV4:
        raise RTA4FormalLifecycleV4Error(
            "prepared config requires a loaded V4 campaign"
        )
    commit = _sha(repository_commit, "repository commit", length=40)
    tree = _sha(repository_tree, "repository tree", length=40)
    build = _sha(
        production_build_manifest_identity,
        "production build manifest identity",
    )
    source = revalidate_task_source_v4(campaign.task_source)
    scientific = campaign.normalized_scientific_config
    config_identity = rta4_formal_config_hash_v4(scientific)
    if config_identity != campaign.normalized_scientific_config_sha256:
        raise RTA4FormalLifecycleV4Error("loaded scientific config identity drift")
    plan = describe_formal_plan_v4(scientific, source)
    base = {
        "schema": RTA4_PREPARED_CONFIG_SCHEMA_V4,
        "profile": RTA4_FORMAL_PROFILE_V4,
        "formal_schema_sha256": formal_schema_hash_v4(),
        "repository_commit": commit,
        "repository_tree": tree,
        "production_build_manifest_identity": build,
        "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
        "normalized_scientific_config": deepcopy(dict(scientific)),
        "normalized_scientific_config_sha256": config_identity,
        "plan": plan,
        "plan_sha256": plan["plan_sha256"],
        "task_source_mode": source.mode,
        "task_source_identity": source.identity,
        "task_source_content_certificate": deepcopy(
            dict(source.content_certificate)
        ),
        "manifest_file_sha256": source.manifest_file_sha256,
        "manifest_semantic_sha256": source.manifest_semantic_sha256,
        "energy_service_identity": campaign.energy_service.identity,
        "energy_service": deepcopy(
            dict(campaign.energy_service.normalized_config)
        ),
        "taskset_store_identity": formal_taskset_store_identity_v4(scientific),
        "taskset_store_header": formal_taskset_store_header_v4(scientific),
        "source_closure_identity": source_closure_identity_v4(scientific),
        "execution_backend": RTA4_EXECUTION_BACKEND_V4,
        "operational": _operational(campaign.runtime),
        "formal_campaign_authorization_status": (
            RTA4_FORMAL_CAMPAIGN_AUTHORIZATION_STATUS_V4
        ),
        "formal_t10_campaign_authorized": False,
    }
    return {
        **base,
        "prepared_config_identity": domain_hash(
            RTA4_PREPARED_CONFIG_DOMAIN_V4, base,
        ),
    }


def validate_prepared_config_v4(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4FormalLifecycleV4Error("prepared config must be a mapping")
    prepared = deepcopy(dict(value))
    observed = prepared.pop("prepared_config_identity", None)
    if (
        prepared.get("schema") != RTA4_PREPARED_CONFIG_SCHEMA_V4
        or prepared.get("profile") != RTA4_FORMAL_PROFILE_V4
        or prepared.get("execution_backend") != RTA4_EXECUTION_BACKEND_V4
        or prepared.get("formal_t10_campaign_authorized") is not False
        or observed != domain_hash(RTA4_PREPARED_CONFIG_DOMAIN_V4, prepared)
    ):
        raise RTA4FormalLifecycleV4Error("prepared config identity mismatch")
    scientific = prepared["normalized_scientific_config"]
    if (
        rta4_formal_config_hash_v4(scientific)
        != prepared["normalized_scientific_config_sha256"]
        or formal_taskset_store_identity_v4(scientific)
        != prepared["taskset_store_identity"]
        or source_closure_identity_v4(scientific)
        != prepared["source_closure_identity"]
        or formal_taskset_store_header_v4(scientific)
        != prepared["taskset_store_header"]
    ):
        raise RTA4FormalLifecycleV4Error("prepared scientific binding drift")
    return {**prepared, "prepared_config_identity": observed}


def build_infrastructure_authorization_v4(
    prepared_config: Mapping[str, Any], *, stage_a5_audit_path: Path | str,
    maximum_request_count: int,
) -> dict[str, Any]:
    prepared = validate_prepared_config_v4(prepared_config)
    audit_path = Path(stage_a5_audit_path).expanduser().resolve(strict=True)
    audit_payload = audit_path.read_bytes()
    audit_sha = hashlib.sha256(audit_payload).hexdigest()
    try:
        audit = json.loads(audit_payload)
    except Exception as exc:
        raise RTA4FormalLifecycleV4Error(
            "cannot parse Stage A.5 audit artifact"
        ) from exc
    required_audit_values = {
        "method_comparison_count": 1408,
        "task_result_record_count": 14080,
        "exact_adapter_parity_mismatch_count": 0,
        "exact_input_parity_mismatch_count": 0,
        "exact_float_decision_path_count": 0,
        "input_identity_failure_count": 0,
        "unclassified_internal_error_count": 0,
        "script_failure_count": 0,
        "dominance_violation_count": 0,
        "formula_changes": False,
        "stage_b_infrastructure_authorized": True,
        "formal_t10_campaign_authorized": False,
        "formal_experiment_started": False,
    }
    if (
        not isinstance(audit, Mapping)
        or any(audit.get(key) != value for key, value in required_audit_values.items())
        or audit.get("service_contracts", {}).get("exact", {}).get(
            "implementation"
        ) != "Fraction(length, 10)"
    ):
        raise RTA4FormalLifecycleV4Error(
            "Stage A.5 artifact does not authorize V4 infrastructure"
        )
    if type(maximum_request_count) is not int or maximum_request_count < 1:
        raise RTA4FormalLifecycleV4Error(
            "maximum_request_count must be a positive plain integer"
        )
    if maximum_request_count > prepared["plan"]["ordered_stream_count"]:
        raise RTA4FormalLifecycleV4Error(
            "infrastructure authorization exceeds dry-run plan"
        )
    base = {
        "schema": RTA4_INFRASTRUCTURE_AUTHORIZATION_SCHEMA_V4,
        "profile": RTA4_FORMAL_PROFILE_V4,
        "execution_class": "TEST_ONLY_INFRASTRUCTURE",
        "prepared_config_identity": prepared["prepared_config_identity"],
        "repository_commit": prepared["repository_commit"],
        "repository_tree": prepared["repository_tree"],
        "production_build_manifest_identity": prepared[
            "production_build_manifest_identity"
        ],
        "plan_sha256": prepared["plan_sha256"],
        "task_source_identity": prepared["task_source_identity"],
        "task_source_content_certificate_identity": prepared[
            "task_source_content_certificate"
        ]["content_certificate_identity"],
        "task_source_content_certificate": deepcopy(
            prepared["task_source_content_certificate"]
        ),
        "manifest_file_sha256": prepared["manifest_file_sha256"],
        "manifest_semantic_sha256": prepared["manifest_semantic_sha256"],
        "energy_service_identity": prepared["energy_service_identity"],
        "taskset_store_identity": prepared["taskset_store_identity"],
        "taskset_store_header": deepcopy(prepared["taskset_store_header"]),
        "source_closure_identity": prepared["source_closure_identity"],
        "execution_backend": RTA4_EXECUTION_BACKEND_V4,
        "stage_a5_audit_sha256": audit_sha,
        "stage_a5_authorization_evidence": deepcopy(required_audit_values),
        "stage_b_infrastructure_authorized": True,
        "formal_t10_campaign_authorized": False,
        "maximum_request_count": maximum_request_count,
    }
    return {
        **base,
        "infrastructure_authorization_identity": domain_hash(
            RTA4_INFRASTRUCTURE_AUTHORIZATION_DOMAIN_V4, base,
        ),
    }


def require_formal_campaign_authorization_v4(
    _prepared_config: Mapping[str, Any],
    _authorization: Mapping[str, Any] | None = None,
) -> None:
    raise RTA4FormalLifecycleV4Error(
        "formal V4 T10 campaign is unauthorized pending a separate parameter freeze"
    )


def dry_run_campaign_v4(campaign: LoadedCampaignV4) -> dict[str, Any]:
    source = revalidate_task_source_v4(campaign.task_source)
    plan = describe_formal_plan_v4(
        campaign.normalized_scientific_config, source,
    )
    records = iter_formal_plan_v4(
        campaign.normalized_scientific_config, source,
    )
    first = next(records, None)
    return {
        "profile": RTA4_FORMAL_PROFILE_V4,
        "dry_run": True,
        "writes_performed": False,
        "output_namespace_created": False,
        "taskset_store_namespace_created": False,
        "formal_t10_campaign_authorized": False,
        "plan": plan,
        "first_record": None if first is None else first.canonical_material(),
    }


__all__ = [
    "RTA4_EXECUTION_BACKEND_V4", "RTA4_PREPARED_CONFIG_SCHEMA_V4",
    "RTA4FormalLifecycleV4Error", "build_infrastructure_authorization_v4",
    "build_prepared_config_v4", "dry_run_campaign_v4",
    "require_formal_campaign_authorization_v4",
    "validate_prepared_config_v4",
]
