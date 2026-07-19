"""Truth-table and matched-control tests for the B2 batch auditor."""

from __future__ import annotations

from copy import deepcopy

import pytest

from experiments.v9_3.ext1b_b2_batch_audit import (
    B2BatchAuditError,
    B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH,
    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER,
    B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER,
    B2_STATE_CONTINUATION_CANDIDATE_WAIT,
    B2_STATE_CONTINUATION_ONLY,
    B2_STATE_ILLEGAL_PARTIAL_LAUNCH,
    B2_STATE_ILLEGAL_TRANSITION,
    B2_STATE_NOT_APPLICABLE,
    B2_STATE_UNCLASSIFIABLE,
    CONTROL_STATUS_ELIGIBLE_MATCHED_STATE,
    CONTROL_STATUS_NOT_APPLICABLE,
    NATIVE_ENERGY_EPSILON_MJ,
    audit_asap_block_pair_control,
    audit_asap_sync_document,
    summarize_b2_observations,
)


TASKSET_HASH = "d" * 64


def _job(task, energy=1.0, *, arrival=0, remaining=2):
    return {
        "task_name": f"v93_task_{task}",
        "arrival_time": arrival,
        "priority": task + 10,
        "ready_order": task,
        "task_unit_energy_mJ": energy,
        "task_unit_energy_mJ_exact": format(energy, ".17g"),
        "remaining_time_ms": remaining,
        "absolute_deadline": 20,
    }


def _scheduled(task, tick, *, arrival=0):
    return {
        "time": tick,
        "event_type": "scheduled",
        "task_name": f"v93_task_{task}",
        "arrival_time": arrival,
    }


def _decision(tick, ready, selected, available, *, scheduler="ASAP-Sync", reason="sync_batch_selected"):
    return {
        "time": tick,
        "event_type": "scheduler_decision",
        "scheduler": scheduler,
        "available_energy_mJ": available,
        "available_energy_mJ_exact": format(available, ".17g"),
        "ready_jobs": deepcopy(ready),
        "selected_jobs": deepcopy(selected),
        "decision_reason": reason,
    }


def _block(tick, jobs, required, available, *, feasible=True):
    return {
        "time": tick,
        "event_type": "sync_batch_block",
        "scheduler": "ASAP-Sync",
        "batch_tasks": deepcopy(jobs),
        "batch_required_energy_mJ": required,
        "batch_required_energy_mJ_exact": format(required, ".17g"),
        "available_energy_mJ": available,
        "available_energy_mJ_exact": format(available, ".17g"),
        "feasible_subset_exists": feasible,
        "reason": "sync_batch_energy_insufficient",
    }


def _candidate_wait(tick, active, continuations, candidates, available):
    continuation = sum(job["task_unit_energy_mJ"] for job in continuations)
    candidate = sum(job["task_unit_energy_mJ"] for job in candidates)
    residual = max(0.0, available - continuation)
    affordable = [
        residual + NATIVE_ENERGY_EPSILON_MJ >= job["task_unit_energy_mJ"]
        for job in candidates
    ]
    return {
        "time": tick,
        "event_type": "sync_batch_candidate_wait",
        "scheduler": "ASAP-Sync",
        "reason": "continuation_preserved_new_candidate_batch_energy_insufficient",
        "active_top_m_tasks": deepcopy(active),
        "continuation_tasks": deepcopy(continuations),
        "new_candidate_tasks": deepcopy(candidates),
        "selected_tasks": deepcopy(continuations),
        "active_top_m_count": len(active),
        "continuation_count": len(continuations),
        "new_candidate_count": len(candidates),
        "selected_count": len(continuations),
        "active_top_m_required_energy_mJ": continuation + candidate,
        "active_top_m_required_energy_mJ_exact": format(
            continuation + candidate, ".17g"
        ),
        "continuation_required_energy_mJ": continuation,
        "continuation_required_energy_mJ_exact": format(continuation, ".17g"),
        "new_candidate_required_energy_mJ": candidate,
        "new_candidate_required_energy_mJ_exact": format(candidate, ".17g"),
        "available_energy_before_decision_mJ": available,
        "available_energy_before_decision_mJ_exact": format(available, ".17g"),
        "residual_energy_after_continuation_reservation_mJ": residual,
        "residual_energy_after_continuation_reservation_mJ_exact": format(
            residual, ".17g"
        ),
        "whole_active_top_m_affordable": False,
        "all_new_candidates_affordable_after_continuation": all(affordable),
        "feasible_new_candidate_subset_exists": any(affordable),
        "native_affordability_epsilon_mJ": NATIVE_ENERGY_EPSILON_MJ,
        "native_affordability_epsilon_mJ_exact": format(
            NATIVE_ENERGY_EPSILON_MJ, ".17g"
        ),
    }


