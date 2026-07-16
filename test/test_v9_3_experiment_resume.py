import json
import pickle
from dataclasses import replace

import pytest

import asap_block_rta_v9_3_taskset as taskset
import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.execution_engine import ExecutionEngine, ExecutionError
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, REQUEST_COLUMNS, TASKSET_RESULT_COLUMNS,
    ResultWriter, ResultWriterError,
    read_csv, write_csv,
)
from v9_3_experiment_helpers import (
    candidate_solver, install_fake_materialization, make_config,
    successful_execution, timeout_execution,
)


def test_resume_skips_atomic_terminal_results(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(engine_module, "execute_isolated", lambda request, timeout: calls.append(request.analysis_id) or successful_execution(request))
    config = make_config(tmp_path)
    first = ExecutionEngine(config).run()
    assert first.requested == first.terminal == 2
    assert len(calls) == 2
    attempts_before = read_csv(first.output_root / "analysis_attempts.csv")

    calls.clear()
    second = ExecutionEngine(config).run(resume=True)
    assert second.terminal == 2
    assert calls == []
    assert read_csv(first.output_root / "analysis_attempts.csv") == attempts_before


def test_resume_rejects_terminal_missing_failure_origin(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal_path = next(
        (outcome.output_root / "terminal_results").glob("*.json")
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["taskset_row"].pop("failure_origin", None)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    with pytest.raises(ExecutionError, match="terminal result schema mismatch"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_terminal_final_attempt_failure_origin_mismatch(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal_path = next(
        (outcome.output_root / "terminal_results").glob("*.json")
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["taskset_row"]["failure_origin"] = "OUTER_TIMEOUT_CONFIGURATION"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    with pytest.raises(ExecutionError, match="failure_origin"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_unknown_terminal_failure_origin(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal_path = next(
        (outcome.output_root / "terminal_results").glob("*.json")
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["taskset_row"]["failure_origin"] = "FUTURE_UNKNOWN_ORIGIN"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    with pytest.raises(ExecutionError, match="failure_origin.*not frozen"):
        ExecutionEngine(config).run(resume=True)


def test_config_hash_mismatch_refuses_resume(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(engine_module, "execute_isolated", lambda request, timeout: successful_execution(request))
    config = make_config(tmp_path)
    ExecutionEngine(config).run()
    changed = make_config(tmp_path)
    changed["grid"]["base_seed"] += 1
    with pytest.raises(ExecutionError, match="configuration hash mismatch"):
        ExecutionEngine(changed).run(resume=True)


def test_resume_rebuilds_terminal_after_attempt_before_terminal_crash(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: calls.append(request.analysis_id)
        or successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal = next((outcome.output_root / "terminal_results").glob("*.json"))
    requests = read_csv(outcome.output_root / "analysis_requests.csv")
    for row in requests:
        if row["analysis_id"] == terminal.stem:
            row["request_status"] = "PLANNED"
    write_csv(
        outcome.output_root / "analysis_requests.csv", REQUEST_COLUMNS, requests
    )
    terminal.unlink()
    calls.clear()
    resumed = ExecutionEngine(config).run(resume=True)
    assert resumed.requested == resumed.terminal == 2
    assert calls == []


def test_resume_detects_successful_attempt_with_missing_state(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal = next((outcome.output_root / "terminal_results").glob("*.json"))
    analysis_id = terminal.stem
    requests = read_csv(outcome.output_root / "analysis_requests.csv")
    for row in requests:
        if row["analysis_id"] == analysis_id:
            row["request_status"] = "PLANNED"
    write_csv(
        outcome.output_root / "analysis_requests.csv", REQUEST_COLUMNS, requests
    )
    terminal.unlink()
    (outcome.output_root / "result_state" / f"{analysis_id}.pickle").unlink()
    with pytest.raises(ExecutionError, match="missing its analyzer state"):
        ExecutionEngine(config).run(resume=True)


def test_duplicate_attempt_and_conflicting_terminal_fail_closed(tmp_path):
    writer = ResultWriter(tmp_path)
    attempt = {column: "" for column in ATTEMPT_COLUMNS}
    attempt.update({"attempt_id": "attempt-1", "analysis_id": "analysis-1"})
    writer.append_attempt(attempt)
    with pytest.raises(ResultWriterError, match="duplicate attempt_id"):
        writer.append_attempt(attempt)
    writer.write_terminal("analysis-1", {"value": 1})
    with pytest.raises(ResultWriterError, match="terminal result conflict"):
        writer.write_terminal("analysis-1", {"value": 2})


def test_unplanned_terminal_is_rejected_on_resume(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    payload = json.loads(next(
        (outcome.output_root / "terminal_results").glob("*.json")
    ).read_text(encoding="utf-8"))
    payload["taskset_row"]["analysis_id"] = "unplanned"
    (outcome.output_root / "terminal_results" / "unplanned.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(ExecutionError, match="do not belong to the active plan"):
        ExecutionEngine(config).run(resume=True)


def _terminal_for_variant(root, variant):
    for path in (root / "terminal_results").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["taskset_row"]["analysis_variant"] == variant:
            return path, payload
    raise AssertionError(f"missing terminal for {variant}")


def _rewrite_as_outer_terminal(
    root,
    variant,
    status,
    *,
    outer_timeout,
    payload_received,
    terminal_origin="OUTER_WORKER",
    exception_type="SyntheticOuterFailure",
):
    terminal_path, terminal = _terminal_for_variant(root, variant)
    state_path = root / "result_state" / f"{terminal_path.stem}.pickle"
    if state_path.exists():
        state_path.unlink()
    attempts_path = root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    attempt = next(
        row for row in attempts if row["analysis_id"] == terminal_path.stem
    )
    attempt.update({
        "solver_status": status,
        "failure_origin": (
            "OUTER_TIMEOUT_CONFIGURATION"
            if outer_timeout
            else (
                "ANALYZER_RESULT"
                if payload_received and not exception_type
                else "WORKER_ERROR_PAYLOAD"
            )
        ),
        "outer_timeout": str(outer_timeout),
        "payload_received": str(payload_received),
        "worker_cleanup_status": "EXITED_NORMALLY",
        "worker_exitcode": "0",
        "exception_type": (
            "ConfigurationTimeout" if outer_timeout else exception_type
        ),
        "exception_message": (
            "hard per-configuration timeout"
            if outer_timeout
            else ("synthetic outer terminal" if exception_type else "")
        ),
        "traceback": "",
    })
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)
    row = terminal["taskset_row"]
    row.update({
        "solver_status": status,
        "certification_status": "NOT_CERTIFIED",
        "taskset_proven": False,
        "first_failed_priority": None,
        "n_tasks_evaluated": 0,
        "n_tasks_candidate_found": 0,
        "n_tasks_certified": 0,
        "source_vector_hash": None,
        "target_carry_in_vector_hash": None,
        "dependency_check_status": "NOT_CHECKED",
        "fixed_carry_in_interface_status": "NOT_APPLICABLE",
        "dominance_invariant_status": "NOT_CHECKED",
        "dominance_violation_count": 0,
        "diagnostic_mode": False,
        "outer_timeout": str(outer_timeout),
        "terminal_origin": terminal_origin,
    })
    terminal["task_rows"] = []
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    return terminal_path


def _mutate_analyzer_attempt(root, variant, **changes):
    terminal_path, terminal = _terminal_for_variant(root, variant)
    attempts_path = root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    attempt = next(
        row for row in attempts if row["analysis_id"] == terminal_path.stem
    )
    attempt.update({
        "outer_timeout": "False",
        "payload_received": "True",
        "worker_cleanup_status": "EXITED_NORMALLY",
        "worker_exitcode": "0",
        "exception_type": "",
        "exception_message": "",
        "traceback": "",
    })
    attempt.update(changes)
    terminal["taskset_row"]["outer_timeout"] = attempt["outer_timeout"]
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    return terminal_path


@pytest.mark.parametrize(
    "status",
    ("COMPLETED", "NO_CANDIDATE", "NOT_APPLICABLE_DEPENDENCY"),
)
def test_resume_rejects_state_required_outer_terminal_status(
    tmp_path, monkeypatch, status
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    _rewrite_as_outer_terminal(
        outcome.output_root,
        "CW_THETA_CW",
        status,
        outer_timeout=False,
        payload_received=True,
        exception_type="",
    )
    with pytest.raises(ExecutionError, match="requires analyzer state"):
        ExecutionEngine(config).run(resume=True)


@pytest.mark.parametrize(
    "changes,match",
    (
        ({"payload_received": "False"}, "payload_received"),
        ({"outer_timeout": "True"}, "outer timeout"),
        (
            {"outer_timeout": "True", "payload_received": "True"},
            "outer timeout",
        ),
    ),
)
def test_resume_rejects_impossible_analyzer_attempt_payload_matrix(
    tmp_path, monkeypatch, changes, match
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    _mutate_analyzer_attempt(
        outcome.output_root, "CW_THETA_CW", **changes
    )
    with pytest.raises(ExecutionError, match=match):
        ExecutionEngine(config).run(resume=True)


@pytest.mark.parametrize(
    "status,outer_timeout,payload_received,match",
    (
        ("COMPLETED", True, False, "outer timeout.*TIMEOUT"),
        ("TIMEOUT", True, True, "outer_timeout.*payload_received"),
        ("UNKNOWN_SOLVER_STATUS", False, False, "solver_status"),
    ),
)
def test_resume_rejects_impossible_outer_attempt_matrix(
    tmp_path, monkeypatch, status, outer_timeout, payload_received, match
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    _rewrite_as_outer_terminal(
        outcome.output_root,
        "CW_THETA_CW",
        status,
        outer_timeout=outer_timeout,
        payload_received=payload_received,
    )
    with pytest.raises(ExecutionError, match=match):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_outer_worker_origin_with_analyzer_state(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal_path, terminal = _terminal_for_variant(
        outcome.output_root, "CW_THETA_CW"
    )
    terminal["taskset_row"]["terminal_origin"] = "OUTER_WORKER"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    with pytest.raises(ExecutionError, match="terminal.*origin"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_production_analyzer_origin_without_state(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    _rewrite_as_outer_terminal(
        outcome.output_root,
        "CW_THETA_CW",
        "TIMEOUT",
        outer_timeout=True,
        payload_received=False,
        terminal_origin="PRODUCTION_ANALYZER",
        exception_type="ConfigurationTimeout",
    )
    with pytest.raises(ExecutionError, match="terminal.*origin"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_synchronized_status_with_missing_payload(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: _failed_prefix_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    _mutate_analyzer_attempt(
        outcome.output_root,
        "CW_THETA_CW",
        solver_status="NO_CANDIDATE",
        payload_received="False",
    )
    with pytest.raises(ExecutionError, match="payload_received"):
        ExecutionEngine(config).run(resume=True)


def test_normal_outer_timeout_terminal_contract_remains_legal(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: timeout_execution(),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    assert outcome.status_counts == {"TIMEOUT": 2}
    resumed = ExecutionEngine(config).run(resume=True)
    assert resumed.terminal == 2


def _worker_error_execution():
    return engine_module.AttemptExecution(
        None,
        "INTERNAL_CONFORMANCE_FAILURE",
        False,
        .01,
        .005,
        .001,
        .001,
        .012,
        exception_type="RuntimeError",
        exception_message="synthetic worker failure",
        traceback_text="synthetic traceback",
        payload_received=True,
        worker_cleanup_status="EXITED_NORMALLY",
        worker_exitcode=0,
        failure_origin="WORKER_ERROR_PAYLOAD",
    )


def test_real_worker_internal_failure_terminal_contract_remains_legal(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: _worker_error_execution(),
    )
    outcome = ExecutionEngine(make_config(tmp_path)).run()
    terminal = next((outcome.output_root / "terminal_results").glob("*.json"))
    payload = json.loads(terminal.read_text(encoding="utf-8"))
    assert payload["taskset_row"]["solver_status"] == "INTERNAL_CONFORMANCE_FAILURE"
    assert payload["taskset_row"]["terminal_origin"] == "OUTER_WORKER"


@pytest.mark.parametrize(
    "changes,match",
    (
        (
            {
                "payload_received": "False",
                "outer_timeout": "False",
                "worker_cleanup_status": "EXITED_NORMALLY",
                "worker_exitcode": "0",
                "exception_type": "MadeUpFailure",
                "exception_message": "",
                "traceback": "",
            },
            "failure origin|IPC receive failure|worker internal failure",
        ),
        (
            {"failure_origin": "MADE_UP_FAILURE_ORIGIN"},
            "failure_origin",
        ),
        (
            {
                "failure_origin": "IPC_RECEIVE_FAILURE",
                "payload_received": "False",
                "exception_type": "EOFError",
                "exception_message": "",
                "traceback": "synthetic parent traceback",
                "worker_cleanup_status": "EXITED_NORMALLY",
                "worker_exitcode": "0",
            },
            "IPC receive failure.*nonzero exitcode",
        ),
        (
            {"traceback": ""},
            "worker error payload.*traceback",
        ),
        (
            {"exception_type": ""},
            "worker error payload.*exception_type",
        ),
        (
            {"failure_origin": "ANALYZER_RESULT"},
            "failure origin.*solver_status|analyzer result",
        ),
        (
            {"worker_exitcode": ""},
            "cleanup requires an exitcode",
        ),
        (
            {"worker_cleanup_status": "TERMINATED"},
            "cleanup status",
        ),
        (
            {"worker_cleanup_status": "KILLED"},
            "cleanup status",
        ),
        (
            {"worker_cleanup_status": "FAILED"},
            "cleanup status",
        ),
        (
            {"worker_cleanup_status": "NOT_REQUIRED"},
            "cleanup status",
        ),
    ),
)
def test_resume_rejects_worker_internal_failure_submatrix_mutation(
    tmp_path, monkeypatch, changes, match
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: _worker_error_execution(),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    attempts_path = outcome.output_root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    attempts[0].update(changes)
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)
    with pytest.raises(ExecutionError, match=match):
        ExecutionEngine(config).run(resume=True)


def _remove_terminal_for_resume(root, terminal):
    requests = read_csv(root / "analysis_requests.csv")
    for row in requests:
        if row["analysis_id"] == terminal.stem:
            row["request_status"] = "PLANNED"
    write_csv(root / "analysis_requests.csv", REQUEST_COLUMNS, requests)
    terminal.unlink()


def _completed_missing_terminal(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal, _payload = _terminal_for_variant(
        outcome.output_root, "CW_THETA_CW"
    )
    _remove_terminal_for_resume(outcome.output_root, terminal)
    return config, outcome, terminal.stem


def _mutate_attempts(root, analysis_id, mutation):
    path = root / "analysis_attempts.csv"
    rows = read_csv(path)
    selected = [row for row in rows if row["analysis_id"] == analysis_id]
    assert len(selected) == 1
    first = selected[0]
    if mutation == "gap":
        first["attempt_number"] = "2"
    elif mutation == "attempt_id":
        first["attempt_id"] = "not-the-deterministic-attempt-id"
    elif mutation == "parent":
        first["parent_attempt_id"] = "wrong-parent"
    elif mutation == "too_many":
        parent = first
        for number in (2, 3):
            extra = dict(first)
            extra["attempt_number"] = str(number)
            extra["attempt_id"] = engine_module._attempt_id(analysis_id, number)
            extra["parent_attempt_id"] = parent["attempt_id"]
            rows.append(extra)
            parent = extra
    elif mutation in {"non_timeout_parent", "wrong_retry_budget"}:
        second = dict(first)
        second["attempt_number"] = "2"
        second["attempt_id"] = engine_module._attempt_id(analysis_id, 2)
        second["parent_attempt_id"] = first["attempt_id"]
        second["timeout_budget_seconds"] = "2.0"
        if mutation == "wrong_retry_budget":
            first["solver_status"] = "TIMEOUT"
            second["timeout_budget_seconds"] = "999"
        rows.append(second)
    elif mutation == "final_status":
        first["solver_status"] = "NO_CANDIDATE"
    else:
        raise AssertionError(mutation)
    write_csv(path, ATTEMPT_COLUMNS, rows)


@pytest.mark.parametrize(
    "mutation,match",
    (
        ("gap", "attempt numbers"),
        ("attempt_id", "attempt ID"),
        ("parent", "attempt parent"),
        ("too_many", "attempt count"),
        ("non_timeout_parent", "follow a timeout"),
        ("wrong_retry_budget", "retry timeout budget"),
        ("final_status", "final attempt/state status"),
    ),
)
def test_resume_rejects_invalid_attempt_chain_before_rebuilding_terminal(
    tmp_path, monkeypatch, mutation, match
):
    config, outcome, analysis_id = _completed_missing_terminal(
        tmp_path, monkeypatch
    )
    _mutate_attempts(outcome.output_root, analysis_id, mutation)
    with pytest.raises(ExecutionError, match=match):
        ExecutionEngine(config).run(resume=True)


def _failed_prefix_execution(request):
    def solver(**kwargs):
        if kwargs["task"].name == "0":
            return taskset.SingleTaskSolverResult(
                taskset.TaskSolverStatus.NO_CANDIDATE,
                failure_reason="injected prefix failure",
            )
        return candidate_solver(**kwargs)

    result = taskset.analyze_taskset_v9_3(
        request.analysis_id,
        request.variant,
        request.analysis_input,
        source=request.source,
        source_analysis_id=request.source_analysis_id,
        dependency_check_status=request.dependency_check_status,
        single_task_solver=solver,
    )
    return engine_module.AttemptExecution(
        result, result.solver_status.value, False, .01, .005, .001, .001, .012,
        payload_received=True,
        worker_cleanup_status="EXITED_NORMALLY",
        worker_exitcode=0,
        failure_origin="ANALYZER_RESULT",
    )


@pytest.mark.parametrize(
    "variant,mutation",
    (
        ("CW_D", "deadline_value"),
        ("LOC_D", "deadline_missing_task"),
        ("CW_THETA_CW", "recursive_missing_prefix"),
        ("LOC_THETA_LOC", "recursive_wrong_prefix_value"),
        ("CW_THETA_CW", "recursive_external_task"),
        ("LOC_THETA_CW", "target_task_vector"),
        ("LOC_THETA_CW", "target_source_vector"),
    ),
)
def test_resume_rejects_mutated_carry_in_trace(
    tmp_path, monkeypatch, variant, mutation
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path, "CORE-2")
    outcome = ExecutionEngine(config).run()
    terminal, _payload = _terminal_for_variant(outcome.output_root, variant)
    state_path = outcome.output_root / "result_state" / f"{terminal.stem}.pickle"
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    records = list(state.task_records)
    if mutation == "deadline_value":
        carry = dict(records[0].carry_in_values_used)
        carry["0"] += 1
        object.__setattr__(
            records[0], "carry_in_values_used", tuple(sorted(carry.items()))
        )
    elif mutation == "deadline_missing_task":
        object.__setattr__(
            records[0],
            "carry_in_values_used",
            records[0].carry_in_values_used[:-1],
        )
    elif mutation == "recursive_missing_prefix":
        object.__setattr__(records[1], "carry_in_values_used", ())
    elif mutation == "recursive_wrong_prefix_value":
        object.__setattr__(
            records[1], "carry_in_values_used", (("0", 999),)
        )
    elif mutation == "recursive_external_task":
        object.__setattr__(
            records[1],
            "carry_in_values_used",
            records[1].carry_in_values_used + (("external-cw-task", 1),),
        )
    elif mutation == "target_task_vector":
        carry = dict(records[0].carry_in_values_used)
        carry["0"] += 1
        object.__setattr__(
            records[0], "carry_in_values_used", tuple(sorted(carry.items()))
        )
    elif mutation == "target_source_vector":
        vector = dict(state.source_candidate_vector)
        vector["0"] += 1
        object.__setattr__(
            state, "source_candidate_vector", tuple(sorted(vector.items()))
        )
    else:
        raise AssertionError(mutation)
    if mutation != "target_source_vector":
        object.__setattr__(state, "task_records", tuple(records))
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)
    _remove_terminal_for_resume(outcome.output_root, terminal)
    with pytest.raises(ExecutionError, match="state failed conformance"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_nonempty_carry_in_on_invalid_dependency(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)

    def execute(request, timeout):
        if request.variant is taskset.AnalysisVariant.CW_THETA_CW:
            return timeout_execution()
        return successful_execution(request)

    monkeypatch.setattr(engine_module, "execute_isolated", execute)
    config = make_config(tmp_path, "CORE-2")
    outcome = ExecutionEngine(config).run()
    terminal, _payload = _terminal_for_variant(
        outcome.output_root, "LOC_THETA_CW"
    )
    state_path = outcome.output_root / "result_state" / f"{terminal.stem}.pickle"
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    records = list(state.task_records)
    object.__setattr__(
        records[0], "carry_in_values_used", (("0", 1),)
    )
    object.__setattr__(state, "task_records", tuple(records))
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)
    _remove_terminal_for_resume(outcome.output_root, terminal)
    with pytest.raises(ExecutionError, match="state failed conformance"):
        ExecutionEngine(config).run(resume=True)


@pytest.mark.parametrize(
    "variant,mutation",
    (
        ("CW_D", "fixed_interface"),
        ("CW_D", "nonlocal_dominance"),
        ("LOC_THETA_CW", "local_dominance"),
        ("CW_D", "diagnostic_mode"),
    ),
)
def test_resume_rejects_mutated_result_status_matrix(
    tmp_path, monkeypatch, variant, mutation
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path, "CORE-2")
    outcome = ExecutionEngine(config).run()
    terminal, _payload = _terminal_for_variant(outcome.output_root, variant)
    state_path = outcome.output_root / "result_state" / f"{terminal.stem}.pickle"
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    if mutation == "fixed_interface":
        object.__setattr__(
            state,
            "fixed_carry_in_interface_status",
            taskset.FixedCarryInInterfaceStatus.NOT_APPLICABLE,
        )
    elif mutation == "nonlocal_dominance":
        object.__setattr__(
            state,
            "dominance_invariant_status",
            taskset.DominanceInvariantStatus.SATISFIED,
        )
    elif mutation == "local_dominance":
        object.__setattr__(
            state,
            "dominance_invariant_status",
            taskset.DominanceInvariantStatus.NOT_APPLICABLE,
        )
    elif mutation == "diagnostic_mode":
        records = list(state.task_records)
        for record in records:
            object.__setattr__(
                record,
                "certification_status",
                taskset.TaskCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED,
            )
        object.__setattr__(state, "task_records", tuple(records))
        object.__setattr__(
            state,
            "certification_status",
            taskset.AnalysisCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED,
        )
        object.__setattr__(state, "taskset_proven", False)
        object.__setattr__(state, "n_tasks_certified", 0)
        object.__setattr__(state, "diagnostic_mode", True)
    else:
        raise AssertionError(mutation)
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)
    _remove_terminal_for_resume(outcome.output_root, terminal)
    with pytest.raises(ExecutionError, match="state failed conformance"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_bool_scientific_integer_in_pickle(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal, _payload = _terminal_for_variant(
        outcome.output_root, "CW_THETA_CW"
    )
    state_path = outcome.output_root / "result_state" / f"{terminal.stem}.pickle"
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    records = list(state.task_records)
    object.__setattr__(records[0], "candidate_response_time", True)
    object.__setattr__(records[0], "closing_w", True)
    object.__setattr__(state, "task_records", tuple(records))
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)
    _remove_terminal_for_resume(outcome.output_root, terminal)
    with pytest.raises(
        ExecutionError, match="candidate_response_time.*plain integer"
    ):
        ExecutionEngine(config).run(resume=True)


def test_terminal_materialization_fails_closed_on_bool_scientific_integer(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)

    def forged_execution(request, timeout):
        execution = successful_execution(request)
        records = list(execution.result.task_records)
        object.__setattr__(records[0], "candidate_response_time", True)
        object.__setattr__(records[0], "closing_w", True)
        object.__setattr__(execution.result, "task_records", tuple(records))
        return execution

    monkeypatch.setattr(engine_module, "execute_isolated", forged_execution)
    outcome = ExecutionEngine(make_config(tmp_path)).run()
    assert outcome.status_counts == {"INTERNAL_CONFORMANCE_FAILURE": 1}
    rows = read_csv(outcome.output_root / "per_task_results.csv")
    assert all(row["candidate_response_time"] != "True" for row in rows)
    terminal = next((outcome.output_root / "terminal_results").glob("*.json"))
    payload = json.loads(terminal.read_text(encoding="utf-8"))
    assert payload["taskset_row"]["terminal_origin"] == "OUTER_WORKER"


@pytest.mark.parametrize("mutation", ("suffix_carry", "failed_priority"))
def test_resume_rejects_mutated_failure_prefix_contract(
    tmp_path, monkeypatch, mutation
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: _failed_prefix_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal, _payload = _terminal_for_variant(
        outcome.output_root, "CW_THETA_CW"
    )
    state_path = outcome.output_root / "result_state" / f"{terminal.stem}.pickle"
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    if mutation == "suffix_carry":
        records = list(state.task_records)
        object.__setattr__(
            records[1], "carry_in_values_used", (("forbidden", 1),)
        )
        object.__setattr__(state, "task_records", tuple(records))
    else:
        object.__setattr__(state, "first_failed_priority", 1)
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)
    _remove_terminal_for_resume(outcome.output_root, terminal)
    with pytest.raises(ExecutionError, match="state failed conformance"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_terminal_request_status_disagreement(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal = next((outcome.output_root / "terminal_results").glob("*.json"))
    terminal.unlink()
    with pytest.raises(ExecutionError, match="request/terminal status mismatch"):
        ExecutionEngine(config).run(resume=True)


@pytest.mark.parametrize(
    "mutation",
    ("source", "variant", "context", "certification", "task_certification"),
)
def test_resume_revalidates_every_pickled_result_contract(
    tmp_path, monkeypatch, mutation
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal, _ = _terminal_for_variant(outcome.output_root, "LOC_THETA_LOC")
    state_path = outcome.output_root / "result_state" / f"{terminal.stem}.pickle"
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    if mutation == "source":
        object.__setattr__(state, "source_analysis_id", "polluted-cw-source")
    elif mutation == "variant":
        object.__setattr__(state, "analysis_variant", taskset.AnalysisVariant.LOC_D)
    elif mutation == "context":
        object.__setattr__(
            state,
            "dependency_context",
            replace(state.dependency_context, e0_canonical_identity="tampered-e0"),
        )
    elif mutation == "certification":
        object.__setattr__(
            state,
            "certification_status",
            taskset.AnalysisCertificationStatus.NOT_CERTIFIED,
        )
    else:
        records = list(state.task_records)
        records[0] = replace(
            records[0],
            certification_status=taskset.TaskCertificationStatus.NOT_CERTIFIED,
        )
        object.__setattr__(state, "task_records", tuple(records))
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)
    with pytest.raises(ExecutionError, match="state failed conformance"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_terminal_pickle_disagreement(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    terminal, payload = _terminal_for_variant(
        outcome.output_root, "LOC_THETA_LOC"
    )
    payload["taskset_row"]["certification_status"] = "NOT_CERTIFIED"
    terminal.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExecutionError, match="terminal/state mismatch"):
        ExecutionEngine(config).run(resume=True)


def test_resume_rejects_request_provenance_tampering(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    outcome = ExecutionEngine(config).run()
    requests = read_csv(outcome.output_root / "analysis_requests.csv")
    requests[0]["source_analysis_id"] = "tampered-source"
    write_csv(
        outcome.output_root / "analysis_requests.csv", REQUEST_COLUMNS, requests
    )
    with pytest.raises(ExecutionError, match="persisted request mismatch"):
        ExecutionEngine(config).run(resume=True)
