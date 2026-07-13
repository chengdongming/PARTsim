import experiments.v9_3.execution_engine as engine_module
from experiments.v9_3.aggregation import aggregate_core1
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.result_writer import read_csv
from v9_3_experiment_helpers import install_fake_materialization, make_config, successful_execution


def test_core1_pipeline_pairing_outputs_and_plot_data(tmp_path, monkeypatch):
    install_fake_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(engine_module, "execute_isolated", lambda request, timeout: successful_execution(request))
    outcome = ExecutionEngine(make_config(tmp_path, "CORE-1", e0=["0", "1"])).run()
    summary = aggregate_core1(outcome.output_root)
    assert outcome.requested == outcome.terminal == 4
    rows = read_csv(outcome.output_root / "per_taskset_results.csv")
    assert {row["analysis_variant"] for row in rows} == {"CW_THETA_CW", "LOC_THETA_LOC"}
    assert len({row["taskset_hash"] for row in rows}) == 1
    assert summary["dominance_violations"] == 0
    assert read_csv(outcome.output_root / "core1_plot_data.csv")
