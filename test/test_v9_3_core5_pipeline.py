from __future__ import annotations

import json
from pathlib import Path

from experiments.v9_3.config import load_config
from experiments.v9_3.core5_aggregation import aggregate_core5
from experiments.v9_3.core5_scalability import (
    SCALABILITY_CELL_COLUMNS, Core5ScalabilityRunner, _cell_timing,
)
from experiments.v9_3.resource_measurement import RESOURCE_OBSERVATION_COLUMNS
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, FAILURE_COLUMNS, GENERATED_COLUMNS, REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS, read_csv, write_csv,
)


ROOT = Path(__file__).resolve().parents[1]


def test_core5_checkpoint_root_resume_guard(tmp_path):
    config = load_config(ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5")
    config["execution"]["output_root"] = str(tmp_path / "run")
    runner = Core5ScalabilityRunner(config)
    assert runner.describe()["request_count"] == 16
    runner._initialize(False)
    runner._initialize(True)


def test_core5_aggregation_writes_plot_data_and_worker_semantics(tmp_path):
    cells = []
    attempts = []
    results = []
    tasks = []
    observations = []
    requests = []
    for worker in (1, 2):
        analysis = f"a{worker}"
        cells.append({
            "scalability_cell_id": f"c{worker}", "scaling_axis": "worker_count",
            "level_index": worker - 1, "level_id": str(worker),
            "level_value": str(worker), "M": 2, "task_n": 6,
            "period_min": 40, "period_max": 200, "utilization": "1/5",
            "worker_count": worker, "variants": '["CW_THETA_CW"]',
            "tasksets_requested": 1, "analysis_ids_json": json.dumps([analysis]),
            "cell_wall_seconds": 1, "terminal_analysis_count": 1,
            "throughput_analyses_per_second": 1,
        })
        attempts.append({
            "attempt_id": f"x{worker}", "analysis_id": analysis,
            "attempt_number": 1, "timeout_budget_seconds": 2,
            "solver_status": "COMPLETED", "outer_timeout": False,
            "payload_received": True,
            "solver_wall_seconds": .5, "solver_cpu_seconds": .4,
            "worker_startup_seconds": .05, "serialization_seconds": .01,
            "ipc_seconds": .04, "total_wall_seconds": .6,
        })
        requests.append({
            "request_id": f"r{worker}", "analysis_id": analysis,
            "cell_id": f"inner{worker}", "taskset_id": "t",
            "taskset_hash": "hash", "exact_e0": "0",
            "variant": "CW_THETA_CW", "numerical_mode": "EXACT_RATIONAL",
            "timeout_seconds": 2, "retry_timeout_seconds": "",
            "source_analysis_id": "", "request_status": "TERMINAL",
        })
        results.append({
            "analysis_id": analysis, "request_id": f"r{worker}",
            "taskset_id": "t", "taskset_hash": "hash",
            "analysis_variant": "CW_THETA_CW", "solver_status": "COMPLETED",
            "certification_status": "CERTIFIED_TASKSET", "taskset_proven": True,
            "n_tasks_candidate_found": 1, "final_attempt_id": f"x{worker}",
            "timeout_budget_seconds": 2, "runtime_wall_seconds": .6,
            "outer_timeout": False,
        })
        tasks.append({
            "analysis_id": analysis, "taskset_id": "t", "task_id": "0",
            "task_solver_status": "CANDIDATE_FOUND", "candidate_response_time": 1,
            "checked_w_count": 1, "checked_h_count": 1,
            "checked_q_count": 1, "envelope_call_count": 1,
        })
        observations.append({
            "attempt_id": f"x{worker}", "analysis_id": analysis,
            "peak_rss_kib": 100 + worker, "peak_rss_scope": "CHILD_PROCESS",
            "peak_rss_unit": "KiB", "observation_status": "AVAILABLE",
            "unavailability_reason": "",
        })
    write_csv(tmp_path / "scalability_cells.csv", SCALABILITY_CELL_COLUMNS, cells)
    write_csv(tmp_path / "generated_tasksets.csv", GENERATED_COLUMNS, [{
        "generation_id": "g", "taskset_id": "t", "taskset_index": 0,
        "generation_seed": 1, "M": 2, "task_n": 6,
        "target_total_utilization": "1/5", "deadline_mode": "constrained",
        "d_over_t_values_json": "[]", "taskset_hash": "hash",
        "priority_hash": "priority", "power_hash": "power",
        "service_curve_reference": "service", "task_input_json": "[]",
    }])
    write_csv(tmp_path / "analysis_requests.csv", REQUEST_COLUMNS, requests)
    write_csv(tmp_path / "analysis_attempts.csv", ATTEMPT_COLUMNS, attempts)
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, tasks)
    write_csv(tmp_path / "attempt_resource_observations.csv", RESOURCE_OBSERVATION_COLUMNS, observations)
    write_csv(tmp_path / "failures.csv", FAILURE_COLUMNS, [])
    summary = aggregate_core5(tmp_path)
    assert summary["worker_semantic_check_count"] == 1
    assert summary["worker_semantic_mismatch_count"] == 0
    assert read_csv(tmp_path / "core5_plot_data.csv")
    assert read_csv(tmp_path / "runtime_censoring.csv")[0]["event_observed"] == "True"


def test_resume_does_not_replace_first_run_cell_timing():
    prior = {
        "terminal_analysis_count": "2",
        "cell_wall_seconds": "12.500000000",
        "throughput_analyses_per_second": "0.16",
    }
    assert _cell_timing(prior, 2, 0.01) == ("12.500000000", "0.16")
    wall, throughput = _cell_timing(prior, 3, 2.0)
    assert wall == "2.000000000"
    assert throughput == 1.5
