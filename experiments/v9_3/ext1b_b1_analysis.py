"""EXT-1B1 bypass episodes and paired high/low task effects."""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import canonical_json
from .result_writer import read_csv, write_csv
from .simulation_engine import load_simulation_terminal


BLOCK_SCHEDULER = "gpfp_asap_block"
NONBLOCK_SCHEDULER = "gpfp_asap_nonblock"
MAIN_SCHEDULERS = (BLOCK_SCHEDULER, NONBLOCK_SCHEDULER)
COMPARABLE_STATUSES = {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
TRACE_AUDITABLE_STATUSES = COMPARABLE_STATUSES | {"SIM_HORIZON_INSUFFICIENT"}
UNAVAILABLE = "UNAVAILABLE"

B1_EPISODE_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_cell_id", "taskset_id",
    "taskset_hash", "scheduler", "blocked_task_id", "blocked_job_id",
    "low_task_id", "bypassed_task_id", "bypassed_job_id",
    "bypassed_task_ids_json", "bypassed_job_ids_json",
    "episode_start_tick", "episode_last_bypass_tick", "bypass_event_count",
    "recovery_tick", "recovery_delay_ticks", "censored", "censor_reason",
)

B1_TASK_EFFECT_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_cell_id", "taskset_id",
    "taskset_hash", "scheduler", "status", "comparison_eligible",
    "high_task_id", "low_task_id",
    "high_observation_state", "high_job_count", "high_completed_job_count",
    "high_censored_job_count", "high_first_execution_time",
    "high_response_time_mean", "high_response_time_max",
    "high_deadline_miss_count", "high_deadline_miss_ratio",
    "high_job_identities_json", "high_response_job_identities_json",
    "low_observation_state", "low_job_count", "low_completed_job_count",
    "low_censored_job_count", "low_first_execution_time",
    "low_response_time_mean", "low_response_time_max",
    "low_deadline_miss_count", "low_deadline_miss_ratio",
    "low_job_identities_json", "low_response_job_identities_json",
    "ready_but_idle_ticks",
    "total_deadline_miss_count",
)

B1_PAIRED_EFFECT_COLUMNS = (
    "paired_instance_id", "scenario_cell_id", "taskset_id", "taskset_hash",
    "block_request_id", "nonblock_request_id", "block_status",
    "nonblock_status", "high_task_id", "low_task_id", "pair_valid",
    "pair_failure_reason", "mechanism_activated", "bypass_event_count",
    "bypass_episode_count", "resolved_bypass_episode_count",
    "censored_bypass_episode_count", "high_response_pairable",
    "low_response_pairable", "high_first_execution_delta",
    "high_response_mean_delta", "high_response_max_delta",
    "high_deadline_miss_delta", "low_first_execution_delta",
    "low_response_mean_delta", "low_response_max_delta",
    "low_deadline_miss_delta", "ready_but_idle_ticks_delta",
    "total_deadline_miss_delta",
)

DELTA_FIELDS = (
    "high_first_execution_delta", "high_response_mean_delta",
    "high_response_max_delta", "high_deadline_miss_delta",
    "low_first_execution_delta", "low_response_mean_delta",
    "low_response_max_delta", "low_deadline_miss_delta",
    "ready_but_idle_ticks_delta", "total_deadline_miss_delta",
)

B1_SUMMARY_COLUMNS = (
    "total_pairs", "valid_pairs", "invalid_pairs", "mechanism_activated_pairs",
    "bypass_event_count", "bypass_episode_count",
    "resolved_bypass_episode_count", "censored_bypass_episode_count",
    "recovery_delay_sum_ticks", "recovery_delay_mean_ticks",
    "recovery_delay_median_ticks", "recovery_delay_max_ticks",
    "recovery_denominator_zero", "timeout_count", "internal_error_count",
    "horizon_insufficient_count",
    *tuple(
        name
        for field in DELTA_FIELDS
        for name in (
            f"{field}_pair_count", f"{field}_mean", f"{field}_median",
        )
    ),
)

PAIR_FAIRNESS_FIELDS = (
    "paired_instance_id", "taskset_id", "taskset_hash", "trace_hash",
    "simulation_config_hash", "input_hash", "generation_seed", "M",
    "initial_battery", "battery_capacity", "horizon", "maximum_horizon",
    "priority_hash", "power_hash", "deadline_hash", "release_hash",
    "workload_vector_hash", "simulator_build_hash",
)


