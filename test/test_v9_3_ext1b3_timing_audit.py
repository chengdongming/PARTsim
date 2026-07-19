import json
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test"))

from experiments.v9_3.ext1b_b3_timing_audit import (  # noqa: E402
    ALAP_POSITIVE_SLACK_DEFER,
    ALAP_URGENT_ELIGIBILITY,
    ASAP_IMMEDIATE_ELIGIBILITY,
    B3TimingAuditError,
    ILLEGAL_TIMING_TRANSITION,
    NOT_APPLICABLE,
    ST_AFFORDABLE_ASAP_BEHAVIOR,
    ST_ENERGY_INSUFFICIENT_SLACK_WAIT,
    UNCLASSIFIABLE,
    audit_timing_trace,
    classify_timing_event,
)
from test_scheduler_trace_identity import run_scheduler as _run_scheduler  # noqa: E402


IDENTITIES = {
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


def observation(scheduler, kind="selected", **updates):
    display, family, policy = IDENTITIES[scheduler]
    event = {
        "time": 0,
        "event_type": "b3_timing_observation",
        "scheduler": display,
        "scheduler_family": family,
        "blocking_policy": policy,
        "task_name": "H",
        "task_id": "H",
        "arrival_time": 0,
        "job_id": "H@0",
        "remaining_time_ms": 2.0,
        "rounded_remaining_ms": 2,
        "absolute_deadline": 10,
        "scheduler_slack": 8,
        "ready": True,
        "timing_gate_open": True,
        "cpu_available": True,
        "continuation": False,
        "selected": True,
        "job_required_energy_mJ": 2.0,
        "decision_required_energy_mJ": 2.0,
        "available_energy_mJ": 3.0,
        "job_energy_affordable": True,
        "decision_energy_affordable": True,
        "native_epsilon_mJ": 1e-6,
        "blocking_policy_reason": "NONE",
        "actual_outcome": "DISPATCH_SELECTED",
        "reason_code": "NATIVE_SELECTED",
    }
    if kind == "deferred":
        event.update({
            "timing_gate_open": False,
            "selected": False,
            "actual_outcome": "TIMING_DEFERRED",
            "reason_code": "NATIVE_TIMING_DEFER",
        })
    elif kind == "blocked":
        event.update({
            "selected": False,
            "actual_outcome": "BLOCKED",
            "reason_code": "NATIVE_BLOCKED",
        })
    event.update(updates)
    return event


def finding(scheduler, event, *, dispatch=True, running=False):
    identity = (event.get("time"), event.get("task_name"), event.get("arrival_time"))
    return classify_timing_event(
        event,
        configured_scheduler=scheduler,
        scheduled_identities={identity} if dispatch else set(),
        running_before_identities={identity} if running else set(),
    )


TRUTH_TABLE = []
for scheduler in (
    "gpfp_asap_block", "gpfp_asap_nonblock", "gpfp_asap_sync"
):
    TRUTH_TABLE.append((
        scheduler, observation(scheduler), True,
        ASAP_IMMEDIATE_ELIGIBILITY,
    ))
for scheduler in (
    "gpfp_alap_block", "gpfp_alap_nonblock", "gpfp_alap_sync"
):
    TRUTH_TABLE.append((
        scheduler, observation(scheduler, "deferred"), False,
        ALAP_POSITIVE_SLACK_DEFER,
    ))
    TRUTH_TABLE.append((
        scheduler,
        observation(
            scheduler,
            absolute_deadline=2,
            scheduler_slack=0,
        ),
        True,
        ALAP_URGENT_ELIGIBILITY,
    ))
for scheduler in (
    "gpfp_st_block", "gpfp_st_nonblock", "gpfp_st_sync"
):
    TRUTH_TABLE.append((
        scheduler, observation(scheduler), True,
        ST_AFFORDABLE_ASAP_BEHAVIOR,
    ))
    TRUTH_TABLE.append((
        scheduler,
        observation(
            scheduler,
            "deferred",
            available_energy_mJ=1.0,
            job_energy_affordable=False,
            decision_energy_affordable=False,
        ),
        False,
        ST_ENERGY_INSUFFICIENT_SLACK_WAIT,
    ))

TRUTH_TABLE.extend([
    (
        "gpfp_asap_block",
        observation("gpfp_asap_block", "blocked", ready=False),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_asap_block",
        observation(
            "gpfp_asap_block", "blocked",
            available_energy_mJ=1.0,
            job_energy_affordable=False,
            decision_energy_affordable=False,
            blocking_policy_reason="ENERGY_INSUFFICIENT",
        ),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_asap_block",
        observation(
            "gpfp_asap_block", "blocked",
            cpu_available=False,
            blocking_policy_reason="CPU_CAPACITY",
        ),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_asap_nonblock",
        observation(
            "gpfp_asap_nonblock", "blocked",
            blocking_policy_reason="NONBLOCK_BYPASS",
        ),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_asap_sync",
        observation(
            "gpfp_asap_sync", "blocked",
            blocking_policy_reason="SYNC_ATOMIC_BATCH_WAIT",
        ),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_alap_block",
        observation(
            "gpfp_alap_block", "blocked",
            timing_gate_open=False,
            available_energy_mJ=1.0,
            job_energy_affordable=False,
            decision_energy_affordable=False,
            blocking_policy_reason="ENERGY_INSUFFICIENT",
        ),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_alap_block",
        observation(
            "gpfp_alap_block", "blocked",
            absolute_deadline=2,
            scheduler_slack=0,
            blocking_policy_reason="BLOCK_HEAD_OF_LINE",
        ),
        False,
        ALAP_URGENT_ELIGIBILITY,
    ),
    (
        "gpfp_alap_block",
        observation(
            "gpfp_alap_block", "blocked",
            absolute_deadline=2,
            scheduler_slack=0,
            available_energy_mJ=1.0,
            job_energy_affordable=False,
            decision_energy_affordable=False,
            blocking_policy_reason="ENERGY_INSUFFICIENT",
        ),
        False,
        ALAP_URGENT_ELIGIBILITY,
    ),
    (
        "gpfp_alap_block",
        observation(
            "gpfp_alap_block",
            absolute_deadline=1,
            scheduler_slack=-1,
        ),
        True,
        ALAP_URGENT_ELIGIBILITY,
    ),
    (
        "gpfp_st_block",
        observation(
            "gpfp_st_block", "blocked",
            absolute_deadline=2,
            scheduler_slack=0,
            available_energy_mJ=1.0,
            job_energy_affordable=False,
            decision_energy_affordable=False,
            blocking_policy_reason="ENERGY_INSUFFICIENT",
        ),
        False,
        NOT_APPLICABLE,
    ),
    (
        "gpfp_st_sync",
        observation(
            "gpfp_st_sync", "blocked",
            timing_gate_open=False,
            available_energy_mJ=1.0,
            job_energy_affordable=False,
            decision_energy_affordable=False,
            blocking_policy_reason="SYNC_ATOMIC_BATCH_WAIT",
        ),
        False,
        NOT_APPLICABLE,
    ),
])


@pytest.mark.parametrize(
    "scheduler,event,dispatch,expected",
    TRUTH_TABLE,
    ids=[f"truth-{index:02d}" for index in range(len(TRUTH_TABLE))],
)
def test_b3_truth_table(scheduler, event, dispatch, expected):
    assert finding(scheduler, event, dispatch=dispatch).state == expected


ILLEGAL_CASES = [
    observation("gpfp_asap_block", "deferred"),
    observation(
        "gpfp_asap_block", "deferred", timing_gate_open=True
    ),
    observation(
        "gpfp_alap_block", "deferred", timing_gate_open=True
    ),
    observation(
        "gpfp_alap_block", "deferred", absolute_deadline=2,
        scheduler_slack=0, timing_gate_open=False,
    ),
    observation(
        "gpfp_st_block", "deferred", timing_gate_open=False
    ),
    observation(
        "gpfp_st_block", "deferred", absolute_deadline=2,
        scheduler_slack=0, timing_gate_open=False,
        available_energy_mJ=1.0, job_energy_affordable=False,
        decision_energy_affordable=False,
    ),
]


@pytest.mark.parametrize("event", ILLEGAL_CASES)
def test_impossible_native_transitions_are_illegal(event):
    scheduler = next(
        key for key, identity in IDENTITIES.items()
        if identity[0] == event["scheduler"]
    )
    assert finding(scheduler, event, dispatch=False).state == (
        ILLEGAL_TIMING_TRANSITION
    )


@pytest.mark.parametrize("mutation", [
    lambda event: event.pop("scheduler_slack"),
    lambda event: event.update(scheduler_slack=7),
    lambda event: event.update(job_id="H@1"),
    lambda event: event.update(job_energy_affordable=False),
    lambda event: event.update(scheduler_family="ST"),
])
def test_malformed_evidence_is_unclassifiable(mutation):
    event = observation("gpfp_asap_block")
    mutation(event)
    assert finding("gpfp_asap_block", event).state == UNCLASSIFIABLE


def test_dispatch_and_continuation_evidence_are_fail_closed():
    event = observation("gpfp_asap_block")
    assert finding("gpfp_asap_block", event, dispatch=False).state == (
        UNCLASSIFIABLE
    )
    event.update(
        continuation=True,
        actual_outcome="CONTINUE_SELECTED",
        reason_code="NATIVE_CONTINUATION",
    )
    assert finding(
        "gpfp_asap_block", event, dispatch=False, running=True
    ).state == ASAP_IMMEDIATE_ELIGIBILITY


def test_running_continuation_may_be_blocked_without_becoming_unclassifiable():
    event = observation(
        "gpfp_asap_sync", "blocked",
        continuation=True,
        available_energy_mJ=0.0,
        job_energy_affordable=False,
        decision_energy_affordable=False,
        blocking_policy_reason="SYNC_ATOMIC_BATCH_WAIT",
    )
    assert finding(
        "gpfp_asap_sync", event, dispatch=False, running=True
    ).state == NOT_APPLICABLE


def trace_document(events):
    return {
        "trace_schema_version": 2,
        "configured_scheduler": "gpfp_asap_block",
        "events": events,
    }


def scheduled_event():
    return {
        "time": "0",
        "event_type": "scheduled",
        "task_name": "H",
        "arrival_time": "0",
    }


def arrival_event():
    return {
        "time": "0",
        "event_type": "arrival",
        "task_name": "H",
        "arrival_time": "0",
    }


def test_trace_requires_b3_event(tmp_path):
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(trace_document([])), encoding="utf-8")
    report = audit_timing_trace(path, expected_scheduler="gpfp_asap_block")
    with pytest.raises(B3TimingAuditError, match="missing"):
        report.assert_audit_closed()


