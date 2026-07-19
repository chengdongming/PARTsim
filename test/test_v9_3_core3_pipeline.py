from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3.config import load_config
from experiments.v9_3.core3_pairing import Core3PairingRunner
from experiments.v9_3.execution_engine import RunOutcome
from experiments.v9_3.result_writer import (
    FAILURE_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS,
    read_csv,
    write_csv,
)
from experiments.v9_3.simulation_engine import (
    SimulationConfigurationError,
    SimulationExecution,
    load_simulation_terminal,
    run_paired_simulation,
    simulation_result_from_dict,
    simulation_result_to_dict,
    write_simulation_terminal,
)
from experiments.v9_3.simulation_result import (
    JobObservation,
    SimulationResult,
    SimulationStatus,
    TaskObservation,
)
from v9_3_core3_helpers import task_payload


ROOT = Path(__file__).resolve().parents[1]


def simulation_config(simulator: Path) -> dict:
    return {
        "horizon": 10, "warmup": 0, "minimum_jobs_per_task": 1,
        "maximum_horizon": 10, "horizon_extension_policy": "none",
        "deadline_miss_fail_fast": True, "timeout_seconds": 1,
        "trace_mode": "semantic", "trace_on_failure": True,
        "simulator_bin": str(simulator),
    }


def test_simulation_timeout_is_a_distinct_terminal_status(tmp_path, monkeypatch):
    simulator = tmp_path / "rtsim"
    simulator.write_text("", encoding="utf-8")
    system = tmp_path / "system.yaml"
    taskset = tmp_path / "taskset.yaml"
    system.write_text("x", encoding="utf-8")
    taskset.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.materialize_simulation_inputs",
        lambda *args, **kwargs: (system, taskset),
    )
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.validate_no_overflow_guard",
        lambda *args, **kwargs: Fraction(0),
    )
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("rtsim", 1)
        ),
    )
    execution = run_paired_simulation(
        simulation_id_value="s" * 64, base_system_path=system,
        run_root=tmp_path / "run", task_payload=task_payload(),
        taskset_hash="a" * 64, processors=4, exact_e0=Fraction(1),
        energy_config={"simulation_initial_battery": "20", "battery_capacity": "100"},
        simulation_config=simulation_config(simulator),
    )
    assert execution.result.status is SimulationStatus.RUNTIME_TIMEOUT
    assert execution.attempt_count == 1


def test_trace_on_failure_retains_semantic_parse_error_only(tmp_path, monkeypatch):
    simulator = tmp_path / "rtsim"
    simulator.write_text("", encoding="utf-8")
    system = tmp_path / "system.yaml"
    taskset = tmp_path / "taskset.yaml"
    system.write_text("x", encoding="utf-8")
    taskset.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.materialize_simulation_inputs",
        lambda *args, **kwargs: (system, taskset),
    )
    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.validate_no_overflow_guard",
        lambda *args, **kwargs: Fraction(0),
    )

    def fake_run(command, **kwargs):
        trace = Path(command[command.index("-t") + 1])
        trace.write_text("{not-json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "experiments.v9_3.simulation_engine.subprocess.run", fake_run
    )
    execution = run_paired_simulation(
        simulation_id_value="e" * 64, base_system_path=system,
        run_root=tmp_path / "run", task_payload=task_payload(),
        taskset_hash="a" * 64, processors=4, exact_e0=Fraction(1),
        energy_config={"simulation_initial_battery": "20", "battery_capacity": "100"},
        simulation_config=simulation_config(simulator),
    )
    assert execution.result.status is SimulationStatus.INTERNAL_ERROR
    assert execution.retained_trace_path is not None
    assert execution.retained_trace_path.is_file()


