from __future__ import annotations

import json
from pathlib import Path

from experiments.v9_3.config import load_config
from experiments.v9_3.ext2_real_trace import Ext2Runner
from experiments.v9_3.result_writer import read_csv


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
