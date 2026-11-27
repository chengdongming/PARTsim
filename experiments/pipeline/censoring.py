"""CORE-3 horizon extension and right-censoring policy helpers."""

from __future__ import annotations

from typing import Optional

from .simulation_result import SimulationResult, SimulationStatus, TaskObservation


def next_horizon(
    current: int,
    maximum: int,
    policy: str,
) -> Optional[int]:
    """Return the next fresh-run horizon, or ``None`` when extension stops."""

    if current <= 0 or maximum < current:
        raise ValueError("invalid horizon bounds")
    if policy == "none" or current == maximum:
        return None
    if policy != "double":
        raise ValueError(f"unknown horizon extension policy: {policy}")
    return min(maximum, current * 2)


def task_is_tightness_eligible(
    simulation: SimulationResult,
    task: TaskObservation,
) -> bool:
    return bool(
        simulation.status is SimulationStatus.PASS_OBSERVED
        and simulation.release_e0_valid
        and task.minimum_jobs_satisfied
        and task.missed_jobs == 0
        and task.r_sim_max is not None
    )


def censoring_label(task: TaskObservation) -> str:
    if task.missed_jobs:
        return "DEADLINE_MISS"
    if not task.minimum_jobs_satisfied:
        return "MINIMUM_JOBS_NOT_OBSERVED"
    if task.censored_jobs:
        return "OBSERVED_WITH_RIGHT_CENSORED_TAIL"
    return "OBSERVED"
