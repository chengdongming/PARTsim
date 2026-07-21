"""EXT-1B/B3-v2 target identity and actual-trace recovery contract."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any, Mapping, Sequence

from .config import domain_hash, fraction_text
from .ext1b_capacity_contract import NATIVE_ENERGY_EPSILON_J
from .task_identity import runtime_task_name_for_source_id


B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2 = (
    "B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2"
)
B3_V2_TASKSET_SCHEMA = "ASAP_BLOCK_V9_3_EXT1B_B3_TARGET_TRACE_TASKSET_V5"
B3_V2_TASKSET_DOMAIN = "ASAP_BLOCK:V9.3:EXT1B:TASKSET:v5"
B3_V2_SCENARIO_CANDIDATE_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B3:SCENARIO_CANDIDATE:v3"
)
B3_V2_PAIRED_INSTANCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:PAIRED_INSTANCE:v4"
)
B3_V2_REQUEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v4"
)
B3_V2_HARVEST_TRACE_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:HARVEST_TRACE:v2"
)
B3_V2_STORE_CONTRACT_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B3:TARGET_TRACE_STORE_CONTRACT:v3"
)
B3_V2_SIMULATION_CONFIG_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_CONFIG:v3"
)
B3_V2_FAIR_INPUT_DOMAIN = "ASAP_BLOCK:V9.3:EXT1B:FAIR_INPUT:v3"
B3_V2_RECOVERY_PREFIX_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B3:RECOVERY_PREFIX:v1"
)
RECOVERY_PREFIX_MATERIAL_FIELDS = (
    "target_recovery_contract_applicable", "recovery_prefix_order",
    "initial_ready_job_count", "recovery_prefix_length",
    "recovery_prefix_task_ids", "recovery_prefix_runtime_names",
    "recovery_prefix_priority_ranks", "recovery_prefix_unit_energies",
    "recovery_prefix_required_energy", "materialized_battery_capacity",
    "recovery_prefix_affordable_at_full", "target_blocked_at_initial_energy",
    "recovery_earliest_initial_deadline", "native_affordability_epsilon_j",
)
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
        "recovery_prefix_domain": B3_V2_RECOVERY_PREFIX_DOMAIN,
    }


def binary64_materialized_text(value: Any) -> str:
    """Return the exact decimal round-trip used by simulator YAML inputs."""

    try:
        numeric = float(Fraction(str(value)))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("binary64 energy materialization requires a number") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("binary64 energy materialization requires a finite non-negative value")
    return format(numeric, ".17g")


def binary64_prefix_sum(values: Sequence[Any]) -> str:
    """Mirror the native left-to-right ``double`` prefix-energy sum."""

    total = 0.0
    for value in values:
        total += float(Fraction(binary64_materialized_text(value)))
    if not math.isfinite(total) or total <= 0:
        raise ValueError("runtime recovery prefix must require positive finite energy")
    return format(total, ".17g")


def native_binary64_affordable(available: Any, required: Any) -> bool:
    """Mirror the native joule-domain affordability comparison."""

    available_value = float(Fraction(binary64_materialized_text(available)))
    required_value = float(Fraction(binary64_materialized_text(required)))
    return available_value + float(NATIVE_ENERGY_EPSILON_J) >= required_value


def recovery_prefix_affordable_at_capacity(
    prefix_material: Mapping[str, Any], capacity: Any,
) -> bool:
    """Return whether native ST can reserve the frozen full prefix."""

    return native_binary64_affordable(
        capacity, prefix_material["recovery_prefix_required_energy"],
    )


def runtime_recovery_prefix(
    tasks: Sequence[Mapping[str, Any]],
    processors: int,
    *,
    initial_energy: Any,
) -> dict[str, Any]:
    """Freeze the synchronous runtime RM/task-number top-q prefix.

    The simulator assigns task numbers in materialized YAML order.  Its ST
    comparator orders by RM period and then that task number.
    """

    if isinstance(processors, bool) or not isinstance(processors, int) or processors <= 0:
        raise ValueError("processor count must be a positive integer")
    numbered = [(task_number, dict(row)) for task_number, row in enumerate(tasks)]
    ready = [
        (task_number, row) for task_number, row in numbered
        if int(row.get("arrival_offset", 0)) == 0
    ]
    ready.sort(key=lambda item: (int(item[1]["T"]), item[0]))
    if not ready:
        raise ValueError("recovery prefix has no initially ready jobs")
    ranked = [int(row["priority_rank"]) for _, row in ready]
    if ranked != sorted(ranked) or ranked[:min(processors, len(ready))] != list(
        range(min(processors, len(ready)))
    ):
        raise ValueError("frozen priority ranks disagree with runtime RM/task-number order")

    q_value = min(processors, len(ready))
    prefix = ready[:q_value]
    task_ids = [str(row["task_id"]) for _, row in prefix]
    runtime_names = [
        runtime_task_name_for_source_id(task_id) for task_id in task_ids
    ]
    priority_ranks = [int(row["priority_rank"]) for _, row in prefix]
    unit_energies = [binary64_materialized_text(row["P"]) for _, row in prefix]
    required_energy = binary64_prefix_sum(unit_energies)
    capacity = binary64_materialized_text(required_energy)
    target_energy = binary64_materialized_text(prefix[0][1]["P"])
    initial = binary64_materialized_text(initial_energy)
    target_blocked = not native_binary64_affordable(initial, target_energy)
    prefix_affordable = native_binary64_affordable(capacity, required_energy)
    earliest_deadline = min(int(row["D"]) for _, row in ready)
    material = {
        "target_recovery_contract_applicable": True,
        "recovery_prefix_order": "RM_PERIOD_THEN_RUNTIME_TASK_NUMBER",
        "initial_ready_job_count": len(ready),
        "recovery_prefix_length": q_value,
        "recovery_prefix_task_ids": task_ids,
        "recovery_prefix_runtime_names": runtime_names,
        "recovery_prefix_priority_ranks": priority_ranks,
        "recovery_prefix_unit_energies": unit_energies,
        "recovery_prefix_required_energy": required_energy,
        "materialized_battery_capacity": capacity,
        "recovery_prefix_affordable_at_full": prefix_affordable,
        "target_blocked_at_initial_energy": target_blocked,
        "recovery_earliest_initial_deadline": earliest_deadline,
        "native_affordability_epsilon_j": fraction_text(
            NATIVE_ENERGY_EPSILON_J
        ),
    }
    material["recovery_prefix_identity"] = v2_recovery_prefix_identity(
        material
    )
    return material


def v2_recovery_prefix_identity(material: Mapping[str, Any]) -> str:
    missing = [
        key for key in RECOVERY_PREFIX_MATERIAL_FIELDS if key not in material
    ]
    if missing:
        raise ValueError(f"recovery prefix identity lacks fields: {missing}")
    return domain_hash(B3_V2_RECOVERY_PREFIX_DOMAIN, {
        key: material[key] for key in RECOVERY_PREFIX_MATERIAL_FIELDS
    })


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
    recovery_contract: Mapping[str, Any] | None = None,
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
        "recovery_contract": dict(recovery_contract or {}),
    })


@dataclass(frozen=True)
class ActualTraceRecovery:
    affordable_tick: int
    full_tick: int
    target_initial_slack: int
    configured_recovery_margin_ticks: int
    recovery_headroom: int
    earliest_initial_deadline: int | None
    predicate_satisfied: bool

    def material(self) -> dict[str, Any]:
        return {
            "actual_trace_affordable_tick": self.affordable_tick,
            "actual_trace_target_affordable_tick": self.affordable_tick,
            "actual_trace_full_tick": self.full_tick,
            "target_initial_slack": self.target_initial_slack,
            "configured_recovery_margin_ticks": (
                self.configured_recovery_margin_ticks
            ),
            "actual_trace_recovery_headroom": self.recovery_headroom,
            "recovery_earliest_initial_deadline": (
                self.earliest_initial_deadline
            ),
            "actual_trace_full_tick_strictly_before_earliest_initial_deadline": (
                self.earliest_initial_deadline is None
                or self.full_tick < self.earliest_initial_deadline
            ),
            "predicate": (
                "E_init + native_epsilon < e_target; 0 < "
                "actual_trace_affordable_tick <= actual_trace_full_tick; "
                "actual_trace_full_tick + configured_recovery_margin_ticks "
                "< target_initial_slack; actual_trace_full_tick < "
                "recovery_earliest_initial_deadline"
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
    earliest_initial_deadline: int | None = None,
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
    if earliest_initial_deadline is not None and (
        isinstance(earliest_initial_deadline, bool)
        or not isinstance(earliest_initial_deadline, int)
        or earliest_initial_deadline <= 0
    ):
        raise ValueError("earliest initial deadline must be a positive integer")

    energy = float(initial)
    capacity_value = float(capacity)
    target_value = float(target)
    epsilon = float(NATIVE_ENERGY_EPSILON_J)
    affordable_tick = (
        0 if energy + epsilon >= target_value else None
    )
    full_tick = (
        0 if energy + epsilon >= capacity_value else None
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
        energy = min(capacity_value, energy + float(applied_harvest))
        if (
            affordable_tick is None
            and energy + epsilon >= target_value
        ):
            affordable_tick = tick
        if full_tick is None and energy + epsilon >= capacity_value:
            full_tick = tick
        if affordable_tick is not None and full_tick is not None:
            break

    if affordable_tick is None:
        raise ValueError("actual trace never makes the target affordable")
    if full_tick is None:
        raise ValueError("actual trace never reaches the ST full-battery release")
    recovery_headroom = target_initial_slack - full_tick
    predicate = (
        float(initial) + epsilon < target_value
        and 0 < affordable_tick <= full_tick
        and full_tick + recovery_margin_ticks < target_initial_slack
        and (
            earliest_initial_deadline is None
            or full_tick < earliest_initial_deadline
        )
    )
    return ActualTraceRecovery(
        affordable_tick,
        full_tick,
        target_initial_slack,
        recovery_margin_ticks,
        recovery_headroom,
        earliest_initial_deadline,
        predicate,
    )
