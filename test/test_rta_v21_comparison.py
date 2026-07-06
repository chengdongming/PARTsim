import csv
import json
import subprocess
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

from scripts import analyze_rta_v21_comparison as analyzer
from scripts import run_rta_v21_comparison as runner


def report(
    version,
    proven=True,
    bound=10,
    runtime_sec=None,
    profile=None,
    top_level=None,
    reason=None,
):
    task = {
        "task_name": "t1",
        "proven_under_assumptions": proven,
        "response_time_bound": bound if proven else None,
    }
    if profile is not None:
        task["rta_profile"] = profile
    payload = {
        "rta_version": version,
        "proven_under_assumptions": proven,
        "tasks": [task],
    }
    if top_level:
        payload.update(top_level)
    return {
        "version": version,
        "status": "proven_under_assumptions" if proven else "rta_unproven",
        "proven": proven,
        "error": "",
        "reason": "" if proven else (reason or "not closed"),
        "bound": bound if proven else None,
        "runtime_sec": runtime_sec,
        "report": payload,
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


def simulation(accepted=True, response=5, status=None):
    status = status or ("accepted" if accepted else "rejected")
    return {
        "accepted": accepted,
        "simulation_status": status,
        "simulated_response_time": response if status != "simulation_error" else "",
        "deadline_miss_time": "" if accepted else 8,
        "first_missed_task": "" if accepted else "t1",
        "max_response_by_task": {} if status == "simulation_error" else {"t1": response},
        "trace_path": "",
    }


def test_comparison_row_contains_both_versions_and_valid_tightness():
    profile = {
        "delta_iterations": 2,
        "g_loc_calls": 3,
        "omega_feasibility_calls": 4,
        "empty_omega_count": 1,
        "no_closure_count": 0,
        "closed_prefix_count": 2,
        "delta_cap_exceeded_count": 0,
        "max_delta_cap": 7,
        "max_delta_seen": 5,
        "delta_jump_count": 1,
    }
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12),
        report(runner.V21_VERSION, bound=10, profile=profile),
        0.0,
    )
    assert set(runner.RESULT_FIELDS) == set(row)
    assert len(runner.RESULT_FIELDS) == len(set(runner.RESULT_FIELDS))
    assert row["v20p4_rta_version"] == "v20.4"
    assert row["v20_rta_version"] == "v20.4"
    assert row["v20_theory_family"] == "complete_window"
    assert row["v20_uses_local_window"] == 0
    assert row["v20_empty_state_guard"] == 1
    assert row["v21_rta_version"] == "v21-local-window"
    assert row["v21_theory_family"] == "local_window_closure"
    assert row["v21_closure_method"] == "delta_closure"
    assert row["v21_empty_set_guard"] == 1
    assert row["v21_fallback_guard"] == 1
    assert row["v21_consistency_guard"] == 1
    assert row["v21_certified_carry_in_source"] == "v21_recursive_certification"
    assert row["v21_uses_local_window"] == 1
    assert row["v21_uses_delta_closure"] == 1
    assert row["v21_uses_parallel_u_compression"] == 0
    assert row["v21_delta_iterations"] == 2
    assert row["v21_g_loc_calls"] == 3
    assert row["v21_omega_feasibility_calls"] == 4
    assert row["v21_empty_omega_count"] == 1
    assert row["v21_no_closure_count"] == 0
    assert row["v21_closed_prefix_count"] == 2
    assert row["v21_delta_cap_exceeded_count"] == 0
    assert row["v21_max_delta_cap"] == 7
    assert row["v21_max_delta_seen"] == 5
    assert row["v21_delta_jump_count"] == 1
    assert row["v20p4_tightness"] == pytest.approx(2.4)
    assert row["v21_tightness"] == pytest.approx(2.0)
    assert row["pessimism_v20"] == pytest.approx(2.4)
    assert row["pessimism_v21"] == pytest.approx(2.0)
    assert row["intersection_pessimism_v20"] == pytest.approx(2.4)
    assert row["intersection_pessimism_v21"] == pytest.approx(2.0)
    assert row["intersection_pessimism_improvement"] == pytest.approx(0.4)
    assert row["v21_minus_v20p4_bound"] == -2
    assert row["v21_bound_lt_v20p4"] == 1
    assert row["v21_bound_gt_v20"] == 0
    assert row["v21_bound_gt_v20_reason"] == ""
    assert row["v20_soundness_violation"] == 0
    assert row["v21_soundness_violation"] == 0
    assert row["soundness_valid"] == 1
    assert row["soundness_excluded_reason"] == ""
    assert row["e0_assumption_scope"] == "unconditional_zero_lower_bound"
    assert row["release_energy_assumption_verified"] == 1


