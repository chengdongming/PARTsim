import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.result_writer import read_csv
from v9_3_experiment_helpers import install_fake_materialization, make_config, successful_execution, timeout_execution


def test_only_timeout_retries_and_links_parent_attempt(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    seen = {}

    def execute(request, timeout):
        count = seen.get(request.analysis_id, 0)
        seen[request.analysis_id] = count + 1
        if request.variant.name == "CW_THETA_CW" and count == 0:
            return timeout_execution()
        return successful_execution(request)

    monkeypatch.setattr(engine_module, "execute_isolated", execute)
    outcome = ExecutionEngine(make_config(tmp_path)).run()
    attempts = read_csv(outcome.output_root / "analysis_attempts.csv")
    cw = [row for row in attempts if row["analysis_id"] == attempts[0]["analysis_id"]]
    assert [row["solver_status"] for row in cw] == ["TIMEOUT", "COMPLETED"]
    assert cw[1]["parent_attempt_id"] == cw[0]["attempt_id"]
    assert outcome.status_counts.get("TIMEOUT", 0) == 0