class B1AnalysisError(RuntimeError):
    """B1 evidence is incomplete, ambiguous, or internally inconsistent."""


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise B1AnalysisError(f"{label} must be an integer")
    try:
        parsed = Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise B1AnalysisError(f"{label} must be an integer") from exc
    if parsed.denominator != 1:
        raise B1AnalysisError(f"{label} must be an integer")
    return parsed.numerator


def _optional_number(value: Any) -> float | int | None:
    if value in {None, "", UNAVAILABLE, "NONE"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise B1AnalysisError(f"expected numeric value, got {value!r}") from exc
    if not math.isfinite(number):
        raise B1AnalysisError(f"expected finite value, got {value!r}")
    return int(number) if number.is_integer() else number


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in {"TRUE", "1"}:
        return True
    if text in {"FALSE", "0"}:
        return False
    return None


def _task_id(task_name: Any, known_task_ids: set[str]) -> str:
    if not isinstance(task_name, str) or not task_name.startswith("v93_task_"):
        raise B1AnalysisError(f"invalid native task identity: {task_name!r}")
    task_id = task_name[len("v93_task_"):]
    if task_id not in known_task_ids:
        raise B1AnalysisError(f"unknown B1 task identity: {task_name!r}")
    return task_id


def _job_identity(payload: Mapping[str, Any], known_task_ids: set[str]) -> tuple[str, str]:
    task_id = _task_id(payload.get("task_name"), known_task_ids)
    release = _integer(payload.get("arrival_time"), "job arrival_time")
    identity = f"{task_id}@{release}"
    reported = payload.get("job_id")
    if reported not in {None, "", f"v93_task_{task_id}@{release}", identity}:
        raise B1AnalysisError(
            f"job identity mismatch: payload={reported!r}, derived={identity!r}"
        )
    return task_id, identity


def _strict_trace(path: Path | str) -> Mapping[str, Any]:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise B1AnalysisError(f"duplicate trace JSON key: {key}")
            result[key] = value
        return result

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=no_duplicates)
    except (OSError, json.JSONDecodeError) as exc:
        raise B1AnalysisError(f"cannot read B1 trace: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("events"), list):
        raise B1AnalysisError("B1 trace must contain an event array")
    return document


def _decision_for_bypass(
    events: Sequence[Mapping[str, Any]], position: int, tick: int,
) -> Mapping[str, Any]:
    for candidate in reversed(events[:position]):
        candidate_tick = _integer(candidate.get("time"), "decision event time")
        if candidate_tick < tick:
            break
        if (
            candidate_tick == tick
            and candidate.get("event_type") == "scheduler_decision"
            and candidate.get("scheduler") == "ASAP-NonBlock"
        ):
            return candidate
    raise B1AnalysisError(f"bypass event at tick {tick} has no same-tick decision")


def reconstruct_bypass_episodes(
    trace: Path | str | Mapping[str, Any], *, request: Mapping[str, Any],
    high_task_id: str, low_task_id: str, known_task_ids: Iterable[str],
    terminal_status: str,
) -> list[Dict[str, Any]]:
    """Reconstruct consecutive per-job bypass intervals from a native trace."""

    document = _strict_trace(trace) if not isinstance(trace, Mapping) else trace
    if document.get("trace_schema_version") != 2:
        raise B1AnalysisError("B1 requires trace schema version 2")
    if document.get("configured_scheduler") != NONBLOCK_SCHEDULER:
        raise B1AnalysisError("B1 bypass trace scheduler is not ASAP-NONBLOCK")
    known = {str(value) for value in known_task_ids}
    if high_task_id not in known or low_task_id not in known or high_task_id == low_task_id:
        raise B1AnalysisError("B1 high/low structure identity is invalid")

    events = document.get("events")
    if not isinstance(events, list):
        raise B1AnalysisError("B1 trace events must be an array")
    typed_events: list[Mapping[str, Any]] = []
    prior_tick = -1
    for position, raw in enumerate(events):
        if not isinstance(raw, dict):
            raise B1AnalysisError(f"trace event {position} is not an object")
        tick = _integer(raw.get("time"), f"trace event {position} time")
        if tick < prior_tick:
            raise B1AnalysisError("B1 trace event time is not monotonic")
        prior_tick = tick
        typed_events.append(raw)

    scheduled: Dict[str, list[int]] = defaultdict(list)
    missed: Dict[str, list[int]] = defaultdict(list)
    completed: Dict[str, list[int]] = defaultdict(list)
    bypasses: list[Dict[str, Any]] = []

    for position, event in enumerate(typed_events):
        event_type = event.get("event_type")
        tick = _integer(event.get("time"), f"trace event {position} time")
        if event_type in {"scheduled", "dline_miss", "end_instance"}:
            _, identity = _job_identity(event, known)
            target = (
                scheduled if event_type == "scheduled"
                else missed if event_type == "dline_miss"
                else completed
            )
            target[identity].append(tick)
        if event_type != "nonblock_bypass":
            continue
        if event.get("scheduler") != "ASAP-NonBlock" or event.get("reason") != (
            "lower_priority_bypass_due_to_energy"
        ):
            raise B1AnalysisError("invalid native NONBLOCK_BYPASS event")
        blocked_name = event.get("blocked_higher_priority_task")
        bypassed_name = event.get("bypassed_task")
        blocked_id = _task_id(blocked_name, known)
        bypassed_id = _task_id(bypassed_name, known)
        if blocked_id == bypassed_id:
            raise B1AnalysisError("bypass event uses the same blocked/bypassed task")

        decision = _decision_for_bypass(typed_events, position, tick)
        ready = decision.get("ready_jobs")
        selected = decision.get("selected_jobs")
        if not isinstance(ready, list) or not isinstance(selected, list):
            raise B1AnalysisError("same-tick bypass decision lacks job arrays")
        blocked_jobs = [
            item for item in ready
            if isinstance(item, dict) and item.get("task_name") == blocked_name
        ]
        bypassed_jobs = [
            item for item in selected
            if isinstance(item, dict) and item.get("task_name") == bypassed_name
        ]
        if len(blocked_jobs) != 1 or len(bypassed_jobs) != 1:
            raise B1AnalysisError("bypass job identity is missing or ambiguous")
        _, blocked_job_id = _job_identity(blocked_jobs[0], known)
        _, bypassed_job_id = _job_identity(bypassed_jobs[0], known)
        selected_ids = {
            _job_identity(item, known)[1]
            for item in selected if isinstance(item, dict)
        }
        if blocked_job_id in selected_ids:
            raise B1AnalysisError("blocked bypass job is also selected")
        if tick in scheduled.get(blocked_job_id, []):
            raise B1AnalysisError("blocked bypass job executes at the bypass tick")
        bypasses.append({
            "tick": tick,
            "blocked_task_id": blocked_id,
            "blocked_job_id": blocked_job_id,
            "bypassed_task_id": bypassed_id,
            "bypassed_job_id": bypassed_job_id,
        })

    by_blocked_job: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for event in bypasses:
        by_blocked_job[event["blocked_job_id"]].append(event)

    episodes: list[Dict[str, Any]] = []
    for blocked_job_id, members in by_blocked_job.items():
        members.sort(key=lambda row: int(row["tick"]))
        segments: list[list[Dict[str, Any]]] = []
        current: list[Dict[str, Any]] = []
        for event in members:
            tick = int(event["tick"])
            if current:
                previous = int(current[-1]["tick"])
                execution_boundary = any(
                    previous < scheduled_tick <= tick
                    for scheduled_tick in scheduled.get(blocked_job_id, [])
                )
                if tick != previous + 1 or execution_boundary:
                    segments.append(current)
                    current = []
            current.append(event)
        if current:
            segments.append(current)

        for segment in segments:
            start = int(segment[0]["tick"])
            last = int(segment[-1]["tick"])
            recovery = min(
                (tick for tick in scheduled.get(blocked_job_id, []) if tick > start),
                default=None,
            )
            censored = recovery is None
            if not censored:
                censor_reason = ""
                recovery_delay: Any = recovery - start
            else:
                recovery_delay = ""
                if any(tick >= start for tick in missed.get(blocked_job_id, [])):
                    censor_reason = "DEADLINE_MISS_BEFORE_RECOVERY"
                elif any(tick >= start for tick in completed.get(blocked_job_id, [])):
                    raise B1AnalysisError("job completed without an observed recovery")
                elif terminal_status == "SIM_HORIZON_INSUFFICIENT":
                    censor_reason = "HORIZON_CUTOFF"
                else:
                    censor_reason = "NO_RECOVERY_BEFORE_SIMULATION_END"
            bypassed_task_ids = sorted({row["bypassed_task_id"] for row in segment})
            bypassed_job_ids = sorted({row["bypassed_job_id"] for row in segment})
            episodes.append({
                "request_id": request["request_id"],
                "paired_instance_id": request["paired_instance_id"],
                "scenario_cell_id": request["scenario_cell_id"],
                "taskset_id": request["taskset_id"],
                "taskset_hash": request["taskset_hash"],
                "scheduler": NONBLOCK_SCHEDULER,
                "blocked_task_id": segment[0]["blocked_task_id"],
                "blocked_job_id": blocked_job_id,
                "low_task_id": low_task_id,
                "bypassed_task_id": (
                    bypassed_task_ids[0] if len(bypassed_task_ids) == 1 else "MULTIPLE"
                ),
                "bypassed_job_id": (
                    bypassed_job_ids[0] if len(bypassed_job_ids) == 1 else "MULTIPLE"
                ),
                "bypassed_task_ids_json": canonical_json(bypassed_task_ids),
                "bypassed_job_ids_json": canonical_json(bypassed_job_ids),
                "episode_start_tick": start,
                "episode_last_bypass_tick": last,
                "bypass_event_count": len(segment),
                "recovery_tick": "" if recovery is None else recovery,
                "recovery_delay_ticks": recovery_delay,
                "censored": censored,
                "censor_reason": censor_reason,
            })
    return sorted(
        episodes,
        key=lambda row: (
            int(row["episode_start_tick"]), str(row["blocked_job_id"]),
        ),
    )


def _observation_state(status: str, jobs: Sequence[Mapping[str, Any]]) -> str:
    if status == "SIM_RUNTIME_TIMEOUT":
        return "RUNTIME_TIMEOUT"
    if status == "SIM_INTERNAL_ERROR":
        return "INTERNAL_ERROR"
    if status == "SIM_HORIZON_INSUFFICIENT":
        return "HORIZON_INSUFFICIENT"
    if not jobs:
        return "NO_JOBS"
    if any(_bool(job.get("deadline_miss")) is True for job in jobs):
        return "DEADLINE_MISS"
    if any(_bool(job.get("censored")) is True for job in jobs):
        return "HORIZON_CENSORED"
    return "COMPLETED"


def _role_metrics(
    role: str, task_id: str, task_row: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]], status: str,
) -> Dict[str, Any]:
    eligible = [job for job in jobs if _bool(job.get("eligible_after_warmup")) is True]
    for job in eligible:
        if str(job.get("task_id")) != task_id:
            raise B1AnalysisError(f"{role} job/task identity mismatch")
    job_count = len(eligible)
    completed = [job for job in eligible if job.get("completion") is not None]
    censored = [job for job in eligible if _bool(job.get("censored")) is True]
    missed = [job for job in eligible if _bool(job.get("deadline_miss")) is True]
    expected_counts = {
        "observed_jobs": job_count,
        "completed_jobs": len(completed),
        "missed_jobs": len(missed),
        "censored_jobs": len(censored),
    }
    for field, expected in expected_counts.items():
        if _integer(task_row.get(field), f"{role} {field}") != expected:
            raise B1AnalysisError(f"{role} task/job {field} mismatch")
    responses = [
        _integer(job.get("response_time"), f"{role} response_time")
        for job in eligible if job.get("response_time") is not None
    ]
    firsts = [
        _integer(job.get("first_execution"), f"{role} first_execution")
        for job in eligible if job.get("first_execution") is not None
    ]
    identified_jobs = sorted(
        [
            (
                f"{task_id}@{_integer(job.get('release'), f'{role} release')}#"
                f"{_integer(job.get('job_index'), f'{role} job_index')}",
                job,
            )
            for job in eligible
        ],
        key=lambda item: item[0],
    )
    identities = [identity for identity, _ in identified_jobs]
    if len(identities) != len(set(identities)):
        raise B1AnalysisError(f"{role} job identities are not unique")
    response_identities = [
        identity for identity, job in identified_jobs
        if job.get("response_time") is not None
    ]
    derived_first = min(firsts) if firsts else None
    reported_first = _optional_number(task_row.get("first_execution_time"))
    if reported_first != derived_first:
        raise B1AnalysisError(f"{role} task/job first-execution mismatch")
    derived_max_response = max(responses) if responses else None
    reported_max_response = _optional_number(
        task_row.get("maximum_observed_response_time")
    )
    if reported_max_response != derived_max_response:
        raise B1AnalysisError(f"{role} task/job maximum-response mismatch")
    return {
        f"{role}_observation_state": _observation_state(status, eligible),
        f"{role}_job_count": job_count,
        f"{role}_completed_job_count": len(completed),
        f"{role}_censored_job_count": len(censored),
        f"{role}_first_execution_time": derived_first if derived_first is not None else "",
        f"{role}_response_time_mean": mean(responses) if responses else "",
        f"{role}_response_time_max": max(responses) if responses else "",
        f"{role}_deadline_miss_count": len(missed),
        f"{role}_deadline_miss_ratio": (
            len(missed) / job_count if job_count else ""
        ),
        f"{role}_job_identities_json": canonical_json(identities),
        f"{role}_response_job_identities_json": canonical_json(response_identities),
    }