def test_comparison_row_contains_runtime_and_clear_aliases():
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12, runtime_sec=0.5),
        report(runner.V21_VERSION, bound=10, runtime_sec=1.5),
        0.25,
    )
    assert row["runtime_v20_sec"] == pytest.approx(0.5)
    assert row["runtime_v21_sec"] == pytest.approx(1.5)
    assert row["runtime_slowdown_v21_over_v20"] == pytest.approx(3.0)
    assert row["v20_only_proven"] == 0
    assert row["v21_only_proven"] == 0
    assert row["both_rejected"] == 0


def test_v21_bound_gt_v20_is_consistency_audit_not_soundness():
    row = runner._comparison_row(
        base_row(), simulation(response=9),
        report(runner.V20_VERSION, bound=10),
        report(runner.V21_VERSION, bound=12),
        0.25,
        soundness_mode="audit",
    )
    assert row["v21_bound_gt_v20"] == 1
    assert row["v21_bound_gt_v20_reason"] == "both_proven_v21_bound_larger"
    assert row["v21_soundness_violation"] == 0
    assert row["v20_soundness_violation"] == 0


def test_tightness_is_empty_for_unproven_analysis():
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=False),
        1.0,
    )
    assert row["v20p4_tightness"] == ""
    assert row["v21_tightness"] == ""
    assert row["pessimism_v20"] == ""
    assert row["pessimism_v21"] == ""
    assert row["intersection_pessimism_v20"] == ""
    assert row["intersection_pessimism_v21"] == ""
    assert row["intersection_pessimism_improvement"] == ""
    assert row["both_unproven"] == 1
    assert row["both_rejected"] == 1


def test_pessimism_is_empty_when_observed_response_is_not_positive():
    row = runner._comparison_row(
        base_row(), simulation(response=0),
        report(runner.V20_VERSION, bound=12),
        report(runner.V21_VERSION, bound=10),
        1.0,
    )
    assert row["pessimism_v20"] == ""
    assert row["pessimism_v21"] == ""
    assert row["intersection_pessimism_v20"] == ""
    assert row["intersection_pessimism_v21"] == ""
    assert row["intersection_pessimism_improvement"] == ""


def test_soundness_rejects_proven_but_simulation_rejected():
    with pytest.raises(RuntimeError, match="soundness violation") as error:
        runner._comparison_row(
            base_row(), simulation(accepted=False),
            report(runner.V20_VERSION, proven=False),
            report(runner.V21_VERSION, proven=True),
            0.0,
        )
    message = str(error.value)
    for field in (
        "seed_base", "taskset_seed", "normalized_utilization",
        "task_idx", "taskset_id", "E0", "taskset_path", "accepted",
        "simulation_status", "simulated_response_time",
        "deadline_miss_time", "first_missed_task", "v21_status",
        "v21_proven", "v21_bound", "v21_error", "v20p4_status",
        "v20p4_proven", "v20p4_bound", "v20p4_error",
    ):
        assert "{}=".format(field) in message
    assert "normalized_utilization=0.1" in message
    assert "task_idx=0" in message
    assert "E0=0.0" in message
    assert "taskset_path='/tmp/tasks.yml'" in message
    assert "v21_bound=10" in message