def _trace(events, *, scheduler="gpfp_asap_sync"):
    return {
        "events": events,
        "trace_schema_version": 2,
        "run_id": "request-b2",
        "taskset_semantic_hash": TASKSET_HASH,
        "configured_scheduler": scheduler,
    }


def _audit(document, processors=2):
    rows = audit_asap_sync_document(document, processors=processors, pair_id="pair-b2")
    assert len(rows) == 1
    return rows[0]


def test_exhaustive_truth_table_for_legal_mechanism_states():
    two = [_job(0), _job(1)]
    affordable = _audit(_trace([
        _decision(0, two, two, 2.0), _scheduled(0, 0), _scheduled(1, 0),
    ]))
    assert affordable["classified_state"] == B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH

    atomic_wait = _audit(_trace([
        _decision(0, two, [], 1.5, reason="sync_batch_energy_insufficient"),
        _block(0, two, 2.0, 1.5),
    ]))
    assert atomic_wait["classified_state"] == (
        B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER
    )

    expensive = [_job(0, 2.0), _job(1, 2.0)]
    no_member = _audit(_trace([
        _decision(0, expensive, [], 1.5, reason="sync_batch_energy_insufficient"),
        _block(0, expensive, 4.0, 1.5, feasible=False),
    ]))
    assert no_member["classified_state"] == (
        B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER
    )

    one = [_job(0)]
    not_applicable = _audit(_trace([
        _decision(0, one, one, 1.0), _scheduled(0, 0),
    ]))
    assert not_applicable["classified_state"] == B2_STATE_NOT_APPLICABLE


def test_q0_general_block_is_continuation_only_not_atomic_wait():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _scheduled(0, 0), _scheduled(1, 0),
        _decision(1, jobs, [], 0.5, reason="sync_batch_energy_insufficient"),
        _block(1, jobs, 2.0, 0.5, feasible=False),
    ]))
    assert row["classified_state"] == B2_STATE_CONTINUATION_ONLY
    assert row["q0_general_block"] is True
    assert row["general_block_nonatomic"] is True
    assert row["atomic_opportunity"] is False


def test_continuation_candidate_wait_is_distinct_atomic_opportunity():
    jobs = [_job(0), _job(1), _job(2)]
    row = _audit(_trace([
        _scheduled(0, 0),
        _decision(1, jobs, jobs[:1], 2.5, reason="sync_batch_energy_insufficient"),
        _candidate_wait(1, jobs, jobs[:1], jobs[1:], 2.5),
    ]), processors=3)
    assert row["classified_state"] == B2_STATE_CONTINUATION_CANDIDATE_WAIT
    assert row["atomic_wait_with_affordable_member"] is True


def test_partial_launch_and_other_transition_are_separate_illegal_states():
    jobs = [_job(0), _job(1)]
    partial = _audit(_trace([
        _decision(0, jobs, jobs[:1], 2.0), _scheduled(0, 0),
    ]))
    assert partial["classified_state"] == B2_STATE_ILLEGAL_PARTIAL_LAUNCH

    missing_launch = _audit(_trace([_decision(0, jobs, jobs, 2.0)]))
    assert missing_launch["classified_state"] == B2_STATE_ILLEGAL_TRANSITION


def test_missing_core_energy_evidence_remains_unclassifiable():
    jobs = [_job(0), _job(1)]
    del jobs[1]["task_unit_energy_mJ"]
    row = _audit(_trace([_decision(
        0, jobs, [], 2.0, reason="sync_batch_energy_insufficient",
    )]))
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE


