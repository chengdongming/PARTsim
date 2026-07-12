import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from unittest import mock

import pytest


os.environ.setdefault("MPLBACKEND", "Agg")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance


ALGORITHM = "gpfp_asap_block"


def identity(algorithm=ALGORITHM):
    return {
        "run_id": "test-run",
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": algorithm,
        "scheduler_display_name": acceptance.ALGO_DISPLAY_NAMES[algorithm],
        "scheduler_implementation": (
            acceptance.SCHEDULER_IMPLEMENTATIONS[algorithm]
        ),
    }


def write_trace(path, horizon, *, deadline_miss=False, metadata=None):
    events = [
        {"run_generation": 1, "time": "0", "event_type": "arrival",
         "task_name": "task_0"},
        {"run_generation": 1, "time": str(horizon),
         "event_type": "idle"},
    ]
    if deadline_miss:
        events.insert(1, {
            "run_generation": 1,
            "time": "1", "event_type": "dline_miss",
            "task_name": "task_0",
            "job_id": "task_0@0",
            "arrival_time": "0",
            "deadline": "1",
            "remaining_execution_ms": 1,
        })
    payload = {
        "events": events,
        "trace_schema_version": acceptance.TRACE_SCHEMA_VERSION,
        "run_count": 1,
        "run_generation": 1,
        "target_run_generation": 1,
        "expected_simulation_horizon_ms": horizon,
        "observed_simulation_end_ms": horizon,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
    }
    payload.update(identity() if metadata is None else metadata)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("horizon", [100, 500, 30000])
def test_trace_parser_uses_actual_simulation_horizon(tmp_path, horizon):
    trace = tmp_path / "trace.json"
    write_trace(trace, horizon)
    result = acceptance.TraceParser(str(trace)).evaluate(horizon)
    assert result.status == "accepted"
    assert result.acceptance_ratio == 1.0
    assert result.expected_horizon_ms == horizon
    assert result.observed_horizon_ms == horizon


def test_trace_parser_prefers_run_completion_metadata(tmp_path):
    trace = tmp_path / "trace.json"
    payload = {
        **identity(),
        "expected_simulation_horizon_ms": 500,
        "observed_simulation_end_ms": 500,
            "trace_schema_version": acceptance.TRACE_SCHEMA_VERSION,
            "run_count": 1,
            "run_generation": 1,
            "target_run_generation": 1,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
        "events": [
                {"run_generation": 1, "time": "0",
                 "event_type": "arrival", "task_name": "task_0"},
                {"run_generation": 1, "time": "400",
                 "event_type": "idle"},
        ],
    }
    trace.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "accepted"
    assert result.observed_horizon_ms == 500


def test_formal_parser_rejects_multiple_run_generations_before_miss(tmp_path):
    trace = tmp_path / "multiple_runs.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload["run_count"] = 2
    payload["target_run_generation"] = 2
    payload["events"] = [
        {
            "run_generation": 1, "time": "1",
            "event_type": "dline_miss", "task_name": "task_0",
            "job_id": "task_0@0", "arrival_time": "0",
            "deadline": "1", "remaining_execution_ms": 1,
        },
        {
            "run_generation": 2, "time": "0",
            "event_type": "arrival", "task_name": "task_0",
        },
        {"run_generation": 2, "time": "500", "event_type": "idle"},
    ]
    trace.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "multiple_simulation_runs_not_supported"


@pytest.mark.parametrize("value", [None, True, 0, -1, "1"])
def test_formal_parser_uses_invalid_run_count_taxonomy(tmp_path, value):
    trace = tmp_path / "invalid_run_count.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    if value is None:
        payload.pop("run_count")
    else:
        payload["run_count"] = value
    trace.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "invalid_run_count"


def test_formal_parser_rejects_event_without_run_generation(tmp_path):
    trace = tmp_path / "missing_generation.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload["events"][0].pop("run_generation")
    trace.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "missing_run_generation"


def test_formal_parser_rejects_target_generation_mismatch(tmp_path):
    trace = tmp_path / "generation_mismatch.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload["target_run_generation"] = 7
    trace.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "run_generation_mismatch"