def test_soundness_rejects_observed_response_above_v21_bound():
    with pytest.raises(RuntimeError, match="soundness violation"):
        runner._comparison_row(
            base_row(), simulation(response=11),
            report(runner.V20_VERSION, proven=False),
            report(runner.V21_VERSION, proven=True, bound=10),
            0.0,
        )


def test_soundness_audit_records_violation_without_raising():
    row = runner._comparison_row(
        base_row(), simulation(accepted=False),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=True),
        0.0,
        soundness_mode="audit",
    )
    assert row["v21_soundness_proven_but_rejected"] == 1
    assert row["v21_sim_rejected_violation"] == 1
    assert row["v21_observed_bound_violation"] == 0
    assert row["v20p4_soundness_proven_but_rejected"] == 0
    assert row["v21_soundness_violation"] == 1
    assert row["v20_soundness_violation"] == 0
    assert row["soundness_valid"] == 1
    assert row["soundness_excluded_reason"] == ""


def test_soundness_audit_or_includes_observed_bound_violation():
    row = runner._comparison_row(
        base_row(), simulation(response=11),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=True, bound=10),
        0.0,
        soundness_mode="audit",
    )
    assert row["v21_soundness_proven_but_rejected"] == 0
    assert row["v21_sim_rejected_violation"] == 0
    assert row["v21_soundness_observed_exceeds_bound"] == 1
    assert row["v21_observed_bound_violation"] == 1
    assert row["v21_soundness_violation"] == 1


@pytest.mark.parametrize(
    "status,reason",
    [
        ("simulation_timeout", "timeout"),
        ("simulation_error", "simulation_error"),
        ("config_error", "config_error"),
    ],
)
def test_soundness_audit_excludes_timeout_and_infrastructure_failures(
    status, reason
):
    row = runner._comparison_row(
        base_row(), simulation(accepted=False, status=status),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=True),
        0.0,
        soundness_mode="audit",
    )
    assert row["v21_soundness_proven_but_rejected"] == 0
    assert row["v21_sim_rejected_violation"] == 0
    assert row["v21_observed_bound_violation"] == 0
    assert row["v21_soundness_violation"] == 0
    assert row["soundness_valid"] == 0
    assert row["soundness_excluded_reason"] == reason


def test_soundness_fail_fast_does_not_raise_soundness_for_infrastructure():
    row = runner._comparison_row(
        base_row(), simulation(accepted=False, status="simulation_error"),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=True),
        0.0,
    )
    assert row["v21_soundness_violation"] == 0
    assert row["soundness_valid"] == 0


def test_e0_zero_proven_and_rejected_is_unconditional_violation():
    row = runner._comparison_row(
        base_row(),
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=True),
        report(runner.V21_VERSION, proven=True),
        0.0,
        soundness_mode="audit",
    )
    assert row["e0_assumption_scope"] == "unconditional_zero_lower_bound"
    assert row["release_energy_assumption_verified"] == 1
    assert row["soundness_valid"] == 1
    assert row["soundness_excluded_reason"] == ""
    assert row["v20_soundness_violation"] == 1
    assert row["v21_soundness_violation"] == 1


def test_positive_e0_proven_and_rejected_is_conditional_diagnostic():
    row = runner._comparison_row(
        base_row(),
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=True),
        report(runner.V21_VERSION, proven=True),
        1.0,
        soundness_mode="audit",
    )
    assert (
        row["e0_assumption_scope"]
        == "conditional_release_time_lower_bound"
    )
    assert row["release_energy_assumption_verified"] == 0
    assert row["soundness_valid"] == 0
    assert (
        row["soundness_excluded_reason"]
        == "e0_release_energy_assumption_not_verified"
    )
    assert row["v20_soundness_violation"] == 0
    assert row["v21_soundness_violation"] == 0
    assert row["v20_sim_rejected_violation"] == 0
    assert row["v21_sim_rejected_violation"] == 0
    assert row["v20_conditional_proven_but_sim_rejected"] == 1
    assert row["v21_conditional_proven_but_sim_rejected"] == 1


