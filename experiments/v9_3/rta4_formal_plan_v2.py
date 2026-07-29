"""Versioned RTA4 plan identities over the unchanged mathematical grids."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Dict, Iterable, Iterator, Mapping

import asap_block_rta_v9_3_methods as method_registry

from . import exact_energy
from .rta4_formal_config import (
    RTA4_CORE2_METHODS,
    RTA4_RECURSIVE_METHODS,
    canonical_json,
    domain_hash,
)
from .rta4_formal_config_v2 import (
    RTA4_FORMAL_PLAN_VERSION_V2,
    RTA4_FORMAL_PROFILE_V2,
    formal_taskset_store_identity_v2,
    rta4_formal_config_hash_v2,
    validate_rta4_formal_config_v2,
)
from .rta4_formal_plan_grid import (
    EXPECTED_STREAM_COUNTS,
    FormalPlanGridPoint,
    TasksetGridSpec,
    iter_formal_plan_grid,
)
from .rta4_formal_schema_v2 import formal_schema_hash_v2
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_production_build_manifest import PRODUCTION_BUILD_MANIFEST_SCHEMA
from .rta4_shared_energy import (
    BETA_CONTRACT_VERSION,
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    TASK_ENERGY_MATERIAL_SCHEMA,
)


RTA4_PLAN_RECORD_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN_RECORD:v2"
RTA4_MATH_REQUEST_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_MATH_REQUEST:v2"
RTA4_EXECUTION_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_EXECUTION:v2"
RTA4_PLAN_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN:v2"
RTA4_STREAM_DIGEST_DOMAIN_V2 = b"ASAP_BLOCK:V9.3:RTA4_ORDERED_STREAM:v2\0"
RTA4_TASKSET_SLOT_PAIRING_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_TASKSET_SLOT:v1"
RTA4_SKELETON_SLOT_PAIRING_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SKELETON_SLOT:v1"
RTA4_CORE5B_PAIRING_SELECTION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_CORE5B_SELECTION:v1"
)
RTA4_CORE5B_PAIRING_MATH_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_MATH_REQUEST:v1"
RTA4_CORE5B_PAIRING_PROFILE = "ASAP_BLOCK_V9_3_RTA4_FORMAL_V1"


@dataclass(frozen=True)
class FormalPlanRecordV2:
    kind: str
    core: str
    ordinal: int
    mathematical_request_id: str | None
    execution_id: str | None
    taskset_slot_id: str | None
    taskset_skeleton_slot_id: str | None
    material: Mapping[str, Any]

    def canonical_material(self) -> Dict[str, Any]:
        return {
            "plan_version": RTA4_FORMAL_PLAN_VERSION_V2,
            "kind": self.kind,
            "core": self.core,
            "ordinal": self.ordinal,
            "mathematical_request_id": self.mathematical_request_id,
            "execution_id": self.execution_id,
            "taskset_slot_id": self.taskset_slot_id,
            "taskset_skeleton_slot_id": self.taskset_skeleton_slot_id,
            "material": dict(self.material),
        }

    @property
    def record_id(self) -> str:
        return domain_hash(RTA4_PLAN_RECORD_DOMAIN_V2, self.canonical_material())


@dataclass(frozen=True)
class StreamDigestV2:
    count: int
    sha256: str


def _versioned_material(material: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        **dict(material),
        "profile": RTA4_FORMAL_PROFILE_V2,
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "formal_schema_sha256": formal_schema_hash_v2(),
        "production_build_manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "task_energy_material_schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "service_material_schema": SERVICE_MATERIAL_SCHEMA,
        "horizon_contract_version": HORIZON_CONTRACT_VERSION,
        "beta_contract_version": BETA_CONTRACT_VERSION,
        "service_scale_semantics": "REBUILD_CANONICAL_SOLAR_SUPPORT_INPUT",
    }


def _method_material(method: str) -> Dict[str, Any]:
    spec = method_registry.method_spec_v9_3(method)
    return {
        "method": spec.method_id.value,
        "kernel": spec.kernel.value,
        "carry_policy": spec.carry_policy.value,
        "dominance_rank": spec.dominance_rank,
    }


def _grid_slots(slot: TasksetGridSpec) -> tuple[str, str]:
    skeleton = domain_hash(
        RTA4_SKELETON_SLOT_PAIRING_DOMAIN, slot.skeleton_material(),
    )
    taskset = domain_hash(RTA4_TASKSET_SLOT_PAIRING_DOMAIN, {
        "taskset_skeleton_slot_id": skeleton,
        "deadline_variant": slot.deadline_variant,
        "power_scale": slot.power_scale,
        "integer_time_scale": slot.integer_time_scale,
    })
    return skeleton, taskset


def _rta_material(
    point: FormalPlanGridPoint, *, profile: str,
) -> tuple[Dict[str, Any], str, str]:
    skeleton, taskset = _grid_slots(point.slot)
    grid = point.material
    material = {
        "profile": profile,
        "core": point.core,
        "scenario": grid["scenario"],
        "taskset_skeleton_slot_id": skeleton,
        "taskset_slot_id": taskset,
        **_method_material(str(grid["method"])),
        "exact_e0": grid["exact_e0"],
        "service_scale": grid["service_scale"],
        "power_scale": grid["power_scale"],
        "deadline_variant": grid["deadline_variant"],
        "axis": grid["axis"],
        "axis_value": grid["axis_value"],
        "timeout_contract": grid["timeout_contract"],
        "source_analysis_id": None,
        "normalized_utilization": grid["normalized_utilization"],
        "processor_count": grid["processor_count"],
        "task_count": grid["task_count"],
        "replicate_index": grid["replicate_index"],
    }
    return material, skeleton, taskset


def _rta_record_from_grid(point: FormalPlanGridPoint) -> FormalPlanRecordV2:
    base, skeleton, taskset = _rta_material(
        point, profile=RTA4_FORMAL_PROFILE_V2,
    )
    material = _versioned_material(base)
    mathematical = domain_hash(RTA4_MATH_REQUEST_DOMAIN_V2, material)
    execution = domain_hash(RTA4_EXECUTION_DOMAIN_V2, {
        "mathematical_request_id": mathematical,
        "worker_count": 1,
        "execution_role": "PRIMARY",
        "profile": RTA4_FORMAL_PROFILE_V2,
    })
    return FormalPlanRecordV2(
        "rta_request", point.core, point.ordinal, mathematical, execution,
        taskset, skeleton, material,
    )


def _core5b_ranker(point: FormalPlanGridPoint) -> tuple[str, str, str]:
    """Preserve the frozen paired subset while issuing only V2 identities."""

    pairing_material, _, _ = _rta_material(
        point, profile=RTA4_CORE5B_PAIRING_PROFILE,
    )
    pairing_source_id = domain_hash(
        RTA4_CORE5B_PAIRING_MATH_DOMAIN, pairing_material,
    )
    selection_hash = domain_hash(RTA4_CORE5B_PAIRING_SELECTION_DOMAIN, {
        "source_analysis_id": pairing_source_id,
        "utilization_stratum": point.material["normalized_utilization"],
        "method": point.material["method"],
    })
    v2_source_id = str(_rta_record_from_grid(point).mathematical_request_id)
    return selection_hash, pairing_source_id, v2_source_id


def _record_from_grid(point: FormalPlanGridPoint) -> FormalPlanRecordV2:
    if point.kind == "rta_request":
        return _rta_record_from_grid(point)
    skeleton, taskset = _grid_slots(point.slot)
    if point.kind == "simulation":
        material = _versioned_material({
            "profile": RTA4_FORMAL_PROFILE_V2,
            "taskset_skeleton_slot_id": skeleton,
            "taskset_slot_id": taskset,
            **point.material,
        })
        execution = domain_hash(RTA4_EXECUTION_DOMAIN_V2, {
            "kind": point.kind,
            "material": material,
        })
        return FormalPlanRecordV2(
            point.kind, point.core, point.ordinal, None, execution,
            taskset, skeleton, material,
        )
    if point.kind == "worker_execution":
        mathematical = point.source_mathematical_request_id
        material = _versioned_material({
            "mathematical_request_id": mathematical,
            **point.material,
        })
        execution = domain_hash(RTA4_EXECUTION_DOMAIN_V2, material)
        return FormalPlanRecordV2(
            point.kind, point.core, point.ordinal, mathematical, execution,
            taskset, skeleton, material,
        )
    raise ValueError(f"unknown RTA4 V2 grid record kind: {point.kind!r}")


def iter_formal_plan_v2(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV2]:
    normalized = validate_rta4_formal_config_v2(config)
    points = iter_formal_plan_grid(
        normalized["core"],
        recursive_methods=RTA4_RECURSIVE_METHODS,
        core2_methods=RTA4_CORE2_METHODS,
        core5b_ranker=_core5b_ranker,
    )
    for point in points:
        yield _record_from_grid(point)


def ordered_stream_digest_v2(records: Iterable[FormalPlanRecordV2]) -> StreamDigestV2:
    digest = hashlib.sha256(RTA4_STREAM_DIGEST_DOMAIN_V2)
    count = 0
    for count, record in enumerate(records, start=1):
        encoded = canonical_json(record.canonical_material()).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return StreamDigestV2(count, digest.hexdigest())


def describe_formal_plan_v2(config: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_rta4_formal_config_v2(config)
    stream = ordered_stream_digest_v2(iter_formal_plan_v2(normalized))
    expected = EXPECTED_STREAM_COUNTS[normalized["core"]]
    if stream.count != expected:
        raise ValueError("RTA4 V2 plan count drift")
    identity_material = {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V2,
        "core": normalized["core"],
        "config_semantic_hash": rta4_formal_config_hash_v2(normalized),
        "schema_sha256": formal_schema_hash_v2(),
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "taskset_store_identity": formal_taskset_store_identity_v2(),
        "production_build_manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "task_energy_material_schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "service_material_schema": SERVICE_MATERIAL_SCHEMA,
        "ordered_request_or_simulation_digest": stream.sha256,
    }
    return {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "core": normalized["core"],
        "parameter_status": "UNAUTHORIZED_PRE_PILOT",
        "ordered_stream_count": stream.count,
        "ordered_stream_digest": stream.sha256,
        "plan_sha256": domain_hash(RTA4_PLAN_DOMAIN_V2, identity_material),
        "identity_material": identity_material,
    }


def describe_all_formal_plans_v2(
    configs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    required = {"CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B"}
    if set(configs) != required:
        raise ValueError("all six RTA4 V2 configurations are required")
    plans = {core: describe_formal_plan_v2(configs[core]) for core in sorted(required)}
    return {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "plans": plans,
        "total_unique_rta_requests": 124_400,
        "total_simulations": 6_400,
        "core5b_mathematical_requests": 3_000,
        "core5b_executions": 12_000,
        "all_plan_digest": domain_hash(
            RTA4_PLAN_DOMAIN_V2,
            {core: plans[core]["plan_sha256"] for core in sorted(plans)},
        ),
    }


__all__ = [
    "FormalPlanRecordV2", "StreamDigestV2", "describe_all_formal_plans_v2",
    "describe_formal_plan_v2", "iter_formal_plan_v2",
    "ordered_stream_digest_v2",
]
