"""Deterministic trace-parser tests for EXT-1B2 batch semantics."""

from __future__ import annotations

from copy import deepcopy

import pytest

from experiments.v9_3.ext1b_b2_batch_trace import (
    B2BatchTraceError,
    B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH,
    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT,
    B2_STATE_ILLEGAL_PARTIAL_LAUNCH,
    B2_STATE_NO_BATCH,
    B2_STATE_UNCLASSIFIABLE,
    NATIVE_ENERGY_EPSILON_MJ,
    audit_asap_block_pair_control,
    audit_asap_sync_document,
)


TASKSET_HASH = "b" * 64


def _job(task: int, energy: float = 1.0, *, arrival: int = 0):
    return {
        "task_name": f"v93_task_{task}",
        "arrival_time": arrival,
        "priority": task + 10,
        "ready_order": task,
        "task_unit_energy_mJ": energy,
        "remaining_time_ms": 2,
        "absolute_deadline": 20,
    }


def _scheduled(task: int, tick: int, *, arrival: int = 0):
    return {
        "time": tick,
        "event_type": "scheduled",
        "task_name": f"v93_task_{task}",
        "arrival_time": arrival,
        "task_unit_energy_mJ": 1.0,
    }


def _decision(
    tick: int,
    ready,
    selected,
    available: float,
    *,
    scheduler: str = "ASAP-Sync",
    reason: str = "sync_batch_selected",
):
    return {
        "time": tick,
        "event_type": "scheduler_decision",
        "scheduler": scheduler,
        "available_energy_mJ": available,
        "ready_jobs": deepcopy(ready),
        "selected_jobs": deepcopy(selected),
        "decision_reason": reason,
    }


def _block(tick: int, jobs, required: float, available: float):
    return {
        "time": tick,
        "event_type": "sync_batch_block",
        "scheduler": "ASAP-Sync",
        "batch_tasks": deepcopy(jobs),
        "batch_required_energy_mJ": required,
        "available_energy_mJ": available,
        "feasible_subset_exists": True,
        "reason": "sync_batch_energy_insufficient",
    }


def _candidate_wait(
    tick: int,
    active,
    continuations,
    candidates,
    available: float,
):
    continuation_required = sum(job["task_unit_energy_mJ"] for job in continuations)
    candidate_required = sum(job["task_unit_energy_mJ"] for job in candidates)
    residual = max(0.0, available - continuation_required)
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
        "active_top_m_required_energy_mJ": continuation_required + candidate_required,
        "continuation_required_energy_mJ": continuation_required,
        "new_candidate_required_energy_mJ": candidate_required,
        "available_energy_before_decision_mJ": available,
        "residual_energy_after_continuation_reservation_mJ": residual,
        "whole_active_top_m_affordable": False,
        "all_new_candidates_affordable_after_continuation": all(affordable),
        "feasible_new_candidate_subset_exists": any(affordable),
        "native_affordability_epsilon_mJ": NATIVE_ENERGY_EPSILON_MJ,
    }


def _trace(events, *, scheduler: str = "gpfp_asap_sync"):
    return {
        "events": events,
        "trace_schema_version": 2,
        "run_id": "request-b2",
        "taskset_semantic_hash": TASKSET_HASH,
        "configured_scheduler": scheduler,
    }


def _audit(document, processors: int = 2):
    rows = audit_asap_sync_document(
        document, processors=processors, pair_id="pair-b2",
    )
    assert len(rows) == 1
    return rows[0]


def _unaffordable_trace():
    jobs = [_job(0), _job(1)]
    return _trace([
        _decision(
            0, jobs, [], 1.5,
            reason="sync_batch_energy_insufficient",
        ),
        _block(0, jobs, 2.0, 1.5),
    ])


def test_no_batch_when_active_top_m_is_all_continuation():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _scheduled(0, 0),
        _scheduled(1, 0),
        _decision(1, jobs, jobs, 2.0),
    ]))
    assert row["classified_state"] == B2_STATE_NO_BATCH
    assert row["candidate_count"] == 0
    assert row["continuation_job_ids"] == ["v93_task_0@0", "v93_task_1@0"]
    assert row["actual_launch_count"] == 0


def test_whole_batch_affordable_launches_complete_candidate_set_same_tick():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _decision(0, jobs, jobs, 2.0),
        _scheduled(1, 0),
        _scheduled(0, 0),
    ]))
    assert row["classified_state"] == B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH
    assert row["candidate_count"] == row["selected_count"] == row["actual_launch_count"] == 2
    assert row["partial_launch_violation"] is False


def test_whole_batch_unaffordable_waits_when_single_job_is_affordable():
    row = _audit(_unaffordable_trace())
    assert row["classified_state"] == B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT
    assert row["whole_batch_affordable"] is False
    assert row["affordable_prefix_length"] == 1
    assert row["selected_count"] == row["actual_launch_count"] == 0
    assert row["sync_batch_block_present"] is True