@pytest.mark.parametrize(
    "value",
    [None, True, "not-a-sha256", "b" * 64],
)
def test_formal_parser_requires_matching_taskset_semantic_hash(
        tmp_path, value):
    trace = tmp_path / "taskset_identity.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    if value is None:
        payload.pop("taskset_semantic_hash")
    else:
        payload["taskset_semantic_hash"] = value
    trace.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(trace)).evaluate(
        500, expected_taskset_semantic_hash="a" * 64
    )
    assert result.status == "error"
    assert result.reason == "taskset_semantic_hash_mismatch"


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("malformed", "malformed_trace"),
        ("empty", "empty_trace"),
        ("missing", "missing_trace"),
    ],
)
def test_invalid_trace_is_error_not_rejected(tmp_path, kind, reason):
    trace = tmp_path / "trace.json"
    if kind == "malformed":
        trace.write_text("{not-json", encoding="utf-8")
    elif kind == "empty":
        trace.write_text(json.dumps({"events": [], **identity()}),
                         encoding="utf-8")

    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == reason
    assert math.isnan(result.acceptance_ratio)


def worker_task(tmp_path, horizon=500, algorithm=ALGORITHM):
    return (
        algorithm,
        str(tmp_path / "system.yml"),
        str(tmp_path / "tasks.yml"),
        0,
        0.5,
        horizon,
        str(tmp_path),
        {"taskset_semantic_hash": "a" * 64},
    )


def test_worker_propagates_horizon_and_identity(tmp_path):
    observed_command = []

    def simulator(command, **kwargs):
        observed_command.extend(command)
        trace = Path(command[command.index("-t") + 1])
        write_trace(trace, 500)
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(acceptance.subprocess, "run", simulator):
        result = acceptance.run_single_simulation_worker(worker_task(tmp_path))

    assert result["simulation_status"] == "accepted"
    assert result["accepted"] and not result["rejected"]
    assert not result["error"] and not result["timeout"]
    assert result["expected_simulation_horizon_ms"] == 500
    assert result["observed_trace_horizon_ms"] == 500
    assert result["configured_scheduler"] == ALGORITHM
    assert result["scheduler_implementation"] == (
        acceptance.SCHEDULER_IMPLEMENTATIONS[ALGORITHM]
    )
    assert result["observed_configured_scheduler"] == ALGORITHM
    assert result["expected_configured_scheduler"] == ALGORITHM
    assert result["simulation_completed"] is True
    assert result["simulation_completion_reason"] == "reached_horizon"
    assert observed_command[
        observed_command.index("--taskset-semantic-hash") + 1
    ] == "a" * 64


def test_worker_uses_explicit_fresh_binary_environment(tmp_path, monkeypatch):
    selected = tmp_path / "fresh" / "rtsim" / "rtsim"
    monkeypatch.setenv("PARTSIM_RTSIM_BIN", str(selected))
    observed = []

    def simulator(command, **kwargs):
        observed.append(command)
        trace = Path(command[command.index("-t") + 1])
        write_trace(trace, 500)
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(acceptance.subprocess, "run", simulator):
        result = acceptance.run_single_simulation_worker(worker_task(tmp_path))
    assert result["simulation_status"] == "accepted"
    assert Path(observed[0][0]) == selected


