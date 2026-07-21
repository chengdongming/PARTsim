"""Frozen B3-v2 calibration, evidence, and no-substitution tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.v9_3.ext1b_config import load_ext1b_config
from experiments.v9_3.ext1b_engine import Ext1BRunner
import scripts.decide_v9_3_ext1b3_b3_v2_candidate as decision_module
from scripts.audit_v9_3_ext1b3_b3_v2_calibration import (
    CALIBRATION_CLOSURE_FIELDS,
    FROZEN_ETAS,
    FROZEN_MARGINS,
    FROZEN_PRIMARY_CANDIDATE,
    FROZEN_RHOS,
    FROZEN_SCHEDULERS,
    REPORT_SCHEMA,
    _calibration_grid_audit,
    audit_calibration,
    audit_generation_attempt_history,
    evaluate_primary_numeric_gate,
    summarize_metric_rows,
)
from scripts.decide_v9_3_ext1b3_b3_v2_candidate import (
    ON_PRIMARY_FAILURE,
    SIM_DEADLINE_MISS_USE,
    decide_candidate,
    decide_from_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    PROJECT_ROOT
    / "configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml"
)


def _raw_metric_row(
    *,
    denominator: int = 50,
    transitions: int | None = None,
    structurally_accepted: int = 50,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    transition_count = denominator if transitions is None else transitions
    row: dict[str, object] = {
        "target_observation_denominator": denominator,
        "target_wait_observed_count": denominator,
        "target_positive_slack_transition_count": transition_count,
        "full_release_prefix_affordable_count": denominator,
        "recovery_prefix_audit_closed_count": denominator,
        "target_audit_closed_count": denominator,
        "target_audit_error_count": 0,
        "recovery_prefix_audit_error_count": 0,
        "later_target_job_positive_transition_count": 0,
        "later_target_substitution_count": 0,
        "non_target_positive_transition_count": 0,
        "non_target_substitution_count": 0,
        "activation_from_other_job_only_count": 0,
        "target_transition_after_slack_exhaustion_count": 0,
        "target_terminated_without_transition_count": 0,
        "accepted_capacity_infeasible_task_count": 0,
        "accepted_capacity_infeasible_taskset_count": 0,
        "structurally_accepted_count": structurally_accepted,
        "structural_rejection_attempt_count": 0,
    }
    row.update({field: True for field in CALIBRATION_CLOSURE_FIELDS})
    if overrides:
        row.update(overrides)
    return row


def _control_metric_row() -> dict[str, object]:
    return _raw_metric_row(
        denominator=0, transitions=0, structurally_accepted=50,
    )


def _dataset() -> dict[str, object]:
    return {
        "passed": True,
        "errors": [],
        "checkpoint": {
            "schema": "ASAP_BLOCK_V9_3_EXT1B_CHECKPOINT_V1",
            "config_hash": "fixture",
            "requested": 2700,
            "terminal": 2700,
            "pending": 0,
            "stop_requested": False,
            "updated_at_utc": "fixture",
        },
        "terminal_status_counts": {"SIM_PASS_OBSERVED": 2700},
        "simulation_attempt_status_counts": {"SIM_PASS_OBSERVED": 2700},
        "runner_failure_count": 0,
        "illegal_timing_transition_count": 0,
        "unclassifiable_timing_transition_count": 0,
        "b3_timing_audit_error_count": 0,
        "b3_timing_open_audit_count": 0,
        "generated_taskset_count": 900,
        "generation_attempt_count": 900,
        "accepted_generation_attempt_count": 900,
        "logical_attempt_group_count": 900,
        "complete_attempt_history_group_count": 900,
        "incomplete_attempt_history_group_count": 0,
        "missing_attempt_index_count": 0,
        "duplicate_attempt_index_count": 0,
        "accepted_not_last_count": 0,
        "multiple_accepted_count": 0,
        "no_accepted_count": 0,
        "logical_index_domain_closed": True,
        "attempt_sequence_audit_closed": True,
        "accepted_cross_table_identity_audit_closed": True,
        "paired_instance_count": 900,
        "scheduler_request_count": 2700,
        "terminal_result_count": 2700,
        "calibration_unit_count": 18,
        "file_hashes_valid": True,
        "sim_deadline_miss_is_schedulability_or_performance_evidence": False,
    }


def _candidate(
    parameters: dict[str, object], rows: list[dict[str, object]],
) -> dict[str, object]:
    primary = parameters == FROZEN_PRIMARY_CANDIDATE
    per_utilization = {
        utilization: summarize_metric_rows([row])
        for utilization, row in zip(("1/5", "2/5"), rows)
    }
    return {
        "parameters": parameters,
        "role": "PRECOMMITTED_PRIMARY" if primary else "DIAGNOSTIC_ONLY",
        "formal_selection_eligible": primary,
        "per_utilization": per_utilization,
        "overall": summarize_metric_rows(rows),
    }


def _acceptance_report(
    *, primary_overrides: tuple[dict[str, object], dict[str, object]] | None = None,
) -> dict[str, object]:
    overrides = primary_overrides or ({}, {})
    primary_rows = [
        _raw_metric_row(overrides=dict(overrides[0])),
        _raw_metric_row(overrides=dict(overrides[1])),
    ]
    candidates = []
    for margin in FROZEN_MARGINS:
        for rho in FROZEN_RHOS:
            for eta in FROZEN_ETAS:
                parameters = {
                    "recovery_margin_ticks": margin,
                    "interpolation_rho": rho,
                    "nominal_energy_supply_ratio": eta,
                }
                rows = (
                    primary_rows
                    if parameters == FROZEN_PRIMARY_CANDIDATE
                    else [_raw_metric_row(), _raw_metric_row()]
                )
                candidates.append(_candidate(parameters, rows))
    primary_per = {
        utilization: evaluate_primary_numeric_gate(
            [row], expected_denominator=50,
        )
        for utilization, row in zip(("1/5", "2/5"), primary_rows)
    }
    primary_overall = evaluate_primary_numeric_gate(
        primary_rows, expected_denominator=100,
    )
    numeric_passed = (
        all(gate["passed"] for gate in primary_per.values())
        and primary_overall["passed"]
    )
    return {
        "schema": REPORT_SCHEMA,
        "protocol_state": "CALIBRATION_ONLY",
        "frozen_primary_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
        "automatic_parameter_replacement_permitted": False,
        "alternate_candidates_are_diagnostic_only": True,
        "config_audit": {
            "runner_description": {
                "cell_count": 18,
                "paired_instance_count": 900,
                "simulation_request_count": 2700,
            },
        },
        "dataset_integrity": _dataset(),
        "positive_controls": [
            {
                "normalized_utilization": utilization,
                "metrics": summarize_metric_rows([_control_metric_row()]),
            }
            for utilization in ("1/5", "2/5")
        ],
        "charging_candidates": candidates,
        "primary_gate": {
            "passed": numeric_passed,
            "dataset_integrity_passed": True,
            "numeric_gate_passed": numeric_passed,
            "per_utilization": primary_per,
            "overall": primary_overall,
        },
        "calibration_passed": numeric_passed,
        "formal_profile_created_or_authorized": False,
        "sim_deadline_miss_use": SIM_DEADLINE_MISS_USE,
        "on_primary_failure": ON_PRIMARY_FAILURE,
    }


def _primary(report: dict[str, object]) -> dict[str, object]:
    return next(
        candidate
        for candidate in report["charging_candidates"]
        if candidate["parameters"] == FROZEN_PRIMARY_CANDIDATE
    )


def _assert_rejected(report: dict[str, object]) -> dict[str, object]:
    decision = decide_candidate(report)
    assert decision["decision"] == "REJECTED"
    assert decision["selected_candidate"] is None
    assert decision["formal_profile_pr_permitted"] is False
    assert decision["parameter_status_formal_authorized"] is False
    assert decision["automatic_parameter_replacement_permitted"] is False
    assert decision["required_next_action"] == "NEW_PR_AND_NEW_PROTOCOL_REDESIGN"
    return decision


def _history_tables(
    logical_indices: range | tuple[int, ...], *, accepted_index: int = 1,
) -> tuple[list[dict[str, object]], list[dict[str, object]],
           list[dict[str, object]], list[dict[str, object]]]:
    attempts: list[dict[str, object]] = []
    generated: list[dict[str, object]] = []
    instances: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []
    for logical_index in logical_indices:
        pair_id = f"pair-{logical_index}"
        for attempt_index in range(accepted_index + 1):
            source_index = logical_index * 24 + attempt_index
            accepted = attempt_index == accepted_index
            attempts.append({
                "scenario_cell_id": "cell",
                "normalized_utilization": "1/5",
                "logical_taskset_index": logical_index,
                "logical_index": logical_index,
                "attempt_index": attempt_index,
                "source_taskset_index": source_index,
                "source_index": source_index,
                "generation_seed": f"seed-{source_index}",
                "source_taskset_id": f"source-{source_index}",
                "source_taskset_hash": f"source-hash-{source_index}",
                "attempt_status": "ACCEPTED" if accepted else "REJECTED",
                "paired_instance_id": pair_id if accepted else "",
            })
        accepted_source_index = logical_index * 24 + accepted_index
        generated.append({
            "scenario_cell_id": "cell",
            "logical_taskset_index": logical_index,
            "accepted_attempt_index": accepted_index,
            "source_taskset_id": f"source-{accepted_source_index}",
            "source_taskset_hash": f"source-hash-{accepted_source_index}",
            "generation_seed": f"seed-{accepted_source_index}",
            "taskset_id": f"taskset-{logical_index}",
            "taskset_hash": f"taskset-hash-{logical_index}",
        })
        instances.append({
            "paired_instance_id": pair_id,
            "scenario_cell_id": "cell",
            "normalized_utilization": "1/5",
            "logical_taskset_index": logical_index,
            "taskset_id": f"taskset-{logical_index}",
            "taskset_hash": f"taskset-hash-{logical_index}",
            "generation_seed": f"seed-{accepted_source_index}",
        })
        for scheduler_index, scheduler_id in enumerate(FROZEN_SCHEDULERS):
            requests.append({
                "request_id": f"request-{logical_index}-{scheduler_index}",
                "paired_instance_id": pair_id,
                "scenario_cell_id": "cell",
                "scheduler_id": scheduler_id,
                "taskset_id": f"taskset-{logical_index}",
                "taskset_hash": f"taskset-hash-{logical_index}",
                "generation_seed": f"seed-{accepted_source_index}",
            })
    return attempts, generated, instances, requests


def _history_audit(
    tables: tuple[list[dict[str, object]], list[dict[str, object]],
                  list[dict[str, object]], list[dict[str, object]]],
    *, tasksets_per_unit: int,
) -> tuple[list[str], dict[str, object]]:
    return audit_generation_attempt_history(
        *tables,
        scenario_cell_ids=["cell"],
        utilizations=["1/5"],
        logical_index_start=0,
        tasksets_per_unit=tasksets_per_unit,
        retry_limit=24,
        scheduler_ids=FROZEN_SCHEDULERS,
    )


def _calibration_summary_control(*, applicable: bool) -> dict[str, object]:
    return {
        **_control_metric_row(),
        "scenario_cell_id": "positive-control",
        "normalized_utilization": "1/5",
        "timing_subtype": "POSITIVE_SLACK_ENERGY_AVAILABLE",
        "configured_recovery_margin_ticks": 1,
        "interpolation_rho": "1/2",
        "nominal_energy_supply_ratio": "1/2",
        "target_recovery_contract_applicable": applicable,
    }


def test_full_calibration_config_freezes_18_units_900_pairs_2700_requests():
    config = load_ext1b_config(CONFIG_PATH)
    description = Ext1BRunner(config).describe()
    assert config["parameter_status"] == "CALIBRATION"
    assert config["grid"] == {
        "utilization_points": ["1/5", "2/5"],
        "tasksets_per_cell": 50,
        "base_seed": 981301,
        "seed_mode": "generation_dimensions",
        "taskset_index_start": 0,
    }
    assert config["execution"]["resume"] is False
    assert config["scenario"]["structural_retry_limit"] == 24
    assert description["cell_count"] == 18
    assert description["paired_instance_count"] == 900
    assert description["simulation_request_count"] == 2700


def test_primary_transition_threshold_is_exactly_95_percent():
    assert evaluate_primary_numeric_gate(
        [_raw_metric_row(transitions=48)], expected_denominator=50,
    )["passed"] is True
    failed = evaluate_primary_numeric_gate(
        [_raw_metric_row(transitions=47)], expected_denominator=50,
    )
    assert failed["passed"] is False
    assert (
        "target_positive_slack_transition_at_least_95_percent"
        in failed["failed_checks"]
    )


def test_complete_truthful_report_passes_primary_only():
    decision = decide_candidate(_acceptance_report())
    assert decision["decision"] == "CALIBRATION_PASS_PRIMARY_ONLY"
    assert decision["selected_candidate"] == FROZEN_PRIMARY_CANDIDATE
    assert decision["formal_profile_pr_permitted"] is True
    assert decision["parameter_status_formal_authorized"] is False
    assert decision["formal_profile_created"] is False
    assert decision["formal_profile_requirements"]["st_gate_metric"] == (
        "initial_target_job.target_positive_slack_transition"
    )


def test_internal_reaudit_failure_overrides_supplied_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    internal = _acceptance_report(primary_overrides=(
        {"target_positive_slack_transition_count": 47}, {},
    ))
    supplied = _acceptance_report()
    report_path = tmp_path / "acceptance.json"
    report_path.write_text(json.dumps(supplied), encoding="utf-8")
    monkeypatch.setattr(decision_module, "audit_calibration", lambda *_: internal)
    decision = decide_from_evidence(CONFIG_PATH, tmp_path, report_path)
    assert decision["decision"] == "REJECTED"
    assert decision["formal_profile_pr_permitted"] is False


def test_dataset_with_only_passed_field_is_rejected():
    report = _acceptance_report()
    report["dataset_integrity"] = {"passed": True}
    _assert_rejected(report)


def test_primary_gate_with_only_passed_field_is_rejected():
    report = _acceptance_report()
    report["primary_gate"] = {"passed": True}
    _assert_rejected(report)


def test_empty_per_utilization_is_rejected_without_exception():
    report = _acceptance_report()
    _primary(report)["per_utilization"] = {}
    _assert_rejected(report)


def test_empty_overall_is_rejected():
    report = _acceptance_report()
    _primary(report)["overall"] = {}
    _assert_rejected(report)


@pytest.mark.parametrize(
    ("mutation"),
    [
        pytest.param("missing-1/5", id="missing-utilization-1-5"),
        pytest.param("extra-3/5", id="extra-utilization-3-5"),
    ],
)
def test_utilization_domain_must_be_exact(mutation: str):
    report = _acceptance_report()
    per_utilization = _primary(report)["per_utilization"]
    if mutation == "missing-1/5":
        del per_utilization["1/5"]
    else:
        per_utilization["3/5"] = deepcopy(per_utilization["1/5"])
    _assert_rejected(report)


@pytest.mark.parametrize(
    ("location", "denominator"),
    [
        pytest.param("per-1/5", 49, id="per-utilization-denominator"),
        pytest.param("per-2/5", 51, id="second-utilization-denominator"),
        pytest.param("overall", 99, id="overall-denominator"),
    ],
)
def test_metric_denominator_must_be_exact(location: str, denominator: int):
    report = _acceptance_report()
    primary = _primary(report)
    metrics = (
        primary["overall"]
        if location == "overall"
        else primary["per_utilization"][location[4:]]
    )
    metrics["target_observation_denominator"] = denominator
    _assert_rejected(report)


def test_primary_47_of_50_is_rejected_even_when_alternates_pass():
    report = _acceptance_report(primary_overrides=(
        {"target_positive_slack_transition_count": 47}, {},
    ))
    decision = _assert_rejected(report)
    assert all(
        candidate["overall"]["target_positive_slack_transition"]["ratio"] == "1"
        for candidate in report["charging_candidates"]
        if candidate["parameters"] != FROZEN_PRIMARY_CANDIDATE
    )
    assert decision["selected_candidate"] is None


def test_primary_48_of_50_is_a_numeric_pass():
    report = _acceptance_report(primary_overrides=(
        {"target_positive_slack_transition_count": 48}, {},
    ))
    assert report["primary_gate"]["per_utilization"]["1/5"]["passed"] is True
    assert decide_candidate(report)["decision"] == "CALIBRATION_PASS_PRIMARY_ONLY"


def test_passed_true_with_nonempty_failed_checks_is_rejected():
    report = _acceptance_report()
    gate = report["primary_gate"]["per_utilization"]["1/5"]
    gate["failed_checks"] = ["target_audit_errors_zero"]
    _assert_rejected(report)


def test_passed_true_with_false_check_is_rejected():
    report = _acceptance_report()
    gate = report["primary_gate"]["per_utilization"]["1/5"]
    gate["checks"]["target_audit_errors_zero"] = False
    _assert_rejected(report)


def test_ratio_count_greater_than_denominator_is_rejected():
    report = _acceptance_report()
    metric = _primary(report)["per_utilization"]["1/5"][
        "target_positive_slack_transition"
    ]
    metric["count"] = 51
    _assert_rejected(report)


def test_ratio_text_must_equal_exact_count_over_denominator():
    report = _acceptance_report()
    metric = _primary(report)["per_utilization"]["1/5"][
        "target_positive_slack_transition"
    ]
    metric["ratio"] = "49/50"
    _assert_rejected(report)


@pytest.mark.parametrize(
    "candidate_count",
    [pytest.param(7, id="fewer-than-eight"), pytest.param(9, id="more-than-eight")],
)
def test_candidate_count_must_be_exactly_eight(candidate_count: int):
    report = _acceptance_report()
    candidates = report["charging_candidates"]
    if candidate_count == 7:
        candidates.pop()
    else:
        candidates.append(deepcopy(candidates[-1]))
    _assert_rejected(report)


def test_duplicate_candidate_is_rejected():
    report = _acceptance_report()
    candidates = report["charging_candidates"]
    candidates[-1]["parameters"] = deepcopy(candidates[-2]["parameters"])
    _assert_rejected(report)


def test_replaced_candidate_parameter_set_is_rejected():
    report = _acceptance_report()
    report["charging_candidates"][-1]["parameters"]["recovery_margin_ticks"] = 5
    _assert_rejected(report)


def test_two_precommitted_primary_roles_are_rejected():
    report = _acceptance_report()
    alternate = next(
        candidate for candidate in report["charging_candidates"]
        if candidate["parameters"] != FROZEN_PRIMARY_CANDIDATE
    )
    alternate["role"] = "PRECOMMITTED_PRIMARY"
    _assert_rejected(report)


def test_diagnostic_candidate_cannot_be_formal_selection_eligible():
    report = _acceptance_report()
    alternate = next(
        candidate for candidate in report["charging_candidates"]
        if candidate["parameters"] != FROZEN_PRIMARY_CANDIDATE
    )
    alternate["formal_selection_eligible"] = True
    _assert_rejected(report)


def test_supplied_acceptance_report_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    authoritative = _acceptance_report()
    supplied = deepcopy(authoritative)
    supplied["calibration_passed"] = False
    report_path = tmp_path / "acceptance.json"
    report_path.write_text(json.dumps(supplied), encoding="utf-8")
    monkeypatch.setattr(
        decision_module, "audit_calibration", lambda *_: authoritative,
    )
    decision = decide_from_evidence(CONFIG_PATH, tmp_path, report_path)
    assert decision["decision"] == "REJECTED"


def test_supplied_exact_canonical_copy_uses_internal_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    authoritative = _acceptance_report()
    report_path = tmp_path / "acceptance.json"
    report_path.write_text(
        json.dumps(authoritative, indent=4, sort_keys=False), encoding="utf-8",
    )
    monkeypatch.setattr(
        decision_module, "audit_calibration", lambda *_: authoritative,
    )
    decision = decide_from_evidence(CONFIG_PATH, tmp_path, report_path)
    assert decision["decision"] == "CALIBRATION_PASS_PRIMARY_ONLY"


def test_supplied_report_with_duplicate_json_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    report_path = tmp_path / "acceptance.json"
    report_path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
    monkeypatch.setattr(
        decision_module, "audit_calibration", lambda *_: _acceptance_report(),
    )
    assert decide_from_evidence(
        CONFIG_PATH, tmp_path, report_path,
    )["decision"] == "REJECTED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("runner_failure_count", 1, id="nonempty-failures-table"),
        pytest.param(
            "terminal_status_counts", {"SIM_RUNTIME_TIMEOUT": 2700},
            id="illegal-terminal-status",
        ),
        pytest.param("b3_timing_open_audit_count", 1, id="open-timing-audit"),
        pytest.param(
            "illegal_timing_transition_count", 1, id="illegal-transition",
        ),
        pytest.param(
            "unclassifiable_timing_transition_count", 1,
            id="unclassifiable-transition",
        ),
        pytest.param("file_hashes_valid", False, id="file-hash-failure"),
    ],
)
def test_dataset_integrity_failures_are_fail_closed(field: str, value: object):
    report = _acceptance_report()
    report["dataset_integrity"][field] = value
    _assert_rejected(report)


def test_accepted_capacity_violation_rejects_primary():
    report = _acceptance_report(primary_overrides=(
        {"accepted_capacity_infeasible_task_count": 1}, {},
    ))
    _assert_rejected(report)


def test_primary_pass_cannot_hide_missing_alternate_evidence():
    report = _acceptance_report()
    alternate_index = next(
        index for index, candidate in enumerate(report["charging_candidates"])
        if candidate["parameters"] != FROZEN_PRIMARY_CANDIDATE
    )
    report["charging_candidates"].pop(alternate_index)
    _assert_rejected(report)


def test_unknown_critical_field_is_rejected():
    report = _acceptance_report()
    _primary(report)["overall"]["unfrozen_metric"] = 0
    _assert_rejected(report)


def test_wrong_critical_type_is_rejected_without_exception():
    report = _acceptance_report()
    _primary(report)["per_utilization"]["1/5"][
        "target_observation_denominator"
    ] = "50"
    _assert_rejected(report)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("automatic-replacement-zero", id="false-is-not-integer-zero"),
        pytest.param("diagnostic-only-one", id="true-is-not-integer-one"),
        pytest.param("formal-created-zero", id="formal-false-type-is-strict"),
        pytest.param("checkpoint-stop-zero", id="checkpoint-false-type-is-strict"),
        pytest.param("frozen-margin-boolean", id="primary-integer-type-is-strict"),
        pytest.param("runner-count-float", id="runner-count-type-is-strict"),
    ],
)
def test_critical_scalar_types_are_strict(mutation: str):
    report = _acceptance_report()
    if mutation == "automatic-replacement-zero":
        report["automatic_parameter_replacement_permitted"] = 0
    elif mutation == "diagnostic-only-one":
        report["alternate_candidates_are_diagnostic_only"] = 1
    elif mutation == "formal-created-zero":
        report["formal_profile_created_or_authorized"] = 0
    elif mutation == "checkpoint-stop-zero":
        report["dataset_integrity"]["checkpoint"]["stop_requested"] = 0
    elif mutation == "frozen-margin-boolean":
        report["frozen_primary_candidate"]["recovery_margin_ticks"] = True
    else:
        report["config_audit"]["runner_description"]["cell_count"] = 18.0
    _assert_rejected(report)


def test_attempt_zero_rejected_one_accepted_passes():
    errors, result = _history_audit(
        _history_tables(range(1), accepted_index=1), tasksets_per_unit=1,
    )
    assert errors == []
    assert result["complete_attempt_history_group_count"] == 1
    assert result["attempt_sequence_audit_closed"] is True
    assert result["accepted_cross_table_identity_audit_closed"] is True


def test_attempt_sequence_with_gap_fails():
    tables = _history_tables(range(1), accepted_index=2)
    tables[0].pop(1)
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["missing_attempt_index_count"] == 1
    assert result["attempt_sequence_audit_closed"] is False


def test_attempt_after_accepted_fails():
    tables = _history_tables(range(1), accepted_index=1)
    extra = deepcopy(tables[0][0])
    extra.update({
        "attempt_index": 2,
        "source_taskset_index": 2,
        "source_index": 2,
        "source_taskset_id": "source-2",
        "source_taskset_hash": "source-hash-2",
        "generation_seed": "seed-2",
    })
    tables[0].append(extra)
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["accepted_not_last_count"] == 1


def test_multiple_accepted_attempts_fail():
    tables = _history_tables(range(1), accepted_index=1)
    tables[0][0]["attempt_status"] = "ACCEPTED"
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["multiple_accepted_count"] == 1


def test_all_rejected_attempts_fail():
    tables = _history_tables(range(1), accepted_index=1)
    tables[0][-1]["attempt_status"] = "REJECTED"
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["no_accepted_count"] == 1


def test_duplicate_attempt_index_fails():
    tables = _history_tables(range(1), accepted_index=1)
    tables[0].append(deepcopy(tables[0][0]))
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["duplicate_attempt_index_count"] == 1


def test_source_index_formula_mismatch_fails():
    tables = _history_tables(range(1), accepted_index=1)
    tables[0][0]["source_taskset_index"] = 7
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["attempt_sequence_audit_closed"] is False


def test_missing_logical_index_17_fails_domain_audit():
    tables = _history_tables(range(50), accepted_index=0)
    tables[0][:] = [
        row for row in tables[0] if row["logical_taskset_index"] != 17
    ]
    errors, result = _history_audit(tables, tasksets_per_unit=50)
    assert errors
    assert result["logical_attempt_group_count"] == 49
    assert result["logical_index_domain_closed"] is False


def test_extra_logical_index_50_fails_domain_audit():
    tables = _history_tables(range(51), accepted_index=0)
    errors, result = _history_audit(tables, tasksets_per_unit=50)
    assert errors
    assert result["logical_attempt_group_count"] == 51
    assert result["logical_index_domain_closed"] is False


def test_accepted_attempt_generated_identity_mismatch_fails():
    tables = _history_tables(range(1), accepted_index=1)
    tables[1][0]["source_taskset_hash"] = "tampered"
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["accepted_cross_table_identity_audit_closed"] is False


def test_generated_scenario_identity_mismatch_fails():
    tables = _history_tables(range(1), accepted_index=1)
    tables[2][0]["taskset_hash"] = "tampered"
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["accepted_cross_table_identity_audit_closed"] is False


def test_request_bound_to_rejected_attempt_fails():
    tables = _history_tables(range(1), accepted_index=1)
    tables[0][0]["paired_instance_id"] = "pair-0"
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["accepted_cross_table_identity_audit_closed"] is False


def test_complete_attempt_sequence_passes_even_when_csv_rows_are_reordered():
    tables = _history_tables(range(1), accepted_index=3)
    tables[0].reverse()
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors == []
    assert result["attempt_sequence_audit_closed"] is True


def test_duplicate_request_id_fails_cross_table_audit():
    tables = _history_tables(range(1), accepted_index=1)
    tables[3][1]["request_id"] = tables[3][0]["request_id"]
    errors, result = _history_audit(tables, tasksets_per_unit=1)
    assert errors
    assert result["accepted_cross_table_identity_audit_closed"] is False


def test_positive_control_zero_denominator_is_summarizable():
    metrics = summarize_metric_rows([_control_metric_row()])
    assert metrics["target_observation_denominator"] == 0
    assert metrics["target_positive_slack_transition"] == {
        "count": 0, "denominator": 0, "ratio": None,
    }
    assert metrics["structurally_accepted_count"] == 50


def test_positive_control_recovery_applicable_true_fails_grid_audit():
    errors, _charging, _controls = _calibration_grid_audit([
        _calibration_summary_control(applicable=True),
    ])
    assert "positive control is incorrectly recovery-applicable" in errors


def test_missing_output_file_fails_closed(tmp_path: Path):
    decision = decide_from_evidence(CONFIG_PATH, tmp_path)
    assert decision["decision"] == "REJECTED"
    assert decision["formal_profile_pr_permitted"] is False


def test_audit_does_not_modify_output_root_on_read_failure(tmp_path: Path):
    sentinel = tmp_path / "sentinel.bin"
    sentinel.write_bytes(b"immutable calibration evidence")
    before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    before_names = sorted(path.name for path in tmp_path.iterdir())
    with pytest.raises(Exception):
        audit_calibration(CONFIG_PATH, tmp_path)
    after = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    after_names = sorted(path.name for path in tmp_path.iterdir())
    assert after == before
    assert after_names == before_names