def test_illegal_partial_selection_is_a_p0_violation():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _decision(0, jobs, jobs[:1], 2.0),
        _scheduled(0, 0),
    ]))
    assert row["classified_state"] == B2_STATE_ILLEGAL_PARTIAL_LAUNCH
    assert "partial_selection_count" in row["partial_launch_violation_reasons"]


def test_illegal_partial_actual_launch_is_a_p0_violation():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _decision(0, jobs, jobs, 2.0),
        _scheduled(0, 0),
    ]))
    assert row["classified_state"] == B2_STATE_ILLEGAL_PARTIAL_LAUNCH
    assert "partial_actual_launch_count" in row["partial_launch_violation_reasons"]


def test_blocked_event_candidate_identity_mismatch_is_unclassifiable():
    document = _unaffordable_trace()
    document["events"][1]["batch_tasks"][1] = _job(2)
    row = _audit(document)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "blocked_event_candidate_identity_mismatch" in row["classification_errors"]


def test_blocked_event_required_energy_mismatch_is_unclassifiable():
    document = _unaffordable_trace()
    document["events"][1]["batch_required_energy_mJ"] = 2.5
    row = _audit(document)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "blocked_event_required_energy_mismatch" in row["classification_errors"]


def test_blocked_event_available_energy_mismatch_is_unclassifiable():
    document = _unaffordable_trace()
    document["events"][1]["available_energy_mJ"] = 1.25
    row = _audit(document)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "blocked_event_available_energy_mismatch" in row["classification_errors"]


def test_complete_selection_with_zero_launch_is_illegal_not_unclassifiable():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([_decision(0, jobs, jobs, 2.0)]))
    assert row["classified_state"] == B2_STATE_ILLEGAL_PARTIAL_LAUNCH
    assert "complete_selection_incomplete_same_tick_launch" in row[
        "partial_launch_violation_reasons"
    ]


def test_continuation_is_not_counted_as_new_selection_or_launch():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _scheduled(0, 0),
        _decision(1, jobs, jobs, 2.0),
        _scheduled(1, 1),
    ]))
    assert row["classified_state"] == B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH
    assert row["continuation_job_ids"] == ["v93_task_0@0"]
    assert row["candidate_job_ids"] == ["v93_task_1@0"]
    assert row["selected_job_ids"] == ["v93_task_1@0"]
    assert row["actually_launched_job_ids"] == ["v93_task_1@0"]


def test_native_epsilon_boundary_is_affordable():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _decision(0, jobs, jobs, 2.0 - NATIVE_ENERGY_EPSILON_MJ),
        _scheduled(0, 0),
        _scheduled(1, 0),
    ]))
    assert row["whole_batch_affordable"] is True
    assert row["classified_state"] == B2_STATE_BATCH_AFFORDABLE_ATOMIC_LAUNCH


def test_missing_trace_energy_returns_unclassifiable_instead_of_guessing():
    jobs = [_job(0), _job(1)]
    del jobs[1]["task_unit_energy_mJ"]
    selected = [_job(0), _job(1)]
    row = _audit(_trace([
        _decision(0, jobs, selected, 2.0),
        _scheduled(0, 0),
        _scheduled(1, 0),
    ]))
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "missing_batch_job_energy" in row["classification_errors"]


def test_unaffordable_decision_without_block_event_is_unclassifiable():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([_decision(
        0, jobs, [], 1.5, reason="sync_batch_energy_insufficient",
    )]))
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "missing_sync_batch_block_evidence" in row["classification_errors"]


def test_native_continuation_wait_branch_exposes_missing_block_evidence():
    jobs = [_job(0), _job(1)]
    row = _audit(_trace([
        _scheduled(0, 0),
        _decision(
            1, jobs, jobs[:1], 1.5,
            reason="sync_batch_energy_insufficient",
        ),
    ]))
    assert row["continuation_job_ids"] == ["v93_task_0@0"]
    assert row["candidate_job_ids"] == ["v93_task_1@0"]
    assert row["selected_count"] == row["actual_launch_count"] == 0
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "missing_sync_batch_block_evidence" in row["classification_errors"]


def _continuation_wait_trace(*, available: float = 2.5):
    jobs = [_job(0), _job(1), _job(2)]
    return _trace([
        _scheduled(0, 0),
        _decision(
            1, jobs, jobs[:1], available,
            reason="sync_batch_energy_insufficient",
        ),
        _candidate_wait(1, jobs, jobs[:1], jobs[1:], available),
    ])


def test_valid_continuation_wait_event_closes_unaffordable_atomic_wait():
    row = _audit(_continuation_wait_trace(), processors=3)
    assert row["classified_state"] == B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT
    assert row["sync_batch_block_present"] is False
    assert row["sync_batch_candidate_wait_present"] is True
    assert row["continuation_job_ids"] == ["v93_task_0@0"]
    assert row["candidate_job_ids"] == ["v93_task_1@0", "v93_task_2@0"]
    assert row["actually_launched_job_ids"] == []
    assert row["feasible_subset_exists"] is True


def test_candidate_wait_continuation_identity_mismatch_is_unclassifiable():
    document = _continuation_wait_trace()
    document["events"][2]["continuation_tasks"] = [_job(1)]
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "candidate_wait_continuation_tasks_identity_mismatch" in row[
        "classification_errors"
    ]


