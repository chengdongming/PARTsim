"""Fail-closed auditor for EXT-1B2 ASAP-SYNC batch semantics.

The auditor consumes schema-v2 semantic traces with exact-energy strings,
classifies every batch opportunity, checks atomic launch/wait behavior, and
matches comparable ASAP-BLOCK pre-decision states.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from . import ext1b_b2_batch_trace as trace


B2_STATE_NO_BATCH = "B2_STATE_NO_BATCH"
B2_STATE_NOT_APPLICABLE = "B2_STATE_NOT_APPLICABLE"
B2_STATE_CONTINUATION_ONLY = "B2_STATE_CONTINUATION_ONLY"
B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH = (
    "B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH"
)
B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER = (
    "B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER"
)
B2_STATE_CONTINUATION_CANDIDATE_WAIT = (
    "B2_STATE_CONTINUATION_CANDIDATE_WAIT"
)
B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER = (
    "B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER"
)
B2_STATE_ILLEGAL_PARTIAL_LAUNCH = "B2_STATE_ILLEGAL_PARTIAL_LAUNCH"
B2_STATE_ILLEGAL_TRANSITION = "B2_STATE_ILLEGAL_TRANSITION"
B2_STATE_UNCLASSIFIABLE = "B2_STATE_UNCLASSIFIABLE"

ALLOWED_STATES = (
    B2_STATE_NO_BATCH,
    B2_STATE_NOT_APPLICABLE,
    B2_STATE_CONTINUATION_ONLY,
    B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH,
    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER,
    B2_STATE_CONTINUATION_CANDIDATE_WAIT,
    B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER,
    B2_STATE_ILLEGAL_PARTIAL_LAUNCH,
    B2_STATE_ILLEGAL_TRANSITION,
    B2_STATE_UNCLASSIFIABLE,
)

ATOMIC_WAIT_STATES = frozenset({
    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER,
    B2_STATE_CONTINUATION_CANDIDATE_WAIT,
})
ATOMIC_OPPORTUNITY_STATES = frozenset({
    B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH,
    *ATOMIC_WAIT_STATES,
})

CONTROL_STATUS_ELIGIBLE_MATCHED_STATE = "ELIGIBLE_MATCHED_STATE"
CONTROL_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
CONTROL_STATUS_EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"

NATIVE_ENERGY_EPSILON_MJ = trace.NATIVE_ENERGY_EPSILON_MJ
PREDECISION_FINGERPRINT_DOMAIN = (
    "ASAP_BLOCK:V9.3:EXT1B:B2:PREDECISION_STATE_FINGERPRINT:v1"
)

_JOB_ARRAY_FIELDS = (
    "ready_jobs", "selected_jobs", "batch_tasks", "active_top_m_tasks",
    "continuation_tasks", "new_candidate_tasks", "selected_tasks",
)
_EVENT_ENERGY_FIELDS = (
    "available_energy_mJ",
    "batch_required_energy_mJ",
    "active_top_m_required_energy_mJ",
    "continuation_required_energy_mJ",
    "new_candidate_required_energy_mJ",
    "available_energy_before_decision_mJ",
    "residual_energy_after_continuation_reservation_mJ",
    "native_affordability_epsilon_mJ",
)
_GENERAL_BLOCK_ERROR_PREFIXES = (
    "blocked_event_", "multiple_sync_batch_block_events",
    "missing_sync_batch_block_evidence",
)
_CANDIDATE_ERROR_PREFIXES = (
    "candidate_wait_", "multiple_sync_batch_candidate_wait_events",
)
_IRREVERSIBLE_LIFECYCLE_COMPONENTS = ("completed", "missed", "killed")
_IRREVERSIBLE_LIFECYCLE_EVENTS = {
    "end_instance": "completed",
    "dline_miss": "missed",
    "kill": "killed",
}


class B2BatchAuditError(trace.B2BatchTraceError):
    """B2 trace or exact-energy evidence is malformed."""


def _exact_value(value: Any, label: str) -> float:
    if not isinstance(value, str) or not value:
        raise B2BatchAuditError(f"{label} exact energy must be text")
    return trace._finite(value, label)


def _normalize_exact_energy(document: Mapping[str, Any]) -> Dict[str, Any]:
    """Replace display numbers with required exact-energy strings."""

    normalized = deepcopy(document)
    events = normalized.get("events")
    if not isinstance(events, list):
        return normalized
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        if event.get("event_type") not in {
            "scheduler_decision",
            "sync_batch_block",
            "sync_batch_candidate_wait",
        }:
            continue
        for field in _EVENT_ENERGY_FIELDS:
            exact_field = f"{field}_exact"
            if field not in event:
                continue
            if exact_field not in event:
                raise B2BatchAuditError(
                    f"event {event_index} missing required {exact_field}"
                )
            event[field] = _exact_value(
                event[exact_field], f"event {event_index} {exact_field}",
            )
        for array_field in _JOB_ARRAY_FIELDS:
            jobs = event.get(array_field)
            if not isinstance(jobs, list):
                continue
            for job_index, job in enumerate(jobs):
                if not isinstance(job, dict):
                    continue
                if "task_unit_energy_mJ" not in job:
                    continue
                if "task_unit_energy_mJ_exact" not in job:
                    raise B2BatchAuditError(
                        f"event {event_index} {array_field}[{job_index}] "
                        "missing required task_unit_energy_mJ_exact"
                    )
                job["task_unit_energy_mJ"] = _exact_value(
                    job["task_unit_energy_mJ_exact"],
                    f"event {event_index} {array_field}[{job_index}] exact energy",
                )
    return normalized


def _job_identity(event: Mapping[str, Any]) -> str | None:
    return trace._event_job_id(event)


def _lifecycle_before_events(
    events: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    running: set[str] = set()
    released: set[str] = set()
    completed: set[str] = set()
    missed: set[str] = set()
    killed: set[str] = set()
    snapshots: list[Dict[str, Any]] = []
    for event in events:
        snapshots.append({
            "running": sorted(running),
            "released": sorted(released),
            "completed": sorted(completed),
            "missed": sorted(missed),
            "killed": sorted(killed),
        })
        event_type = event.get("event_type")
        identifier = _job_identity(event)
        if identifier is None:
            continue
        if event_type == "arrival":
            released.add(identifier)
        elif event_type == "scheduled":
            running.add(identifier)
        elif event_type == "descheduled":
            running.discard(identifier)
        elif event_type == "end_instance":
            running.discard(identifier)
            completed.add(identifier)
        elif event_type == "dline_miss":
            running.discard(identifier)
            missed.add(identifier)
        elif event_type == "kill":
            running.discard(identifier)
            killed.add(identifier)
    return snapshots


def _irreversible_lifecycle_before_ticks(
    events: Sequence[Mapping[str, Any]], target_ticks: Sequence[Any],
) -> Dict[int, Dict[str, list[str]]]:
    targets = sorted({
        trace._integer(value, "strict-before target tick")
        for value in target_ticks
    })
    changes: Dict[int, Dict[str, set[str]]] = {}
    for position, event in enumerate(events):
        event_tick = trace._integer(event.get("time"), f"event {position} time")
        component = _IRREVERSIBLE_LIFECYCLE_EVENTS.get(event.get("event_type"))
        if component is None:
            continue
        identifier = _job_identity(event)
        if identifier is None:
            raise B2BatchAuditError(
                f"event {position} has invalid irreversible lifecycle identity"
            )
        changes.setdefault(event_tick, {
            key: set() for key in _IRREVERSIBLE_LIFECYCLE_COMPONENTS
        })[component].add(identifier)

    history = {
        key: set() for key in _IRREVERSIBLE_LIFECYCLE_COMPONENTS
    }
    result: Dict[int, Dict[str, list[str]]] = {}
    change_ticks = sorted(changes)
    change_index = 0
    for target in targets:
        while (
            change_index < len(change_ticks)
            and change_ticks[change_index] < target
        ):
            for component in _IRREVERSIBLE_LIFECYCLE_COMPONENTS:
                history[component].update(
                    changes[change_ticks[change_index]][component]
                )
            change_index += 1
        result[target] = {
            component: sorted(history[component])
            for component in _IRREVERSIBLE_LIFECYCLE_COMPONENTS
        }
    return result


def _irreversible_lifecycle_before_tick(
    events: Sequence[Mapping[str, Any]], target_tick: Any,
) -> Dict[str, list[str]]:
    """Return irreversible lifecycle history strictly before ``target_tick``.

    Events at the target tick are excluded because completion, miss, kill, or
    deschedule events may precede a scheduler call within that tick.  Only the
    deterministic ``completed``, ``missed``, and ``killed`` histories prove
    prior trajectory divergence: those histories are irreversible and are
    already part of the complete pre-decision fingerprint.  ``running`` can
    change again within the tick, while a ``released`` mismatch may instead
    signal ordering or trace-evidence damage, so neither is sufficient proof.
    Callers must also prove trace coverage before treating a history difference
    as non-applicability; divergence without coverage remains incomplete.
    """

    tick = trace._integer(target_tick, "strict-before target tick")
    return _irreversible_lifecycle_before_ticks(events, [tick])[tick]


def _validated_irreversible_lifecycle(
    value: Any, label: str,
) -> Dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) != set(
        _IRREVERSIBLE_LIFECYCLE_COMPONENTS
    ):
        raise B2BatchAuditError(f"{label} has invalid lifecycle components")
    result: Dict[str, list[str]] = {}
    for component in _IRREVERSIBLE_LIFECYCLE_COMPONENTS:
        identifiers = value.get(component)
        if (
            not isinstance(identifiers, list)
            or any(not isinstance(identifier, str) or not identifier
                   for identifier in identifiers)
            or identifiers != sorted(set(identifiers))
        ):
            raise B2BatchAuditError(
                f"{label} has invalid {component} lifecycle history"
            )
        result[component] = list(identifiers)
    return result


def _trace_covers_tick(document: Mapping[str, Any], tick: Any) -> bool:
    """Prove coverage from the valid schema-v2 observed end tick.

    Malformed, boolean, negative, non-integral, or non-finite values raise;
    arbitrary later business events are deliberately not coverage evidence.
    """

    try:
        target = trace._integer(tick, "control target tick")
        observed_end = trace._integer(
            document.get("observed_simulation_end_ms"),
            "observed_simulation_end_ms",
        )
    except trace.B2BatchTraceError as exc:
        raise B2BatchAuditError(str(exc)) from exc
    return observed_end >= target


def _incomplete_missing_decision_control(
    common: Mapping[str, Any], error: str, *, covers_tick: bool | None,
    **evidence: Any,
) -> Dict[str, Any]:
    return {
        **common,
        "control_status": CONTROL_STATUS_EVIDENCE_INCOMPLETE,
        "control_passed": False,
        "control_errors": [error],
        "block_state_fingerprint": None,
        "block_trace_covers_tick": covers_tick,
        **evidence,
    }


def _canonical_number(value: Any) -> str:
    number = trace._finite(value, "state fingerprint numeric field")
    return format(number, ".17g")


def _predecision_material(
    document: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]], position: int, *, processors: int,
) -> Dict[str, Any]:
    event = events[position]
    errors: list[str] = []
    ready_ids, ready_energy = trace._jobs(
        event.get("ready_jobs"), "ready_jobs", errors
    )
    if errors:
        raise B2BatchAuditError("state fingerprint lacks valid ready-job evidence")
    jobs = trace._job_payloads(event.get("ready_jobs"))
    ready_material = []
    for identifier in ready_ids:
        payload = jobs[identifier]
        ready_material.append({
            "job_id": identifier,
            "priority": _canonical_number(payload.get("priority")),
            "ready_order": trace._integer(
                payload.get("ready_order"), "ready order"
            ),
            "remaining_time_ms": _canonical_number(payload.get("remaining_time_ms")),
            "absolute_deadline": _canonical_number(payload.get("absolute_deadline")),
            "task_unit_energy_mJ": _canonical_number(ready_energy[identifier]),
        })
    desired = ready_ids[:min(processors, len(ready_ids))]
    running = set(snapshots[position]["running"])
    continuations = [identifier for identifier in desired if identifier in running]
    candidates = [identifier for identifier in desired if identifier not in running]
    lifecycle = {
        key: list(snapshots[position][key])
        for key in ("released", "completed", "missed", "killed")
    }
    material = {
        "tick": trace._tick(event.get("time"), "fingerprint tick"),
        "taskset_semantic_hash": document.get("taskset_semantic_hash"),
        "processor_count": processors,
        "ready_jobs": ready_material,
        "running_job_ids": sorted(running),
        "active_top_m_job_ids": desired,
        "continuation_job_ids": continuations,
        "candidate_job_ids": candidates,
        "available_energy_mJ": _canonical_number(event.get("available_energy_mJ")),
        "lifecycle": lifecycle,
        "decision_input_counters": {
            "released": len(lifecycle["released"]),
            "completed": len(lifecycle["completed"]),
            "missed": len(lifecycle["missed"]),
            "killed": len(lifecycle["killed"]),
            "ready": len(ready_ids),
            "running": len(running),
            "continuation": len(continuations),
            "candidate": len(candidates),
        },
    }
    return material


def _fingerprint(material: Mapping[str, Any]) -> str:
    payload = json.dumps(
        material, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(PREDECISION_FINGERPRINT_DOMAIN.encode() + b"\0" + payload).hexdigest()


def _strict_partial(reasons: Sequence[str]) -> bool:
    return any(reason in {
        "partial_selection_count", "partial_actual_launch_count",
    } for reason in reasons)


def audit_asap_sync_document(
    document: Mapping[str, Any], *, processors: int,
    request_id: str | None = None, pair_id: str = "UNAVAILABLE",
) -> list[Dict[str, Any]]:
    """Classify every ASAP-SYNC decision into one exhaustive audit state."""

    normalized = _normalize_exact_energy(document)
    events = trace._validate_document(normalized, "gpfp_asap_sync")
    snapshots = _lifecycle_before_events(events)
    base_rows = trace.audit_asap_sync_document(
        normalized, processors=processors, request_id=request_id, pair_id=pair_id,
    )
    strict_before_by_tick = _irreversible_lifecycle_before_ticks(
        events, [base["tick"] for base in base_rows],
    )
    rows: list[Dict[str, Any]] = []
    for base in base_rows:
        position = base["evidence_event_ids"]["scheduler_decision"][0]
        event = events[position]
        tick = trace._integer(event.get("time"), f"event {position} time")
        errors = list(base.get("classification_errors", []))
        general_block_errors = [
            error for error in errors
            if error.startswith(_GENERAL_BLOCK_ERROR_PREFIXES)
        ]
        semantic_errors = [
            error for error in errors if error not in general_block_errors
        ]
        candidate_errors = [
            error for error in semantic_errors
            if error.startswith(_CANDIDATE_ERROR_PREFIXES)
        ]
        q = int(base["candidate_count"])
        active_count = len(base["active_top_m_job_ids"])
        continuations = list(base["continuation_job_ids"])
        raw_selected = list(base["raw_selected_job_ids"])
        violations = list(base.get("partial_launch_violation_reasons", []))
        block_present = bool(base.get("sync_batch_block_present"))
        candidate_wait_present = bool(
            base.get("sync_batch_candidate_wait_present")
        )
        whole_affordable = base.get("whole_batch_affordable")
        feasible_member = base.get("feasible_subset_exists") is True
        continuation_branch = (
            active_count >= 2 and q > 0 and continuations
            and whole_affordable is False and raw_selected == continuations
        )
        if continuation_branch and not candidate_wait_present:
            semantic_errors.append("missing_sync_batch_candidate_wait_evidence")
            candidate_errors.append("missing_sync_batch_candidate_wait_evidence")

        if _strict_partial(violations):
            state = B2_STATE_ILLEGAL_PARTIAL_LAUNCH
        elif violations or (block_present and candidate_wait_present):
            state = B2_STATE_ILLEGAL_TRANSITION
        elif semantic_errors:
            state = B2_STATE_UNCLASSIFIABLE
        elif active_count == 0:
            state = B2_STATE_NO_BATCH
        elif q == 0:
            state = B2_STATE_CONTINUATION_ONLY
        elif whole_affordable is False and not feasible_member:
            if base["selected_job_ids"] or base["actually_launched_job_ids"]:
                state = B2_STATE_ILLEGAL_TRANSITION
            else:
                state = (
                    B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER
                )
        elif active_count < 2:
            state = B2_STATE_NOT_APPLICABLE
        elif whole_affordable is True:
            complete = (
                set(raw_selected) == set(base["active_top_m_job_ids"])
                and int(base["selected_count"]) == q
                and int(base["actual_launch_count"]) == q
                and set(base["selected_job_ids"]) == set(base["candidate_job_ids"])
                and set(base["actually_launched_job_ids"])
                == set(base["candidate_job_ids"])
                and not candidate_wait_present
            )
            state = (
                B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH
                if complete else B2_STATE_ILLEGAL_TRANSITION
            )
        elif whole_affordable is False:
            if base["selected_job_ids"] or base["actually_launched_job_ids"]:
                state = B2_STATE_ILLEGAL_TRANSITION
            elif candidate_wait_present:
                state = B2_STATE_CONTINUATION_CANDIDATE_WAIT
            else:
                state = (
                    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER
                )
        else:
            state = B2_STATE_UNCLASSIFIABLE
            semantic_errors.append("missing_whole_batch_affordability")

        try:
            material = _predecision_material(
                normalized, events, snapshots, position, processors=processors,
            )
        except B2BatchAuditError:
            if state != B2_STATE_UNCLASSIFIABLE:
                raise
            material = None
        row = dict(base)
        row.update({
            "base_classified_state": base["classified_state"],
            "classified_state": state,
            "classification_errors": semantic_errors,
            "general_block_evidence_errors": general_block_errors,
            "general_block_present": block_present,
            "general_block_nonatomic": (
                block_present and state not in ATOMIC_WAIT_STATES
            ),
            "q0_general_block": block_present and q == 0,
            "precision_source": "MAX_DIGITS10_EXACT_STRING",
            "continuation_evidence_failure": bool(candidate_errors),
            "atomic_opportunity": state in ATOMIC_OPPORTUNITY_STATES,
            "atomic_wait_with_affordable_member": state in ATOMIC_WAIT_STATES,
            "predecision_state_fingerprint": (
                None if material is None else _fingerprint(material)
            ),
            "predecision_state_material": material,
            "strict_before_irreversible_lifecycle": deepcopy(
                strict_before_by_tick[tick]
            ),
        })
        rows.append(row)
    return rows


def audit_asap_sync_trace(path: Path | str, **kwargs: Any) -> list[Dict[str, Any]]:
    return audit_asap_sync_document(trace._load_trace(path), **kwargs)


def _decision_positions(
    events: Sequence[Mapping[str, Any]], scheduler: str,
) -> Dict[int, int]:
    result: Dict[int, int] = {}
    for position, event in enumerate(events):
        if event.get("event_type") != "scheduler_decision":
            continue
        if event.get("scheduler") != scheduler:
            continue
        tick = trace._tick(event.get("time"), f"event {position} time")
        if tick in result:
            raise B2BatchAuditError("multiple scheduler decisions at one tick")
        result[tick] = position
    return result


def audit_asap_block_pair_control(
    sync_rows: Sequence[Mapping[str, Any]],
    block_document: Mapping[str, Any], *, processors: int,
    expected_min_prefix_length: int,
) -> list[Dict[str, Any]]:
    """Evaluate BLOCK only when paired evidence supports comparability."""

    normalized = _normalize_exact_energy(block_document)
    events = trace._validate_document(normalized, "gpfp_asap_block")
    snapshots = _lifecycle_before_events(events)
    positions = _decision_positions(events, "ASAP-Block")
    controls: list[Dict[str, Any]] = []
    for sync in sync_rows:
        if sync.get("classified_state") not in ATOMIC_WAIT_STATES:
            continue
        tick = trace._integer(sync.get("tick"), "SYNC control tick")
        position = positions.get(tick)
        common = {
            "pair_id": sync.get("pair_id", "UNAVAILABLE"),
            "tick": tick,
            "sync_state_fingerprint": sync.get("predecision_state_fingerprint"),
        }
        if position is None:
            try:
                block_trace_covers_tick = _trace_covers_tick(normalized, tick)
            except trace.B2BatchTraceError:
                controls.append(_incomplete_missing_decision_control(
                    common, "invalid_block_trace_coverage", covers_tick=None,
                ))
                continue
            if not block_trace_covers_tick:
                controls.append(_incomplete_missing_decision_control(
                    common, "block_trace_does_not_cover_tick",
                    covers_tick=False,
                ))
                continue
            if sync.get("taskset_semantic_hash") != normalized.get(
                "taskset_semantic_hash"
            ):
                controls.append(_incomplete_missing_decision_control(
                    common, "taskset_semantic_hash_mismatch", covers_tick=True,
                ))
                continue
            sync_predecision = sync.get("predecision_state_material")
            if not isinstance(sync_predecision, dict):
                controls.append(_incomplete_missing_decision_control(
                    common, "missing_sync_predecision_state_material",
                    covers_tick=True,
                ))
                continue
            sync_processors = sync_predecision.get("processor_count")
            if (
                isinstance(sync_processors, bool)
                or not isinstance(sync_processors, int)
                or sync_processors != processors
            ):
                controls.append(_incomplete_missing_decision_control(
                    common, "processor_count_mismatch", covers_tick=True,
                ))
                continue
            try:
                sync_lifecycle = _validated_irreversible_lifecycle(
                    sync.get("strict_before_irreversible_lifecycle"),
                    "SYNC strict-before evidence",
                )
            except B2BatchAuditError:
                controls.append(_incomplete_missing_decision_control(
                    common, "invalid_sync_strict_before_lifecycle_evidence",
                    covers_tick=True,
                ))
                continue
            try:
                block_lifecycle = _irreversible_lifecycle_before_tick(
                    events, tick,
                )
            except trace.B2BatchTraceError:
                controls.append(_incomplete_missing_decision_control(
                    common, "invalid_block_strict_before_lifecycle_evidence",
                    covers_tick=True,
                ))
                continue
            differing = sorted(
                component for component in _IRREVERSIBLE_LIFECYCLE_COMPONENTS
                if sync_lifecycle[component] != block_lifecycle[component]
            )
            lifecycle_evidence = {
                "sync_strict_before_irreversible_lifecycle": sync_lifecycle,
                "block_strict_before_irreversible_lifecycle": block_lifecycle,
            }
            if differing:
                controls.append({
                    **common,
                    "control_status": CONTROL_STATUS_NOT_APPLICABLE,
                    "control_passed": None,
                    "control_errors": [],
                    "block_state_fingerprint": None,
                    "block_trace_covers_tick": True,
                    "not_applicable_reason": "prior_trajectory_divergence",
                    "incomparable_state_components": differing,
                    **lifecycle_evidence,
                })
                continue
            controls.append(_incomplete_missing_decision_control(
                common,
                "missing_asap_block_decision_without_proven_divergence",
                covers_tick=True,
                incomparable_state_components=[],
                **lifecycle_evidence,
            ))
            continue
        block_material = _predecision_material(
            normalized, events, snapshots, position, processors=processors,
        )
        block_fingerprint = _fingerprint(block_material)
        common["block_state_fingerprint"] = block_fingerprint
        sync_material = sync.get("predecision_state_material")
        if not isinstance(sync_material, dict):
            controls.append({
                **common,
                "control_status": CONTROL_STATUS_EVIDENCE_INCOMPLETE,
                "control_passed": False,
                "control_errors": ["missing_sync_predecision_state_material"],
            })
            continue
        if block_fingerprint != common["sync_state_fingerprint"]:
            differing = sorted(
                key for key in set(sync_material) | set(block_material)
                if sync_material.get(key) != block_material.get(key)
            )
            controls.append({
                **common,
                "control_status": CONTROL_STATUS_NOT_APPLICABLE,
                "control_passed": None,
                "control_errors": [],
                "incomparable_state_components": differing,
            })
            continue

        # A matched state is a positive-prefix control opportunity only when
        # the frozen priority prefix itself contains an affordable member.
        # "Some lower-priority member is affordable" proves the SYNC atomic
        # opportunity but cannot make the non-bypassing BLOCK prefix positive.
        if int(sync.get("affordable_prefix_length", 0)) < expected_min_prefix_length:
            controls.append({
                **common,
                "control_status": CONTROL_STATUS_NOT_APPLICABLE,
                "control_passed": None,
                "control_errors": [],
                "incomparable_state_components": [],
                "not_applicable_reason": "matched_state_has_no_affordable_priority_prefix",
            })
            continue

        decision = events[position]
        errors: list[str] = []
        ready_ids, ready_energy = trace._jobs(
            decision.get("ready_jobs"), "ready_jobs", errors,
        )
        selected_ids, _ = trace._jobs(
            decision.get("selected_jobs"), "selected_jobs", errors,
        )
        if selected_ids != ready_ids[:len(selected_ids)]:
            errors.append("asap_block_selection_is_not_priority_prefix")
        if len(selected_ids) < expected_min_prefix_length:
            errors.append("asap_block_has_no_positive_affordable_prefix")
        running_before = set(snapshots[position]["running"])
        new_selected = [job for job in selected_ids if job not in running_before]
        following = trace._same_tick_following_events(
            events, position, tick, "ASAP-Block",
        )
        launched: list[str] = []
        scheduled_positions: list[int] = []
        for event_index, following_event in following:
            if following_event.get("event_type") != "scheduled":
                continue
            identifier = trace._event_job_id(following_event)
            if identifier is not None and identifier not in running_before:
                launched.append(identifier)
                scheduled_positions.append(event_index)
        if launched != new_selected:
            errors.append("asap_block_selected_launch_mismatch")
        try:
            available = trace._finite(
                decision.get("available_energy_mJ"), "block available energy",
            )
        except trace.B2BatchTraceError:
            available = None
            errors.append("missing_asap_block_available_energy")
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
        controls.append({
            **common,
            "control_status": CONTROL_STATUS_ELIGIBLE_MATCHED_STATE,
            "taskset_semantic_hash": normalized.get("taskset_semantic_hash"),
            "scheduler_id": "gpfp_asap_block",
            "ready_job_ids": ready_ids,
            "selected_job_ids": selected_ids,
            "actually_launched_job_ids": launched,
            "prefix_required_energy_mJ": prefix_required,
            "available_energy_mJ": available,
            "prefix_affordable": prefix_affordable,
            "expected_min_prefix_length": expected_min_prefix_length,
            "control_passed": not errors,
            "control_errors": errors,
            "evidence_event_ids": {
                "scheduler_decision": [position],
                "scheduled": scheduled_positions,
            },
        })
    return controls


def audit_asap_block_pair_trace(
    sync_rows: Sequence[Mapping[str, Any]], block_path: Path | str,
    **kwargs: Any,
) -> list[Dict[str, Any]]:
    return audit_asap_block_pair_control(
        sync_rows, trace._load_trace(block_path), **kwargs,
    )


def summarize_b2_observations(
    rows: Sequence[Mapping[str, Any]],
    controls: Sequence[Mapping[str, Any]] = (), *,
    reported_synchronization_wait_ticks: int | None = None,
    require_matched_controls: bool = False,
) -> Dict[str, Any]:
    """Return non-overloaded counters and the atomicity-only metric."""

    state_counts = {state: 0 for state in ALLOWED_STATES}
    for row in rows:
        state = row.get("classified_state")
        if state not in state_counts:
            raise B2BatchAuditError(f"unknown B2 audit state: {state!r}")
        state_counts[state] += 1
    affordable = state_counts[B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH]
    waiting = sum(state_counts[state] for state in ATOMIC_WAIT_STATES)
    active = affordable + waiting
    raw_blocks = sum(row.get("sync_batch_block_present") is True for row in rows)
    reported_mismatch = 0
    if reported_synchronization_wait_ticks is not None:
        reported_mismatch = int(raw_blocks != reported_synchronization_wait_ticks)
    control_evidence_incomplete = sum(
        control.get("control_status") == CONTROL_STATUS_EVIDENCE_INCOMPLETE
        for control in controls
    )
    if require_matched_controls and len(controls) != waiting:
        control_evidence_incomplete += abs(len(controls) - waiting)
    summary = {
        "decision_row_count": len(rows),
        "state_counts": state_counts,
        "state_unclassifiable_count": state_counts[B2_STATE_UNCLASSIFIABLE],
        "illegal_partial_count": state_counts[B2_STATE_ILLEGAL_PARTIAL_LAUNCH],
        "illegal_transition_count": state_counts[B2_STATE_ILLEGAL_TRANSITION],
        "no_affordable_member_count": state_counts[
            B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER
        ],
        "matched_control_failure_count": sum(
            control.get("control_status")
            == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
            and control.get("control_passed") is not True
            for control in controls
        ),
        "matched_control_success_count": sum(
            control.get("control_status")
            == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
            and control.get("control_passed") is True
            for control in controls
        ),
        "control_not_applicable_count": sum(
            control.get("control_status") == CONTROL_STATUS_NOT_APPLICABLE
            for control in controls
        ),
        "control_evidence_incomplete_count": control_evidence_incomplete,
        "continuation_evidence_failure_count": sum(
            row.get("continuation_evidence_failure") is True for row in rows
        ),
        "synchronization_wait_ticks_mismatch_count": reported_mismatch,
        "affordable_atomic_launch_count": affordable,
        "atomic_wait_with_affordable_member_count": waiting,
        "active_batch_opportunity_count": active,
        "atomic_wait_share": (
            None if active == 0
            else f"{Fraction(waiting, active).numerator}/{Fraction(waiting, active).denominator}"
        ),
    }
    if sum(state_counts.values()) != len(rows):
        raise B2BatchAuditError("B2 audit state partition does not close")
    return summary
