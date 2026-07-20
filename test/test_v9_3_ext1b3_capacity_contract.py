"""B3 scenario-level per-tick battery-capacity contract tests."""

from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.v9_3.ext1b_capacity_contract import (  # noqa: E402
    B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION,
    CAPACITY_FEASIBILITY_ERROR_CODE,
    NATIVE_ENERGY_EPSILON_J,
    SIMULATOR_TICK_DURATION_SECONDS,
    capacity_contract_identity,
    capacity_feasibility_violations,
)
from experiments.v9_3.ext1b_config import (  # noqa: E402
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.config import domain_hash  # noqa: E402
from experiments.v9_3.ext1b_engine import Ext1BRunner  # noqa: E402
from experiments.v9_3.ext1b_generation import (  # noqa: E402
    StructuralRejection,
    build_scenario_instance,
    enforce_b3_capacity_feasibility,
    scenario_cells,
)
from experiments.v9_3.cell_model import expand_cells  # noqa: E402
from experiments.v9_3.taskset_store import (  # noqa: E402
    TasksetStore,
    TasksetStoreError,
    prepare_service_curve,
)


CALIBRATION = ROOT / "configs/v9_3_ext1b3_timing_calibration.yaml"


def _task(config, workload="hash", task_id="0"):
    model = {
        str(row["workload"]): Fraction(str(row["energy_per_tick"]))
        for row in config["generation"]["workload_contract"]["power_model"]
    }
    return {
        "task_id": task_id,
        "source_name": f"source-{task_id}",
        "priority_rank": int(task_id),
        "C": 1,
        "D": 10,
        "T": 20,
        "P": str(model[workload]),
        "D_over_T": "1/2",
        "workload": workload,
        "arrival_offset": 0,
    }


@pytest.mark.parametrize(
    "capacity_delta,expected_count",
    [
        (Fraction(1, 10**6), 0),
        (Fraction(0), 0),
        (-NATIVE_ENERGY_EPSILON_J, 0),
        (-NATIVE_ENERGY_EPSILON_J - Fraction(1, 10**12), 1),
    ],
    ids=("below-capacity", "equal-capacity", "native-epsilon", "above-epsilon"),
)
def test_capacity_boundary_matches_native_simulator_predicate(
    capacity_delta, expected_count,
):
    config = load_ext1b_config(CALIBRATION)
    task = _task(config)
    energy = Fraction(task["P"])
    violations = capacity_feasibility_violations(
        [task], energy + capacity_delta, config,
    )
    assert len(violations) == expected_count


def test_one_infeasible_task_rejects_whole_candidate_with_complete_diagnostics():
    config = load_ext1b_config(CALIBRATION)
    low = _task(config, "control", "0")
    high = _task(config, "bzip2", "1")
    capacity = Fraction(low["P"])

    with pytest.raises(StructuralRejection) as caught:
        enforce_b3_capacity_feasibility([low, high], capacity, config)

    error = caught.value
    assert error.code == CAPACITY_FEASIBILITY_ERROR_CODE
    assert error.diagnostics["capacity_infeasible_task_count"] == 1
    assert error.diagnostics["task_name"] == "v93_task_1"
    assert error.diagnostics["workload"] == "bzip2"
    assert Fraction(error.diagnostics["task_tick_energy_mJ"]) > (
        Fraction(error.diagnostics["battery_capacity_mJ"])
        + Fraction(error.diagnostics["native_affordability_epsilon_mJ"])
    )
    assert error.diagnostics["actual_power_unit"] == "W"
    assert error.diagnostics["tick_duration_unit"] == "s"
    assert error.diagnostics["energy_unit"] == "mJ"
    assert error.diagnostics["power_model_identity"] == config[
        "generation"
    ]["workload_contract"]["power_model_identity"]
    assert error.diagnostics["workload_contract_version"] == config[
        "generation"
    ]["workload_contract"]["version"]


def test_capacity_contract_reuses_native_tick_and_epsilon_authorities():
    config = load_ext1b_config(CALIBRATION)
    assert SIMULATOR_TICK_DURATION_SECONDS == Fraction(1, 1000)
    assert NATIVE_ENERGY_EPSILON_J == Fraction(1, 10**9)
    assert config["scenario"]["capacity_feasibility_contract"] == (
        B3_TASK_CAPACITY_FEASIBILITY_CONTRACT_VERSION
    )
    assert len(capacity_contract_identity(config)) == 64


def test_legacy_b3_config_requires_explicit_regeneration():
    raw = yaml.safe_load(CALIBRATION.read_text(encoding="utf-8"))
    raw["scenario"].pop("capacity_feasibility_contract")
    with pytest.raises(
        ValueError,
        match="legacy B3.*capacity feasibility contract.*regenerate",
    ):
        validate_ext1b_config(raw)


def test_legacy_b3_store_is_not_silently_reused(tmp_path):
    config = load_ext1b_config(CALIBRATION)
    service = prepare_service_curve(config, tmp_path / "service")
    root = tmp_path / "store"
    TasksetStore(root, config, service)
    manifest_path = root / "pairing_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract"].pop("scenario_capacity_feasibility_contract")
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TasksetStoreError,
        match="legacy B3 taskset store.*capacity feasibility contract.*regenerate",
    ):
        TasksetStore(root, config, service)


