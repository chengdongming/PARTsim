from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

import asap_block_rta as legacy_rta

from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import config_hash, load_config
from experiments.v9_3.config import TASK_WORKLOAD_CONTRACT_VERSION
from experiments.v9_3.core3_pairing import Core3PairingRunner
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    construct_paired_harvest_trace,
    core3_energy_preflight,
    materialize_simulation_inputs,
    no_overflow_contract,
    select_largest_dyadic_solar_scale,
    validate_no_overflow_guard,
)
from experiments.v9_3.taskset_store import prepare_service_curve


ROOT = Path(__file__).resolve().parents[1]
R1_B20 = ROOT / "configs/v9_3_core3_formal_b20.yaml"
R1_B100 = ROOT / "configs/v9_3_core3_formal_b100.yaml"
R2_B20 = ROOT / "configs/v9_3_core3_formal_b20_r2.yaml"
R2_B100 = ROOT / "configs/v9_3_core3_formal_b100_r2.yaml"
# These identities intentionally follow the production EnergyConfig phase:
# 21,975,000 ms is floored to minute 366 before tick advancement.
H_RAW = Fraction(1313553644316084375, 1125899906842624)
H_SCALED = Fraction(1313553644316084375, 144115188075855872)


@pytest.mark.parametrize(("initial", "capacity", "harvest", "margin", "valid"), [
    (20, 20, 1, 0, False),
    (20, 20, 0, 0, True),
    (1, 20, 19, 0, True),
    (1, 20, Fraction(191, 10), 0, False),
    (1, 20, 18, 1, True),
    (1, 20, Fraction(181, 10), 1, False),
])
def test_no_overflow_contract_boundaries(initial, capacity, harvest, margin, valid):
    _required, _available, observed = no_overflow_contract(
        initial_battery=Fraction(initial),
        battery_capacity=Fraction(capacity),
        offered_harvest=Fraction(harvest),
        required_safety_margin=Fraction(margin),
    )
    assert observed is valid


def test_frozen_dyadic_rule_selects_one_over_128():
    scale = select_largest_dyadic_solar_scale(
        raw_offered_harvest=H_RAW,
        initial_battery=Fraction(1),
        battery_capacity=Fraction(20),
        required_safety_margin=Fraction(1),
    )
    assert scale == Fraction(1, 128)
    assert Fraction(1) + scale * H_RAW <= Fraction(19)
    assert Fraction(1) + 2 * scale * H_RAW > Fraction(19)


def test_r2_preflight_rejects_a_nonmaximal_dyadic_scale():
    config = load_config(R2_B20, expected_core="CORE-3")
    config["energy"]["service_curve"]["solar_scale"] = "1/256"
    with pytest.raises(
        SimulationConfigurationError, match="not the largest feasible dyadic"
    ):
        core3_energy_preflight(config)


def test_r1_formal_tracks_fail_closed_with_positive_real_harvest():
    b20 = core3_energy_preflight(load_config(R1_B20, expected_core="CORE-3"))
    b100 = core3_energy_preflight(load_config(R1_B100, expected_core="CORE-3"))
    for report in (b20, b100):
        assert report["use_real_solar_data"] is True
        assert Fraction(report["raw_offered_harvest_j"]) == H_RAW
        assert Fraction(report["scaled_offered_harvest_j"]) > 0
        assert report["no_overflow_preflight_valid"] is False
        assert Fraction(report["available_headroom_j"]) < 0


def test_r2_formal_tracks_share_scale_service_and_harvest_with_fixed_margin():
    b20 = core3_energy_preflight(load_config(R2_B20, expected_core="CORE-3"))
    b100 = core3_energy_preflight(load_config(R2_B100, expected_core="CORE-3"))
    for report in (b20, b100):
        assert report["simulation_initial_battery_j"] == "1"
        assert report["applied_solar_scale"] == "1/128"
        assert report["largest_feasible_dyadic_scale"] == "1/128"
        assert report["dyadic_scale_selection_rule"] == (
            "largest_feasible_dyadic_v1"
        )
        assert report["pv_area_m2"] == "1/128"
        assert Fraction(report["raw_offered_harvest_j"]) == H_RAW
        assert Fraction(report["scaled_offered_harvest_j"]) == H_SCALED
        assert Fraction(report["available_headroom_j"]) >= 1
        assert report["no_overflow_preflight_valid"] is True
    assert b20["service_curve_id"] == b100["service_curve_id"]
    assert b20["scaled_offered_harvest_j"] == b100["scaled_offered_harvest_j"]
    assert Fraction(b100["available_headroom_j"]) > Fraction(
        b20["available_headroom_j"]
    )


