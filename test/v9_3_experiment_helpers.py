from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset

from experiments.v9_3.config import validate_config
from experiments.v9_3.execution_engine import AttemptExecution
from experiments.v9_3.taskset_store import ServiceCurveMaterial, StoredTaskset


def make_config(tmp_path: Path, core_name: str = "CORE-1", *, e0=None):
    variants = (
        ["CW_THETA_CW", "LOC_THETA_LOC"] if core_name == "CORE-1" else
        ["CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"]
    )
    return validate_config({
        "experiment_id": f"test-{core_name.lower()}",
        "core": core_name,
        "platform": {"cores": [2], "task_count": [2]},
        "generation": {
            "deadline_mode": "implicit",
            "constrained_deadline": {
                "d_over_t_values": [], "d_over_t_min": "0", "d_over_t_max": "1",
                "distribution": "generator_uniform_integer",
            },
            "period_min": 5, "period_max": 10, "wcet_rounding": "compensated",
            "utilization_tolerance": "0.1", "min_task_util": "0.01",
            "max_task_util": "0.8", "priority_policy": "RM",
            "power_mode": "generator_default_heterogeneous",
            "generator_timeout_seconds": 10,
        },
        "energy": {
            "initial_energy_values": e0 or ["100"],
            "exact_rational_encoding": "canonical_fraction",
            "service_curve": {
                "id": "test-curve", "system_template": "unused.yml", "horizon": 20,
            },
            "battery_mode": "finite", "battery_capacity": "200",
        },
        "grid": {"utilization_points": ["0.2"], "tasksets_per_cell": 1, "base_seed": 93},
        "analysis": {
            "variants": variants, "timeout_seconds": 1,
            "retry_timeout_seconds": 2, "retry_policy": "timeout_once",
            "worker_count": 1, "numerical_mode": "EXACT_RATIONAL",
        },
        "execution": {
            "checkpoint_every": 1, "output_root": str(tmp_path / f"run-{core_name}"),
            "taskset_store": str(tmp_path / "store"), "resume": False,
            "fail_fast_on_p0": True, "preserve_attempt_history": True,
        },
        "plots": {},
    })


class FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get_or_create(self, cell, index):
        tasks = (
            core.V93Task("0", 1, 5, 5, Fraction(1)),
            core.V93Task("1", 1, 7, 7, Fraction(1)),
        )
        payload = (
            {"task_id": "0", "source_name": "a", "priority_rank": 0, "C": 1, "D": 5, "T": 5, "P": "1", "D_over_T": "1", "workload": "idle", "arrival_offset": 0},
            {"task_id": "1", "source_name": "b", "priority_rank": 1, "C": 1, "D": 7, "T": 7, "P": "1", "D_over_T": "1", "workload": "idle", "arrival_offset": 0},
        )
        semantic = f"semantic-{cell.generation_id}"
        return StoredTaskset(
            f"taskset-{cell.generation_id}-{index}", cell.generation_id, index, 123,
            semantic, "priority", "power", cell.utilization * cell.processors,
            Fraction(12, 35), cell.processors, len(tasks), cell.deadline_mode,
            tasks, payload, 0.01, "service", self.root / f"{semantic}.json",
        )


def install_fake_materialization(monkeypatch, tmp_path: Path):
    import experiments.v9_3.execution_engine as module

    service = ServiceCurveMaterial(
        tuple(Fraction(0) for _ in range(10)), "service", "{}", tmp_path / "system.yml"
    )
    store = FakeStore(tmp_path / "store")
    monkeypatch.setattr(module, "prepare_service_curve", lambda config, root: service)
    monkeypatch.setattr(module, "TasksetStore", lambda root, config, material: store)
    return service, store


def candidate_solver(**kwargs: Any) -> taskset.SingleTaskSolverResult:
    task = kwargs["task"]
    return taskset.SingleTaskSolverResult(
        taskset.TaskSolverStatus.CANDIDATE_FOUND,
        candidate_response_time=task.wcet,
        closing_w=task.wcet,
        witness_h=0,
        checked_w_count=1,
        checked_h_count=1,
        checked_q_count=1,
        envelope_call_count=1,
    )


def successful_execution(request) -> AttemptExecution:
    result = taskset.analyze_taskset_v9_3(
        request.analysis_id, request.variant, request.analysis_input,
        source=request.source,
        dependency_check_status=request.dependency_check_status,
        single_task_solver=candidate_solver,
    )
    return AttemptExecution(result, result.solver_status.value, False, .01, .005, .001, .001, .012)


def timeout_execution() -> AttemptExecution:
    return AttemptExecution(
        None, "TIMEOUT", True, 1.0, 0, .001, .001, 1.002,
        "ConfigurationTimeout", "test timeout", None,
    )
