from pathlib import Path

import pandas as pd
import pytest

from scripts import analyze_rta_ablation as analyzer


INPUT_COLUMNS = [
    "experiment_name",
    "config_id",
    "taskset_id",
    "taskset_family_id",
    "seed",
    "task_n",
    "M",
    "normalized_utilization",
    "total_utilization",
    "rta_initial_energy",
    "variant_name",
    "variant_label",
    "variant_safety_label",
    "variant_is_default",
    "variant_is_experimental",
    "proof_claim_eligible",
    "diagnostic_only",
    "rta_version",
    "rta_tool",
    "expected_rta_version",
    "rta_status",
    "rta_error",
    "result_status",
    "rta_proven",
    "rta_schedulable",
    "rta_response_time_bound",
    "rta_response_bound",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_runtime_source",
    "rta_timed_out",
    "rta_timeout_sec",
    "rta_profile_enabled",
    "rta_profile_task_time_sum_sec",
    "rta_profile_task_count",
]

FORBIDDEN = {
    "acceptance_ratio",
    "pessimism",
    "observed_max_response_time",
}


def row(**updates):
    base = {
        "experiment_name": "e3-test",
        "config_id": "cfg-u02",
        "taskset_id": "ts-0",
        "taskset_family_id": "fam-0",
        "seed": 123,
        "task_n": 2,
        "M": 2,
        "normalized_utilization": 0.2,
        "total_utilization": 0.4,
        "rta_initial_energy": 0.0,
        "variant_name": "v20p4_full",
        "variant_label": "v20.4 full RTA",
        "variant_safety_label": "safe_under_v20p4_assumptions",
        "variant_is_default": True,
        "variant_is_experimental": False,
        "proof_claim_eligible": True,
        "diagnostic_only": False,
        "rta_version": "v20.4",
        "rta_tool": "asap_block_rta.py",
        "expected_rta_version": "v20.4",
        "rta_status": "proven_under_assumptions",
        "rta_error": "",
        "result_status": "completed",
        "rta_proven": True,
        "rta_schedulable": True,
        "rta_response_time_bound": 10.0,
        "rta_response_bound": 10.0,
        "rta_attempted": True,
        "rta_runtime_sec": 1.0,
        "rta_runtime_source": "subprocess_wall_clock_perf_counter",
        "rta_timed_out": False,
        "rta_timeout_sec": 7.0,
        "rta_profile_enabled": False,
        "rta_profile_task_time_sum_sec": "",
        "rta_profile_task_count": 0,
    }
    base.update(updates)
    return base


def v21_row(**updates):
    values = row(
        variant_name="v21_experimental",
        variant_label="v21 local-window experimental RTA",
        variant_safety_label="experimental_sufficient_candidate",
        variant_is_default=False,
        variant_is_experimental=True,
        proof_claim_eligible=False,
        rta_version="v21-local-window",
        rta_tool="asap_block_rta_v21_local_window.py",
        expected_rta_version="v21-local-window",
        rta_response_time_bound=8.0,
        rta_response_bound=8.0,
    )
    values.update(updates)
    return values


def write_rows(path, rows, include_forbidden=False):
    columns = list(INPUT_COLUMNS)
    if include_forbidden:
        columns.extend(sorted(FORBIDDEN))
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def output_files():
    return (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_VARIANT_FILENAME,
        analyzer.BY_UTILIZATION_FILENAME,
        analyzer.BY_VARIANT_UTILIZATION_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
        "plots/rta_pass_ratio_by_variant.png",
        "plots/rta_pass_ratio_by_variant_utilization.png",
        "plots/proof_claim_pass_ratio_by_variant.png",
        "plots/runtime_by_variant.png",
        "plots/timeout_rate_by_variant.png",
        "plots/bound_by_variant.png",
    )


def test_empty_input_writes_all_csvs_and_no_data_plots(tmp_path):
    source = tmp_path / "empty.csv"
    output = tmp_path / "analysis"
    write_rows(source, [])

    overall, by_variant, by_util, by_variant_util, by_config = analyzer.analyze(
        source, output
    )

    assert overall.iloc[0]["group_key"] == "overall"
    assert overall.iloc[0]["group_value"] == "all"
    assert overall.iloc[0]["total_rows"] == 0
    assert by_variant.empty
    assert by_util.empty
    assert by_variant_util.empty
    assert by_config.empty
    for relative in output_files():
        assert (output / relative).is_file()
    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_VARIANT_FILENAME,
        analyzer.BY_UTILIZATION_FILENAME,
        analyzer.BY_VARIANT_UTILIZATION_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
    ):
        saved = pd.read_csv(output / filename)
        assert list(saved.columns) == analyzer.SUMMARY_COLUMNS


