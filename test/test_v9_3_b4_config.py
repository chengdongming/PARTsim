import subprocess
import sys
from copy import deepcopy

import pytest
import yaml

from experiments.v9_3.config import ConfigError
from experiments.v9_3.performance_config import assert_execution_seals, normalize_performance_config, plan_counts
from experiments.v9_3.performance_environment import (
    STAGE_ENVIRONMENT_DOMAIN, StageEnvironmentError,
    assert_environment_compatible, stage_scientific_config_hash,
)
from experiments.v9_3.config import domain_hash
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


def _environment(**updates):
    value = {
        "schema": "B4_STAGE_ENVIRONMENT_V1",
        "exact_source_commit": "source", "tracked_worktree_clean": True,
        "simulator_binary_sha256": "binary",
        "system_template_sha256": "system", "solar_data_sha256": "solar",
        "workload_power_contract_identity": "power",
        "outcome_contract_version": "PERF_OUTCOME_V2",
        "outcome_source_sha256": "outcome-source",
        "energy_contract_version": "energy-v1",
        "request_contract_version": "request-v1",
        "stage_config_hash": "config",
    }
    value.update(updates)
    value["environment_identity"] = domain_hash(STAGE_ENVIRONMENT_DOMAIN, value)
    return value


def test_stage_environment_excludes_execution_paths_and_detects_data_drift():
    baseline = config("v9_3_b4_smoke.yaml")
    changed = deepcopy(baseline)
    changed["execution"].update({
        "output_root": "/different", "worker_count": 99,
        "checkpoint_every": 999, "resume": True,
    })
    assert stage_scientific_config_hash(baseline) == stage_scientific_config_hash(changed)
    raw = yaml.safe_load((PROJECT_ROOT / "configs/v9_3_b4_smoke.yaml").read_text(encoding="utf-8"))
    changed_raw = deepcopy(raw)
    changed_raw["execution"].update({
        "output_root": "/other", "taskset_store": "/other-store",
        "worker_count": 77, "checkpoint_every": 88,
    })
    assert normalize_performance_config(raw)["config_hash"] == normalize_performance_config(changed_raw)["config_hash"]
    assert_environment_compatible(_environment(), _environment())
    with pytest.raises(StageEnvironmentError, match="solar_data_sha256"):
        assert_environment_compatible(_environment(), _environment(solar_data_sha256="changed"))
    with pytest.raises(StageEnvironmentError, match="not clean"):
        assert_environment_compatible(_environment(tracked_worktree_clean=False), _environment())