def test_positive_e0_unproven_and_rejected_is_excluded_not_violation():
    row = runner._comparison_row(
        base_row(),
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=False),
        1.0,
        soundness_mode="audit",
    )
    assert row["soundness_valid"] == 0
    assert (
        row["soundness_excluded_reason"]
        == "e0_release_energy_assumption_not_verified"
    )
    assert row["v20_soundness_violation"] == 0
    assert row["v21_soundness_violation"] == 0
    assert row["v20_conditional_proven_but_sim_rejected"] == 0
    assert row["v21_conditional_proven_but_sim_rejected"] == 0


def test_positive_e0_observed_bound_conflict_is_conditional_diagnostic():
    row = runner._comparison_row(
        base_row(),
        simulation(response=11),
        report(runner.V20_VERSION, proven=True, bound=10),
        report(runner.V21_VERSION, proven=True, bound=10),
        1.0,
        soundness_mode="audit",
    )
    assert row["soundness_valid"] == 0
    assert row["v20_observed_bound_violation"] == 0
    assert row["v21_observed_bound_violation"] == 0
    assert row["v20_soundness_violation"] == 0
    assert row["v21_soundness_violation"] == 0
    assert row["v20_conditional_observed_exceeds_bound"] == 1
    assert row["v21_conditional_observed_exceeds_bound"] == 1


def test_positive_e0_with_release_energy_certificate_is_soundness_valid():
    row = runner._comparison_row(
        base_row(),
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=True),
        report(runner.V21_VERSION, proven=True),
        1.0,
        soundness_mode="audit",
        release_energy_assumption_verified=True,
    )
    assert row["e0_assumption_scope"] == "conditional_release_time_lower_bound"
    assert row["release_energy_assumption_verified"] == 1
    assert row["soundness_valid"] == 1
    assert row["soundness_excluded_reason"] == ""
    assert row["v20_soundness_violation"] == 1
    assert row["v21_soundness_violation"] == 1
    assert row["v20_conditional_proven_but_sim_rejected"] == 0
    assert row["v21_conditional_proven_but_sim_rejected"] == 0


def _write_result(path: Path, **updates):
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12),
        report(runner.V21_VERSION, bound=10),
        0.0,
    )
    row.update(updates)
    pd.DataFrame([row], columns=runner.RESULT_FIELDS).to_csv(path, index=False)


def test_analyzer_writes_summary_and_figures(tmp_path):
    source = tmp_path / "comparison.csv"
    output = tmp_path / "analysis_outputs_v21"
    _write_result(
        source,
        runtime_v20_sec=0.5,
        runtime_v21_sec=1.5,
        runtime_slowdown_v21_over_v20=3.0,
    )
    summary, by_util = analyzer.analyze(source, output)
    assert summary.iloc[0]["v21_bound_lt_v20p4_count"] == 1
    assert summary.iloc[0]["median_pessimism_v20"] == pytest.approx(2.4)
    assert summary.iloc[0]["p95_pessimism_v21"] == pytest.approx(2.0)
    assert summary.iloc[0][
        "median_intersection_pessimism_improvement"
    ] == pytest.approx(0.4)
    assert summary.iloc[0]["runtime_p95_v20_sec"] == pytest.approx(0.5)
    assert summary.iloc[0]["runtime_p95_v21_sec"] == pytest.approx(1.5)
    assert summary.iloc[0][
        "runtime_p95_slowdown_v21_over_v20"
    ] == pytest.approx(3.0)
    assert by_util.iloc[0]["v21_proven_count"] == 1
    assert by_util.iloc[0]["median_pessimism_v20"] == pytest.approx(2.4)
    assert by_util.iloc[0][
        "median_intersection_pessimism_v21"
    ] == pytest.approx(2.0)
    assert by_util.iloc[0]["runtime_p95_v20_sec"] == pytest.approx(0.5)
    assert by_util.iloc[0]["runtime_p95_v21_sec"] == pytest.approx(1.5)
    for filename in (
        analyzer.SUMMARY_FILENAME,
        analyzer.BY_UTIL_FILENAME,
        "rta_v21_bound_delta.png",
        "rta_v21_tightness_comparison.png",
        "rta_v21_proven_ratio.png",
        "plots/pessimism_cdf.png",
        "plots/intersection_pessimism_boxplot.png",
        "plots/runtime_slowdown.png",
    ):
        assert (output / filename).is_file()


