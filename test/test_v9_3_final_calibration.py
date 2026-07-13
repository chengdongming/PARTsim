from fractions import Fraction

from experiments.v9_3.calibration import stage_b_cells, summarize_cells
from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import load_config, validate_config


def test_calibration_grid_exact_e0_seed_and_pairing_dimensions():
    config = load_config("configs/v9_3_final_calibration.yaml", expected_core="CORE-2")
    cells = expand_cells(config)
    assert len(cells) == 6
    assert {cell.exact_e0 for cell in cells} == {
        Fraction(1), Fraction(21473099401200000281, 200000000000000000000)
    }
    for utilization_index in range(3):
        paired = [cell for cell in cells if cell.utilization_index == utilization_index]
        assert len({cell.generation_id for cell in paired}) == 1
        seeds = {
            derive_seed(
                config["grid"]["base_seed"], cell.generation_id, 0,
                seed_mode=config["grid"]["seed_mode"],
                utilization_index=cell.utilization_index,
            )
            for cell in paired
        }
        assert len(seeds) == 1


def test_stage_b_filter_and_taskset_index_are_explicit():
    config = load_config("configs/v9_3_final_calibration.yaml")
    config["grid"]["cell_filter"] = [{
        "utilization": "3/10", "exact_e0": "1",
    }]
    config["grid"]["taskset_index_start"] = 1
    filtered = validate_config(config)
    cells = expand_cells(filtered)
    assert len(cells) == 1
    assert cells[0].utilization == Fraction(3, 10)
    assert filtered["grid"]["taskset_index_start"] == 1


def test_cell_classification_and_stage_b_rule():
    results = []
    variants = ["CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"]
    for variant in variants:
        results.append({
            "utilization": "1/5", "exact_e0": "1", "taskset_id": "t",
            "solver_status": "COMPLETED", "taskset_proven": "True",
            "runtime_wall_seconds": "1.0", "analysis_variant": variant,
        })
    generated = [{
        "taskset_id": "t", "d_over_t_values_json": '["1/2","3/4"]',
    }]
    tightness = [{
        "utilization": "1/5", "exact_e0": "1",
        "strict_envelope_count": 2, "local_only_closure_count": 0,
        "strict_response": False, "certification_gain": False,
        "local_only_candidate": False, "response_relation": "EQUAL",
        "diagnostic_status": "TRACED_COMPLETE",
    }]
    summary = summarize_cells(results, generated, tightness)
    assert summary[0]["category"] == "B"
    assert stage_b_cells(summary) == [{"utilization": "1/5", "exact_e0": "1"}]