def test_trace_rejects_duplicate_observation_identity(tmp_path):
    event = observation("gpfp_asap_block")
    path = tmp_path / "duplicate.json"
    path.write_text(
        json.dumps(trace_document([
            arrival_event(), scheduled_event(), event, event
        ])),
        encoding="utf-8",
    )
    report = audit_timing_trace(path, expected_scheduler="gpfp_asap_block")
    assert report.findings[-1].state == UNCLASSIFIABLE
    with pytest.raises(B3TimingAuditError, match="duplicate"):
        report.assert_audit_closed()


def test_trace_rejects_duplicate_json_key(tmp_path):
    path = tmp_path / "duplicate-key.json"
    path.write_text(
        '{"trace_schema_version":2,"trace_schema_version":2,"events":[]}',
        encoding="utf-8",
    )
    with pytest.raises(B3TimingAuditError, match="duplicate JSON key"):
        audit_timing_trace(path, expected_scheduler="gpfp_asap_block")


def test_complete_trace_closes_audit(tmp_path):
    path = tmp_path / "complete.json"
    path.write_text(
        json.dumps(trace_document([
            arrival_event(), scheduled_event(),
            observation("gpfp_asap_block")
        ])),
        encoding="utf-8",
    )
    report = audit_timing_trace(path, expected_scheduler="gpfp_asap_block")
    report.assert_audit_closed()
    assert report.state_counts[ASAP_IMMEDIATE_ELIGIBILITY] == 1


