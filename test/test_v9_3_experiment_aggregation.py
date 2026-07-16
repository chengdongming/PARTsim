import json
import pickle

import pytest

import asap_block_rta_v9_3_taskset as taskset
import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.aggregation import (
    AggregationError, aggregate_core1, aggregate_core2, variant_summary,
)
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, DEPENDENCY_COLUMNS, DOMINANCE_COLUMNS,
    FAILURE_COLUMNS, TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, read_csv,
    write_csv,
)
from v9_3_experiment_helpers import (
    install_fake_materialization, make_config, successful_execution,
    timeout_execution,
)


def test_denominators_are_reported_separately():
    requests = [
        {"cell_id": "c", "variant": "CW_THETA_CW"},
        {"cell_id": "c", "variant": "CW_THETA_CW"},
        {"cell_id": "c", "variant": "CW_THETA_CW"},
    ]
    results = [
        {"cell_id": "c", "analysis_variant": "CW_THETA_CW", "solver_status": "COMPLETED", "taskset_proven": "True", "runtime_wall_seconds": "1"},
        {"cell_id": "c", "analysis_variant": "CW_THETA_CW", "solver_status": "NO_CANDIDATE", "taskset_proven": "False", "runtime_wall_seconds": "2"},
        {"cell_id": "c", "analysis_variant": "CW_THETA_CW", "solver_status": "TIMEOUT", "taskset_proven": "False", "runtime_wall_seconds": "3"},
    ]
    row = variant_summary(requests, results)[0]
    assert row["unconditional_denominator"] == 3
    assert row["completed_only_denominator"] == 2
    assert row["certification_ratio_unconditional"] == 1 / 3
    assert row["certification_ratio_completed_only"] == 1 / 2
    assert row["runtime_mean"] == 1.5
    assert row["runtime_censored_count"] == 1


def _completed_run(tmp_path, monkeypatch, core="CORE-1"):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    return ExecutionEngine(make_config(tmp_path, core)).run()


def _outer_timeout_run(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: timeout_execution(),
    )
    return ExecutionEngine(make_config(tmp_path)).run()


def _worker_internal_run(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)

    def worker_error(request, timeout):
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

    monkeypatch.setattr(engine_module, "execute_isolated", worker_error)
    config = make_config(tmp_path)
    config["execution"]["fail_fast_on_p0"] = False
    outcome = ExecutionEngine(config).run()
    write_csv(outcome.output_root / "failures.csv", FAILURE_COLUMNS, [])
    return outcome


def test_aggregation_rejects_impossible_no_payload_internal_failure(
    tmp_path, monkeypatch
):
    outcome = _worker_internal_run(tmp_path, monkeypatch)
    path = outcome.output_root / "analysis_attempts.csv"
    attempts = read_csv(path)
    attempts[0].update({
        "payload_received": "False",
        "outer_timeout": "False",
        "worker_cleanup_status": "EXITED_NORMALLY",
        "worker_exitcode": "0",
        "exception_type": "MadeUpFailure",
        "exception_message": "",
        "traceback": "",
    })
    write_csv(path, ATTEMPT_COLUMNS, attempts)
    with pytest.raises(
        AggregationError,
        match="failure origin|IPC receive failure|worker internal failure",
    ):
        aggregate_core1(outcome.output_root)


