from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path

import pytest
import yaml

import asap_block_rta as legacy_rta
from global_task_generator import EnergyAwareTaskGenerator

from experiments.v9_3 import exact_energy
from experiments.v9_3.config import (
    ConfigError,
    shared_task_energy_input,
    task_demand_for_wcet,
)


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PATH = ROOT / "system_config_unified_template.yml"
WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")


def _source_identity(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_five_workloads_and_wcets_match_fact_source_and_compatibility_wrapper():
    system = legacy_rta.load_system_config(str(SYSTEM_PATH))
    source_identity = _source_identity(SYSTEM_PATH)

    assert tuple(
        sorted(name for name in system.workload_coefficients if name != "idle")
    ) == WORKLOADS
    for workload in WORKLOADS:
        for wcet in range(1, 201):
            shared = shared_task_energy_input(
                system,
                workload,
                wcet,
                label=f"{workload}-{wcet}",
                source_identity=source_identity,
            )
            expected = exact_energy.materialize_task_demand_upper_bound(
                base_power=system.base_power,
                workload_coefficient=system.workload_coefficient(workload),
                frequency_ratio=system.frequency_ratio(),
                wcet=wcet,
                energy_coefficient=1.0,
                label=f"expected-{workload}-{wcet}",
            )
            compatibility_wrapper = task_demand_for_wcet(
                system,
                workload,
                wcet,
                label=f"compatibility-{workload}-{wcet}",
            )

            assert shared.energy_j_per_tick == expected.exact_value
            assert shared.energy_j_per_tick_binary64 == expected.binary64_hex
            assert shared.energy_j_per_tick == compatibility_wrapper
            assert shared.unit == "J/tick"
            assert shared.source_identity == source_identity


def test_task_energy_provenance_exposes_all_scheduler_operands():
    system = legacy_rta.load_system_config(str(SYSTEM_PATH))
    shared = shared_task_energy_input(
        system,
        "hash",
        17,
        label="provenance",
        source_identity=_source_identity(SYSTEM_PATH),
    )
    material = shared.provenance_material()

    assert material["base_power_binary64"] == system.base_power.hex()
    assert material["workload_coefficient_binary64"] == (
        system.workload_coefficient("hash").hex()
    )
    assert material["frequency_ratio_binary64"] == system.frequency_ratio().hex()
    assert material["energy_coefficient_binary64"] == (1.0).hex()
    assert material["energy_j_per_tick"] == exact_energy.fraction_text(
        shared.energy_j_per_tick
    )
    assert material["unit"] == "J/tick"


def test_scheduler_energy_coefficient_is_delegated_without_a_shortcut():
    system = legacy_rta.load_system_config(str(SYSTEM_PATH))
    coefficient = 0.75
    shared = shared_task_energy_input(
        system,
        "encrypt",
        19,
        label="coefficient",
        source_identity=_source_identity(SYSTEM_PATH),
        energy_coefficient=coefficient,
    )
    expected = exact_energy.materialize_task_demand_upper_bound(
        base_power=system.base_power,
        workload_coefficient=system.workload_coefficient("encrypt"),
        frequency_ratio=system.frequency_ratio(),
        wcet=19,
        energy_coefficient=coefficient,
        label="expected coefficient",
    )
    assert shared.energy_j_per_tick == expected.exact_value
    assert shared.energy_j_per_tick_binary64 == expected.binary64_hex


def test_old_watt_reconstruction_differs_from_correct_joules_per_tick():
    generator = EnergyAwareTaskGenerator(
        seed=0,
        energy_manager=None,
        system_config_path=str(SYSTEM_PATH),
    )
    wcet = 23
    workload = "control"
    total_energy = generator.calculate_energy(
        wcet, workload, generator.base_frequency
    )
    old_watt_value = (
        Fraction.from_float(float(total_energy))
        / Fraction(wcet, 1000)
    )
    system = legacy_rta.load_system_config(str(SYSTEM_PATH))
    shared = shared_task_energy_input(
        system,
        workload,
        wcet,
        label="old-watt-regression",
        source_identity=_source_identity(SYSTEM_PATH),
    )

    assert old_watt_value != shared.energy_j_per_tick
    assert float(old_watt_value) > float(shared.energy_j_per_tick)
    assert shared.unit == "J/tick"


def test_legal_system_parameter_changes_are_recomputed_from_the_new_source(
    tmp_path,
):
    baseline_system = legacy_rta.load_system_config(str(SYSTEM_PATH))
    baseline = shared_task_energy_input(
        baseline_system,
        "bzip2",
        31,
        label="baseline",
        source_identity=_source_identity(SYSTEM_PATH),
    )

    document = yaml.safe_load(SYSTEM_PATH.read_text(encoding="utf-8"))
    island = document["cpu_islands"][0]
    island["base_freq"] = 9_500
    model = document["energy_management"]["scheduler_energy_model"]
    model["base_power"] = 0.7
    model["workload_coefficients"]["bzip2"] = 1.1
    model["frequency_power_ratios"][9_500] = 1.2
    changed_path = tmp_path / "changed-system.yml"
    changed_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    changed_system = legacy_rta.load_system_config(str(changed_path))
    changed = shared_task_energy_input(
        changed_system,
        "bzip2",
        31,
        label="changed",
        source_identity=_source_identity(changed_path),
    )
    expected = exact_energy.materialize_task_demand_upper_bound(
        base_power=changed_system.base_power,
        workload_coefficient=changed_system.workload_coefficient("bzip2"),
        frequency_ratio=changed_system.frequency_ratio(),
        wcet=31,
        energy_coefficient=1.0,
        label="changed expected",
    )

    assert changed.energy_j_per_tick == expected.exact_value
    assert changed.energy_j_per_tick != baseline.energy_j_per_tick
    assert changed.source_identity != baseline.source_identity


def test_shared_task_energy_requires_explicit_source_identity():
    system = legacy_rta.load_system_config(str(SYSTEM_PATH))
    with pytest.raises(ConfigError, match="source_identity"):
        shared_task_energy_input(
            system,
            "decrypt",
            1,
            label="missing identity",
            source_identity="",
        )
