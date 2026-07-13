from __future__ import annotations

from experiments.v9_3.monotonicity import compare_paired_analyses


def _result(proven=False, status="COMPLETED"):
    return {
        "solver_status": status, "taskset_proven": proven,
        "taskset_hash": "same", "outer_timeout": False,
    }


def _task(value, task_id="0"):
    return {
        "task_id": task_id, "task_solver_status": "CANDIDATE_FOUND",
        "candidate_response_time": value,
    }


def test_higher_e0_improvement_and_equality_are_distinct():
    improved = compare_paired_analyses(
        _result(False), _result(True), [_task(5)], [_task(4)],
        direction="RESOURCE_INCREASE",
    )
    assert improved["monotonicity_status"] == "MONOTONICITY_HOLDS"
    equal = compare_paired_analyses(
        _result(True), _result(True), [_task(5)], [_task(5)],
        direction="RESOURCE_INCREASE",
    )
    assert equal["monotonicity_status"] == "EQUAL"


def test_candidate_regression_is_a_violation_but_timeout_is_censored():
    violation = compare_paired_analyses(
        _result(True), _result(True), [_task(4)], [_task(5)],
        direction="RESOURCE_INCREASE",
    )
    assert violation["monotonicity_status"] == "MONOTONICITY_VIOLATION"
    censored = compare_paired_analyses(
        _result(True), _result(False, "TIMEOUT"), [_task(4)], [],
        direction="RESOURCE_INCREASE",
    )
    assert censored["monotonicity_status"] == "TIMEOUT_CENSORED"


def test_higher_power_cannot_improve_and_dependency_is_unavailable():
    violation = compare_paired_analyses(
        _result(False), _result(True), [_task(5)], [_task(4)],
        direction="COST_INCREASE",
    )
    assert violation["monotonicity_status"] == "MONOTONICITY_VIOLATION"
    unavailable = compare_paired_analyses(
        {**_result(), "solver_status": "DEPENDENCY_UNAVAILABLE"},
        _result(), [], [], direction="RESOURCE_INCREASE",
    )
    assert unavailable["monotonicity_status"] == "DEPENDENCY_UNAVAILABLE"
