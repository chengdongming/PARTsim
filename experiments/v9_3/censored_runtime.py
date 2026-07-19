"""Right-censored runtime summaries for CORE-5."""

from __future__ import annotations

import math
from statistics import mean, median
from typing import Any, Dict, Iterable, Mapping, Sequence


def p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def restricted_mean_runtime(
    observations: Iterable[tuple[float, bool]], tau: float
) -> float | None:
    """Kaplan-Meier restricted mean through ``tau``.

    Each observation is ``(time, completed_event)``. A timeout is a censoring
    event and therefore reduces the risk set without dropping survival.
    """

    data = [(min(float(value), tau), bool(event)) for value, event in observations]
    if not data or tau <= 0:
        return None
    grouped: Dict[float, list[bool]] = {}
    for value, event in data:
        grouped.setdefault(value, []).append(event)
    at_risk = len(data)
    survival = 1.0
    area = 0.0
    previous = 0.0
    for value in sorted(grouped):
        area += survival * max(0.0, value - previous)
        events = sum(grouped[value])
        censored = len(grouped[value]) - events
        if at_risk and events:
            survival *= 1.0 - events / at_risk
        at_risk -= events + censored
        previous = value
    if previous < tau:
        area += survival * (tau - previous)
    return area


def runtime_summary(
    rows: Sequence[Mapping[str, Any]], *, planned_analysis_count: int | None = None,
) -> Dict[str, Any]:
    completed = [
        float(row["observed_time_seconds"])
        for row in rows if str(row["event_observed"]) == "True"
    ]
    timeout_count = sum(
        str(row["censoring_status"]) == "RIGHT_CENSORED_TIMEOUT"
        for row in rows
    )
    technical_count = sum(
        str(row.get("terminal_class")) == "TECHNICAL_FAILURE"
        or str(row["censoring_status"]) == "TECHNICAL_FAILURE"
        for row in rows
    )
    observations = [
        (float(row["observed_time_seconds"]), str(row["event_observed"]) == "True")
        for row in rows
        if str(row["event_observed"]) == "True"
        or str(row["censoring_status"]) == "RIGHT_CENSORED_TIMEOUT"
    ]
    evaluable_count = len(observations)
    tau = max(
        (
            float(row["timeout_budget_seconds"])
            for row in rows
            if str(row["event_observed"]) == "True"
            or str(row["censoring_status"]) == "RIGHT_CENSORED_TIMEOUT"
        ),
        default=None,
    )
    planned = len(rows) if planned_analysis_count is None else planned_analysis_count
    return {
        "planned_analysis_count": planned,
        "terminal_analysis_count": len(rows),
        "runtime_evaluable_count": evaluable_count,
        "completed_count": len(completed),
        "completed_mean_seconds": mean(completed) if completed else None,
        "completed_median_seconds": median(completed) if completed else None,
        "completed_p95_seconds": p95(completed),
        "completed_max_seconds": max(completed) if completed else None,
        "timeout_count": timeout_count,
        "technical_failure_count": technical_count,
        "timeout_rate_evaluable_denominator": evaluable_count,
        "timeout_rate": timeout_count / evaluable_count if evaluable_count else None,
        "censored_count": timeout_count,
        "restriction_tau_seconds": tau,
        "restricted_mean_runtime_seconds": (
            restricted_mean_runtime(observations, tau)
            if tau is not None else None
        ),
    }
