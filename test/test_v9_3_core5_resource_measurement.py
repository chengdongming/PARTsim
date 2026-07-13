from __future__ import annotations

import json

from experiments.v9_3.resource_measurement import (
    RESOURCE_OBSERVATION_COLUMNS, materialize_resource_usage,
)
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, write_csv,
)


def test_resource_fields_and_unavailable_peak_are_explicit(tmp_path):
    write_csv(tmp_path / "scalability_cells.csv", (
        "scalability_cell_id", "scaling_axis", "level_id", "worker_count", "analysis_ids_json"
    ), [{
        "scalability_cell_id": "c", "scaling_axis": "task_count",
        "level_id": "6", "worker_count": 1, "analysis_ids_json": json.dumps(["a"]),
    }])
    write_csv(tmp_path / "analysis_attempts.csv", ATTEMPT_COLUMNS, [{
        "attempt_id": "x", "analysis_id": "a", "attempt_number": 1,
        "solver_wall_seconds": 1, "solver_cpu_seconds": .5,
        "total_wall_seconds": 1.2, "worker_startup_seconds": .1,
        "serialization_seconds": .01, "ipc_seconds": .09,
        "timeout_budget_seconds": 2, "solver_status": "COMPLETED", "outer_timeout": False,
    }])
    write_csv(tmp_path / "attempt_resource_observations.csv", RESOURCE_OBSERVATION_COLUMNS, [{
        "attempt_id": "x", "analysis_id": "a", "peak_rss_kib": "UNAVAILABLE",
        "peak_rss_scope": "UNAVAILABLE", "peak_rss_unit": "UNAVAILABLE",
    }])
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, [{
        "analysis_id": "a", "analysis_variant": "CW_THETA_CW",
        "final_attempt_id": "x", "n_tasks_candidate_found": 1,
        "first_failed_priority": "", "solver_status": "COMPLETED",
    }])
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, [{
        "analysis_id": "a", "task_id": "0", "checked_w_count": 2,
        "checked_h_count": 3, "checked_q_count": 4, "envelope_call_count": 5,
    }])
    rows = materialize_resource_usage(tmp_path)
    assert rows[0]["peak_rss"] == "UNAVAILABLE"
    assert rows[0]["deserialization_seconds"] == "UNAVAILABLE"
    assert rows[0]["fixed_point_iterations"] == "UNAVAILABLE"
    assert rows[0]["checked_w_count"] == 2