def test_simulation_terminal_resume_is_idempotent_and_conflicts_fail(tmp_path):
    result = SimulationResult(
        SimulationStatus.RUNTIME_TIMEOUT, "timeout", 10, (), (), False, None,
        {}, 2, "gpfp_asap_block", False, "timeout",
    )
    execution = SimulationExecution(
        "id", result, 1.0, 1, (10,), Path("system"), Path("task"), None,
    )
    terminal = tmp_path / "terminal.json"
    write_simulation_terminal(terminal, execution)
    write_simulation_terminal(terminal, execution)
    assert load_simulation_terminal(terminal) == execution
    conflict = SimulationExecution(
        "id", result, 2.0, 1, (10,), Path("system"), Path("task"), None,
    )
    with pytest.raises(SimulationConfigurationError, match="conflicting duplicate"):
        write_simulation_terminal(terminal, conflict)


def valid_serialized_simulation_result() -> dict:
    return simulation_result_to_dict(SimulationResult(
        SimulationStatus.RUNTIME_TIMEOUT, "timeout", 10, (), (), False,
        None, {}, 2, "gpfp_asap_block", False, "timeout",
    ))


def test_terminal_result_rejects_missing_trace_schema_version():
    value = valid_serialized_simulation_result()
    del value["trace_schema_version"]
    with pytest.raises(
        SimulationConfigurationError,
        match=r"trace_schema_version.*actual='<missing>'",
    ):
        simulation_result_from_dict(value)


@pytest.mark.parametrize("actual", [None, "2", True, 1, 3])
def test_terminal_result_rejects_invalid_trace_schema_version(actual):
    value = valid_serialized_simulation_result()
    value["trace_schema_version"] = actual
    with pytest.raises(
        SimulationConfigurationError,
        match=r"trace_schema_version.*actual=",
    ):
        simulation_result_from_dict(value)


def test_terminal_result_accepts_exact_integer_trace_schema_version_two():
    value = valid_serialized_simulation_result()
    assert value["trace_schema_version"] == 2
    result = simulation_result_from_dict(value)
    assert result.trace_schema_version == 2


def test_terminal_result_schema_version_round_trip_stays_explicit():
    value = valid_serialized_simulation_result()
    restored = simulation_result_from_dict(value)
    serialized = simulation_result_to_dict(restored)
    assert "trace_schema_version" in serialized
    assert serialized["trace_schema_version"] == 2


def test_pipeline_materializes_soundness_tightness_checkpoint_and_plot_data(tmp_path):
    config = load_config(ROOT / "configs/v9_3_core3_smoke.yaml", expected_core="CORE-3")
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    write_csv(tmp_path / "failures.csv", FAILURE_COLUMNS, [])
    rta_rows = []
    task_rows = []
    for index, method in enumerate(("CW_THETA_CW", "LOC_THETA_LOC")):
        analysis_id = f"analysis-{index}"
        rta_rows.append({
            "analysis_id": analysis_id, "cell_id": "cell", "taskset_id": "taskset",
            "taskset_hash": "a" * 64, "exact_e0": "1",
            "analysis_variant": method, "solver_status": "COMPLETED",
            "certification_status": "CERTIFIED_TASKSET", "taskset_proven": True,
            "runtime_wall_seconds": "0.1", "attempt_count": 1,
        })
        task_rows.append({
            "analysis_id": analysis_id, "cell_id": "cell", "taskset_id": "taskset",
            "exact_e0": "1", "analysis_variant": method, "task_id": "0",
            "priority_rank": 0, "D": 5, "task_solver_status": "CANDIDATE_FOUND",
            "candidate_response_time": 3,
        })
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, rta_rows)
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, task_rows)
    result = SimulationResult(
        SimulationStatus.PASS_OBSERVED, "minimum_jobs_observed", 10,
        (JobObservation(
            "0", 0, 0, 2, 5, 2, False, 0, 0, 0, 0, 2,
            True, False, None,
        ),),
        (TaskObservation("0", 1, 1, 0, 0, 2, 1.0, True),),
        True, 20.0, {"0": 0.1}, 2, "gpfp_asap_block", True,
        "reached_horizon",
    )
    execution = SimulationExecution(
        "simulation", result, 0.2, 1, (10,), Path("system"), Path("task"), None,
    )
    plan = [{
        "simulation_id": "simulation", "cell_id": "cell",
        "cell": {"M": "4"}, "taskset_id": "taskset",
        "taskset_hash": "a" * 64, "exact_e0": Fraction(1),
        "generated": {"service_curve_reference": "service"},
    }]
    outcome = RunOutcome(tmp_path, 2, 2, {"COMPLETED": 2}, False)
    runner._checkpoint(outcome, plan)
    summary = runner._materialize(outcome, plan, {"simulation": execution})
    assert len(read_csv(tmp_path / "soundness_matrix.csv")) == 2
    assert len(read_csv(tmp_path / "tightness_by_task.csv")) == 2
    assert read_csv(tmp_path / "core3_plot_data.csv")
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["core3_artifact_contract_version"] == 2
    assert checkpoint["schema"] == "ASAP_BLOCK_V9_3_CORE3_CHECKPOINT"
    assert checkpoint["schema_version"] == 2
    assert checkpoint["core"] == "CORE-3"
    assert checkpoint["requested_rta_count"] == 2
    assert checkpoint["requested_simulation_count"] == 1
    assert summary["soundness_evaluable_denominator"] == 2
    assert summary["tightness_common_task_denominator"] == 2