def test_candidate_wait_candidate_identity_mismatch_is_unclassifiable():
    document = _continuation_wait_trace()
    document["events"][2]["new_candidate_tasks"][1] = _job(3)
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "candidate_wait_new_candidate_tasks_identity_mismatch" in row[
        "classification_errors"
    ]


def test_candidate_wait_total_energy_mismatch_is_unclassifiable():
    document = _continuation_wait_trace()
    document["events"][2]["active_top_m_required_energy_mJ"] = 3.5
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "candidate_wait_active_top_m_required_energy_mJ_mismatch" in row[
        "classification_errors"
    ]


def test_candidate_wait_residual_energy_mismatch_is_unclassifiable():
    document = _continuation_wait_trace()
    document["events"][2][
        "residual_energy_after_continuation_reservation_mJ"
    ] = 1.25
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert (
        "candidate_wait_residual_energy_after_continuation_reservation_mJ_mismatch"
        in row["classification_errors"]
    )


def test_candidate_wait_wrong_decision_reason_is_unclassifiable():
    document = _continuation_wait_trace()
    document["events"][1]["decision_reason"] = "sync_batch_selected"
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "candidate_wait_decision_reason_mismatch" in row[
        "classification_errors"
    ]


def test_candidate_wait_missing_required_field_is_unclassifiable():
    document = _continuation_wait_trace()
    del document["events"][2]["new_candidate_required_energy_mJ"]
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "candidate_wait_new_candidate_required_energy_mJ_missing" in row[
        "classification_errors"
    ]


def test_candidate_wait_wrong_native_epsilon_is_unclassifiable():
    document = _continuation_wait_trace()
    document["events"][2]["native_affordability_epsilon_mJ"] = 0.0
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_UNCLASSIFIABLE
    assert "candidate_wait_native_affordability_epsilon_mJ_mismatch" in row[
        "classification_errors"
    ]


def test_candidate_wait_with_new_candidate_launch_is_illegal():
    document = _continuation_wait_trace()
    document["events"].append(_scheduled(1, 1))
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_ILLEGAL_PARTIAL_LAUNCH
    assert "partial_actual_launch_count" in row["partial_launch_violation_reasons"]
    assert "unaffordable_batch_member_launched" in row[
        "partial_launch_violation_reasons"
    ]


def test_candidate_wait_continuation_affordability_native_epsilon_boundary():
    jobs = [_job(0), _job(1, 0.25), _job(2, 0.25)]
    available = 1.0 - NATIVE_ENERGY_EPSILON_MJ
    document = _trace([
        _scheduled(0, 0),
        _decision(
            1, jobs, jobs[:1], available,
            reason="sync_batch_energy_insufficient",
        ),
        _candidate_wait(1, jobs, jobs[:1], jobs[1:], available),
    ])
    row = _audit(document, processors=3)
    assert row["classified_state"] == B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT
    assert row["residual_energy_after_continuation_reservation_mJ"] == 0.0
    assert row["actually_launched_job_ids"] == []


def test_asap_block_pair_control_proves_positive_affordable_prefix_launch():
    sync_row = _audit(_unaffordable_trace())
    jobs = [_job(0), _job(1)]
    block_document = _trace([
        _decision(
            0, jobs, jobs[:1], 1.5,
            scheduler="ASAP-Block", reason="prefix_energy_insufficient",
        ),
        _scheduled(0, 0),
    ], scheduler="gpfp_asap_block")
    controls = audit_asap_block_pair_control(
        [sync_row], block_document, processors=2,
        expected_min_prefix_length=1,
    )
    assert len(controls) == 1
    assert controls[0]["control_passed"] is True


def test_asap_block_pair_control_rejects_different_taskset_hash():
    sync_row = _audit(_unaffordable_trace())
    jobs = [_job(0), _job(1)]
    block_document = _trace([
        _decision(
            0, jobs, jobs[:1], 1.5,
            scheduler="ASAP-Block", reason="prefix_energy_insufficient",
        ),
        _scheduled(0, 0),
    ], scheduler="gpfp_asap_block")
    block_document["taskset_semantic_hash"] = "c" * 64
    control = audit_asap_block_pair_control(
        [sync_row], block_document, processors=2,
        expected_min_prefix_length=1,
    )[0]
    assert control["control_passed"] is False
    assert "taskset_semantic_hash_mismatch" in control["control_errors"]


def test_invalid_top_level_taskset_identity_fails_closed():
    document = _unaffordable_trace()
    document["taskset_semantic_hash"] = "UNAVAILABLE"
    with pytest.raises(B2BatchTraceError, match="invalid taskset_semantic_hash"):
        audit_asap_sync_document(document, processors=2)


@pytest.mark.parametrize("processors", [0, -1, True])
def test_invalid_processor_count_fails_closed(processors):
    with pytest.raises(ValueError, match="positive integer"):
        audit_asap_sync_document(_unaffordable_trace(), processors=processors)
