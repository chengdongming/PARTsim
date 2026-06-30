import csv
from pathlib import Path

import pandas as pd
import pytest

from scripts import analyze_rta_v21_comparison as analyzer
from scripts import run_rta_v21_comparison as runner


def report(version, proven=True, bound=10):
    return {
        "version": version,
        "status": "proven_under_assumptions" if proven else "rta_unproven",
        "proven": proven,
        "error": "",
        "reason": "" if proven else "not closed",
        "bound": bound if proven else None,
        "report": {
            "rta_version": version,
            "proven_under_assumptions": proven,
            "tasks": [
                {
                    "task_name": "t1",
                    "proven_under_assumptions": proven,
                    "response_time_bound": bound if proven else None,
                }
            ],
        },
    }


def base_row():
    return {
        "experiment_name": "v21-test",
        "seed_base": 424242,
        "taskset_seed": 425242,
        "normalized_utilization": 0.1,
        "task_idx": 0,
        "taskset_id": "u0.10-000",
        "taskset_path": "/tmp/tasks.yml",
    }


def simulation(accepted=True, response=5):
    return {
        "accepted": accepted,
        "simulation_status": "accepted" if accepted else "rejected",
        "simulated_response_time": response,
        "deadline_miss_time": "" if accepted else 8,
        "first_missed_task": "" if accepted else "t1",
        "max_response_by_task": {"t1": response},
        "trace_path": "",
    }


def test_comparison_row_contains_both_versions_and_valid_tightness():
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12),
        report(runner.V21_VERSION, bound=10),
        0.25,
    )
    assert set(runner.RESULT_FIELDS) == set(row)
    assert row["v20p4_rta_version"] == "v20.4"
    assert row["v21_rta_version"] == "v21-local-window"
    assert row["v20p4_tightness"] == pytest.approx(2.4)
    assert row["v21_tightness"] == pytest.approx(2.0)
    assert row["v21_minus_v20p4_bound"] == -2
    assert row["v21_bound_lt_v20p4"] == 1


def test_tightness_is_empty_for_unproven_analysis():
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=False),
        1.0,
    )
    assert row["v20p4_tightness"] == ""
    assert row["v21_tightness"] == ""
    assert row["both_unproven"] == 1


def test_soundness_rejects_proven_but_simulation_rejected():
    with pytest.raises(RuntimeError, match="soundness violation"):
        runner._comparison_row(
            base_row(), simulation(accepted=False),
            report(runner.V20_VERSION, proven=False),
            report(runner.V21_VERSION, proven=True),
            0.25,
        )


def test_soundness_rejects_observed_response_above_v21_bound():
    with pytest.raises(RuntimeError, match="soundness violation"):
        runner._comparison_row(
            base_row(), simulation(response=11),
            report(runner.V20_VERSION, proven=False),
            report(runner.V21_VERSION, proven=True, bound=10),
            0.25,
        )


def _write_result(path: Path, **updates):
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12),
        report(runner.V21_VERSION, bound=10),
        0.25,
    )
    row.update(updates)
    pd.DataFrame([row], columns=runner.RESULT_FIELDS).to_csv(path, index=False)


def test_analyzer_writes_summary_and_figures(tmp_path):
    source = tmp_path / "comparison.csv"
    output = tmp_path / "analysis_outputs_v21"
    _write_result(source)
    summary, by_util = analyzer.analyze(source, output)
    assert summary.iloc[0]["v21_bound_lt_v20p4_count"] == 1
    assert by_util.iloc[0]["v21_proven_count"] == 1
    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_UTIL_FILENAME,
        "rta_v21_bound_delta.png",
        "rta_v21_tightness_comparison.png",
        "rta_v21_proven_ratio.png",
    ):
        assert (output / filename).is_file()


def test_analyzer_accepts_comparison_manifest(tmp_path):
    run_dir = tmp_path / "v21-run"
    run_dir.mkdir()
    source = run_dir / runner.RESULT_FILENAME
    _write_result(source)
    manifest = tmp_path / "manifest.csv"
    pd.DataFrame([{"results_file": str(source)}]).to_csv(
        manifest, index=False
    )
    loaded = analyzer.load_results(manifest)
    assert len(loaded) == 1
    assert loaded.iloc[0]["v21_rta_version"] == "v21-local-window"


@pytest.mark.parametrize(
    "field",
    [
        "v21_soundness_proven_but_rejected",
        "v21_soundness_observed_exceeds_bound",
    ],
)
def test_analyzer_raises_on_v21_soundness_violation(tmp_path, field):
    source = tmp_path / "comparison.csv"
    _write_result(source, **{field: 1})
    with pytest.raises(ValueError, match="soundness violation"):
        analyzer.analyze(source, tmp_path / "out")


@pytest.mark.parametrize(
    "updates",
    [
        {"accepted": 0, "simulation_status": "rejected"},
        {"simulated_response_time": 11, "v21_bound": 10},
    ],
)
def test_analyzer_recomputes_v21_soundness_from_raw_values(tmp_path, updates):
    source = tmp_path / "comparison.csv"
    _write_result(source, **updates)
    with pytest.raises(ValueError, match="soundness violation"):
        analyzer.load_results(source)


def test_analyzer_refuses_frozen_v20p4_paths(tmp_path):
    frozen = tmp_path / "rta-e0-sensitivity-v20p4-formal"
    frozen.mkdir()
    source = frozen / "comparison.csv"
    _write_result(source)
    with pytest.raises(ValueError, match="refuses frozen"):
        analyzer.load_results(source)


def test_runner_default_root_is_v21_specific():
    parser = runner.build_parser()
    args = parser.parse_args(
        ["--experiment-name", "v21-smoke", "--e0-values", "0.25"]
    )
    assert args.output_root == "acceptance_ratio_runs_v21"


def test_runner_dry_run_creates_only_v21_plan_files(tmp_path):
    args = runner.build_parser().parse_args([
        "--output-root", str(tmp_path),
        "--experiment-name", "v21-dry-run",
        "--e0-values", "0.25", "1.0",
        "--num-tasksets", "1",
        "--dry-run",
    ])
    runner._validate_args(runner.build_parser(), args)
    results = runner.run(args)
    assert results == tmp_path / "v21-dry-run" / runner.RESULT_FILENAME
    with results.open(newline="", encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == runner.RESULT_FIELDS
    manifest = results.parent / runner.MANIFEST_FILENAME
    assert manifest.is_file()
    assert "rta-e0-sensitivity-v20p4" not in str(results)