@pytest.mark.parametrize("scheduler,expected", [
    ("gpfp_asap_block", ASAP_IMMEDIATE_ELIGIBILITY),
    ("gpfp_asap_nonblock", ASAP_IMMEDIATE_ELIGIBILITY),
    ("gpfp_asap_sync", ASAP_IMMEDIATE_ELIGIBILITY),
    ("gpfp_alap_block", ALAP_POSITIVE_SLACK_DEFER),
    ("gpfp_alap_nonblock", ALAP_POSITIVE_SLACK_DEFER),
    ("gpfp_alap_sync", ALAP_POSITIVE_SLACK_DEFER),
    ("gpfp_st_block", ST_AFFORDABLE_ASAP_BEHAVIOR),
    ("gpfp_st_nonblock", ST_AFFORDABLE_ASAP_BEHAVIOR),
    ("gpfp_st_sync", ST_AFFORDABLE_ASAP_BEHAVIOR),
])
def test_real_nine_scheduler_microcase_emits_auditable_state(
        tmp_path, scheduler, expected):
    completed, trace = _run_scheduler(tmp_path, scheduler)
    assert completed.returncode == 0, completed.stderr
    scheduled = {
        (int(event["time"]), event["task_name"], int(event["arrival_time"]))
        for event in trace["events"]
        if event.get("event_type") == "scheduled"
    }
    observations = [
        event for event in trace["events"]
        if event.get("event_type") == "b3_timing_observation"
    ]
    assert observations
    report = audit_timing_trace(
        tmp_path / f"{scheduler}.json",
        expected_scheduler=scheduler,
    )
    report.assert_audit_closed()
    findings = [
        classify_timing_event(
            event,
            configured_scheduler=scheduler,
            scheduled_identities=scheduled,
        )
        for event in observations
    ]
    assert all(finding.state not in {
        ILLEGAL_TIMING_TRANSITION, UNCLASSIFIABLE
    } for finding in findings), findings
    assert expected in {finding.state for finding in findings}