@pytest.mark.parametrize("path", [R1_B20, R1_B100, R2_B20, R2_B100])
def test_core3_formal_configs_enforce_the_global_workload_contract(path):
    config = load_config(path, expected_core="CORE-3")
    assert config["generation"]["workload_candidates"] == [
        "bzip2", "control", "decrypt", "encrypt", "hash",
    ]
    assert config["generation"]["workload_contract"]["version"] == (
        TASK_WORKLOAD_CONTRACT_VERSION
    )


@pytest.mark.parametrize("path", [R2_B20, R2_B100])
def test_r2_formal_artifact_identity_uses_fresh_workload_contract_paths(path):
    config = load_config(path, expected_core="CORE-3")
    assert config["experiment_id"].endswith("-workload-contract-v2")
    assert "workload_contract_v2" in config["execution"]["output_root"]
    assert "workload_contract_v2" in config["execution"]["taskset_store"]


def test_r2_tracks_only_change_capacity_and_experiment_output_identity():
    b20 = load_config(R2_B20, expected_core="CORE-3")
    b100 = load_config(R2_B100, expected_core="CORE-3")
    assert b20["energy"]["simulation_initial_battery"] == "1"
    assert b100["energy"]["simulation_initial_battery"] == "1"
    assert b20["energy"]["battery_capacity"] == "20"
    assert b100["energy"]["battery_capacity"] == "100"
    assert config_hash(b20) != config_hash(b100)
    cells20 = expand_cells(b20)
    cells100 = expand_cells(b100)
    assert [cell.generation_id for cell in cells20] == [
        cell.generation_id for cell in cells100
    ]
    assert [
        derive_seed(b20["grid"]["base_seed"], cell.generation_id, 0)
        for cell in cells20
    ] == [
        derive_seed(b100["grid"]["base_seed"], cell.generation_id, 0)
        for cell in cells100
    ]
    left, right = deepcopy(b20), deepcopy(b100)
    left.pop("experiment_id")
    right.pop("experiment_id")
    left["energy"].pop("battery_capacity")
    right["energy"].pop("battery_capacity")
    for key in ("output_root", "taskset_store"):
        left["execution"].pop(key)
        right["execution"].pop(key)
    assert left == right


@pytest.mark.parametrize("path", [R2_B20, R2_B100])
def test_r2_request_counts_remain_frozen(path):
    description = Core3PairingRunner(
        load_config(path, expected_core="CORE-3")
    ).describe()
    assert description["cell_count"] == 24
    assert description["unique_taskset_count"] == 1600
    assert description["rta_request_count"] == 9600
    assert description["simulation_request_count"] == 1600
    assert description["total_terminal_count"] == 11200


def test_preflight_and_runtime_guard_share_trace_constructor(tmp_path, monkeypatch):
    import experiments.v9_3.simulation_engine as engine

    calls = []

    def trace_spy(_path, horizon):
        calls.append(horizon)
        return (Fraction(0),) * horizon

    monkeypatch.setattr(engine, "construct_paired_harvest_trace", trace_spy)
    config = load_config(
        ROOT / "configs/v9_3_core3_smoke.yaml", expected_core="CORE-3"
    )
    report = core3_energy_preflight(config)
    assert report["no_overflow_preflight_valid"] is True
    assert calls == [1600, 1600]
    system_path = tmp_path / "system.yaml"
    system_path.write_text("unused by spy\n", encoding="utf-8")
    validate_no_overflow_guard(
        system_path,
        2,
        initial_battery=Fraction(1),
        battery_capacity=Fraction(20),
    )
    assert calls[-1] == 2


