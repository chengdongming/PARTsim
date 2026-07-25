"""Trusted config checkpoint and exact lightweight plan membership for RTA4."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from itertools import islice
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .rta4_formal_config import (
    RTA4_FORMAL_PARAMETER_STATUS, RTA4_FORMAL_PLAN_VERSION,
    RTA4_FORMAL_PROFILE, canonical_json, domain_hash,
    rta4_formal_config_hash, validate_rta4_formal_config,
)
from .rta4_formal_plan import (
    FormalPlanRecord, core3_comparisons_for_simulation,
    describe_formal_plan, iter_core2_source_references,
    iter_core5b_math_references,
    iter_formal_plan, ordered_stream_digest,
)


RTA4_CONFIG_CHECKPOINT = "formal_config_checkpoint.json"
RTA4_PLAN_MANIFEST = "formal_plan_manifest.json"
RTA4_PLAN_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_TRUSTED_PLAN_MANIFEST_V1"
RTA4_PLAN_MANIFEST_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_TRUSTED_PLAN_MANIFEST:v1"
RTA4_SOURCE_RELATION_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SOURCE_RELATION:v1"
RTA4_COMPARISON_PLAN_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_COMPARISON_PLAN:v1"
NONFORMAL_TEST_FIXTURE = "NONFORMAL_TEST_FIXTURE"
RTA4_FIXTURE_LIMIT = 100


class RTA4FormalManifestError(ValueError):
    """Raised when a persisted plan is not derivable from trusted code/config."""


def config_checkpoint(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Return path/resume-independent, code-validated configuration material."""

    normalized = validate_rta4_formal_config(config)
    material = deepcopy(normalized)
    material["execution"].pop("output_root")
    material["execution"].pop("taskset_store")
    material["execution"].pop("resume")
    return {
        "profile": RTA4_FORMAL_PROFILE,
        "parameter_status": RTA4_FORMAL_PARAMETER_STATUS,
        "core": normalized["core"],
        "config_semantic_hash": rta4_formal_config_hash(normalized),
        "validated_config_material": material,
    }


