from fractions import Fraction
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_rta_load_cross as analyzer
import run_rta_load_cross as runner

import asap_block_rta_v9_3_methods as methods
from experiments.v9_3 import rta_load_cross as cross


def test_frozen_cells_and_static_counts():
    assert len(cross.frozen_cells()) == 42
    assert cross.static_counts() == {
        "cells": 42, "skeletons": 4000, "scaled_tasksets": 21000, "requests": 168000,
    }


def test_cross_scan_deduplicates_nine_shared_cells():
    first = {(uc, ue) for uc in cross.FROZEN_UC for ue in cross.FROZEN_UE_FIRST}
    second = {(uc, ue) for uc in cross.FROZEN_UC_SECOND for ue in cross.FROZEN_UE_SECOND}
    assert len(first & second) == 9
    assert len(cross.frozen_cells()) == len(first | second)


def test_exact_energy_and_scaling_and_pairing(tmp_path):
    config = tmp_path / "system.yml"
    config.write_text("""
energy_management:
  scheduler_energy_model:
    base_power: 0.5
    frequency_power_ratios: {8100: 0.93}
    workload_coefficients:
      bzip2: 1.2
      control: 0.1
      decrypt: 1.5
      encrypt: 1.5
      hash: 0.8
""", encoding="utf-8")
    energies = cross._load_exact_energy_model(config)
    assert energies == {
        "bzip2": Fraction(279, 500), "control": Fraction(93, 2000),
        "decrypt": Fraction(279, 400), "encrypt": Fraction(279, 400),
        "hash": Fraction(93, 250),
    }
    skeleton = (
        {"name": "task_0", "priority": 0, "C": 2, "D": 4, "T": 5, "workload": "bzip2"},
        {"name": "task_1", "priority": 1, "C": 1, "D": 3, "T": 7, "workload": "hash"},
    )
    low = cross.scale_skeleton(skeleton, target_uc=Fraction(1, 10), target_ue=Fraction(1, 2), generation_index=0, seed=1, processors=4, rho=Fraction(11, 2), base_energies=energies)
    high = cross.scale_skeleton(skeleton, target_uc=Fraction(1, 10), target_ue=Fraction(4, 5), generation_index=0, seed=1, processors=4, rho=Fraction(11, 2), base_energies=energies)
    assert low["actual_ue"] == "1/2"
    assert high["actual_ue"] == "4/5"
    assert [(row["C"], row["D"], row["T"], row["workload"], row["priority"]) for row in low["tasks"]] == [(row["C"], row["D"], row["T"], row["workload"], row["priority"]) for row in high["tasks"]]


def test_stable_ids_seeds_and_method_catalog():
    seed = cross.stable_seed(20260814, 4, 10, Fraction(3, 10), 7)
    assert seed == cross.stable_seed(20260814, 4, 10, Fraction(3, 10), 7)
    assert seed != cross.stable_seed(20260814, 4, 10, Fraction(3, 10), 8)
    assert cross.request_id("uc0.3-i0000-ue0.8", Fraction(37), "SEQ") == "uc0.3-i0000-ue0.8-e0-37-SEQ"
    assert [methods.method_spec_v9_3(cross.METHOD_DISPLAY_TO_ID[name]).display_name for name in ("CW", "LOC", "PH", "SEQ")] == ["CW", "LOC", "PH", "SEQ"]


def test_core3_export_is_slice_of_existing_tasksets(tmp_path):
    rows = []
    for uc in cross.FROZEN_UC:
        for index in range(2):
            rows.append({"taskset_id": cross.taskset_id(uc, index, Fraction(4, 5)), "target_uc": cross.fraction_text(uc), "target_ue": "4/5", "generation_index": index, "tasks": []})
    path = tmp_path / "core3.jsonl"
    assert cross.export_core3_tasksets(rows, path) == 16
    assert len(path.read_text(encoding="utf-8").splitlines()) == 16


def _semantic_config(**overrides):
    values = {
        "seed": 20260814,
        "cells": ((Fraction(1, 10), Fraction(1, 2)),),
        "rho": Fraction(11, 2),
        "latency": Fraction(2, 5),
        "processors": 4,
        "tasks": 10,
        "period_min": 40,
        "period_max": 200,
        "min_util": Fraction(1, 100),
        "max_util": Fraction(4, 5),
        "tolerance": Fraction(1, 100),
        "samples_per_uc": 1,
        "e0_values": [Fraction(0), Fraction(37)],
        "method_names": ["CW", "LOC", "PH", "SEQ"],
        "system_config": Path("system_config_unified_template.yml").resolve(),
        "workers": 1,
        "timeout_first": 600.0,
        "timeout_retry": 1200.0,
    }
    values.update(overrides)
    return runner._canonical_semantic_config(**values)


def _resume_fixture(tmp_path):
    output = tmp_path / "resume"
    output.mkdir()
    semantic = _semantic_config()
    (output / "run_config.json").write_text(
        json.dumps({"semantic_config": semantic}, indent=2), encoding="utf-8"
    )
    taskset = {
        "taskset_id": "uc0.1-i0000-ue0.5", "target_uc": "1/10",
        "actual_uc": "1/10", "target_ue": "1/2", "actual_ue": "1/2",
        "generation_index": 0, "seed": 1,
        "tasks": [{
            "name": "task_0", "priority": 0, "C": 1, "D": 1, "T": 2,
            "workload": "hash", "base_energy_per_tick": "93/250",
            "energy_per_tick": "11/2",
        }],
    }
    (output / "tasksets.jsonl").write_text(json.dumps(taskset) + "\n", encoding="utf-8")
    complete_results = [
        {"request_id": f"uc0.1-i0000-ue0.5-e0-{e0}-{method}"}
        for e0 in ("0", "37")
        for method in ("CW", "LOC", "PH", "SEQ")
    ]
    (output / "results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in complete_results) + "\n",
        encoding="utf-8",
    )
    return output


