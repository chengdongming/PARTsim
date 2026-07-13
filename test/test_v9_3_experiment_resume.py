import pytest

import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.execution_engine import ExecutionEngine, ExecutionError
from experiments.v9_3.result_writer import read_csv
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