def _mutate_terminal_and_materialized_result(
    root, *, terminal_changes, csv_changes
):
    terminal_path = next((root / "terminal_results").glob("*.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    analysis_id = terminal["taskset_row"]["analysis_id"]
    terminal["taskset_row"].update(terminal_changes)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    result_path = root / "per_taskset_results.csv"
    results = read_csv(result_path)
    result = next(row for row in results if row["analysis_id"] == analysis_id)
    result.update(csv_changes)
    write_csv(result_path, TASKSET_RESULT_COLUMNS, results)


@pytest.mark.parametrize(
    "terminal_changes,csv_changes,match",
    (
        (
            {"certification_status": "CERTIFIED_TASKSET"},
            {"certification_status": "CERTIFIED_TASKSET"},
            "state-less terminal result matrix",
        ),
        (
            {"taskset_proven": True},
            {"taskset_proven": "True"},
            "state-less terminal result matrix",
        ),
        (
            {"n_tasks_evaluated": 1},
            {"n_tasks_evaluated": "1"},
            "state-less terminal result matrix",
        ),
        (
            {"n_tasks_candidate_found": 1},
            {"n_tasks_candidate_found": "1"},
            "state-less terminal result matrix",
        ),
        (
            {"n_tasks_certified": 1},
            {"n_tasks_certified": "1"},
            "state-less terminal result matrix",
        ),
        (
            {"first_failed_priority": 0},
            {"first_failed_priority": "0"},
            "state-less terminal result matrix",
        ),
        (
            {"dependency_check_status": "VALID"},
            {"dependency_check_status": "VALID"},
            "non-dependency variant|state-less terminal result matrix",
        ),
        (
            {"fixed_carry_in_interface_status": "VALID"},
            {"fixed_carry_in_interface_status": "VALID"},
            "state-less terminal result matrix",
        ),
        (
            {"dominance_invariant_status": "DOMINATES"},
            {"dominance_invariant_status": "DOMINATES"},
            "state-less terminal result matrix",
        ),
        (
            {"dominance_counterexample": {"task_id": "forged"}},
            {},
            "terminal result schema",
        ),
        (
            {"n_tasks_evaluated": True},
            {"n_tasks_evaluated": "True"},
            "n_tasks_evaluated.*plain integer",
        ),
        (
            {"n_tasks_evaluated": "True"},
            {"n_tasks_evaluated": "True"},
            "n_tasks_evaluated.*plain integer",
        ),
        (
            {"taskset_proven": "CERTAINLY"},
            {"taskset_proven": "CERTAINLY"},
            "taskset_proven.*boolean",
        ),
        (
            {
                "certification_status": "CERTIFIED_TASKSET",
                "taskset_proven": True,
                "n_tasks_evaluated": 2,
                "n_tasks_candidate_found": 2,
                "n_tasks_certified": 2,
            },
            {
                "certification_status": "CERTIFIED_TASKSET",
                "taskset_proven": "True",
                "n_tasks_evaluated": "2",
                "n_tasks_candidate_found": "2",
                "n_tasks_certified": "2",
            },
            "state-less terminal result matrix",
        ),
    ),
)
def test_aggregation_rejects_synchronized_outer_terminal_result_mutation(
    tmp_path,
    monkeypatch,
    terminal_changes,
    csv_changes,
    match,
):
    outcome = _outer_timeout_run(tmp_path, monkeypatch)
    _mutate_terminal_and_materialized_result(
        outcome.output_root,
        terminal_changes=terminal_changes,
        csv_changes=csv_changes,
    )
    with pytest.raises(AggregationError, match=match):
        aggregate_core1(outcome.output_root)


def _columns_with_failure_origin():
    return (
        TASKSET_RESULT_COLUMNS
        if "failure_origin" in TASKSET_RESULT_COLUMNS
        else (*TASKSET_RESULT_COLUMNS, "failure_origin")
    )


def test_aggregation_rejects_terminal_csv_failure_origin_disagreement(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch)
    result_path = outcome.output_root / "per_taskset_results.csv"
    results = read_csv(result_path)
    target = results[0]
    target["failure_origin"] = "WORKER_ERROR_PAYLOAD"
    write_csv(result_path, _columns_with_failure_origin(), results)

    with pytest.raises(AggregationError, match="failure_origin"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_three_distinct_failure_origins(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch)
    result_path = outcome.output_root / "per_taskset_results.csv"
    results = read_csv(result_path)
    target = results[0]
    analysis_id = target["analysis_id"]
    target["failure_origin"] = "WORKER_ERROR_PAYLOAD"
    write_csv(result_path, _columns_with_failure_origin(), results)

    terminal_path = (
        outcome.output_root / "terminal_results" / f"{analysis_id}.json"
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["taskset_row"]["failure_origin"] = "OUTER_TIMEOUT_CONFIGURATION"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    attempts_path = outcome.output_root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    final = next(
        row for row in attempts if row["analysis_id"] == analysis_id
    )
    final.update({
        "solver_status": "TIMEOUT",
        "failure_origin": "OUTER_TIMEOUT_STARTUP",
        "outer_timeout": "True",
        "payload_received": "False",
        "worker_cleanup_status": "REAPED_AFTER_TERMINATE",
        "worker_exitcode": "-15",
        "exception_type": "WorkerStartupTimeout",
        "exception_message": "analysis worker did not start",
        "traceback": "",
    })
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)

    with pytest.raises(
        AggregationError,
        match="attempt|analyzer state|failure.origin|failure_origin",
    ):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_analyzer_state_with_outer_failure_origin(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch)
    result_path = outcome.output_root / "per_taskset_results.csv"
    results = read_csv(result_path)
    target = results[0]
    analysis_id = target["analysis_id"]
    target.update({
        "solver_status": "TIMEOUT",
        "failure_origin": "OUTER_TIMEOUT_CONFIGURATION",
        "outer_timeout": "True",
    })
    write_csv(result_path, _columns_with_failure_origin(), results)
    terminal_path = (
        outcome.output_root / "terminal_results" / f"{analysis_id}.json"
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["taskset_row"].update({
        "solver_status": "TIMEOUT",
        "failure_origin": "OUTER_TIMEOUT_CONFIGURATION",
        "outer_timeout": "True",
    })
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    attempts_path = outcome.output_root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    final = next(
        row for row in attempts if row["analysis_id"] == analysis_id
    )
    final.update({
        "solver_status": "TIMEOUT",
        "failure_origin": "OUTER_TIMEOUT_CONFIGURATION",
        "outer_timeout": "True",
        "payload_received": "False",
        "worker_cleanup_status": "REAPED_AFTER_TERMINATE",
        "worker_exitcode": "-15",
        "exception_type": "ConfigurationTimeout",
        "exception_message": "hard per-configuration timeout",
        "traceback": "",
    })
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)

    with pytest.raises(AggregationError, match="analyzer state"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_stateless_timeout_with_analyzer_origin(
    tmp_path, monkeypatch
):
    outcome = _outer_timeout_run(tmp_path, monkeypatch)
    attempts_path = outcome.output_root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    analysis_id = attempts[0]["analysis_id"]
    final = [row for row in attempts if row["analysis_id"] == analysis_id][-1]
    final["failure_origin"] = "ANALYZER_RESULT"
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)

    result_path = outcome.output_root / "per_taskset_results.csv"
    results = read_csv(result_path)
    target = next(row for row in results if row["analysis_id"] == analysis_id)
    target["failure_origin"] = "ANALYZER_RESULT"
    write_csv(result_path, _columns_with_failure_origin(), results)
    terminal_path = (
        outcome.output_root / "terminal_results" / f"{analysis_id}.json"
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["taskset_row"]["failure_origin"] = "ANALYZER_RESULT"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    with pytest.raises(AggregationError, match="failure origin|failure_origin"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_missing_right_side(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch)
    path = outcome.output_root / "per_taskset_results.csv"
    rows = read_csv(path)
    rows = [row for row in rows if row["analysis_variant"] != "LOC_THETA_LOC"]
    write_csv(path, TASKSET_RESULT_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="result set"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_duplicate_left_side(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch)
    path = outcome.output_root / "per_taskset_results.csv"
    rows = read_csv(path)
    rows.append(next(row for row in rows if row["analysis_variant"] == "CW_THETA_CW"))
    write_csv(path, TASKSET_RESULT_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="duplicate"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_extra_analysis(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch)
    path = outcome.output_root / "per_taskset_results.csv"
    rows = read_csv(path)
    rows.append({**rows[0], "analysis_id": "extra-analysis"})
    write_csv(path, TASKSET_RESULT_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="result set"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_partial_run(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module, "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    config = make_config(tmp_path)
    config["grid"]["tasksets_per_cell"] = 2
    outcome = ExecutionEngine(config).run(max_tasksets=1)
    with pytest.raises(RuntimeError, match="partial run"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_stopped_run(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch)
    checkpoint_path = outcome.output_root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["stop_requested"] = True
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(RuntimeError, match="stopped"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_wrong_non_dependency_provenance(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch)
    path = outcome.output_root / "per_taskset_results.csv"
    rows = read_csv(path)
    target = next(
        row for row in rows if row["analysis_variant"] == "LOC_THETA_LOC"
    )
    target["source_analysis_id"] = "forbidden-source"
    write_csv(path, TASKSET_RESULT_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="source"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_terminal_task_set_mismatch(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch)
    terminal_path = next((outcome.output_root / "terminal_results").glob("*.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["task_rows"] = terminal["task_rows"][:-1]
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    with pytest.raises(RuntimeError, match="terminal/CSV task set mismatch"):
        aggregate_core1(outcome.output_root)


def test_aggregation_rejects_missing_dominance_check(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch)
    path = outcome.output_root / "dominance_checks.csv"
    rows = read_csv(path)
    write_csv(path, DOMINANCE_COLUMNS, rows[:-1])
    with pytest.raises(RuntimeError, match="dominance check closure mismatch"):
        aggregate_core1(outcome.output_root)


def test_core2_aggregation_requires_all_five_variants(tmp_path, monkeypatch):
    outcome = _completed_run(tmp_path, monkeypatch, core="CORE-2")
    path = outcome.output_root / "per_taskset_results.csv"
    rows = read_csv(path)
    rows = [row for row in rows if row["analysis_variant"] != "LOC_D"]
    write_csv(path, TASKSET_RESULT_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="result set"):
        aggregate_core2(outcome.output_root)


def test_core2_aggregation_rejects_dependency_provenance_tampering(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch, core="CORE-2")
    path = outcome.output_root / "dependency_records.csv"
    rows = read_csv(path)
    rows[0]["source_analysis_id"] = "tampered-source"
    write_csv(path, DEPENDENCY_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="dependency provenance mismatch"):
        aggregate_core2(outcome.output_root)


def _completed_retry_run(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    calls = {}

    def execute(request, timeout):
        count = calls.get(request.analysis_id, 0)
        calls[request.analysis_id] = count + 1
        return timeout_execution() if count == 0 else successful_execution(request)

    monkeypatch.setattr(engine_module, "execute_isolated", execute)
    return ExecutionEngine(make_config(tmp_path)).run()


@pytest.mark.parametrize(
    "mutation",
    (
        "attempt_number",
        "parent_attempt_id",
        "first_status",
        "retry_budget",
        "final_status",
    ),
)
def test_aggregation_revalidates_complete_attempt_chain(
    tmp_path, monkeypatch, mutation
):
    outcome = _completed_retry_run(tmp_path, monkeypatch)
    path = outcome.output_root / "analysis_attempts.csv"
    rows = read_csv(path)
    analysis_id = rows[0]["analysis_id"]
    attempts = [row for row in rows if row["analysis_id"] == analysis_id]
    assert len(attempts) == 2
    if mutation == "attempt_number":
        attempts[1]["attempt_number"] = "3"
    elif mutation == "parent_attempt_id":
        attempts[1]["parent_attempt_id"] = "wrong-parent"
    elif mutation == "first_status":
        attempts[0]["solver_status"] = "COMPLETED"
    elif mutation == "retry_budget":
        attempts[1]["timeout_budget_seconds"] = "999"
    elif mutation == "final_status":
        attempts[1]["solver_status"] = "NO_CANDIDATE"
    else:
        raise AssertionError(mutation)
    write_csv(path, ATTEMPT_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="attempt"):
        aggregate_core1(outcome.output_root)


@pytest.mark.parametrize(
    "mutation,match",
    (
        ("payload_received", "payload_received"),
        ("outer_timeout", "outer timeout"),
        ("cleanup_status", "cleanup"),
    ),
)
def test_aggregation_revalidates_attempt_payload_semantics(
    tmp_path, monkeypatch, mutation, match
):
    outcome = _completed_run(tmp_path, monkeypatch)
    attempts_path = outcome.output_root / "analysis_attempts.csv"
    attempts = read_csv(attempts_path)
    target = attempts[0]
    target.update({
        "payload_received": "True",
        "outer_timeout": "False",
        "worker_cleanup_status": "EXITED_NORMALLY",
        "worker_exitcode": "0",
        "exception_type": "",
        "exception_message": "",
        "traceback": "",
    })
    if mutation == "payload_received":
        target["payload_received"] = "False"
    elif mutation == "outer_timeout":
        target["outer_timeout"] = "True"
        taskset_path = outcome.output_root / "per_taskset_results.csv"
        tasksets = read_csv(taskset_path)
        result = next(
            row for row in tasksets
            if row["analysis_id"] == target["analysis_id"]
        )
        result["outer_timeout"] = "True"
        write_csv(taskset_path, TASKSET_RESULT_COLUMNS, tasksets)
        terminal_path = (
            outcome.output_root
            / "terminal_results"
            / f"{target['analysis_id']}.json"
        )
        terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        terminal["taskset_row"]["outer_timeout"] = "True"
        terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    elif mutation == "cleanup_status":
        target["worker_cleanup_status"] = "MADE_UP_CLEANUP_STATUS"
    else:
        raise AssertionError(mutation)
    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)
    with pytest.raises(AggregationError, match=match):
        aggregate_core1(outcome.output_root)


@pytest.mark.parametrize(
    "field,value",
    (
        ("source_certified", "False"),
        ("source_numerical_mode", "FLOAT_APPROXIMATION"),
        ("target_numerical_mode", "FLOAT_APPROXIMATION"),
        ("source_e0", "999"),
        ("target_e0", "999"),
        ("source_taskset_hash", "wrong-source-taskset"),
        ("target_taskset_hash", "wrong-target-taskset"),
        ("source_analysis_id", "wrong-source-analysis"),
        ("source_variant", "LOC_D"),
        ("target_variant", "LOC_D"),
        ("source_vector_hash", "wrong-source-vector"),
        ("target_vector_hash", "wrong-target-vector"),
        ("applicable", "False"),
        ("dependency_check_status", "INVALID"),
        ("fallback_used", "True"),
    ),
)
def test_aggregation_rejects_every_dependency_record_field_mutation(
    tmp_path, monkeypatch, field, value
):
    outcome = _completed_run(tmp_path, monkeypatch, core="CORE-2")
    path = outcome.output_root / "dependency_records.csv"
    rows = read_csv(path)
    rows[0][field] = value
    write_csv(path, DEPENDENCY_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="dependency"):
        aggregate_core2(outcome.output_root)


def test_aggregation_rejects_matching_but_false_dependency_vector_hashes(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch, core="CORE-2")
    path = outcome.output_root / "dependency_records.csv"
    rows = read_csv(path)
    rows[0]["source_vector_hash"] = "same-but-wrong-vector"
    rows[0]["target_vector_hash"] = "same-but-wrong-vector"
    write_csv(path, DEPENDENCY_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="dependency"):
        aggregate_core2(outcome.output_root)


def test_aggregation_rejects_invalid_source_claimed_as_certified(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)

    def execute(request, timeout):
        if request.variant is taskset.AnalysisVariant.CW_THETA_CW:
            return timeout_execution()
        return successful_execution(request)

    monkeypatch.setattr(engine_module, "execute_isolated", execute)
    outcome = ExecutionEngine(make_config(tmp_path, "CORE-2")).run()
    path = outcome.output_root / "dependency_records.csv"
    rows = read_csv(path)
    assert rows[0]["source_certified"] == "False"
    rows[0]["source_certified"] = "True"
    write_csv(path, DEPENDENCY_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="dependency"):
        aggregate_core2(outcome.output_root)


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "extra"))
def test_aggregation_rejects_dependency_record_set_mutation(
    tmp_path, monkeypatch, mutation
):
    outcome = _completed_run(tmp_path, monkeypatch, core="CORE-2")
    path = outcome.output_root / "dependency_records.csv"
    rows = read_csv(path)
    if mutation == "missing":
        rows = []
    elif mutation == "duplicate":
        rows.append(dict(rows[0]))
    else:
        rows.append({**rows[0], "analysis_id": "extra-analysis"})
    write_csv(path, DEPENDENCY_COLUMNS, rows)
    with pytest.raises(RuntimeError, match="dependency"):
        aggregate_core2(outcome.output_root)


def test_aggregation_binds_dependency_hash_to_real_source_candidates(
    tmp_path, monkeypatch
):
    outcome = _completed_run(tmp_path, monkeypatch, core="CORE-2")
    taskset_rows = read_csv(outcome.output_root / "per_taskset_results.csv")
    source = next(
        row for row in taskset_rows
        if row["analysis_variant"] == "CW_THETA_CW"
    )
    state_path = (
        outcome.output_root / "result_state" / f"{source['analysis_id']}.pickle"
    )
    with state_path.open("rb") as handle:
        state = pickle.load(handle)
    records = list(state.task_records)
    object.__setattr__(records[-1], "candidate_response_time", 2)
    object.__setattr__(records[-1], "closing_w", 2)
    object.__setattr__(state, "task_records", tuple(records))
    with state_path.open("wb") as handle:
        pickle.dump(state, handle)

    terminal_path = (
        outcome.output_root / "terminal_results" / f"{source['analysis_id']}.json"
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["task_rows"][-1]["candidate_response_time"] = 2
    terminal["task_rows"][-1]["closing_w"] = 2
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")

    task_path = outcome.output_root / "per_task_results.csv"
    task_rows = read_csv(task_path)
    source_task = next(
        row for row in task_rows
        if row["analysis_id"] == source["analysis_id"] and row["task_id"] == "1"
    )
    source_task["candidate_response_time"] = "2"
    source_task["closing_w"] = "2"
    write_csv(task_path, TASK_RESULT_COLUMNS, task_rows)

    dominance_path = outcome.output_root / "dominance_checks.csv"
    dominance = read_csv(dominance_path)
    for row in dominance:
        if row["left_variant"] == "CW_THETA_CW" and row["task_id"] == "1":
            row["left_candidate"] = "2"
            reduction = 2 - int(row["right_candidate"])
            row["reduction"] = str(reduction)
            row["status"] = "TIGHTER" if reduction > 0 else "EQUAL"
    write_csv(dominance_path, DOMINANCE_COLUMNS, dominance)

    with pytest.raises(RuntimeError, match="dependency"):
        aggregate_core2(outcome.output_root)
