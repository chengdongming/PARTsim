from __future__ import annotations

from collections import Counter

import pytest

from experiments.v9_3.core3_aggregation import (
    SoundnessClass,
    aggregate_tightness,
    classify_soundness,
    tightness_row,
)
from experiments.v9_3.simulation_result import SimulationStatus


def rta(*, proven: bool, timeout: bool = False):
    return {
        "solver_status": "TIMEOUT" if timeout else "COMPLETED",
        "certification_status": "CERTIFIED_TASKSET" if proven else "NOT_CERTIFIED",
        "taskset_proven": proven,
    }


def test_soundness_matrix_covers_all_eight_states():
    cases = [
        (rta(proven=True), SimulationStatus.PASS_OBSERVED.value, True, SoundnessClass.RTA_PASS_SIM_PASS),
        (rta(proven=True), SimulationStatus.DEADLINE_MISS.value, True, SoundnessClass.RTA_PASS_SIM_FAIL),
        (rta(proven=False), SimulationStatus.PASS_OBSERVED.value, True, SoundnessClass.RTA_FAIL_SIM_PASS),
        (rta(proven=False), SimulationStatus.DEADLINE_MISS.value, True, SoundnessClass.RTA_FAIL_SIM_FAIL),
        (rta(proven=True), SimulationStatus.HORIZON_INSUFFICIENT.value, True, SoundnessClass.RTA_PASS_SIM_CENSORED),
        (rta(proven=False), SimulationStatus.HORIZON_INSUFFICIENT.value, True, SoundnessClass.RTA_FAIL_SIM_CENSORED),
        (rta(proven=False, timeout=True), SimulationStatus.PASS_OBSERVED.value, True, SoundnessClass.RTA_TIMEOUT),
        (rta(proven=True), SimulationStatus.INTERNAL_ERROR.value, True, SoundnessClass.SIM_TIMEOUT_OR_ERROR),
    ]
    observed = [
        classify_soundness(row, status, release_e0_valid=e0)
        for row, status, e0, _expected in cases
    ]
    assert observed == [expected for *_rest, expected in cases]
    assert set(observed) == set(SoundnessClass)


def test_invalid_release_e0_does_not_erase_raw_soundness_failure():
    assert classify_soundness(
        rta(proven=True), SimulationStatus.DEADLINE_MISS.value,
        release_e0_valid=False,
    ) is SoundnessClass.RTA_PASS_SIM_FAIL


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