def test_analyzer_plots_do_not_fail_without_metric_data(tmp_path):
    source = tmp_path / "comparison.csv"
    output = tmp_path / "analysis_outputs_v21"
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, proven=False),
        report(runner.V21_VERSION, proven=False),
        0.25,
    )
    pd.DataFrame([row], columns=runner.RESULT_FIELDS).to_csv(
        source, index=False
    )

    analyzer.analyze(source, output)

    for filename in (
        analyzer.PESSIMISM_CDF_PLOT,
        analyzer.INTERSECTION_PESSIMISM_BOXPLOT,
        analyzer.RUNTIME_SLOWDOWN_PLOT,
    ):
        assert (output / "plots" / filename).is_file()


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
        "v21_soundness_violation",
        "v21_soundness_proven_but_rejected",
        "v21_soundness_observed_exceeds_bound",
    ],
)
def test_analyzer_summarizes_v21_soundness_violation(tmp_path, field):
    source = tmp_path / "comparison.csv"
    _write_result(source, **{field: 1})
    summary, _ = analyzer.analyze(source, tmp_path / "out")
    assert summary.iloc[0]["v21_soundness_violation_count"] == 1
    assert summary.iloc[0]["soundness_violations"] == 1


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
    loaded = analyzer.load_results(source)
    assert loaded.iloc[0]["v21_soundness_violation"]


def test_analyzer_does_not_recompute_infrastructure_failure_as_violation(tmp_path):
    source = tmp_path / "comparison.csv"
    _write_result(
        source,
        accepted=0,
        simulation_status="simulation_error",
        soundness_valid=0,
        soundness_excluded_reason="simulation_error",
    )
    loaded = analyzer.load_results(source)
    assert not loaded.iloc[0]["v21_soundness_violation"]
    assert not loaded.iloc[0]["v21_soundness_proven_but_rejected"]


def test_analyzer_excludes_positive_e0_from_unconditional_soundness(tmp_path):
    source = tmp_path / "comparison.csv"
    zero = runner._comparison_row(
        base_row(),
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=True),
        report(runner.V21_VERSION, proven=True),
        0.0,
        soundness_mode="audit",
    )
    positive = runner._comparison_row(
        {**base_row(), "task_idx": 1, "taskset_id": "u0.10-001"},
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=True),
        report(runner.V21_VERSION, proven=True),
        1.0,
        soundness_mode="audit",
    )
    pd.DataFrame(
        [zero, positive], columns=runner.RESULT_FIELDS
    ).to_csv(source, index=False)
    summary, _ = analyzer.analyze(source, tmp_path / "out")
    overall = summary.iloc[0]
    assert overall["unconditional_soundness_rows"] == 1
    assert overall["conditional_assumption_rows"] == 1
    assert overall["soundness_excluded_rows"] == 1
    assert overall["v20_soundness_violation_count"] == 1
    assert overall["v21_soundness_violation_count"] == 1
    assert overall["soundness_violations"] == 1
    assert (
        json.loads(overall["soundness_excluded_reason_counts"])
        == {"e0_release_energy_assumption_not_verified": 1}
    )
    assert (
        overall["v20_conditional_proven_but_sim_rejected_count"] == 1
    )
    assert (
        overall["v21_conditional_proven_but_sim_rejected_count"] == 1
    )
    assert "conditional release-time energy lower-bound assumption" in (
        overall["soundness_assumption_note"]
    )


