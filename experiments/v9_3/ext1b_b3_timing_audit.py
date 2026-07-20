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
) -> TimingAuditReport:
    data = _strict_json(path)
    errors: list[str] = []
    if data.get("trace_schema_version") != 2:
        errors.append("timing audit requires trace schema version 2")
    if data.get("configured_scheduler") != expected_scheduler:
        errors.append("configured scheduler mismatch")

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

    return TimingAuditReport(
        tuple(findings),
        tuple(errors),
        timing_activation=timing_activation,
        same_job_transition_count=len(transition_jobs),
        activation_candidate_job_count=len(candidate_jobs),
        activation_denominator_zero=not candidate_jobs,
    )
