from pathlib import Path

import pandas as pd
import pytest

from scripts import analyze_rta_parameter_sensitivity as analyzer
from scripts import experiment_runner


INPUT_COLUMNS = [
    "sweep_name",
    "sweep_parameter",
    "sweep_value",
    "config_id",
    "taskset_id",
    "rta_status",
    "rta_error",
    "result_status",
    "rta_proven",
    "rta_schedulable",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_timed_out",
    "rta_profile_task_time_sum_sec",
]


def row(**updates):
    base = {
        "sweep_name": "utilization",
        "sweep_parameter": "utilization",
        "sweep_value": 0.2,
        "config_id": "cfg-util",
        "taskset_id": "ts-0",
        "rta_status": "proven_under_assumptions",
        "rta_error": "",
        "result_status": "completed",
        "rta_proven": True,
        "rta_schedulable": True,
        "rta_attempted": True,
        "rta_runtime_sec": 1.0,
        "rta_timed_out": False,
        "rta_profile_task_time_sum_sec": "",
    }
    base.update(updates)
    return base


def write_rows(
    path,
    rows,
    include_assumption=False,
    include_forbidden=False,
):
    columns = list(INPUT_COLUMNS)
    if include_assumption:
        columns.append("assumption_eligible")
    if include_forbidden:
        columns.extend([
            "acceptance_ratio",
            "pessimism",
            "observed_max_response_time",
        ])
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def output_files():
    return (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_SWEEP_FILENAME,
        analyzer.BY_VALUE_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
        "plots/proven_ratio_vs_utilization.png",
        "plots/proven_ratio_vs_e0.png",
        "plots/proven_yield_vs_parameter.png",
        "plots/runtime_vs_utilization.png",
        "plots/runtime_vs_e0.png",
        "plots/timeout_rate_vs_parameter.png",
    )


def test_empty_input_writes_all_csvs_and_no_data_plots(tmp_path):
    source = tmp_path / "empty.csv"
    output = tmp_path / "analysis"
    write_rows(source, [])

    overall, by_sweep, by_value, by_config = analyzer.analyze(
        source, output
    )

    assert overall.iloc[0]["group_key"] == "overall"
    assert overall.iloc[0]["group_value"] == "all"
    assert overall.iloc[0]["total_rows"] == 0
    assert by_sweep.empty
    assert by_value.empty
    assert by_config.empty
    for relative in output_files():
        assert (output / relative).is_file()
    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_SWEEP_FILENAME,
        analyzer.BY_VALUE_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
    ):
        saved = pd.read_csv(output / filename)
        assert list(saved.columns) == analyzer.SUMMARY_COLUMNS


