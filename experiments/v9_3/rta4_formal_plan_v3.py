"""Dynamic, hash-bound plans for parameterized RTA4 V3 campaigns."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Dict, Iterable, Iterator, Mapping

from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v3 import (
    RTA4_FORMAL_PLAN_VERSION_V3,
    RTA4_FORMAL_PROFILE_V3,
    RTA4_SELECTION_RULE_V3,
    formal_taskset_store_identity_v3,
    rta4_formal_config_hash_v3,
    source_binding_v3,
    validate_source_binding_v3,
)
from .rta4_formal_schema_v3 import formal_schema_hash_v3


RTA4_PLAN_RECORD_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN_RECORD:v3"
RTA4_MATH_REQUEST_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_MATH_REQUEST:v3"
RTA4_EXECUTION_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_EXECUTION:v3"
RTA4_PLAN_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN:v3"
RTA4_STREAM_DIGEST_DOMAIN_V3 = b"ASAP_BLOCK:V9.3:RTA4_ORDERED_STREAM:v3\0"
RTA4_TASKSET_SLOT_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_TASKSET_SLOT:v3"
RTA4_TASKSET_SKELETON_SLOT_DOMAIN_V3 = (
    "ASAP_BLOCK:V9.3:RTA4_TASKSET_SKELETON_SLOT:v3"
)
RTA4_CORE5B_SELECTION_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_CORE5B_SELECTION:v3"


class RTA4FormalPlanV3Error(ValueError):
    """Raised when dynamic planning differs from its validated finite axes."""


@dataclass(frozen=True)
class FormalPlanRecordV3:
    kind: str
    core: str
    ordinal: int
    mathematical_request_id: str
    execution_id: str
    taskset_skeleton_slot_id: str
    taskset_slot_id: str
    material: Mapping[str, Any]

    def canonical_material(self) -> Dict[str, Any]:
        return {
            "plan_version": RTA4_FORMAL_PLAN_VERSION_V3,
            "kind": self.kind,
            "core": self.core,
            "ordinal": self.ordinal,
            "mathematical_request_id": self.mathematical_request_id,
            "execution_id": self.execution_id,
            "taskset_skeleton_slot_id": self.taskset_skeleton_slot_id,
            "taskset_slot_id": self.taskset_slot_id,
            "material": dict(self.material),
        }

    @property
    def record_id(self) -> str:
        return domain_hash(RTA4_PLAN_RECORD_DOMAIN_V3, self.canonical_material())


@dataclass(frozen=True)
class StreamDigestV3:
    count: int
    sha256: str


def _slot(material: Mapping[str, Any]) -> str:
    return domain_hash(RTA4_TASKSET_SLOT_DOMAIN_V3, material)


def _record(
    *, core: str, ordinal: int, kind: str, slot_material: Mapping[str, Any],
    math_material: Mapping[str, Any], execution_material: Mapping[str, Any] | None = None,
    skeleton_material: Mapping[str, Any] | None = None,
) -> FormalPlanRecordV3:
    slot = _slot(slot_material)
    skeleton = domain_hash(
        RTA4_TASKSET_SKELETON_SLOT_DOMAIN_V3,
        slot_material if skeleton_material is None else skeleton_material,
    )
    mathematical = domain_hash(RTA4_MATH_REQUEST_DOMAIN_V3, {
        "profile": RTA4_FORMAL_PROFILE_V3,
        "core": core,
        "taskset_slot_id": slot,
        **dict(math_material),
    })
    execution = domain_hash(RTA4_EXECUTION_DOMAIN_V3, {
        "profile": RTA4_FORMAL_PROFILE_V3,
        "mathematical_request_id": mathematical,
        **({} if execution_material is None else dict(execution_material)),
    })
    return FormalPlanRecordV3(
        kind, core, ordinal, mathematical, execution, skeleton, slot,
        {
            **dict(slot_material), **dict(math_material),
            **({} if execution_material is None else dict(execution_material)),
        },
    )


def _core1(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV3]:
    ordinal = 0
    for utilization in config["normalized_utilization"]:
        for replicate in range(config["tasksets_per_utilization"]):
            slot = {
                "namespace": "RTA4_CORE1_PARAMETERIZED_V3",
                "processor_count": config["processors"], "task_count": config["task_count"],
                "normalized_utilization": utilization, "replicate_index": replicate,
            }
            for e0 in config["e0"]:
                for method in config["methods"]:
                    yield _record(
                        core="CORE-1", ordinal=ordinal, kind="rta_request",
                        slot_material=slot,
                        math_material={"method": method, "exact_e0": e0},
                    )
                    ordinal += 1


def _source_slot(config: Mapping[str, Any], index: int) -> Dict[str, Any]:
    source = config["source"]
    return {
        "namespace": "RTA4_PARAMETERIZED_SOURCE_V3",
        "source_taskset_store_identity": source["source_taskset_store_identity"],
        "source_taskset_index": index,
    }


def _core2(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV3]:
    ordinal = 0
    for index in range(config["source"]["taskset_count"]):
        for e0 in config["e0"]:
            for method in config["methods"]:
                yield _record(
                    core="CORE-2", ordinal=ordinal, kind="rta_request",
                    slot_material=_source_slot(config, index),
                    math_material={
                        "method": method, "exact_e0": e0,
                        "referenced_recursive_methods": config[
                            "referenced_recursive_methods"
                        ],
                        "source": config["source"],
                    },
                )
                ordinal += 1


def _core3(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV3]:
    ordinal = 0
    for index in range(config["source"]["taskset_count"]):
        slot = _source_slot(config, index)
        for release_mode in config["release_modes"]:
            yield _record(
                core="CORE-3", ordinal=ordinal, kind="simulation",
                slot_material=slot,
                math_material={
                    "track": "THEOREM_ALIGNED", "release_mode": release_mode,
                    "applicability_track": "THEOREM_ALIGNED",
                    "battery_model": "THEOREM_NO_OVERFLOW_EXACT",
                    "battery_capacity": "1000000000",
                    "physical_initial_energy": config["projection_e0"][0],
                    "service_scale": "1",
                    "scheduler": "gpfp_asap_block",
                    "projection_methods": config["projection_methods"],
                    "projection_e0": config["projection_e0"],
                    "simulation_horizon": config["simulation_horizon"],
                    "release_horizon": config["simulation_horizon"][
                        "release_horizon"
                    ],
                    "observation_horizon": config["simulation_horizon"][
                        "observation_horizon"
                    ],
                    "source": config["source"],
                },
            )
            ordinal += 1
        for capacity in config["finite_battery_capacities"]:
            yield _record(
                core="CORE-3", ordinal=ordinal, kind="simulation",
                slot_material=slot,
                math_material={
                    "track": "FINITE_BATTERY_EMPIRICAL",
                    "applicability_track": "FINITE_BATTERY_EMPIRICAL",
                    "release_mode": "ASYNC_HASH_PHASE_V1",
                    "battery_capacity": capacity,
                    "battery_model": "FINITE_CAPACITY_EXACT",
                    "physical_initial_energy": config["projection_e0"][0],
                    "service_scale": "1",
                    "scheduler": "gpfp_asap_block",
                    "projection_methods": config["projection_methods"],
                    "projection_e0": config["projection_e0"],
                    "simulation_horizon": config["simulation_horizon"],
                    "release_horizon": config["simulation_horizon"][
                        "release_horizon"
                    ],
                    "observation_horizon": config["simulation_horizon"][
                        "observation_horizon"
                    ],
                    "source": config["source"],
                },
            )
            ordinal += 1


def core4_conditions_v3(config: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    baseline = dict(config["baseline"])
    conditions: list[Mapping[str, str]] = [{
        "axis": "baseline", "axis_value": "baseline", **baseline,
    }]
    for axis in ("e0", "service_scale", "power_scale", "deadline_slack_fraction"):
        for value in config["axes"][axis]:
            if value == baseline[axis]:
                continue
            conditions.append({
                "axis": axis, "axis_value": value, **baseline, axis: value,
            })
    for condition in conditions[1:]:
        changed = sum(
            condition[key] != baseline[key]
            for key in ("e0", "service_scale", "power_scale", "deadline_slack_fraction")
        )
        if changed != 1:
            raise RTA4FormalPlanV3Error("CORE-4 OFAT condition changed multiple axes")
    return tuple(conditions)


def _core4(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV3]:
    ordinal = 0
    conditions = core4_conditions_v3(config)
    for utilization in config["normalized_utilization"]:
        for replicate in range(config["skeletons_per_utilization"]):
            for condition in conditions:
                for method in config["methods"]:
                    yield _record(
                        core="CORE-4", ordinal=ordinal, kind="rta_request",
                        slot_material={
                            "namespace": "RTA4_CORE4_PARAMETERIZED_V3",
                            "processor_count": config["processors"],
                            "task_count": config["task_count"],
                            "normalized_utilization": utilization,
                            "replicate_index": replicate,
                            "deadline_slack_fraction": condition[
                                "deadline_slack_fraction"
                            ],
                            "deadline_variant": (
                                "fixed_slack_fraction_v1:"
                                + condition["deadline_slack_fraction"]
                            ),
                            "power_scale": condition["power_scale"],
                        },
                        skeleton_material={
                            "namespace": "RTA4_CORE4_PARAMETERIZED_V3",
                            "processor_count": config["processors"],
                            "task_count": config["task_count"],
                            "normalized_utilization": utilization,
                            "replicate_index": replicate,
                        },
                        math_material={"method": method, **condition},
                    )
                    ordinal += 1


def _core5a_axis(
    config: Mapping[str, Any], *, axis: str, values: Iterable[int], tasksets: int,
    processors: int, task_count: int,
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    baseline = config["baseline"]
    for value in values:
        for replicate in range(tasksets):
            slot = {
                "namespace": f"RTA4_CORE5A_{axis.upper()}_V3",
                "processor_count": value if axis == "processor_count" else processors,
                "task_count": value if axis == "task_count" else task_count,
                "normalized_utilization": baseline["normalized_utilization"],
                "replicate_index": replicate,
                "integer_time_scale": value if axis == "integer_time_scale" else 1,
                "deadline_variant": (
                    "fixed_slack_fraction_v1:"
                    + baseline["deadline_slack_fraction"]
                ),
                "power_scale": baseline["power_scale"],
            }
            yield slot, {"axis": axis, "axis_value": str(value), **baseline}


def _core5a(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV3]:
    ordinal = 0
    task_axis = config["task_count_axis"]
    processor_axis = config["processor_axis"]
    time_axis = config["integer_time_scale_axis"]
    axes = (
        _core5a_axis(
            config, axis="task_count", values=task_axis["values"],
            tasksets=task_axis["tasksets"], processors=task_axis["processors"],
            task_count=task_axis["values"][0],
        ),
        _core5a_axis(
            config, axis="processor_count", values=processor_axis["values"],
            tasksets=processor_axis["tasksets"], processors=processor_axis["values"][0],
            task_count=processor_axis["task_count"],
        ),
        _core5a_axis(
            config, axis="integer_time_scale", values=time_axis["values"],
            tasksets=time_axis["base_tasksets"], processors=task_axis["processors"],
            task_count=processor_axis["task_count"],
        ),
    )
    for axis_points in axes:
        for slot, material in axis_points:
            for method in config["methods"]:
                skeleton = dict(slot)
                skeleton.pop("integer_time_scale", None)
                yield _record(
                    core="CORE-5A", ordinal=ordinal, kind="rta_request",
                    slot_material=slot, skeleton_material=skeleton,
                    math_material={"method": method, **material},
                )
                ordinal += 1


def _core5b(config: Mapping[str, Any]) -> Iterator[FormalPlanRecordV3]:
    ordinal = 0
    source = config["source"]
    for stratum in config["utilization_strata"]:
        for method in config["methods"]:
            candidates = []
            for candidate in range(config["candidates_per_method_stratum"]):
                material = {
                    "selection_rule": RTA4_SELECTION_RULE_V3,
                    "source_plan_sha256": source["source_plan_sha256"],
                    "utilization_stratum": stratum,
                    "method": method, "candidate_index": candidate,
                }
                candidates.append((
                    domain_hash(RTA4_CORE5B_SELECTION_DOMAIN_V3, material), candidate,
                ))
            for selection_hash, candidate in sorted(candidates)[
                :config["selected_per_method_stratum"]
            ]:
                slot_material = {
                    "namespace": "RTA4_CORE5B_SELECTED_CORE4_BASELINE_V3",
                    "source_taskset_store_identity": source[
                        "source_taskset_store_identity"
                    ],
                    "utilization_stratum": stratum,
                    "candidate_index": candidate,
                }
                math = {
                    "method": method, "selection_hash": selection_hash,
                    "selection_rule": RTA4_SELECTION_RULE_V3,
                    "source": source,
                }
                for worker in config["workers"]:
                    yield _record(
                        core="CORE-5B", ordinal=ordinal, kind="worker_execution",
                        slot_material=slot_material, math_material=math,
                        execution_material={"worker_count": worker},
                    )
                    ordinal += 1


def expected_counts_v3(config: Mapping[str, Any]) -> Dict[str, int]:
    core = config["core"]
    if core == "CORE-1":
        skeletons = len(config["normalized_utilization"]) * config["tasksets_per_utilization"]
        mathematical = skeletons * len(config["e0"]) * len(config["methods"])
        return {"taskset_skeleton_count": skeletons, "mathematical_request_count": mathematical,
                "ordered_stream_count": mathematical}
    if core == "CORE-2":
        skeletons = config["source"]["taskset_count"]
        mathematical = skeletons * len(config["e0"]) * len(config["methods"])
        return {"taskset_skeleton_count": skeletons, "mathematical_request_count": mathematical,
                "ordered_stream_count": mathematical}
    if core == "CORE-3":
        skeletons = config["source"]["taskset_count"]
        mathematical = skeletons * (
            len(config["release_modes"]) + len(config["finite_battery_capacities"])
        )
        return {"taskset_skeleton_count": skeletons, "mathematical_request_count": mathematical,
                "ordered_stream_count": mathematical}
    if core == "CORE-4":
        skeletons = len(config["normalized_utilization"]) * config["skeletons_per_utilization"]
        mathematical = skeletons * len(core4_conditions_v3(config)) * len(config["methods"])
        return {"taskset_skeleton_count": skeletons, "mathematical_request_count": mathematical,
                "ordered_stream_count": mathematical}
    if core == "CORE-5A":
        skeletons = (
            len(config["task_count_axis"]["values"]) * config["task_count_axis"]["tasksets"]
            + len(config["processor_axis"]["values"]) * config["processor_axis"]["tasksets"]
            + len(config["integer_time_scale_axis"]["values"])
            * config["integer_time_scale_axis"]["base_tasksets"]
        )
        mathematical = skeletons * len(config["methods"])
        return {"taskset_skeleton_count": skeletons, "mathematical_request_count": mathematical,
                "ordered_stream_count": mathematical}
    if core == "CORE-5B":
        mathematical = (
            len(config["utilization_strata"]) * len(config["methods"])
            * config["selected_per_method_stratum"]
        )
        stream = mathematical * len(config["workers"])
        return {"taskset_skeleton_count": mathematical,
                "mathematical_request_count": mathematical,
                "ordered_stream_count": stream}
    raise RTA4FormalPlanV3Error(f"unknown V3 core: {core!r}")


_ITERATORS = {
    "CORE-1": _core1, "CORE-2": _core2, "CORE-3": _core3,
    "CORE-4": _core4, "CORE-5A": _core5a, "CORE-5B": _core5b,
}


def iter_formal_plan_v3(
    scientific_config: Mapping[str, Any], *,
    observed_source_binding: Mapping[str, Any] | None = None,
) -> Iterator[FormalPlanRecordV3]:
    rta4_formal_config_hash_v3(scientific_config)
    source = source_binding_v3(scientific_config)
    if source is not None and observed_source_binding is not None:
        validate_source_binding_v3(scientific_config, observed_source_binding)
    yield from _ITERATORS[str(scientific_config["core"])](scientific_config)


def ordered_stream_digest_v3(records: Iterable[FormalPlanRecordV3]) -> StreamDigestV3:
    digest = hashlib.sha256(RTA4_STREAM_DIGEST_DOMAIN_V3)
    count = 0
    for count, record in enumerate(records, start=1):
        encoded = canonical_json(record.canonical_material()).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return StreamDigestV3(count, digest.hexdigest())


def describe_formal_plan_v3(
    scientific_config: Mapping[str, Any], *,
    observed_source_binding: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    scientific_hash = rta4_formal_config_hash_v3(scientific_config)
    expected = expected_counts_v3(scientific_config)
    stream = ordered_stream_digest_v3(iter_formal_plan_v3(
        scientific_config, observed_source_binding=observed_source_binding,
    ))
    if stream.count != expected["ordered_stream_count"]:
        raise RTA4FormalPlanV3Error(
            f"dynamic plan count mismatch: expected {expected['ordered_stream_count']}, "
            f"observed {stream.count}"
        )
    identity_material = {
        "profile": RTA4_FORMAL_PROFILE_V3,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V3,
        "core": scientific_config["core"],
        "normalized_scientific_config_sha256": scientific_hash,
        "formal_schema_sha256": formal_schema_hash_v3(),
        "dynamic_counts": expected,
        "ordered_stream_digest": stream.sha256,
        "taskset_store_identity": formal_taskset_store_identity_v3(scientific_config),
        "source_binding": source_binding_v3(scientific_config),
    }
    return {
        **expected,
        "profile": RTA4_FORMAL_PROFILE_V3,
        "core": scientific_config["core"],
        "normalized_scientific_config_sha256": scientific_hash,
        "ordered_stream_digest": stream.sha256,
        "taskset_store_identity": identity_material["taskset_store_identity"],
        "source_binding": identity_material["source_binding"],
        "plan_sha256": domain_hash(RTA4_PLAN_DOMAIN_V3, identity_material),
        "identity_material": identity_material,
    }


__all__ = [
    "FormalPlanRecordV3", "RTA4FormalPlanV3Error", "StreamDigestV3",
    "core4_conditions_v3", "describe_formal_plan_v3", "expected_counts_v3",
    "iter_formal_plan_v3", "ordered_stream_digest_v3",
]
