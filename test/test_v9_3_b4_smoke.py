import json

from experiments.v9_3.performance_outcome import PERF_OUTCOME_VERSION


def test_job_trace_feasibility_evidence_if_present():
    path = __import__("pathlib").Path("/tmp/b4_job_trace_recovery1.json")
    if not path.is_file():
        return
    trace = json.loads(path.read_text(encoding="utf-8"))
    kinds = {row["event_type"] for row in trace["events"]}
    assert {"arrival", "scheduled", "end_instance", "simulation_run_outcome"}.issubset(kinds)
    assert trace["simulation_completion_reason"] == "reached_horizon"
    assert PERF_OUTCOME_VERSION == "PERF_OUTCOME_V2"
