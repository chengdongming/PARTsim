from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
import time

import pytest

import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.execution_engine import execute_isolated
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS,
    ResultWriter,
    ResultWriterError,
    read_csv,
)
from v9_3_experiment_helpers import (
    install_fake_materialization,
    make_config,
    successful_execution,
)


class _FakeSolverStatus(Enum):
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class _FakeResult:
    solver_status: _FakeSolverStatus = (
        _FakeSolverStatus.COMPLETED
    )


def _send_payload_then_linger(
    sending,
    started_event,
    linger_seconds,
):
    started_event.set()
    sending.send((
        "ok",
        _FakeResult(),
        0.01,
        0.005,
        1234,
    ))
    sending.close()
    time.sleep(float(linger_seconds))


def _start_and_never_send(
    sending,
    started_event,
    linger_seconds,
):
    started_event.set()
    time.sleep(float(linger_seconds))


def _run_lingering_worker():
    return execute_isolated(
        30.0,
        10.0,
        worker_target=_send_payload_then_linger,
        post_payload_join_seconds=0.02,
        terminate_join_seconds=2.0,
        kill_join_seconds=2.0,
    )


def test_complete_payload_survives_lingering_worker():
    execution = _run_lingering_worker()

    assert execution.result == _FakeResult()
    assert execution.solver_status == "COMPLETED"
    assert execution.outer_timeout is False
    assert execution.payload_received is True
    assert execution.worker_cleanup_status in {
        "REAPED_AFTER_TERMINATE",
        "REAPED_AFTER_KILL",
    }
    assert execution.worker_exitcode is not None
    assert execution.exception_type is None


def test_pre_payload_timeout_remains_timeout():
    execution = execute_isolated(
        30.0,
        0.1,
        worker_target=_start_and_never_send,
        post_payload_join_seconds=0.02,
        terminate_join_seconds=2.0,
        kill_join_seconds=2.0,
    )

    assert execution.result is None
    assert execution.solver_status == "TIMEOUT"
    assert execution.outer_timeout is True
    assert execution.payload_received is False
    assert execution.worker_cleanup_status in {
        "REAPED_AFTER_TERMINATE",
        "REAPED_AFTER_KILL",
    }


def test_twelve_concurrent_lingering_workers_preserve_payloads():
    with ThreadPoolExecutor(max_workers=12) as executor:
        executions = list(
            executor.map(
                lambda _: _run_lingering_worker(),
                range(12),
            )
        )

    assert len(executions) == 12

    for execution in executions:
        assert execution.result == _FakeResult()
        assert execution.solver_status == "COMPLETED"
        assert execution.payload_received is True
        assert execution.worker_cleanup_status in {
            "REAPED_AFTER_TERMINATE",
            "REAPED_AFTER_KILL",
        }
        assert (
            execution.worker_cleanup_status
            != "UNREAPED_AFTER_KILL"
        )
        assert execution.exception_type is None


def test_attempt_journal_records_cleanup_fields(
    tmp_path,
    monkeypatch,
):
    install_fake_materialization(
        monkeypatch,
        tmp_path,
    )

    def execute(request, timeout):
        del timeout

        return replace(
            successful_execution(request),
            payload_received=True,
            worker_cleanup_status=(
                "REAPED_AFTER_TERMINATE"
            ),
            worker_exitcode=-15,
            worker_cleanup_seconds=0.25,
            total_wall_seconds=0.262,
        )

    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        execute,
    )

    outcome = engine_module.ExecutionEngine(
        make_config(tmp_path)
    ).run()

    attempts = read_csv(
        outcome.output_root
        / "analysis_attempts.csv"
    )

    assert attempts

    for column in (
        "payload_received",
        "worker_cleanup_status",
        "worker_exitcode",
        "worker_cleanup_seconds",
    ):
        assert column in ATTEMPT_COLUMNS

    for row in attempts:
        assert row["payload_received"] == "True"
        assert (
            row["worker_cleanup_status"]
            == "REAPED_AFTER_TERMINATE"
        )
        assert row["worker_exitcode"] == "-15"
        assert (
            row["worker_cleanup_seconds"]
            == "0.250000000"
        )

