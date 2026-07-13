from __future__ import annotations

import json
from pathlib import Path

from experiments.v9_3.config import load_config
from experiments.v9_3.ext2_real_trace import Ext2Runner
from experiments.v9_3.ext2_aggregation import aggregate_ext2
from experiments.v9_3.ext2_real_trace import REQUEST_COLUMNS, RESULT_COLUMNS
from experiments.v9_3.result_writer import read_csv, write_csv
from experiments.v9_3.simulation_engine import materialize_simulation_inputs
from fractions import Fraction
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_no_real_data_fallback_is_explicit_and_fixture_labeled(tmp_path):
    config = load_config(ROOT / "configs/v9_3_ext2_smoke.yaml", expected_core="CORE-3")
    config["execution"]["output_root"] = str(tmp_path)
    runner = Ext2Runner(config)
    assert runner.describe()["data_status"] == "REAL_TRACE_DATA_UNAVAILABLE"
    runner._initialize(resume=False)
    trace, _segment_id, system_path = runner._prepare_trace()
    assert trace.fixture_label == "SYNTHETIC_TEST_FIXTURE"
    assert system_path.is_file()
    materialized, _ = materialize_simulation_inputs(
        system_path, tmp_path / "materialized", ({
            "task_id": "0", "priority_rank": 0, "C": 1, "D": 2,
            "T": 2, "P": "1/10", "workload": "hash", "arrival_offset": 0,
        },), processors=1, initial_battery=Fraction(1),
        battery_capacity=Fraction(2),
    )
    assert yaml.safe_load(materialized.read_text(encoding="utf-8"))
    metadata = json.loads((tmp_path / "trace_metadata.json").read_text(encoding="utf-8"))
    assert metadata["data_status"] == "REAL_TRACE_DATA_UNAVAILABLE"
    assert metadata["fixture_label"] == "SYNTHETIC_TEST_FIXTURE"
    checks = read_csv(tmp_path / "energy_conservation_checks.csv")
    assert next(row for row in checks if row["operation"] == "RESAMPLE")["difference_j"] == "0"
    service = read_csv(tmp_path / "service_bound_checks.csv")[0]
    assert service["rta_status"] == "NOT_APPLICABLE_NO_CERTIFIED_SERVICE_BOUND"


def test_ext2_smoke_request_count_is_bounded_to_one():
    runner = Ext2Runner.from_path(ROOT / "configs/v9_3_ext2_smoke.yaml")
    assert runner.describe()["simulation_request_count"] == 1


def test_resume_accepts_same_config_and_rejects_unrequested_restart(tmp_path):
    config = load_config(ROOT / "configs/v9_3_ext2_smoke.yaml", expected_core="CORE-3")
    config["execution"]["output_root"] = str(tmp_path)
    runner = Ext2Runner(config)
    runner._initialize(resume=False)
    runner._initialize(resume=True)


def test_ext2_aggregation_keeps_requested_terminal_and_valid_denominators(tmp_path):
    request = {
        "request_id": "r", "cell_id": "c", "taskset_id": "t",
        "taskset_hash": "h", "trace_id": "trace", "trace_hash": "th",
        "trace_scale": "1", "segment_id": "segment",
        "scheduler_id": "gpfp_asap_block", "simulation_config_hash": "s",
        "input_hash": "i", "request_status": "PLANNED",
    }
    write_csv(tmp_path / "simulation_requests.csv", REQUEST_COLUMNS, [request, {
        **request, "request_id": "missing", "taskset_id": "t2",
    }])
    result = {
        key: "" for key in RESULT_COLUMNS
    }
    result.update({
        "request_id": "r", "cell_id": "c", "taskset_id": "t",
        "taskset_hash": "h", "trace_id": "trace", "trace_hash": "th",
        "trace_scale": "1", "segment_id": "segment",
        "scheduler_id": "gpfp_asap_block", "status": "SIM_PASS_OBSERVED",
        "comparison_eligible": True,
    })
    write_csv(tmp_path / "simulation_results.csv", RESULT_COLUMNS, [result])
    aggregate_ext2(tmp_path)
    summary = read_csv(tmp_path / "trace_summary.csv")[0]
    assert summary["requested_denominator"] == "2"
    assert summary["terminal_denominator"] == "1"
    assert summary["valid_terminal_denominator"] == "1"
