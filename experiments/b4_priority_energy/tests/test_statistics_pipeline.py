import json

from scripts.analyze_b4_priority_energy import analyze


def test_direct_statistics_have_zero_coverage_defects(tmp_path):
    plan = {"rows": []}
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text("", encoding="utf-8")
    audit = analyze(tmp_path)
    assert audit["expected_request_count"] == 0
    assert audit["missing"] == 0
    assert audit["duplicate"] == 0
    assert audit["pairing_complete"] is True
