import json

from scripts.analyze_b4_priority_energy import analyze


def test_direct_analyzer_extracts_schema3_statistics(tmp_path):
    plan = {
        "rows": [{
            "case_id": "case-1", "algorithm": "ASAP-BLOCK",
            "utilization": "0.3", "lambda_E": "0.70", "rho_E": "1",
            "taskset_semantic_hash": "a" * 64,
        }]
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    trace = {
        "trace_schema_version": 3, "run_id": "case-1",
        "configured_scheduler": "gpfp_asap_block",
        "expected_simulation_horizon_ms": 30000,
        "observed_simulation_end_ms": 30000,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
        "observability_summary_horizon_ms": 30000,
        "taskset_semantic_hash": "a" * 64,
        "energy_summary": {}, "mechanism_summary": {},
        "per_task_summary": [
            {"priority_rank": rank, "adjudicable_jobs": 100,
             "released_jobs": 100, "completed_jobs": 100,
             "terminated_jobs": 0, "deadline_miss_jobs": 0,
             "unfinished_at_horizon_jobs": 0}
            for rank in range(10)
        ],
    }
    result = {"case_id": "case-1", "status": "success",
              "technical_error": False, "result_relpath": "result.json"}
    (tmp_path / "results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
    (tmp_path / "result.json").write_text(json.dumps(trace), encoding="utf-8")
    audit = analyze(tmp_path)
    assert audit["missing"] == 0
    assert audit["duplicate"] == 0
    assert audit["technical_error"] == 0
