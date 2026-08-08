"""V5 identity layer over the frozen V3 six-core plan stream."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
from typing import Any, Iterable, Iterator, Mapping, Sequence

from experiments.common.exact_service_curve import (
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
    ExactServiceCurve,
    fraction_text,
    materialize_exact_service_curve,
    normalize_exact_service_curve,
    scale_exact_service_curve,
)

from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v3 import (
    formal_taskset_store_identity_v3,
    rta4_formal_config_hash_v3,
)
from .rta4_formal_config_v5 import (
    CORE3_SIMULATION_CONTRACT_V7,
    CORE5A_SCALED_E0_V1,
    CORE5A_SCALED_LATENCY_SERVICE_V1,
    RTA4_FORMAL_PLAN_VERSION_V5,
    RTA4_FORMAL_PROFILE_V5,
    TaskSourceBindingV5,
    formal_taskset_store_identity_v5,
    rta4_formal_config_hash_v5,
    source_closure_identity_v5,
)
from .rta4_energy_service_v5 import (
    core3_simulation_projection_v5,
    core3_simulation_projection_v6,
)
from .rta4_formal_plan_v3 import (
    describe_formal_plan_v3,
    expected_counts_v3,
    iter_formal_plan_v3,
)
from .rta4_formal_schema_v5 import formal_schema_hash_v5
from .rta4_physical_core_slots_v3 import PHYSICAL_CORE_EXECUTION_BACKEND_V3
from .rta4_task_source_v4 import (
    TasksetV4,
    revalidate_task_source_v4,
)


RTA4_PLAN_RECORD_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:PLAN_RECORD:v5"
RTA4_MATH_REQUEST_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:MATH_REQUEST:v5"
RTA4_EXECUTION_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:EXECUTION:v5"
RTA4_PLAN_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:FORMAL_PLAN:v5"
RTA4_STREAM_DIGEST_DOMAIN_V5 = b"ASAP_BLOCK:V9.3:RTA4:ORDERED_STREAM:v5\0"
RTA4_TASKSET_SLOT_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:TASKSET_SLOT:v5"


class RTA4FormalPlanV5Error(ValueError):
    """Raised when a V3 grid, V4 source or exact service binding drifts."""


@dataclass(frozen=True)
class FormalPlanRecordV5:
    kind: str
    core: str
    ordinal: int
    mathematical_request_id: str
    execution_id: str
    taskset_slot_id: str
    taskset_identity: str
    configured_service_identity: str
    effective_service_identity: str
    v3_record_id: str
    material: Mapping[str, Any]

    def canonical_material(self) -> dict[str, Any]:
        return {
            "plan_version": RTA4_FORMAL_PLAN_VERSION_V5,
            "kind": self.kind,
            "core": self.core,
            "ordinal": self.ordinal,
            "mathematical_request_id": self.mathematical_request_id,
            "execution_id": self.execution_id,
            "taskset_slot_id": self.taskset_slot_id,
            "taskset_identity": self.taskset_identity,
            "configured_service_identity": self.configured_service_identity,
            "effective_service_identity": self.effective_service_identity,
            "v3_record_id": self.v3_record_id,
            "material": dict(self.material),
        }

    @property
    def record_id(self) -> str:
        return domain_hash(RTA4_PLAN_RECORD_DOMAIN_V5, self.canonical_material())


@dataclass(frozen=True)
class StreamDigestV5:
    count: int
    sha256: str


def _validate_inputs(
    scientific: Mapping[str, Any],
    bindings: Sequence[TaskSourceBindingV5],
    service: ExactServiceCurve,
) -> tuple[TaskSourceBindingV5, ...]:
    rta4_formal_config_hash_v5(scientific)
    if type(service) is not ExactServiceCurve:
        raise RTA4FormalPlanV5Error("plan requires a normalized exact service")
    if (
        service.identity != scientific["service_curve_identity"]
        or dict(service.normalized_config) != scientific["service_curve"]
    ):
        raise RTA4FormalPlanV5Error("configured service identity drift")
    if not isinstance(bindings, (tuple, list)) or not bindings:
        raise RTA4FormalPlanV5Error("plan requires exact task-source bindings")
    validated = []
    for binding in bindings:
        if type(binding) is not TaskSourceBindingV5:
            raise RTA4FormalPlanV5Error("task source was not normalized by V5")
        try:
            observed = revalidate_task_source_v4(binding.source)
        except Exception as exc:
            raise RTA4FormalPlanV5Error(
                "task source runtime revalidation failed"
            ) from exc
        validated.append(TaskSourceBindingV5(
            binding.axis, binding.axis_value, observed,
        ))
    if [binding.material() for binding in validated] != scientific[
        "task_source_bindings"
    ]:
        raise RTA4FormalPlanV5Error("task source binding identity drift")
    v3 = scientific["v3_plan_grid"]
    if rta4_formal_config_hash_v3(v3) != scientific["v3_plan_grid_identity"]:
        raise RTA4FormalPlanV5Error("V3 plan-grid identity drift")
    return tuple(validated)


def _source_key(record: Any, v3: Mapping[str, Any]) -> tuple[str, str, int]:
    material = record.material
    core = record.core
    if core in {"CORE-1", "CORE-4"}:
        utilizations = v3["normalized_utilization"]
        per_utilization = (
            v3["tasksets_per_utilization"]
            if core == "CORE-1" else v3["skeletons_per_utilization"]
        )
        index = (
            utilizations.index(material["normalized_utilization"])
            * per_utilization
            + int(material["replicate_index"])
        )
        return "campaign", "all", index
    if core in {"CORE-2", "CORE-3"}:
        return "campaign", "all", int(material["source_taskset_index"])
    if core == "CORE-5B":
        stratum = v3["utilization_strata"].index(material["utilization_stratum"])
        index = (
            stratum * v3["candidates_per_method_stratum"]
            + int(material["candidate_index"])
        )
        return "campaign", "all", index
    if core == "CORE-5A":
        return (
            str(material["axis"]),
            str(material["axis_value"]),
            int(material["replicate_index"]),
        )
    raise RTA4FormalPlanV5Error(f"unknown V5 core: {core!r}")


def _select_taskset(
    record: Any, v3: Mapping[str, Any],
    bindings: tuple[TaskSourceBindingV5, ...],
) -> tuple[TaskSourceBindingV5, int, TasksetV4]:
    axis, axis_value, index = _source_key(record, v3)
    matches = [
        binding for binding in bindings
        if (binding.axis, binding.axis_value) == (axis, axis_value)
    ]
    if len(matches) != 1:
        raise RTA4FormalPlanV5Error("record has no unique exact task source")
    source = matches[0].source
    try:
        taskset = source.taskset(index)
    except Exception as exc:
        raise RTA4FormalPlanV5Error(
            "record taskset index exceeds bound source"
        ) from exc
    return matches[0], index, taskset


def _effective_service(
    base: ExactServiceCurve, record: Any, scientific: Mapping[str, Any],
) -> ExactServiceCurve:
    scale = str(record.material.get("service_scale", "1"))
    curve = scale_exact_service_curve(base, scale)
    if (
        record.core == "CORE-5A"
        and record.material.get("axis") == "integer_time_scale"
        and scientific["integer_time_scale_service_semantics"]
        == CORE5A_SCALED_LATENCY_SERVICE_V1
        and curve.model == EXACT_RATE_LATENCY_SERVICE_CURVE_V1
    ):
        integer_scale = int(record.material["axis_value"])
        curve = normalize_exact_service_curve({
            "model": curve.model,
            "rate": fraction_text(curve.rate),
            "latency": fraction_text(curve.latency * integer_scale),
            "time_unit": "tick",
        })
    return curve


def _service_material_for_record(
    record: Any, taskset: TasksetV4, curve: ExactServiceCurve,
    simulation_tick_ms: int | None,
    cache: dict[tuple[str, int, int, str], dict[str, Any]],
    effective_core3_material: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if record.core != "CORE-3":
        return None
    maximum = (
        int(effective_core3_material["observation_horizon"])
        if effective_core3_material is not None
        else int(record.material["release_horizon"])
        + max(task.D for task in taskset.tasks)
    )
    if type(simulation_tick_ms) is not int or simulation_tick_ms <= 0:
        raise RTA4FormalPlanV5Error(
            "CORE-3 plan requires a positive simulation_tick_ms"
        )
    contract = (
        effective_core3_material.get("core3_simulation_contract")
        if effective_core3_material is not None else None
    )
    core3_v7 = (
        isinstance(contract, Mapping)
        and contract.get("contract_version")
        == CORE3_SIMULATION_CONTRACT_V7
    )
    model_energy_unit_joules = (
        str(effective_core3_material["model_energy_unit_joules"])
        if core3_v7 else ""
    )
    key = (
        curve.identity, maximum, simulation_tick_ms,
        model_energy_unit_joules,
    )
    if key in cache:
        return cache[key]
    material = materialize_exact_service_curve(curve, maximum)
    projection = (
        core3_simulation_projection_v6(
            exact_service_material_identity=material.identity,
            harvest_trace=material.harvest_trace,
            simulation_tick_ms=simulation_tick_ms,
            model_energy_unit_joules=model_energy_unit_joules,
        )
        if core3_v7
        else core3_simulation_projection_v5(
            exact_service_material_identity=material.identity,
            harvest_trace=material.harvest_trace,
            simulation_tick_ms=simulation_tick_ms,
        )
    )
    summary = {
        "maximum_length": maximum,
        "material_identity": material.identity,
        "trace_sha256": material.trace_sha256,
        "prefix_trace_equality": True,
        "simulation_tick_ms": simulation_tick_ms,
        "simulation_projection": projection,
    }
    cache[key] = summary
    return summary


def _effective_core3_simulation_material(
    scientific: Mapping[str, Any], record: Any, taskset: TasksetV4,
    *, effective_release_mode: str | None = None,
) -> dict[str, Any] | None:
    contract = scientific.get("core3_simulation_contract")
    if record.core != "CORE-3" or not isinstance(contract, Mapping):
        return None
    source = dict(record.material)
    if effective_release_mode is not None:
        source["release_mode"] = effective_release_mode
    release_horizon = int(source["release_horizon"])
    dmax = max(task.D for task in taskset.tasks)
    observation_horizon = release_horizon + dmax
    capacity = (
        str(contract[
            "theorem_battery_capacity_model_units"
            if contract.get("contract_version")
            == CORE3_SIMULATION_CONTRACT_V7
            else "theorem_battery_capacity"
        ])
        if source["track"] == "THEOREM_ALIGNED"
        else str(source["battery_capacity"])
    )
    core3_v7 = (
        contract.get("contract_version") == CORE3_SIMULATION_CONTRACT_V7
    )
    physical = str(contract[
        "physical_initial_energy_model_units"
        if core3_v7 else "physical_initial_energy"
    ])
    if Fraction(physical) > Fraction(capacity):
        raise RTA4FormalPlanV5Error(
            "CORE-3 effective initial energy exceeds battery capacity"
        )
    common = {
        **source,
        "release_horizon": release_horizon,
        "dmax": dmax,
        "observation_horizon": observation_horizon,
        "simulation_horizon": {
            "release_horizon": release_horizon,
            "dmax": dmax,
            "observation_horizon": observation_horizon,
            "observation_horizon_semantics": "release_horizon_plus_dmax",
        },
        "release_cutoff_enabled": True,
        "trace_schema_version": int(contract["trace_schema_version"]),
        "observability_contract_version": int(
            contract["observability_contract_version"]
        ),
        "core3_simulation_contract": dict(contract),
    }
    if not core3_v7:
        return {
            **common,
            "battery_capacity": capacity,
            "physical_initial_energy": physical,
        }
    scale = Fraction(str(contract["model_energy_unit_joules"]))
    for ambiguous in (
        "battery_capacity", "physical_initial_energy", "projection_e0",
    ):
        common.pop(ambiguous, None)
    projection_e0_model = tuple(
        str(value) for value in contract["projection_e0_model_units"]
    )
    return {
        **common,
        "model_energy_unit_joules": fraction_text(scale),
        "battery_capacity_model_units": capacity,
        "battery_capacity_j": fraction_text(Fraction(capacity) * scale),
        "physical_initial_energy_model_units": physical,
        "physical_initial_energy_j": fraction_text(
            Fraction(physical) * scale
        ),
        "projection_e0_model_units": list(projection_e0_model),
        "projection_e0_j": [
            fraction_text(Fraction(value) * scale)
            for value in projection_e0_model
        ],
    }


def expected_counts_v5(scientific_config: Mapping[str, Any]) -> dict[str, int]:
    rta4_formal_config_hash_v5(scientific_config)
    if (
        scientific_config["core"] == "CORE-3"
        and isinstance(
            scientific_config.get("core3_simulation_contract"), Mapping,
        )
    ):
        v3 = scientific_config["v3_plan_grid"]
        skeletons = int(v3["source"]["taskset_count"])
        mathematical = skeletons * len(v3["release_modes"]) * (
            1 + len(v3["finite_battery_capacities"])
        )
        return {
            "taskset_skeleton_count": skeletons,
            "mathematical_request_count": mathematical,
            "ordered_stream_count": mathematical,
        }
    return expected_counts_v3(scientific_config["v3_plan_grid"])


def _source_records_v5(
    scientific_config: Mapping[str, Any],
) -> Iterator[tuple[Any, str | None]]:
    """Preserve V3 evidence while expanding the opt-in V6 effective grid."""

    v3 = scientific_config["v3_plan_grid"]
    core3_v6 = (
        scientific_config["core"] == "CORE-3"
        and isinstance(
            scientific_config.get("core3_simulation_contract"), Mapping,
        )
    )
    for record in iter_formal_plan_v3(v3):
        if (
            core3_v6
            and record.material.get("track") == "FINITE_BATTERY_EMPIRICAL"
        ):
            for release_mode in v3["release_modes"]:
                yield record, str(release_mode)
        else:
            yield record, None


def iter_formal_plan_v5(
    scientific_config: Mapping[str, Any],
    task_sources: Sequence[TaskSourceBindingV5],
    service_curve: ExactServiceCurve,
) -> Iterator[FormalPlanRecordV5]:
    bindings = _validate_inputs(scientific_config, task_sources, service_curve)
    v3 = scientific_config["v3_plan_grid"]
    service_material_cache: dict[
        tuple[str, int, int, str], dict[str, Any]
    ] = {}
    for ordinal, (record, effective_release_mode) in enumerate(
        _source_records_v5(scientific_config)
    ):
        binding, source_index, taskset = _select_taskset(record, v3, bindings)
        effective = _effective_service(service_curve, record, scientific_config)
        effective_core3_material = _effective_core3_simulation_material(
            scientific_config, record, taskset,
            effective_release_mode=effective_release_mode,
        )
        service_material = _service_material_for_record(
            record,
            taskset,
            effective,
            scientific_config.get("simulation_tick_ms"),
            service_material_cache,
            effective_core3_material,
        )
        slot_material = {
            "profile": RTA4_FORMAL_PROFILE_V5,
            "core": record.core,
            "v3_taskset_slot_id": record.taskset_slot_id,
            "task_source_selector": {
                "axis": binding.axis, "axis_value": binding.axis_value,
            },
            "task_source_identity": binding.source.identity,
            "taskset_source_index": source_index,
            "taskset_identity": taskset.identity,
            "taskset_content_sha256": taskset.content_sha256,
            "task_order_sha256": taskset.task_order_sha256,
        }
        slot = domain_hash(RTA4_TASKSET_SLOT_DOMAIN_V5, slot_material)
        grid_material = dict(record.material)
        worker_count = grid_material.pop("worker_count", None)
        if record.kind != "simulation" and "exact_e0" not in grid_material:
            if "e0" in grid_material:
                base_e0 = Fraction(grid_material["e0"])
                if (
                    record.core == "CORE-5A"
                    and grid_material.get("axis") == "integer_time_scale"
                    and scientific_config.get(
                        "integer_time_scale_e0_semantics"
                    ) == CORE5A_SCALED_E0_V1
                ):
                    base_e0 *= int(grid_material["axis_value"])
                grid_material["exact_e0"] = fraction_text(base_e0)
            elif record.core == "CORE-5B":
                grid_material["exact_e0"] = scientific_config[
                    "source_baseline_exact_e0"
                ]
            else:
                raise RTA4FormalPlanV5Error(
                    "RTA record has no explicit exact E0 binding"
                )
        math_material = {
            **slot_material,
            "taskset_slot_id": slot,
            "v3_mathematical_request_id": record.mathematical_request_id,
            "v3_grid_material": grid_material,
            **({
                "effective_core3_simulation_material": (
                    effective_core3_material
                ),
            } if effective_core3_material is not None else {}),
            "configured_service_identity": service_curve.identity,
            "effective_service_identity": effective.identity,
            "effective_service_curve": dict(effective.normalized_config),
            "service_material": service_material,
            **({
                "simulation_tick_ms": scientific_config["simulation_tick_ms"],
            } if record.core == "CORE-3" else {}),
            "integer_time_scale_service_semantics": scientific_config[
                "integer_time_scale_service_semantics"
            ],
            **({
                "integer_time_scale_e0_semantics": scientific_config[
                    "integer_time_scale_e0_semantics"
                ],
            } if "integer_time_scale_e0_semantics" in scientific_config else {}),
        }
        mathematical = domain_hash(RTA4_MATH_REQUEST_DOMAIN_V5, math_material)
        execution_material = {
            "profile": RTA4_FORMAL_PROFILE_V5,
            "mathematical_request_id": mathematical,
            "execution_backend": PHYSICAL_CORE_EXECUTION_BACKEND_V3,
        }
        if worker_count is not None:
            execution_material["worker_count"] = worker_count
        execution = domain_hash(RTA4_EXECUTION_DOMAIN_V5, execution_material)
        yield FormalPlanRecordV5(
            record.kind,
            record.core,
            ordinal,
            mathematical,
            execution,
            slot,
            taskset.identity,
            service_curve.identity,
            effective.identity,
            record.record_id,
            {
                **math_material,
                **({} if worker_count is None else {"worker_count": worker_count}),
            },
        )


def ordered_stream_digest_v5(
    records: Iterable[FormalPlanRecordV5],
) -> StreamDigestV5:
    digest = hashlib.sha256(RTA4_STREAM_DIGEST_DOMAIN_V5)
    count = 0
    for count, record in enumerate(records, start=1):
        encoded = canonical_json(record.canonical_material()).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return StreamDigestV5(count, digest.hexdigest())


def describe_formal_plan_v5(
    scientific_config: Mapping[str, Any],
    task_sources: Sequence[TaskSourceBindingV5],
    service_curve: ExactServiceCurve,
) -> dict[str, Any]:
    scientific_hash = rta4_formal_config_hash_v5(scientific_config)
    records = tuple(iter_formal_plan_v5(
        scientific_config, task_sources, service_curve,
    ))
    expected = expected_counts_v5(scientific_config)
    if len(records) != expected["ordered_stream_count"]:
        raise RTA4FormalPlanV5Error(
            "V5 ordered stream count differs from the frozen V3 grid"
        )
    unique_math = {record.mathematical_request_id for record in records}
    if len(unique_math) != expected["mathematical_request_count"]:
        raise RTA4FormalPlanV5Error(
            "V5 mathematical identity cardinality differs from V3"
        )
    stream = ordered_stream_digest_v5(records)
    identity_material = {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V5,
        "core": scientific_config["core"],
        "normalized_scientific_config_sha256": scientific_hash,
        "formal_schema_sha256": formal_schema_hash_v5(),
        "dynamic_counts": expected,
        "ordered_stream_digest": stream.sha256,
        "taskset_store_identity": formal_taskset_store_identity_v5(
            scientific_config
        ),
        "source_closure_identity": source_closure_identity_v5(
            scientific_config
        ),
        "v3_plan_grid_identity": scientific_config["v3_plan_grid_identity"],
        "service_curve_identity": scientific_config["service_curve_identity"],
    }
    return {
        **expected,
        "profile": RTA4_FORMAL_PROFILE_V5,
        "core": scientific_config["core"],
        "normalized_scientific_config_sha256": scientific_hash,
        "ordered_stream_digest": stream.sha256,
        "taskset_store_identity": identity_material["taskset_store_identity"],
        "source_closure_identity": identity_material["source_closure_identity"],
        "service_curve_identity": scientific_config["service_curve_identity"],
        "task_source_identities": [
            binding.source.identity for binding in task_sources
        ],
        "plan_sha256": domain_hash(RTA4_PLAN_DOMAIN_V5, identity_material),
        "formal_campaign_authorization_status": scientific_config[
            "formal_campaign_authorization_status"
        ],
        "identity_material": identity_material,
    }


def validate_source_dependency_v5(
    dependent_scientific: Mapping[str, Any],
    source_scientific: Mapping[str, Any],
    source_task_sources: Sequence[TaskSourceBindingV5],
    source_service: ExactServiceCurve,
    dependent_task_sources: Sequence[TaskSourceBindingV5] | None = None,
) -> None:
    """Validate CORE-2/3/5B dependency without weakening V3 source hashes."""

    dependent_core = dependent_scientific.get("core")
    dependent_v3 = dependent_scientific.get("v3_plan_grid")
    source_ref = (
        dependent_v3.get("source", {})
        if isinstance(dependent_v3, Mapping) else {}
    )
    expected_source_core = (
        "CORE-1" if dependent_core in {"CORE-2", "CORE-3"}
        else source_ref.get("core") if dependent_core == "CORE-5B" else None
    )
    if expected_source_core is None or source_scientific.get("core") != expected_source_core:
        raise RTA4FormalPlanV5Error("invalid V5 source/dependent core pair")
    dependent_binding = dependent_scientific["task_source_bindings"]
    source_binding = source_scientific["task_source_bindings"]
    core1_taskset_subset = (
        dependent_core == "CORE-5B" and expected_source_core == "CORE-1"
    )
    if not core1_taskset_subset and dependent_binding != source_binding:
        raise RTA4FormalPlanV5Error("dependent task source differs from source core")
    if (
        dependent_scientific["service_curve_identity"]
        != source_scientific["service_curve_identity"]
    ):
        raise RTA4FormalPlanV5Error("dependent service differs from source core")
    source_v3 = source_scientific["v3_plan_grid"]
    source_plan_v3 = describe_formal_plan_v3(source_v3)
    if (
        source_ref["source_campaign_config_sha256"]
        != rta4_formal_config_hash_v3(source_v3)
        or source_ref["source_plan_sha256"] != source_plan_v3["plan_sha256"]
        or source_ref["source_taskset_store_identity"]
        != formal_taskset_store_identity_v3(source_v3)
    ):
        raise RTA4FormalPlanV5Error("frozen V3 source dependency hashes drifted")
    validated_source = _validate_inputs(
        source_scientific, source_task_sources, source_service,
    )
    if not core1_taskset_subset:
        return
    if dependent_task_sources is None:
        raise RTA4FormalPlanV5Error(
            "CORE-1 CORE-5B dependency requires the exact candidate task source"
        )
    validated_dependent = _validate_inputs(
        dependent_scientific, dependent_task_sources, source_service,
    )
    if len(validated_source) != 1 or len(validated_dependent) != 1:
        raise RTA4FormalPlanV5Error(
            "CORE-1 CORE-5B dependency requires one campaign task source"
        )
    source_tasksets = validated_source[0].source
    dependent_tasksets = validated_dependent[0].source
    source_utilizations = list(source_v3["normalized_utilization"])
    per_utilization = int(source_v3["tasksets_per_utilization"])
    candidates = int(dependent_v3["candidates_per_method_stratum"])
    if (
        source_ref["taskset_count"]
        != len(source_utilizations) * per_utilization
        or candidates > per_utilization
    ):
        raise RTA4FormalPlanV5Error(
            "CORE-1 CORE-5B source/candidate taskset shape drifted"
        )
    expected_source_indices = []
    for stratum in dependent_v3["utilization_strata"]:
        try:
            source_stratum = source_utilizations.index(stratum)
        except ValueError as exc:
            raise RTA4FormalPlanV5Error(
                "CORE-5B utilization stratum is absent from CORE-1"
            ) from exc
        start = source_stratum * per_utilization
        expected_source_indices.extend(range(start, start + candidates))
    if dependent_tasksets.taskset_count != len(expected_source_indices):
        raise RTA4FormalPlanV5Error(
            "CORE-5B candidate task source count differs from CORE-1 subset"
        )
    for dependent_index, source_index in enumerate(expected_source_indices):
        dependent_taskset = dependent_tasksets.taskset(dependent_index)
        source_taskset = source_tasksets.taskset(source_index)
        if (
            dependent_taskset.content_sha256 != source_taskset.content_sha256
            or dependent_taskset.task_order != source_taskset.task_order
            or dependent_taskset.task_order_sha256
            != source_taskset.task_order_sha256
            or dependent_taskset.material(include_identity=False)
            != source_taskset.material(include_identity=False)
        ):
            raise RTA4FormalPlanV5Error(
                "CORE-5B candidate task source is not the exact ordered "
                "CORE-1 utilization subset"
            )


__all__ = [
    "FormalPlanRecordV5",
    "RTA4FormalPlanV5Error",
    "StreamDigestV5",
    "describe_formal_plan_v5",
    "expected_counts_v5",
    "iter_formal_plan_v5",
    "ordered_stream_digest_v5",
    "validate_source_dependency_v5",
]
