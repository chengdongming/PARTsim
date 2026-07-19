from __future__ import annotations

from pathlib import Path

from experiments.v9_3.config import load_config
from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.core5_scalability import (
    Core5ScalabilityRunner, assert_single_axis_isolation,
    expand_scalability_cells,
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


def test_exact_single_axis_plan_and_method_scope_is_8_16_20():
    config = load_config(ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5")
    runner = Core5ScalabilityRunner(config)
    description = runner.describe()
    assert config["analysis"]["variants"] == ["CW_THETA_CW", "LOC_THETA_LOC"]
    assert [(cell.scaling_axis, cell.level_id) for cell in expand_scalability_cells(config)] == [
        ("task_count", "6"), ("task_count", "10"),
        ("core_count", "2"), ("core_count", "4"),
        ("period_range", "default-40-200"),
        ("period_range", "exact-2x-80-400"),
        ("worker_count", "1"), ("worker_count", "2"),
    ]
    assert description["cell_count"] == 8
    assert description["request_count"] == 16
    assert description["hard_analysis_limit"] == 20


def test_worker_count_changes_execution_identity_but_not_generation_or_store():
    config = load_config(ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5")
    runner = Core5ScalabilityRunner(config)
    workers = [
        cell for cell in expand_scalability_cells(config)
        if cell.scaling_axis == "worker_count"
    ]
    children = [runner._child_config(cell, 1) for cell in workers]
    left_cell, right_cell = (expand_cells(child)[0] for child in children)
    assert left_cell.generation_id == right_cell.generation_id
    assert derive_seed(config["grid"]["base_seed"], left_cell.generation_id, 0) == derive_seed(
        config["grid"]["base_seed"], right_cell.generation_id, 0
    )
    assert children[0]["execution"]["taskset_store"] == children[1]["execution"]["taskset_store"]
    assert workers[0].cell_id != workers[1].cell_id
    assert children[0]["analysis"]["worker_count"] == 1
    assert children[1]["analysis"]["worker_count"] == 2


def test_period_axis_keeps_service_declaration_and_only_extends_validated_prefix():
    config = load_config(ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5")
    runner = Core5ScalabilityRunner(config)
    periods = [
        cell for cell in expand_scalability_cells(config)
        if cell.scaling_axis == "period_range"
    ]
    children = [runner._child_config(cell, 1) for cell in periods]
    assert children[0]["energy"]["service_curve"] == children[1]["energy"]["service_curve"]
    assert children[0]["generation"]["period_max"] == 200
    assert children[1]["generation"]["period_max"] == 400
