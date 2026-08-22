import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_rta_load_uc_fixed_scale as analyzer


METHODS = ("CW", "LOC", "PH", "SEQ")


def _write_fixture(
    tmp_path,
    *,
    e0_values=("37",),
    taskset_ucs=("1/10", "1/5"),
    statuses=None,
    vectors=None,
    energy_mode="fixed_scale",
):
    output = tmp_path / "fixed-scale"
    output.mkdir(parents=True)
    config = {
        "energy_mode": energy_mode,
        "energy_scale": "3/2",
        "seed": 7,
        "uc_values": list(taskset_ucs),
        "e0_values": list(e0_values),
        "methods": list(METHODS),
        "samples_per_uc": 1,
    }
    (output / "run_config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    tasksets = []
    for index, uc in enumerate(taskset_ucs):
        tasksets.append({
            "taskset_id": f"ts-{index}", "energy_mode": "fixed_scale",
            "energy_scale": "3/2", "target_uc": uc, "actual_uc": uc,
            "tasks": [{"name": "task-0"}],
        })
    (output / "tasksets.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tasksets), encoding="utf-8"
    )
    rows = []
    for taskset in tasksets:
        for e0 in e0_values:
            for method in METHODS:
                status = (statuses or {}).get((taskset["taskset_id"], e0, method), "PROVEN")
                vector = (vectors or {}).get((taskset["taskset_id"], e0, method))
                if vector is None:
                    vector = {"CW": [4], "LOC": [3], "PH": [2], "SEQ": [1]}[method]
                rows.append({
                    "request_id": f"{taskset['taskset_id']}-e0-{e0}-{method}",
                    "taskset_id": taskset["taskset_id"],
                    "target_uc": taskset["target_uc"], "e0": e0,
                    "method": method, "final_status": status,
                    "response_time_vector": vector,
                })
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return output


def _report(output):
    return json.loads((output / "invariant_report.json").read_text(encoding="utf-8"))


def test_fixed_scale_analyzer_writes_summary_figure_and_zero_invariants(tmp_path):
    output = _write_fixture(tmp_path)
    assert analyzer.main(["--input", str(output)]) == 0
    assert (output / "summary.csv").is_file()
    assert (output / "figure_uc_fixed_scale_e0_37.png").is_file()
    report = _report(output)
    assert report["n_tasksets"] == 2
    assert report["n_results"] == 8
    assert all(report[key] == 0 for key in (
        "dominance_violation_count",
        "certification_nesting_violation_count",
        "e0_monotonicity_violation_count",
        "denominator_violation_count",
    ))


def test_fixed_scale_analyzer_detects_missing_and_duplicate_results(tmp_path):
    missing = _write_fixture(tmp_path / "missing")
    lines = (missing / "results.jsonl").read_text().splitlines()
    (missing / "results.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    assert analyzer.main(["--input", str(missing)]) == 2
    assert _report(missing)["missing_method_or_results_count"] == 1

    duplicate = _write_fixture(tmp_path / "duplicate")
    content = (duplicate / "results.jsonl").read_text()
    (duplicate / "results.jsonl").write_text(
        content + content.splitlines()[0] + "\n"
    )
    assert analyzer.main(["--input", str(duplicate)]) == 2
    report = _report(duplicate)
    assert report["duplicate_result_key_count"] == 1
    assert report["duplicate_request_id_count"] == 1


def test_fixed_scale_analyzer_detects_certification_nesting_violation(tmp_path):
    output = _write_fixture(tmp_path, statuses={
        ("ts-0", "37", "LOC"): "NOT_PROVEN",
    })
    assert analyzer.main(["--input", str(output)]) == 2
    assert _report(output)["certification_nesting_violation_count"] == 1


def test_fixed_scale_analyzer_detects_response_time_dominance_violation(tmp_path):
    output = _write_fixture(tmp_path, vectors={
        ("ts-0", "37", "CW"): [1],
        ("ts-0", "37", "LOC"): [2],
        ("ts-0", "37", "PH"): [3],
        ("ts-0", "37", "SEQ"): [4],
    })
    assert analyzer.main(["--input", str(output)]) == 2
    assert _report(output)["dominance_violation_count"] == 3


def test_fixed_scale_analyzer_checks_e0_monotonicity(tmp_path):
    output = _write_fixture(
        tmp_path,
        e0_values=("0", "37"),
        taskset_ucs=("1/10",),
        statuses={("ts-0", "37", "CW"): "NOT_PROVEN"},
    )
    assert analyzer.main(["--input", str(output)]) == 2
    assert _report(output)["e0_monotonicity_violation_count"] == 1


def test_fixed_scale_analyzer_checks_each_adjacent_e0_pair(tmp_path):
    output = _write_fixture(
        tmp_path,
        e0_values=("0", "20", "37"),
        taskset_ucs=("1/10",),
        statuses={
            ("ts-0", "20", "CW"): "NOT_PROVEN",
        },
    )
    assert analyzer.main(["--input", str(output)]) == 2
    assert _report(output)["e0_monotonicity_violation_count"] == 1


def test_fixed_scale_analyzer_detects_intermediate_e0_response_regression(tmp_path):
    output = _write_fixture(
        tmp_path,
        e0_values=("0", "20", "37"),
        taskset_ucs=("1/10",),
        vectors={
            ("ts-0", "0", "CW"): [5],
            ("ts-0", "20", "CW"): [7],
            ("ts-0", "37", "CW"): [4],
        },
    )
    assert analyzer.main(["--input", str(output)]) == 2
    assert _report(output)["e0_monotonicity_violation_count"] == 1


def test_fixed_scale_analyzer_rejects_missing_request_id(tmp_path):
    output = _write_fixture(tmp_path)
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    del rows[0]["request_id"]
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    assert analyzer.main(["--input", str(output)]) == 2
    assert _report(output)["invalid_result_count"] > 0


def test_fixed_scale_analyzer_fails_closed_for_non_fixed_scale_config(tmp_path):
    output = _write_fixture(tmp_path, energy_mode="load_cross")
    assert analyzer.main(["--input", str(output)]) == 2
    assert not (output / "summary.csv").exists()