def test_mismatched_b3_store_contract_requires_regeneration(tmp_path):
    config = load_ext1b_config(CALIBRATION)
    service = prepare_service_curve(config, tmp_path / "service")
    root = tmp_path / "store"
    TasksetStore(root, config, service)
    manifest_path = root / "pairing_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract"]["scenario_capacity_feasibility_contract"][
        "contract_identity"
    ] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TasksetStoreError,
        match="B3 taskset store capacity feasibility contract mismatch.*regenerate",
    ):
        TasksetStore(root, config, service)


def test_legacy_b3_run_config_is_not_silently_resumed(tmp_path):
    config = load_ext1b_config(CALIBRATION)
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    simulator = tmp_path / "simulator-not-to-run"
    simulator.write_bytes(b"identity only\n")
    config["simulation"]["simulator_bin"] = str(simulator)

    raw = yaml.safe_load(CALIBRATION.read_text(encoding="utf-8"))
    raw["execution"]["output_root"] = str(tmp_path / "run")
    raw["execution"]["taskset_store"] = str(tmp_path / "store")
    raw["simulation"]["simulator_bin"] = str(simulator)
    raw["scenario"].pop("capacity_feasibility_contract")
    (tmp_path / "run").mkdir()
    (tmp_path / "run/run_config.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="legacy B3.*capacity feasibility contract.*regenerate",
    ):
        Ext1BRunner(config).run(resume=True)


def test_valid_existing_taskset_keeps_workload_and_actual_power(tmp_path):
    config = load_ext1b_config(CALIBRATION)
    base_cell = expand_cells(config)[0]
    service = prepare_service_curve(config, tmp_path / "service")
    stored = TasksetStore(tmp_path / "store", config, service).get_or_create(
        base_cell, 0,
    )
    before = tuple((row["workload"], row["P"]) for row in stored.task_payload)
    available_cell = next(
        cell for cell in scenario_cells(config)
        if cell.subtype == "POSITIVE_SLACK_ENERGY_AVAILABLE"
    )
    instance = build_scenario_instance(
        stored,
        config,
        available_cell,
        logical_taskset_index=0,
        attempt_index=0,
        system_root=tmp_path / "systems",
    )
    after = tuple((row["workload"], row["P"]) for row in instance.tasks)
    assert after == before
    assert not capacity_feasibility_violations(
        instance.tasks, instance.battery_capacity, config,
    )


