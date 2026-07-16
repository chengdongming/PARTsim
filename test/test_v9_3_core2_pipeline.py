import json
import pickle

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

    source = next(
        row for row in rows if row["analysis_variant"] == "CW_THETA_CW"
    )
    target = next(
        row for row in rows if row["analysis_variant"] == "LOC_THETA_CW"
    )
    local_recursive = next(
        row for row in rows if row["analysis_variant"] == "LOC_THETA_LOC"
    )
    requests = read_csv(outcome.output_root / "analysis_requests.csv")
    target_request = next(
        row for row in requests if row["analysis_id"] == target["analysis_id"]
    )
    local_request = next(
        row for row in requests
        if row["analysis_id"] == local_recursive["analysis_id"]
    )
    target_terminal = json.loads(
        (
            outcome.output_root / "terminal_results"
            / f"{target['analysis_id']}.json"
        ).read_text(encoding="utf-8")
    )["taskset_row"]
    local_terminal = json.loads(
        (
            outcome.output_root / "terminal_results"
            / f"{local_recursive['analysis_id']}.json"
        ).read_text(encoding="utf-8")
    )["taskset_row"]
    with (
        outcome.output_root / "result_state"
        / f"{target['analysis_id']}.pickle"
    ).open("rb") as handle:
        target_state = pickle.load(handle)
    with (
        outcome.output_root / "result_state"
        / f"{local_recursive['analysis_id']}.pickle"
    ).open("rb") as handle:
        local_state = pickle.load(handle)
    assert (
        source["analysis_id"]
        == target_request["source_analysis_id"]
        == target["source_analysis_id"]
        == target_terminal["source_analysis_id"]
        == target_state.source_analysis_id
        == dependency[0]["source_analysis_id"]
    )
    assert (
        target["source_vector_hash"]
        == target["target_carry_in_vector_hash"]
        == target_terminal["source_vector_hash"]
        == dependency[0]["source_vector_hash"]
        == dependency[0]["target_vector_hash"]
    )
    assert local_request["source_analysis_id"] == ""
    assert local_recursive["source_analysis_id"] == ""
    assert local_recursive["source_vector_hash"] == ""
    assert local_recursive["target_carry_in_vector_hash"] == ""
    assert local_terminal["source_analysis_id"] is None
    assert local_state.source_analysis_id is None
    assert local_state.source_candidate_vector == ()
