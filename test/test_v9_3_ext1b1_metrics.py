"""Deterministic scientific tests for EXT-1B1 mechanism metrics."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.v9_3.ext1b_b1_analysis import (
    B1AnalysisError,
    BLOCK_SCHEDULER,
    NONBLOCK_SCHEDULER,
    build_b1_paired_effects,
    build_b1_task_effect_row,
    reconstruct_bypass_episodes,
    summarize_b1,
)
from scripts.run_v9_3_ext1b import runner_with_overrides


def _native_job(task_id: str, release: int = 0):
    return {
        "task_name": f"v93_task_{task_id}",
        "arrival_time": release,
        "job_id": f"v93_task_{task_id}@{release}",
    }


def _decision(
    tick: int, blocked: str = "0", bypassed: str = "1", *, release: int = 0,
):
    return {
        "time": tick,
        "event_type": "scheduler_decision",
        "scheduler": "ASAP-NonBlock",
        "ready_jobs": [
            _native_job(blocked, release), _native_job(bypassed, release),
        ],
        "selected_jobs": [_native_job(bypassed, release)],
    }


def _bypass(
    tick: int, blocked: str = "0", bypassed: str = "1",
):
    return {
        "time": tick,
        "event_type": "nonblock_bypass",
        "scheduler": "ASAP-NonBlock",
        "blocked_higher_priority_task": f"v93_task_{blocked}",
        "bypassed_task": f"v93_task_{bypassed}",
        "reason": "lower_priority_bypass_due_to_energy",
    }


def _scheduled(tick: int, task_id: str = "0", release: int = 0):
    return {
        "time": tick,
        "event_type": "scheduled",
        **_native_job(task_id, release),
    }


def _deadline_miss(tick: int, task_id: str = "0", release: int = 0):
    return {
        "time": tick,
        "event_type": "dline_miss",
        **_native_job(task_id, release),
    }


def _trace(events):
    return {
        "trace_schema_version": 2,
        "configured_scheduler": NONBLOCK_SCHEDULER,
        "events": events,
    }


def _request(pair: str = "pair", scheduler: str = NONBLOCK_SCHEDULER):
    return {
        "request_id": f"{pair}:{scheduler}",
        "paired_instance_id": pair,
        "scenario_kind": "BYPASS_STRESS",
        "scenario_cell_id": "cell",
        "taskset_id": "taskset",
        "taskset_hash": "a" * 64,
        "trace_hash": "b" * 64,
        "simulation_config_hash": "c" * 64,
        "input_hash": "d" * 64,
        "scheduler_id": scheduler,
        "generation_seed": 10,
        "M": 2,
        "initial_battery": "1/2",
        "battery_capacity": "10",
        "horizon": 20,
        "maximum_horizon": 20,
        "priority_hash": "e" * 64,
        "power_hash": "f" * 64,
        "deadline_hash": "1" * 64,
        "release_hash": "2" * 64,
        "workload_vector_hash": "3" * 64,
        "simulator_build_hash": "4" * 64,
    }


def _episodes(
    events, *, status: str = "SIM_PASS_OBSERVED",
    completion_reason: str = "reached_horizon",
):
    return reconstruct_bypass_episodes(
        _trace(events), request=_request(), high_task_id="0", low_task_id="1",
        known_task_ids={"0", "1", "2"}, terminal_status=status,
        terminal_completion_reason=completion_reason,
    )


def test_single_bypass_recovers_at_next_unit_boundary():
    rows = _episodes([_decision(0), _bypass(0), _scheduled(1)])
    assert len(rows) == 1
    assert rows[0]["episode_start_tick"] == 0
    assert rows[0]["recovery_tick"] == 1
    assert rows[0]["recovery_delay_ticks"] == 1
    assert rows[0]["censored"] is False


def test_bypass_gap_without_execution_stays_in_one_open_episode():
    rows = _episodes([
        _decision(10), _bypass(10),
        _decision(12), _bypass(12), _scheduled(13),
    ])
    assert len(rows) == 1
    assert rows[0]["episode_start_tick"] == 10
    assert rows[0]["episode_last_bypass_tick"] == 12
    assert rows[0]["bypass_event_count_in_episode"] == 2
    assert rows[0]["recovery_delay_ticks"] == 3


def test_recovery_boundary_splits_later_bypass_into_second_episode():
    rows = _episodes([
        _decision(0), _bypass(0), _scheduled(1),
        _decision(2), _bypass(2), _scheduled(3),
    ])
    assert [(row["episode_start_tick"], row["recovery_tick"]) for row in rows] == [
        (0, 1), (2, 3),
    ]


def test_unrecovered_episode_is_censored_at_horizon():
    rows = _episodes(
        [_decision(0), _bypass(0)], status="SIM_HORIZON_INSUFFICIENT",
    )
    assert rows[0]["censored"] is True
    assert rows[0]["recovery_tick"] == ""
    assert rows[0]["recovery_delay_ticks"] == ""
    assert rows[0]["censor_reason"] == "HORIZON_CUTOFF"


def test_interleaved_blocked_jobs_keep_independent_open_episodes():
    rows = _episodes([
        _decision(0), _bypass(0),
        _decision(1, blocked="2"), _bypass(1, blocked="2"),
        _decision(2), _bypass(2), _scheduled(3, "0"),
        _decision(4, blocked="2"), _bypass(4, blocked="2"),
        _scheduled(5, "2"),
    ])
    assert len(rows) == 2
    assert {row["blocked_job_id"] for row in rows} == {"0@0", "2@0"}
    assert {
        row["blocked_job_id"]: row["bypass_event_count_in_episode"]
        for row in rows
    } == {"0@0": 2, "2@0": 2}


def test_deadline_miss_does_not_close_episode_when_simulation_continues():
    rows = _episodes([
        _decision(10), _bypass(10), _deadline_miss(11), _scheduled(13),
    ], status="SIM_DEADLINE_MISS")
    assert len(rows) == 1
    assert rows[0]["censored"] is False
    assert rows[0]["recovery_tick"] == 13
    assert rows[0]["recovery_delay_ticks"] == 3
    assert rows[0]["censor_reason"] == ""


def test_deadline_miss_termination_censors_unrecovered_episode():
    rows = _episodes(
        [_decision(10), _bypass(10), _deadline_miss(11)],
        status="SIM_DEADLINE_MISS",
        completion_reason="deadline_miss_terminated",
    )
    assert rows[0]["censored"] is True
    assert rows[0]["censor_reason"] == (
        "DEADLINE_MISS_TERMINATION_BEFORE_RECOVERY"
    )


def test_normal_simulation_end_has_distinct_censor_reason():
    rows = _episodes([_decision(10), _bypass(10)])
    assert rows[0]["censored"] is True
    assert rows[0]["censor_reason"] == "SIMULATION_END_BEFORE_RECOVERY"


def test_zero_bypass_events_produces_zero_episodes():
    assert _episodes([_scheduled(0)]) == []


def _job(
    task_id: str, job_index: int, release: int, response: int | None,
    *, first: int | None = None, missed: bool = False,
    censored: bool = False,
):
    return {
        "task_id": task_id,
        "job_index": job_index,
        "release": release,
        "completion": None if response is None else release + response,
        "absolute_deadline": release + 10,
        "response_time": response,
        "deadline_miss": missed,
        "first_execution": first,
        "preemption_count": 0,
        "energy_blocked_ticks": 0,
        "processor_wait_ticks": None,
        "executed_ticks": 1,
        "eligible_after_warmup": True,
        "censored": censored,
        "censoring_reason": "UNFINISHED_AT_HORIZON" if censored else None,
    }


def _task_row(request, task_id: str, jobs):
    selected = [job for job in jobs if job["task_id"] == task_id]
    return {
        "request_id": request["request_id"],
        "task_id": task_id,
        "observed_jobs": len(selected),
        "completed_jobs": sum(job["completion"] is not None for job in selected),
        "missed_jobs": sum(job["deadline_miss"] for job in selected),
        "censored_jobs": sum(job["censored"] for job in selected),
        "first_execution_time": min(
            (job["first_execution"] for job in selected if job["first_execution"] is not None),
            default="UNAVAILABLE",
        ),
        "maximum_observed_response_time": max(
            (job["response_time"] for job in selected if job["response_time"] is not None),
            default="UNAVAILABLE",
        ),
    }


def _result(request, *, status="SIM_PASS_OBSERVED", eligible=True, idle=0, misses=0):
    return {
        "request_id": request["request_id"],
        "paired_instance_id": request["paired_instance_id"],
        "scenario_kind": "BYPASS_STRESS",
        "scheduler_id": request["scheduler_id"],
        "status": status,
        "comparison_eligible": eligible,
        "idle_cores_while_ready_jobs_exist_ticks": idle,
        "missed_jobs": misses,
    }


def _effect(request, jobs, *, status="SIM_PASS_OBSERVED", eligible=True, idle=0):
    result = _result(request, status=status, eligible=eligible, idle=idle)
    tasks = [_task_row(request, task_id, jobs) for task_id in ("0", "1")]
    return build_b1_task_effect_row(
        request, result, tasks, jobs, {"high_task_id": "0", "low_task_id": "1"},
    ), result


def test_missing_high_or_low_task_link_fails_closed():
    request = _request(scheduler=BLOCK_SCHEDULER)
    jobs = [_job("0", 0, 0, 2, first=0)]
    with pytest.raises(B1AnalysisError, match="link is missing"):
        build_b1_task_effect_row(
            request, _result(request), [_task_row(request, "0", jobs)], jobs,
            {"high_task_id": "0", "low_task_id": "1"},
        )


def test_first_start_response_and_deadline_denominators_are_explicit():
    request = _request(scheduler=BLOCK_SCHEDULER)
    jobs = [
        _job("0", 0, 10, 5, first=12),
        _job("0", 1, 20, None, censored=True),
        _job("0", 2, 30, None, first=31, missed=True),
    ]
    effect = build_b1_task_effect_row(
        request, _result(request, status="SIM_DEADLINE_MISS"),
        [_task_row(request, task_id, jobs) for task_id in ("0", "1")],
        jobs, {"high_task_id": "0", "low_task_id": "1"},
    )
    assert "high_first_execution_time" not in effect
    assert effect["high_response_time_observed_job_count"] == 1
    assert effect["high_response_time_mean"] == 5
    assert effect["high_first_start_observed_job_count"] == 2
    assert effect["high_first_start_delay_mean_ticks"] == 1.5
    assert effect["high_first_start_delay_max_ticks"] == 2
    assert effect["high_deadline_observable_job_count"] == 2
    assert effect["high_deadline_miss_count"] == 1
    assert effect["high_deadline_miss_ratio"] == 0.5
    assert effect["low_response_time_observed_job_count"] == 0
    assert effect["low_response_time_mean"] == ""
    assert effect["low_response_time_denominator_zero"] is True
    assert effect["low_first_start_delay_mean_ticks"] == ""
    assert effect["low_first_start_denominator_zero"] is True
    assert effect["low_deadline_miss_count"] == ""
    assert effect["low_deadline_miss_ratio"] == ""
    assert effect["low_deadline_denominator_zero"] is True


def _scenario():
    return {
        "paired_instance_id": "pair",
        "scenario_kind": "BYPASS_STRESS",
        "scenario_cell_id": "cell",
        "taskset_id": "taskset",
        "taskset_hash": "a" * 64,
    }


def test_block_nonblock_job_identity_mismatch_marks_pair_invalid():
    block_request = _request(scheduler=BLOCK_SCHEDULER)
    nonblock_request = _request(scheduler=NONBLOCK_SCHEDULER)
    block_jobs = [_job("0", 0, 0, 2, first=0), _job("1", 0, 0, 4, first=1)]
    nonblock_jobs = [_job("0", 0, 1, 2, first=1), _job("1", 0, 0, 3, first=0)]
    block_effect, block_result = _effect(block_request, block_jobs)
    nonblock_effect, nonblock_result = _effect(nonblock_request, nonblock_jobs)
    rows = build_b1_paired_effects(
        [_scenario()], [block_request, nonblock_request],
        [block_result, nonblock_result], [block_effect, nonblock_effect], [],
    )
    assert rows[0]["pair_valid"] is False
    assert "job_or_task_identity_mismatch" in rows[0]["pair_failure_reason"]
    assert rows[0]["high_response_mean_delta"] == ""


@pytest.mark.parametrize(
    ("status", "summary_field"),
    [
        ("SIM_RUNTIME_TIMEOUT", "timeout_count"),
        ("SIM_INTERNAL_ERROR", "internal_error_count"),
        ("SIM_HORIZON_INSUFFICIENT", "horizon_insufficient_count"),
    ],
)
def test_timeout_error_and_horizon_are_excluded_from_effect_summary(
    status, summary_field,
):
    block_request = _request(scheduler=BLOCK_SCHEDULER)
    nonblock_request = _request(scheduler=NONBLOCK_SCHEDULER)
    jobs = [_job("0", 0, 0, 2, first=0), _job("1", 0, 0, 4, first=1)]
    block_effect, block_result = _effect(block_request, jobs)
    nonblock_effect, nonblock_result = _effect(
        nonblock_request, [], status=status, eligible=False,
    )
    rows = build_b1_paired_effects(
        [_scenario()], [block_request, nonblock_request],
        [block_result, nonblock_result], [block_effect, nonblock_effect], [],
    )
    summary = summarize_b1(rows, [], [block_result, nonblock_result])
    assert rows[0]["pair_valid"] is False
    assert summary["valid_pairs"] == 0
    assert summary[summary_field] == 1
    assert summary["high_response_mean_delta_pair_count"] == 0
    assert summary["high_response_time_observed_job_count"] == 0
    assert summary["high_response_time_denominator_zero"] is True
    assert summary["high_first_start_observed_job_count"] == 0
    assert summary["high_first_start_denominator_zero"] is True
    assert summary["high_deadline_observable_job_count"] == 0
    assert summary["high_deadline_denominator_zero"] is True
    assert summary["recovery_denominator_zero"] is True
    assert summary["recovery_delay_mean_ticks"] == ""
    assert summary["recovery_delay_median_ticks"] == ""
    assert summary["recovery_delay_max_ticks"] == ""


def test_job_level_paired_delta_direction_is_nonblock_minus_block():
    block_request = _request(scheduler=BLOCK_SCHEDULER)
    nonblock_request = _request(scheduler=NONBLOCK_SCHEDULER)
    block_jobs = [
        _job("0", 0, 0, 2, first=0), _job("0", 1, 10, 4, first=11),
        _job("1", 0, 0, 6, first=2), _job("1", 1, 10, 4, first=11),
    ]
    nonblock_jobs = [
        _job("0", 0, 0, 4, first=1), _job("0", 1, 10, 6, first=12),
        _job("1", 0, 0, 2, first=0), _job("1", 1, 10, 2, first=10),
    ]
    block_effect, block_result = _effect(block_request, block_jobs, idle=5)
    nonblock_effect, nonblock_result = _effect(nonblock_request, nonblock_jobs, idle=2)
    rows = build_b1_paired_effects(
        [_scenario()], [block_request, nonblock_request],
        [block_result, nonblock_result], [block_effect, nonblock_effect], [],
    )
    row = rows[0]
    assert row["pair_valid"] is True
    assert row["high_first_start_delay_mean_delta"] == 1
    assert row["low_first_start_delay_mean_delta"] == -1.5
    assert row["high_response_mean_delta"] == 2
    assert row["low_response_mean_delta"] == -3
    assert row["ready_but_idle_ticks_delta"] == -3
    assert row["high_response_time_observed_job_count"] == 2
    assert row["high_first_start_observed_job_count"] == 2
    assert row["high_deadline_observable_job_count"] == 2
    summary = summarize_b1(rows, [], [block_result, nonblock_result])
    assert summary["high_response_time_observed_job_count"] == 2
    assert summary["high_response_time_denominator_zero"] is False
    assert summary["low_first_start_observed_job_count"] == 2
    assert summary["low_first_start_denominator_zero"] is False
    assert summary["high_deadline_observable_job_count"] == 2
    assert summary["high_deadline_denominator_zero"] is False
    assert summary["resolved_bypass_episode_count"] == 0
    assert summary["recovery_denominator_zero"] is True


def test_pilot_cli_paths_are_noninteractive_and_persisted(tmp_path):
    output = tmp_path / "output"
    simulator = tmp_path / "rtsim"
    simulator.write_text("", encoding="utf-8")
    runner = runner_with_overrides(
        ROOT / "configs" / "v9_3_ext1b1_pilot.yaml",
        output_root=output, simulator_bin=simulator,
    )
    assert runner.root == output.resolve()
    assert runner.config["execution"]["taskset_store"] == str(
        output.resolve() / "taskset_store"
    )
    assert runner.config["simulation"]["retain_trace"] is True
    assert runner.config["simulation"]["simulator_bin"] == str(simulator.resolve())
    assert runner.config["required_outputs"] == [
        "b1_bypass_episodes.csv", "b1_task_effects.csv",
        "b1_paired_effects.csv", "b1_summary.csv",
    ]