def test_retry_rejects_infeasible_candidate_and_emits_complete_pair(
    tmp_path, monkeypatch,
):
    config = deepcopy(load_ext1b_config(CALIBRATION))
    config["execution"]["output_root"] = str(tmp_path / "plan")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    simulator = tmp_path / "simulator-not-to-run"
    simulator.write_bytes(b"identity only\n")
    config["simulation"]["simulator_bin"] = str(simulator)

    from experiments.v9_3 import ext1b_engine

    real_build = ext1b_engine.build_scenario_instance

    def first_candidate_infeasible(*args, **kwargs):
        instance = real_build(*args, **kwargs)
        if kwargs["attempt_index"] == 0:
            return replace(instance, battery_capacity=Fraction(0))
        return instance

    monkeypatch.setattr(ext1b_engine, "build_scenario_instance", first_candidate_infeasible)
    monkeypatch.setattr(
        ext1b_engine,
        "run_paired_simulation",
        lambda *_args, **_kwargs: pytest.fail("plan-only invoked simulator"),
    )

    outcome = Ext1BRunner(config).materialize_plan(
        max_cells=1, max_tasksets=1,
    )
    with (tmp_path / "plan/generation_attempts.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        attempts = list(csv.DictReader(handle))
    with (tmp_path / "plan/simulation_requests.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        requests = list(csv.DictReader(handle))
    assert [row["rejection_code"] for row in attempts] == [
        CAPACITY_FEASIBILITY_ERROR_CODE,
        "",
    ]
    rejected = attempts[0]
    for field in (
        "experiment_id", "scenario_cell_id", "paired_instance_id",
        "logical_taskset_index", "source_taskset_index", "attempt_index",
        "task_name", "workload", "actual_power", "tick_duration",
        "task_tick_energy_mJ", "battery_capacity_mJ",
        "native_affordability_epsilon_mJ", "excess_energy_mJ",
        "power_model_identity", "workload_contract_version",
    ):
        assert field in rejected
    assert rejected["experiment_id"] == config["experiment_id"]
    assert rejected["paired_instance_id"] == ""
    assert len(requests) == 3
    assert len({row["paired_instance_id"] for row in requests}) == 1
    assert [row["scheduler_id"] for row in requests] == config["scheduler_ids"]
    identity = capacity_contract_identity(config)
    assert {row["capacity_feasibility_contract_identity"] for row in requests} == {
        identity
    }
    for row in requests:
        assert row["request_id"] == domain_hash(
            "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v2",
            {
                "paired_instance_id": row["paired_instance_id"],
                "scheduler_id": row["scheduler_id"],
                "capacity_feasibility_contract_identity": identity,
            },
        )
    with (tmp_path / "plan/generated_tasksets.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        generated = list(csv.DictReader(handle))
    with (tmp_path / "plan/scenario_instances.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        instances = list(csv.DictReader(handle))
    assert len(generated) == len(instances) == 1
    assert generated[0]["capacity_feasibility_contract_identity"] == identity
    assert instances[0]["capacity_feasibility_contract_identity"] == identity
    assert generated[0]["scenario_candidate_identity"]
    assert instances[0]["scenario_candidate_identity"] == generated[0][
        "scenario_candidate_identity"
    ]
    assert outcome.summary["simulator_invoked"] is False
    assert outcome.summary["capacity_infeasible_task_count"] == 0
    assert outcome.summary["capacity_infeasible_taskset_count"] == 0
    assert outcome.summary["capacity_feasibility_rejection_count"] == 1


def test_full_run_capacity_preflight_fails_before_simulator(
    tmp_path, monkeypatch,
):
    config = deepcopy(load_ext1b_config(CALIBRATION))
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    simulator = tmp_path / "simulator-must-not-run"
    simulator.write_bytes(b"identity only\n")
    config["simulation"]["simulator_bin"] = str(simulator)
    config["scenario"]["structural_retry_limit"] = 1

    from experiments.v9_3 import ext1b_engine

    real_build = ext1b_engine.build_scenario_instance
    invoked = False

    def infeasible(*args, **kwargs):
        return replace(real_build(*args, **kwargs), battery_capacity=Fraction(0))

    def forbidden(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        pytest.fail("capacity-infeasible input reached simulator")

    monkeypatch.setattr(ext1b_engine, "build_scenario_instance", infeasible)
    monkeypatch.setattr(ext1b_engine, "run_paired_simulation", forbidden)
    with pytest.raises(RuntimeError, match="structural retry limit exhausted"):
        Ext1BRunner(config).run(max_cells=1, max_tasksets=1)
    assert invoked is False
    attempts = (tmp_path / "run/generation_attempts.csv").read_text(
        encoding="utf-8"
    )
    assert CAPACITY_FEASIBILITY_ERROR_CODE in attempts
