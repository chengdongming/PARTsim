"""Independent half-open-window deadline outcome evaluator for B4."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


PERF_OUTCOME_VERSION = "PERF_OUTCOME_V2"
UNAVAILABLE = "UNAVAILABLE"


class PerformanceOutcomeError(ValueError):
    pass


@dataclass(frozen=True)
class TaskPerformance:
    task_id: str
    priority_rank: int
    adjudicable_jobs: int
    missed_jobs: int
    completed_inside_window: int
    minimum_jobs_satisfied: bool

    def row(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceOutcome:
    contract_version: str
    observed_pass: bool
    reason: str
    horizon_ms: int
    warmup_ms: int
    adjudicable_jobs: int
    missed_jobs: int
    completed_inside_window: int
    censored_jobs: int
    jmr: Optional[float]
    jmr_top_m: Optional[float]
    jmr_top_25_percent: Optional[float]
    completion_ratio: Optional[float]
    tasks: Tuple[TaskPerformance, ...]

    def row(self) -> Dict[str, Any]:
        value = asdict(self)
        for key in ("jmr", "jmr_top_m", "jmr_top_25_percent", "completion_ratio"):
            if value[key] is None:
                value[key] = UNAVAILABLE
        return value


def _value(job: Any, name: str, default: Any = None) -> Any:
    if isinstance(job, Mapping):
        return job.get(name, default)
    return getattr(job, name, default)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator == 0 else numerator / denominator


def evaluate_performance_outcome(
    jobs: Sequence[Any], task_payload: Sequence[Mapping[str, Any]], *,
    horizon_ms: int, warmup_ms: int, minimum_jobs_per_task: int,
    simulation_completed: bool, completion_reason: str,
    technical_error: Optional[str] = None, processors: int = 4,
) -> PerformanceOutcome:
    """Recompute observed pass and paper metrics without using legacy status."""

    if horizon_ms <= 0 or warmup_ms < 0 or warmup_ms >= horizon_ms:
        raise PerformanceOutcomeError("invalid half-open observation window")
    if minimum_jobs_per_task <= 0:
        raise PerformanceOutcomeError("minimum_jobs_per_task must be positive")
    definitions = {str(task["task_id"]): task for task in task_payload}
    if len(definitions) != len(task_payload):
        raise PerformanceOutcomeError("duplicate task ID")
    ranks = sorted(int(task["priority_rank"]) for task in task_payload)
    if ranks != list(range(len(task_payload))):
        raise PerformanceOutcomeError("priority ranks must be contiguous")

    records = []
    seen = set()
    censored = 0
    for index, job in enumerate(jobs):
        task_id = _value(job, "task_id")
        release = _value(job, "release")
        deadline = _value(job, "absolute_deadline")
        completion = _value(job, "completion")
        explicit_miss = bool(_value(job, "deadline_miss", False))
        if task_id is None or release is None:
            raise PerformanceOutcomeError("missing arrival identity")
        task_id = str(task_id)
        if task_id not in definitions:
            raise PerformanceOutcomeError(f"unknown task: {task_id}")
        try:
            release = int(release)
            deadline = int(deadline)
            completion = None if completion is None else int(completion)
        except (TypeError, ValueError) as exc:
            raise PerformanceOutcomeError(f"invalid job timing at index {index}") from exc
        identity = (task_id, release)
        if identity in seen:
            raise PerformanceOutcomeError("duplicate logical job")
        seen.add(identity)
        if deadline < release:
            raise PerformanceOutcomeError("deadline precedes release")
        if completion is not None and completion < release:
            raise PerformanceOutcomeError("completion precedes release")
        if completion is not None and completion <= deadline and explicit_miss:
            raise PerformanceOutcomeError("on-time completion conflicts with explicit miss")
        if release < warmup_ms:
            continue
        if deadline >= horizon_ms:
            censored += 1
            continue
        on_time = completion is not None and completion <= deadline
        missed = not on_time
        completed_inside = completion is not None and completion < horizon_ms
        records.append({
            "task_id": task_id,
            "rank": int(definitions[task_id]["priority_rank"]),
            "missed": missed,
            "completed_inside": completed_inside,
        })

    task_rows = []
    for task in sorted(task_payload, key=lambda value: int(value["priority_rank"])):
        task_id = str(task["task_id"])
        selected = [record for record in records if record["task_id"] == task_id]
        task_rows.append(TaskPerformance(
            task_id=task_id, priority_rank=int(task["priority_rank"]),
            adjudicable_jobs=len(selected),
            missed_jobs=sum(record["missed"] for record in selected),
            completed_inside_window=sum(record["completed_inside"] for record in selected),
            minimum_jobs_satisfied=len(selected) >= minimum_jobs_per_task,
        ))

    total = len(records)
    missed = sum(record["missed"] for record in records)
    completed = sum(record["completed_inside"] for record in records)
    top_m_records = [record for record in records if record["rank"] < min(processors, len(task_payload))]
    top_25_count = ceil(0.25 * len(task_payload))
    top_25_records = [record for record in records if record["rank"] < top_25_count]
    minimum_satisfied = all(task.minimum_jobs_satisfied for task in task_rows)
    technical_ok = simulation_completed and completion_reason == "reached_horizon" and technical_error is None
    observed_pass = technical_ok and minimum_satisfied and missed == 0
    if technical_error is not None:
        reason = f"technical_error:{technical_error}"
    elif not simulation_completed:
        reason = "simulation_not_completed"
    elif completion_reason != "reached_horizon":
        reason = "simulation_not_reached_horizon"
    elif not minimum_satisfied:
        reason = "minimum_adjudicable_jobs_not_satisfied"
    elif missed:
        reason = "deadline_miss"
    else:
        reason = "observed_pass"
    return PerformanceOutcome(
        contract_version=PERF_OUTCOME_VERSION,
        observed_pass=observed_pass, reason=reason,
        horizon_ms=horizon_ms, warmup_ms=warmup_ms,
        adjudicable_jobs=total, missed_jobs=missed,
        completed_inside_window=completed, censored_jobs=censored,
        jmr=_ratio(missed, total),
        jmr_top_m=_ratio(sum(record["missed"] for record in top_m_records), len(top_m_records)),
        jmr_top_25_percent=_ratio(sum(record["missed"] for record in top_25_records), len(top_25_records)),
        completion_ratio=_ratio(completed, total), tasks=tuple(task_rows),
    )


def evaluate_simulation_result(
    result: Any, task_payload: Sequence[Mapping[str, Any]], *,
    horizon_ms: int, warmup_ms: int, minimum_jobs_per_task: int,
    technical_error: Optional[str] = None, processors: int = 4,
) -> PerformanceOutcome:
    return evaluate_performance_outcome(
        result.jobs, task_payload, horizon_ms=horizon_ms, warmup_ms=warmup_ms,
        minimum_jobs_per_task=minimum_jobs_per_task,
        simulation_completed=bool(result.simulation_completed),
        completion_reason=str(result.completion_reason),
        technical_error=technical_error, processors=processors,
    )
