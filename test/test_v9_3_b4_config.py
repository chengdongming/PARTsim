import subprocess
import sys

import pytest

from experiments.v9_3.config import ConfigError
from experiments.v9_3.performance_config import assert_execution_seals, normalize_performance_config, plan_counts
from v9_3_b4_helpers import PROJECT_ROOT, config


def test_four_frozen_plan_counts():
    assert plan_counts(config("v9_3_b4_calibration_r1.yaml"))["requests"] == 6750
    assert plan_counts(config("v9_3_b4_calibration_r1.yaml"))["confirmation_requests"] == 1350
    assert plan_counts(config("v9_3_b4_horizon_gate_r1.yaml"))["requests"] == 4000
    assert plan_counts(config("v9_3_b4_formal_template_r1.yaml"))["formal_requests"] == 43200


def test_unknown_key_and_binary_float_fail_closed():
    raw = config()
    raw.pop("contract_version")
    raw.pop("config_hash")
    raw["unexpected"] = True
    with pytest.raises(ConfigError, match="unknown"):
        normalize_performance_config(raw)
    raw.pop("unexpected")
    raw["energy"]["eta_values"] = [1.0]
    with pytest.raises(ConfigError, match="exact"):
        normalize_performance_config(raw)


def test_plan_only_does_not_create_configured_roots(tmp_path):
    output = tmp_path / "never-created-output"
    store = tmp_path / "never-created-store"
    completed = subprocess.run([
        sys.executable, str(PROJECT_ROOT / "scripts/run_v9_3_b4_performance.py"),
        "--config", str(PROJECT_ROOT / "configs/v9_3_b4_smoke.yaml"),
        "--plan-only", "--output-root", str(output), "--taskset-store", str(store),
    ], cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert not output.exists() and not store.exists()


def test_formal_execution_requires_cal_and_horizon_seals():
    formal = config("v9_3_b4_formal_template_r1.yaml")
    with pytest.raises(ConfigError, match="CAL selection seal"):
        assert_execution_seals(formal)
