"""B3 scenario-level per-tick battery-capacity feasibility contract."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

import asap_block_rta as legacy_rta

from . import exact_energy
from .config import (
    canonical_json, domain_hash, fraction_text, task_demand_for_wcet,
)
from .task_identity import runtime_task_name_for_source_id


B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION = (
    "B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_V1"
)
CAPACITY_FEASIBILITY_ERROR_CODE = (
    "TASK_TICK_ENERGY_EXCEEDS_BATTERY_CAPACITY"
)
CAPACITY_FEASIBILITY_CONTRACT_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B3:TASK_CAPACITY_FEASIBILITY_CONTRACT:v1"
)

# These are exact Python mirrors of the two native quantities used by the
# simulator: its one-millisecond tick and STEnergy::kEnergyEpsilonJ.
SIMULATOR_TICK_DURATION_SECONDS = Fraction(str(legacy_rta.TICK_SECONDS))
NATIVE_ENERGY_EPSILON_J = Fraction(1, 10**9)


def capacity_contract_material(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical B3 contract material tied to the actual model."""

    workload = config["generation"]["workload_contract"]
    return {
        "version": B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION,
        "predicate": (
            "task_tick_energy_j <= battery_capacity_j + "
            "native_affordability_epsilon_j"
        ),
        "tick_duration_seconds": fraction_text(
            SIMULATOR_TICK_DURATION_SECONDS
        ),
        "native_affordability_epsilon_j": fraction_text(
            NATIVE_ENERGY_EPSILON_J
        ),
        "workload_contract_version": workload["version"],
        "power_model_identity": workload["power_model_identity"],
        "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
    }


def capacity_contract_identity(config: Mapping[str, Any]) -> str:
    return domain_hash(
        CAPACITY_FEASIBILITY_CONTRACT_DOMAIN,
        capacity_contract_material(config),
    )


def capacity_feasibility_violations(
    tasks: Sequence[Mapping[str, Any]],
    battery_capacity: Fraction,
    config: Mapping[str, Any],
) -> Tuple[Dict[str, Any], ...]:
    """Return exact diagnostics for tasks a full battery cannot execute."""

    capacity = Fraction(battery_capacity)
    workload_contract = config["generation"]["workload_contract"]
    known_workloads = {
        str(row["workload"])
        for row in workload_contract["power_model"]
    }
    system_path = (
        Path(__file__).resolve().parents[2]
        / config["energy"]["service_curve"]["system_template"]
    )
    system = legacy_rta.load_system_config(str(system_path))
    contract_identity = capacity_contract_identity(config)
    violations = []
    for row in tasks:
        workload = str(row.get("workload", ""))
        if workload not in known_workloads:
            raise ValueError(
                "capacity feasibility received workload outside the actual "
                f"power model: {workload}"
            )
        task_energy = task_demand_for_wcet(
            system,
            workload,
            int(row["C"]),
            label=f"capacity task {row.get('task_id', '')} exact P",
        )
        try:
            frozen_energy = exact_energy.parse_persisted_fraction(
                row["P"], f"capacity task {row.get('task_id', '')} frozen P",
            )
        except (KeyError, exact_energy.ExactEnergyError) as exc:
            raise ValueError(
                "capacity feasibility received invalid materialized task P"
            ) from exc
        if frozen_energy != task_energy:
            raise ValueError(
                "capacity feasibility materialized task P does not match the "
                "actual power model"
            )
        if task_energy <= capacity + NATIVE_ENERGY_EPSILON_J:
            continue

        task_energy_mj = task_energy * 1000
        capacity_mj = capacity * 1000
        epsilon_mj = NATIVE_ENERGY_EPSILON_J * 1000
        actual_power_w = task_energy / SIMULATOR_TICK_DURATION_SECONDS
        excess_mj = task_energy_mj - capacity_mj - epsilon_mj
        task_id = str(row.get("task_id", ""))
        violations.append({
            "code": CAPACITY_FEASIBILITY_ERROR_CODE,
            "task_name": runtime_task_name_for_source_id(task_id),
            "workload": workload,
            "actual_power": fraction_text(actual_power_w),
            "actual_power_unit": "W",
            "tick_duration": fraction_text(
                SIMULATOR_TICK_DURATION_SECONDS
            ),
            "tick_duration_unit": "s",
            "task_tick_energy_mJ": fraction_text(task_energy_mj),
            "battery_capacity_mJ": fraction_text(capacity_mj),
            "native_affordability_epsilon_mJ": fraction_text(epsilon_mj),
            "excess_energy_mJ": fraction_text(excess_mj),
            "energy_unit": "mJ",
            "power_model_identity": workload_contract[
                "power_model_identity"
            ],
            "workload_contract_version": workload_contract["version"],
            "capacity_feasibility_contract_version": (
                B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION
            ),
            "capacity_feasibility_contract_identity": contract_identity,
        })
    return tuple(violations)


def capacity_rejection_detail(
    violations: Sequence[Mapping[str, Any]],
) -> str:
    """Stable bounded detail for the structural-rejection text column."""

    return canonical_json({
        "capacity_infeasible_task_count": len(violations),
        "representative_violation": dict(violations[0]),
    })
