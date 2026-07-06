from pathlib import Path

import pandas as pd
import pytest

from scripts import analyze_rta_scalability as analyzer


INPUT_COLUMNS = [
    "config_id",
    "taskset_id",
    "task_n",
    "M",
    "utilization",
    "rta_status",
    "rta_error",
    "rta_proven",
    "rta_schedulable",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_timed_out",
    "rta_profile_task_time_sum_sec",
]


def row(**updates):
    base = {
        "config_id": "cfg-a",
        "taskset_id": "ts-0",
        "task_n": 4,
        "M": 2,
        "utilization": 0.2,
        "rta_status": "proven_under_assumptions",
        "rta_error": "",
        "rta_proven": True,
        "rta_schedulable": True,
        "rta_attempted": True,
        "rta_runtime_sec": 1.0,
        "rta_timed_out": False,
        "rta_profile_task_time_sum_sec": "",
    }
    base.update(updates)
    return base


def write_rows(path, rows):
    pd.DataFrame(rows, columns=INPUT_COLUMNS).to_csv(path, index=False)


def output_files():
    return (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_N_FILENAME,
        analyzer.BY_M_FILENAME,
        analyzer.BY_UTILIZATION_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
        "plots/runtime_vs_n.png",
        "plots/runtime_vs_m.png",
        "plots/runtime_vs_utilization.png",
        "plots/timeout_rate.png",
    )


def test_empty_input_writes_all_csvs_and_no_data_plots(tmp_path):
    source = tmp_path / "empty.csv"
    output = tmp_path / "analysis"
    write_rows(source, [])

    overall, by_n, by_m, by_utilization, by_config = analyzer.analyze(
        source, output
    )

    assert overall.iloc[0]["total_rows"] == 0
    assert by_n.empty
    assert by_m.empty
    assert by_utilization.empty
    assert by_config.empty
    for relative in output_files():
        assert (output / relative).is_file()
    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_N_FILENAME,
        analyzer.BY_M_FILENAME,
        analyzer.BY_UTILIZATION_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
    ):
        saved = pd.read_csv(output / filename)
        assert list(saved.columns) == analyzer.SUMMARY_COLUMNS


def test_counts_rates_groups_profile_and_plots(tmp_path):
    source = tmp_path / "results.csv"
    output = tmp_path / "analysis"
    write_rows(source, [
        row(
            taskset_id="proven",
            rta_runtime_sec=1.0,
            rta_profile_task_time_sum_sec=0.1,
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
            config_id="cfg-b",
            taskset_id="timeout",
            task_n=8,
            M=4,
            utilization=0.4,
            rta_status="rta_error",
            rta_error="RTA timed out",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=30.0,
            rta_timed_out=True,
        ),
        row(
            config_id="cfg-b",
            taskset_id="error",
            task_n=8,
            M=4,
            utilization=0.4,
            rta_status="rta_error",
            rta_error="invalid JSON",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=3.0,
        ),
        row(
            config_id="cfg-c",
            taskset_id="unattempted",
            task_n=16,
            M=4,
            utilization=0.6,
            rta_status="rta_error",
            rta_error="task generation failed",
            rta_proven=False,
            rta_schedulable=False,
            rta_attempted=False,
            rta_runtime_sec="",
        ),
    ])

    overall, by_n, by_m, by_utilization, by_config = analyzer.analyze(
        source, output
    )
    summary = overall.iloc[0]
    assert summary["total_rows"] == 5
    assert summary["rta_attempted_count"] == 4
    assert summary["rta_completed_count"] == 2
    assert summary["rta_timeout_count"] == 1
    assert summary["rta_error_count"] == 1
    assert summary["rta_unattempted_count"] == 1
    assert summary["rta_proven_count"] == 1
    assert summary["rta_unproven_count"] == 1
    assert summary["rta_proven_ratio"] == pytest.approx(0.5)
    assert summary["rta_timeout_rate"] == pytest.approx(0.25)
    assert summary["rta_error_rate"] == pytest.approx(0.25)
    assert summary["runtime_sample_count"] == 2
    assert summary["runtime_mean_sec"] == pytest.approx(1.5)
    assert summary["runtime_max_sec"] == pytest.approx(2.0)
    assert summary["profile_runtime_sample_count"] == 2
    assert summary["profile_runtime_mean_sec"] == pytest.approx(0.15)
    assert summary["profile_runtime_median_sec"] == pytest.approx(0.15)
    assert summary["profile_runtime_p95_sec"] == pytest.approx(0.195)
    assert summary["profile_runtime_max_sec"] == pytest.approx(0.2)

    assert set(by_n["group_key"]) == {"task_n"}
    assert set(by_n["group_value"]) == {4, 8, 16}
    assert set(by_m["group_key"]) == {"M"}
    assert set(by_m["group_value"]) == {2, 4}
    assert set(by_utilization["group_key"]) == {"utilization"}
    assert set(by_utilization["group_value"]) == {0.2, 0.4, 0.6}
    assert set(by_config["group_key"]) == {"config_id"}
    assert set(by_config["group_value"]) == {"cfg-a", "cfg-b", "cfg-c"}
    for relative in output_files():
        assert (output / relative).is_file()


