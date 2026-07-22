from types import SimpleNamespace

import pytest

from experiments.v9_3.performance_outcome import (
    PERF_OUTCOME_VERSION, PerformanceOutcomeError, UNAVAILABLE,
    evaluate_performance_outcome, evaluate_simulation_result,
)


TASKS = [
    {"task_id": "0", "priority_rank": 0},
    {"task_id": "1", "priority_rank": 1},
]


def job(task, release, deadline, completion, miss=False):
    return SimpleNamespace(
        task_id=str(task), release=release, absolute_deadline=deadline,
        completion=completion, deadline_miss=miss,
    )


def evaluate(jobs, minimum=1):
    return evaluate_performance_outcome(
        jobs, TASKS, horizon_ms=10, warmup_ms=0,
        minimum_jobs_per_task=minimum, simulation_completed=True,
        completion_reason="reached_horizon", processors=1,
    )


def test_half_open_boundaries_and_misses():
    outcome = evaluate([
        job(0, 0, 5, 5),             # completion == deadline: on time
        job(1, 0, 5, 6),             # completion > deadline: miss
        job(0, 6, 10, None),          # deadline == H: censored
        job(1, 6, 11, None),          # unfinished deadline >= H: censored
    ])
    assert outcome.contract_version == PERF_OUTCOME_VERSION
    assert outcome.adjudicable_jobs == 2 and outcome.censored_jobs == 2
    assert outcome.missed_jobs == 1 and not outcome.observed_pass
    assert outcome.completion_ratio == 1.0


def test_unfinished_adjudicable_is_miss_and_completion_at_h_is_not_inside():
    outcome = evaluate([job(0, 0, 5, None), job(1, 0, 5, 10)])
    assert outcome.missed_jobs == 2
    assert outcome.completed_inside_window == 0


def test_completed_count_does_not_define_minimum_and_unavailable_is_explicit():
    outcome = evaluate([job(0, 0, 5, None), job(1, 0, 5, None)])
    assert all(task.minimum_jobs_satisfied for task in outcome.tasks)
    empty = evaluate_performance_outcome(
        [job(0, 0, 10, None), job(1, 0, 10, None)], TASKS,
        horizon_ms=10, warmup_ms=0, minimum_jobs_per_task=1,
        simulation_completed=True, completion_reason="reached_horizon",
    )
    assert empty.row()["jmr"] == UNAVAILABLE


def test_inconsistencies_fail_closed():
    with pytest.raises(PerformanceOutcomeError, match="explicit miss"):
        evaluate([job(0, 0, 5, 4, True), job(1, 0, 5, 4)])
    with pytest.raises(PerformanceOutcomeError, match="duplicate"):
        evaluate([job(0, 0, 5, 4), job(0, 0, 5, 4)])


def test_legacy_status_is_not_the_b4_outcome_authority():
    parsed = SimpleNamespace(
        status="legacy_status_deliberately_ignored",
        jobs=(job(0, 0, 5, 5), job(1, 0, 5, 5)),
        simulation_completed=True, completion_reason="reached_horizon",
    )
    outcome = evaluate_simulation_result(
        parsed, TASKS, horizon_ms=10, warmup_ms=0,
        minimum_jobs_per_task=1, processors=1,
    )
    assert outcome.observed_pass
