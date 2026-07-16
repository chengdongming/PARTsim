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


def test_core2_final_cw_timeout_makes_loc_dependency_na_without_fallback(
    tmp_path, monkeypatch
):
    install_fake_materialization(monkeypatch, tmp_path)

    def execute(request, timeout):
        if request.variant.name == "CW_THETA_CW":
            return timeout_execution()
        return successful_execution(request)

    monkeypatch.setattr(engine_module, "execute_isolated", execute)
    outcome = ExecutionEngine(make_config(tmp_path, "CORE-2")).run()
    rows = read_csv(outcome.output_root / "per_taskset_results.csv")
    target = next(
        row for row in rows if row["analysis_variant"] == "LOC_THETA_CW"
    )
    request = next(
        row for row in read_csv(outcome.output_root / "analysis_requests.csv")
        if row["analysis_id"] == target["analysis_id"]
    )
    assert target["solver_status"] == "NOT_APPLICABLE_DEPENDENCY"
    assert target["dependency_check_status"] == "INVALID"
    assert target["source_analysis_id"] == request["source_analysis_id"]
    assert target["source_vector_hash"] == ""
    assert target["target_carry_in_vector_hash"] == ""