def test_by_utilization_keeps_normalized_and_total_modes_separate(tmp_path):
    source = tmp_path / "mixed-modes.csv"
    output = tmp_path / "analysis"
    rows = [
        row(taskset_id="norm", utilization=0.5, rta_runtime_sec=1.0),
        row(taskset_id="total", utilization=0.5, rta_runtime_sec=2.0),
    ]
    frame = pd.DataFrame(rows)
    frame.loc[0, "utilization_mode"] = "normalized"
    frame.loc[1, "utilization_mode"] = "total"
    frame.to_csv(source, index=False)

    _overall, _by_n, _by_m, by_utilization, _by_config = analyzer.analyze(
        source, output
    )

    assert len(by_utilization) == 2
    assert set(by_utilization["group_value"]) == {0.5}
    assert set(by_utilization["utilization_mode"]) == {"normalized", "total"}


def test_runtime_quantiles_ignore_blank_nan_inf_negative_and_errors(tmp_path):
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
            (-1, -1),
        ])
    ]
    error_row = row(
        taskset_id="error",
        rta_status="rta_error",
        rta_error="invalid JSON",
        rta_proven=False,
        rta_schedulable=False,
        rta_runtime_sec=100,
        rta_profile_task_time_sum_sec="",
    )
    write_rows(source, valid_rows + invalid_rows + [error_row])

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


def test_strict_rejects_missing_input_or_schema_but_accepts_failures(tmp_path):
    assert analyzer.main([
        "--input", str(tmp_path / "missing.csv"),
        "--output-dir", str(tmp_path / "missing-output"),
        "--strict",
    ]) != 0

    malformed = tmp_path / "malformed.csv"
    pd.DataFrame([{"task_n": 4}]).to_csv(malformed, index=False)
    assert analyzer.main([
        "--input", str(malformed),
        "--output-dir", str(tmp_path / "malformed-output"),
        "--strict",
    ]) != 0

    failures = tmp_path / "failures.csv"
    write_rows(failures, [
        row(
            taskset_id="timeout",
            rta_status="rta_error",
            rta_error="timeout",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=30,
            rta_timed_out=True,
        ),
        row(
            taskset_id="error",
            rta_status="rta_error",
            rta_error="invalid JSON",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=1,
        ),
    ])
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("status\ncompleted\n", encoding="utf-8")
    output = tmp_path / "failure-output"
    assert analyzer.main([
        "--input", str(failures),
        "--manifest", str(manifest),
        "--output-dir", str(output),
        "--strict",
    ]) == 0
    assert (output / analyzer.SUMMARY_FILENAME).is_file()


def test_analyzer_source_does_not_call_rta_or_simulation():
    source = Path(analyzer.__file__).read_text(encoding="utf-8")
    assert "run_asap_block_rta" not in source
    assert "run_single_simulation_worker" not in source
    assert "asap_block_rta_" + "v21" not in source
