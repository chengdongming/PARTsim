"""Ordered, identity-isolated plans for exact task-source RTA4 V4."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Iterator, Mapping

from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v4 import (
    RTA4_FORMAL_PLAN_VERSION_V4,
    RTA4_FORMAL_PROFILE_V4,
    formal_taskset_store_identity_v4,
    rta4_formal_config_hash_v4,
    source_closure_identity_v4,
)
from .rta4_formal_schema_v4 import formal_schema_hash_v4
from .rta4_physical_core_slots_v3 import PHYSICAL_CORE_EXECUTION_BACKEND_V3
from .rta4_task_source_v4 import TaskSourceV4, revalidate_task_source_v4


RTA4_PLAN_RECORD_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:PLAN_RECORD:v4"
RTA4_MATH_REQUEST_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:MATH_REQUEST:v4"
RTA4_EXECUTION_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:EXECUTION:v4"
RTA4_PLAN_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:FORMAL_PLAN:v4"
RTA4_STREAM_DIGEST_DOMAIN_V4 = b"ASAP_BLOCK:V9.3:RTA4:ORDERED_STREAM:v4\0"
RTA4_TASKSET_SLOT_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:TASKSET_SLOT:v4"


class RTA4FormalPlanV4Error(ValueError):
    """Raised when V4 planning differs from its bound task source."""


@dataclass(frozen=True)
class FormalPlanRecordV4:
    ordinal: int
    taskset_index: int
    taskset_identity: str
    taskset_slot_id: str
    mathematical_request_id: str
    execution_id: str
    material: Mapping[str, Any]

    def canonical_material(self) -> dict[str, Any]:
        return {
            "plan_version": RTA4_FORMAL_PLAN_VERSION_V4,
            "kind": "rta_request",
            "core": "CORE-1",
            "ordinal": self.ordinal,
            "taskset_index": self.taskset_index,
            "taskset_identity": self.taskset_identity,
            "taskset_slot_id": self.taskset_slot_id,
            "mathematical_request_id": self.mathematical_request_id,
            "execution_id": self.execution_id,
            "material": dict(self.material),
        }

    @property
    def record_id(self) -> str:
        return domain_hash(RTA4_PLAN_RECORD_DOMAIN_V4, self.canonical_material())


@dataclass(frozen=True)
class StreamDigestV4:
    count: int
    sha256: str


def _validate_source(
    scientific_config: Mapping[str, Any], source: TaskSourceV4,
) -> TaskSourceV4:
    rta4_formal_config_hash_v4(scientific_config)
    if type(source) is not TaskSourceV4:
        raise RTA4FormalPlanV4Error("plan requires a normalized task source")
    if (
        source.identity != scientific_config["task_source_identity"]
        or source.content_certificate
        != scientific_config["task_source_content_certificate"]
        or source.taskset_count != scientific_config["taskset_count"]
        or source.task_count != scientific_config["task_count"]
    ):
        raise RTA4FormalPlanV4Error("plan task source identity drift")
    try:
        return revalidate_task_source_v4(source)
    except Exception as exc:
        raise RTA4FormalPlanV4Error(
            "plan task source runtime revalidation failed"
        ) from exc


def expected_counts_v4(scientific_config: Mapping[str, Any]) -> dict[str, int]:
    rta4_formal_config_hash_v4(scientific_config)
    tasksets = int(scientific_config["taskset_count"])
    mathematical = (
        tasksets * len(scientific_config["e0"])
        * len(scientific_config["methods"])
    )
    return {
        "taskset_count": tasksets,
        "mathematical_request_count": mathematical,
        "ordered_stream_count": mathematical,
    }


def iter_formal_plan_v4(
    scientific_config: Mapping[str, Any], task_source: TaskSourceV4,
) -> Iterator[FormalPlanRecordV4]:
    source = _validate_source(scientific_config, task_source)
    ordinal = 0
    for taskset_index, taskset in enumerate(source.tasksets):
        slot_material = {
            "profile": RTA4_FORMAL_PROFILE_V4,
            "task_source_mode": source.mode,
            "task_source_identity": source.identity,
            "taskset_index": taskset_index,
            "taskset_identity": taskset.identity,
            "taskset_content_sha256": taskset.content_sha256,
            "task_order_sha256": taskset.task_order_sha256,
        }
        slot = domain_hash(RTA4_TASKSET_SLOT_DOMAIN_V4, slot_material)
        for e0 in scientific_config["e0"]:
            for method in scientific_config["methods"]:
                math_material = {
                    **slot_material,
                    "taskset_slot_id": slot,
                    "method": method,
                    "exact_e0": e0,
                    "energy_service_identity": scientific_config[
                        "energy_service_identity"
                    ],
                }
                mathematical = domain_hash(
                    RTA4_MATH_REQUEST_DOMAIN_V4, math_material,
                )
                execution = domain_hash(RTA4_EXECUTION_DOMAIN_V4, {
                    "profile": RTA4_FORMAL_PROFILE_V4,
                    "mathematical_request_id": mathematical,
                    "execution_backend": PHYSICAL_CORE_EXECUTION_BACKEND_V3,
                })
                yield FormalPlanRecordV4(
                    ordinal, taskset_index, taskset.identity, slot,
                    mathematical, execution,
                    {
                        "task_source_mode": source.mode,
                        "task_source_identity": source.identity,
                        "taskset_content_sha256": taskset.content_sha256,
                        "task_order_sha256": taskset.task_order_sha256,
                        "method": method,
                        "exact_e0": e0,
                        "energy_service_identity": scientific_config[
                            "energy_service_identity"
                        ],
                    },
                )
                ordinal += 1


def ordered_stream_digest_v4(
    records: Iterable[FormalPlanRecordV4],
) -> StreamDigestV4:
    digest = hashlib.sha256(RTA4_STREAM_DIGEST_DOMAIN_V4)
    count = 0
    for count, record in enumerate(records, start=1):
        encoded = canonical_json(record.canonical_material()).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return StreamDigestV4(count, digest.hexdigest())


def describe_formal_plan_v4(
    scientific_config: Mapping[str, Any], task_source: TaskSourceV4,
) -> dict[str, Any]:
    config_identity = rta4_formal_config_hash_v4(scientific_config)
    expected = expected_counts_v4(scientific_config)
    stream = ordered_stream_digest_v4(
        iter_formal_plan_v4(scientific_config, task_source)
    )
    if stream.count != expected["ordered_stream_count"]:
        raise RTA4FormalPlanV4Error("V4 ordered stream count mismatch")
    identity_material = {
        "profile": RTA4_FORMAL_PROFILE_V4,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V4,
        "normalized_scientific_config_sha256": config_identity,
        "formal_schema_sha256": formal_schema_hash_v4(),
        "counts": expected,
        "ordered_stream_digest": stream.sha256,
        "task_source_identity": task_source.identity,
        "task_source_content_certificate_identity": task_source.content_certificate[
            "content_certificate_identity"
        ],
        "energy_service_identity": scientific_config["energy_service_identity"],
        "taskset_store_identity": formal_taskset_store_identity_v4(
            scientific_config
        ),
        "source_closure_identity": source_closure_identity_v4(
            scientific_config
        ),
    }
    return {
        **expected,
        "profile": RTA4_FORMAL_PROFILE_V4,
        "normalized_scientific_config_sha256": config_identity,
        "ordered_stream_digest": stream.sha256,
        "task_source_identity": task_source.identity,
        "energy_service_identity": scientific_config["energy_service_identity"],
        "taskset_store_identity": identity_material["taskset_store_identity"],
        "source_closure_identity": identity_material["source_closure_identity"],
        "plan_sha256": domain_hash(RTA4_PLAN_DOMAIN_V4, identity_material),
        "identity_material": identity_material,
    }


__all__ = [
    "FormalPlanRecordV4", "RTA4FormalPlanV4Error", "StreamDigestV4",
    "describe_formal_plan_v4", "expected_counts_v4", "iter_formal_plan_v4",
    "ordered_stream_digest_v4",
]
