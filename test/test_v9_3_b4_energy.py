from fractions import Fraction

from experiments.v9_3.performance_energy import (
    battery_values, build_energy_material, burst_energy, runner_energy_config,
    taskset_demand,
)
from v9_3_b4_helpers import task_payload


def reference():
    return {
        "reference_power_j_per_tick": Fraction(2),
        "solar_source_hash": "solar", "solar_phase_ms": 7,
        "raw_reference_pv_area_m2": Fraction(1),
        "normalization_horizon_ms": 60000,
        "system_template_hash": "template",
    }


def test_exact_demand_burst_and_battery():
    tasks = task_payload()
    assert taskset_demand(tasks) == sum(Fraction(t["C"], t["T"]) * Fraction(t["P"]) for t in tasks)
    assert burst_energy(tasks, 4) == 10
    assert battery_values(tasks, 4, "50") == (Fraction(500), Fraction(250))


def test_fixed_window_energy_identity_is_horizon_independent():
    material = build_energy_material(
        task_payload=task_payload(), taskset_semantic_hash="taskset",
        processors=4, kappa="50", eta="3/4", solar_reference=reference(),
        power_contract_hash="power",
    )
    assert material.normalization_horizon_ms == 60000
    assert "runtime_horizon" not in material.material()
    assert material.identity == build_energy_material(
        task_payload=task_payload(), taskset_semantic_hash="taskset",
        processors=4, kappa="50", eta="3/4", solar_reference=reference(),
        power_contract_hash="power",
    ).identity
    assert material.materialized_initial_energy == "250"
    assert runner_energy_config(material)["allow_harvest_clipping"] is True