def build_b1_task_effect_row(
    request: Mapping[str, Any], result: Mapping[str, Any],
    task_rows: Sequence[Mapping[str, Any]], jobs: Sequence[Mapping[str, Any]],
    structure: Mapping[str, Any],
) -> Dict[str, Any]:
    """Join the structural high/low IDs to task and job observations."""

    high_task_id = str(structure.get("high_task_id", ""))
    low_task_id = str(structure.get("low_task_id", ""))
    if not high_task_id or not low_task_id or high_task_id == low_task_id:
        raise B1AnalysisError("B1 structure has invalid high_task_id/low_task_id")
    for field in (
        "request_id", "paired_instance_id", "scenario_cell_id", "taskset_id",
        "taskset_hash", "scheduler_id",
    ):
        if field in result and str(request.get(field)) != str(result.get(field)):
            raise B1AnalysisError(f"B1 request/result {field} mismatch")
    by_task: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in task_rows:
        if str(row.get("request_id")) != str(request.get("request_id")):
            raise B1AnalysisError("B1 task row belongs to another request")
        by_task[str(row.get("task_id"))].append(row)
    if len(by_task.get(high_task_id, [])) != 1 or len(by_task.get(low_task_id, [])) != 1:
        raise B1AnalysisError("B1 high/low task outcome link is missing or ambiguous")
    by_job: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for job in jobs:
        by_job[str(job.get("task_id"))].append(job)

    status = str(result.get("status"))
    base = {
        "request_id": request["request_id"],
        "paired_instance_id": request["paired_instance_id"],
        "scenario_cell_id": request["scenario_cell_id"],
        "taskset_id": request["taskset_id"],
        "taskset_hash": request["taskset_hash"],
        "scheduler": request["scheduler_id"],
        "status": status,
        "comparison_eligible": _bool(result.get("comparison_eligible")) is True,
        "high_task_id": high_task_id,
        "low_task_id": low_task_id,
        **_role_metrics(
            "high", high_task_id, by_task[high_task_id][0],
            by_job.get(high_task_id, []), status,
        ),
        **_role_metrics(
            "low", low_task_id, by_task[low_task_id][0],
            by_job.get(low_task_id, []), status,
        ),
        "ready_but_idle_ticks": result.get(
            "idle_cores_while_ready_jobs_exist_ticks", "",
        ),
        "total_deadline_miss_count": result.get("missed_jobs", ""),
    }
    return base