def test_counts_rates_groups_profile_plots_and_forbidden_metrics(tmp_path):
    source = tmp_path / "results.csv"
    output = tmp_path / "analysis"
    rows = [
        row(
            taskset_id="proven",
            rta_runtime_sec=1.0,
            rta_profile_task_time_sum_sec=0.1,
            acceptance_ratio=1.0,
            pessimism=99,
            observed_max_response_time=10,
        ),
        row(
            taskset_id="unproven",
            rta_status="rta_unproven",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=2.0,
            rta_profile_task_time_sum_sec=0.2,
        ),
        row(
            sweep_name="e0",
            sweep_parameter="e0",
            sweep_value=0.2,
            config_id="cfg-e0",
            taskset_id="timeout",
            rta_status="rta_error",
            rta_error="RTA timed out",
            result_status="rta_timeout",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=30.0,
            rta_timed_out=True,
        ),
        row(
            sweep_name="e0",
            sweep_parameter="e0",
            sweep_value=0.2,
            config_id="cfg-e0",
            taskset_id="error",
            rta_status="rta_unproven",
            rta_error="",
            result_status="runner_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=3.0,
        ),
        row(
            sweep_name="e0",
            sweep_parameter="e0",
            sweep_value=0.2,
            config_id="cfg-e0",
            taskset_id="unattempted",
            rta_status="rta_error",
            rta_error="task generation failed",
            result_status="task_generation_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_attempted=False,
            rta_runtime_sec="",
        ),
    ]
    write_rows(source, rows, include_forbidden=True)

    overall, by_sweep, by_value, by_config = analyzer.analyze(
        source, output
    )
    summary = overall.iloc[0]
    assert summary["total_rows"] == 5
    assert summary["attempted_count"] == 4
    assert summary["completed_count"] == 2
    assert summary["timeout_count"] == 1
    assert summary["error_count"] == 1
    assert summary["unattempted_count"] == 1
    assert summary["proven_count"] == 1
    assert summary["unproven_count"] == 1
    assert summary["rta_proven_ratio"] == pytest.approx(0.5)
    assert summary["rta_proven_yield"] == pytest.approx(0.25)
    assert summary["timeout_rate"] == pytest.approx(0.25)
    assert summary["error_rate"] == pytest.approx(0.25)
    assert summary["runtime_sample_count"] == 2
    assert summary["runtime_mean_sec"] == pytest.approx(1.5)
    assert summary["runtime_median_sec"] == pytest.approx(1.5)
    assert summary["profile_runtime_sample_count"] == 2
    assert summary["profile_runtime_mean_sec"] == pytest.approx(0.15)
    assert summary["profile_runtime_median_sec"] == pytest.approx(0.15)
    assert summary["profile_runtime_p95_sec"] == pytest.approx(0.195)
    assert summary["profile_runtime_max_sec"] == pytest.approx(0.2)
    assert pd.isna(summary["conditional_denominator_count"])
    assert pd.isna(summary["conditional_proven_ratio"])

    assert set(by_sweep["group_key"]) == {"sweep_parameter"}
    assert set(by_sweep["group_value"]) == {"utilization", "e0"}
    assert set(by_value["group_key"]) == {"sweep_value"}
    assert len(by_value) == 2
    assert set(zip(
        by_value["sweep_parameter"],
        by_value["group_value"],
    )) == {("utilization", 0.2), ("e0", 0.2)}
    assert set(by_config["group_key"]) == {"config_id"}
    assert set(by_config["group_value"]) == {"cfg-util", "cfg-e0"}

    forbidden = {
        "acceptance_ratio",
        "pessimism",
        "observed_max_response_time",
    }
    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_SWEEP_FILENAME,
        analyzer.BY_VALUE_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
    ):
        saved = pd.read_csv(output / filename)
        assert forbidden.isdisjoint(saved.columns)
    for relative in output_files():
        assert (output / relative).is_file()


def test_runtime_quantiles_ignore_nonfinite_negative_and_failures(tmp_path):
    source = tmp_path / "quantiles.csv"
    valid_rows = [
        row(
            taskset_id="valid-{}".format(index),
            rta_runtime_sec=float(index),
            rta_profile_task_time_sum_sec=index / 10.0,
        )
        for index in range(1, 6)
    ]
    invalid_rows = [
        row(
            taskset_id="invalid-{}".format(index),
            rta_status="rta_unproven",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=runtime,
            rta_profile_task_time_sum_sec=profile,
        )
        for index, (runtime, profile) in enumerate([
            ("", ""),
            ("nan", "nan"),
            ("inf", "inf"),
            ("-inf", "-inf"),
            (-1, -1),
        ])
    ]
    failure_rows = [
        row(
            taskset_id="timeout",
            rta_status="rta_error",
            rta_error="timeout",
            result_status="rta_timeout",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=100,
            rta_timed_out=True,
        ),
        row(
            taskset_id="error",
            rta_status="rta_error",
            rta_error="invalid JSON",
            result_status="rta_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=200,
        ),
        row(
            taskset_id="unattempted",
            rta_status="rta_error",
            rta_error="not run",
            result_status="config_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_attempted=False,
            rta_runtime_sec=300,
        ),
    ]
    write_rows(source, valid_rows + invalid_rows + failure_rows)

    frame = analyzer.load_results(source)
    summary = analyzer.build_overall(frame, source).iloc[0]

    assert summary["runtime_sample_count"] == 5
    assert summary["runtime_mean_sec"] == pytest.approx(3.0)
    assert summary["runtime_median_sec"] == pytest.approx(3.0)
    assert summary["runtime_p75_sec"] == pytest.approx(4.0)
    assert summary["runtime_p90_sec"] == pytest.approx(4.6)
    assert summary["runtime_p95_sec"] == pytest.approx(4.8)
    assert summary["runtime_max_sec"] == pytest.approx(5.0)
    assert summary["profile_runtime_sample_count"] == 5
    assert summary["profile_runtime_mean_sec"] == pytest.approx(0.3)
    assert summary["profile_runtime_median_sec"] == pytest.approx(0.3)
    assert summary["profile_runtime_p95_sec"] == pytest.approx(0.48)
    assert summary["profile_runtime_max_sec"] == pytest.approx(0.5)


