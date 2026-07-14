from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
import json
import os
from pathlib import Path
import signal
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
    worker_pid: int = 0


def _ok_payload():
    return (
        "ok",
        _FakeResult(worker_pid=os.getpid()),
        0.01,
        0.005,
        1234,
    )


def _send_payload_and_exit(
    sending,
    started_event,
    unused_request,
):
    del unused_request
    started_event.set()
    sending.send(_ok_payload())
    sending.close()


def _send_payload_then_linger(
    sending,
    started_event,
    linger_seconds,
):
    started_event.set()
    sending.send(_ok_payload())
    sending.close()
    time.sleep(float(linger_seconds))


def _send_payload_then_ignore_sigterm(
    sending,
    started_event,
    unused_request,
):
    del unused_request
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    started_event.set()
    sending.send(_ok_payload())
    sending.close()

    while True:
        time.sleep(60.0)


def _send_malformed_payload(
    sending,
    started_event,
    unused_request,
):
    del unused_request
    started_event.set()
    sending.send(("unknown-payload-kind",))
    sending.close()


def _send_error_payload(
    sending,
    started_event,
    unused_request,
):
    del unused_request
    started_event.set()
    sending.send((
        "error",
        "RuntimeError",
        "synthetic worker failure",
        "synthetic traceback",
        0.01,
        0.005,
        1234,
    ))
    sending.close()


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
        kill_confirmation_seconds=0.5,
    )


def _run_stress_worker(case_index):
    mode = int(case_index) % 3

    if mode == 0:
        target = _send_payload_and_exit
        request = None
    elif mode == 1:
        target = _send_payload_then_linger
        request = 60.0
    else:
        target = _send_payload_then_ignore_sigterm
        request = None

    return execute_isolated(
        request,
        1.0,
        start_method="spawn",
        worker_target=target,
        post_payload_join_seconds=0.5,
        terminate_join_seconds=0.05,
        kill_join_seconds=0.5,
        kill_confirmation_seconds=0.5,
    )


def _pid_state(pid):
    stat_path = Path("/proc") / str(pid) / "stat"

    try:
        fields = stat_path.read_text(
            encoding="utf-8"
        ).split()
    except FileNotFoundError:
        return None

    return fields[2] if len(fields) >= 3 else "UNKNOWN"


def test_normal_payload_and_exit_is_confirmed():
    execution = execute_isolated(
        None,
        1.0,
        worker_target=_send_payload_and_exit,
        post_payload_join_seconds=1.0,
        terminate_join_seconds=0.1,
        kill_join_seconds=0.1,
        kill_confirmation_seconds=0.1,
    )

    assert execution.result is not None
    worker_pid = execution.result.worker_pid

    assert execution.solver_status == "COMPLETED"
    assert execution.payload_received is True
    assert execution.worker_cleanup_status == "EXITED_NORMALLY"
    assert execution.worker_exitcode == 0
    assert _pid_state(worker_pid) is None


def test_complete_payload_survives_sigkill_cleanup():
    execution = execute_isolated(
        None,
        1.0,
        worker_target=_send_payload_then_ignore_sigterm,
        post_payload_join_seconds=0.02,
        terminate_join_seconds=0.05,
        kill_join_seconds=0.5,
        kill_confirmation_seconds=0.5,
    )

    assert execution.result is not None
    worker_pid = execution.result.worker_pid

    assert execution.solver_status == "COMPLETED"
    assert execution.outer_timeout is False
    assert execution.payload_received is True
    assert execution.worker_cleanup_status == "REAPED_AFTER_KILL"
    assert execution.worker_exitcode == -signal.SIGKILL
    assert execution.exception_type is None
    assert _pid_state(worker_pid) is None


def test_complete_payload_survives_lingering_worker():
    execution = _run_lingering_worker()

    assert execution.result is not None
    assert (
        execution.result.solver_status
        is _FakeSolverStatus.COMPLETED
    )
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


def test_malformed_payload_fails_closed_after_cleanup():
    execution = execute_isolated(
        None,
        1.0,
        worker_target=_send_malformed_payload,
        post_payload_join_seconds=1.0,
        terminate_join_seconds=0.1,
        kill_join_seconds=0.1,
        kill_confirmation_seconds=0.1,
    )

    assert execution.result is None
    assert execution.solver_status == "INTERNAL_CONFORMANCE_FAILURE"
    assert execution.payload_received is True
    assert execution.worker_cleanup_status == "EXITED_NORMALLY"
    assert execution.worker_exitcode == 0
    assert execution.exception_type == "InvalidWorkerPayload"


def test_worker_error_payload_fails_closed_after_cleanup():
    execution = execute_isolated(
        None,
        1.0,
        worker_target=_send_error_payload,
        post_payload_join_seconds=1.0,
        terminate_join_seconds=0.1,
        kill_join_seconds=0.1,
        kill_confirmation_seconds=0.1,
    )

    assert execution.result is None
    assert execution.solver_status == "INTERNAL_CONFORMANCE_FAILURE"
    assert execution.payload_received is True
    assert execution.worker_cleanup_status == "EXITED_NORMALLY"
    assert execution.worker_exitcode == 0
    assert execution.exception_type == "RuntimeError"
    assert execution.exception_message == "synthetic worker failure"


class _NeverConfirmingProcess:
    def __init__(self, sentinel):
        self.sentinel = sentinel
        self.exitcode = None
        self.terminated = False
        self.killed = False
        self.closed = False

    def join(self, timeout=None):
        del timeout

    def is_alive(self):
        return True

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def close(self):
        self.closed = True