@pytest.mark.parametrize("kind", ["malformed", "empty", "missing"])
def test_worker_classifies_invalid_trace_as_error(tmp_path, kind):
    def simulator(command, **kwargs):
        trace = Path(command[command.index("-t") + 1])
        if kind == "malformed":
            trace.write_text("{bad-json", encoding="utf-8")
        elif kind == "empty":
            trace.write_text(json.dumps({"events": [], **identity()}),
                             encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(acceptance.subprocess, "run", simulator):
        result = acceptance.run_single_simulation_worker(worker_task(tmp_path))

    assert result["simulation_status"] == "error"
    assert result["error"] and not result["rejected"]
    assert math.isnan(result["acceptance_ratio"])


def test_worker_classifies_nonzero_exit_and_timeout(tmp_path):
    with mock.patch.object(
        acceptance.subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(3, ["rtsim"]),
    ):
        failed = acceptance.run_single_simulation_worker(worker_task(tmp_path))
    assert failed["simulation_status"] == "error"
    assert failed["reason"] == "simulator_nonzero_exit"

    with mock.patch.object(
        acceptance.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(["rtsim"], 120),
    ):
        timed_out = acceptance.run_single_simulation_worker(
            worker_task(tmp_path)
        )
    assert timed_out["simulation_status"] == "timeout"
    assert timed_out["timeout"] and not timed_out["rejected"]


def test_worker_classifies_invalid_task_model_as_error(tmp_path):
    error = subprocess.CalledProcessError(
        2, ["rtsim"],
        stderr=(
            "invalid_task_model: invalid_constrained_deadline_task "
            "task=bad C=2 D=11 T=10"
        ),
    )
    with mock.patch.object(
        acceptance.subprocess, "run", side_effect=error
    ):
        result = acceptance.run_single_simulation_worker(
            worker_task(tmp_path)
        )
    assert result["simulation_status"] == "error"
    assert result["reason"] == "invalid_task_model"
    assert not result["rejected"]


def test_worker_rejects_scheduler_identity_mismatch(tmp_path):
    def simulator(command, **kwargs):
        trace = Path(command[command.index("-t") + 1])
        wrong = identity()
        wrong["scheduler_implementation"] = "GPFPASAPSyncScheduler"
        write_trace(trace, 500, metadata=wrong)
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(acceptance.subprocess, "run", simulator):
        result = acceptance.run_single_simulation_worker(worker_task(tmp_path))

    assert result["simulation_status"] == "error"
    assert result["reason"] == "scheduler_identity_mismatch"
    assert math.isnan(result["acceptance_ratio"])


def make_runner(tmp_path):
    return acceptance.ExperimentRunner(
        tmp_path, [0.5], 4, 2, 10, 20, 500,
        1.0, 1.0, 0, use_real_solar_data=False,
        system_cores=1, max_workers=1,
    )


def aggregate(tmp_path, statuses):
    runner = make_runner(tmp_path)
    results = defaultdict(lambda: defaultdict(list))
    results[ALGORITHM][0.5] = [
        {
            "algorithm": ALGORITHM,
            "config_id": runner.config_id(0.5),
            "config_group_id": runner.config_group_id(0.5),
            "taskset_id": "taskset-{}".format(index),
            "taskset_hash": "taskset-hash-{}".format(index),
            "task_idx": index,
            "simulation_status": status,
            "acceptance_ratio": (
                1.0 if status == "accepted"
                else 0.0 if status == "rejected"
                else math.nan
            ),
        }
        for index, status in enumerate(statuses)
    ]
    return runner.aggregate_results(results).iloc[0]


@pytest.mark.parametrize(
    "statuses,conditional,unconditional,valid,errors,timeouts",
    [
        (["accepted", "rejected"], 0.5, 0.5, 2, 0, 0),
        (["accepted", "error"], 1.0, 0.5, 1, 1, 0),
        (["accepted", "timeout"], 1.0, 0.5, 1, 0, 1),
        (["accepted", "rejected", "error", "timeout"],
         0.5, 0.25, 2, 1, 1),
    ],
)
def test_conditional_and_unconditional_acceptance(
        tmp_path, statuses, conditional, unconditional,
        valid, errors, timeouts):
    row = aggregate(tmp_path, statuses)
    assert row.acceptance_ratio == conditional
    assert row.unconditional_success_rate == unconditional
    assert row.simulation_num_valid == valid
    assert row.simulation_num_error == errors
    assert row.simulation_num_timeout == timeouts
    assert row.num_requested_samples == len(statuses)


def test_no_valid_simulations_is_nan(tmp_path):
    row = aggregate(tmp_path, ["error", "timeout"])
    assert math.isnan(row.acceptance_ratio)
    assert row.no_valid_simulations
    assert row.simulation_num_valid == 0
    assert row.unconditional_success_rate == 0.0


def test_generation_failure_does_not_enter_valid_denominator(tmp_path):
    row = aggregate(tmp_path, ["accepted", "generation_error"])
    assert row.acceptance_ratio == 1.0
    assert row.unconditional_success_rate == 0.5
    assert row.simulation_num_valid == 1
    assert row.simulation_num_error == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected", math.nan),
        ("expected", math.inf),
        ("expected", -math.inf),
        ("expected", 0),
        ("expected", -1),
        ("expected", True),
        ("observed", math.nan),
        ("observed", math.inf),
        ("observed", -math.inf),
        ("observed", -1),
        ("observed", True),
        ("observed", "not-a-number"),
        ("observed", None),
    ],
)
def test_invalid_horizon_metadata_is_error(tmp_path, field, value):
    trace = tmp_path / "invalid_horizon.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    if field == "observed":
        payload["observed_simulation_end_ms"] = value
        expected = 500
    else:
        expected = value
    trace.write_text(json.dumps(payload), encoding="utf-8")

    result = acceptance.TraceParser(str(trace)).evaluate(expected)
    assert result.status == "error"
    assert result.reason == "invalid_horizon_metadata"


