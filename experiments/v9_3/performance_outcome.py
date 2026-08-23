"""Thin finite-horizon outcome evaluator for PERF-G."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


def evaluate_outcome(
    jobs: Sequence[Mapping[str, Any]],
    task_ids: Sequence[str],
    *,
    horizon: int,
    warmup: int = 0,
    minimum_adjudicable_jobs: int = 100,
    simulation_completed: bool = True,
    technical_error: str | None = None,
    strict_wholepass: bool = False,
) -> dict[str, Any]:
    """Recompute pass/fail on the half-open observation window [0, horizon)."""

    if technical_error is not None or not simulation_completed:
        return {
            "outcome_status": "TECHNICAL_FAILURE",
            "taskset_pass": None,
            "reason": technical_error or "simulation_did_not_reach_horizon",
            "jmr": None, "top4_jmr": None, "top25_jmr": None,
            "completion_ratio": None, "adjudicable_jobs": 0,
            "deadline_miss_jobs": 0, "censored_jobs": 0,
            "completed_jobs": 0, "released_jobs": 0,
            "wholepass": None, "technical_failure": True,
        }

    per_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    censored = 0
    for job in jobs:
        task_id = str(job.get("task_id"))
        release = int(job.get("release", 0))
        deadline = int(job.get("absolute_deadline"))
        if release < warmup or release >= horizon:
            continue
        if deadline >= horizon:
            censored += 1
            continue
        per_task[task_id].append(job)

    misses = 0
    adjudicable = 0
    task_failures = []
    for task_id in task_ids:
        eligible = per_task.get(str(task_id), [])
        adjudicable += len(eligible)
        task_misses = 0
        for job in eligible:
            completion = job.get("completion")
            if completion is None or int(completion) > int(job["absolute_deadline"]):
                task_misses += 1
        misses += task_misses
        if len(eligible) < minimum_adjudicable_jobs or task_misses:
            task_failures.append(task_id)

    all_jobs = [
        job for job in jobs
        if warmup <= int(job.get("release", 0)) < horizon
    ]
    completed = sum(job.get("completion") is not None for job in all_jobs)
    denominator = adjudicable
    if strict_wholepass:
        # Paper WholePass is a taskset-level hard-real-time predicate over
        # every non-censored job, not a job completion ratio or a per-task
        # average.  A zero denominator is unavailable/technical and never a
        # scientific failure.
        taskset_pass = None if not denominator else misses == 0
    else:
        taskset_pass = None if not denominator or task_failures else True
        if denominator and task_failures:
            taskset_pass = False
    available = bool(denominator)
    return {
        "outcome_status": "AVAILABLE" if available else "UNAVAILABLE",
        "taskset_pass": taskset_pass,
        "wholepass": taskset_pass if strict_wholepass else None,
        "technical_failure": not available,
        "reason": "all_adjudicable_jobs_on_time" if taskset_pass is True else (
            "zero_adjudicable_jobs" if not denominator else "deadline_or_coverage_failure"
        ),
        "jmr": (misses / denominator) if denominator else None,
        "top4_jmr": _rank_ratio(per_task, (0, 1, 2, 3)),
        "top25_jmr": _rank_ratio(per_task, (0, 1, 2)),
        "completion_ratio": (completed / len(all_jobs)) if all_jobs else None,
        "adjudicable_jobs": denominator,
        "deadline_miss_jobs": misses,
        "censored_jobs": censored,
        "completed_jobs": completed,
        "released_jobs": len(all_jobs),
        "task_failures": task_failures,
    }


def _rank_ratio(per_task: Mapping[str, Sequence[Mapping[str, Any]]], ranks: Sequence[int]) -> float | None:
    selected = []
    for task_id, jobs in per_task.items():
        if int(task_id) in ranks:
            selected.extend(jobs)
    if not selected:
        return None
    misses = sum(
        job.get("completion") is None
        or int(job.get("completion")) > int(job["absolute_deadline"])
        for job in selected
    )
    return misses / len(selected)


__all__ = ["evaluate_outcome"]
