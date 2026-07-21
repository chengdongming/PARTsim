"""Fail-closed EXT-1B/B3 timing-family observation audit.

The auditor consumes only additive ``b3_timing_observation`` events.  It does
not infer a timing mechanism from first-execution outcomes or from B1/B2
events.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .task_identity import runtime_job_id


B3_STATE_ASAP_IMMEDIATE_ELIGIBILITY = (
    "B3_STATE_ASAP_IMMEDIATE_ELIGIBILITY"
)
B3_STATE_ALAP_POSITIVE_SLACK_DEFER = "B3_STATE_ALAP_POSITIVE_SLACK_DEFER"
B3_STATE_ALAP_URGENT_ELIGIBILITY = "B3_STATE_ALAP_URGENT_ELIGIBILITY"
B3_STATE_ST_AFFORDABLE_ASAP_BEHAVIOR = (
    "B3_STATE_ST_AFFORDABLE_ASAP_BEHAVIOR"
)
B3_STATE_ST_ENERGY_INSUFFICIENT_SLACK_WAIT = (
    "B3_STATE_ST_ENERGY_INSUFFICIENT_SLACK_WAIT"
)
B3_STATE_NOT_APPLICABLE = "B3_STATE_NOT_APPLICABLE"
B3_STATE_ILLEGAL_TIMING_TRANSITION = "B3_STATE_ILLEGAL_TIMING_TRANSITION"
B3_STATE_UNCLASSIFIABLE = "B3_STATE_UNCLASSIFIABLE"

# Compact aliases keep classification code readable while the serialized
# state values and public stable constants retain the explicit B3 namespace.
ASAP_IMMEDIATE_ELIGIBILITY = B3_STATE_ASAP_IMMEDIATE_ELIGIBILITY
ALAP_POSITIVE_SLACK_DEFER = B3_STATE_ALAP_POSITIVE_SLACK_DEFER
ALAP_URGENT_ELIGIBILITY = B3_STATE_ALAP_URGENT_ELIGIBILITY
ST_AFFORDABLE_ASAP_BEHAVIOR = B3_STATE_ST_AFFORDABLE_ASAP_BEHAVIOR
ST_ENERGY_INSUFFICIENT_SLACK_WAIT = (
    B3_STATE_ST_ENERGY_INSUFFICIENT_SLACK_WAIT
)
NOT_APPLICABLE = B3_STATE_NOT_APPLICABLE
ILLEGAL_TIMING_TRANSITION = B3_STATE_ILLEGAL_TIMING_TRANSITION
UNCLASSIFIABLE = B3_STATE_UNCLASSIFIABLE

STABLE_STATES = (
    ASAP_IMMEDIATE_ELIGIBILITY,
    ALAP_POSITIVE_SLACK_DEFER,
    ALAP_URGENT_ELIGIBILITY,
    ST_AFFORDABLE_ASAP_BEHAVIOR,
    ST_ENERGY_INSUFFICIENT_SLACK_WAIT,
    NOT_APPLICABLE,
    ILLEGAL_TIMING_TRANSITION,
    UNCLASSIFIABLE,
)

SCHEDULERS = {
    "gpfp_asap_block": ("ASAP-Block", "ASAP", "BLOCK"),
    "gpfp_asap_nonblock": ("ASAP-NonBlock", "ASAP", "NONBLOCK"),
    "gpfp_asap_sync": ("ASAP-Sync", "ASAP", "SYNC"),
    "gpfp_alap_block": ("ALAP-Block", "ALAP", "BLOCK"),
    "gpfp_alap_nonblock": ("ALAP-NonBlock", "ALAP", "NONBLOCK"),
    "gpfp_alap_sync": ("ALAP-Sync", "ALAP", "SYNC"),
    "gpfp_st_block": ("ST-Block", "ST", "BLOCK"),
    "gpfp_st_nonblock": ("ST-NonBlock", "ST", "NONBLOCK"),
    "gpfp_st_sync": ("ST-Sync", "ST", "SYNC"),
}

POLICY_REASONS = {
    "NONE",
    "CPU_CAPACITY",
    "HIGHER_PRIORITY",
    "ENERGY_INSUFFICIENT",
    "BLOCK_HEAD_OF_LINE",
    "NONBLOCK_BYPASS",
    "SYNC_ATOMIC_BATCH_WAIT",
}
OUTCOMES = {
    "DISPATCH_SELECTED",
    "CONTINUE_SELECTED",
    "TIMING_DEFERRED",
    "BLOCKED",
}
REQUIRED_FIELDS = {
    "time",
    "event_type",
    "scheduler",
    "scheduler_family",
    "blocking_policy",
    "task_name",
    "task_id",
    "arrival_time",
    "job_id",
    "remaining_time_ms",
    "rounded_remaining_ms",
    "absolute_deadline",
    "scheduler_slack",
    "ready",
    "timing_gate_open",
    "cpu_available",
    "continuation",
    "selected",
    "job_required_energy_mJ",
    "decision_required_energy_mJ",
    "available_energy_mJ",
    "job_energy_affordable",
    "decision_energy_affordable",
    "native_epsilon_mJ",
    "blocking_policy_reason",
    "actual_outcome",
    "reason_code",
}


class B3TimingAuditError(RuntimeError):
    """The trace cannot satisfy the B3 timing audit."""


@dataclass(frozen=True)
class TimingFinding:
    state: str
    identity: tuple[int, str, int]
    reason: str
    scheduler_slack: int | None = None


@dataclass(frozen=True)
class TimingAuditReport:
    findings: tuple[TimingFinding, ...]
    errors: tuple[str, ...]
    timing_activation: bool = False
    same_job_transition_count: int = 0
    activation_candidate_job_count: int = 0
    activation_denominator_zero: bool = True
    target_wait_observed: bool = False
    target_positive_slack_transition: bool = False
    target_transition_after_slack_exhaustion: bool = False
    target_terminated_without_transition: bool = False
    any_target_job_positive_transition_count: int = 0
    later_target_job_positive_transition_count: int = 0
    non_target_positive_transition_count: int = 0
    activation_from_other_job_only: bool = False
    target_audit_closed: bool = False
    target_audit_error_count: int = 0
    full_release_target_present: bool = False
    full_release_target_selected: bool = False
    full_release_prefix_affordable: bool = False
    runtime_recovery_prefix_matches: bool = False
    runtime_recovery_prefix_names: tuple[str, ...] = ()
    recovery_prefix_audit_closed: bool = False
    recovery_prefix_audit_error_count: int = 0

    @property
    def state_counts(self) -> Mapping[str, int]:
        return {
            state: sum(finding.state == state for finding in self.findings)
            for state in STABLE_STATES
        }

    def assert_audit_closed(self) -> None:
        if self.errors or any(
            finding.state in {ILLEGAL_TIMING_TRANSITION, UNCLASSIFIABLE}
            for finding in self.findings
        ):
            raise B3TimingAuditError(self.closure_diagnostic())

    def closure_diagnostic(
        self,
        *,
        request_id: str = "UNAVAILABLE",
        scheduler_id: str = "UNAVAILABLE",
        sample_limit: int = 5,
    ) -> str:
        """Return bounded diagnostics without changing closure semantics."""

        if isinstance(sample_limit, bool) or sample_limit < 0:
            raise ValueError("sample_limit must be a non-negative integer")
        failures = tuple(
            finding for finding in self.findings
            if finding.state in {
                ILLEGAL_TIMING_TRANSITION,
                UNCLASSIFIABLE,
            }
        )
        reasons = Counter(finding.reason for finding in failures)
        reason_text = ",".join(
            f"{reason}:{count}" for reason, count in sorted(reasons.items())
        ) or "NONE"
        samples = failures[:sample_limit]
        sample_text = " | ".join(
            f"{finding.identity}:{finding.state}:{finding.reason}"
            for finding in samples
        ) or "NONE"
        error_text = " | ".join(self.errors[:sample_limit]) or "NONE"
        return "; ".join((
            f"request_id={request_id}",
            f"scheduler_id={scheduler_id}",
            f"illegal_finding_count={sum(f.state == ILLEGAL_TIMING_TRANSITION for f in failures)}",
            f"unclassifiable_finding_count={sum(f.state == UNCLASSIFIABLE for f in failures)}",
            f"report_error_count={len(self.errors)}",
            f"reason_counts={reason_text}",
            f"sample={sample_text}",
            f"report_error_sample={error_text}",
        ))


def _strict_json(path: Path) -> Mapping[str, Any]:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise B3TimingAuditError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=no_duplicates)
    except (OSError, json.JSONDecodeError) as exc:
        raise B3TimingAuditError(f"cannot read timing trace: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
        raise B3TimingAuditError("trace must contain an event list")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        parsed = Fraction(value.strip())
        if parsed.denominator == 1:
            return parsed.numerator
    raise ValueError(f"{label} must be an integer")


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _identity(event: Mapping[str, Any]) -> tuple[int, str, int]:
    return (
        _integer(event.get("time"), "time"),
        str(event.get("task_name", "")),
        _integer(event.get("arrival_time"), "arrival_time"),
    )


def _illegal(event: Mapping[str, Any], reason: str) -> TimingFinding:
    return TimingFinding(ILLEGAL_TIMING_TRANSITION, _identity(event), reason)


def classify_timing_event(
    event: Mapping[str, Any],
    *,
    configured_scheduler: str,
    scheduled_identities: Iterable[tuple[int, str, int]] = (),
    running_before_identities: Iterable[tuple[int, str, int]] = (),
) -> TimingFinding:
    """Validate and classify one B3 event without outcome inference."""

    fallback_identity = (-1, str(event.get("task_name", "")), -1)
    try:
        missing = sorted(REQUIRED_FIELDS.difference(event))
        if missing:
            raise ValueError("missing fields: " + ", ".join(missing))
        if event["event_type"] != "b3_timing_observation":
            raise ValueError("wrong event_type")
        expected = SCHEDULERS.get(configured_scheduler)
        if expected is None:
            raise ValueError("unknown configured scheduler")
        if tuple(event[name] for name in (
            "scheduler", "scheduler_family", "blocking_policy"
        )) != expected:
            raise ValueError("scheduler family/policy mismatch")

        identity = _identity(event)
        now, task_name, arrival = identity
        if not task_name or event["task_id"] != task_name:
            raise ValueError("task identity mismatch")
        if event["job_id"] != f"{task_name}@{arrival}":
            raise ValueError("job identity mismatch")

        remaining = _finite(event["remaining_time_ms"], "remaining")
        rounded = _integer(event["rounded_remaining_ms"], "rounded remaining")
        deadline = _integer(event["absolute_deadline"], "absolute deadline")
        slack = _integer(event["scheduler_slack"], "scheduler slack")
        if remaining <= 0.0 or rounded != math.ceil(remaining):
            raise ValueError("remaining/rounded remaining mismatch")
        if slack != deadline - rounded - now:
            raise ValueError("scheduler slack mismatch")

        ready = _boolean(event["ready"], "ready")
        gate = _boolean(event["timing_gate_open"], "timing gate")
        cpu = _boolean(event["cpu_available"], "cpu available")
        continuation = _boolean(event["continuation"], "continuation")
        selected = _boolean(event["selected"], "selected")
        job_required = _finite(
            event["job_required_energy_mJ"], "job required energy"
        )
        decision_required = _finite(
            event["decision_required_energy_mJ"], "decision required energy"
        )
        available = _finite(event["available_energy_mJ"], "available energy")
        epsilon = _finite(event["native_epsilon_mJ"], "native epsilon")
        if min(job_required, decision_required, available, epsilon) < 0.0:
            raise ValueError("energy evidence must be non-negative")
        job_affordable = _boolean(
            event["job_energy_affordable"], "job affordability"
        )
        decision_affordable = _boolean(
            event["decision_energy_affordable"], "decision affordability"
        )
        if job_affordable != (available + epsilon >= job_required):
            raise ValueError("job affordability mismatch")
        if decision_affordable != (available + epsilon >= decision_required):
            raise ValueError("decision affordability mismatch")

        policy_reason = event["blocking_policy_reason"]
        outcome = event["actual_outcome"]
        if policy_reason not in POLICY_REASONS:
            raise ValueError("unknown blocking policy reason")
        if outcome not in OUTCOMES:
            raise ValueError("unknown actual outcome")
        if not isinstance(event["reason_code"], str) or not event["reason_code"]:
            raise ValueError("missing reason code")

        selected_outcome = outcome in {
            "DISPATCH_SELECTED", "CONTINUE_SELECTED"
        }
        if selected != selected_outcome:
            raise ValueError("selected/result mismatch")
        if outcome == "CONTINUE_SELECTED" and not continuation:
            raise ValueError("continuation result lacks running-before flag")
        if outcome == "DISPATCH_SELECTED" and continuation:
            raise ValueError("dispatch result is incorrectly marked continuation")
        scheduled = set(scheduled_identities)
        running_before = set(running_before_identities)
        if outcome == "DISPATCH_SELECTED" and identity not in scheduled:
            raise ValueError("selected job has no same-tick dispatch")
        if outcome == "CONTINUE_SELECTED" and identity not in running_before:
            raise ValueError("continuation has no running-before evidence")
        if continuation and identity not in running_before:
            raise ValueError("continuation flag has no running-before evidence")
        if not selected and identity in scheduled:
            raise ValueError("unselected job has same-tick dispatch")
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        try:
            fallback_identity = _identity(event)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return TimingFinding(UNCLASSIFIABLE, fallback_identity, str(exc))

    family = expected[1]
    if not ready:
        return TimingFinding(
            NOT_APPLICABLE, identity, "job is not ready"
        )
    if policy_reason in {"NONBLOCK_BYPASS", "SYNC_ATOMIC_BATCH_WAIT"}:
        return TimingFinding(
            NOT_APPLICABLE, identity, "B1/B2 blocking-policy evidence excluded"
        )

    if selected and (not gate or not cpu or not decision_affordable):
        return _illegal(event, "selected while a native eligibility gate is closed")

    if family == "ASAP":
        if not gate:
            return _illegal(event, "ASAP timing gate is closed")
        if cpu and decision_affordable and policy_reason == "NONE":
            if not selected:
                return _illegal(event, "eligible ASAP job was deferred")
            return TimingFinding(
                ASAP_IMMEDIATE_ELIGIBILITY, identity, "all ASAP gates open",
                slack,
            )
        return TimingFinding(NOT_APPLICABLE, identity, "non-timing gate closed")

    if family == "ALAP":
        if gate != (slack <= 0):
            return _illegal(event, "ALAP timing gate disagrees with scheduler slack")
        if slack > 0:
            if selected:
                return _illegal(event, "positive-slack ALAP job was selected")
            if (
                cpu and decision_affordable and policy_reason == "NONE"
                and outcome == "TIMING_DEFERRED"
            ):
                return TimingFinding(
                    ALAP_POSITIVE_SLACK_DEFER,
                    identity,
                    "positive scheduler slack uniquely caused defer",
                    slack,
                )
            return TimingFinding(NOT_APPLICABLE, identity, "non-timing gate closed")
        if cpu and decision_affordable and policy_reason == "NONE" and selected:
            return TimingFinding(
                ALAP_URGENT_ELIGIBILITY,
                identity,
                "non-positive slack opened the timing gate and the job executed",
                slack,
            )
        return TimingFinding(
            NOT_APPLICABLE,
            identity,
            "ALAP timing gate is open but a non-timing gate or selection is absent",
            slack,
        )

    if slack <= 0 and not gate:
        return _illegal(event, "ST continued timing wait after slack exhaustion")
    if decision_affordable:
        if not gate:
            return _illegal(event, "affordable ST decision kept timing gate closed")
        if cpu and policy_reason == "NONE":
            if not selected:
                return _illegal(event, "affordable eligible ST job was deferred")
            return TimingFinding(
                ST_AFFORDABLE_ASAP_BEHAVIOR,
                identity,
                "ST followed ASAP with an affordable native decision",
                slack,
            )
        return TimingFinding(NOT_APPLICABLE, identity, "non-timing gate closed")
    if (
        slack > 0 and not gate and cpu and policy_reason == "NONE"
        and outcome == "TIMING_DEFERRED"
    ):
        return TimingFinding(
            ST_ENERGY_INSUFFICIENT_SLACK_WAIT,
            identity,
            "native energy gate closed while positive slack permits wait",
            slack,
        )
    return TimingFinding(NOT_APPLICABLE, identity, "not a B3 timing transition")


def audit_timing_trace(
    path: Path,
    *,
    expected_scheduler: str,
    target_runtime_task_name: str | None = None,
    target_arrival_time: int | None = None,
    target_recovery_contract_applicable: bool | None = None,
    recovery_prefix_identity: str | None = None,
    recovery_prefix_runtime_names: Sequence[str] = (),
    recovery_prefix_required_energy: str | None = None,
    materialized_battery_capacity: str | None = None,
    actual_trace_full_tick: int | None = None,
) -> TimingAuditReport:
    data = _strict_json(path)
    errors: list[str] = []
    if data.get("trace_schema_version") != 2:
        errors.append("timing audit requires trace schema version 2")
    if data.get("configured_scheduler") != expected_scheduler:
        errors.append("configured scheduler mismatch")

    target_job_identity: tuple[str, int] | None = None
    recovery_applicable = (
        target_runtime_task_name is not None
        if target_recovery_contract_applicable is None
        else target_recovery_contract_applicable
    )
    if not isinstance(recovery_applicable, bool):
        errors.append("target recovery applicability must be boolean")
        recovery_applicable = False
    if (target_runtime_task_name is None) != (target_arrival_time is None):
        errors.append(
            "target runtime task name and arrival time must be supplied together"
        )
    elif target_runtime_task_name is not None:
        if not isinstance(target_runtime_task_name, str) or not target_runtime_task_name:
            errors.append("target runtime task name must be non-empty")
        elif isinstance(target_arrival_time, bool) or not isinstance(
            target_arrival_time, int
        ) or target_arrival_time < 0:
            errors.append("target arrival time must be a non-negative integer")
        else:
            target_job_identity = (
                target_runtime_task_name, target_arrival_time,
            )
            expected_job_id = runtime_job_id(*target_job_identity)
            trace_identity = (
                data.get("target_runtime_task_name"),
                data.get("target_arrival_time"),
                data.get("target_job_id"),
            )
            if trace_identity != (
                target_runtime_task_name, target_arrival_time, expected_job_id,
            ):
                errors.append("trace target job identity mismatch")

    prefix_audit_requested = (
        recovery_applicable and recovery_prefix_identity is not None
    )
    prefix_identity_valid = True
    if target_recovery_contract_applicable is not None:
        if data.get("target_recovery_contract_applicable") is not recovery_applicable:
            errors.append("trace target recovery applicability mismatch")
            prefix_identity_valid = False
        if recovery_applicable:
            prefix_names = tuple(str(name) for name in recovery_prefix_runtime_names)
            expected_recovery = {
                "recovery_prefix_identity": recovery_prefix_identity,
                "recovery_prefix_length": len(prefix_names),
                "recovery_prefix_runtime_names_json": json.dumps(
                    list(prefix_names), separators=(",", ":"),
                ),
                "recovery_prefix_required_energy": (
                    recovery_prefix_required_energy
                ),
                "materialized_battery_capacity": (
                    materialized_battery_capacity
                ),
                "actual_trace_full_tick": actual_trace_full_tick,
            }
            if any(data.get(key) != value for key, value in expected_recovery.items()):
                errors.append("trace recovery prefix identity mismatch")
                prefix_identity_valid = False
            if (
                not prefix_names
                or not isinstance(actual_trace_full_tick, int)
                or actual_trace_full_tick <= 0
            ):
                errors.append("invalid recovery prefix audit inputs")
                prefix_identity_valid = False

    events = data["events"]
    trace_has_terminal_outcome = any(
        isinstance(event, dict)
        and event.get("event_type") == "simulation_run_outcome"
        and event.get("simulation_completed") is True
        for event in events
    )
    scheduled: set[tuple[int, str, int]] = set()
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "scheduled":
            continue
        try:
            scheduled.add((
                _integer(event.get("time"), "scheduled time"),
                str(event.get("task_name", "")),
                _integer(event.get("arrival_time"), "scheduled arrival"),
            ))
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            errors.append(f"invalid scheduled event: {exc}")

    findings: list[TimingFinding] = []
    identities: set[tuple[int, str, int]] = set()
    arrivals: set[tuple[str, int]] = set()
    running: set[tuple[int, str, int]] = set()
    terminal_events: dict[tuple[str, int], list[tuple[int, str]]] = {}
    last_observation_time: dict[tuple[str, int], int] = {}
    observation_count = 0
    stop_events = {"descheduled", "end_instance", "dline_miss", "killed"}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if event_type == "arrival":
            try:
                arrivals.add((
                    str(event.get("task_name", "")),
                    _integer(event.get("arrival_time"), "arrival release"),
                ))
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                errors.append(f"invalid arrival event: {exc}")
            continue
        if event_type == "scheduled":
            try:
                identity = (
                    _integer(event.get("time"), "scheduled time"),
                    str(event.get("task_name", "")),
                    _integer(event.get("arrival_time"), "scheduled arrival"),
                )
                if (identity[1], identity[2]) not in arrivals:
                    errors.append("scheduled task/job has no matching arrival")
                running = {
                    item for item in running
                    if item[1:] != identity[1:]
                }
                running.add(identity)
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                errors.append(f"invalid scheduled event: {exc}")
            continue
        if event_type in stop_events:
            try:
                task_name = str(event.get("task_name", ""))
                arrival = _integer(
                    event.get("arrival_time"), f"{event_type} arrival"
                )
                running = {
                    item for item in running
                    if item[1:] != (task_name, arrival)
                }
                if event_type in {"end_instance", "dline_miss", "killed"}:
                    terminal_events.setdefault((task_name, arrival), []).append((
                        _integer(event.get("time"), f"{event_type} time"),
                        str(event_type),
                    ))
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                errors.append(f"invalid {event_type} event: {exc}")
            continue
        if event_type != "b3_timing_observation":
            continue
        observation_count += 1
        try:
            raw_identity = _identity(event)
        except (TypeError, ValueError, ZeroDivisionError):
            raw_identity = (-1, str(event.get("task_name", "")), -1)
        raw_job_identity = raw_identity[1:]
        previous_time = last_observation_time.get(raw_job_identity)
        if previous_time is not None and raw_identity[0] < previous_time:
            errors.append(
                "same-job B3 observation time is reversed for "
                f"{raw_job_identity[0]}@{raw_job_identity[1]}: "
                f"{raw_identity[0]} < {previous_time}"
            )
        last_observation_time[raw_job_identity] = max(
            raw_identity[0], previous_time if previous_time is not None else raw_identity[0]
        )
        running_before = {
            (raw_identity[0], item[1], item[2]) for item in running
        }
        finding = classify_timing_event(
            event,
            configured_scheduler=expected_scheduler,
            scheduled_identities=scheduled,
            running_before_identities=running_before,
        )
        if (
            finding.state != UNCLASSIFIABLE
            and (finding.identity[1], finding.identity[2]) not in arrivals
        ):
            finding = TimingFinding(
                UNCLASSIFIABLE,
                finding.identity,
                "task/job has no matching arrival",
            )
        if finding.identity in identities:
            findings.append(TimingFinding(
                UNCLASSIFIABLE,
                finding.identity,
                "duplicate B3 observation identity",
            ))
        else:
            identities.add(finding.identity)
            findings.append(finding)
    if observation_count == 0:
        errors.append("missing b3_timing_observation trace")

    family = SCHEDULERS.get(expected_scheduler, ("", "", ""))[1]
    by_job: dict[tuple[str, int], list[TimingFinding]] = {}
    for finding in findings:
        if finding.state in {UNCLASSIFIABLE, ILLEGAL_TIMING_TRANSITION}:
            continue
        by_job.setdefault(finding.identity[1:], []).append(finding)

    candidate_jobs: set[tuple[str, int]] = set()
    transition_jobs: set[tuple[str, int]] = set()
    if family == "ASAP":
        candidate_jobs = {
            job for job, rows in by_job.items()
            if any(row.state == ASAP_IMMEDIATE_ELIGIBILITY for row in rows)
        }
        timing_activation = bool(candidate_jobs)
    else:
        stage_a = (
            ALAP_POSITIVE_SLACK_DEFER
            if family == "ALAP"
            else ST_ENERGY_INSUFFICIENT_SLACK_WAIT
        )
        stage_b = (
            ALAP_URGENT_ELIGIBILITY
            if family == "ALAP"
            else ST_AFFORDABLE_ASAP_BEHAVIOR
        )
        for job, rows in by_job.items():
            first_stage_a = min(
                (row.identity[0] for row in rows if row.state == stage_a),
                default=None,
            )
            if first_stage_a is None:
                continue
            candidate_jobs.add(job)
            if (
                not any(row.identity[0] > first_stage_a for row in rows)
                and not trace_has_terminal_outcome
                and job not in terminal_events
            ):
                errors.append(
                    "missing transition evidence after timing-deferred "
                    f"observation for {job[0]}@{job[1]}"
                )
            matching_stage_b = any(
                row.state == stage_b
                and row.identity[0] > first_stage_a
                and (
                    family != "ST"
                    or (
                        row.scheduler_slack is not None
                        and row.scheduler_slack > 0
                    )
                )
                for row in rows
            )
            if matching_stage_b:
                transition_jobs.add(job)
        timing_activation = bool(transition_jobs)

    target_wait_observed = False
    target_positive_slack_transition = False
    target_transition_after_slack_exhaustion = False
    target_terminated_without_transition = False
    any_target_job_positive_transition_count = 0
    later_target_job_positive_transition_count = 0
    non_target_positive_transition_count = 0
    activation_from_other_job_only = False
    full_release_target_present = False
    full_release_target_selected = False
    full_release_prefix_affordable = False
    runtime_recovery_prefix_matches = False
    runtime_recovery_prefix_names: tuple[str, ...] = ()
    prefix_errors_before = len(errors)
    if target_job_identity is not None and recovery_applicable:
        if target_job_identity not in arrivals:
            errors.append(
                "target runtime job has no matching arrival: "
                f"{runtime_job_id(*target_job_identity)}"
            )
        elif family == "ST":
            first_wait = min((
                row.identity[0] for row in by_job.get(target_job_identity, ())
                if row.state == ST_ENERGY_INSUFFICIENT_SLACK_WAIT
            ), default=None)
            target_wait_observed = first_wait is not None
            target_positive_slack_transition = (
                target_job_identity in transition_jobs
            )
            target_task_positive_jobs = {
                job for job in transition_jobs
                if job[0] == target_runtime_task_name
            }
            any_target_job_positive_transition_count = len(
                target_task_positive_jobs
            )
            later_target_job_positive_transition_count = sum(
                job[1] > target_arrival_time
                for job in target_task_positive_jobs
            )
            if first_wait is not None:
                post_wait_transitions = [
                    row for row in by_job.get(target_job_identity, ())
                    if row.state == ST_AFFORDABLE_ASAP_BEHAVIOR
                    and row.identity[0] > first_wait
                ]
                if not target_positive_slack_transition and any(
                    row.scheduler_slack is not None
                    and row.scheduler_slack <= 0
                    for row in post_wait_transitions
                ):
                    target_transition_after_slack_exhaustion = True
                if not post_wait_transitions and any(
                    terminal_time > first_wait
                    for terminal_time, _ in terminal_events.get(
                        target_job_identity, []
                    )
                ):
                    target_terminated_without_transition = True
            non_target_positive_transition_count = sum(
                job[0] != target_runtime_task_name for job in transition_jobs
            )
            activation_from_other_job_only = (
                non_target_positive_transition_count > 0
                and not target_positive_slack_transition
            )

    if prefix_audit_requested and family == "ST":
        expected_names = tuple(
            str(name) for name in recovery_prefix_runtime_names
        )
        full_tick_rows = []
        for event in events:
            if (
                isinstance(event, dict)
                and event.get("event_type") == "b3_timing_observation"
            ):
                try:
                    if _integer(event.get("time"), "prefix audit time") == (
                        actual_trace_full_tick
                    ):
                        full_tick_rows.append(event)
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
        initial_rows = [
            row for row in full_tick_rows
            if row.get("ready") is True
            and row.get("arrival_time") == target_arrival_time
        ]
        selected_names = tuple(
            str(row.get("task_name", "")) for row in initial_rows
            if row.get("selected") is True
        )
        runtime_recovery_prefix_names = selected_names
        full_release_target_present = any(
            str(row.get("task_name", "")) == target_runtime_task_name
            for row in initial_rows
        )
        full_release_target_selected = any(
            str(row.get("task_name", "")) == target_runtime_task_name
            and row.get("selected") is True
            for row in initial_rows
        )
        runtime_recovery_prefix_matches = selected_names == expected_names
        expected_rows = [
            row for name in expected_names for row in initial_rows
            if str(row.get("task_name", "")) == name
        ]
        full_release_prefix_affordable = (
            len(expected_rows) == len(expected_names)
            and all(
                row.get("selected") is True
                and row.get("decision_energy_affordable") is True
                and row.get("reason_code") != "ST_CHARGE_BEGIN"
                for row in expected_rows
            )
        )
        full_tick_target_transition = any(
            finding.identity == (
                actual_trace_full_tick,
                target_runtime_task_name,
                target_arrival_time,
            )
            and finding.state == ST_AFFORDABLE_ASAP_BEHAVIOR
            and finding.scheduler_slack is not None
            and finding.scheduler_slack > 0
            for finding in findings
        )
        if not full_release_target_present:
            errors.append("initial target job absent at full-battery release tick")
        if not full_release_target_selected:
            errors.append("initial target job not selected at full-battery release tick")
        if not runtime_recovery_prefix_matches:
            errors.append("runtime RM/task-number prefix differs from structure")
        if not full_release_prefix_affordable:
            errors.append("runtime top-q has an energy blocker at full release")
        if not full_tick_target_transition:
            errors.append("initial target lacks positive-slack transition at full release")

    invalid_count = sum(
        finding.state in {ILLEGAL_TIMING_TRANSITION, UNCLASSIFIABLE}
        for finding in findings
    )
    target_audit_error_count = len(errors) + invalid_count
    target_audit_closed = (
        target_job_identity is not None
        and target_audit_error_count == 0
    )
    recovery_prefix_audit_error_count = len(errors) - prefix_errors_before
    if not prefix_identity_valid:
        recovery_prefix_audit_error_count += 1
    recovery_prefix_audit_closed = (
        prefix_audit_requested
        and family == "ST"
        and recovery_prefix_audit_error_count == 0
        and prefix_identity_valid
        and full_release_prefix_affordable
        and runtime_recovery_prefix_matches
    )

    return TimingAuditReport(
        tuple(findings),
        tuple(errors),
        timing_activation=timing_activation,
        same_job_transition_count=len(transition_jobs),
        activation_candidate_job_count=len(candidate_jobs),
        activation_denominator_zero=not candidate_jobs,
        target_wait_observed=target_wait_observed,
        target_positive_slack_transition=target_positive_slack_transition,
        target_transition_after_slack_exhaustion=(
            target_transition_after_slack_exhaustion
        ),
        target_terminated_without_transition=(
            target_terminated_without_transition
        ),
        any_target_job_positive_transition_count=(
            any_target_job_positive_transition_count
        ),
        later_target_job_positive_transition_count=(
            later_target_job_positive_transition_count
        ),
        non_target_positive_transition_count=(
            non_target_positive_transition_count
        ),
        activation_from_other_job_only=activation_from_other_job_only,
        target_audit_closed=target_audit_closed,
        target_audit_error_count=target_audit_error_count,
        full_release_target_present=full_release_target_present,
        full_release_target_selected=full_release_target_selected,
        full_release_prefix_affordable=full_release_prefix_affordable,
        runtime_recovery_prefix_matches=runtime_recovery_prefix_matches,
        runtime_recovery_prefix_names=runtime_recovery_prefix_names,
        recovery_prefix_audit_closed=recovery_prefix_audit_closed,
        recovery_prefix_audit_error_count=recovery_prefix_audit_error_count,
    )
