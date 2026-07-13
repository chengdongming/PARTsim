from __future__ import annotations

from experiments.v9_3.censored_runtime import restricted_mean_runtime, runtime_summary


def test_timeout_is_right_censored_not_a_completed_runtime():
    rows = [
        {"observed_time_seconds": "2", "event_observed": "True", "censoring_status": "COMPLETED_EVENT", "timeout_budget_seconds": "10"},
        {"observed_time_seconds": "10", "event_observed": "False", "censoring_status": "RIGHT_CENSORED_TIMEOUT", "timeout_budget_seconds": "10"},
    ]
    summary = runtime_summary(rows)
    assert summary["completed_mean_seconds"] == 2
    assert summary["timeout_count"] == 1
    assert summary["censored_count"] == 1
    assert summary["restricted_mean_runtime_seconds"] > 2


def test_restricted_mean_handles_all_censored_inputs():
    assert restricted_mean_runtime([(3, False), (5, False)], 5) == 5
