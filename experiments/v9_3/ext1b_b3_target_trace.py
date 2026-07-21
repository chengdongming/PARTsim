"""EXT-1B/B3-v2 target identity and actual-trace recovery contract."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping, Sequence

from .config import domain_hash, fraction_text
from .ext1b_capacity_contract import NATIVE_ENERGY_EPSILON_J


B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2 = (
    "B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2"
)
B3_V2_TASKSET_SCHEMA = "ASAP_BLOCK_V9_3_EXT1B_B3_TARGET_TRACE_TASKSET_V4"
B3_V2_TASKSET_DOMAIN = "ASAP_BLOCK:V9.3:EXT1B:TASKSET:v4"
B3_V2_SCENARIO_CANDIDATE_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B3:SCENARIO_CANDIDATE:v2"
)
B3_V2_PAIRED_INSTANCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:PAIRED_INSTANCE:v3"
)
B3_V2_REQUEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v3"
)
B3_V2_HARVEST_TRACE_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:HARVEST_TRACE:v2"
)
B3_V2_STORE_CONTRACT_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B3:TARGET_TRACE_STORE_CONTRACT:v2"
)
B3_V2_SIMULATION_CONFIG_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_CONFIG:v2"
)
B3_V2_FAIR_INPUT_DOMAIN = "ASAP_BLOCK:V9.3:EXT1B:FAIR_INPUT:v2"
ST_NATIVE_HARVEST_APPLICATION_THRESHOLD_J = Fraction(1, 10**6)


def is_b3_target_trace_v2(config: Mapping[str, Any]) -> bool:
    return config.get("scenario", {}).get("scenario_contract_id") == (
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
    )


def target_trace_contract_material() -> dict[str, Any]:
    """Canonical authorities persisted in v2 stores and run metadata."""

    return {
        "scenario_contract_id": B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
        "tick_order": (
            "tick_0_decides_with_initial_energy;tick_t_ge_1_harvests_"
            "trace[t-1]_then_caps_then_decides"
        ),
        "native_affordability_epsilon_j": fraction_text(
            NATIVE_ENERGY_EPSILON_J
        ),
        "st_native_harvest_application_predicate": (
            "trace_tick_energy_j > 1/1000000"
        ),
        "taskset_schema": B3_V2_TASKSET_SCHEMA,
        "taskset_domain": B3_V2_TASKSET_DOMAIN,
        "scenario_candidate_domain": B3_V2_SCENARIO_CANDIDATE_DOMAIN,
        "paired_instance_domain": B3_V2_PAIRED_INSTANCE_DOMAIN,
        "request_domain": B3_V2_REQUEST_DOMAIN,
        "harvest_trace_domain": B3_V2_HARVEST_TRACE_DOMAIN,
        "store_contract_domain": B3_V2_STORE_CONTRACT_DOMAIN,
        "simulation_config_domain": B3_V2_SIMULATION_CONFIG_DOMAIN,
        "fair_input_domain": B3_V2_FAIR_INPUT_DOMAIN,
    }


def v2_taskset_hash_from_document(document: Mapping[str, Any]) -> str:
    material = dict(document)
    observed_hash = material.pop("taskset_hash", None)
    material.pop("taskset_id", None)
    if (
        material.get("schema") != B3_V2_TASKSET_SCHEMA
        or material.get("scenario_contract_id")
        != B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
        or not isinstance(observed_hash, str)
    ):
        raise ValueError("invalid B3-v2 canonical taskset identity material")
    return domain_hash(B3_V2_TASKSET_DOMAIN, material)


def v2_scenario_candidate_identity(
    *, scenario_cell: Mapping[str, Any], source_taskset_hash: str,
    logical_taskset_index: int, attempt_index: int,
    capacity_feasibility_contract_identity: str, trace_hash: str,
    structure: Mapping[str, Any],
) -> str:
    return domain_hash(B3_V2_SCENARIO_CANDIDATE_DOMAIN, {
        "scenario_cell": dict(scenario_cell),
        "source_taskset_hash": source_taskset_hash,
        "logical_taskset_index": logical_taskset_index,
        "attempt_index": attempt_index,
        "capacity_feasibility_contract_identity": (
            capacity_feasibility_contract_identity
        ),
        "scenario_contract_id": B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
        "trace_hash": trace_hash,
        "structure": dict(structure),
    })


def v2_paired_instance_identity(
    *, scenario_cell: Mapping[str, Any], logical_taskset_index: int,
    taskset_hash: str, trace_hash: str, initial_battery: str,
    battery_capacity: str, processors: int, horizon: int,
    scenario_candidate_identity: str,
    capacity_feasibility_contract_identity: str,
) -> str:
    return domain_hash(B3_V2_PAIRED_INSTANCE_DOMAIN, {
        "scenario_cell": dict(scenario_cell),
        "logical_taskset_index": logical_taskset_index,
        "taskset_hash": taskset_hash,
        "trace_hash": trace_hash,
        "initial_battery": initial_battery,
        "battery_capacity": battery_capacity,
        "processors": processors,
        "horizon": horizon,
        "scenario_candidate_identity": scenario_candidate_identity,
        "capacity_feasibility_contract_identity": (
            capacity_feasibility_contract_identity
        ),
        "scenario_contract_id": B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
    })


def v2_simulation_config_identity(
    *, simulation: Mapping[str, Any], initial_battery: str,
    battery_capacity: str, allow_harvest_clipping: bool,
    system_template_hash: str,
) -> str:
    return domain_hash(B3_V2_SIMULATION_CONFIG_DOMAIN, {
        "simulation": dict(simulation),
        "initial_battery": initial_battery,
        "battery_capacity": battery_capacity,
        "allow_harvest_clipping": allow_harvest_clipping,
        "system_template_hash": system_template_hash,
    })


def v2_fair_input_identity(material: Mapping[str, Any]) -> str:
    return domain_hash(B3_V2_FAIR_INPUT_DOMAIN, dict(material))


def v2_request_identity(
    *, paired_instance_id: str, scheduler_id: str,
    capacity_feasibility_contract_identity: str,
    target_runtime_task_name: str, target_arrival_time: int,
    target_job_id: str,
) -> str:
    return domain_hash(B3_V2_REQUEST_DOMAIN, {
        "paired_instance_id": paired_instance_id,
        "scheduler_id": scheduler_id,
        "capacity_feasibility_contract_identity": (
            capacity_feasibility_contract_identity
        ),
        "scenario_contract_id": B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
        "target_runtime_task_name": target_runtime_task_name,
        "target_arrival_time": target_arrival_time,
        "target_job_id": target_job_id,
    })


@dataclass(frozen=True)
class ActualTraceRecovery:
    affordable_tick: int
    full_tick: int
    target_initial_slack: int
    configured_recovery_margin_ticks: int
    recovery_headroom: int
    predicate_satisfied: bool

    def material(self) -> dict[str, Any]:
        return {
            "actual_trace_affordable_tick": self.affordable_tick,
            "actual_trace_full_tick": self.full_tick,
            "target_initial_slack": self.target_initial_slack,
            "configured_recovery_margin_ticks": (
                self.configured_recovery_margin_ticks
            ),
            "actual_trace_recovery_headroom": self.recovery_headroom,
            "predicate": (
                "E_init + native_epsilon < e_target; 0 < "
                "actual_trace_affordable_tick <= actual_trace_full_tick; "
                "actual_trace_full_tick + configured_recovery_margin_ticks "
                "< target_initial_slack"
            ),
            "predicate_satisfied": self.predicate_satisfied,
        }


def actual_trace_recovery(
    trace_values: Sequence[Fraction],
    *,
    initial_energy: Fraction,
    battery_capacity: Fraction,
    target_unit_energy: Fraction,
    target_initial_slack: int,
    recovery_margin_ticks: int,
) -> ActualTraceRecovery:
    """Mirror native tick ordering for target affordability and ST release.

    Tick zero makes a decision with initial energy.  Trace element ``t - 1``
    is harvested and capped before the decision at tick ``t``.
    """

    initial = Fraction(initial_energy)
    capacity = Fraction(battery_capacity)
    target = Fraction(target_unit_energy)
    if initial < 0 or capacity <= 0 or target <= 0 or initial > capacity:
        raise ValueError("invalid target-trace energy bounds")
    if isinstance(target_initial_slack, bool) or target_initial_slack <= 0:
        raise ValueError("target initial slack must be positive")
    if isinstance(recovery_margin_ticks, bool) or recovery_margin_ticks < 0:
        raise ValueError("recovery margin must be a non-negative integer")

    energy = initial
    affordable_tick = (
        0 if energy + NATIVE_ENERGY_EPSILON_J >= target else None
    )
    full_tick = (
        0 if energy + NATIVE_ENERGY_EPSILON_J >= capacity else None
    )
    for tick, raw_harvest in enumerate(trace_values, start=1):
        harvest = Fraction(raw_harvest)
        if harvest < 0:
            raise ValueError("actual trace contains negative harvested energy")
        applied_harvest = (
            harvest
            if harvest > ST_NATIVE_HARVEST_APPLICATION_THRESHOLD_J
            else Fraction(0)
        )
        energy = min(capacity, energy + applied_harvest)
        if (
            affordable_tick is None
            and energy + NATIVE_ENERGY_EPSILON_J >= target
        ):
            affordable_tick = tick
        if full_tick is None and energy + NATIVE_ENERGY_EPSILON_J >= capacity:
            full_tick = tick
        if affordable_tick is not None and full_tick is not None:
            break

    if affordable_tick is None:
        raise ValueError("actual trace never makes the target affordable")
    if full_tick is None:
        raise ValueError("actual trace never reaches the ST full-battery release")
    recovery_headroom = target_initial_slack - full_tick
    predicate = (
        initial + NATIVE_ENERGY_EPSILON_J < target
        and 0 < affordable_tick <= full_tick
        and full_tick + recovery_margin_ticks < target_initial_slack
    )
    return ActualTraceRecovery(
        affordable_tick,
        full_tick,
        target_initial_slack,
        recovery_margin_ticks,
        recovery_headroom,
        predicate,
    )
