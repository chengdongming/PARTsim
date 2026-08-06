"""Strict V5 campaign wrapper over the frozen V3 grid and V4 task sources.

This module adds source and service selection without modifying either legacy
implementation.  V3 remains the sole owner of the six experiment grids; V4
remains the sole owner of exact task parsing, generation and content identity.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from .rta4_core3_contracts_v6 import (
    CORE3_SIMULATION_CONTRACT_DOMAIN_V6,
    RTA4Core3ContractV6Error,
    normalize_core3_artifact_storage_v1,
    normalize_core3_energy_conservation_rule_v1,
)
from .rta4_energy_service_v5 import (
    ExactServiceCurve,
    RTA4EnergyServiceV5Error,
    normalize_energy_service_v5,
)
from .rta4_formal_config import domain_hash, fraction_text
from .rta4_formal_config_v3 import (
    RTA4_CORES_V3,
    RTA4FormalConfigV3Error,
    normalize_rta4_campaign_v3,
    rta4_formal_config_hash_v3,
)
from .rta4_task_source_v4 import (
    PRIORITY_POLICY_RM,
    RTA4TaskSourceV4Error,
    TaskSourceV4,
    _UniqueKeyLoader,
    load_task_source_v4,
)


RTA4_FORMAL_PROFILE_V5 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_V5_SELECTABLE_EXACT_SERVICE"
)
RTA4_FORMAL_SCHEMA_VERSION_V5 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_SCHEMA_V5_SELECTABLE_EXACT_SERVICE"
)
RTA4_FORMAL_PLAN_VERSION_V5 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_PLAN_V5_UNIFIED_SIX_CORE"
)
RTA4_FORMAL_CONFIG_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v5"
RTA4_FORMAL_TASKSET_STORE_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE:v5"
RTA4_SOURCE_CLOSURE_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:SOURCE_CLOSURE:v5"
RTA4_CAMPAIGN_AUTHORIZATION_STATUS_V5 = (
    "UNAUTHORIZED_LOCAL_NOT_FOR_PAPER_ONLY_REQUIRES_SEPARATE_FREEZE"
)
CORE5A_FIXED_TICK_SERVICE_V1 = "FIXED_TICK_SERVICE_PARAMETERS_V1"
CORE5A_SCALED_LATENCY_SERVICE_V1 = "SCALE_SERVICE_LATENCY_WITH_TIME_V1"
CORE5A_TIME_SERVICE_SEMANTICS_V5 = (
    CORE5A_FIXED_TICK_SERVICE_V1,
    CORE5A_SCALED_LATENCY_SERVICE_V1,
)

_V5_COMMON_EXTRA_FIELDS = {"service_curve"}
_V5_SINGLE_SOURCE_FIELD = {"task_source"}
_V5_CORE3_FIELDS = {
    "task_source", "simulation_tick_ms", "physical_initial_energy",
    "theorem_battery_capacity", "core3_campaign_type",
    "energy_conservation_rule", "artifact_storage",
}
_V5_CORE5B_FIELDS = {"task_source", "source_baseline_exact_e0"}
_V5_CORE5A_FIELDS = {
    "task_sources", "integer_time_scale_service_semantics",
}
_RATIONAL_LIKE = re.compile(r"[+]?[0-9]+(?:/[0-9]+)?|[+]?[0-9]*\.[0-9]+")
_RATIONAL_FIELD_NAMES = {
    "normalized_utilization", "e0", "finite_battery_capacities",
    "projection_e0", "service_scale", "power_scale",
    "deadline_slack_fraction", "utilization_strata", "rate", "latency",
    "power", "background_utilization",
    "source_baseline_exact_e0",
    "physical_initial_energy", "theorem_battery_capacity",
    "absolute_tolerance_j", "relative_tolerance",
    "fixed_total_utilization", "fixed_total_utilization_tolerance",
}

CORE3_SIMULATION_CONTRACT_V6 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_CONTRACT_V6"
)
CORE3_RESULT_SCHEMA_V6 = "ASAP_BLOCK_V9_3_RTA4_CORE3_SIMULATION_RESULT_V6"
CORE3_RESULT_DOMAIN_V6 = "ASAP_BLOCK:V9.3:RTA4:CORE3_SIMULATION_RESULT:v6"
CORE3_PROJECTION_E0_V6 = ("34", "35", "36", "37", "38", "39", "40")
CORE3_CAMPAIGN_TYPES_V6 = ("FORMAL", "CALIBRATION")
CORE3_ENERGY_TOLERANCE_EXACT_J_V6 = "1/100000000"


class RTA4FormalConfigV5Error(ValueError):
    """Raised before a V5 scientific identity can be issued."""


@dataclass(frozen=True)
class TaskSourceBindingV5:
    axis: str
    axis_value: str
    source: TaskSourceV4

    def material(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "axis_value": self.axis_value,
            "task_source": deepcopy(dict(self.source.normalized_config)),
            "task_source_identity": self.source.identity,
            "content_certificate": deepcopy(dict(self.source.content_certificate)),
        }


@dataclass(frozen=True)
class LoadedCampaignV5:
    campaign_path: Path
    raw_campaign_file_sha256: str
    normalized_scientific_config: Mapping[str, Any]
    normalized_scientific_config_sha256: str
    runtime: Mapping[str, Any]
    v3_scientific_config: Mapping[str, Any]
    task_sources: tuple[TaskSourceBindingV5, ...]
    service_curve: ExactServiceCurve


def _reject_scientific_floats(value: Any, label: str = "campaign") -> None:
    if type(value) is float:
        raise RTA4FormalConfigV5Error(f"{label} contains a scientific float")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_scientific_floats(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_scientific_floats(item, f"{label}[{index}]")


def _reject_noncanonical_rational_spellings(
    value: Any, label: str = "campaign", *, rational_context: bool = False,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_noncanonical_rational_spellings(
                item,
                f"{label}.{key}",
                rational_context=str(key) in _RATIONAL_FIELD_NAMES,
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_noncanonical_rational_spellings(
                item,
                f"{label}[{index}]",
                rational_context=rational_context,
            )
        return
    if (
        rational_context and type(value) is str
        and _RATIONAL_LIKE.fullmatch(value)
    ):
        try:
            canonical = fraction_text(Fraction(value))
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4FormalConfigV5Error(f"{label} is not rational") from exc
        if value != canonical:
            raise RTA4FormalConfigV5Error(
                f"{label} must be a canonical rational string: {canonical}"
            )


def _load_source(raw: Any, base_directory: Path | str | None) -> TaskSourceV4:
    try:
        return load_task_source_v4(raw, base_directory=base_directory)
    except RTA4TaskSourceV4Error as exc:
        raise RTA4FormalConfigV5Error(str(exc)) from exc


def _expected_single_source_shape(
    core: str, v3: Mapping[str, Any], source: TaskSourceV4,
) -> None:
    if core == "CORE-1":
        count = len(v3["normalized_utilization"]) * v3["tasksets_per_utilization"]
        processors, task_count = v3["processors"], v3["task_count"]
    elif core in {"CORE-2", "CORE-3"}:
        count = v3["source"]["taskset_count"]
        processors, task_count = source.processors, source.task_count
    elif core == "CORE-4":
        count = len(v3["normalized_utilization"]) * v3["skeletons_per_utilization"]
        processors, task_count = v3["processors"], v3["task_count"]
    elif core == "CORE-5B":
        count = (
            len(v3["utilization_strata"])
            * v3["candidates_per_method_stratum"]
        )
        processors, task_count = source.processors, source.task_count
    else:
        raise RTA4FormalConfigV5Error("single source used for unsupported core")
    if source.taskset_count != count:
        raise RTA4FormalConfigV5Error(
            f"{core} requires exactly {count} exact tasksets; got "
            f"{source.taskset_count}"
        )
    if source.processors != processors or source.task_count != task_count:
        raise RTA4FormalConfigV5Error(
            f"{core} V3 grid differs from exact task-source shape"
        )


def _positive_selector(value: Any, label: str) -> str:
    if type(value) is not int or value <= 0:
        raise RTA4FormalConfigV5Error(f"{label} must be a positive integer")
    return str(value)


def _core5a_sources(
    raw: Any, v3: Mapping[str, Any], base_directory: Path | str | None,
) -> tuple[TaskSourceBindingV5, ...]:
    if type(raw) is not list or not raw:
        raise RTA4FormalConfigV5Error("task_sources must be a non-empty list")
    bindings: list[TaskSourceBindingV5] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or set(item) != {
            "axis", "axis_value", "task_source",
        }:
            raise RTA4FormalConfigV5Error(
                f"task_sources[{index}] field set mismatch"
            )
        axis = item["axis"]
        if axis not in {"task_count", "processor_count", "integer_time_scale"}:
            raise RTA4FormalConfigV5Error(
                f"task_sources[{index}].axis is unsupported"
            )
        value = _positive_selector(
            item["axis_value"], f"task_sources[{index}].axis_value"
        )
        key = (str(axis), value)
        if key in seen:
            raise RTA4FormalConfigV5Error("duplicate CORE-5A task-source selector")
        seen.add(key)
        source = _load_source(item["task_source"], base_directory)
        bindings.append(TaskSourceBindingV5(str(axis), value, source))

    task_axis = v3["task_count_axis"]
    processor_axis = v3["processor_axis"]
    time_axis = v3["integer_time_scale_axis"]
    expected = {
        *(('task_count', str(value)) for value in task_axis["values"]),
        *(('processor_count', str(value)) for value in processor_axis["values"]),
        *(('integer_time_scale', str(value)) for value in time_axis["values"]),
    }
    if seen != expected:
        raise RTA4FormalConfigV5Error(
            f"CORE-5A task-source selectors mismatch; missing={sorted(expected-seen)}, "
            f"unknown={sorted(seen-expected)}"
        )

    for binding in bindings:
        value = int(binding.axis_value)
        source = binding.source
        if binding.axis == "task_count":
            expected_shape = (
                task_axis["processors"], value, task_axis["tasksets"],
            )
        elif binding.axis == "processor_count":
            expected_shape = (
                value, processor_axis["task_count"], processor_axis["tasksets"],
            )
        else:
            expected_shape = (
                task_axis["processors"], processor_axis["task_count"],
                time_axis["base_tasksets"],
            )
        observed_shape = (
            source.processors, source.task_count, source.taskset_count,
        )
        if observed_shape != expected_shape:
            raise RTA4FormalConfigV5Error(
                f"CORE-5A {binding.axis}={value} task-source shape mismatch"
            )
    if "fixed_total_utilization" in processor_axis:
        _validate_fixed_total_processor_sources(bindings, processor_axis)
    _validate_integer_time_sources(bindings)
    return tuple(bindings)


def _validate_fixed_total_processor_sources(
    bindings: list[TaskSourceBindingV5], processor_axis: Mapping[str, Any],
) -> None:
    processor_bindings = [
        binding for binding in bindings if binding.axis == "processor_count"
    ]
    if not processor_bindings:
        raise RTA4FormalConfigV5Error(
            "fixed-total processor axis has no task sources"
        )
    reference_source = processor_bindings[0].source
    reference_contract = (
        reference_source.mode,
        reference_source.priority_policy,
        reference_source.task_count,
        reference_source.taskset_count,
    )
    reference_config = deepcopy(dict(reference_source.normalized_config))
    reference_config.pop("processors", None)
    reference_config.pop("manifest_file_sha256", None)
    reference_config.pop("manifest_semantic_sha256", None)
    if isinstance(reference_config.get("parameters"), Mapping):
        reference_config["parameters"] = deepcopy(
            dict(reference_config["parameters"])
        )
        reference_config["parameters"].pop("processors", None)
    expected_total = Fraction(processor_axis["fixed_total_utilization"])
    tolerance_text = processor_axis.get("fixed_total_utilization_tolerance")
    tolerance = (
        None if tolerance_text is None else Fraction(tolerance_text)
    )
    for binding in processor_bindings:
        source = binding.source
        if (
            source.mode,
            source.priority_policy,
            source.task_count,
            source.taskset_count,
        ) != reference_contract:
            raise RTA4FormalConfigV5Error(
                "fixed-total processor sources differ beyond processors"
            )
        source_config = deepcopy(dict(source.normalized_config))
        source_config.pop("processors", None)
        source_config.pop("manifest_file_sha256", None)
        source_config.pop("manifest_semantic_sha256", None)
        if isinstance(source_config.get("parameters"), Mapping):
            source_config["parameters"] = deepcopy(
                dict(source_config["parameters"])
            )
            source_config["parameters"].pop("processors", None)
        if source_config != reference_config:
            raise RTA4FormalConfigV5Error(
                "fixed-total processor sources differ beyond processors"
            )
        for replicate, taskset in enumerate(source.tasksets):
            reference_taskset = reference_source.taskset(replicate)
            if (
                taskset.task_order != reference_taskset.task_order
                or taskset.task_order_sha256
                != reference_taskset.task_order_sha256
                or taskset.source_seed != reference_taskset.source_seed
                or taskset.material(include_identity=False)
                != reference_taskset.material(include_identity=False)
            ):
                raise RTA4FormalConfigV5Error(
                    "fixed-total processor sources do not pair exact tasksets"
                )
            observed_total = sum(
                (Fraction(task.C, task.T) for task in taskset.tasks),
                Fraction(0),
            )
            if tolerance is None and observed_total != expected_total:
                raise RTA4FormalConfigV5Error(
                    "fixed-total processor taskset utilization mismatch"
                )
            if (
                tolerance is not None
                and abs(observed_total - expected_total) > tolerance
            ):
                raise RTA4FormalConfigV5Error(
                    "fixed-total processor taskset utilization exceeds "
                    "allowed tolerance"
                )


def _validate_integer_time_sources(
    bindings: list[TaskSourceBindingV5],
) -> None:
    time_bindings = [
        binding for binding in bindings if binding.axis == "integer_time_scale"
    ]
    reference: tuple[tuple[dict[str, Any], ...], ...] | None = None
    for binding in time_bindings:
        scale = int(binding.axis_value)
        normalized_tasksets = []
        for taskset in binding.source.tasksets:
            normalized_tasks = []
            for task in taskset.tasks:
                if task.C % scale or task.D % scale or task.T % scale:
                    raise RTA4FormalConfigV5Error(
                        "integer-time source has a non-divisible C/D/T value"
                    )
                normalized_tasks.append({
                    "name": task.name,
                    "C": task.C // scale,
                    "D": task.D // scale,
                    "T": task.T // scale,
                    "power": task.power,
                })
            normalized_tasksets.append(tuple(normalized_tasks))
        normalized = tuple(normalized_tasksets)
        if reference is None:
            reference = normalized
        elif normalized != reference:
            raise RTA4FormalConfigV5Error(
                "integer-time task sources are not exact C/D/T scalings with "
                "unchanged power"
            )


def _binding_material(
    bindings: tuple[TaskSourceBindingV5, ...],
) -> list[dict[str, Any]]:
    return [binding.material() for binding in bindings]


def _canonical_energy_v6(
    value: Any, label: str, *, strictly_positive: bool,
) -> str:
    if type(value) is not str:
        raise RTA4FormalConfigV5Error(
            f"{label} must be a canonical rational string"
        )
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4FormalConfigV5Error(f"{label} is not rational") from exc
    if exact < 0 or (strictly_positive and exact <= 0):
        qualifier = "positive" if strictly_positive else "nonnegative"
        raise RTA4FormalConfigV5Error(f"{label} must be {qualifier}")
    canonical = fraction_text(exact)
    if value != canonical:
        raise RTA4FormalConfigV5Error(
            f"{label} must be a canonical rational string: {canonical}"
        )
    return canonical


def _core3_contract_v6(
    raw: Mapping[str, Any],
    v3: Mapping[str, Any],
    bindings: tuple[TaskSourceBindingV5, ...],
) -> dict[str, Any] | None:
    selected = {
        field for field in (
            "physical_initial_energy", "theorem_battery_capacity",
            "core3_campaign_type", "energy_conservation_rule",
        )
        if field in raw
    }
    if not selected:
        return None
    required = {
        "physical_initial_energy", "theorem_battery_capacity",
        "core3_campaign_type", "energy_conservation_rule",
    }
    if selected != required:
        raise RTA4FormalConfigV5Error(
            "CORE-3 V6 fields must be supplied as one explicit contract"
        )
    physical = _canonical_energy_v6(
        raw["physical_initial_energy"],
        "physical_initial_energy",
        strictly_positive=False,
    )
    theorem_capacity = _canonical_energy_v6(
        raw["theorem_battery_capacity"],
        "theorem_battery_capacity",
        strictly_positive=True,
    )
    campaign_type = raw["core3_campaign_type"]
    if campaign_type not in CORE3_CAMPAIGN_TYPES_V6:
        raise RTA4FormalConfigV5Error(
            "CORE-3 V6 campaign type must be FORMAL or CALIBRATION"
        )
    if tuple(v3["projection_e0"]) != CORE3_PROJECTION_E0_V6:
        raise RTA4FormalConfigV5Error(
            "CORE-3 V6 projection_e0 must be exactly 34 through 40"
        )
    if len(bindings) != 1 or bindings[0].source.task_count != 10:
        raise RTA4FormalConfigV5Error(
            "CORE-3 V6 schema-3 preflight requires exactly 10 tasks"
        )
    capacities = (
        theorem_capacity, *tuple(v3["finite_battery_capacities"]),
    )
    if any(Fraction(physical) > Fraction(capacity) for capacity in capacities):
        raise RTA4FormalConfigV5Error(
            "physical_initial_energy exceeds a configured battery capacity"
        )
    try:
        conservation_rule = normalize_core3_energy_conservation_rule_v1(
            raw["energy_conservation_rule"]
        )
    except RTA4Core3ContractV6Error as exc:
        raise RTA4FormalConfigV5Error(str(exc)) from exc
    material = {
        "contract_version": CORE3_SIMULATION_CONTRACT_V6,
        "result_schema_version": CORE3_RESULT_SCHEMA_V6,
        "result_identity_domain": CORE3_RESULT_DOMAIN_V6,
        "physical_initial_energy": physical,
        "theorem_battery_capacity": theorem_capacity,
        "projection_e0": list(CORE3_PROJECTION_E0_V6),
        "campaign_type": campaign_type,
        "trace_schema_version": 3,
        "observability_contract_version": 2,
        "release_energy_sampling_stage": (
            "post_harvest_pre_consumption"
        ),
        "energy_tolerance_j": CORE3_ENERGY_TOLERANCE_EXACT_J_V6,
        "energy_conservation_rule": conservation_rule,
    }
    return {
        **material,
        "contract_identity": domain_hash(
            CORE3_SIMULATION_CONTRACT_DOMAIN_V6, material,
        ),
    }


def normalize_rta4_campaign_v5(
    raw: Any, *, base_directory: Path | str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RTA4FormalConfigV5Error("campaign must be a mapping")
    _reject_scientific_floats(raw)
    _reject_noncanonical_rational_spellings(raw)
    core = raw.get("core")
    if core not in RTA4_CORES_V3:
        raise RTA4FormalConfigV5Error("campaign core is unsupported")
    core_extra = (
        _V5_CORE5A_FIELDS
        if core == "CORE-5A"
        else _V5_CORE3_FIELDS
        if core == "CORE-3"
        else _V5_CORE5B_FIELDS
        if core == "CORE-5B"
        else _V5_SINGLE_SOURCE_FIELD
    )
    extra = _V5_COMMON_EXTRA_FIELDS.union(core_extra)
    simulation_tick_ms: int | None = None
    if core == "CORE-3":
        simulation_tick_ms = raw.get("simulation_tick_ms")
        if type(simulation_tick_ms) is not int or simulation_tick_ms <= 0:
            raise RTA4FormalConfigV5Error(
                "CORE-3 simulation_tick_ms must be an explicit positive "
                "plain integer"
            )
    source_baseline_exact_e0: str | None = None
    if core == "CORE-5B":
        source_baseline_exact_e0 = raw.get("source_baseline_exact_e0")
        try:
            exact_source_e0 = Fraction(source_baseline_exact_e0)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise RTA4FormalConfigV5Error(
                "CORE-5B source_baseline_exact_e0 must be an explicit "
                "canonical nonnegative rational string"
            ) from exc
        if (
            type(source_baseline_exact_e0) is not str
            or exact_source_e0 < 0
            or fraction_text(exact_source_e0) != source_baseline_exact_e0
        ):
            raise RTA4FormalConfigV5Error(
                "CORE-5B source_baseline_exact_e0 must be an explicit "
                "canonical nonnegative rational string"
            )
    v3_raw = {key: deepcopy(value) for key, value in raw.items() if key not in extra}
    v5_runtime: dict[str, Any] = {}
    if isinstance(v3_raw.get("runtime"), Mapping):
        v5_runtime = deepcopy(dict(v3_raw["runtime"]))
        simulator_path = v5_runtime.pop("simulator_path", None)
        v3_raw["runtime"] = deepcopy(v5_runtime)
        if simulator_path is not None:
            if type(simulator_path) is not str or not simulator_path.strip():
                raise RTA4FormalConfigV5Error(
                    "runtime.simulator_path must be a non-empty path"
                )
            v5_runtime["simulator_path"] = simulator_path
    try:
        v3_normalized = normalize_rta4_campaign_v3(v3_raw)
        service = normalize_energy_service_v5(raw.get("service_curve"))
    except (RTA4FormalConfigV3Error, RTA4EnergyServiceV5Error) as exc:
        raise RTA4FormalConfigV5Error(str(exc)) from exc
    v3 = v3_normalized["normalized_scientific_config"]
    if core == "CORE-5A":
        semantics = raw.get("integer_time_scale_service_semantics")
        if semantics not in CORE5A_TIME_SERVICE_SEMANTICS_V5:
            raise RTA4FormalConfigV5Error(
                "CORE-5A requires explicit integer-time service semantics"
            )
        bindings = _core5a_sources(raw.get("task_sources"), v3, base_directory)
    else:
        semantics = "NOT_APPLICABLE"
        source = _load_source(raw.get("task_source"), base_directory)
        _expected_single_source_shape(str(core), v3, source)
        bindings = (TaskSourceBindingV5("campaign", "all", source),)
    if any(binding.source.priority_policy != PRIORITY_POLICY_RM for binding in bindings):
        raise RTA4FormalConfigV5Error("all exact task sources must use strict RM order")
    core3_contract = (
        _core3_contract_v6(raw, v3, bindings)
        if core == "CORE-3" else None
    )
    artifact_storage = None
    if core3_contract is not None:
        try:
            artifact_storage = normalize_core3_artifact_storage_v1(
                raw.get("artifact_storage")
            )
        except RTA4Core3ContractV6Error as exc:
            raise RTA4FormalConfigV5Error(str(exc)) from exc
    elif core == "CORE-3" and "artifact_storage" in raw:
        raise RTA4FormalConfigV5Error(
            "legacy CORE-3 cannot select the V6 artifact storage contract"
        )
    grid_identity = rta4_formal_config_hash_v3(v3)
    scientific = {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V5,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V5,
        "campaign_id": v3["campaign_id"],
        "core": core,
        "v3_plan_grid": deepcopy(v3),
        "v3_plan_grid_identity": grid_identity,
        "task_source_bindings": _binding_material(bindings),
        "service_curve": deepcopy(dict(service.normalized_config)),
        "service_curve_identity": service.identity,
        **({
            "simulation_tick_ms": simulation_tick_ms,
        } if core == "CORE-3" else {}),
        **({
            "core3_simulation_contract": core3_contract,
        } if core3_contract is not None else {}),
        **({
            "source_baseline_exact_e0": source_baseline_exact_e0,
        } if core == "CORE-5B" else {}),
        "integer_time_scale_service_semantics": semantics,
        "numeric_contract": {
            **deepcopy(dict(v3["numeric_contract"])),
            "service_curve_contract": "PARTSIM_EXACT_SERVICE_CURVE_V1",
            "task_source_contract": "RTA4_EXACT_TASK_SOURCE_V4_REUSED",
            "scientific_float_inputs_allowed": False,
        },
        "fixed_semantics": {
            "v3_grid_reimplemented": False,
            "v4_task_source_reimplemented": False,
            "rta_math_kernel_changed": False,
            "task_power_auto_scaling_allowed": False,
            "e0_auto_scaling_allowed": False,
            "battery_capacity_auto_scaling_allowed": False,
            "methods_share_task_and_service_material": True,
            **({
                "legacy_v5_core3_behavior_preserved": True,
            } if core3_contract is not None else {}),
        },
        "formal_campaign_authorization_status": (
            RTA4_CAMPAIGN_AUTHORIZATION_STATUS_V5
        ),
    }
    return {
        "normalized_scientific_config": scientific,
        "runtime": {
            **deepcopy(v3_normalized["runtime"]),
            **({
                "simulator_path": v5_runtime["simulator_path"]
            } if "simulator_path" in v5_runtime else {}),
            **({
                "artifact_storage": artifact_storage,
            } if artifact_storage is not None else {}),
        },
        "v3_scientific_config": deepcopy(v3),
        "task_sources": bindings,
        "service_curve": service,
    }


def rta4_formal_config_hash_v5(scientific_config: Mapping[str, Any]) -> str:
    if (
        not isinstance(scientific_config, Mapping)
        or scientific_config.get("profile") != RTA4_FORMAL_PROFILE_V5
    ):
        raise RTA4FormalConfigV5Error("not a normalized V5 scientific config")
    return domain_hash(RTA4_FORMAL_CONFIG_DOMAIN_V5, scientific_config)


def formal_taskset_store_identity_v5(
    scientific_config: Mapping[str, Any],
) -> str:
    return domain_hash(RTA4_FORMAL_TASKSET_STORE_DOMAIN_V5, {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "scientific_config_identity": rta4_formal_config_hash_v5(
            scientific_config
        ),
        "task_source_bindings": scientific_config["task_source_bindings"],
        "service_curve_identity": scientific_config["service_curve_identity"],
        **({
            "simulation_tick_ms": scientific_config["simulation_tick_ms"],
        } if scientific_config["core"] == "CORE-3" else {}),
        "legacy_store_accepted": False,
    })


def source_closure_identity_v5(
    scientific_config: Mapping[str, Any],
) -> str:
    rta4_formal_config_hash_v5(scientific_config)
    return domain_hash(RTA4_SOURCE_CLOSURE_DOMAIN_V5, {
        "v3_plan_grid_identity": scientific_config["v3_plan_grid_identity"],
        "task_source_bindings": scientific_config["task_source_bindings"],
        "service_curve": scientific_config["service_curve"],
        "service_curve_identity": scientific_config["service_curve_identity"],
        **({
            "simulation_tick_ms": scientific_config["simulation_tick_ms"],
        } if scientific_config["core"] == "CORE-3" else {}),
        **({
            "core3_simulation_contract": scientific_config[
                "core3_simulation_contract"
            ],
        } if "core3_simulation_contract" in scientific_config else {}),
    })


def load_rta4_campaign_v5(path: Path | str) -> LoadedCampaignV5:
    campaign_path = Path(path).expanduser().resolve(strict=True)
    payload = campaign_path.read_bytes()
    try:
        raw = yaml.load(payload, Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4FormalConfigV5Error(
            f"cannot parse V5 campaign: {campaign_path}"
        ) from exc
    normalized = normalize_rta4_campaign_v5(
        raw, base_directory=campaign_path.parent,
    )
    scientific = normalized["normalized_scientific_config"]
    return LoadedCampaignV5(
        campaign_path,
        hashlib.sha256(payload).hexdigest(),
        deepcopy(scientific),
        rta4_formal_config_hash_v5(scientific),
        deepcopy(normalized["runtime"]),
        deepcopy(normalized["v3_scientific_config"]),
        normalized["task_sources"],
        normalized["service_curve"],
    )


__all__ = [
    "CORE3_CAMPAIGN_TYPES_V6",
    "CORE3_ENERGY_TOLERANCE_EXACT_J_V6",
    "CORE3_PROJECTION_E0_V6",
    "CORE3_RESULT_DOMAIN_V6",
    "CORE3_RESULT_SCHEMA_V6",
    "CORE3_SIMULATION_CONTRACT_V6",
    "CORE5A_FIXED_TICK_SERVICE_V1",
    "CORE5A_SCALED_LATENCY_SERVICE_V1",
    "CORE5A_TIME_SERVICE_SEMANTICS_V5",
    "LoadedCampaignV5",
    "RTA4_CAMPAIGN_AUTHORIZATION_STATUS_V5",
    "RTA4_FORMAL_CONFIG_DOMAIN_V5",
    "RTA4_FORMAL_PLAN_VERSION_V5",
    "RTA4_FORMAL_PROFILE_V5",
    "RTA4_FORMAL_SCHEMA_VERSION_V5",
    "RTA4FormalConfigV5Error",
    "TaskSourceBindingV5",
    "formal_taskset_store_identity_v5",
    "load_rta4_campaign_v5",
    "normalize_rta4_campaign_v5",
    "rta4_formal_config_hash_v5",
    "source_closure_identity_v5",
]