def test_counts_rates_proof_claim_groups_plots_and_forbidden_metrics(tmp_path):
    source = tmp_path / "results.csv"
    output = tmp_path / "analysis"
    rows = [
        row(
            taskset_id="v20-pass",
            rta_runtime_sec=1.0,
            rta_profile_task_time_sum_sec=0.1,
            acceptance_ratio=1.0,
            pessimism=99,
            observed_max_response_time=10,
        ),
        row(
            taskset_id="v20-fail",
            rta_status="rta_unproven",
            rta_proven=False,
            rta_schedulable=False,
            rta_response_bound="",
            rta_response_time_bound="",
            rta_runtime_sec=2.0,
            rta_profile_task_time_sum_sec=0.2,
        ),
        v21_row(
            taskset_id="v21-pass",
            normalized_utilization=0.4,
            config_id="cfg-u04",
            rta_runtime_sec=3.0,
            rta_profile_task_time_sum_sec=0.3,
        ),
        row(
            taskset_id="timeout",
            normalized_utilization=0.4,
            config_id="cfg-u04",
            rta_status="rta_error",
            rta_error="RTA timed out",
            result_status="rta_timeout",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=30.0,
            rta_timed_out=True,
        ),
        v21_row(
            taskset_id="version-mismatch",
            normalized_utilization=0.4,
            config_id="cfg-u04",
            rta_status="rta_error",
            rta_error="expected v21-local-window report, got 'v20.4'",
            result_status="rta_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_version="v20.4",
            rta_response_bound="",
            rta_response_time_bound="",
            rta_runtime_sec=4.0,
        ),
        row(
            taskset_id="unattempted",
            config_id="cfg-unattempted",
            rta_status="rta_error",
            rta_error="task generation failed",
            result_status="task_generation_error",
            rta_proven=False,
            rta_schedulable=False,
            rta_attempted=False,
            rta_response_bound="",
            rta_response_time_bound="",
            rta_runtime_sec="",
        ),
    ]
    write_rows(source, rows, include_forbidden=True)

    overall, by_variant, by_util, by_variant_util, by_config = analyzer.analyze(
        source, output
    )
    summary = overall.iloc[0]
    assert summary["total_rows"] == 6
    assert summary["attempted_count"] == 5
    assert summary["completed_count"] == 3
    assert summary["timeout_count"] == 1
    assert summary["error_count"] == 1
    assert summary["unattempted_count"] == 1
    assert summary["rta_pass_count"] == 2
    assert summary["rta_fail_count"] == 1
    assert summary["rta_pass_ratio"] == pytest.approx(2 / 3)
    assert summary["rta_pass_yield"] == pytest.approx(2 / 5)
    assert summary["timeout_rate"] == pytest.approx(1 / 5)
    assert summary["error_rate"] == pytest.approx(1 / 5)
    assert summary["proof_claim_eligible_completed_count"] == 2
    assert summary["proof_claim_pass_count"] == 1
    assert summary["proof_claim_pass_ratio"] == pytest.approx(0.5)
    assert summary["runtime_sample_count"] == 3
    assert summary["runtime_mean_sec"] == pytest.approx(2.0)
    assert summary["bound_sample_count"] == 2
    assert summary["profile_runtime_sample_count"] == 3
    assert summary["profile_runtime_p95_sec"] == pytest.approx(0.29)

    v20 = by_variant.set_index("variant_name").loc["v20p4_full"]
    assert v20["proof_claim_eligible_completed_count"] == 2
    assert v20["proof_claim_pass_count"] == 1
    assert v20["proof_claim_pass_ratio"] == pytest.approx(0.5)

    v21 = by_variant.set_index("variant_name").loc["v21_experimental"]
    assert v21["rta_pass_count"] == 1
    assert v21["proof_claim_eligible_completed_count"] == 0
    assert v21["proof_claim_pass_count"] == 0
    assert pd.isna(v21["proof_claim_pass_ratio"])

    assert set(by_variant["group_key"]) == {"variant_name"}
    assert set(by_variant["group_value"]) == {"v20p4_full", "v21_experimental"}
    assert set(by_util["group_key"]) == {"normalized_utilization"}
    assert set(by_util["group_value"]) == {0.2, 0.4}
    assert set(by_variant_util["group_key"]) == {"variant_utilization"}
    assert set(by_config["group_key"]) == {"config_id"}
    assert set(by_config["group_value"]) == {
        "cfg-u02",
        "cfg-u04",
        "cfg-unattempted",
    }

    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_VARIANT_FILENAME,
        analyzer.BY_UTILIZATION_FILENAME,
        analyzer.BY_VARIANT_UTILIZATION_FILENAME,
        analyzer.BY_CONFIG_FILENAME,
    ):
        saved = pd.read_csv(output / filename)
        assert FORBIDDEN.isdisjoint(saved.columns)
    for relative in output_files():
        assert (output / relative).is_file()


