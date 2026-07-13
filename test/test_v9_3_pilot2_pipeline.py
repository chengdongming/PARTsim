import json
from pathlib import Path

import pytest

from scripts import analyze_v9_3_pilot2 as analysis
from scripts import run_v9_3_pilot2 as pilot2


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config():
    return pilot2.load_config(PROJECT_ROOT / "configs" / "v9_3_pilot2.yaml")


@pytest.fixture(scope="module")
def baseline(config):
    return pilot2._baseline_context(config)


def test_baseline_reconstruction_is_exact_and_complete(baseline):
    assert len(baseline["generated"]) == 60
    assert len(baseline["taskset_rows"]) == 300
    assert sum(row["timeout"] == "True" for row in baseline["taskset_rows"]) == 27
    assert len(baseline["request_ids"]) == 300
    assert all(len(generated.tasks) == 10 for generated in baseline["generated"].values())


def test_config_predeclares_legal_aliases_and_fixed_grid(config):
    structures = config["screening"]["structures"]
    assert structures[2]["alias_of"] == "S1"
    assert structures[3]["alias_of"] == "S2"
    assert {entry["power_mode"] for entry in structures} == {
        "generator_default_heterogeneous"
    }
    assert config["screening"]["normalized_utilizations"] == [0.4, 0.6]
    assert config["screening"]["tasksets_per_cell"] == 5


def test_control_sample_excludes_dependency_not_applicable(baseline):
    controls = pilot2._select_controls(baseline, 2)
    assert len(controls) == 10
    assert all(row["solver_status"] != "NOT_APPLICABLE_DEPENDENCY" for row in controls)


def test_screening_seed_is_stable_and_cell_separated():
    values = {
        pilot2.derive_screening_seed(930112, structure, utilization, index)
        for structure in range(4)
        for utilization in range(2)
        for index in range(5)
    }
    assert len(values) == 40
    assert pilot2.derive_screening_seed(930112, 2, 1, 4) == pilot2.derive_screening_seed(
        930112, 2, 1, 4
    )
    assert pilot2.derive_screening_seed(930112, 2, 1, 4) != pilot2.derive_screening_seed(
        930212, 2, 1, 4
    )


def _request(index, variant="CW_D"):
    return {
        "request_id": f"r{index}", "analysis_variant": variant,
        "U_norm": "0.6", "E0": "1",
    }


def _attempt(index, budget, status, purpose="TIMEOUT_REQUEST", wall=16.0, match=True):
    return {
        "purpose": purpose, "request_id": f"r{index}", "budget_seconds": budget,
        "solver_status": status,
        "certification_status": "CERTIFIED_TASKSET" if status == "COMPLETED" else "NOT_CERTIFIED",
        "worker_startup_seconds": 0.01, "solver_wall_seconds": wall,
        "solver_cpu_seconds": wall - 0.01, "serialization_seconds": 0.001,
        "deserialization_seconds": 0.001, "transport_and_exit_seconds": 0.002,
        "total_wall_seconds": wall + 0.014,
        "candidate_matches_original": match,
        "certification_matches_original": match,
        "outcome_matches_original": match,
    }


def test_timeout_summary_enforces_conditional_sixty_and_uses_runtime(config):
    requests = [_request(index) for index in range(27)]
    attempts = [_attempt(index, 30, "COMPLETED", wall=16 + index / 100) for index in range(26)]
    attempts.extend([
        _attempt(26, 30, "TIMEOUT", wall=30),
        _attempt(26, 60, "NO_CANDIDATE", wall=34),
    ])
    for index in range(10):
        attempts.append(_attempt(100 + index, 30, "COMPLETED", "NON_TIMEOUT_CONTROL"))
    summary = analysis.summarize_timeout(requests, attempts, config)
    assert summary["fifteen_to_thirty_completed"] == 26
    assert summary["thirty_to_sixty_attempted"] == 1
    assert summary["thirty_to_sixty_completed"] == 1
    assert summary["new_no_candidate"] == 1
    assert summary["temporary_recommendation"] == "60_SECONDS"
    assert summary["non_timeout_outcome_drift"] == 0


def test_timeout_summary_rejects_ungated_sixty(config):
    requests = [_request(index) for index in range(27)]
    attempts = [_attempt(index, 30, "COMPLETED") for index in range(27)]
    attempts.append(_attempt(0, 60, "COMPLETED"))
    with pytest.raises(analysis.AnalysisError, match="not gated"):
        analysis.summarize_timeout(requests, attempts, config)


def _screening_fixture(config, strict_cell="S2-U0.4"):
    tasksets = []
    results = []
    tightness = []
    for structure in config["screening"]["structures"]:
        for utilization in config["screening"]["normalized_utilizations"]:
            cell = f"{structure['id']}-U{utilization:.1f}"
            for taskset_index in range(5):
                taskset_id = f"{cell}-t{taskset_index}"
                tasksets.append({"cell_id": cell, "U_norm": str(utilization)})
                for variant in config["analysis"]["variants"]:
                    results.append({
                        "cell_id": cell, "taskset_id": taskset_id,
                        "analysis_variant": variant, "solver_status": "COMPLETED",
                        "certification_status": "CERTIFIED_TASKSET", "timeout": False,
                        "numeric_error": False, "internal_error": False,
                        "not_applicable": False, "total_wall_seconds": 1.0,
                    })
                for rank in range(10):
                    tightness.append({
                        "cell_id": cell, "relation": "DEADLINE_CARRY_IN",
                        "status": "TIGHTER" if cell == strict_cell and rank == 0 and taskset_index == 0 else "EQUAL",
                        "envelope_common_count": 2, "envelope_strict_count": 1,
                        "envelope_equal_count": 1, "envelope_violation_count": 0,
                        "local_only_closure_count": 0, "q_plus_h_equals_w_count": 1,
                    })
    return tasksets, results, tightness


def test_screening_summary_keeps_all_eight_cells_and_selects_strict(config):
    tasksets, results, tightness = _screening_fixture(config)
    summary = analysis.summarize_screening(tasksets, results, tightness, config, 30)
    assert summary["generated_tasksets"] == 40
    assert summary["analysis_results"] == 200
    assert len(summary["cells"]) == 8
    assert summary["differentiating_cells"] == ["S2-U0.4"]
    assert summary["selected_confirmation_cells"] == ["S2-U0.4"]
    assert summary["cells"]["S3-U0.4"]["structure_alias_of"] == "S1"


def test_timed_production_request_has_nonnegative_components(baseline):
    generated = sorted(baseline["generated"].values(), key=lambda item: item.taskset_id)[0]
    row = baseline["tasksets"][(generated.taskset_id, "CW_D")]
    execution = pilot2._run_request(
        generated, pilot2.taskset.AnalysisVariant.CW_D, 2, baseline,
        row["analysis_id"],
    )
    assert execution.result is not None
    assert not execution.outer_timeout
    components = (
        execution.worker_startup_seconds, execution.solver_wall_seconds,
        execution.solver_cpu_seconds, execution.serialization_seconds,
        execution.deserialization_seconds, execution.transport_and_exit_seconds,
        execution.total_wall_seconds,
    )
    assert all(value >= 0 for value in components)
    assert execution.total_wall_seconds >= execution.solver_wall_seconds
