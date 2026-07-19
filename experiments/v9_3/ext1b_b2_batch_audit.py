"""Retained-trace observation contract for EXT-1B2 ASAP batch semantics.

This module deliberately does not consume terminal outcomes.  It reconstructs
the ASAP-Sync decision, its pre-existing continuations, and the task ``scheduled``
events that follow the decision at the same tick.
"""

from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


B2_STATE_NO_BATCH = "B2_STATE_NO_BATCH"
B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH = (
    "B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH"
)
B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT = (
    "B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT"
)
B2_STATE_ILLEGAL_PARTIAL_LAUNCH = "B2_STATE_ILLEGAL_PARTIAL_LAUNCH"
B2_STATE_UNCLASSIFIABLE = "B2_STATE_UNCLASSIFIABLE"

# Native schedulers compare joules with 1e-9 J.  Trace energy fields are mJ.
NATIVE_ENERGY_EPSILON_MJ = 1e-6
TRACE_ENERGY_ABS_TOLERANCE_MJ = 1e-6


class B2BatchTraceError(RuntimeError):
    """The retained trace is not a usable schema-v2 B2 observation."""


def _tick(value: Any, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise B2BatchTraceError(f"{label} must be an integer tick")
    try:
        parsed = Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise B2BatchTraceError(f"{label} must be an integer tick") from exc
    if parsed.denominator != 1:
        raise B2BatchTraceError(f"{label} must be an integer tick")
    return parsed.numerator


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise B2BatchTraceError(f"{label} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise B2BatchTraceError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise B2BatchTraceError(f"{label} must be finite")
    return result


def _integer(value: Any, label: str) -> int:
    result = _tick(value, label)
    if result < 0:
        raise B2BatchTraceError(f"{label} must be non-negative")
    return result


def _load_trace(path: Path | str) -> Mapping[str, Any]:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise B2BatchTraceError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=no_duplicates)
    except (OSError, json.JSONDecodeError) as exc:
        raise B2BatchTraceError(f"cannot read B2 trace: {exc}") from exc
    if not isinstance(document, dict):
        raise B2BatchTraceError("B2 trace must be a JSON object")
    return document


def _job_id(job: Mapping[str, Any], label: str) -> str:
    name = job.get("task_name")
    if not isinstance(name, str) or not name:
        raise B2BatchTraceError(f"{label} has no task_name")
    arrival = _tick(job.get("arrival_time"), f"{label} arrival_time")
    return f"{name}@{arrival}"


def _task_id(job_id: str) -> str:
    name = job_id.rsplit("@", 1)[0]
    return name[len("v93_task_"):] if name.startswith("v93_task_") else name


def _jobs(
    value: Any, label: str, errors: list[str],
) -> tuple[list[str], Dict[str, float]]:
    if not isinstance(value, list):
        errors.append(f"{label}_not_array")
        return [], {}
    identifiers: list[str] = []
    energies: Dict[str, float] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            errors.append(f"{label}_{index}_not_object")
            continue
        try:
            identifier = _job_id(raw, f"{label}[{index}]")
        except B2BatchTraceError:
            errors.append(f"{label}_{index}_invalid_identity")
            continue
        if identifier in identifiers:
            errors.append(f"{label}_duplicate_identity:{identifier}")
            continue
        identifiers.append(identifier)
        try:
            energies[identifier] = _finite(
                raw.get("task_unit_energy_mJ"),
                f"{label}[{index}] task_unit_energy_mJ",
            )
        except B2BatchTraceError:
            errors.append(f"{label}_missing_energy:{identifier}")
    return identifiers, energies


def _event_job_id(event: Mapping[str, Any]) -> str | None:
    try:
        return _job_id(event, "task event")
    except B2BatchTraceError:
        return None


def _job_payloads(value: Any) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    if not isinstance(value, list):
        return result
    for raw in value:
        if not isinstance(raw, dict):
            continue
        try:
            result[_job_id(raw, "trace job")] = raw
        except B2BatchTraceError:
            continue
    return result


def _validate_job_payloads(
    observed_value: Any,
    observed_ids: Sequence[str],
    expected_payloads: Mapping[str, Mapping[str, Any]],
    label: str,
    errors: list[str],
) -> None:
    observed_payloads = _job_payloads(observed_value)
    for identifier in observed_ids:
        observed = observed_payloads.get(identifier)
        expected = expected_payloads.get(identifier)
        if observed is None or expected is None:
            errors.append(f"{label}_job_payload_identity_mismatch:{identifier}")
            continue
        for field in (
            "priority", "task_unit_energy_mJ", "remaining_time_ms",
            "absolute_deadline",
        ):
            try:
                observed_number = _finite(
                    observed.get(field), f"{label} {identifier} {field}",
                )
                expected_number = _finite(
                    expected.get(field), f"ready_jobs {identifier} {field}",
                )
            except B2BatchTraceError:
                errors.append(f"{label}_job_payload_missing:{identifier}:{field}")
                continue
            if not _isclose(observed_number, expected_number):
                errors.append(f"{label}_job_payload_mismatch:{identifier}:{field}")


def _running_before_events(
    events: Sequence[Mapping[str, Any]],
) -> list[set[str]]:
    running: set[str] = set()
    snapshots: list[set[str]] = []
    for event in events:
        snapshots.append(set(running))
        event_type = event.get("event_type")
        identifier = _event_job_id(event)
        if identifier is None:
            continue
        if event_type == "scheduled":
            running.add(identifier)
        elif event_type in {
            "descheduled", "end_instance", "dline_miss", "kill",
        }:
            running.discard(identifier)
    return snapshots


def _same_tick_following_events(
    events: Sequence[Mapping[str, Any]], position: int, tick: int,
    decision_scheduler: str,
) -> list[tuple[int, Mapping[str, Any]]]:
    result = []
    for index in range(position + 1, len(events)):
        event = events[index]
        try:
            event_tick = _tick(event.get("time"), f"event {index} time")
        except B2BatchTraceError:
            continue
        if event_tick != tick:
            if event_tick > tick:
                break
            continue
        if (
            event.get("event_type") == "scheduler_decision"
            and event.get("scheduler") == decision_scheduler
        ):
            break
        result.append((index, event))
    return result


def _isclose(left: float, right: float) -> bool:
    return math.isclose(
        left, right, rel_tol=1e-6,
        abs_tol=TRACE_ENERGY_ABS_TOLERANCE_MJ,
    )


def _validate_document(
    document: Mapping[str, Any], expected_scheduler: str,
) -> list[Mapping[str, Any]]:
    if document.get("trace_schema_version") != 2:
        raise B2BatchTraceError("B2 auditor requires trace schema version 2")
    if document.get("configured_scheduler") != expected_scheduler:
        raise B2BatchTraceError(
            "B2 trace scheduler mismatch: expected "
            f"{expected_scheduler}, got {document.get('configured_scheduler')!r}"
        )
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise B2BatchTraceError("B2 trace has no run_id")
    taskset_hash = document.get("taskset_semantic_hash")
    if (
        not isinstance(taskset_hash, str)
        or len(taskset_hash) != 64
        or any(character not in "0123456789abcdef" for character in taskset_hash)
    ):
        raise B2BatchTraceError("B2 trace has invalid taskset_semantic_hash")
    events = document.get("events")
    if not isinstance(events, list) or not all(
        isinstance(event, dict) for event in events
    ):
        raise B2BatchTraceError("B2 trace events must be an object array")
    return list(events)


def audit_asap_sync_document(
    document: Mapping[str, Any],
    *,
    processors: int,
    request_id: str | None = None,
    pair_id: str = "UNAVAILABLE",
) -> list[Dict[str, Any]]:
    """Classify each retained ASAP-Sync scheduler decision.

    ``candidate_job_ids`` are jobs in the frozen active top-M group that were
    not already executing before the decision.  The source variable named
    ``idle_core_batch`` has exactly this meaning; it may include a replacement
    for an unselected running job, so pre-decision idle cores do not cap it.
    """

    if isinstance(processors, bool) or not isinstance(processors, int) or processors <= 0:
        raise ValueError("processors must be a positive integer")
    events = _validate_document(document, "gpfp_asap_sync")
    snapshots = _running_before_events(events)
    resolved_request_id = request_id or str(document.get("run_id", "UNAVAILABLE"))
    taskset_hash = str(document.get("taskset_semantic_hash", "UNAVAILABLE"))
    rows: list[Dict[str, Any]] = []

    for position, event in enumerate(events):
        if (
            event.get("event_type") != "scheduler_decision"
            or event.get("scheduler") != "ASAP-Sync"
        ):
            continue
        errors: list[str] = []
        violations: list[str] = []
        tick = _tick(event.get("time"), f"event {position} time")
        ready_ids, ready_energy = _jobs(event.get("ready_jobs"), "ready_jobs", errors)
        raw_selected_ids, _ = _jobs(
            event.get("selected_jobs"), "selected_jobs", errors,
        )
        desired_ids = ready_ids[:min(processors, len(ready_ids))]
        running_before = snapshots[position]
        continuation_ids = [job for job in desired_ids if job in running_before]
        candidate_ids = [job for job in desired_ids if job not in running_before]
        candidate_set = set(candidate_ids)
        continuation_set = set(continuation_ids)
        desired_set = set(desired_ids)
        selected_candidate_ids = [
            job for job in raw_selected_ids if job not in continuation_set
        ]

        following = _same_tick_following_events(
            events, position, tick, "ASAP-Sync",
        )
        scheduled_event_ids: list[int] = []
        launched_ids: list[str] = []
        block_events: list[tuple[int, Mapping[str, Any]]] = []
        candidate_wait_events: list[tuple[int, Mapping[str, Any]]] = []
        for event_index, nested in following:
            if nested.get("event_type") == "scheduled":
                identifier = _event_job_id(nested)
                if identifier is not None and identifier not in running_before:
                    scheduled_event_ids.append(event_index)
                    launched_ids.append(identifier)
            elif (
                nested.get("event_type") == "sync_batch_block"
                and nested.get("scheduler") == "ASAP-Sync"
            ):
                block_events.append((event_index, nested))
            elif (
                nested.get("event_type") == "sync_batch_candidate_wait"
                and nested.get("scheduler") == "ASAP-Sync"
            ):
                candidate_wait_events.append((event_index, nested))
        if len(block_events) > 1:
            errors.append("multiple_sync_batch_block_events")
        if len(candidate_wait_events) > 1:
            errors.append("multiple_sync_batch_candidate_wait_events")

        available: float | None
        try:
            available = _finite(event.get("available_energy_mJ"), "available energy")
        except B2BatchTraceError:
            available = None
            errors.append("missing_available_energy")
        missing_desired_energy = [job for job in desired_ids if job not in ready_energy]
        if missing_desired_energy:
            errors.append("missing_batch_job_energy")
        whole_required = (
            sum(ready_energy[job] for job in desired_ids)
            if not missing_desired_energy else None
        )
        whole_affordable = (
            available + NATIVE_ENERGY_EPSILON_MJ >= whole_required
            if available is not None and whole_required is not None else None
        )
        energy_margin = (
            available - whole_required
            if available is not None and whole_required is not None else None
        )
        per_job_affordable = {
            job: available + NATIVE_ENERGY_EPSILON_MJ >= energy
            for job, energy in ready_energy.items()
            if job in desired_set and available is not None
        }
        prefix_length = 0
        if available is not None and whole_required is not None:
            prefix_energy = 0.0
            for job in desired_ids:
                prefix_energy += ready_energy[job]
                if available + NATIVE_ENERGY_EPSILON_MJ >= prefix_energy:
                    prefix_length += 1
                else:
                    break
        feasible_subset = any(per_job_affordable.values())
        continuation_required = (
            sum(ready_energy[job] for job in continuation_ids)
            if not any(job not in ready_energy for job in continuation_ids)
            else None
        )
        candidate_required = (
            sum(ready_energy[job] for job in candidate_ids)
            if not any(job not in ready_energy for job in candidate_ids)
            else None
        )
        residual_after_continuation = (
            max(0.0, available - continuation_required)
            if available is not None and continuation_required is not None
            else None
        )
        continuation_affordable = (
            available + NATIVE_ENERGY_EPSILON_MJ >= continuation_required
            if available is not None and continuation_required is not None
            else None
        )
        candidate_affordable_after_continuation = {
            job: residual_after_continuation + NATIVE_ENERGY_EPSILON_MJ >= ready_energy[job]
            for job in candidate_ids
            if residual_after_continuation is not None and job in ready_energy
        }
        all_new_candidates_affordable = (
            all(candidate_affordable_after_continuation.values())
            if candidate_ids and len(candidate_affordable_after_continuation) == len(candidate_ids)
            else None
        )
        feasible_new_candidate_subset = (
            any(candidate_affordable_after_continuation.values())
            if candidate_ids and len(candidate_affordable_after_continuation) == len(candidate_ids)
            else None
        )

        block_present = bool(block_events)
        block_feasible: bool | None = None
        block_event_ids: list[int] = []
        if block_events:
            block_position, block = block_events[0]
            block_event_ids.append(block_position)
            block_ids, _ = _jobs(block.get("batch_tasks"), "batch_tasks", errors)
            if len(block_ids) != len(desired_ids) or set(block_ids) != desired_set:
                errors.append("blocked_event_candidate_identity_mismatch")
            try:
                block_required = _finite(
                    block.get("batch_required_energy_mJ"), "block required energy",
                )
                if whole_required is not None and not _isclose(
                    block_required, whole_required,
                ):
                    errors.append("blocked_event_required_energy_mismatch")
            except B2BatchTraceError:
                errors.append("blocked_event_missing_required_energy")
            try:
                block_available = _finite(
                    block.get("available_energy_mJ"), "block available energy",
                )
                if available is not None and not _isclose(block_available, available):
                    errors.append("blocked_event_available_energy_mismatch")
            except B2BatchTraceError:
                errors.append("blocked_event_missing_available_energy")
            raw_feasible = block.get("feasible_subset_exists")
            if isinstance(raw_feasible, bool):
                block_feasible = raw_feasible
                if block_feasible != feasible_subset:
                    errors.append("blocked_event_feasible_subset_mismatch")
            else:
                errors.append("blocked_event_missing_feasible_subset")

        candidate_wait_present = bool(candidate_wait_events)
        candidate_wait_event_ids: list[int] = []
        candidate_wait_valid = False
        if candidate_wait_events:
            wait_position, wait = candidate_wait_events[0]
            candidate_wait_event_ids.append(wait_position)
            wait_error_start = len(errors)
            if wait.get("reason") != (
                "continuation_preserved_new_candidate_batch_energy_insufficient"
            ):
                errors.append("candidate_wait_reason_mismatch")
            if event.get("decision_reason") != "sync_batch_energy_insufficient":
                errors.append("candidate_wait_decision_reason_mismatch")
            expected_payloads = _job_payloads(event.get("ready_jobs"))
            wait_arrays = (
                ("active_top_m_tasks", desired_ids),
                ("continuation_tasks", continuation_ids),
                ("new_candidate_tasks", candidate_ids),
                ("selected_tasks", raw_selected_ids),
            )
            parsed_wait_ids: Dict[str, list[str]] = {}
            for field, expected_ids in wait_arrays:
                observed_ids, _ = _jobs(wait.get(field), field, errors)
                parsed_wait_ids[field] = observed_ids
                if observed_ids != expected_ids:
                    errors.append(f"candidate_wait_{field}_identity_mismatch")
                _validate_job_payloads(
                    wait.get(field), observed_ids, expected_payloads,
                    f"candidate_wait_{field}", errors,
                )
            observed_continuations = set(parsed_wait_ids["continuation_tasks"])
            observed_candidates = set(parsed_wait_ids["new_candidate_tasks"])
            if observed_continuations.intersection(observed_candidates):
                errors.append("candidate_wait_continuation_candidate_overlap")
            if observed_continuations.union(observed_candidates) != set(
                parsed_wait_ids["active_top_m_tasks"]
            ):
                errors.append("candidate_wait_active_top_m_partition_mismatch")
            count_fields = (
                ("active_top_m_count", len(desired_ids)),
                ("continuation_count", len(continuation_ids)),
                ("new_candidate_count", len(candidate_ids)),
                ("selected_count", len(raw_selected_ids)),
            )
            for field, expected_count in count_fields:
                try:
                    if _integer(wait.get(field), field) != expected_count:
                        errors.append(f"candidate_wait_{field}_mismatch")
                except B2BatchTraceError:
                    errors.append(f"candidate_wait_{field}_missing")
            energy_fields = (
                ("active_top_m_required_energy_mJ", whole_required),
                ("continuation_required_energy_mJ", continuation_required),
                ("new_candidate_required_energy_mJ", candidate_required),
                ("available_energy_before_decision_mJ", available),
                (
                    "residual_energy_after_continuation_reservation_mJ",
                    residual_after_continuation,
                ),
            )
            for field, expected_energy in energy_fields:
                try:
                    observed_energy = _finite(wait.get(field), field)
                    if expected_energy is None or not _isclose(
                        observed_energy, expected_energy,
                    ):
                        errors.append(f"candidate_wait_{field}_mismatch")
                except B2BatchTraceError:
                    errors.append(f"candidate_wait_{field}_missing")
            try:
                observed_epsilon = _finite(
                    wait.get("native_affordability_epsilon_mJ"),
                    "native_affordability_epsilon_mJ",
                )
                if not math.isclose(
                    observed_epsilon, NATIVE_ENERGY_EPSILON_MJ,
                    rel_tol=1e-9, abs_tol=1e-12,
                ):
                    errors.append(
                        "candidate_wait_native_affordability_epsilon_mJ_mismatch"
                    )
            except B2BatchTraceError:
                errors.append(
                    "candidate_wait_native_affordability_epsilon_mJ_missing"
                )
            boolean_fields = (
                ("whole_active_top_m_affordable", whole_affordable),
                (
                    "all_new_candidates_affordable_after_continuation",
                    all_new_candidates_affordable,
                ),
                (
                    "feasible_new_candidate_subset_exists",
                    feasible_new_candidate_subset,
                ),
            )
            for field, expected_boolean in boolean_fields:
                observed_boolean = wait.get(field)
                if not isinstance(observed_boolean, bool):
                    errors.append(f"candidate_wait_{field}_missing")
                elif expected_boolean is None or observed_boolean != expected_boolean:
                    errors.append(f"candidate_wait_{field}_mismatch")
            if not continuation_ids:
                errors.append("candidate_wait_has_no_continuation")
            if not candidate_ids:
                errors.append("candidate_wait_has_no_new_candidate")
            if raw_selected_ids != continuation_ids:
                errors.append("candidate_wait_selected_not_continuations")
            if whole_affordable is not False:
                errors.append("candidate_wait_whole_group_not_unaffordable")
            if continuation_affordable is not True:
                errors.append("candidate_wait_continuation_not_affordable")
            candidate_wait_valid = len(errors) == wait_error_start

        if block_present and candidate_wait_present:
            errors.append("conflicting_sync_wait_event_subtypes")

        if (
            candidate_ids
            and whole_affordable is False
            and feasible_subset
            and not block_present
            and not candidate_wait_present
        ):
            errors.append("missing_sync_batch_block_evidence")

        q = len(candidate_ids)
        selected_count = len(selected_candidate_ids)
        launch_count = len(launched_ids)
        if q == 0:
            if selected_candidate_ids or launched_ids:
                violations.append("new_job_selected_or_launched_without_candidate")
        else:
            if 0 < selected_count < q:
                violations.append("partial_selection_count")
            if selected_candidate_ids and set(selected_candidate_ids) != candidate_set:
                violations.append("selected_candidate_identity_mismatch")
            if 0 < launch_count < q:
                violations.append("partial_actual_launch_count")
            if launched_ids and set(launched_ids) != candidate_set:
                violations.append("actual_launch_candidate_identity_mismatch")
            if set(selected_candidate_ids) == candidate_set and set(launched_ids) != candidate_set:
                violations.append("complete_selection_incomplete_same_tick_launch")
            if whole_affordable is False and candidate_set.intersection(launched_ids):
                violations.append("unaffordable_batch_member_launched")
        if any(job not in desired_set for job in raw_selected_ids):
            violations.append("raw_selected_identity_outside_active_top_m")

        if violations:
            state = B2_STATE_ILLEGAL_PARTIAL_LAUNCH
        elif errors:
            state = B2_STATE_UNCLASSIFIABLE
        elif q == 0:
            state = B2_STATE_NO_BATCH
        elif whole_affordable is True:
            if (
                len(raw_selected_ids) == len(desired_ids)
                and set(raw_selected_ids) == desired_set
                and selected_count == q
                and set(selected_candidate_ids) == candidate_set
                and launch_count == q
                and set(launched_ids) == candidate_set
                and not block_present
            ):
                state = B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH
            else:
                state = B2_STATE_UNCLASSIFIABLE
        elif whole_affordable is False:
            if not feasible_subset:
                state = B2_STATE_UNCLASSIFIABLE
            elif (
                block_present
                and not candidate_wait_present
                and not raw_selected_ids
                and selected_count == 0
                and launch_count == 0
                and block_feasible is True
            ):
                state = B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT
            elif (
                candidate_wait_present
                and not block_present
                and candidate_wait_valid
                and raw_selected_ids == continuation_ids
                and selected_count == 0
                and launch_count == 0
            ):
                state = B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT
            else:
                state = B2_STATE_UNCLASSIFIABLE
        else:
            state = B2_STATE_UNCLASSIFIABLE

        rows.append({
            "request_id": resolved_request_id,
            "pair_id": pair_id,
            "taskset_semantic_hash": taskset_hash,
            "scheduler_id": "gpfp_asap_sync",
            "tick": tick,
            "idle_core_count": max(0, processors - len(running_before)),
            "active_top_m_job_ids": desired_ids,
            "continuation_job_ids": continuation_ids,
            "candidate_job_ids": candidate_ids,
            "candidate_count": q,
            "candidate_task_ids": [_task_id(job) for job in candidate_ids],
            "per_job_energy_mJ": {
                job: ready_energy[job] for job in desired_ids if job in ready_energy
            },
            "per_job_affordable_vector": per_job_affordable,
            "new_candidate_affordable_after_continuation_vector": (
                candidate_affordable_after_continuation
            ),
            "affordable_prefix_length": prefix_length,
            "whole_batch_required_energy_mJ": whole_required,
            "continuation_required_energy_mJ": continuation_required,
            "new_candidate_required_energy_mJ": candidate_required,
            "available_energy_mJ": available,
            "residual_energy_after_continuation_reservation_mJ": (
                residual_after_continuation
            ),
            "energy_margin_mJ": energy_margin,
            "native_energy_epsilon_mJ": NATIVE_ENERGY_EPSILON_MJ,
            "whole_batch_affordable": whole_affordable,
            "raw_selected_job_ids": raw_selected_ids,
            "selected_job_ids": selected_candidate_ids,
            "selected_count": selected_count,
            "actually_launched_job_ids": launched_ids,
            "actual_launch_count": launch_count,
            "sync_batch_block_present": block_present,
            "sync_batch_candidate_wait_present": candidate_wait_present,
            "feasible_subset_exists": (
                block_feasible if block_present else feasible_subset
            ),
            "partial_launch_violation": bool(violations),
            "partial_launch_violation_reasons": violations,
            "classified_state": state,
            "classification_errors": errors,
            "decision_reason": event.get("decision_reason"),
            "evidence_event_ids": {
                "scheduler_decision": [position],
                "sync_batch_block": block_event_ids,
                "sync_batch_candidate_wait": candidate_wait_event_ids,
                "scheduled": scheduled_event_ids,
            },
        })
    return rows


def audit_asap_sync_trace(
    trace_path: Path | str, **kwargs: Any,
) -> list[Dict[str, Any]]:
    return audit_asap_sync_document(_load_trace(trace_path), **kwargs)


def _block_decision_at_tick(
    document: Mapping[str, Any], tick: int,
) -> tuple[int, Mapping[str, Any], set[str], list[tuple[int, Mapping[str, Any]]]] | None:
    events = _validate_document(document, "gpfp_asap_block")
    snapshots = _running_before_events(events)
    for position, event in enumerate(events):
        if (
            event.get("event_type") == "scheduler_decision"
            and event.get("scheduler") == "ASAP-Block"
            and _tick(event.get("time"), f"event {position} time") == tick
        ):
            return (
                position, event, snapshots[position],
                _same_tick_following_events(
                    events, position, tick, "ASAP-Block",
                ),
            )
    return None


def audit_asap_block_pair_control(
    sync_rows: Sequence[Mapping[str, Any]],
    block_document: Mapping[str, Any],
    *,
    processors: int,
    expected_min_prefix_length: int,
) -> list[Dict[str, Any]]:
    """Verify the ASAP-Block positive-prefix control for Sync wait rows."""

    if expected_min_prefix_length <= 0:
        raise ValueError("expected_min_prefix_length must be positive")
    controls: list[Dict[str, Any]] = []
    block_hash = str(block_document.get("taskset_semantic_hash", "UNAVAILABLE"))
    for sync in sync_rows:
        if sync.get("classified_state") != B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT:
            continue
        errors: list[str] = []
        tick = int(sync["tick"])
        found = _block_decision_at_tick(block_document, tick)
        if found is None:
            controls.append({
                "pair_id": sync.get("pair_id", "UNAVAILABLE"),
                "tick": tick,
                "control_passed": False,
                "control_errors": ["missing_asap_block_decision"],
                "evidence_event_ids": {},
            })
            continue
        position, decision, running_before, following = found
        ready_ids, ready_energy = _jobs(decision.get("ready_jobs"), "ready_jobs", errors)
        selected_ids, _ = _jobs(
            decision.get("selected_jobs"), "selected_jobs", errors,
        )
        group_ids = list(sync.get("active_top_m_job_ids", []))
        if ready_ids[:len(group_ids)] != group_ids:
            errors.append("asap_block_priority_order_mismatch")
        if block_hash != sync.get("taskset_semantic_hash"):
            errors.append("taskset_semantic_hash_mismatch")
        try:
            available = _finite(
                decision.get("available_energy_mJ"), "block available energy",
            )
        except B2BatchTraceError:
            available = None
            errors.append("missing_asap_block_available_energy")
        sync_available = sync.get("available_energy_mJ")
        if (
            available is not None and isinstance(sync_available, (int, float))
            and not _isclose(available, float(sync_available))
        ):
            errors.append("paired_available_energy_mismatch")
        selected_is_prefix = selected_ids == ready_ids[:len(selected_ids)]
        if not selected_is_prefix:
            errors.append("asap_block_selection_is_not_priority_prefix")
        new_selected = [job for job in selected_ids if job not in running_before]
        launched = []
        scheduled_positions = []
        for event_index, event in following:
            if event.get("event_type") != "scheduled":
                continue
            identifier = _event_job_id(event)
            if identifier is not None and identifier not in running_before:
                launched.append(identifier)
                scheduled_positions.append(event_index)
        if not new_selected:
            errors.append("asap_block_has_no_positive_new_prefix")
        if launched != new_selected:
            errors.append("asap_block_selected_launch_mismatch")
        missing_energy = [job for job in selected_ids if job not in ready_energy]
        prefix_required = (
            sum(ready_energy[job] for job in selected_ids)
            if not missing_energy else None
        )
        prefix_affordable = (
            available + NATIVE_ENERGY_EPSILON_MJ >= prefix_required
            if available is not None and prefix_required is not None else False
        )
        if not prefix_affordable:
            errors.append("asap_block_prefix_unaffordable")
        next_blocked_or_boundary = False
        if len(selected_ids) >= expected_min_prefix_length:
            next_blocked_or_boundary = True
        elif (
            available is not None and prefix_required is not None
            and len(selected_ids) < min(processors, len(ready_ids))
        ):
            next_id = ready_ids[len(selected_ids)]
            if next_id in ready_energy:
                next_blocked_or_boundary = (
                    prefix_required + ready_energy[next_id]
                    > available + NATIVE_ENERGY_EPSILON_MJ
                )
        if not next_blocked_or_boundary:
            errors.append("asap_block_next_job_not_blocked_and_p_boundary_not_met")
        controls.append({
            "pair_id": sync.get("pair_id", "UNAVAILABLE"),
            "tick": tick,
            "taskset_semantic_hash": block_hash,
            "scheduler_id": "gpfp_asap_block",
            "ready_job_ids": ready_ids,
            "selected_job_ids": selected_ids,
            "actually_launched_job_ids": launched,
            "prefix_required_energy_mJ": prefix_required,
            "available_energy_mJ": available,
            "prefix_affordable": prefix_affordable,
            "expected_min_prefix_length": expected_min_prefix_length,
            "next_job_blocked_or_p_boundary_met": next_blocked_or_boundary,
            "control_passed": not errors,
            "control_errors": errors,
            "evidence_event_ids": {
                "scheduler_decision": [position],
                "scheduled": scheduled_positions,
            },
        })
    return controls


def audit_asap_block_pair_trace(
    sync_rows: Sequence[Mapping[str, Any]],
    block_trace_path: Path | str,
    **kwargs: Any,
) -> list[Dict[str, Any]]:
    return audit_asap_block_pair_control(
        sync_rows, _load_trace(block_trace_path), **kwargs,
    )
