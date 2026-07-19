from __future__ import annotations

import json

import pytest

from experiments.v9_3.resource_measurement import (
    RESOURCE_OBSERVATION_COLUMNS,
    ResourceContractError,
    materialize_resource_usage,
)
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, REQUEST_COLUMNS, TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS, write_csv,
)


def _write_fixture(
    tmp_path, *, payload_received, terminal_status, observation,
    outer_timeout=None,
):
    outer_timeout = (
        terminal_status == "TIMEOUT"
        if outer_timeout is None else outer_timeout
    )
    write_csv(tmp_path / "scalability_cells.csv", (
        "scalability_cell_id", "scaling_axis", "level_id", "worker_count",
        "analysis_ids_json",
    ), [{
        "scalability_cell_id": "c", "scaling_axis": "task_count",
        "level_id": "6", "worker_count": 1,
        "analysis_ids_json": json.dumps(["a"]),
    }])
    write_csv(tmp_path / "analysis_requests.csv", REQUEST_COLUMNS, [{
        "request_id": "r", "analysis_id": "a", "cell_id": "inner",
        "taskset_id": "t", "taskset_hash": "hash", "exact_e0": "0",
        "variant": "CW_THETA_CW", "numerical_mode": "EXACT_RATIONAL",
        "timeout_seconds": 2, "retry_timeout_seconds": "",
        "source_analysis_id": "", "request_status": "TERMINAL",
    }])
    write_csv(tmp_path / "analysis_attempts.csv", ATTEMPT_COLUMNS, [{
        "attempt_id": "x", "analysis_id": "a", "attempt_number": 1,
        "solver_wall_seconds": 1, "solver_cpu_seconds": .5,
        "total_wall_seconds": 1.2, "worker_startup_seconds": .1,
        "serialization_seconds": .01, "ipc_seconds": .09,
        "timeout_budget_seconds": 2, "solver_status": terminal_status,
        "outer_timeout": outer_timeout,
        "payload_received": payload_received,
    }])
    write_csv(
        tmp_path / "attempt_resource_observations.csv",
        RESOURCE_OBSERVATION_COLUMNS, [observation],
    )
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, [{
        "analysis_id": "a", "request_id": "r", "taskset_id": "t",
        "taskset_hash": "hash", "exact_e0": "0",
        "analysis_variant": "CW_THETA_CW", "final_attempt_id": "x",
        "n_tasks_candidate_found": 1, "first_failed_priority": "",
        "solver_status": terminal_status,
        "outer_timeout": outer_timeout,
        "taskset_proven": terminal_status == "COMPLETED",
    }])
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, [{
        "analysis_id": "a", "taskset_id": "t", "task_id": "0",
        "checked_w_count": 2, "checked_h_count": 3,
        "checked_q_count": 4, "envelope_call_count": 5,
    }])


def test_no_payload_timeout_has_explicit_expected_unavailable_rss(tmp_path):
    _write_fixture(tmp_path, payload_received=False, terminal_status="TIMEOUT", observation={
        "attempt_id": "x", "analysis_id": "a",
        "peak_rss_kib": "UNAVAILABLE", "peak_rss_scope": "UNAVAILABLE",
        "peak_rss_unit": "UNAVAILABLE",
        "observation_status": "EXPECTED_UNAVAILABLE",
        "unavailability_reason": "NO_PAYLOAD_TIMEOUT",
    })
    rows = materialize_resource_usage(tmp_path)
    assert rows[0]["peak_rss"] == "UNAVAILABLE"
    assert rows[0]["resource_observation_status"] == "EXPECTED_UNAVAILABLE"
    assert rows[0]["deserialization_seconds"] == "UNAVAILABLE"
    assert rows[0]["fixed_point_iterations"] == "UNAVAILABLE"
    assert rows[0]["checked_w_count"] == 2


def test_payload_received_without_rss_is_contract_failure(tmp_path):
    _write_fixture(tmp_path, payload_received=True, terminal_status="COMPLETED", observation={
        "attempt_id": "x", "analysis_id": "a",
        "peak_rss_kib": "UNAVAILABLE", "peak_rss_scope": "UNAVAILABLE",
        "peak_rss_unit": "UNAVAILABLE",
        "observation_status": "TECHNICAL_UNAVAILABLE",
        "unavailability_reason": "MISSING_SAMPLE",
    })
    with pytest.raises(ResourceContractError, match="payload-bearing"):
        materialize_resource_usage(tmp_path)


def test_outer_timeout_flag_does_not_disguise_technical_resource_status(
    tmp_path,
):
    _write_fixture(
        tmp_path, payload_received=False,
        terminal_status="INTERNAL_CONFORMANCE_FAILURE",
        outer_timeout=True,
        observation={
            "attempt_id": "x", "analysis_id": "a",
            "peak_rss_kib": "UNAVAILABLE",
            "peak_rss_scope": "UNAVAILABLE",
            "peak_rss_unit": "UNAVAILABLE",
            "observation_status": "TECHNICAL_UNAVAILABLE",
            "unavailability_reason": "NO_PAYLOAD_TECHNICAL_FAILURE",
        },
    )
    row = materialize_resource_usage(tmp_path)[0]
    assert row["resource_observation_status"] == "TECHNICAL_UNAVAILABLE"
    assert row["first_failed_priority"] == "UNAVAILABLE"