def _pair_delta(nonblock: Mapping[str, Any], block: Mapping[str, Any], field: str) -> Any:
    left = _optional_number(nonblock.get(field))
    right = _optional_number(block.get(field))
    return "" if left is None or right is None else left - right


def build_b1_paired_effects(
    scenario_rows: Sequence[Mapping[str, Any]], requests: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]], task_effects: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    """Build NONBLOCK-minus-BLOCK effects without admitting invalid pairs."""

    request_index: Dict[str, Mapping[str, Any]] = {}
    request_by_pair: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in requests:
        request_id = str(row["request_id"])
        scheduler = str(row["scheduler_id"])
        pair_id = str(row["paired_instance_id"])
        if request_id in request_index or scheduler in request_by_pair[pair_id]:
            raise B1AnalysisError("duplicate B1 request identity")
        request_index[request_id] = row
        request_by_pair[pair_id][scheduler] = row
    result_by_pair: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in results:
        pair_id, scheduler = str(row["paired_instance_id"]), str(row["scheduler_id"])
        if scheduler in result_by_pair[pair_id]:
            raise B1AnalysisError("duplicate B1 result identity")
        result_by_pair[pair_id][scheduler] = row
    effect_by_pair: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in task_effects:
        pair_id, scheduler = str(row["paired_instance_id"]), str(row["scheduler"])
        if scheduler in effect_by_pair[pair_id]:
            raise B1AnalysisError("duplicate B1 task-effect identity")
        effect_by_pair[pair_id][scheduler] = row
    episode_by_pair: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in episodes:
        episode_by_pair[str(row["paired_instance_id"])].append(row)

    output: list[Dict[str, Any]] = []
    for scenario in sorted(
        scenario_rows, key=lambda row: str(row["paired_instance_id"]),
    ):
        if str(scenario.get("scenario_kind")) != "BYPASS_STRESS":
            continue
        pair_id = str(scenario["paired_instance_id"])
        results_by_scheduler = result_by_pair.get(pair_id, {})
        effects_by_scheduler = effect_by_pair.get(pair_id, {})
        failures: list[str] = []
        block_result = results_by_scheduler.get(BLOCK_SCHEDULER)
        nonblock_result = results_by_scheduler.get(NONBLOCK_SCHEDULER)
        block_effect = effects_by_scheduler.get(BLOCK_SCHEDULER)
        nonblock_effect = effects_by_scheduler.get(NONBLOCK_SCHEDULER)
        if block_result is None:
            failures.append("missing_block_result")
        if nonblock_result is None:
            failures.append("missing_nonblock_result")
        if block_effect is None:
            failures.append("missing_block_task_effect")
        if nonblock_effect is None:
            failures.append("missing_nonblock_task_effect")

        block_request = request_by_pair.get(pair_id, {}).get(BLOCK_SCHEDULER)
        nonblock_request = request_by_pair.get(pair_id, {}).get(NONBLOCK_SCHEDULER)
        if block_request is None:
            failures.append("missing_block_request")
        if nonblock_request is None:
            failures.append("missing_nonblock_request")
        if block_request is not None and nonblock_request is not None:
            for field in PAIR_FAIRNESS_FIELDS:
                if str(block_request.get(field)) != str(nonblock_request.get(field)):
                    failures.append(f"fairness_mismatch:{field}")

        for label, row in (("block", block_result), ("nonblock", nonblock_result)):
            if row is None:
                continue
            if str(row.get("status")) not in COMPARABLE_STATUSES:
                failures.append(f"{label}_status_not_comparable:{row.get('status')}")
            if _bool(row.get("comparison_eligible")) is not True:
                failures.append(f"{label}_comparison_ineligible")

        if block_effect is not None and nonblock_effect is not None:
            for field in (
                "high_task_id", "low_task_id", "high_job_identities_json",
                "low_job_identities_json",
            ):
                if str(block_effect.get(field)) != str(nonblock_effect.get(field)):
                    failures.append(f"job_or_task_identity_mismatch:{field}")
            for label, field in (
                ("block", "ready_but_idle_ticks"),
                ("nonblock", "ready_but_idle_ticks"),
                ("block", "total_deadline_miss_count"),
                ("nonblock", "total_deadline_miss_count"),
            ):
                row = block_effect if label == "block" else nonblock_effect
                if _optional_number(row.get(field)) is None:
                    failures.append(f"{label}_missing_{field}")

        failures = list(dict.fromkeys(failures))
        valid = not failures
        pair_episodes = episode_by_pair.get(pair_id, [])
        event_count = sum(_integer(row["bypass_event_count"], "episode event count") for row in pair_episodes)
        resolved = sum(_bool(row.get("censored")) is False for row in pair_episodes)
        censored = sum(_bool(row.get("censored")) is True for row in pair_episodes)
        row: Dict[str, Any] = {
            "paired_instance_id": pair_id,
            "scenario_cell_id": scenario["scenario_cell_id"],
            "taskset_id": scenario["taskset_id"],
            "taskset_hash": scenario["taskset_hash"],
            "block_request_id": "" if block_request is None else block_request["request_id"],
            "nonblock_request_id": "" if nonblock_request is None else nonblock_request["request_id"],
            "block_status": "" if block_result is None else block_result["status"],
            "nonblock_status": "" if nonblock_result is None else nonblock_result["status"],
            "high_task_id": "" if block_effect is None else block_effect["high_task_id"],
            "low_task_id": "" if block_effect is None else block_effect["low_task_id"],
            "pair_valid": valid,
            "pair_failure_reason": ";".join(failures),
            "mechanism_activated": bool(event_count),
            "bypass_event_count": event_count,
            "bypass_episode_count": len(pair_episodes),
            "resolved_bypass_episode_count": resolved,
            "censored_bypass_episode_count": censored,
            "high_response_pairable": bool(
                valid
                and block_effect is not None and nonblock_effect is not None
                and str(block_effect.get("high_response_job_identities_json"))
                == str(nonblock_effect.get("high_response_job_identities_json"))
                and str(block_effect.get("high_response_job_identities_json")) != "[]"
            ),
            "low_response_pairable": bool(
                valid
                and block_effect is not None and nonblock_effect is not None
                and str(block_effect.get("low_response_job_identities_json"))
                == str(nonblock_effect.get("low_response_job_identities_json"))
                and str(block_effect.get("low_response_job_identities_json")) != "[]"
            ),
        }
        delta_sources = {
            "high_first_execution_delta": "high_first_execution_time",
            "high_response_mean_delta": "high_response_time_mean",
            "high_response_max_delta": "high_response_time_max",
            "high_deadline_miss_delta": "high_deadline_miss_count",
            "low_first_execution_delta": "low_first_execution_time",
            "low_response_mean_delta": "low_response_time_mean",
            "low_response_max_delta": "low_response_time_max",
            "low_deadline_miss_delta": "low_deadline_miss_count",
            "ready_but_idle_ticks_delta": "ready_but_idle_ticks",
            "total_deadline_miss_delta": "total_deadline_miss_count",
        }
        for delta_field, source_field in delta_sources.items():
            response_role = (
                "high" if delta_field.startswith("high_response_")
                else "low" if delta_field.startswith("low_response_")
                else None
            )
            row[delta_field] = (
                _pair_delta(nonblock_effect, block_effect, source_field)
                if valid and nonblock_effect is not None and block_effect is not None
                and (
                    response_role is None
                    or row[f"{response_role}_response_pairable"]
                )
                else ""
            )
        output.append(row)
    return output