def test_incomplete_completion_outcome_is_error(tmp_path):
    trace = tmp_path / "incomplete.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload["events"][-1]["time"] = "100"
    payload["observed_simulation_end_ms"] = 100
    payload["simulation_completed"] = False
    payload["simulation_completion_reason"] = "event_queue_exhausted"
    trace.write_text(json.dumps(payload), encoding="utf-8")

    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "invalid_completion_metadata"


def test_contradictory_metadata_precedes_deadline_miss_classification(tmp_path):
    trace = tmp_path / "early_rejected.json"
    write_trace(trace, 500, deadline_miss=True)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload["events"] = payload["events"][:2]
    payload["observed_simulation_end_ms"] = 1
    payload["simulation_completed"] = False
    payload["simulation_completion_reason"] = "deadline_miss_terminated"
    trace.write_text(json.dumps(payload), encoding="utf-8")

    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "invalid_completion_metadata"


@pytest.mark.parametrize("deadline_miss", [False, True])
@pytest.mark.parametrize(
    "mutation",
    [
        {"simulation_completed": True,
         "simulation_completion_reason": "event_queue_exhausted"},
        {"simulation_completed": False,
         "simulation_completion_reason": "reached_horizon"},
        {"observed_simulation_end_ms": 501},
        {"observed_simulation_end_ms": 499},
        {"simulation_completion_reason": ""},
        {"simulation_completed": "true"},
    ],
)
def test_completion_truth_table_rejects_contradictions_before_outcome(
        tmp_path, deadline_miss, mutation):
    trace = tmp_path / "contradictory.json"
    write_trace(trace, 500, deadline_miss=deadline_miss)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload.update(mutation)
    trace.write_text(json.dumps(payload), encoding="utf-8")

    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "invalid_completion_metadata"


def test_valid_and_malformed_deadline_miss_payloads(tmp_path):
    valid = tmp_path / "valid_miss.json"
    write_trace(valid, 500, deadline_miss=True)
    result = acceptance.TraceParser(str(valid)).evaluate(500)
    assert result.status == "rejected"
    assert result.reason == "deadline_miss"

    malformed = tmp_path / "malformed_miss.json"
    write_trace(malformed, 500, deadline_miss=True)
    payload = json.loads(malformed.read_text(encoding="utf-8"))
    miss = next(
        event for event in payload["events"]
        if event.get("event_type") == "dline_miss"
    )
    miss["remaining_execution_ms"] = 0
    malformed.write_text(json.dumps(payload), encoding="utf-8")
    result = acceptance.TraceParser(str(malformed)).evaluate(500)
    assert result.status == "error"
    assert result.reason == "malformed_deadline_miss"


def test_ordinary_kill_is_not_a_deadline_miss(tmp_path):
    trace = tmp_path / "ordinary_kill.json"
    write_trace(trace, 500)
    payload = json.loads(trace.read_text(encoding="utf-8"))
    payload["events"].insert(1, {
        "run_generation": 1, "time": "10", "event_type": "kill",
        "task_name": "task_0",
        "reason": "ordinary_cancellation",
    })
    trace.write_text(json.dumps(payload), encoding="utf-8")

    result = acceptance.TraceParser(str(trace)).evaluate(500)
    assert result.status == "accepted"


def test_legacy_trace_requires_explicit_opt_in(tmp_path):
    trace = tmp_path / "legacy.json"
    trace.write_text(json.dumps({
        "events": [
            {"time": "0", "event_type": "arrival", "task_name": "task_0"},
            {"time": "500", "event_type": "idle"},
        ],
        **identity(),
    }), encoding="utf-8")

    strict = acceptance.TraceParser(str(trace)).evaluate(500)
    assert strict.status == "error"
    assert strict.reason == "unsupported_trace_schema"
    with pytest.warns(RuntimeWarning, match="legacy trace schema"):
        legacy = acceptance.TraceParser(
            str(trace), allow_legacy=True
        ).evaluate(500)
    assert legacy.status == "accepted"
