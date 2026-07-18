"""B1 trace-parser regressions for active jobs at a deadline miss."""

from __future__ import annotations

from fractions import Fraction
import json

import pytest

from experiments.v9_3.simulation_result import (
    SimulationStatus,
    SimulationTraceError,
    parse_simulation_trace,
)


TASKSET_HASH = "a" * 64
TASK_NAME = "v93_task_0"


def _task_payload(*, c: int, d: int, horizon: int):
    return [{
        "task_id": "0",
        "priority_rank": 0,
        "C": c,
        "D": d,
        "T": horizon + 1,
        "P": "1/10",
        "D_over_T": str(Fraction(d, horizon + 1)),
        "workload": "control",
        "arrival_offset": 0,
    }]


def _event(time: int, event_type: str, **fields):
    return {
        "run_generation": 1,
        "time": time,
        "event_type": event_type,
        "task_name": TASK_NAME,
        "arrival_time": 0,
        **fields,
    }


def _parse(tmp_path, name: str, *, c: int, d: int, horizon: int, events):
    document = {
        "events": [
            _event(0, "arrival", current_energy_mJ=20000),
            *events,
            {
                "run_generation": 1,
                "time": horizon,
                "event_type": "simulation_run_outcome",
                "simulation_completed": True,
                "simulation_completion_reason": "reached_horizon",
            },
        ],
        "trace_schema_version": 2,
        "run_count": 1,
        "target_run_generation": 1,
        "run_generation": 1,
        "run_id": "ext1b-active-at-miss",
        "taskset_semantic_hash": TASKSET_HASH,
        "configured_scheduler": "gpfp_asap_block",
        "scheduler_display_name": "gpfp_asap_block",
        "scheduler_implementation": "gpfp_asap_block",
        "expected_simulation_horizon_ms": horizon,
        "observed_simulation_end_ms": horizon,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
    }
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return parse_simulation_trace(
        path,
        _task_payload(c=c, d=d, horizon=horizon),
        expected_taskset_hash=TASKSET_HASH,
        horizon=horizon,
        warmup=0,
        minimum_jobs_per_task=1,
        release_e0=Fraction(1),
        expected_scheduler="gpfp_asap_block",
    )


def _scheduled(time: int):
    return _event(time, "scheduled", task_unit_energy_mJ=100)


def _descheduled(time: int):
    return _event(time, "descheduled", reason="preemption")


def _miss(time: int, remaining: int):
    return _event(
        time,
        "dline_miss",
        job_id=f"{TASK_NAME}@0",
        deadline=time,
        remaining_execution_ms=remaining,
    )


def test_active_interval_is_settled_at_deadline_miss(tmp_path):
    result = _parse(
        tmp_path,
        "active-single-miss.json",
        c=7,
        d=9,
        horizon=10,
        events=[_scheduled(4), _miss(9, 2)],
    )
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert result.jobs[0].executed_ticks == 5
    assert result.jobs[0].executed_ticks + 2 == 7


def test_resumed_active_interval_is_settled_at_deadline_miss(tmp_path):
    result = _parse(
        tmp_path,
        "active-resumed-miss.json",
        c=9,
        d=13,
        horizon=14,
        events=[
            _scheduled(4),
            _descheduled(7),
            _scheduled(8),
            _miss(13, 1),
        ],
    )
    assert result.jobs[0].executed_ticks == 8
    assert result.jobs[0].preemption_count == 1
    assert result.jobs[0].executed_ticks + 1 == 9


def test_closed_interval_is_not_counted_again_at_deadline_miss(tmp_path):
    result = _parse(
        tmp_path,
        "closed-before-miss.json",
        c=9,
        d=13,
        horizon=14,
        events=[_scheduled(4), _descheduled(7), _miss(13, 6)],
    )
    assert result.jobs[0].executed_ticks == 3
    assert result.jobs[0].preemption_count == 1
    assert result.jobs[0].executed_ticks + 6 == 9


def test_deadline_miss_execution_conflict_fails_closed(tmp_path):
    with pytest.raises(
        SimulationTraceError,
        match="deadline-miss execution invariant failed",
    ) as captured:
        _parse(
            tmp_path,
            "active-miss-conflict.json",
            c=9,
            d=13,
            horizon=14,
            events=[
                _scheduled(4),
                _descheduled(7),
                _scheduled(8),
                _miss(13, 2),
            ],
        )
    message = str(captured.value)
    for evidence in (
        "request=active-miss-conflict",
        "job='v93_task_0@0'",
        "wcet=9",
        "executed=8",
        "remaining=2",
        "miss_time=13",
        "running_interval=[8,13)",
    ):
        assert evidence in message