def test_analyzer_corrects_old_positive_e0_violation_rows(tmp_path):
    source = tmp_path / "comparison.csv"
    row = runner._comparison_row(
        base_row(),
        simulation(accepted=False),
        report(runner.V20_VERSION, proven=True),
        report(runner.V21_VERSION, proven=True),
        1.0,
        soundness_mode="audit",
    )
    for field in (
        "e0_assumption_scope",
        "release_energy_assumption_verified",
        "v20_conditional_proven_but_sim_rejected",
        "v21_conditional_proven_but_sim_rejected",
        "v20_conditional_observed_exceeds_bound",
        "v21_conditional_observed_exceeds_bound",
    ):
        row.pop(field)
    row.update({
        "soundness_valid": 1,
        "soundness_excluded_reason": "",
        "v20p4_soundness_proven_but_rejected": 1,
        "v21_soundness_proven_but_rejected": 1,
        "v20_sim_rejected_violation": 1,
        "v21_sim_rejected_violation": 1,
        "v20_soundness_violation": 1,
        "v21_soundness_violation": 1,
    })
    pd.DataFrame([row]).to_csv(source, index=False)
    loaded = analyzer.load_results(source)
    result = loaded.iloc[0]
    assert result["e0_assumption_scope"] == (
        "conditional_release_time_lower_bound"
    )
    assert not result["release_energy_assumption_verified"]
    assert not result["soundness_valid"]
    assert result["soundness_excluded_reason"] == (
        "e0_release_energy_assumption_not_verified"
    )
    assert not result["v20_soundness_violation"]
    assert not result["v21_soundness_violation"]
    assert result["v20_conditional_proven_but_sim_rejected"]
    assert result["v21_conditional_proven_but_sim_rejected"]


def test_analyzer_string_assignments_emit_no_future_warning(tmp_path):
    source = tmp_path / "comparison.csv"
    _write_result(
        source,
        v21_bound=13,
        v21_minus_v20p4_bound=1,
        v21_bound_gt_v20=1,
        v21_bound_gt_v20_reason="",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        loaded = analyzer.load_results(source)
    assert (
        loaded.iloc[0]["v21_bound_gt_v20_reason"]
        == "both_proven_v21_bound_larger"
    )


def test_analyzer_treats_v21_bound_gt_v20_as_consistency_not_soundness(tmp_path):
    source = tmp_path / "comparison.csv"
    row = runner._comparison_row(
        base_row(), simulation(response=9),
        report(runner.V20_VERSION, bound=10),
        report(runner.V21_VERSION, bound=12),
        0.25,
        soundness_mode="audit",
    )
    pd.DataFrame([row], columns=runner.RESULT_FIELDS).to_csv(source, index=False)
    summary, _ = analyzer.analyze(source, tmp_path / "out")
    assert summary.iloc[0]["v21_bound_gt_v20_count"] == 1
    assert summary.iloc[0]["both_proven_v21_looser_count"] == 1
    assert summary.iloc[0]["v21_soundness_violation_count"] == 0
    assert summary.iloc[0]["soundness_violations"] == 0


def test_analyzer_aggregates_v21_closure_counters(tmp_path):
    source = tmp_path / "comparison.csv"
    first = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12),
        report(
            runner.V21_VERSION,
            bound=10,
            profile={
                "delta_iterations": 2,
                "g_loc_calls": 4,
                "omega_feasibility_calls": 6,
                "empty_omega_count": 1,
                "no_closure_count": 0,
                "closed_prefix_count": 2,
                "delta_cap_exceeded_count": 1,
                "max_delta_cap": 8,
                "max_delta_seen": 5,
                "delta_jump_count": 1,
            },
        ),
        0.25,
    )
    second = dict(first)
    second.update({
        "task_idx": 1,
        "taskset_id": "u0.10-001",
        "v21_delta_iterations": 3,
        "v21_g_loc_calls": 5,
        "v21_omega_feasibility_calls": 7,
        "v21_empty_omega_count": 2,
        "v21_no_closure_count": 1,
        "v21_closed_prefix_count": 4,
        "v21_delta_cap_exceeded_count": 0,
        "v21_max_delta_cap": 9,
        "v21_max_delta_seen": 6,
        "v21_delta_jump_count": 2,
        "v21_no_closure_observed": 1,
        "v21_timeout_or_horizon_failure": 1,
        "v21_failure_reason": "no closure by horizon",
    })
    pd.DataFrame([first, second], columns=runner.RESULT_FIELDS).to_csv(
        source, index=False
    )
    summary, _ = analyzer.analyze(source, tmp_path / "out")
    row = summary.iloc[0]
    assert row["v21_delta_iterations_total"] == 5
    assert row["v21_g_loc_calls_total"] == 9
    assert row["v21_omega_feasibility_calls_total"] == 13
    assert row["v21_empty_omega_count_total"] == 3
    assert row["v21_no_closure_count_total"] == 1
    assert row["v21_closed_prefix_count_total"] == 6
    assert row["v21_delta_cap_exceeded_count_total"] == 1
    assert row["v21_delta_jump_count_total"] == 3
    assert row["v21_max_delta_cap_max"] == 9
    assert row["v21_max_delta_seen_max"] == 6
    assert row["v21_no_closure_observed_count"] == 1
    assert row["v21_timeout_or_horizon_failure_count"] == 1