def test_exact_energy_strings_override_display_numbers():
    jobs = [_job(0, 0.0465), _job(1, 1.0)]
    for job in jobs:
        job["task_unit_energy_mJ_exact"] = format(job["task_unit_energy_mJ"], ".17g")
    decision = _decision(
        1, jobs, jobs[:1], 1.01109, reason="sync_batch_energy_insufficient",
    )
    decision["available_energy_mJ_exact"] = "1.011088"
    wait = _candidate_wait(1, jobs, jobs[:1], jobs[1:], 1.01109)
    wait.update({
        "active_top_m_required_energy_mJ_exact": "1.0465",
        "continuation_required_energy_mJ_exact": "0.0465",
        "new_candidate_required_energy_mJ_exact": "1",
        "available_energy_before_decision_mJ_exact": "1.011088",
        "residual_energy_after_continuation_reservation_mJ_exact": "0.964588",
        "native_affordability_epsilon_mJ_exact": "9.9999999999999995e-07",
    })
    row = _audit(_trace([_scheduled(0, 0), decision, wait]))
    assert row["classified_state"] == B2_STATE_CONTINUATION_CANDIDATE_WAIT
    assert row["precision_source"] == "MAX_DIGITS10_EXACT_STRING"


def test_malformed_exact_energy_fails_closed():
    jobs = [_job(0), _job(1)]
    decision = _decision(0, jobs, [], 1.5, reason="sync_batch_energy_insufficient")
    decision["available_energy_mJ_exact"] = 1.5
    with pytest.raises(B2BatchAuditError, match="must be text"):
        audit_asap_sync_document(_trace([decision]), processors=2)


def test_missing_exact_energy_fails_closed():
    jobs = [_job(0), _job(1)]
    decision = _decision(0, jobs, [], 1.5, reason="sync_batch_energy_insufficient")
    del decision["available_energy_mJ_exact"]
    with pytest.raises(B2BatchAuditError, match="missing required"):
        audit_asap_sync_document(_trace([decision]), processors=2)


def _sync_atomic_wait(jobs, available, *, tick=0):
    return _audit(_trace([
        _decision(tick, jobs, [], available, reason="sync_batch_energy_insufficient"),
        _block(tick, jobs, sum(job["task_unit_energy_mJ"] for job in jobs), available),
    ]))


def _block_trace(jobs, selected, available, *, tick=0, scheduled=()):
    return _trace([
        _decision(
            tick, jobs, selected, available, scheduler="ASAP-Block",
            reason="prefix_energy_insufficient",
        ),
        *[_scheduled(task, tick) for task in scheduled],
    ], scheduler="gpfp_asap_block")


def test_matched_t0_control_is_eligible_and_passes():
    jobs = [_job(0), _job(1)]
    sync = _sync_atomic_wait(jobs, 1.5)
    control = audit_asap_block_pair_control(
        [sync], _block_trace(jobs, jobs[:1], 1.5, scheduled=(0,)),
        processors=2, expected_min_prefix_length=1,
    )[0]
    assert control["control_status"] == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
    assert control["control_passed"] is True


def test_same_tick_post_divergence_is_not_comparable():
    jobs = [_job(0), _job(1)]
    sync = _sync_atomic_wait(jobs, 1.5, tick=7)
    control = audit_asap_block_pair_control(
        [sync], _block_trace(jobs, jobs[:1], 1.4, tick=7, scheduled=(0,)),
        processors=2, expected_min_prefix_length=1,
    )[0]
    assert control["control_status"] == CONTROL_STATUS_NOT_APPLICABLE
    assert control["control_passed"] is None
    assert "available_energy_mJ" in control["incomparable_state_components"]


def test_later_fully_rematched_state_can_be_eligible():
    jobs = [_job(0), _job(1)]
    sync = _sync_atomic_wait(jobs, 1.5, tick=9)
    control = audit_asap_block_pair_control(
        [sync], _block_trace(jobs, jobs[:1], 1.5, tick=9, scheduled=(0,)),
        processors=2, expected_min_prefix_length=1,
    )[0]
    assert control["control_status"] == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
    assert control["control_passed"] is True


