import json

import pytest

import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.execution_engine import ExecutionEngine, ExecutionError
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, ResultWriter, ResultWriterError, read_csv,
)
from v9_3_experiment_helpers import install_fake_materialization, make_config, successful_execution


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
