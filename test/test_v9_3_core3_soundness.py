from __future__ import annotations

from collections import Counter

import pytest

from experiments.v9_3.core3_aggregation import (
    SoundnessClass,
    aggregate_tightness,
    classify_soundness,
    response_bound_violation_row,
    tightness_row,
)
from experiments.v9_3.simulation_result import SimulationStatus


def rta(*, proven: bool, timeout: bool = False):
    return {
        "solver_status": "TIMEOUT" if timeout else "COMPLETED",
        "certification_status": "CERTIFIED_TASKSET" if proven else "NOT_CERTIFIED",
        "taskset_proven": proven,
    }


def test_soundness_matrix_covers_eligible_quadrants_and_ineligible_states():
    cases = [
        (rta(proven=True), SimulationStatus.PASS_OBSERVED.value, {}, SoundnessClass.RTA_PASS_SIM_PASS),
        (rta(proven=True), SimulationStatus.DEADLINE_MISS.value, {}, SoundnessClass.RTA_PASS_SIM_FAIL),
        (rta(proven=False), SimulationStatus.PASS_OBSERVED.value, {}, SoundnessClass.RTA_FAIL_SIM_PASS),
        (rta(proven=False), SimulationStatus.DEADLINE_MISS.value, {}, SoundnessClass.RTA_FAIL_SIM_FAIL),
        (rta(proven=True), SimulationStatus.DEADLINE_MISS.value, {"release_e0_valid": False}, SoundnessClass.ASSUMPTION_E0_NOT_SATISFIED),
        (rta(proven=True), SimulationStatus.DEADLINE_MISS.value, {"no_overflow_guard": False}, SoundnessClass.NO_OVERFLOW_GUARD_NOT_SATISFIED),
        (rta(proven=True), SimulationStatus.HORIZON_INSUFFICIENT.value, {"release_e0_valid": False, "comparison_eligible": False}, SoundnessClass.HORIZON_CENSORED),
        (rta(proven=True), SimulationStatus.PASS_OBSERVED.value, {"comparison_eligible": False}, SoundnessClass.OBSERVATION_COMPARISON_INELIGIBLE),
        (rta(proven=False, timeout=True), SimulationStatus.PASS_OBSERVED.value, {}, SoundnessClass.RTA_TIMEOUT),
        (rta(proven=True), SimulationStatus.INTERNAL_ERROR.value, {"release_e0_valid": False, "comparison_eligible": False}, SoundnessClass.SIM_TIMEOUT_OR_ERROR),
    ]
    valid_gates = {
        "release_e0_valid": True,
        "comparison_eligible": True,
        "no_overflow_guard": True,
    }
    observed = [
        classify_soundness(row, status, **{**valid_gates, **eligibility})
        for row, status, eligibility, _expected in cases
    ]
    assert observed == [expected for *_rest, expected in cases]
    assert set(observed) == set(SoundnessClass)


def test_invalid_release_e0_precedes_deadline_miss_soundness_quadrant():
    assert classify_soundness(
        rta(proven=True), SimulationStatus.DEADLINE_MISS.value,
        release_e0_valid=False,
        comparison_eligible=False,
        no_overflow_guard=True,
    ) is SoundnessClass.ASSUMPTION_E0_NOT_SATISFIED


@pytest.mark.parametrize("missing", [
    "release_e0_valid",
    "comparison_eligible",
    "no_overflow_guard",
])
def test_soundness_gate_parameters_are_required(missing):
    gates = {
        "release_e0_valid": True,
        "comparison_eligible": True,
        "no_overflow_guard": True,
    }
    del gates[missing]
    with pytest.raises(TypeError, match=missing):
        classify_soundness(
            rta(proven=True), SimulationStatus.PASS_OBSERVED.value,
            **gates,
        )


@pytest.mark.parametrize("false_gate", [
    "release_e0_valid",
    "comparison_eligible",
    "no_overflow_guard",
])
def test_false_soundness_gate_never_enters_a_quadrant(false_gate):
    gates = {
        "release_e0_valid": True,
        "comparison_eligible": True,
        "no_overflow_guard": True,
    }
    gates[false_gate] = False
    classification = classify_soundness(
        rta(proven=True), SimulationStatus.PASS_OBSERVED.value,
        **gates,
    )
    assert classification not in {
        SoundnessClass.RTA_PASS_SIM_PASS,
        SoundnessClass.RTA_PASS_SIM_FAIL,
        SoundnessClass.RTA_FAIL_SIM_PASS,
        SoundnessClass.RTA_FAIL_SIM_FAIL,
    }


