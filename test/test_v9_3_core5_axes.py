from __future__ import annotations

from pathlib import Path

from experiments.v9_3.config import load_config
from experiments.v9_3.core5_scalability import (
    assert_single_axis_isolation, expand_scalability_cells,
)


ROOT = Path(__file__).resolve().parents[1]


def test_scalability_expansion_is_deterministic_and_single_axis():
    config = load_config(ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5")
    first = expand_scalability_cells(config)
    second = expand_scalability_cells(config)
    assert first == second
    assert len(first) == 8
    assert {cell.scaling_axis for cell in first} == {
        "task_count", "core_count", "period_range", "worker_count"
    }
    for cell in first:
        assert_single_axis_isolation(config, cell)


def test_worker_levels_have_identical_mathematical_inputs():
    config = load_config(ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5")
    workers = [
        cell for cell in expand_scalability_cells(config)
        if cell.scaling_axis == "worker_count"
    ]
    assert workers[0].mathematical_input() == workers[1].mathematical_input()
    assert workers[0].worker_count != workers[1].worker_count
