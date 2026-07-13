from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import config_hash, load_config
from experiments.v9_3.execution_engine import ExecutionEngine, ExecutionError
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    materialize_simulation_inputs,
    simulation_identity,
    validate_no_overflow_guard,
)
from v9_3_core3_helpers import task_payload


ROOT = Path(__file__).resolve().parents[1]


def core3_config() -> dict:
    return load_config(ROOT / "configs/v9_3_core3_smoke.yaml", expected_core="CORE-3")


def test_core3_config_reuses_two_main_methods_and_separates_energy_mapping():
    config = core3_config()
    assert config["analysis"]["variants"] == ["CW_THETA_CW", "LOC_THETA_LOC"]
    assert config["energy"]["initial_energy_values"] == ["1"]
    assert config["energy"]["simulation_initial_battery"] == "20"
    assert Fraction(config["energy"]["simulation_initial_battery"]) > Fraction("1")


def test_simulator_projection_preserves_timing_priority_and_energy_workload(tmp_path):
    payload = task_payload()
    system_path, task_path = materialize_simulation_inputs(
        ROOT / "system_config_unified_template.yml", tmp_path, payload,
        processors=4, initial_battery=Fraction(20), battery_capacity=Fraction(100),
    )
    system = yaml.safe_load(system_path.read_text(encoding="utf-8"))
    tasks = yaml.safe_load(task_path.read_text(encoding="utf-8"))["taskset"]
    assert system["cpu_islands"][0]["numcpus"] == 4
    assert system["cpu_islands"][0]["kernel"]["scheduler"] == "gpfp_asap_block"
    assert system["energy_management"]["initial_energy"] == 20.0
    assert all(
        item["speed_params"] == [1, 0, 0, 0]
        for model in system["power_models"] for item in model["params"]
    )
    assert tasks[0]["name"] == "v93_task_0"
    assert (tasks[0]["runtime"], tasks[0]["deadline"], tasks[0]["iat"]) == (2, 5, 10)
    assert "workload=control" in tasks[0]["params"]


def test_no_overflow_guard_rejects_capacity_that_can_clip(tmp_path, monkeypatch):
    system_path, _ = materialize_simulation_inputs(
        ROOT / "system_config_unified_template.yml", tmp_path, task_payload(),
        processors=4, initial_battery=Fraction(20), battery_capacity=Fraction(20),
    )
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.legacy_rta._harvest_trace_from_config",
        lambda _system, horizon: [1.0] * horizon,
    )
    with pytest.raises(SimulationConfigurationError, match="can clip"):
        validate_no_overflow_guard(
            system_path, 2, initial_battery=Fraction(20),
            battery_capacity=Fraction(20),
        )


def test_pairing_and_seed_identities_are_deterministic_and_simulation_sensitive():
    config = core3_config()
    cells = expand_cells(config)
    seed1 = derive_seed(config["grid"]["base_seed"], cells[0].generation_id, 0)
    seed2 = derive_seed(config["grid"]["base_seed"], cells[0].generation_id, 0)
    assert seed1 == seed2
    first = simulation_identity(
        cells[0].cell_id, "a" * 64, cells[0].exact_e0, config["simulation"]
    )
    changed = deepcopy(config["simulation"])
    changed["minimum_jobs_per_task"] += 1
    second = simulation_identity(cells[0].cell_id, "a" * 64, cells[0].exact_e0, changed)
    assert first != second
    assert config_hash(config) != config_hash({**config, "simulation": changed})


def test_shared_engine_rejects_config_hash_mismatch_before_resume(tmp_path):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path / "run")
    run_root = Path(config["execution"]["output_root"])
    run_root.mkdir()
    (run_root / "run_metadata.json").write_text(
        json.dumps({"config_hash": "wrong"}), encoding="utf-8"
    )
    with pytest.raises(ExecutionError, match="configuration hash mismatch"):
        ExecutionEngine(config)._initialize(resume=True)
