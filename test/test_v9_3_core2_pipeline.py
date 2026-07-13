import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.aggregation import aggregate_core2
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.result_writer import read_csv
from v9_3_experiment_helpers import install_fake_materialization, make_config, successful_execution


def test_core2_five_variants_dependency_and_outputs(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(engine_module, "execute_isolated", lambda request, timeout: successful_execution(request))
    outcome = ExecutionEngine(make_config(tmp_path, "CORE-2")).run()
    summary = aggregate_core2(outcome.output_root)
    rows = read_csv(outcome.output_root / "per_taskset_results.csv")
    assert outcome.requested == outcome.terminal == 5
    assert {row["analysis_variant"] for row in rows} == {
        "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"
    }
    dependency = read_csv(outcome.output_root / "dependency_records.csv")
    assert len(dependency) == 1
    assert dependency[0]["applicable"] == "True"
    assert dependency[0]["source_vector_hash"] == dependency[0]["target_vector_hash"]
    assert dependency[0]["fallback_used"] == "False"
    assert summary["dominance_violations"] == 0
