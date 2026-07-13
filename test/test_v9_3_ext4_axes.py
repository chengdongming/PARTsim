from pathlib import Path

from experiments.v9_3.ext4_robustness import Ext4Runner
from experiments.v9_3.generator_family import (
    audited_generator_capabilities, required_service_period_max,
)
from experiments.v9_3.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_capability_audit_has_no_invented_priority_or_power_mode():
    capabilities = audited_generator_capabilities()
    assert capabilities["generator_families"] == ["UUNIFAST_DISCARD"]
    assert capabilities["priority_policies"] == ["RM"]
    assert capabilities["power_modes"] == ["generator_default_heterogeneous"]


def test_smoke_respects_rta_and_simulation_hard_limits():
    runner = Ext4Runner.from_path(ROOT / "configs/v9_3_ext4_smoke.yaml")
    description = runner.describe()
    assert description["sample_count"] == 3
    assert description["rta_analysis_count"] == 6 <= 12
    assert description["simulation_request_count"] == 3 <= 6


def test_shared_service_prefix_covers_every_period_range():
    config = load_config(ROOT / "configs/v9_3_ext4_smoke.yaml", expected_core="CORE-3")
    assert required_service_period_max(config) == 400