@pytest.mark.parametrize("invalid", [None, "", "True", "true", 1])
def test_soundness_gate_rejects_non_boolean_encoding(invalid):
    with pytest.raises(TypeError, match="release_e0_valid"):
        classify_soundness(
            rta(proven=True), SimulationStatus.PASS_OBSERVED.value,
            release_e0_valid=invalid,
            comparison_eligible=True,
            no_overflow_guard=True,
        )


def response_case(
    *, candidate=8, simulated=9, status=SimulationStatus.PASS_OBSERVED,
    eligible=True,
):
    return response_bound_violation_row({
        "analysis_id": "analysis", "cell_id": "cell",
        "taskset_id": "taskset", "exact_e0": "1",
        "analysis_variant": "CW_THETA_CW", "task_id": "0",
        "priority_rank": "0", "C": "2", "D": "10", "T": "20",
        "P": "1/10", "task_solver_status": "CANDIDATE_FOUND",
        "candidate_response_time": str(candidate),
    }, {
        "r_sim_max": "" if simulated is None else str(simulated),
        "simulation_status": status.value,
    }, eligible=eligible)


def test_response_bound_violation_is_detected_even_below_deadline():
    row = response_case(candidate=8, simulated=9)
    assert row is not None
    assert row["D"] == "10"
    assert row["absolute_gap"] == -1


def test_response_equal_to_candidate_is_not_a_violation():
    assert response_case(candidate=8, simulated=8) is None


@pytest.mark.parametrize("updates", [
    {"eligible": False},
    {"simulated": None, "eligible": False},
])
def test_response_bound_violation_requires_every_comparison_premise(updates):
    assert response_case(**updates) is None


def test_gap_ratio_and_deadline_slack_use_observed_maximum():
    row = tightness_row({
        "analysis_id": "a", "cell_id": "c", "taskset_id": "t", "exact_e0": "1",
        "analysis_variant": "CW_THETA_CW", "task_id": "0", "priority_rank": "0",
        "D": "10", "task_solver_status": "CANDIDATE_FOUND",
        "candidate_response_time": "8",
    }, {
        "r_sim_max": "4", "tightness_eligible": True,
    })
    assert row is not None
    assert row["absolute_gap"] == 4
    assert row["normalized_gap"] == 1
    assert row["ratio"] == 2
    assert row["slack_to_deadline"] == 2


def test_censored_sample_is_excluded_from_tightness():
    assert tightness_row({
        "task_solver_status": "CANDIDATE_FOUND", "candidate_response_time": "5",
        "D": "10",
    }, {"r_sim_max": "4", "tightness_eligible": False}) is None


def test_equal_cw_loc_candidates_have_equal_tightness_and_comparison_count():
    common = {
        "analysis_id": "a", "cell_id": "c", "taskset_id": "t", "exact_e0": "1",
        "task_id": "0", "priority_rank": 0, "D": 10, "r_rta": 7,
        "r_sim_max": 5, "absolute_gap": 2, "normalized_gap": 0.4,
        "ratio": 1.4, "slack_to_deadline": 3, "exact_equality": False,
    }
    rows = [
        {**common, "analysis_variant": "CW_THETA_CW"},
        {**common, "analysis_id": "b", "analysis_variant": "LOC_THETA_LOC"},
    ]
    tasksets, summary = aggregate_tightness(rows)
    assert len(tasksets) == 2
    assert summary["loc_gap_eq_cw"] == 1
    assert summary["loc_gap_lt_cw"] == 0


def test_summary_distributions_keep_method_denominators_separate():
    rows = [{
        "analysis_id": method, "cell_id": "c", "taskset_id": method,
        "exact_e0": "1", "analysis_variant": method, "task_id": "0",
        "priority_rank": 0, "D": 10, "r_rta": candidate,
        "r_sim_max": 5, "absolute_gap": candidate - 5,
        "normalized_gap": (candidate - 5) / 5, "ratio": candidate / 5,
        "slack_to_deadline": 10 - candidate, "exact_equality": candidate == 5,
    } for method, candidate in (("CW_THETA_CW", 8), ("LOC_THETA_LOC", 6))]
    _tasksets, summary = aggregate_tightness(rows)
    assert summary["methods"]["CW_THETA_CW"]["task_count"] == 1
    assert summary["methods"]["LOC_THETA_LOC"]["task_count"] == 1
    assert summary["methods"]["CW_THETA_CW"]["absolute_gap"]["mean"] == 3
    assert summary["methods"]["LOC_THETA_LOC"]["absolute_gap"]["mean"] == 1