def _resume_args(output, *extra):
    return [
        "--output", str(output), "--seed", "20260814", "--workers", "1",
        "--samples-per-uc", "1", "--processors", "4", "--tasks", "10",
        "--period-min", "40", "--period-max", "200", "--min-task-util", "0.01",
        "--max-task-util", "0.8", "--util-tolerance-total", "0.01",
        "--e0-values", "0,37", "--methods", "CW,LOC,PH,SEQ", "--rho", "11/2",
        "--latency", "2/5", "--timeout-first", "600", "--timeout-retry", "1200",
        "--system-config", "system_config_unified_template.yml", "--cells", "0.1:0.5",
        "--resume", *extra,
    ]


def test_resume_same_semantics_and_equivalent_fraction_spellings(tmp_path):
    output = _resume_fixture(tmp_path)
    assert runner.main(_resume_args(output)) == 0
    assert runner.main(_resume_args(
        output, "--rho", "5.5", "--latency", "0.4",
        "--cells", "1/10:1/2", "--e0-values", "37,0",
        "--methods", "SEQ,PH,LOC,CW",
    )) == 0


@pytest.mark.parametrize("extra", [
    ("--rho", "6"),
    ("--latency", "1"),
    ("--cells", "0.2:0.5"),
    ("--samples-per-uc", "2"),
    ("--processors", "2"),
    ("--e0-values", "0,1"),
    ("--methods", "CW,LOC,PH"),
])
def test_resume_semantic_mismatch_fails_closed_without_overwriting(tmp_path, extra):
    output = _resume_fixture(tmp_path)
    before = (output / "run_config.json").read_text(encoding="utf-8")
    assert runner.main(_resume_args(output, *extra)) == 2
    assert (output / "run_config.json").read_text(encoding="utf-8") == before


def _analysis_fixture(tmp_path, methods_list=("CW", "LOC", "PH", "SEQ"), statuses=None):
    output = tmp_path / "analysis"
    output.mkdir(parents=True)
    semantic = _semantic_config(method_names=list(methods_list))
    (output / "run_config.json").write_text(json.dumps({"semantic_config": semantic}), encoding="utf-8")
    taskset = {"taskset_id": "t0", "target_uc": "1/10", "target_ue": "1/2", "actual_uc": "1/10", "actual_ue": "1/2", "tasks": []}
    (output / "tasksets.jsonl").write_text(json.dumps(taskset) + "\n", encoding="utf-8")
    rows = []
    for e0 in ("0", "37"):
        for method in methods_list:
            status = statuses.get((e0, method), "PROVEN") if statuses else "PROVEN"
            rows.append({
                "request_id": f"t0-e0-{e0}-{method}", "taskset_id": "t0",
                "target_uc": "1/10", "actual_uc": "1/10", "target_ue": "1/2",
                "actual_ue": "1/2", "e0": e0, "method": method,
                "final_status": status, "response_time_vector": [1],
            })
    (output / "results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return output


def test_analyzer_detects_missing_method_missing_request_and_duplicate(tmp_path):
    missing_method = _analysis_fixture(tmp_path / "method")
    lines = [row for row in (missing_method / "results.jsonl").read_text().splitlines() if '"method": "SEQ"' not in row]
    (missing_method / "results.jsonl").write_text("\n".join(lines) + "\n")
    assert analyzer.main(["--input", str(missing_method)]) == 2
    assert json.loads((missing_method / "invariant_report.json").read_text())["missing_method_or_results_count"] > 0

    missing_request = _analysis_fixture(tmp_path / "request")
    lines = [row for row in (missing_request / "results.jsonl").read_text().splitlines() if not ('"e0": "37"' in row and '"method": "PH"' in row)]
    (missing_request / "results.jsonl").write_text("\n".join(lines) + "\n")
    assert analyzer.main(["--input", str(missing_request)]) == 2

    duplicate = _analysis_fixture(tmp_path / "duplicate")
    content = duplicate.joinpath("results.jsonl").read_text()
    duplicate.joinpath("results.jsonl").write_text(content + content.splitlines()[0] + "\n")
    assert analyzer.main(["--input", str(duplicate)]) == 2


@pytest.mark.parametrize("low,high,violations,inconclusive", [
    ("PROVEN", "PROVEN", 0, 0),
    ("NOT_PROVEN", "PROVEN", 0, 0),
    ("PROVEN", "NOT_PROVEN", 1, 0),
    ("PROVEN", "UNPROVEN_TIMEOUT", 0, 1),
    ("PROVEN", "NUMERIC_ERROR", 0, 1),
])
def test_analyzer_e0_monotonicity_classifies_only_comparable_states(tmp_path, low, high, violations, inconclusive):
    output = _analysis_fixture(tmp_path, methods_list=("CW",), statuses={
        ("0", "CW"): low, ("37", "CW"): high,
    })
    assert analyzer.main(["--input", str(output)]) == (2 if violations else 0)
    report = json.loads((output / "invariant_report.json").read_text())
    assert report["e0_monotonicity_violation_count"] == violations
    assert report["inconclusive_monotonicity_pairs"] == inconclusive