def test_matched_state_without_affordable_priority_prefix_is_not_applicable():
    jobs = [_job(0, 2.0), _job(1, 1.0)]
    sync = _sync_atomic_wait(jobs, 1.5, tick=4)
    control = audit_asap_block_pair_control(
        [sync], _block_trace(jobs, [], 1.5, tick=4),
        processors=2, expected_min_prefix_length=1,
    )[0]
    assert control["control_status"] == CONTROL_STATUS_NOT_APPLICABLE
    assert control["not_applicable_reason"] == (
        "matched_state_has_no_affordable_priority_prefix"
    )


def test_matched_block_control_failure_is_reported():
    jobs = [_job(0), _job(1)]
    sync = _sync_atomic_wait(jobs, 1.5)
    control = audit_asap_block_pair_control(
        [sync], _block_trace(jobs, [], 1.5),
        processors=2, expected_min_prefix_length=1,
    )[0]
    assert control["control_status"] == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
    assert control["control_passed"] is False
    summary = summarize_b2_observations([sync], [control])
    assert summary["matched_control_failure_count"] == 1


def test_metric_excludes_no_affordable_member_and_fails_closed_at_zero_denominator():
    jobs = [_job(0), _job(1)]
    launch = _audit(_trace([
        _decision(0, jobs, jobs, 2.0), _scheduled(0, 0), _scheduled(1, 0),
    ]))
    wait = _sync_atomic_wait(jobs, 1.5)
    expensive = [_job(0, 2.0), _job(1, 2.0)]
    no_member = _audit(_trace([
        _decision(0, expensive, [], 1.5, reason="sync_batch_energy_insufficient"),
        _block(0, expensive, 4.0, 1.5, feasible=False),
    ]))
    summary = summarize_b2_observations([launch, wait, no_member])
    assert summary["no_affordable_member_count"] == 1
    assert summary["active_batch_opportunity_count"] == 2
    assert summary["atomic_wait_share"] == "1/2"
    excluded_only = summarize_b2_observations([no_member])
    assert excluded_only["active_batch_opportunity_count"] == 0
    assert excluded_only["atomic_wait_share"] is None


def test_fingerprint_material_contains_no_scheduler_or_result_label():
    jobs = [_job(0), _job(1)]
    row = _sync_atomic_wait(jobs, 1.5)
    material = row["predecision_state_material"]
    serialized = str(material).lower()
    assert "scheduler" not in serialized
    assert "decision_reason" not in serialized
    assert "selected_jobs" not in serialized


def test_deterministic_m4_atomic_wait_and_block_prefix_control_microcase():
    jobs = [_job(index, 1.0) for index in range(4)]
    sync_rows = audit_asap_sync_document(
        _trace([
            _decision(
                0, jobs, [], 1.5,
                reason="sync_batch_energy_insufficient",
            ),
            _block(0, jobs, 4.0, 1.5),
        ]),
        processors=4,
        pair_id="pair-b2-m4",
    )
    assert len(sync_rows) == 1
    sync = sync_rows[0]
    assert len(sync["active_top_m_job_ids"]) == 4
    assert sync["candidate_count"] == 4
    assert sync["whole_batch_affordable"] is False
    assert sync["feasible_subset_exists"] is True
    assert sync["affordable_prefix_length"] == 1
    assert sync["actual_launch_count"] == 0
    assert sync["classified_state"] == (
        B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER
    )

    controls = audit_asap_block_pair_control(
        sync_rows,
        _block_trace(jobs, jobs[:1], 1.5, scheduled=(0,)),
        processors=4,
        expected_min_prefix_length=1,
    )
    assert len(controls) == 1
    assert controls[0]["control_status"] == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
    assert controls[0]["control_passed"] is True
    assert controls[0]["selected_job_ids"] == ["v93_task_0@0"]
    assert controls[0]["actually_launched_job_ids"] == ["v93_task_0@0"]

    summary = summarize_b2_observations(
        sync_rows, controls, require_matched_controls=True,
    )
    assert summary["atomic_wait_with_affordable_member_count"] == 1
    assert summary["active_batch_opportunity_count"] == 1
    assert summary["illegal_partial_count"] == 0
    assert summary["illegal_transition_count"] == 0
    assert summary["state_unclassifiable_count"] == 0
    assert summary["matched_control_failure_count"] == 0
    assert summary["matched_control_success_count"] == 1
    assert summary["control_not_applicable_count"] == 0
    assert summary["control_evidence_incomplete_count"] == 0