def materialize_soundness_case(
    tmp_path: Path,
    *,
    status: SimulationStatus,
    release_e0_valid: bool,
    candidate: int = 8,
    r_sim_max: int = 9,
    legacy_soundness_failure: bool = False,
):
    config = load_config(
        ROOT / "configs/v9_3_core3_smoke.yaml", expected_core="CORE-3"
    )
    config["execution"]["output_root"] = str(tmp_path)
    runner = Core3PairingRunner(config)
    write_csv(tmp_path / "failures.csv", FAILURE_COLUMNS, ([{
        "severity": "P0", "stage": "SIMULATION",
        "analysis_id": "simulation", "cell_id": "cell",
        "taskset_id": "taskset", "variant": "ASAP_BLOCK_SIMULATION",
        "code": "RTA_PASS_SIM_FAIL", "detail": "legacy misclassification",
        "traceback": "", "failure_input": "legacy",
    }] if legacy_soundness_failure else []))
    rta_rows = []
    task_rows = []
    for index, method in enumerate(("CW_THETA_CW", "LOC_THETA_LOC")):
        analysis_id = f"analysis-{index}"
        rta_rows.append({
            "analysis_id": analysis_id, "cell_id": "cell",
            "taskset_id": "taskset", "taskset_hash": "a" * 64,
            "exact_e0": "1", "analysis_variant": method,
            "solver_status": "COMPLETED",
            "certification_status": "CERTIFIED_TASKSET",
            "taskset_proven": True, "runtime_wall_seconds": "0.1",
            "attempt_count": 1,
        })
        task_rows.append({
            "analysis_id": analysis_id, "cell_id": "cell",
            "taskset_id": "taskset", "exact_e0": "1",
            "analysis_variant": method, "task_id": "0",
            "priority_rank": 0, "C": 2, "D": 10, "T": 20,
            "P": "1/10", "task_solver_status": "CANDIDATE_FOUND",
            "candidate_response_time": candidate,
        })
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, rta_rows)
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, task_rows)
    deadline_miss = status is SimulationStatus.DEADLINE_MISS
    jobs = [JobObservation(
        "0", 0, 0, r_sim_max, 10, r_sim_max, False, 0, 0, 0, 0,
        2, True, False, None,
    )]
    if deadline_miss:
        jobs.append(JobObservation(
            "0", 1, 20, None, 30, None, True, 20, 0, 0, None,
            1, True, False, None,
        ))
    result = SimulationResult(
        status,
        "deadline_miss" if deadline_miss else "minimum_jobs_observed",
        40,
        tuple(jobs),
        (TaskObservation(
            "0", len(jobs), 1, int(deadline_miss), 0, r_sim_max,
            1 / len(jobs), True,
        ),),
        release_e0_valid,
        1.0 if release_e0_valid else 0.5,
        {"0": 0.1},
        2,
        "gpfp_asap_block",
        True,
        "reached_horizon",
    )
    execution = SimulationExecution(
        "simulation", result, 0.2, 1, (40,), Path("system"),
        Path("task"), None,
    )
    plan = [{
        "simulation_id": "simulation", "cell_id": "cell",
        "cell": {"M": "4"}, "taskset_id": "taskset",
        "taskset_hash": "a" * 64, "exact_e0": Fraction(1),
        "generated": {"service_curve_reference": "service"},
        "rta_rows": rta_rows,
    }]
    outcome = RunOutcome(tmp_path, 2, 2, {"COMPLETED": 2}, False)
    return runner._materialize(outcome, plan, {"simulation": execution})


