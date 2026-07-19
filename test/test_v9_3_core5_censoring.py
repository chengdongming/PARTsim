from __future__ import annotations

import pytest

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


def test_kaplan_meier_ties_process_events_with_full_risk_set():
    # At t=2 one completion and one censor share the same risk set of three.
    # S(t) becomes 2/3, then the final completion at t=4 closes survival.
    assert restricted_mean_runtime(
        [(2, True), (2, False), (4, True)], 4
    ) == pytest.approx(2 + (2 / 3) * 2)


def test_different_timeout_budgets_use_reported_common_restriction_tau():
    rows = [
        {
            "observed_time_seconds": "2", "event_observed": "True",
            "terminal_class": "SCIENTIFIC_COMPLETION",
            "censoring_status": "COMPLETED_EVENT",
            "timeout_budget_seconds": "4",
        },
        {
            "observed_time_seconds": "8", "event_observed": "False",
            "terminal_class": "RIGHT_CENSORED",
            "censoring_status": "RIGHT_CENSORED_TIMEOUT",
            "timeout_budget_seconds": "8",
        },
    ]
    summary = runtime_summary(rows, planned_analysis_count=2)
    assert summary["restriction_tau_seconds"] == 8
    assert summary["timeout_rate_evaluable_denominator"] == 2


def test_all_completed_rmst_and_completed_metrics_use_only_events():
    rows = [
        {
            "observed_time_seconds": str(value), "event_observed": "True",
            "terminal_class": "SCIENTIFIC_COMPLETION",
            "censoring_status": "COMPLETED_EVENT",
            "timeout_budget_seconds": "10",
        }
        for value in (1, 3)
    ]
    summary = runtime_summary(rows)
    assert summary["completed_mean_seconds"] == 2
    assert summary["restricted_mean_runtime_seconds"] == 2
    assert summary["timeout_rate"] == 0


def test_technical_terminal_is_excluded_from_timeout_and_rmst_denominators():
    rows = [
        {
            "observed_time_seconds": "1", "event_observed": "True",
            "terminal_class": "SCIENTIFIC_COMPLETION",
            "censoring_status": "COMPLETED_EVENT",
            "timeout_budget_seconds": "4",
        },
        {
            "observed_time_seconds": "4", "event_observed": "False",
            "terminal_class": "RIGHT_CENSORED",
            "censoring_status": "RIGHT_CENSORED_TIMEOUT",
            "timeout_budget_seconds": "4",
        },
        {
            "observed_time_seconds": "UNAVAILABLE", "event_observed": "False",
            "terminal_class": "TECHNICAL_FAILURE",
            "censoring_status": "TECHNICAL_FAILURE",
            "timeout_budget_seconds": "4",
        },
    ]
    summary = runtime_summary(rows, planned_analysis_count=3)
    assert summary["terminal_analysis_count"] == 3
    assert summary["runtime_evaluable_count"] == 2
    assert summary["technical_failure_count"] == 1
    assert summary["timeout_rate_evaluable_denominator"] == 2
    assert summary["timeout_rate"] == .5


def test_zero_evaluable_runtime_is_unavailable_not_zero():
    rows = [{
        "observed_time_seconds": "UNAVAILABLE", "event_observed": "False",
        "terminal_class": "TECHNICAL_FAILURE",
        "censoring_status": "TECHNICAL_FAILURE",
        "timeout_budget_seconds": "4",
    }]
    summary = runtime_summary(rows)
    assert summary["runtime_evaluable_count"] == 0
    assert summary["completed_mean_seconds"] is None
    assert summary["timeout_rate"] is None
    assert summary["restriction_tau_seconds"] is None
    assert summary["restricted_mean_runtime_seconds"] is None