def test_analyzer_accepts_old_csv_without_new_audit_fields(tmp_path):
    source = tmp_path / "comparison.csv"
    row = runner._comparison_row(
        base_row(), simulation(),
        report(runner.V20_VERSION, bound=12),
        report(runner.V21_VERSION, bound=10),
        0.25,
    )
    old_fields = [
        field for field in runner.RESULT_FIELDS
        if not (
            field.startswith("v20_")
            or field.startswith("v21_delta_")
            or field.startswith("v21_g_loc")
            or field.startswith("v21_omega")
            or field.startswith("v21_empty_omega")
            or field.startswith("v21_no_closure")
            or field.startswith("v21_closed_prefix")
            or field.startswith("v21_max_delta")
            or field
            in {
                "v21_theory_family",
                "v21_closure_method",
                "v21_empty_set_guard",
                "v21_fallback_guard",
                "v21_consistency_guard",
                "v21_certified_carry_in_source",
                "v21_uses_local_window",
                "v21_uses_delta_closure",
                "v21_uses_parallel_u_compression",
                "v21_timeout_or_horizon_failure",
                "v21_fallback_used",
                "v21_fallback_reason",
                "v21_failure_reason",
                "v21_certificate_status",
                "v21_bound_gt_v20",
                "v21_bound_gt_v20_reason",
                "v21_sim_rejected_violation",
                "v21_observed_bound_violation",
            }
        )
    ]
    pd.DataFrame([{field: row[field] for field in old_fields}]).to_csv(
        source, index=False
    )
    loaded = analyzer.load_results(source)
    assert loaded.iloc[0]["v21_delta_iterations"] == 0
    assert not loaded.iloc[0]["v21_bound_gt_v20"]
    assert loaded.iloc[0]["v21_theory_family"] == "local_window_closure"


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
    assert args.soundness_mode == "fail_fast"
    assert runner.acceptance.RTA_VERSION == "v20.4"
    assert Path(runner.acceptance.RTA_TOOL).name == "asap_block_rta.py"


def test_runner_help_distinguishes_release_time_e0_from_simulation_energy():
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(runner.__file__).resolve()),
            "--help",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = " ".join(completed.stdout.lower().split())
    assert "rta analysis-window/job-release energy lower bound" in help_text
    assert "not the simulation initial battery energy at t=0" in help_text
    assert "simulation initial battery-energy ratio at t=0" in help_text


def test_run_v21_enables_profile_flag(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        payload = {
            "rta_version": runner.V21_VERSION,
            "proven_under_assumptions": True,
            "tasks": [
                {
                    "task_name": "t1",
                    "proven_under_assumptions": True,
                    "response_time_bound": 10,
                    "rta_profile": {"delta_iterations": 1},
                }
            ],
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    args = runner.build_parser().parse_args(
        ["--experiment-name", "v21-smoke", "--e0-values", "0.25"]
    )
    result = runner._run_v21("/tmp/system.yml", "/tmp/tasks.yml", args, 0.25)
    assert "--profile-rta" in captured["command"]
    assert result["proven"] is True


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
