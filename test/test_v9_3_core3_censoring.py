from __future__ import annotations

from fractions import Fraction
import json

import pytest

from experiments.v9_3.censoring import (
    censoring_label,
    next_horizon,
    task_is_tightness_eligible,
)
from experiments.v9_3.simulation_result import (
    SimulationStatus,
    parse_simulation_trace,
)
from v9_3_core3_helpers import task_payload, write_trace


HASH = "a" * 64
SCHEDULERS = (
    "gpfp_alap_block", "gpfp_alap_nonblock", "gpfp_alap_sync",
    "gpfp_asap_block", "gpfp_asap_nonblock", "gpfp_asap_sync",
    "gpfp_st_block", "gpfp_st_nonblock", "gpfp_st_sync",
)


def parse(path, *, minimum=1, e0=Fraction(1)):
    return parse_simulation_trace(
        path, task_payload(), expected_taskset_hash=HASH, horizon=10,
        warmup=0, minimum_jobs_per_task=minimum, release_e0=e0,
    )


def _contract_document(
    *, horizon, releases, completions=(), misses=(), blocked=(),
    c=2, d=5, t=10, scheduler="gpfp_asap_block",
):
    name = "v93_task_0"
    completion_by_release = dict(completions)
    misses = set(misses)
    blocked = set(blocked)
    events = []
    for release in releases:
        events.append({
            "run_generation": 1, "time": release, "event_type": "arrival",
            "task_name": name, "arrival_time": release,
            "current_energy_mJ": 20000,
        })
        completion = completion_by_release.get(release)
        if completion is not None:
            events.append({
                "run_generation": 1, "time": release,
                "event_type": "scheduled", "task_name": name,
                "arrival_time": release, "task_unit_energy_mJ": 100,
            })
            for tick in range(release, completion):
                job = {
                    "task_name": name, "arrival_time": release,
                    "remaining_time_ms": completion - tick,
                    "absolute_deadline": release + d,
                }
                events.append({
                    "run_generation": 1, "time": tick,
                    "event_type": "scheduler_decision",
                    "ready_jobs": [job], "selected_jobs": [job],
                    "decision_reason": "selected_prefix",
                })
            events.append({
                "run_generation": 1, "time": completion,
                "event_type": "end_instance", "task_name": name,
                "arrival_time": release, "task_unit_energy_mJ": 100,
            })
        if release in blocked:
            for tick in range(release, release + d):
                job = {
                    "task_name": name, "arrival_time": release,
                    "remaining_time_ms": c,
                    "absolute_deadline": release + d,
                }
                events.append({
                    "run_generation": 1, "time": tick,
                    "event_type": "scheduler_decision",
                    "ready_jobs": [job], "selected_jobs": [],
                    "decision_reason": "highest_priority_energy_insufficient",
                })
        if release in misses:
            events.append({
                "run_generation": 1, "time": release + d,
                "event_type": "dline_miss", "task_name": name,
                "arrival_time": release, "deadline": release + d,
                "remaining_execution_ms": c,
            })
    event_order = {
        "dline_miss": 0, "arrival": 1, "scheduled": 2,
        "scheduler_decision": 3, "end_instance": 4,
    }
    events.sort(key=lambda event: (
        event["time"], event_order.get(event["event_type"], 5)
    ))
    events.append({
        "run_generation": 1, "time": horizon,
        "event_type": "simulation_run_outcome",
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
    })
    return {
        "events": events, "trace_schema_version": 2, "run_count": 1,
        "target_run_generation": 1, "run_generation": 1,
        "run_id": "observation-contract-test",
        "taskset_semantic_hash": HASH,
        "configured_scheduler": scheduler,
        "scheduler_display_name": scheduler,
        "scheduler_implementation": scheduler,
        "expected_simulation_horizon_ms": horizon,
        "observed_simulation_end_ms": horizon,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
    }


def _parse_contract(
    tmp_path, name, *, minimum=2, c=2, d=5, t=10,
    scheduler="gpfp_asap_block", **document,
):
    path = tmp_path / name
    path.write_text(json.dumps(_contract_document(
        c=c, d=d, t=t, scheduler=scheduler, **document,
    )), encoding="utf-8")
    return parse_simulation_trace(
        path, task_payload(c=c, d=d, t=t), expected_taskset_hash=HASH,
        horizon=document["horizon"], warmup=0,
        minimum_jobs_per_task=minimum, release_e0=Fraction(1),
        expected_scheduler=scheduler,
    )


def test_response_time_release_completion_and_job_fields(tmp_path):
    result = parse(write_trace(tmp_path / "trace.json"))
    assert result.status is SimulationStatus.PASS_OBSERVED
    job = result.jobs[0]
    assert (job.release, job.completion, job.absolute_deadline) == (0, 2, 5)
    assert job.response_time == 2
    assert job.first_execution == 0
    assert job.executed_ticks == 2
    assert job.processor_wait_ticks == 0
    assert job.preemption_count == 0


def test_deadline_miss_detection_is_not_horizon_censoring(tmp_path):
    result = parse(write_trace(
        tmp_path / "miss.json", completion=None, deadline_miss=True
    ))
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert result.jobs[0].deadline_miss
    assert not result.jobs[0].censored


