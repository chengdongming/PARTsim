"""Streaming request plans for the five v9.3 four-level experiments."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
from typing import Any, Dict, Iterable, Iterator, Mapping, Sequence

import asap_block_rta_v9_3_methods as method_registry

from . import exact_energy
from .constrained_taskset_identity import (
    GENERATION_REQUEST_CONTRACT_VERSION,
    TASKSET_IDENTITY_CONTRACT_VERSION,
)
from .release_applicability import (
    ASYNC_HASH_PHASE_V1,
    FINITE_BATTERY_EMPIRICAL,
    RELEASE_HORIZON,
    RELEASE_PROJECTION_CONTRACT_VERSION,
    SIMULATION_APPLICABILITY_CONTRACT_VERSION,
    SYNC_V1,
    THEOREM_ALIGNED,
)
from .rta4_formal_config import (
    RTA4_CORE2_METHODS,
    RTA4_FORMAL_PLAN_VERSION,
    RTA4_FORMAL_PROFILE,
    RTA4_FORMAL_STORE_VERSION,
    RTA4_RECURSIVE_METHODS,
    canonical_json,
    domain_hash,
    method_registry_identity,
    rta4_formal_config_hash,
    validate_rta4_formal_config,
)
from .rta4_formal_plan_grid import (
    FormalPlanGridPoint,
    TasksetGridSpec,
    core4_grid_point,
    iter_core1_grid,
    iter_core2_grid,
    iter_core2_source_reference_grid,
    iter_core3_grid,
    iter_core4_grid,
    iter_core5a_grid,
    iter_core5b_grid,
    iter_core5b_selected_sources,
)


RTA4_PLAN_RECORD_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN_RECORD:v1"
RTA4_MATH_REQUEST_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_MATH_REQUEST:v1"
RTA4_EXECUTION_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_EXECUTION:v1"
RTA4_SIMULATION_PLAN_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SIMULATION_PLAN:v1"
RTA4_TASKSET_SLOT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_TASKSET_SLOT:v1"
RTA4_SKELETON_SLOT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SKELETON_SLOT:v1"
RTA4_PLAN_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_PLAN:v1"
RTA4_STREAM_DIGEST_DOMAIN = b"ASAP_BLOCK:V9.3:RTA4_ORDERED_STREAM:v1\0"
RTA4_CORE5B_SELECTION_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_CORE5B_SELECTION:v1"
RTA4_SERVICE_SCALE_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SERVICE_SCALE:v1"
RTA4_BASE_SERVICE_IDENTITY = domain_hash(
    "ASAP_BLOCK:V9.3:RTA4_BASE_SERVICE:v1",
    {"profile": RTA4_FORMAL_PROFILE, "service_contract": "FROZEN_BASE_SERVICE_V1"},
)


class RTA4FormalPlanError(ValueError):
    """Raised when a plan cannot satisfy the exact frozen design."""


@dataclass(frozen=True)
class FormalPlanRecord:
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
            "plan_version": RTA4_FORMAL_PLAN_VERSION,
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
        return domain_hash(RTA4_PLAN_RECORD_DOMAIN, self.canonical_material())


@dataclass(frozen=True)
class StreamDigest:
    count: int
    sha256: str


def ordered_stream_digest(records: Iterable[FormalPlanRecord]) -> StreamDigest:
    digest = hashlib.sha256()
    digest.update(RTA4_STREAM_DIGEST_DOMAIN)
    count = 0
    for count, record in enumerate(records, start=1):
        encoded = canonical_json(record.canonical_material()).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return StreamDigest(count, digest.hexdigest())


def ordered_material_digest(rows: Iterable[Mapping[str, Any]], domain: str) -> StreamDigest:
    digest = hashlib.sha256(domain.encode("ascii") + b"\0")
    count = 0
    for count, row in enumerate(rows, start=1):
        encoded = canonical_json(row).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return StreamDigest(count, digest.hexdigest())


def _slot_material(
    namespace: str, utilization: str, replicate: int, *,
    processors: int = 4, task_count: int = 10, scenario: str = "MAIN",
) -> Dict[str, Any]:
    return {
        "namespace": namespace,
        "scenario": scenario,
        "processor_count": processors,
        "task_count": task_count,
        "normalized_utilization": utilization,
        "replicate_index": replicate,
    }


def _slots(
    namespace: str, utilization: str, replicate: int, *,
    processors: int = 4, task_count: int = 10, scenario: str = "MAIN",
    deadline: str = "constrained_uniform_slack_v1", power_scale: str = "1",
    time_scale: int = 1,
) -> tuple[str, str]:
    skeleton_material = _slot_material(
        namespace, utilization, replicate, processors=processors,
        task_count=task_count, scenario=scenario,
    )
    if time_scale != 1:
        skeleton_material = {**skeleton_material, "integer_time_scale": time_scale}
    skeleton = domain_hash(RTA4_SKELETON_SLOT_DOMAIN, skeleton_material)
    taskset = domain_hash(RTA4_TASKSET_SLOT_DOMAIN, {
        "taskset_skeleton_slot_id": skeleton,
        "deadline_variant": deadline,
        "power_scale": power_scale,
        "integer_time_scale": time_scale,
    })
    return skeleton, taskset


def _method_material(method: str) -> Dict[str, Any]:
    spec = method_registry.method_spec_v9_3(method)
    return {
        "method": spec.method_id.value,
        "kernel": spec.kernel.value,
        "carry_policy": spec.carry_policy.value,
        "dominance_rank": spec.dominance_rank,
    }


def exact_service_scale_identity(base_service_identity: str, scale: Fraction) -> str:
    if not isinstance(base_service_identity, str) or len(base_service_identity) != 64:
        raise RTA4FormalPlanError("base service identity must be SHA-256")
    if type(scale) is not Fraction or scale <= 0:
        raise RTA4FormalPlanError("service scale must be a positive exact Fraction")
    return domain_hash(RTA4_SERVICE_SCALE_DOMAIN, {
        "base_service_identity": base_service_identity,
        "scale": {
            "numerator": scale.numerator, "denominator": scale.denominator,
        },
    })


def formal_service_identity(scale: Any) -> str:
    """Return the exact frozen service identity for one plan scale."""

    if isinstance(scale, bool) or isinstance(scale, float):
        raise RTA4FormalPlanError("service scale must be exact rational data")
    try:
        exact = scale if type(scale) is Fraction else Fraction(scale)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise RTA4FormalPlanError("invalid service scale") from exc
    return exact_service_scale_identity(RTA4_BASE_SERVICE_IDENTITY, exact)


def _rta_record(
    *, core: str, ordinal: int, skeleton: str, taskset: str, method: str,
    e0: str, service_scale: str = "1", power_scale: str = "1",
    deadline_variant: str = "constrained_uniform_slack_v1",
    scenario: str = "MAIN", axis: str = "baseline", axis_value: str = "baseline",
    timeout_contract: str = "UNFROZEN_PRE_PILOT",
    source_analysis_id: str | None = None,
    normalized_utilization: str = "1/2", processor_count: int = 4,
    task_count: int = 10, replicate_index: int = 0,
) -> FormalPlanRecord:
    mathematical = {
        "profile": RTA4_FORMAL_PROFILE,
        "core": core,
        "scenario": scenario,
        "taskset_skeleton_slot_id": skeleton,
        "taskset_slot_id": taskset,
        **_method_material(method),
        "exact_e0": e0,
        "service_scale": service_scale,
        "power_scale": power_scale,
        "deadline_variant": deadline_variant,
        "axis": axis,
        "axis_value": axis_value,
        "timeout_contract": timeout_contract,
        "source_analysis_id": source_analysis_id,
        "normalized_utilization": normalized_utilization,
        "processor_count": processor_count,
        "task_count": task_count,
        "replicate_index": replicate_index,
    }
    request_id = domain_hash(RTA4_MATH_REQUEST_DOMAIN, mathematical)
    execution_id = domain_hash(RTA4_EXECUTION_DOMAIN, {
        "mathematical_request_id": request_id,
        "worker_count": 1,
        "execution_role": "PRIMARY",
    })
    return FormalPlanRecord(
        "rta_request", core, ordinal, request_id, execution_id,
        taskset, skeleton, mathematical,
    )


def _grid_slots(slot: TasksetGridSpec) -> tuple[str, str]:
    return _slots(
        slot.namespace,
        slot.normalized_utilization,
        slot.replicate_index,
        processors=slot.processor_count,
        task_count=slot.task_count,
        scenario=slot.scenario,
        deadline=slot.deadline_variant,
        power_scale=slot.power_scale,
        time_scale=slot.integer_time_scale,
    )


def _rta_record_from_grid(
    point: FormalPlanGridPoint, *, ordinal: int | None = None,
) -> FormalPlanRecord:
    material = point.material
    skeleton, taskset = _grid_slots(point.slot)
    return _rta_record(
        core=point.core,
        ordinal=point.ordinal if ordinal is None else ordinal,
        skeleton=skeleton,
        taskset=taskset,
        method=str(material["method"]),
        e0=str(material["exact_e0"]),
        service_scale=str(material["service_scale"]),
        power_scale=str(material["power_scale"]),
        deadline_variant=str(material["deadline_variant"]),
        scenario=str(material["scenario"]),
        axis=str(material["axis"]),
        axis_value=str(material["axis_value"]),
        timeout_contract=str(material["timeout_contract"]),
        normalized_utilization=str(material["normalized_utilization"]),
        processor_count=int(material["processor_count"]),
        task_count=int(material["task_count"]),
        replicate_index=int(material["replicate_index"]),
    )


def iter_core1_plan() -> Iterator[FormalPlanRecord]:
    for point in iter_core1_grid(RTA4_RECURSIVE_METHODS):
        yield _rta_record_from_grid(point)


def iter_core2_plan() -> Iterator[FormalPlanRecord]:
    for point in iter_core2_grid(RTA4_CORE2_METHODS):
        yield _rta_record_from_grid(point)


def iter_core2_source_references() -> Iterator[Dict[str, Any]]:
    """Reference CORE-1 LOC/PH analyses without creating CORE-2 requests."""

    for point in iter_core2_source_reference_grid():
        source = _rta_record_from_grid(point)
        yield {
            "source_core": "CORE-1",
            "target_core": "CORE-2",
            "taskset_slot_id": source.taskset_slot_id,
            "method": point.material["method"],
            "exact_e0": point.material["exact_e0"],
            "source_analysis_id": source.mathematical_request_id,
        }


def iter_core3_plan() -> Iterator[FormalPlanRecord]:
    for point in iter_core3_grid():
        skeleton, taskset = _grid_slots(point.slot)
        material = {
            "profile": RTA4_FORMAL_PROFILE,
            "taskset_skeleton_slot_id": skeleton,
            "taskset_slot_id": taskset,
            **point.material,
        }
        simulation_id = domain_hash(RTA4_SIMULATION_PLAN_DOMAIN, material)
        yield FormalPlanRecord(
            "simulation", "CORE-3", point.ordinal, None, simulation_id,
            taskset, skeleton, material,
        )


def core3_comparisons_for_simulation(
    simulation: FormalPlanRecord,
) -> Iterator[Dict[str, Any]]:
    if simulation.kind != "simulation" or simulation.core != "CORE-3":
        raise RTA4FormalPlanError("CORE-3 comparison requires a CORE-3 simulation")
    for method in RTA4_RECURSIVE_METHODS:
        for e0 in ("0", "1/20", "1"):
            source = _rta_record(
                core="CORE-1", ordinal=0,
                skeleton=str(simulation.taskset_skeleton_slot_id),
                taskset=str(simulation.taskset_slot_id),
                method=method, e0=e0,
                normalized_utilization=str(
                    simulation.material["normalized_utilization"]
                ),
                processor_count=int(simulation.material["processor_count"]),
                task_count=int(simulation.material["task_count"]),
                replicate_index=int(simulation.material["replicate_index"]),
            )
            yield {
                "simulation_id": simulation.execution_id,
                "source_analysis_id": source.mathematical_request_id,
                "taskset_slot_id": simulation.taskset_slot_id,
                "method": method,
                "exact_e0": e0,
            }


def iter_core3_comparison_plan() -> Iterator[Dict[str, Any]]:
    """Stream the complete 6,400 x 4 x 3 applicability projection."""

    for simulation in iter_core3_plan():
        yield from core3_comparisons_for_simulation(simulation)


def _core4_conditions() -> tuple[tuple[str, str, str, str, str, str], ...]:
    from .rta4_formal_plan_grid import core4_conditions

    return core4_conditions()


def _core4_record(
    utilization: str, replicate: int, condition_index: int, method: str,
    ordinal: int,
) -> FormalPlanRecord:
    return _rta_record_from_grid(
        core4_grid_point(
            utilization, replicate, condition_index, method, ordinal,
        )
    )


def iter_core4_plan() -> Iterator[FormalPlanRecord]:
    for point in iter_core4_grid(RTA4_RECURSIVE_METHODS):
        yield _rta_record_from_grid(point)


def iter_core5a_plan() -> Iterator[FormalPlanRecord]:
    for point in iter_core5a_grid(RTA4_RECURSIVE_METHODS):
        yield _rta_record_from_grid(point)


def _core5b_ranker(point: FormalPlanGridPoint) -> tuple[str, str, str]:
    source = _rta_record_from_grid(point)
    source_id = str(source.mathematical_request_id)
    selection_hash = domain_hash(RTA4_CORE5B_SELECTION_DOMAIN, {
        "source_analysis_id": source_id,
        "utilization_stratum": point.material["normalized_utilization"],
        "method": point.material["method"],
    })
    return selection_hash, source_id, source_id


def iter_core5b_math_references() -> Iterator[FormalPlanRecord]:
    selected_sources = iter_core5b_selected_sources(
        RTA4_RECURSIVE_METHODS, _core5b_ranker,
    )
    for ordinal, selected in enumerate(selected_sources):
        source = _rta_record_from_grid(selected.point)
        material = {
            "selection_hash": selected.selection_hash,
            "source_core": "CORE-4",
            "source_analysis_id": source.mathematical_request_id,
            "method": source.material["method"],
            "taskset_slot_id": source.taskset_slot_id,
            "taskset_skeleton_slot_id": source.taskset_skeleton_slot_id,
            "normalized_utilization": source.material["normalized_utilization"],
            "processor_count": source.material["processor_count"],
            "task_count": source.material["task_count"],
            "replicate_index": source.material["replicate_index"],
        }
        yield FormalPlanRecord(
            "math_reference", "CORE-5B", ordinal,
            source.mathematical_request_id, None, source.taskset_slot_id,
            source.taskset_skeleton_slot_id, material,
        )


def iter_core5b_plan() -> Iterator[FormalPlanRecord]:
    for point in iter_core5b_grid(RTA4_RECURSIVE_METHODS, _core5b_ranker):
        skeleton, taskset = _grid_slots(point.slot)
        execution = {
            "mathematical_request_id": point.source_mathematical_request_id,
            **point.material,
        }
        execution_id = domain_hash(RTA4_EXECUTION_DOMAIN, execution)
        yield FormalPlanRecord(
            "worker_execution", "CORE-5B", point.ordinal,
            point.source_mathematical_request_id, execution_id,
            taskset, skeleton, execution,
        )


PLAN_ITERATORS = {
    "CORE-1": iter_core1_plan,
    "CORE-2": iter_core2_plan,
    "CORE-3": iter_core3_plan,
    "CORE-4": iter_core4_plan,
    "CORE-5A": iter_core5a_plan,
    "CORE-5B": iter_core5b_plan,
}


def iter_formal_plan(config: Mapping[str, Any]) -> Iterator[FormalPlanRecord]:
    normalized = validate_rta4_formal_config(config)
    yield from PLAN_ITERATORS[normalized["core"]]()


def _expected_counts(core: str) -> Dict[str, int]:
    return {
        "CORE-1": {"unique_tasksets": 1600, "unique_skeletons": 1600, "rta_requests": 19200},
        "CORE-2": {"reused_tasksets": 1600, "reused_skeletons": 1600, "rta_requests": 28800, "source_analysis_references": 9600},
        "CORE-3": {"new_rta_requests": 0, "reused_tasksets": 1600, "reused_skeletons": 1600, "simulations": 6400, "applicability_comparisons": 76800},
        "CORE-4": {"unique_skeletons": 1000, "conditions_per_skeleton": 18, "rta_requests": 72000},
        "CORE-5A": {"unique_scenario_tasksets": 1100, "unique_scenario_skeletons": 1100, "rta_requests": 4400},
        "CORE-5B": {"unique_mathematical_requests": 3000, "executions": 12000},
    }[core]


def describe_formal_plan(config: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_rta4_formal_config(config)
    core = normalized["core"]
    stream = ordered_stream_digest(iter_formal_plan(normalized))
    counts = _expected_counts(core)
    expected_stream_count = (
        counts.get("rta_requests")
        or counts.get("simulations")
        or counts.get("executions")
    )
    if stream.count != expected_stream_count:
        raise RTA4FormalPlanError(
            f"{core} stream count drift: expected {expected_stream_count}, got {stream.count}"
        )
    selection_digest = None
    source_relation_digest = None
    applicability_projection_digest = None
    if core == "CORE-5B":
        references = tuple(iter_core5b_math_references())
        selection_digest = ordered_stream_digest(iter(references)).sha256
        counts["selected_tasksets"] = len({row.taskset_slot_id for row in references})
        counts["selected_skeletons"] = len({row.taskset_skeleton_slot_id for row in references})
    elif core == "CORE-2":
        source_stream = ordered_material_digest(
            iter_core2_source_references(),
            "ASAP_BLOCK:V9.3:RTA4_CORE2_SOURCE_REFERENCES:v1",
        )
        if source_stream.count != 9600:
            raise RTA4FormalPlanError("CORE-2 source reference count drift")
        source_relation_digest = source_stream.sha256
    elif core == "CORE-3":
        comparison_stream = ordered_material_digest(
            iter_core3_comparison_plan(),
            "ASAP_BLOCK:V9.3:RTA4_CORE3_COMPARISON_PROJECTION:v1",
        )
        if comparison_stream.count != 76800:
            raise RTA4FormalPlanError("CORE-3 comparison projection count drift")
        applicability_projection_digest = comparison_stream.sha256
    schema_hash = _schema_hash()
    config_hash = rta4_formal_config_hash(normalized)
    plan_material = {
        "profile": RTA4_FORMAL_PROFILE,
        "plan_version": RTA4_FORMAL_PLAN_VERSION,
        "core": core,
        "config_semantic_hash": config_hash,
        "schema_sha256": schema_hash,
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
        "generator_contract": GENERATION_REQUEST_CONTRACT_VERSION,
        "taskset_identity_contract": TASKSET_IDENTITY_CONTRACT_VERSION,
        "method_registry_identity": method_registry_identity(),
        "release_contract": RELEASE_PROJECTION_CONTRACT_VERSION,
        "simulation_applicability_contract": SIMULATION_APPLICABILITY_CONTRACT_VERSION,
        "taskset_store_identity": _taskset_store_identity(),
        "ordered_request_or_simulation_digest": stream.sha256,
        "selection_manifest_digest": selection_digest,
        "source_analysis_relation_digest": source_relation_digest,
        "applicability_projection_digest": applicability_projection_digest,
    }
    methods = normalized["plan"].get("methods") or normalized["plan"].get("projection_methods")
    e0_grid = normalized["plan"].get("e0") or normalized["plan"].get("projection_e0")
    if not e0_grid and "axes" in normalized["plan"]:
        e0_grid = normalized["plan"]["axes"].get("e0", [])
    if not e0_grid and "baseline" in normalized["plan"]:
        baseline_e0 = normalized["plan"]["baseline"].get("e0")
        e0_grid = [] if baseline_e0 is None else [baseline_e0]
    return {
        "profile": RTA4_FORMAL_PROFILE,
        "plan_version": RTA4_FORMAL_PLAN_VERSION,
        "core": core,
        "parameter_status": normalized["experiment_contract"]["parameter_status"],
        "authorization_status": "REJECTED_UNTIL_PR_E",
        "counts": counts,
        "methods": methods or [],
        "e0_grid": e0_grid or [],
        "schema_sha256": schema_hash,
        "config_semantic_hash": config_hash,
        "ordered_stream_count": stream.count,
        "ordered_stream_digest": stream.sha256,
        "selection_manifest_digest": selection_digest,
        "source_analysis_relation_digest": source_relation_digest,
        "applicability_projection_digest": applicability_projection_digest,
        "plan_sha256": domain_hash(RTA4_PLAN_DOMAIN, plan_material),
        "identity_material": plan_material,
    }


def describe_all_formal_plans(configs: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    if set(configs) != set(PLAN_ITERATORS):
        raise RTA4FormalPlanError("all six CORE plan configurations are required")
    descriptions = {core: describe_formal_plan(configs[core]) for core in PLAN_ITERATORS}
    unique_rta = sum(
        descriptions[core]["counts"].get("rta_requests", 0)
        for core in ("CORE-1", "CORE-2", "CORE-4", "CORE-5A")
    )
    return {
        "profile": RTA4_FORMAL_PROFILE,
        "plans": descriptions,
        "total_unique_rta_requests": unique_rta,
        "total_simulations": descriptions["CORE-3"]["counts"]["simulations"],
        "core5b_mathematical_requests": descriptions["CORE-5B"]["counts"]["unique_mathematical_requests"],
        "core5b_executions": descriptions["CORE-5B"]["counts"]["executions"],
        "all_plan_digest": domain_hash(RTA4_PLAN_DOMAIN, {
            core: descriptions[core]["plan_sha256"] for core in PLAN_ITERATORS
        }),
    }


def _schema_hash() -> str:
    from .rta4_formal_schema import formal_schema_hash
    return formal_schema_hash()


def _taskset_store_identity() -> str:
    from .rta4_formal_store import formal_taskset_store_identity
    return formal_taskset_store_identity()


__all__ = [
    "FormalPlanRecord", "PLAN_ITERATORS", "RTA4FormalPlanError",
    "StreamDigest", "core3_comparisons_for_simulation",
    "describe_all_formal_plans", "describe_formal_plan",
    "exact_service_scale_identity",
    "formal_service_identity",
    "iter_core1_plan", "iter_core2_plan", "iter_core2_source_references",
    "iter_core3_comparison_plan", "iter_core3_plan", "iter_core4_plan",
    "iter_core5a_plan", "iter_core5b_math_references", "iter_core5b_plan",
    "iter_formal_plan", "ordered_material_digest", "ordered_stream_digest",
]
