"""Versioned RTA4 plan identities over the unchanged mathematical grids."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Dict, Iterable, Iterator, Mapping

from . import exact_energy
from .rta4_formal_config import canonical_json, default_rta4_formal_config
from .rta4_formal_config_v2 import (
    RTA4_FORMAL_PLAN_VERSION_V2,
    RTA4_FORMAL_PROFILE_V2,
    formal_taskset_store_identity_v2,
    rta4_formal_config_hash_v2,
    validate_rta4_formal_config_v2,
)
from .rta4_formal_plan import iter_formal_plan
from .rta4_formal_schema_v2 import formal_schema_hash_v2
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_production_build_manifest import PRODUCTION_BUILD_MANIFEST_SCHEMA
from .rta4_shared_energy import (
    BETA_CONTRACT_VERSION,
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    TASK_ENERGY_MATERIAL_SCHEMA,
)
from .rta4_formal_config import domain_hash


RTA4_PLAN_RECORD_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN_RECORD:v2"
RTA4_MATH_REQUEST_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_MATH_REQUEST:v2"
RTA4_EXECUTION_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_EXECUTION:v2"
RTA4_PLAN_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN:v2"
RTA4_STREAM_DIGEST_DOMAIN_V2 = b"ASAP_BLOCK:V9.3:RTA4_ORDERED_STREAM:v2\0"


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


def _transform(base: Any) -> FormalPlanRecordV2:
    material = {
        **dict(base.material),
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
    mathematical = (
        None
        if base.mathematical_request_id is None
        else domain_hash(RTA4_MATH_REQUEST_DOMAIN_V2, {
            "v1_mathematical_cell_identity": base.mathematical_request_id,
            "v2_shared_energy_contracts": {
                "profile": RTA4_FORMAL_PROFILE_V2,
                "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
                "schema_sha256": formal_schema_hash_v2(),
                "taskset_store_identity": formal_taskset_store_identity_v2(),
            },
        })
    )
    execution = (
        None
        if base.execution_id is None
        else domain_hash(RTA4_EXECUTION_DOMAIN_V2, {
            "mathematical_request_id": mathematical,
            "v1_execution_cell_identity": base.execution_id,
            "worker_count": material.get("worker_count", 1),
        })
    )
    return FormalPlanRecordV2(
        base.kind, base.core, base.ordinal, mathematical, execution,
        base.taskset_slot_id, base.taskset_skeleton_slot_id, material,
    )


def iter_formal_plan_v2(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV2]:
    normalized = validate_rta4_formal_config_v2(config)
    base = default_rta4_formal_config(normalized["core"])
    for record in iter_formal_plan(base):
        yield _transform(record)


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
    expected = {
        "CORE-1": 19_200,
        "CORE-2": 28_800,
        "CORE-3": 6_400,
        "CORE-4": 72_000,
        "CORE-5A": 4_400,
        "CORE-5B": 12_000,
    }[normalized["core"]]
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