@pytest.mark.parametrize("table", ["request", "attempt", "result", "observation"])
def test_duplicate_join_identity_is_rejected(tmp_path, table):
    observation = {
        "attempt_id": "x", "analysis_id": "a", "peak_rss_kib": 100,
        "peak_rss_scope": "CHILD_PROCESS", "peak_rss_unit": "KiB",
        "observation_status": "AVAILABLE", "unavailability_reason": "",
    }
    _write_fixture(
        tmp_path, payload_received=True, terminal_status="COMPLETED",
        observation=observation,
    )
    paths = {
        "request": ("analysis_requests.csv", REQUEST_COLUMNS),
        "attempt": ("analysis_attempts.csv", ATTEMPT_COLUMNS),
        "result": ("per_taskset_results.csv", TASKSET_RESULT_COLUMNS),
        "observation": (
            "attempt_resource_observations.csv", RESOURCE_OBSERVATION_COLUMNS
        ),
    }
    name, columns = paths[table]
    from experiments.v9_3.result_writer import read_csv

    rows = read_csv(tmp_path / name)
    write_csv(tmp_path / name, columns, [rows[0], rows[0]])
    with pytest.raises(ResourceContractError, match="duplicate"):
        materialize_resource_usage(tmp_path)


def test_all_unavailable_counters_stay_unavailable_not_zero(tmp_path):
    observation = {
        "attempt_id": "x", "analysis_id": "a", "peak_rss_kib": 100,
        "peak_rss_scope": "CHILD_PROCESS", "peak_rss_unit": "KiB",
        "observation_status": "AVAILABLE", "unavailability_reason": "",
    }
    _write_fixture(
        tmp_path, payload_received=True, terminal_status="COMPLETED",
        observation=observation,
    )
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, [{
        "analysis_id": "a", "taskset_id": "t", "task_id": "0",
        "checked_w_count": "UNAVAILABLE", "checked_h_count": "UNAVAILABLE",
        "checked_q_count": "UNAVAILABLE", "envelope_call_count": "UNAVAILABLE",
    }])
    row = materialize_resource_usage(tmp_path)[0]
    assert row["search_counter_observation_status"] == "UNAVAILABLE"
    assert row["checked_w_count"] == "UNAVAILABLE"


def test_partial_counter_observation_is_labeled_and_sums_only_available(tmp_path):
    observation = {
        "attempt_id": "x", "analysis_id": "a", "peak_rss_kib": 100,
        "peak_rss_scope": "CHILD_PROCESS", "peak_rss_unit": "KiB",
        "observation_status": "AVAILABLE", "unavailability_reason": "",
    }
    _write_fixture(
        tmp_path, payload_received=True, terminal_status="COMPLETED",
        observation=observation,
    )
    write_csv(tmp_path / "per_task_results.csv", TASK_RESULT_COLUMNS, [
        {
            "analysis_id": "a", "taskset_id": "t", "task_id": "0",
            "checked_w_count": 2, "checked_h_count": 3,
            "checked_q_count": 4, "envelope_call_count": 5,
        },
        {
            "analysis_id": "a", "taskset_id": "t", "task_id": "1",
            "checked_w_count": "UNAVAILABLE", "checked_h_count": "UNAVAILABLE",
            "checked_q_count": "UNAVAILABLE", "envelope_call_count": "UNAVAILABLE",
        },
    ])
    row = materialize_resource_usage(tmp_path)[0]
    assert row["search_counter_observation_status"] == "PARTIAL"
    assert row["search_counter_available_task_count"] == 1
    assert row["search_counter_total_task_count"] == 2
    assert row["checked_w_count"] == 2


def test_retry_attempt_does_not_duplicate_scientific_search_counters(tmp_path):
    observation = {
        "attempt_id": "x", "analysis_id": "a", "peak_rss_kib": 100,
        "peak_rss_scope": "CHILD_PROCESS", "peak_rss_unit": "KiB",
        "observation_status": "AVAILABLE", "unavailability_reason": "",
    }
    _write_fixture(
        tmp_path, payload_received=True, terminal_status="COMPLETED",
        observation=observation,
    )
    from experiments.v9_3.result_writer import read_csv

    attempts = read_csv(tmp_path / "analysis_attempts.csv")
    retry = dict(attempts[0])
    retry.update({"attempt_id": "x2", "attempt_number": "2", "parent_attempt_id": "x"})
    write_csv(tmp_path / "analysis_attempts.csv", ATTEMPT_COLUMNS, [attempts[0], retry])
    observations = read_csv(tmp_path / "attempt_resource_observations.csv")
    retry_observation = dict(observations[0])
    retry_observation["attempt_id"] = "x2"
    write_csv(
        tmp_path / "attempt_resource_observations.csv",
        RESOURCE_OBSERVATION_COLUMNS, [observations[0], retry_observation],
    )
    results = read_csv(tmp_path / "per_taskset_results.csv")
    results[0]["final_attempt_id"] = "x2"
    write_csv(tmp_path / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
    rows = materialize_resource_usage(tmp_path)
    assert rows[0]["search_counter_observation_status"] == "NOT_APPLICABLE_RETRY_ATTEMPT"
    assert rows[0]["checked_w_count"] == "UNAVAILABLE"
    assert rows[1]["checked_w_count"] == 2
    assert rows[1]["first_failed_priority"] == "NOT_APPLICABLE"