@pytest.mark.parametrize("scheduler", [
    "gpfp_alap_block", "gpfp_alap_nonblock", "gpfp_alap_sync",
])
def test_real_alap_zero_slack_is_urgent(tmp_path, scheduler):
    completed, trace = _run_scheduler(
        tmp_path, scheduler, wcet=2, deadline=2, duration=2
    )
    assert completed.returncode == 0, completed.stderr
    scheduled = {
        (int(event["time"]), event["task_name"], int(event["arrival_time"]))
        for event in trace["events"]
        if event.get("event_type") == "scheduled"
    }
    findings = [
        classify_timing_event(
            event,
            configured_scheduler=scheduler,
            scheduled_identities=scheduled,
        )
        for event in trace["events"]
        if event.get("event_type") == "b3_timing_observation"
    ]
    assert ALAP_URGENT_ELIGIBILITY in {
        finding.state for finding in findings
    }


@pytest.mark.parametrize("scheduler", [
    "gpfp_st_block", "gpfp_st_nonblock", "gpfp_st_sync",
])
def test_real_st_energy_shortage_with_positive_slack_waits(
        tmp_path, scheduler):
    completed, trace = _run_scheduler(
        tmp_path,
        scheduler,
        initial=0.0,
        maximum=1.0,
        harvest=0.0,
        duration=2,
        wcet=1,
        deadline=20,
    )
    assert completed.returncode == 0, completed.stderr
    findings = [
        classify_timing_event(
            event,
            configured_scheduler=scheduler,
            scheduled_identities=set(),
        )
        for event in trace["events"]
        if event.get("event_type") == "b3_timing_observation"
    ]
    assert findings
    assert all(finding.state not in {
        ILLEGAL_TIMING_TRANSITION, UNCLASSIFIABLE
    } for finding in findings), findings
    assert ST_ENERGY_INSUFFICIENT_SLACK_WAIT in {
        finding.state for finding in findings
    }


@pytest.mark.parametrize("scheduler,deadline,expected", [
    ("gpfp_asap_block", 20, ASAP_IMMEDIATE_ELIGIBILITY),
    ("gpfp_asap_nonblock", 20, ASAP_IMMEDIATE_ELIGIBILITY),
    ("gpfp_asap_sync", 20, ASAP_IMMEDIATE_ELIGIBILITY),
    ("gpfp_alap_block", 3, ALAP_URGENT_ELIGIBILITY),
    ("gpfp_alap_nonblock", 3, ALAP_URGENT_ELIGIBILITY),
    ("gpfp_alap_sync", 3, ALAP_URGENT_ELIGIBILITY),
    ("gpfp_st_block", 20, ST_AFFORDABLE_ASAP_BEHAVIOR),
    ("gpfp_st_nonblock", 20, ST_AFFORDABLE_ASAP_BEHAVIOR),
    ("gpfp_st_sync", 20, ST_AFFORDABLE_ASAP_BEHAVIOR),
])
def test_real_selected_continuation_is_proved_from_running_lifecycle(
        tmp_path, scheduler, deadline, expected):
    completed, trace = _run_scheduler(
        tmp_path,
        scheduler,
        initial=1.0,
        maximum=1.0,
        duration=3,
        wcet=3,
        deadline=deadline,
    )
    assert completed.returncode == 0, completed.stderr
    report = audit_timing_trace(
        tmp_path / f"{scheduler}.json",
        expected_scheduler=scheduler,
    )
    report.assert_audit_closed()
    continuation_events = [
        event for event in trace["events"]
        if event.get("event_type") == "b3_timing_observation"
        and event.get("actual_outcome") == "CONTINUE_SELECTED"
    ]
    assert continuation_events
    assert expected in {
        finding.state for finding in report.findings
        if finding.identity[0] in {
            int(event["time"]) for event in continuation_events
        }
    }