def test_conditional_ratio_uses_only_eligible_completed_rows(tmp_path):
    source = tmp_path / "conditional.csv"
    write_rows(source, [
        row(taskset_id="eligible-proven", assumption_eligible=True),
        row(
            taskset_id="eligible-unproven",
            assumption_eligible=True,
            rta_status="rta_unproven",
            rta_proven=False,
            rta_schedulable=False,
        ),
        row(taskset_id="ineligible-proven", assumption_eligible=False),
        row(
            taskset_id="eligible-timeout",
            assumption_eligible=True,
            rta_status="rta_error",
            rta_error="timeout",
            result_status="rta_timeout",
            rta_proven=False,
            rta_schedulable=False,
            rta_timed_out=True,
        ),
        row(
            taskset_id="eligible-error",
            assumption_eligible=True,
            rta_status="rta_error",
            rta_error="invalid JSON",
            result_status="rta_error",
            rta_proven=False,
            rta_schedulable=False,
        ),
    ], include_assumption=True)

    summary = analyzer.build_overall(
        analyzer.load_results(source), source
    ).iloc[0]
    assert summary["completed_count"] == 3
    assert summary["proven_count"] == 2
    assert summary["conditional_denominator_count"] == 2
    assert summary["conditional_proven_ratio"] == pytest.approx(0.5)


def test_strict_validates_input_schema_but_accepts_empty_and_failures(
    tmp_path,
):
    assert analyzer.main([
        "--input", str(tmp_path / "missing.csv"),
        "--output-dir", str(tmp_path / "missing-output"),
        "--strict",
    ]) != 0

    malformed = tmp_path / "malformed.csv"
    pd.DataFrame([{"sweep_parameter": "utilization"}]).to_csv(
        malformed, index=False
    )
    assert analyzer.main([
        "--input", str(malformed),
        "--output-dir", str(tmp_path / "malformed-output"),
        "--strict",
    ]) != 0

    empty = tmp_path / "empty.csv"
    write_rows(empty, [])
    experiment_runner.write_primary_analysis_artifact_attestation(empty)
    assert analyzer.main([
        "--input", str(empty),
        "--output-dir", str(tmp_path / "empty-output"),
        "--strict",
    ]) == 0

    failures = tmp_path / "failures.csv"
    write_rows(failures, [
        row(
            taskset_id="timeout",
            rta_status="rta_error",
            rta_error="timeout",
            result_status="rta_timeout",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=30,
            rta_timed_out=True,
        ),
        row(
            taskset_id="error",
            rta_status="rta_error",
            rta_error="invalid JSON",
            result_status="rta_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=1,
        ),
    ])
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("status\ncompleted\n", encoding="utf-8")
    experiment_runner.write_primary_analysis_artifact_attestation(
        failures, companion_paths=[manifest]
    )
    assert analyzer.main([
        "--input", str(failures),
        "--manifest", str(manifest),
        "--output-dir", str(tmp_path / "failure-output"),
        "--strict",
    ]) == 0


def test_analyzer_source_does_not_call_rta_runner_or_simulation():
    source = Path(analyzer.__file__).read_text(encoding="utf-8")
    assert "run_" + "asap_block_rta" not in source
    assert "run_single_" + "simulation_worker" not in source
    assert "global_" + "task_generator" not in source
    assert "run_rta_" + "parameter_sensitivity" not in source
    assert "asap_block_rta_" + "v21" not in source