def validate_config_checkpoint(
    checkpoint: Mapping[str, Any], config: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = config_checkpoint(config)
    if dict(checkpoint) != expected:
        raise RTA4FormalManifestError("trusted config checkpoint mismatch")
    return expected


def _records_at_ordinals(
    config: Mapping[str, Any], ordinals: Sequence[int],
) -> Tuple[FormalPlanRecord, ...]:
    if type(ordinals) not in {tuple, list}:
        raise RTA4FormalManifestError("fixture ordinals must be an ordered sequence")
    values = tuple(ordinals)
    if len(values) > RTA4_FIXTURE_LIMIT:
        raise RTA4FormalManifestError("fixture exceeds the 100-plan-record limit")
    if any(type(value) is not int or value < 0 for value in values):
        raise RTA4FormalManifestError("fixture ordinals must be non-negative integers")
    if tuple(sorted(set(values))) != values:
        raise RTA4FormalManifestError("fixture ordinals must be unique/increasing")
    if not values:
        return ()
    wanted = set(values)
    result = tuple(
        record
        for ordinal, record in enumerate(
            islice(iter_formal_plan(config), values[-1] + 1)
        )
        if ordinal in wanted
    )
    if len(result) != len(values):
        raise RTA4FormalManifestError("fixture ordinal is outside the trusted plan")
    return result


@lru_cache(maxsize=6)
def _trusted_description(core: str) -> Mapping[str, Any]:
    from .rta4_formal_config import default_rta4_formal_config

    return describe_formal_plan(default_rta4_formal_config(core))


def plan_relation_id(row: Mapping[str, Any]) -> str:
    return domain_hash(RTA4_SOURCE_RELATION_DOMAIN, dict(row))


def plan_comparison_id(row: Mapping[str, Any]) -> str:
    return domain_hash(RTA4_COMPARISON_PLAN_DOMAIN, dict(row))


def _source_relations(
    core: str, records: Sequence[FormalPlanRecord],
) -> Tuple[Mapping[str, Any], ...]:
    if core == "CORE-2":
        selected = {
            (record.taskset_slot_id, str(record.material["exact_e0"]))
            for record in records
        }
        return tuple(
            row for row in iter_core2_source_references()
            if (row["taskset_slot_id"], row["exact_e0"]) in selected
        )
    if core == "CORE-3":
        unique: Dict[str, Mapping[str, Any]] = {}
        for record in records:
            for row in core3_comparisons_for_simulation(record):
                source = {
                    "source_core": "CORE-1",
                    "target_core": "CORE-3",
                    "taskset_slot_id": row["taskset_slot_id"],
                    "method": row["method"],
                    "exact_e0": row["exact_e0"],
                    "source_analysis_id": row["source_analysis_id"],
                }
                unique[plan_relation_id(source)] = source
        return tuple(unique[key] for key in sorted(unique))
    return ()


def _comparison_rows(
    core: str, records: Sequence[FormalPlanRecord],
) -> Tuple[Mapping[str, Any], ...]:
    if core != "CORE-3":
        return ()
    return tuple(
        row
        for record in records
        for row in core3_comparisons_for_simulation(record)
    )


def build_trusted_plan_manifest(
    config: Mapping[str, Any], *, execution_class: str,
    fixture_ordinals: Sequence[int] = (),
) -> Dict[str, Any]:
    normalized = validate_rta4_formal_config(config)
    if execution_class != NONFORMAL_TEST_FIXTURE:
        raise RTA4FormalManifestError(
            "PR-D permits only NONFORMAL_TEST_FIXTURE plan manifests"
        )
    records = _records_at_ordinals(normalized, fixture_ordinals)
    core = normalized["core"]
    description = _trusted_description(core)
    stream = ordered_stream_digest(iter(records))
    relations = _source_relations(core, records)
    comparisons = _comparison_rows(core, records)
    selected_math = tuple(iter_core5b_math_references()) if core == "CORE-5B" else ()
    selected_math_ids = {record.mathematical_request_id for record in records}
    selected_references = tuple(
        record for record in selected_math
        if record.mathematical_request_id in selected_math_ids
    )
    material = {
        "manifest_version": RTA4_PLAN_MANIFEST_VERSION,
        "profile": RTA4_FORMAL_PROFILE,
        "plan_version": RTA4_FORMAL_PLAN_VERSION,
        "parameter_status": RTA4_FORMAL_PARAMETER_STATUS,
        "execution_class": execution_class,
        "core": core,
        "fixture_ordinals": list(fixture_ordinals),
        "config_checkpoint": config_checkpoint(normalized),
        "full_plan_sha256": description["plan_sha256"],
        "full_ordered_stream_count": description["ordered_stream_count"],
        "full_ordered_stream_digest": description["ordered_stream_digest"],
        "selection_manifest_digest": description["selection_manifest_digest"],
        "source_analysis_relation_digest": description["source_analysis_relation_digest"],
        "applicability_projection_digest": description["applicability_projection_digest"],
        "fixture_ordered_stream_count": stream.count,
        "fixture_ordered_stream_digest": stream.sha256,
        "plan_records": [
            {
                "ordinal": record.ordinal,
                "plan_record_id": record.record_id,
                "kind": record.kind,
                "mathematical_request_id": record.mathematical_request_id,
                "execution_id": record.execution_id,
                "taskset_slot_id": record.taskset_slot_id,
                "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            }
            for record in records
        ],
        "source_relations": [
            {"plan_relation_id": plan_relation_id(row), **dict(row)}
            for row in relations
        ],
        "applicability_rows": [
            {"plan_comparison_id": plan_comparison_id(row), **dict(row)}
            for row in comparisons
        ],
        "core5b_selected_references": [
            {
                "plan_record_id": row.record_id,
                "mathematical_request_id": row.mathematical_request_id,
                "selection_hash": row.material["selection_hash"],
                "taskset_slot_id": row.taskset_slot_id,
                "taskset_skeleton_slot_id": row.taskset_skeleton_slot_id,
            }
            for row in selected_references
        ],
    }
    return {
        **material,
        "manifest_sha256": domain_hash(RTA4_PLAN_MANIFEST_DOMAIN, material),
    }


def validate_trusted_plan_manifest(
    manifest: Mapping[str, Any], config: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise RTA4FormalManifestError("trusted plan manifest must be a mapping")
    expected = build_trusted_plan_manifest(
        config,
        execution_class=str(manifest.get("execution_class", "")),
        fixture_ordinals=manifest.get("fixture_ordinals", ()),
    )
    if dict(manifest) != expected:
        raise RTA4FormalManifestError("trusted plan manifest mismatch")
    return expected


def trusted_plan_records(
    config: Mapping[str, Any], manifest: Mapping[str, Any],
) -> Tuple[FormalPlanRecord, ...]:
    """Re-enumerate the exact records selected by a validated manifest."""

    validate_trusted_plan_manifest(manifest, config)
    return _records_at_ordinals(config, manifest["fixture_ordinals"])


__all__ = [
    "NONFORMAL_TEST_FIXTURE", "RTA4_CONFIG_CHECKPOINT", "RTA4_FIXTURE_LIMIT",
    "RTA4_PLAN_MANIFEST", "RTA4_PLAN_MANIFEST_VERSION",
    "RTA4FormalManifestError", "build_trusted_plan_manifest",
    "config_checkpoint", "plan_comparison_id", "plan_relation_id",
    "trusted_plan_records", "validate_config_checkpoint",
    "validate_trusted_plan_manifest",
]