def test_validation_failure_terminal_uses_rewritten_execution(
    tmp_path,
    monkeypatch,
):
    install_fake_materialization(
        monkeypatch,
        tmp_path,
    )

    def execute(request, timeout):
        del timeout

        execution = successful_execution(request)

        assert execution.result is not None

        invalid_result = replace(
            execution.result,
            analysis_id=(
                "invalid-"
                + request.analysis_id
            ),
        )

        return replace(
            execution,
            result=invalid_result,
            payload_received=True,
            worker_cleanup_status=(
                "EXITED_NORMALLY"
            ),
            worker_exitcode=0,
        )

    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        execute,
    )

    outcome = engine_module.ExecutionEngine(
        make_config(tmp_path)
    ).run()

    attempts = read_csv(
        outcome.output_root
        / "analysis_attempts.csv"
    )

    terminals = read_csv(
        outcome.output_root
        / "per_taskset_results.csv"
    )

    failures = read_csv(
        outcome.output_root
        / "failures.csv"
    )

    assert outcome.stopped is True
    assert len(attempts) == 1
    assert len(terminals) == 1
    assert len(failures) == 1

    assert (
        attempts[0]["solver_status"]
        == "INTERNAL_CONFORMANCE_FAILURE"
    )

    assert (
        terminals[0]["solver_status"]
        == "INTERNAL_CONFORMANCE_FAILURE"
    )

    assert (
        terminals[0]["certification_status"]
        == "NOT_CERTIFIED"
    )

    assert (
        terminals[0]["taskset_proven"]
        == "False"
    )

    assert (
        terminals[0]["terminal_origin"]
        == "OUTER_WORKER"
    )

    assert (
        terminals[0]["final_attempt_id"]
        == attempts[0]["attempt_id"]
    )

    assert failures[0]["severity"] == "P0"
    assert failures[0]["stage"] == "ANALYSIS"

    state_files = list(
        (
            outcome.output_root
            / "result_state"
        ).glob("*.pickle")
    )

    assert state_files == []

def test_pilot_delegates_to_common_worker_protocol(
    monkeypatch,
):
    import scripts.run_v9_3_pilot as pilot_module

    sentinel_request = object()
    sentinel_result = object()
    observed = {}

    shared_execution = engine_module.AttemptExecution(
        result=sentinel_result,
        solver_status="COMPLETED",
        outer_timeout=False,
        solver_wall_seconds=0.1,
        solver_cpu_seconds=0.05,
        worker_startup_seconds=0.01,
        ipc_seconds=0.01,
        total_wall_seconds=0.12,
        payload_received=True,
        worker_cleanup_status=(
            "REAPED_AFTER_TERMINATE"
        ),
        worker_exitcode=-15,
        worker_cleanup_seconds=0.02,
    )

    def fake_execute(
        request,
        timeout,
        *,
        start_method,
    ):
        observed["request"] = request
        observed["timeout"] = timeout
        observed["start_method"] = start_method
        return shared_execution

    monkeypatch.setattr(
        pilot_module,
        "execute_formal_isolated",
        fake_execute,
    )

    execution = pilot_module.execute_analysis(
        sentinel_request,
        37.0,
    )

    assert observed == {
        "request": sentinel_request,
        "timeout": 37.0,
        "start_method": "fork",
    }

    assert execution.result is sentinel_result
    assert execution.wall_seconds == 0.12
    assert execution.cpu_seconds == 0.05
    assert execution.outer_timeout is False
    assert execution.payload_received is True

    assert (
        execution.worker_cleanup_status
        == "REAPED_AFTER_TERMINATE"
    )

    assert execution.worker_exitcode == -15
    assert execution.worker_cleanup_seconds == 0.02
    assert execution.exception_type is None

def test_existing_legacy_attempt_header_fails_closed(
    tmp_path,
):
    root = tmp_path / "result-writer"

    # A current-schema result root can be reopened.
    ResultWriter(root)
    ResultWriter(root)

    attempts_path = (
        root
        / "analysis_attempts.csv"
    )

    legacy_header = [
        column
        for column in ATTEMPT_COLUMNS
        if column not in {
            "payload_received",
            "worker_cleanup_status",
            "worker_exitcode",
            "worker_cleanup_seconds",
        }
    ]

    attempts_path.write_text(
        ",".join(legacy_header) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ResultWriterError,
        match=(
            "existing table header mismatch "
            "for analysis_attempts.csv"
        ),
    ):
        ResultWriter(root)
