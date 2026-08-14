import csv
import json

from scripts.analyze_b4_priority_energy import analyze


def test_direct_statistics_csv_is_emitted(tmp_path):
    (tmp_path / "plan.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text("", encoding="utf-8")
    analyze(tmp_path)
    with (tmp_path / "cell_summary.csv").open(newline="", encoding="utf-8") as handle:
        assert "technical_errors" in next(csv.reader(handle))
