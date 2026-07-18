import experiments.v9_3.aggregation as aggregation_module
import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.execution_engine import ExecutionEngine
from v9_3_experiment_helpers import (
    install_fake_materialization,
    make_config,
    successful_execution,
)


class _SinglePassRows:
    def __init__(self, rows):
        self._rows = rows
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("top-level rows were traversed more than once")
        return iter(self._rows)


class _CountingRows(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


def test_analysis_row_indexes_are_single_pass_and_preserve_input_order():
    tasks = _SinglePassRows([
        {
            "analysis_id": "analysis-b",
            "task_id": "2",
            "task_solver_status": "CANDIDATE_FOUND",
        },
        {
            "analysis_id": "analysis-a",
            "task_id": "0",
            "task_solver_status": "NO_CANDIDATE",
        },
        {
            "analysis_id": "analysis-b",
            "task_id": "1",
            "task_solver_status": "CANDIDATE_FOUND",
        },
    ])
    attempts = _SinglePassRows([
        {"analysis_id": "analysis-b", "attempt_id": "attempt-2"},
        {"analysis_id": "analysis-a", "attempt_id": "attempt-0"},
        {"analysis_id": "analysis-b", "attempt_id": "attempt-1"},
    ])

    indexes = aggregation_module._build_analysis_row_indexes(tasks, attempts)

    assert tasks.iterations == attempts.iterations == 1
    assert [
        row["task_id"]
        for row in indexes.tasks_by_analysis["analysis-b"]
    ] == ["2", "1"]
    assert indexes.task_ids_by_analysis["analysis-b"] == {"1", "2"}
    assert list(indexes.candidate_tasks_by_analysis["analysis-b"]) == [
        "2", "1",
    ]
    assert [
        row["attempt_id"]
        for row in indexes.attempts_by_analysis["analysis-b"]
    ] == ["attempt-2", "attempt-1"]


def test_core2_closure_traverses_top_level_task_and_attempt_tables_constant_times(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        engine_module,
        "execute_isolated",
        lambda request, timeout: successful_execution(request),
    )
    outcome = ExecutionEngine(make_config(tmp_path, "CORE-2")).run()

    original_read_csv = aggregation_module.read_csv
    counted = {}

    def counting_read_csv(path):
        rows = original_read_csv(path)
        if path.name == "per_task_results.csv":
            counted["tasks"] = _CountingRows(rows)
            return counted["tasks"]
        if path.name == "analysis_attempts.csv":
            counted["attempts"] = _CountingRows(rows)
            return counted["attempts"]
        return rows

    monkeypatch.setattr(aggregation_module, "read_csv", counting_read_csv)
    closure = aggregation_module.validate_run_closure_read_only(
        outcome.output_root, "CORE-2"
    )

    assert len(closure.requests) == 5
    assert counted["tasks"].iterations <= 4
    assert counted["attempts"].iterations <= 4