def test_minimum_observed_jobs_causes_horizon_insufficient(tmp_path):
    result = parse(write_trace(tmp_path / "short.json"), minimum=2)
    assert result.status is SimulationStatus.HORIZON_INSUFFICIENT
    assert not result.tasks[0].minimum_jobs_satisfied
    assert censoring_label(result.tasks[0]) == "MINIMUM_JOBS_NOT_OBSERVED"
    assert not task_is_tightness_eligible(result, result.tasks[0])


def test_unfinished_horizon_tail_is_job_level_right_censored(tmp_path):
    result = parse(write_trace(tmp_path / "tail.json", completion=None))
    assert result.status is SimulationStatus.HORIZON_INSUFFICIENT
    job = result.jobs[0]
    assert job.censored and job.censoring_reason == "UNFINISHED_AT_HORIZON"
    assert result.tasks[0].censored_jobs == 1


def test_release_time_e0_is_validated_independently_of_initial_battery(tmp_path):
    result = parse(write_trace(tmp_path / "energy.json", energy_j=0.5), e0=Fraction(1))
    assert not result.release_e0_valid
    assert not result.comparison_eligible


def test_horizon_extension_policy_is_bounded_and_deterministic():
    assert next_horizon(10, 35, "double") == 20
    assert next_horizon(20, 35, "double") == 35
    assert next_horizon(35, 35, "double") is None
    assert next_horizon(10, 35, "none") is None
    with pytest.raises(ValueError, match="unknown"):
        next_horizon(10, 20, "grow")


def test_synchronous_periodic_jobs_have_two_completions_at_h_required(tmp_path):
    result = _parse_contract(
        tmp_path, "two-at-required.json", horizon=16,
        releases=(0, 10), completions=((0, 2), (10, 12)),
    )
    assert result.status is SimulationStatus.PASS_OBSERVED
    assert result.tasks[0].completed_jobs == 2
    assert result.tasks[0].minimum_jobs_satisfied


def test_unfinishable_second_job_misses_at_its_deadline(tmp_path):
    result = _parse_contract(
        tmp_path, "second-miss.json", horizon=16,
        releases=(0, 10), completions=((0, 2),), misses=(10,),
    )
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert [(job.release, job.deadline_miss) for job in result.jobs] == [
        (0, False), (10, True),
    ]
    assert result.metrics["first_miss_time"] == 15


def test_energy_blocked_never_scheduled_job_still_misses(tmp_path):
    result = _parse_contract(
        tmp_path, "blocked-miss.json", minimum=1, horizon=6,
        releases=(0,), misses=(0,), blocked=(0,),
    )
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert result.jobs[0].first_execution is None
    assert result.jobs[0].energy_blocked_ticks == 5


@pytest.mark.parametrize("scheduler", SCHEDULERS)
def test_deadline_classification_is_scheduler_independent(tmp_path, scheduler):
    result = _parse_contract(
        tmp_path, f"{scheduler}.json", horizon=6, releases=(0,),
        misses=(0,), scheduler=scheduler,
    )
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert result.jobs[0].absolute_deadline == 5


def test_pending_job_is_not_overwritten_by_next_release(tmp_path):
    result = _parse_contract(
        tmp_path, "pending-next-release.json", minimum=1,
        c=2, d=5, t=5, horizon=7, releases=(0, 5),
        completions=((5, 7),), misses=(0,),
    )
    assert [(job.job_index, job.release) for job in result.jobs] == [
        (0, 0), (1, 5),
    ]
    assert result.jobs[0].deadline_miss
    assert result.jobs[1].completion == 7


def test_same_tick_deadline_and_release_have_distinct_job_identities(tmp_path):
    result = _parse_contract(
        tmp_path, "same-tick.json", minimum=1,
        c=2, d=5, t=5, horizon=6, releases=(0, 5), misses=(0,),
    )
    first, second = result.jobs
    assert first.absolute_deadline == second.release == 5
    assert first.deadline_miss and second.censored
    assert (first.job_index, second.job_index) == (0, 1)


def test_explicit_miss_overrides_two_job_minimum(tmp_path):
    result = _parse_contract(
        tmp_path, "explicit-overrides-minimum.json", horizon=6,
        releases=(0,), misses=(0,), minimum=2,
    )
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert not result.tasks[0].minimum_jobs_satisfied


def test_two_completions_satisfy_minimum_observation(tmp_path):
    result = _parse_contract(
        tmp_path, "two-completions.json", horizon=16,
        releases=(0, 10), completions=((0, 2), (10, 12)), minimum=2,
    )
    assert result.status is SimulationStatus.PASS_OBSERVED
    assert result.tasks[0].observed_jobs == 2
    assert result.tasks[0].completed_jobs == 2


def test_h_required_minus_one_is_insufficient(tmp_path):
    result = _parse_contract(
        tmp_path, "before-required.json", horizon=15,
        releases=(0, 10), completions=((0, 2),), minimum=2,
    )
    assert result.status is SimulationStatus.HORIZON_INSUFFICIENT
    assert result.jobs[1].censored


def test_h_required_observes_terminal_deadline_event(tmp_path):
    result = _parse_contract(
        tmp_path, "at-required.json", horizon=16,
        releases=(0, 10), completions=((0, 2),), misses=(10,), minimum=2,
    )
    assert result.status is SimulationStatus.DEADLINE_MISS
    assert result.jobs[1].absolute_deadline == 15
