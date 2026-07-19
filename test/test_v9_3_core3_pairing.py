from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import config_hash, load_config
from experiments.v9_3.core3_pairing import (
    CORE3_ARTIFACT_CONTRACT_VERSION,
    CORE3_CHECKPOINT_SCHEMA,
    CORE3_CHECKPOINT_SCHEMA_VERSION,
    Core3PairingRunner,
    _required_artifact_bool,
    _response_bound_eligibility,
    analyze_core3,
    deadline_soundness_violation,
)
from experiments.v9_3.execution_engine import (
    ExecutionEngine,
    ExecutionError,
    RunOutcome,
)
from experiments.v9_3.result_writer import (
    FAILURE_COLUMNS,
    TASK_RESULT_COLUMNS,
    read_csv,
    write_csv,
)
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    SimulationExecution,
    materialize_simulation_inputs,
    simulation_identity,
    validate_no_overflow_guard,
)
from experiments.v9_3.simulation_result import (
    JobObservation,
    SimulationResult,
    SimulationStatus,
    TaskObservation,
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


def comparison_execution(
    *, e0: bool, attempts: int = 1, completed: bool = True,
) -> SimulationExecution:
    result = SimulationResult(
        SimulationStatus.DEADLINE_MISS, "deadline_miss", 10, (), (), e0,
        1.0 if e0 else 0.0, {}, 2, "gpfp_asap_block", completed,
        "reached_horizon" if completed else "incomplete",
    )
    return SimulationExecution(
        "simulation", result, 0.1, attempts, (10,), Path("system"),
        Path("taskset"), None,
    )


def certified_rta_row() -> dict:
    return {
        "solver_status": "COMPLETED",
        "certification_status": "CERTIFIED_TASKSET",
        "taskset_proven": True,
    }


def test_invalid_e0_certified_deadline_miss_does_not_trigger_p0():
    assert not deadline_soundness_violation(
        [certified_rta_row()], comparison_execution(e0=False)
    )


def test_valid_e0_certified_deadline_miss_triggers_p0():
    assert deadline_soundness_violation(
        [certified_rta_row()], comparison_execution(e0=True)
    )


@pytest.mark.parametrize("execution", [
    comparison_execution(e0=True, attempts=0),
    comparison_execution(e0=True, completed=False),
])
def test_deadline_p0_requires_no_overflow_and_observation_eligibility(execution):
    assert not deadline_soundness_violation([certified_rta_row()], execution)


def checkpoint_payload(runner: Core3PairingRunner) -> dict:
    return {
        "core3_artifact_contract_version": CORE3_ARTIFACT_CONTRACT_VERSION,
        "schema": CORE3_CHECKPOINT_SCHEMA,
        "schema_version": CORE3_CHECKPOINT_SCHEMA_VERSION,
        "core": "CORE-3",
        "config_hash": runner.config_identity,
    }


def write_checkpoint(runner: Core3PairingRunner, payload: dict) -> None:
    runner.root.mkdir(parents=True, exist_ok=True)
    (runner.root / "checkpoint.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_old_core3_comparison_artifact_without_checkpoint_is_rejected(tmp_path):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    (tmp_path / "soundness_matrix.csv").write_text("legacy\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="without checkpoint.json"):
        runner._validate_existing_core3_contract()


@pytest.mark.parametrize("contract_value", [None, 1])
def test_checkpoint_only_old_or_missing_contract_rejects_resume_and_analyzer(
    tmp_path, contract_value,
):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    payload = checkpoint_payload(runner)
    if contract_value is None:
        del payload["core3_artifact_contract_version"]
    else:
        payload["core3_artifact_contract_version"] = contract_value
    write_checkpoint(runner, payload)
    with pytest.raises(RuntimeError, match="artifact contract version"):
        runner.run(resume=True)
    with pytest.raises(RuntimeError, match="artifact contract version"):
        analyze_core3(config)


@pytest.mark.parametrize(("updates", "message"), [
    ({"schema": "legacy"}, "checkpoint schema mismatch"),
    ({"schema_version": True}, "schema_version"),
    ({"schema_version": 1}, "schema_version"),
    ({"core": "CORE-2"}, "checkpoint core mismatch"),
    ({"config_hash": "wrong"}, "configuration hash mismatch"),
])
def test_checkpoint_v2_validates_schema_core_and_config(
    tmp_path, updates, message,
):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    write_checkpoint(runner, {**checkpoint_payload(runner), **updates})
    with pytest.raises(RuntimeError, match=message):
        runner._validate_existing_core3_contract()


def test_checkpoint_only_v2_allows_resume_and_analyzer_downstream_validation(
    tmp_path, monkeypatch,
):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    write_checkpoint(runner, checkpoint_payload(runner))
    runner._validate_existing_core3_contract()

    class DownstreamReached(RuntimeError):
        pass

    def downstream_run(*_args, **_kwargs):
        raise DownstreamReached("resume downstream reached")

    monkeypatch.setattr(ExecutionEngine, "run", downstream_run)
    with pytest.raises(DownstreamReached, match="resume downstream reached"):
        runner.run(resume=True)

    def downstream_plan(_runner):
        raise DownstreamReached("analyzer downstream reached")

    monkeypatch.setattr(Core3PairingRunner, "_simulation_plan", downstream_plan)
    with pytest.raises(DownstreamReached, match="analyzer downstream reached"):
        analyze_core3(config)


def test_fresh_root_without_checkpoint_allows_new_run_validation(
    tmp_path, monkeypatch,
):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    runner._validate_existing_core3_contract()

    class DownstreamReached(RuntimeError):
        pass

    def downstream_run(*_args, **_kwargs):
        raise DownstreamReached("fresh downstream reached")

    monkeypatch.setattr(ExecutionEngine, "run", downstream_run)
    with pytest.raises(DownstreamReached, match="fresh downstream reached"):
        runner.run(resume=False)


@pytest.mark.parametrize("row", [
    {},
    {"gate": ""},
    {"gate": "true"},
    {"gate": 1},
])
def test_artifact_boolean_missing_empty_or_invalid_fails_closed(row):
    with pytest.raises(RuntimeError, match="gate"):
        _required_artifact_bool(row, "gate")


@pytest.mark.parametrize(("encoded", "expected"), [
    (True, True), (False, False), ("True", True), ("False", False),
])
def test_artifact_boolean_accepts_only_contract_encodings(encoded, expected):
    assert _required_artifact_bool({"gate": encoded}, "gate") is expected


def test_response_bound_violation_triggers_runtime_p0_fail_fast(
    tmp_path, monkeypatch,
):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    write_csv(tmp_path / "failures.csv", FAILURE_COLUMNS, [])
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, [{
        "analysis_id": "analysis", "cell_id": "cell",
        "taskset_id": "taskset", "exact_e0": "1",
        "analysis_variant": "CW_THETA_CW", "task_id": "0",
        "priority_rank": 0, "C": 2, "D": 10, "T": 20, "P": "1/10",
        "task_solver_status": "CANDIDATE_FOUND",
        "candidate_response_time": 8,
    }])
    result = SimulationResult(
        SimulationStatus.PASS_OBSERVED, "minimum_jobs_observed", 20,
        (JobObservation(
            "0", 0, 0, 9, 10, 9, False, 0, 0, 0, 0, 2,
            True, False, None,
        ),),
        (TaskObservation("0", 1, 1, 0, 0, 9, 1.0, True),),
        True, 1.0, {"0": 0.1}, 2, "gpfp_asap_block", True,
        "reached_horizon",
    )
    execution = SimulationExecution(
        "simulation", result, 0.1, 1, (20,), Path("system"),
        Path("taskset"), None,
    )
    monkeypatch.setattr(
        "experiments.v9_3.core3_pairing.run_paired_simulation",
        lambda **_kwargs: execution,
    )
    plan = [{
        "simulation_id": "simulation", "cell_id": "cell",
        "cell": {"M": 4}, "taskset_id": "taskset",
        "taskset_hash": "a" * 64, "exact_e0": Fraction(1),
        "generated": {}, "canonical": Path("canonical"),
        "document": {"tasks": []},
        "rta_rows": [certified_rta_row()],
    }]
    outcome = RunOutcome(tmp_path, 1, 1, {"COMPLETED": 1}, False)
    executions = runner._run_simulations(outcome, plan, resume=False)
    assert set(executions) == {"simulation"}
    assert runner.stop_requested
    assert any(
        row["severity"] == "P0"
        and row["code"] == "RTA_RESPONSE_BOUND_VIOLATION"
        for row in read_csv(tmp_path / "failures.csv")
    )