def test_runtime_bound_and_profile_quantiles_ignore_invalid_failures(tmp_path):
    source = tmp_path / "quantiles.csv"
    valid_rows = [
        row(
            taskset_id="valid-{}".format(index),
            rta_runtime_sec=float(index),
            rta_response_bound=float(index),
            rta_response_time_bound=99.0,
            rta_profile_task_time_sum_sec=index / 10.0,
        )
        for index in range(1, 6)
    ]
    fallback = row(
        taskset_id="fallback-bound",
        rta_runtime_sec=6.0,
        rta_response_bound="",
        rta_response_time_bound=6.0,
        rta_profile_task_time_sum_sec=0.6,
    )
    invalid_rows = [
        row(
            taskset_id="invalid-{}".format(index),
            rta_status="rta_unproven",
            rta_proven=False,
            rta_schedulable=False,
            rta_runtime_sec=runtime,
            rta_response_bound=bound,
            rta_response_time_bound=bound,
            rta_profile_task_time_sum_sec=profile,
        )
        for index, (runtime, bound, profile) in enumerate([
            ("", "", ""),
            ("nan", "nan", "nan"),
            ("inf", "inf", "inf"),
            ("-inf", "-inf", "-inf"),
            (-1, -1, -1),
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
            rta_response_bound=100,
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
            rta_response_bound=200,
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
            rta_response_bound=300,
        ),
    ]
    write_rows(source, valid_rows + [fallback] + invalid_rows + failure_rows)

    summary = analyzer.build_overall(analyzer.load_results(source), source).iloc[0]
    assert summary["runtime_sample_count"] == 6
    assert summary["runtime_mean_sec"] == pytest.approx(3.5)
    assert summary["runtime_median_sec"] == pytest.approx(3.5)
    assert summary["runtime_p75_sec"] == pytest.approx(4.75)
    assert summary["runtime_p90_sec"] == pytest.approx(5.5)
    assert summary["runtime_p95_sec"] == pytest.approx(5.75)
    assert summary["runtime_max_sec"] == pytest.approx(6.0)

    assert summary["bound_sample_count"] == 6
    assert summary["bound_mean"] == pytest.approx(3.5)
    assert summary["bound_median"] == pytest.approx(3.5)
    assert summary["bound_p75"] == pytest.approx(4.75)
    assert summary["bound_p90"] == pytest.approx(5.5)
    assert summary["bound_p95"] == pytest.approx(5.75)
    assert summary["bound_max"] == pytest.approx(6.0)

    assert summary["profile_runtime_sample_count"] == 6
    assert summary["profile_runtime_mean_sec"] == pytest.approx(0.35)
    assert summary["profile_runtime_median_sec"] == pytest.approx(0.35)
    assert summary["profile_runtime_p95_sec"] == pytest.approx(0.575)
    assert summary["profile_runtime_max_sec"] == pytest.approx(0.6)


def test_strict_validates_input_schema_but_accepts_empty_and_failures(tmp_path):
    assert analyzer.main([
        "--input", str(tmp_path / "missing.csv"),
        "--output-dir", str(tmp_path / "missing-output"),
        "--strict",
    ]) != 0

    malformed = tmp_path / "malformed.csv"
    pd.DataFrame([{"variant_name": "v20p4_full"}]).to_csv(
        malformed, index=False
    )
    assert analyzer.main([
        "--input", str(malformed),
        "--output-dir", str(tmp_path / "malformed-output"),
        "--strict",
    ]) != 0

    empty = tmp_path / "empty.csv"
    write_rows(empty, [])
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
        v21_row(taskset_id="v21-pass"),
    ])
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("status\ncompleted\n", encoding="utf-8")
    assert analyzer.main([
        "--input", str(failures),
        "--manifest", str(manifest),
        "--output-dir", str(tmp_path / "failure-output"),
        "--strict",
    ]) == 0


def test_analyzer_source_does_not_call_rta_runner_or_simulation():
    source = Path(analyzer.__file__).read_text(encoding="utf-8")
    assert "run_" + "asap_block_rta" not in source
    assert "asap_block_rta.py" not in source
    assert "asap_block_rta_" + "v21" not in source
    assert "run_single_" + "simulation_worker" not in source
    assert "global_" + "task_generator" not in source
    assert "run_rta_" + "ablation" not in source
    assert "run_rta_" + "v21_comparison" not in source
