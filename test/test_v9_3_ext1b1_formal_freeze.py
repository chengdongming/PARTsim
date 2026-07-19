"""Freeze-contract tests for the EXT-1B1 formal configuration."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.config import ConfigError
from experiments.v9_3.ext1b_b1_analysis import summarize_b1
from experiments.v9_3.ext1b_config import (
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner
from experiments.v9_3 import ext1b_statistics


ROOT = Path(__file__).resolve().parents[1]
FORMAL_PATH = ROOT / "configs" / "v9_3_ext1b1_formal_r1.yaml"
FORMAL_SCHEDULERS = ["gpfp_asap_block", "gpfp_asap_nonblock"]
REQUIRED_OUTPUTS = [
    "b1_bypass_episodes.csv",
    "b1_task_effects.csv",
    "b1_paired_effects.csv",
    "b1_summary.csv",
]


def _raw(path: Path = FORMAL_PATH):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_formal_config_exact_contract_and_dry_run_cardinality():
    runner = Ext1BRunner.from_path(FORMAL_PATH)
    config = runner.config
    description = runner.describe()

    assert config["experiment_id"] == "asap-block-v9.3-ext1b1-formal-r1"
    assert config["parameter_status"] == "FORMAL"
    assert config["seed_space"] == "EXT1B1_FORMAL_R1"
    assert config["grid"]["base_seed"] == 951201
    assert config["platform"] == {"cores": [4], "task_count": [10]}
    assert config["grid"]["utilization_points"] == ["1/5", "2/5"]
    assert config["scenario"]["nominal_energy_supply_ratios"] == [
        "1/4", "1/2", "3/4",
    ]
    assert config["simulation"]["deadline_miss_fail_fast"] is True
    assert config["required_outputs"] == REQUIRED_OUTPUTS
    assert description["cell_count"] == 6
    assert description["tasksets_per_cell"] == 200
    assert description["scheduler_count"] == 2
    assert description["paired_instance_count"] == 1200
    assert description["simulation_request_count"] == 2400
    assert description["scheduler_ids"] == FORMAL_SCHEDULERS


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("grid", "tasksets_per_cell", 199, "tasksets_per_cell"),
        ("grid", "base_seed", 951202, "base_seed"),
        ("grid", "utilization_points", ["2/5", "1/5"], "utilization_points"),
        ("execution", "worker_count", 2, "worker_count"),
        ("simulation", "horizon", 399, "horizon and maximum_horizon"),
        ("simulation", "maximum_horizon", 401, "horizon and maximum_horizon"),
        (
            "scenario", "nominal_energy_supply_ratios",
            ["1/2", "1/4", "3/4"], "nominal_energy_supply_ratios",
        ),
    ],
)
def test_formal_scalar_and_grid_mutations_fail_closed(
    section, field, value, message,
):
    raw = _raw()
    raw[section][field] = value
    with pytest.raises(ConfigError, match=message):
        validate_ext1b_config(raw)


def test_formal_scheduler_order_is_fixed():
    raw = _raw()
    raw["scheduler_ids"] = list(reversed(FORMAL_SCHEDULERS))
    with pytest.raises(ConfigError, match="scheduler_ids"):
        validate_ext1b_config(raw)


def test_formal_required_outputs_are_fixed():
    raw = _raw()
    raw["required_outputs"] = REQUIRED_OUTPUTS[:-1]
    with pytest.raises(ConfigError, match="required_outputs"):
        validate_ext1b_config(raw)


def test_parameter_status_and_seed_spaces_are_bidirectionally_locked():
    formal_with_pilot_seed = _raw()
    formal_with_pilot_seed["seed_space"] = "EXT1B_PILOT"
    with pytest.raises(ConfigError, match="FORMAL requires seed_space"):
        validate_ext1b_config(formal_with_pilot_seed)

    pilot_with_formal_seed = _raw(
        ROOT / "configs" / "v9_3_ext1b1_pilot.yaml"
    )
    pilot_with_formal_seed["seed_space"] = "EXT1B1_FORMAL_R1"
    with pytest.raises(ConfigError, match="PILOT requires seed_space"):
        validate_ext1b_config(pilot_with_formal_seed)


def test_existing_b1_and_b2_configs_remain_valid():
    for name in (
        "v9_3_ext1b1_smoke.yaml",
        "v9_3_ext1b1_pilot.yaml",
        "v9_3_ext1b1_energy_calibration.yaml",
        "v9_3_ext1b2_smoke.yaml",
        "v9_3_ext1b2_pilot.yaml",
    ):
        assert load_ext1b_config(ROOT / "configs" / name)["extension"] == "EXT-1B"

    legacy = Ext1BRunner.from_path(ROOT / "configs" / "v9_3_ext1b1_smoke.yaml")
    assert legacy.describe(max_cells=1, max_tasksets=1)["scheduler_count"] == 9


def test_formal_resume_is_runtime_only_for_the_two_scheduler_plan():
    config = load_ext1b_config(FORMAL_PATH)
    resumed = deepcopy(config)
    resumed["execution"]["resume"] = True
    assert ext1b_config_hash(resumed) == ext1b_config_hash(config)
    assert Ext1BRunner(resumed).describe()["scheduler_ids"] == FORMAL_SCHEDULERS


def _statistic_results():
    rows = []
    for cell in ("u1_eta1", "u2_eta2"):
        for pair_index in range(2):
            pair = f"{cell}:pair:{pair_index}"
            for scheduler in FORMAL_SCHEDULERS:
                rows.append({
                    "scenario_kind": "BYPASS_STRESS",
                    "scenario_subtype": "B1",
                    "scenario_cell_id": cell,
                    "paired_instance_id": pair,
                    "scheduler_id": scheduler,
                    "status": "SIM_DEADLINE_MISS",
                    "comparison_eligible": True,
                    "overall_success": scheduler == "gpfp_asap_block",
                    "top_m_success": pair_index == 0,
                })
    return rows


def test_bootstrap_resamples_paired_tasksets_separately_within_each_cell(
    monkeypatch,
):
    sample_sizes = []

    def capture(differences, **_kwargs):
        sample_sizes.append(len(differences))
        return (0.0, 0.0)

    monkeypatch.setattr(ext1b_statistics, "paired_bootstrap_ci", capture)
    rows = ext1b_statistics.paired_statistics_rows(
        _statistic_results(), bootstrap_seed=7, bootstrap_resamples=10,
        scheduler_ids=FORMAL_SCHEDULERS,
    )
    binary = [row for row in rows if row["metric_type"] == "BINARY"]
    assert {(row["scenario_cell_id"], row["paired_count"]) for row in binary} == {
        ("u1_eta1", 2), ("u2_eta2", 2),
    }
    assert sample_sizes == [2, 2, 2, 2]


def test_episode_recovery_summary_is_descriptive_not_an_independent_sample():
    paired = [{
        "paired_instance_id": "pair-1",
        "pair_valid": True,
        **{
            f"{role}_{suffix}": 0
            for role in ("high", "low")
            for suffix in (
                "response_time_observed_job_count",
                "first_start_observed_job_count",
                "deadline_observable_job_count",
            )
        },
    }]
    episodes = [
        {"paired_instance_id": "pair-1", "bypass_event_count_in_episode": 1,
         "censored": False, "recovery_delay_ticks": delay}
        for delay in range(1, 11)
    ]
    summary = summarize_b1(paired, episodes, [])
    assert summary["total_pairs"] == 1
    assert summary["bypass_episode_count"] == 10
    assert summary["resolved_bypass_episode_count"] == 10
    assert summary["recovery_delay_mean_ticks"] == pytest.approx(5.5)
    assert not any("bootstrap" in key for key in summary)