def test_right_censored_task_does_not_create_response_bound_violation(tmp_path):
    config = core3_config()
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    result = SimulationResult(
        SimulationStatus.PASS_OBSERVED, "minimum_jobs_observed", 20, (),
        (TaskObservation("0", 2, 1, 0, 1, 9, 0.5, True),),
        True, 1.0, {"0": 0.1}, 2, "gpfp_asap_block", True,
        "reached_horizon",
    )
    execution = SimulationExecution(
        "simulation", result, 0.1, 1, (20,), Path("system"),
        Path("taskset"), None,
    )
    task_row = {
        "analysis_id": "analysis", "cell_id": "cell",
        "taskset_id": "taskset", "exact_e0": "1",
        "analysis_variant": "CW_THETA_CW", "task_id": "0",
        "priority_rank": 0, "C": 2, "D": 10, "T": 20, "P": "1/10",
        "task_solver_status": "CANDIDATE_FOUND",
        "candidate_response_time": 8,
    }
    eligibility = _response_bound_eligibility(
        execution, result.tasks[0], task_row
    )
    assert not eligibility.eligible
    assert eligibility.reason == "RIGHT_CENSORED"
    assert runner._response_bound_violations({
        "simulation_id": "simulation", "cell_id": "cell",
        "taskset_id": "taskset", "taskset_hash": "a" * 64,
    }, execution, [task_row]) == []
