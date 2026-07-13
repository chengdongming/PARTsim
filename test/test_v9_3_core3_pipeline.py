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
    assert checkpoint["requested_rta_count"] == 2
    assert checkpoint["requested_simulation_count"] == 1
    assert summary["soundness_evaluable_denominator"] == 2
    assert summary["tightness_common_task_denominator"] == 2
