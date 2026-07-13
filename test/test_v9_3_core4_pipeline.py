from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from experiments.v9_3.config import ConfigError, load_config, validate_config
from experiments.v9_3.core4_aggregation import aggregate_core4
from experiments.v9_3.core4_sensitivity import (
    SENSITIVITY_REQUEST_COLUMNS, Core4SensitivityRunner,
)
from experiments.v9_3.result_writer import (
    TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, write_csv,
)


ROOT = Path(__file__).resolve().parents[1]


def test_core4_config_dry_plan_and_resume_hash_guard(tmp_path):
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    runner = Core4SensitivityRunner(config)
    assert runner.describe()["cell_count"] == 8
    runner._initialize(False)
    runner._initialize(True)
    with pytest.raises(ConfigError, match="use --resume"):
        runner._initialize(False)


def test_invalid_exact_ordering_and_service_object_are_rejected():
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    bad = deepcopy(config)
    bad["sensitivity"]["axes"]["initial_energy"]["values"] = ["1", "0"]
    with pytest.raises(ConfigError, match="exactly ordered"):
        validate_config(bad, expected_core="CORE-4")
    bad = deepcopy(config)
    bad["sensitivity"]["axes"]["service_curve"]["variants"][0].pop("system_template")
    with pytest.raises(ConfigError, match="system_template"):
        validate_config(bad, expected_core="CORE-4")


def test_summary_uses_explicit_denominators_and_writes_plot_data(tmp_path):
    requests = []
    results = []
    tasks = []
    for index, candidate in enumerate((5, 4)):
        analysis = f"a{index}"
        requests.append({
            "sweep_id": "s", "base_taskset_id": "t", "base_taskset_hash": "h",
            "taskset_index": 0, "parameter_name": "initial_energy",
            "ordered_parameter_levels": '["\\\"0\\\"","\\\"1\\\""]',
            "level_index": index, "level_encoding": f'"{index}"',
            "variant": "CW_THETA_CW", "analysis_id": analysis,
            "analysis_input_hash": analysis, "availability": "AVAILABLE",
            "availability_reason": "", "service_curve_relation_to_previous": "NOT_APPLICABLE",
            "paired_analysis_ids": "[]",
        })
        results.append({
            "analysis_id": analysis, "taskset_id": "t", "taskset_hash": "h",
            "analysis_variant": "CW_THETA_CW", "solver_status": "COMPLETED",
            "taskset_proven": True, "runtime_wall_seconds": "0.1",
            "outer_timeout": False,
        })
        tasks.append({
            "analysis_id": analysis, "taskset_id": "t", "task_id": "0",
            "task_solver_status": "CANDIDATE_FOUND",
            "candidate_response_time": candidate,
        })
    write_csv(tmp_path / "sensitivity_requests.csv", SENSITIVITY_REQUEST_COLUMNS, requests)
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, tasks)
    summary = aggregate_core4(tmp_path)
    assert summary["paired_count"] == 1
    assert summary["p0_monotonicity_violation_count"] == 0
    assert (tmp_path / "core4_plot_data.csv").stat().st_size > 0