def test_rta_and_simulation_materialize_the_same_scaled_solar_trace(tmp_path):
    config = load_config(R2_B20, expected_core="CORE-3")
    rta_material = prepare_service_curve(config, tmp_path / "rta")
    b100_material = prepare_service_curve(
        load_config(R2_B100, expected_core="CORE-3"), tmp_path / "rta-b100"
    )
    simulation_system, _ = materialize_simulation_inputs(
        ROOT / config["energy"]["service_curve"]["system_template"],
        tmp_path / "simulation",
        (),
        processors=4,
        initial_battery=Fraction(1),
        battery_capacity=Fraction(20),
        service_curve=config["energy"]["service_curve"],
    )
    rta_system = legacy_rta.load_system_config(str(rta_material.system_path))
    simulator_system = legacy_rta.load_system_config(str(simulation_system))
    assert rta_system.pv_area_m2 == simulator_system.pv_area_m2 == 1 / 128
    assert rta_system.day_of_year == simulator_system.day_of_year == 187
    assert rta_system.time_of_day_ms == simulator_system.time_of_day_ms == 21900000
    assert rta_material.identity == b100_material.identity
    assert construct_paired_harvest_trace(
        rta_material.system_path, 30000
    ) == construct_paired_harvest_trace(simulation_system, 30000)


def test_template_and_solar_hashes_change_preflight_identity(tmp_path):
    solar = tmp_path / "solar.csv"
    solar.write_text("irradiance_W_per_m2\n1\n", encoding="utf-8")
    template = tmp_path / "system.yml"
    source = (ROOT / "system_config_unified_template.yml").read_text(
        encoding="utf-8"
    )
    source = source.replace(
        'solar_data_file: "data/processed/shenyang_solar_minute.csv"',
        f"solar_data_file: {json.dumps(str(solar))}",
    )
    template.write_text(source, encoding="utf-8")
    config = load_config(
        ROOT / "configs/v9_3_core3_smoke.yaml", expected_core="CORE-3"
    )
    config["energy"]["service_curve"]["system_template"] = str(template)
    config["simulation"]["maximum_horizon"] = 1
    first = core3_energy_preflight(config)
    solar.write_text("irradiance_W_per_m2\n2\n", encoding="utf-8")
    second = core3_energy_preflight(config)
    assert first["solar_data_sha256"] != second["solar_data_sha256"]
    assert first["preflight_identity"] != second["preflight_identity"]
    template.write_text(source + "\n# audit identity change\n", encoding="utf-8")
    third = core3_energy_preflight(config)
    assert second["system_template_sha256"] != third["system_template_sha256"]
    assert second["preflight_identity"] != third["preflight_identity"]


def test_cli_preflight_creates_no_formal_artifacts(tmp_path):
    config = yaml.safe_load(R2_B20.read_text(encoding="utf-8"))
    output_root = tmp_path / "formal-output"
    taskset_store = tmp_path / "formal-tasksets"
    config["execution"]["output_root"] = str(output_root)
    config["execution"]["taskset_store"] = str(taskset_store)
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_v9_3_core3.py",
            "--config",
            str(config_path),
            "--preflight",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["no_overflow_preflight_valid"] is True
    assert not output_root.exists()
    assert not taskset_store.exists()


def test_invalid_runner_stops_before_execution_engine_and_artifacts(
    tmp_path, monkeypatch,
):
    config = load_config(R2_B20, expected_core="CORE-3")
    config["energy"]["battery_capacity"] = "1"
    output_root = tmp_path / "formal-output"
    taskset_store = tmp_path / "formal-tasksets"
    config["execution"]["output_root"] = str(output_root)
    config["execution"]["taskset_store"] = str(taskset_store)

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("ExecutionEngine.run must not be reached")

    monkeypatch.setattr(ExecutionEngine, "run", forbidden_run)
    with pytest.raises(SimulationConfigurationError, match="preflight failed"):
        Core3PairingRunner(config).run()
    assert not output_root.exists()
    assert not taskset_store.exists()
