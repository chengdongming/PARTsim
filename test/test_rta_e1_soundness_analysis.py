from pathlib import Path

import pandas as pd
import pytest

from scripts import analyze_rta_e1_soundness as analyzer


def row(**updates):
    base = {
        "algorithm": "gpfp_asap_block",
        "rta_enabled": True,
        "config_id": "cfg",
        "taskset_id": "ts",
        "taskset_seed": 100,
        "rta_version": "v20.4",
        "status": "accepted",
        "accepted": 1,
        "timeout": 0,
        "rta_schedulable": False,
        "rta_proven": False,
        "sim_schedulable": True,
        "soundness_violation": False,
        "soundness_valid": True,
        "soundness_excluded_reason": "",
        "observed_max_response_time": "",
        "rta_response_bound": "",
        "first_missed_task": "",
        "first_missed_job_release": "",
        "first_missed_deadline": "",
        "deadline_miss_time": "",
    }
    base.update(updates)
    return base


def write_csv(path: Path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def matrix_count(confusion, rta_result, sim_result):
    selected = confusion[
        (confusion["rta_result"] == rta_result)
        & (confusion["sim_result"] == sim_result)
    ]
    assert len(selected) == 1
    return int(selected.iloc[0]["count"])


def test_analyzer_writes_outputs_and_four_confusion_quadrants(tmp_path):
    source = tmp_path / "per_taskset_results.csv"
    output = tmp_path / "e1"
    write_csv(source, [
        row(
            taskset_id="pass-pass",
            config_id="cfg-a",
            rta_schedulable=True,
            rta_proven=True,
            sim_schedulable=True,
            observed_max_response_time=5,
            rta_response_bound=10,
        ),
        row(taskset_id="fail-pass", config_id="cfg-a"),
        row(
            taskset_id="fail-fail",
            config_id="cfg-b",
            status="rejected",
            accepted=0,
            sim_schedulable=False,
        ),
        row(
            taskset_id="violation",
            config_id="cfg-b",
            status="rejected",
            accepted=0,
            rta_schedulable=True,
            rta_proven=True,
            sim_schedulable=False,
            soundness_violation=True,
            observed_max_response_time=12,
            rta_response_bound=10,
            first_missed_task="task_0",
            first_missed_job_release=0,
            first_missed_deadline=10,
            deadline_miss_time=12,
        ),
        row(
            taskset_id="timeout",
            status="timeout",
            accepted=0,
            sim_schedulable=False,
            soundness_valid=False,
            soundness_excluded_reason="timeout",
        ),
        row(
            taskset_id="simulation-error",
            status="simulation_error",
            accepted=0,
            sim_schedulable=False,
            soundness_valid=False,
            soundness_excluded_reason="simulation_error",
        ),
        row(
            taskset_id="config-error",
            status="config_error",
            accepted=0,
            sim_schedulable=False,
            soundness_valid=False,
            soundness_excluded_reason="config_error",
        ),
        row(
            algorithm="gpfp_asap_nonblock",
            rta_enabled=False,
            taskset_id="ignored-non-rta",
            rta_schedulable=False,
            sim_schedulable=True,
        ),
    ])

    summary, confusion, violations, observed = analyzer.analyze(source, output)

    summary_row = summary.iloc[0]
    assert summary_row["total_rows"] == 7
    assert summary_row["valid_count"] == 4
    assert summary_row["excluded_count"] == 3
    assert summary_row["excluded_timeout_count"] == 1
    assert summary_row["excluded_infrastructure_count"] == 2
    assert summary_row["rta_pass_sim_pass_count"] == 1
    assert summary_row["rta_fail_sim_pass_count"] == 1
    assert summary_row["rta_fail_sim_fail_count"] == 1
    assert summary_row["rta_pass_sim_fail_count"] == 1
    assert summary_row["soundness_violation_count"] == 1
    assert summary_row["consistency_warning_count"] == 0
    assert summary_row["rta_version_values"] == "v20.4"

    assert matrix_count(confusion, "rta_pass", "sim_pass") == 1
    assert matrix_count(confusion, "rta_fail", "sim_pass") == 1
    assert matrix_count(confusion, "rta_fail", "sim_fail") == 1
    assert matrix_count(confusion, "rta_pass", "sim_fail") == 1

    assert len(violations) == 1
    assert violations.iloc[0]["taskset_id"] == "violation"
    assert violations.iloc[0]["first_missed_task"] == "task_0"

    assert len(observed) == 1
    assert observed.iloc[0]["taskset_id"] == "pass-pass"
    assert observed.iloc[0]["pessimism_ratio"] == pytest.approx(2.0)

    by_config = pd.read_csv(output / analyzer.SUMMARY_BY_CONFIG_FILENAME)
    assert dict(zip(by_config["config_id"], by_config["total_rows"])) == {
        "cfg": 3,
        "cfg-a": 2,
        "cfg-b": 2,
    }

    for relative in (
        analyzer.SUMMARY_FILENAME,
        analyzer.SUMMARY_BY_CONFIG_FILENAME,
        analyzer.CONFUSION_FILENAME,
        analyzer.VIOLATIONS_FILENAME,
        analyzer.OBSERVED_VS_BOUND_FILENAME,
        "plots/e1_confusion_matrix.png",
        "plots/e1_observed_vs_bound.png",
    ):
        assert (output / relative).is_file()


@pytest.mark.parametrize("config_values", [None, ["", ""]])
def test_summary_by_config_is_written_with_headers_without_groups(
    tmp_path, config_values
):
    source = tmp_path / "per_taskset_results.csv"
    output = tmp_path / "e1"
    rows = [row(taskset_id="first"), row(taskset_id="second")]
    if config_values is None:
        for item in rows:
            item.pop("config_id")
    else:
        for item, config_id in zip(rows, config_values):
            item["config_id"] = config_id
    write_csv(source, rows)

    analyzer.analyze(source, output)

    by_config = pd.read_csv(output / analyzer.SUMMARY_BY_CONFIG_FILENAME)
    assert by_config.empty
    assert list(by_config.columns) == analyzer.SUMMARY_BY_CONFIG_COLUMNS


def test_summary_by_config_is_written_for_empty_input(tmp_path):
    source = tmp_path / "empty_per_taskset_results.csv"
    output = tmp_path / "e1"
    pd.DataFrame(columns=["algorithm", "rta_enabled", "status"]).to_csv(
        source, index=False
    )

    analyzer.analyze(source, output)

    by_config = pd.read_csv(output / analyzer.SUMMARY_BY_CONFIG_FILENAME)
    assert by_config.empty
    assert list(by_config.columns) == analyzer.SUMMARY_BY_CONFIG_COLUMNS


def test_missing_soundness_valid_fallback_excludes_timeout_and_infra(tmp_path):
    source = tmp_path / "legacy_per_taskset_results.csv"
    output = tmp_path / "legacy"
    rows = [
        row(taskset_id="accepted", status="accepted"),
        row(
            taskset_id="timeout",
            status="simulation_timeout",
            accepted=0,
            sim_schedulable=False,
        ),
        row(
            taskset_id="infra",
            status="simulation_error",
            accepted=0,
            sim_schedulable=False,
        ),
    ]
    for item in rows:
        item.pop("soundness_valid")
        item.pop("soundness_excluded_reason")
    write_csv(source, rows)

    summary, confusion, _, _ = analyzer.analyze(source, output)

    assert summary.iloc[0]["valid_count"] == 1
    assert summary.iloc[0]["excluded_count"] == 2
    assert summary.iloc[0]["excluded_timeout_count"] == 1
    assert summary.iloc[0]["excluded_infrastructure_count"] == 1
    assert int(confusion["count"].sum()) == 1


def test_consistency_warning_records_valid_pass_fail_without_violation(tmp_path):
    source = tmp_path / "warning.csv"
    output = tmp_path / "warning-out"
    write_csv(source, [
        row(
            status="rejected",
            accepted=0,
            rta_schedulable=True,
            rta_proven=True,
            sim_schedulable=False,
            soundness_violation=False,
        )
    ])

    summary, _, violations, _ = analyzer.analyze(source, output)

    assert summary.iloc[0]["soundness_violation_count"] == 0
    assert summary.iloc[0]["consistency_warning_count"] == 1
    assert violations.empty
    assert analyzer.main([
        "--input", str(source),
        "--output-dir", str(output / "strict"),
        "--strict",
    ]) == 1


def test_strict_mode_returns_nonzero_for_soundness_violation(tmp_path):
    source = tmp_path / "violation.csv"
    write_csv(source, [
        row(
            status="rejected",
            accepted=0,
            rta_schedulable=True,
            rta_proven=True,
            sim_schedulable=False,
            soundness_violation=True,
        )
    ])

    assert analyzer.main([
        "--input", str(source),
        "--output-dir", str(tmp_path / "strict-out"),
        "--strict",
    ]) == 1


def test_empty_violation_file_keeps_requested_header(tmp_path):
    source = tmp_path / "clean.csv"
    output = tmp_path / "clean-out"
    write_csv(source, [
        row(
            rta_schedulable=True,
            rta_proven=True,
            sim_schedulable=True,
            observed_max_response_time=0,
            rta_response_bound=10,
        )
    ])

    _, _, violations, observed = analyzer.analyze(source, output)
    saved = pd.read_csv(output / analyzer.VIOLATIONS_FILENAME)
    observed_saved = pd.read_csv(output / analyzer.OBSERVED_VS_BOUND_FILENAME)

    assert violations.empty
    assert saved.empty
    for column in analyzer.PRIORITY_VIOLATION_COLUMNS:
        assert column in saved.columns
    assert len(observed) == 1
    assert observed_saved.iloc[0]["pessimism_ratio"] != observed_saved.iloc[0][
        "pessimism_ratio"
    ]