def summarize_b1(
    paired_rows: Sequence[Mapping[str, Any]], episodes: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    resolved_delays = [
        _integer(row["recovery_delay_ticks"], "recovery delay")
        for row in episodes if _bool(row.get("censored")) is False
    ]
    event_count = sum(
        _integer(row["bypass_event_count"], "episode event count") for row in episodes
    )
    main_results = [
        row for row in results
        if str(row.get("scenario_kind")) == "BYPASS_STRESS"
        and str(row.get("scheduler_id")) in MAIN_SCHEDULERS
    ]
    statuses = [str(row.get("status")) for row in main_results]
    summary: Dict[str, Any] = {
        "total_pairs": len(paired_rows),
        "valid_pairs": sum(_bool(row.get("pair_valid")) is True for row in paired_rows),
        "invalid_pairs": sum(_bool(row.get("pair_valid")) is False for row in paired_rows),
        "mechanism_activated_pairs": sum(
            _bool(row.get("mechanism_activated")) is True for row in paired_rows
        ),
        "bypass_event_count": event_count,
        "bypass_episode_count": len(episodes),
        "resolved_bypass_episode_count": len(resolved_delays),
        "censored_bypass_episode_count": len(episodes) - len(resolved_delays),
        "recovery_delay_sum_ticks": sum(resolved_delays),
        "recovery_delay_mean_ticks": mean(resolved_delays) if resolved_delays else "",
        "recovery_delay_median_ticks": median(resolved_delays) if resolved_delays else "",
        "recovery_delay_max_ticks": max(resolved_delays) if resolved_delays else "",
        "recovery_denominator_zero": not resolved_delays,
        "timeout_count": statuses.count("SIM_RUNTIME_TIMEOUT"),
        "internal_error_count": statuses.count("SIM_INTERNAL_ERROR"),
        "horizon_insufficient_count": statuses.count("SIM_HORIZON_INSUFFICIENT"),
    }
    valid_pairs = [row for row in paired_rows if _bool(row.get("pair_valid")) is True]
    for field in DELTA_FIELDS:
        values = [
            value for row in valid_pairs
            if (value := _optional_number(row.get(field))) is not None
        ]
        summary[f"{field}_pair_count"] = len(values)
        summary[f"{field}_mean"] = mean(values) if values else ""
        summary[f"{field}_median"] = median(values) if values else ""
    return summary


def _trace_path(root: Path, result: Mapping[str, Any]) -> Path:
    raw = str(result.get("retained_trace_path", "")).strip()
    if not raw:
        raise B1AnalysisError(
            f"missing retained B1 trace for {result.get('request_id')}"
        )
    supplied = Path(raw)
    candidates = [supplied, root / supplied, root.parent / supplied]
    candidates.append(root / "retained_traces" / supplied.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise B1AnalysisError(
        f"retained B1 trace does not exist for {result.get('request_id')}: {raw}"
    )


def write_ext1b_b1_outputs(root: Path, config: Mapping[str, Any]) -> Dict[str, int]:
    """Rebuild all four B1 tables from raw CSVs, terminals, and traces."""

    if str(config.get("scenario", {}).get("kind")) != "BYPASS_STRESS":
        return {
            "b1_episode_rows": 0, "b1_task_effect_rows": 0,
            "b1_paired_effect_rows": 0, "b1_summary_rows": 0,
        }
    requests = read_csv(root / "simulation_requests.csv")
    results = read_csv(root / "simulation_results.csv")
    task_rows = read_csv(root / "task_outcomes.csv")
    scenarios = read_csv(root / "scenario_instances.csv")
    request_index = {str(row["request_id"]): row for row in requests}
    task_by_request: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in task_rows:
        task_by_request[str(row["request_id"])].append(row)
    structure_by_pair: Dict[str, Mapping[str, Any]] = {}
    for row in scenarios:
        pair_id = str(row["paired_instance_id"])
        if pair_id in structure_by_pair:
            raise B1AnalysisError(f"duplicate B1 scenario instance: {pair_id}")
        try:
            structure = json.loads(str(row["structure_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise B1AnalysisError(f"invalid B1 structure for {pair_id}") from exc
        if not isinstance(structure, dict):
            raise B1AnalysisError(f"B1 structure is not an object for {pair_id}")
        high_task_id = str(structure.get("high_task_id", ""))
        low_task_id = str(structure.get("low_task_id", ""))
        if not high_task_id or not low_task_id or high_task_id == low_task_id:
            raise B1AnalysisError(f"B1 structure has invalid high/low IDs for {pair_id}")
        structure_by_pair[pair_id] = structure

    task_effects: list[Dict[str, Any]] = []
    episodes: list[Dict[str, Any]] = []
    for result in results:
        if str(result.get("scenario_kind")) != "BYPASS_STRESS":
            continue
        scheduler = str(result.get("scheduler_id"))
        if scheduler not in MAIN_SCHEDULERS:
            continue
        request_id = str(result.get("request_id"))
        request = request_index.get(request_id)
        if request is None:
            raise B1AnalysisError(f"B1 result has no request: {request_id}")
        pair_id = str(request["paired_instance_id"])
        structure = structure_by_pair.get(pair_id)
        if structure is None:
            raise B1AnalysisError(f"B1 request has no scenario structure: {request_id}")
        terminal_path = root / "simulation_terminal_results" / f"{request_id}.json"
        if not terminal_path.is_file():
            raise B1AnalysisError(f"B1 result has no terminal record: {request_id}")
        execution = load_simulation_terminal(terminal_path)
        jobs = [job.row() for job in execution.result.jobs]
        effect = build_b1_task_effect_row(
            request, result, task_by_request.get(request_id, []), jobs, structure,
        )
        task_effects.append(effect)

        status = str(result.get("status"))
        if scheduler == NONBLOCK_SCHEDULER and status in TRACE_AUDITABLE_STATUSES:
            known_task_ids = {str(row["task_id"]) for row in task_by_request[request_id]}
            observed = reconstruct_bypass_episodes(
                _trace_path(root, result), request=request,
                high_task_id=str(structure.get("high_task_id", "")),
                low_task_id=str(structure.get("low_task_id", "")),
                known_task_ids=known_task_ids, terminal_status=status,
            )
            expected_events = _optional_number(result.get("bypass_count"))
            actual_events = sum(int(row["bypass_event_count"]) for row in observed)
            if expected_events is None or expected_events != actual_events:
                raise B1AnalysisError(
                    f"B1 bypass event count mismatch for {request_id}: "
                    f"result={expected_events}, trace={actual_events}"
                )
            episodes.extend(observed)

    paired = build_b1_paired_effects(
        scenarios, requests, results, task_effects, episodes,
    )
    summary = summarize_b1(paired, episodes, results)
    write_csv(root / "b1_bypass_episodes.csv", B1_EPISODE_COLUMNS, episodes)
    write_csv(root / "b1_task_effects.csv", B1_TASK_EFFECT_COLUMNS, task_effects)
    write_csv(root / "b1_paired_effects.csv", B1_PAIRED_EFFECT_COLUMNS, paired)
    write_csv(root / "b1_summary.csv", B1_SUMMARY_COLUMNS, [summary])
    return {
        "b1_episode_rows": len(episodes),
        "b1_task_effect_rows": len(task_effects),
        "b1_paired_effect_rows": len(paired),
        "b1_summary_rows": 1,
    }