def test_invalid_e0_deadline_miss_preserves_raw_status_but_excludes_soundness(tmp_path):
    summary = materialize_soundness_case(
        tmp_path,
        status=SimulationStatus.DEADLINE_MISS,
        release_e0_valid=False,
        legacy_soundness_failure=True,
    )
    soundness = read_csv(tmp_path / "soundness_matrix.csv")
    assert {row["simulation_status"] for row in soundness} == {
        SimulationStatus.DEADLINE_MISS.value
    }
    assert {row["soundness_class"] for row in soundness} == {
        "ASSUMPTION_E0_NOT_SATISFIED"
    }
    assert not any(row["p0_violation_candidate"] == "True" for row in soundness)
    assert summary["soundness_raw_evaluable_denominator"] == 2
    assert summary["soundness_evaluable_denominator"] == 0
    assert summary["assumption_e0_not_satisfied_count"] == 2
    assert summary["deadline_miss_soundness_violation_count"] == 0
    assert summary["response_bound_violation_task_count"] == 0
    assert summary["total_unique_soundness_counterexample_taskset_count"] == 0
    assert not any(
        row["code"] == "RTA_PASS_SIM_FAIL"
        for row in read_csv(tmp_path / "failures.csv")
    )


def test_response_bound_violation_without_deadline_miss_is_p0(tmp_path):
    summary = materialize_soundness_case(
        tmp_path,
        status=SimulationStatus.PASS_OBSERVED,
        release_e0_valid=True,
    )
    violations = read_csv(tmp_path / "response_bound_violations.csv")
    assert len(violations) == 2
    assert {row["code"] for row in violations} == {
        "RTA_RESPONSE_BOUND_VIOLATION"
    }
    assert {row["absolute_gap"] for row in violations} == {"-1"}
    assert all(Path(row["observation_trace_path"]).is_file() for row in violations)
    assert summary["deadline_miss_soundness_violation_count"] == 0
    assert summary["response_bound_violation_task_count"] == 1
    assert summary["response_bound_violation_comparison_count"] == 2
    assert summary["response_bound_violation_taskset_count"] == 1
    assert summary["total_unique_soundness_counterexample_taskset_count"] == 1
    assert any(
        row["code"] == "RTA_RESPONSE_BOUND_VIOLATION"
        and row["severity"] == "P0"
        for row in read_csv(tmp_path / "failures.csv")
    )


def test_deadline_and_response_bound_share_one_unique_counterexample_taskset(tmp_path):
    summary = materialize_soundness_case(
        tmp_path,
        status=SimulationStatus.DEADLINE_MISS,
        release_e0_valid=True,
    )
    assert summary["deadline_miss_soundness_violation_count"] == 1
    assert summary["response_bound_violation_task_count"] == 1
    assert summary["response_bound_violation_comparison_count"] == 2
    assert summary["response_bound_violation_taskset_count"] == 1
    assert summary["total_unique_soundness_counterexample_taskset_count"] == 1
    assert {row["code"] for row in read_csv(tmp_path / "failures.csv")} == {
        "RTA_PASS_SIM_FAIL",
        "RTA_RESPONSE_BOUND_VIOLATION",
    }
