from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from experiments.v9_3.config import ConfigError, load_config, validate_config
from experiments.v9_3.core4_aggregation import aggregate_core4
from experiments.v9_3.core4_sensitivity import Core4SensitivityRunner
from v9_3_core4_helpers import make_pair_fixture, write_pair_fixture


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
    write_pair_fixture(tmp_path, make_pair_fixture())
    summary = aggregate_core4(tmp_path)
    assert summary["paired_count"] == 1
    assert summary["p0_monotonicity_violation_count"] == 0
    assert (tmp_path / "core4_plot_data.csv").stat().st_size > 0