def test_unconfirmed_exit_remains_unreaped_after_kill():
    read_fd, write_fd = os.pipe()
    process = _NeverConfirmingProcess(read_fd)

    try:
        cleanup = engine_module._reap_worker(
            process,
            initial_join_seconds=0.0,
            terminate_join_seconds=0.0,
            kill_join_seconds=0.0,
            kill_confirmation_seconds=0.02,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert cleanup.status == "UNREAPED_AFTER_KILL"
    assert cleanup.exitcode is None
    assert cleanup.seconds < 0.5
    assert process.terminated is True
    assert process.killed is True
    assert process.closed is False


def test_kill_confirmation_wait_refreshes_exitcode(
    monkeypatch,
):
    process = _NeverConfirmingProcess(object())
    observed = {}

    def confirm_during_wait(objects, timeout):
        observed["objects"] = objects
        observed["timeout"] = timeout
        process.exitcode = -signal.SIGKILL
        return objects

    monkeypatch.setattr(
        engine_module,
        "wait_for_multiprocessing_objects",
        confirm_during_wait,
    )

    cleanup = engine_module._reap_worker(
        process,
        initial_join_seconds=0.0,
        terminate_join_seconds=0.0,
        kill_join_seconds=0.0,
        kill_confirmation_seconds=0.1,
    )

    assert cleanup.status == "REAPED_AFTER_KILL"
    assert cleanup.exitcode == -signal.SIGKILL
    assert observed["objects"] == [process.sentinel]
    assert 0.0 <= observed["timeout"] <= 0.1
    assert process.terminated is True
    assert process.killed is True
    assert process.closed is True


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
        assert execution.result is not None
        assert (
            execution.result.solver_status
            is _FakeSolverStatus.COMPLETED
        )
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


def test_unreaped_payload_still_triggers_p0(
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
                "UNREAPED_AFTER_KILL"
            ),
            worker_exitcode=None,
            worker_cleanup_seconds=0.25,
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
    assert attempts[0]["solver_status"] == "COMPLETED"
    assert terminals[0]["solver_status"] == "COMPLETED"
    assert failures[0]["severity"] == "P0"
    assert failures[0]["code"] == "WorkerUnreapedAfterPayload"


def test_twelve_worker_bounded_cleanup_stress_report(
    tmp_path,
):
    total_cases = 204
    worker_count = 12
    started = time.perf_counter()

    with ThreadPoolExecutor(
        max_workers=worker_count
    ) as executor:
        executions = list(
            executor.map(
                _run_stress_worker,
                range(total_cases),
            )
        )

    duration = time.perf_counter() - started
    cleanup_counts = Counter(
        item.worker_cleanup_status
        for item in executions
    )
    worker_pids = [
        item.result.worker_pid
        for item in executions
        if item.result is not None
    ]
    states = {
        pid: _pid_state(pid)
        for pid in worker_pids
    }
    orphan_count = sum(
        state is not None
        for state in states.values()
    )
    zombie_count = sum(
        state == "Z"
        for state in states.values()
    )
    worker_did_not_exit = sum(
        item.exception_type == "WorkerDidNotExit"
        for item in executions
    )
    payload_received_count = sum(
        item.payload_received
        for item in executions
    )

    status = (
        "PASS"
        if (
            payload_received_count == total_cases
            and cleanup_counts["UNREAPED_AFTER_KILL"] == 0
            and worker_did_not_exit == 0
            and orphan_count == 0
            and zombie_count == 0
        )
        else "FAIL"
    )
    report = {
        "total_cases": total_cases,
        "worker_count": worker_count,
        "payload_received_count": payload_received_count,
        "EXITED_NORMALLY": cleanup_counts["EXITED_NORMALLY"],
        "REAPED_AFTER_TERMINATE": cleanup_counts[
            "REAPED_AFTER_TERMINATE"
        ],
        "REAPED_AFTER_KILL": cleanup_counts["REAPED_AFTER_KILL"],
        "UNREAPED_AFTER_KILL": cleanup_counts[
            "UNREAPED_AFTER_KILL"
        ],
        "WorkerDidNotExit": worker_did_not_exit,
        "orphan_count": orphan_count,
        "zombie_count": zombie_count,
        "test_duration_seconds": round(duration, 9),
        "status": status,
    }
    report_path = Path(
        os.environ.get(
            "V93_WORKER_STRESS_REPORT",
            str(
                tmp_path
                / "v9_3_worker_reap_confirmation_stress_v2.json"
            ),
        )
    )
    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    report_path.write_text(
        json.dumps(
            report,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    assert len(executions) == total_cases
    assert payload_received_count == total_cases
    assert sum(cleanup_counts.values()) == total_cases
    assert set(cleanup_counts) <= {
        "EXITED_NORMALLY",
        "REAPED_AFTER_TERMINATE",
        "REAPED_AFTER_KILL",
    }
    assert cleanup_counts["EXITED_NORMALLY"] > 0
    assert cleanup_counts["REAPED_AFTER_TERMINATE"] > 0
    assert cleanup_counts["REAPED_AFTER_KILL"] > 0
    assert cleanup_counts["UNREAPED_AFTER_KILL"] == 0
    assert worker_did_not_exit == 0
    assert orphan_count == 0
    assert zombie_count == 0
    assert status == "PASS"


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
