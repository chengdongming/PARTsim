from __future__ import annotations

from fractions import Fraction

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


def parse(path, *, minimum=1, e0=Fraction(1)):
    return parse_simulation_trace(
        path, task_payload(), expected_taskset_hash=HASH, horizon=10,
        warmup=0, minimum_jobs_per_task=minimum, release_e0=e0,
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
