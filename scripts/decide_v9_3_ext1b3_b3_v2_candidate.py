#!/usr/bin/env python3
"""Authoritative, fail-closed B3-v2 primary-candidate decision."""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_v9_3_ext1b3_b3_v2_calibration import (  # noqa: E402
    CALIBRATION_CLOSURE_FIELDS,
    EXPECTED_CALIBRATION_UNITS,
    EXPECTED_PAIRED_INSTANCES,
    EXPECTED_SCHEDULER_REQUESTS,
    FROZEN_ETAS,
    FROZEN_MARGINS,
    FROZEN_PRIMARY_CANDIDATE,
    FROZEN_RHOS,
    FROZEN_TASKSETS_PER_UNIT,
    FROZEN_UTILIZATIONS,
    PRIMARY_GATE_CHECK_KEYS,
    REPORT_SCHEMA,
    VALID_TERMINAL_STATUSES,
    audit_calibration,
)


DECISION_SCHEMA = "ASAP_BLOCK_V9_3_EXT1B3_B3_V2_CANDIDATE_DECISION_V1"
NO_SUBSTITUTION_RULE = (
    "Only the precommitted primary candidate may satisfy the calibration gate; "
    "all other charging combinations are diagnostic-only and can never replace it."
)
SIM_DEADLINE_MISS_USE = (
    "TERMINAL_STATUS_ONLY_NOT_SCHEDULABILITY_OR_PERFORMANCE_EVIDENCE"
)
ON_PRIMARY_FAILURE = (
    "RETAIN_EVIDENCE_AND_REDESIGN_BY_NEW_PR_AND_NEW_PROTOCOL"
)
TOP_LEVEL_FIELDS = {
    "schema", "protocol_state", "frozen_primary_candidate",
    "automatic_parameter_replacement_permitted",
    "alternate_candidates_are_diagnostic_only", "config_audit",
    "dataset_integrity", "positive_controls", "charging_candidates",
    "primary_gate", "calibration_passed",
    "formal_profile_created_or_authorized", "sim_deadline_miss_use",
    "on_primary_failure",
}
DATASET_FIELDS = {
    "passed", "errors", "checkpoint", "terminal_status_counts",
    "simulation_attempt_status_counts", "runner_failure_count",
    "illegal_timing_transition_count",
    "unclassifiable_timing_transition_count",
    "b3_timing_audit_error_count", "b3_timing_open_audit_count",
    "generated_taskset_count", "generation_attempt_count",
    "accepted_generation_attempt_count", "logical_attempt_group_count",
    "complete_attempt_history_group_count",
    "incomplete_attempt_history_group_count", "missing_attempt_index_count",
    "duplicate_attempt_index_count", "accepted_not_last_count",
    "multiple_accepted_count", "no_accepted_count",
    "logical_index_domain_closed", "attempt_sequence_audit_closed",
    "accepted_cross_table_identity_audit_closed", "paired_instance_count",
    "scheduler_request_count", "terminal_result_count",
    "calibration_unit_count", "file_hashes_valid",
    "sim_deadline_miss_is_schedulability_or_performance_evidence",
}
METRIC_FIELDS = {
    "target_observation_denominator", "target_initial_job_wait",
    "target_positive_slack_transition",
    "full_release_prefix_affordability", "runtime_prefix_audit_closed",
    "target_audit_closed", "target_audit_error_count",
    "prefix_audit_error_count",
    "later_target_job_positive_transition_count",
    "later_target_substitution_count",
    "non_target_positive_transition_count", "non_target_substitution_count",
    "activation_from_other_job_only_count",
    "transition_after_slack_exhaustion_count",
    "termination_without_transition_count",
    "accepted_capacity_infeasible_task_count",
    "accepted_capacity_infeasible_taskset_count",
    "structurally_accepted_count", "structural_rejection_attempt_count",
    "audit_closures", "all_audit_closures",
}
RATIO_METRIC_FIELDS = (
    "target_initial_job_wait", "target_positive_slack_transition",
    "full_release_prefix_affordability", "runtime_prefix_audit_closed",
    "target_audit_closed",
)
SCALAR_METRIC_FIELDS = tuple(sorted(METRIC_FIELDS.difference({
    "target_observation_denominator", "audit_closures",
    "all_audit_closures", *RATIO_METRIC_FIELDS,
})))
GATE_FIELDS = {"passed", "checks", "failed_checks", "metrics"}
CANDIDATE_FIELDS = {
    "parameters", "role", "formal_selection_eligible",
    "per_utilization", "overall",
}


class CandidateDecisionError(RuntimeError):
    """The supplied evidence cannot authorize a later formal-profile PR."""


def _fail_closed(errors: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": DECISION_SCHEMA,
        "decision": "REJECTED",
        "frozen_primary_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
        "selected_candidate": None,
        "automatic_parameter_replacement_permitted": False,
        "alternate_candidates_are_diagnostic_only": True,
        "formal_profile_pr_permitted": False,
        "parameter_status_formal_authorized": False,
        "formal_profile_created": False,
        "failed_results_must_be_retained": True,
        "required_next_action": "NEW_PR_AND_NEW_PROTOCOL_REDESIGN",
        "no_substitution_rule": NO_SUBSTITUTION_RULE,
        "errors": list(errors) or ["calibration evidence did not authorize PASS"],
    }


def _exact_keys(
    value: Any, expected: set[str], label: str, errors: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be a mapping")
        return None
    observed = set(value)
    if observed != expected:
        errors.append(
            f"{label} fields mismatch: missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )
        return None
    return value


def _strict_int(value: Any, label: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return None
    if value < 0:
        errors.append(f"{label} must be non-negative")
        return None
    return value


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else (
        f"{value.numerator}/{value.denominator}"
    )


def _ratio_document(count: int, denominator: int) -> dict[str, Any]:
    return {
        "count": count,
        "denominator": denominator,
        "ratio": (
            None if denominator == 0
            else _fraction_text(Fraction(count, denominator))
        ),
    }


def _validate_ratio(
    value: Any, expected_denominator: int, label: str, errors: list[str],
) -> None:
    row = _exact_keys(value, {"count", "denominator", "ratio"}, label, errors)
    if row is None:
        return
    count = _strict_int(row["count"], f"{label}.count", errors)
    denominator = _strict_int(
        row["denominator"], f"{label}.denominator", errors,
    )
    if count is None or denominator is None:
        return
    if denominator != expected_denominator:
        errors.append(f"{label}.denominator mismatch")
    if count > denominator:
        errors.append(f"{label}.count exceeds denominator")
        return
    expected_ratio = _ratio_document(count, denominator)["ratio"]
    if row["ratio"] != expected_ratio:
        errors.append(f"{label}.ratio does not match count/denominator")


def _validate_metrics(
    value: Any, expected_denominator: int, label: str, errors: list[str],
    *, expected_structurally_accepted: int | None = None,
) -> Mapping[str, Any] | None:
    metrics = _exact_keys(value, METRIC_FIELDS, label, errors)
    if metrics is None:
        return None
    denominator = _strict_int(
        metrics["target_observation_denominator"],
        f"{label}.target_observation_denominator",
        errors,
    )
    if denominator != expected_denominator:
        errors.append(f"{label}.target_observation_denominator mismatch")
    for field in RATIO_METRIC_FIELDS:
        _validate_ratio(
            metrics[field], expected_denominator, f"{label}.{field}", errors,
        )
    scalar_values: dict[str, int] = {}
    for field in SCALAR_METRIC_FIELDS:
        parsed = _strict_int(metrics[field], f"{label}.{field}", errors)
        if parsed is not None:
            scalar_values[field] = parsed
    for field in (
        "later_target_substitution_count", "non_target_substitution_count",
        "activation_from_other_job_only_count",
        "transition_after_slack_exhaustion_count",
        "termination_without_transition_count",
        "accepted_capacity_infeasible_taskset_count",
    ):
        if scalar_values.get(field, 0) > expected_denominator:
            errors.append(f"{label}.{field} exceeds denominator")
    expected_accepted = (
        expected_denominator
        if expected_structurally_accepted is None
        else expected_structurally_accepted
    )
    if scalar_values.get("structurally_accepted_count") != expected_accepted:
        errors.append(f"{label}.structurally_accepted_count mismatch")
    closures = _exact_keys(
        metrics["audit_closures"], set(CALIBRATION_CLOSURE_FIELDS),
        f"{label}.audit_closures", errors,
    )
    closure_value = None
    if closures is not None:
        if any(not isinstance(value, bool) for value in closures.values()):
            errors.append(f"{label}.audit_closures must contain booleans")
        else:
            closure_value = all(closures.values())
    if not isinstance(metrics["all_audit_closures"], bool):
        errors.append(f"{label}.all_audit_closures must be boolean")
    elif closure_value is not None and metrics["all_audit_closures"] != closure_value:
        errors.append(f"{label}.all_audit_closures is inconsistent")
    return metrics


def _combine_metrics(
    per_utilization: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [per_utilization[utilization] for utilization in FROZEN_UTILIZATIONS]
    denominator = sum(row["target_observation_denominator"] for row in rows)

    def ratio(field: str) -> dict[str, Any]:
        return _ratio_document(
            sum(row[field]["count"] for row in rows), denominator,
        )

    combined = {
        "target_observation_denominator": denominator,
        **{field: ratio(field) for field in RATIO_METRIC_FIELDS},
        **{
            field: sum(row[field] for row in rows)
            for field in SCALAR_METRIC_FIELDS
        },
        "audit_closures": {
            field: all(row["audit_closures"][field] for row in rows)
            for field in CALIBRATION_CLOSURE_FIELDS
        },
    }
    combined["all_audit_closures"] = all(
        combined["audit_closures"].values()
    )
    return combined


def _gate_checks(metrics: Mapping[str, Any], denominator: int) -> dict[str, bool]:
    return {
        "target_observation_denominator_exact": (
            metrics["target_observation_denominator"] == denominator
        ),
        "target_initial_job_wait_100_percent": (
            metrics["target_initial_job_wait"]["count"] == denominator
        ),
        "target_positive_slack_transition_at_least_95_percent": (
            Fraction(
                metrics["target_positive_slack_transition"]["count"],
                denominator,
            ) >= Fraction(19, 20)
        ),
        "full_release_prefix_affordability_100_percent": (
            metrics["full_release_prefix_affordability"]["count"]
            == denominator
        ),
        "runtime_prefix_audit_closed_100_percent": (
            metrics["runtime_prefix_audit_closed"]["count"] == denominator
        ),
        "target_audit_closed_100_percent": (
            metrics["target_audit_closed"]["count"] == denominator
        ),
        "target_audit_errors_zero": metrics["target_audit_error_count"] == 0,
        "prefix_audit_errors_zero": metrics["prefix_audit_error_count"] == 0,
        "later_target_substitution_zero": (
            metrics["later_target_substitution_count"] == 0
        ),
        "non_target_substitution_zero": (
            metrics["non_target_substitution_count"] == 0
        ),
        "transition_after_slack_exhaustion_zero": (
            metrics["transition_after_slack_exhaustion_count"] == 0
        ),
        "termination_without_transition_zero": (
            metrics["termination_without_transition_count"] == 0
        ),
        "accepted_capacity_infeasible_tasks_zero": (
            metrics["accepted_capacity_infeasible_task_count"] == 0
        ),
        "accepted_capacity_infeasible_tasksets_zero": (
            metrics["accepted_capacity_infeasible_taskset_count"] == 0
        ),
        "all_hash_pairing_workload_source_output_audits_closed": (
            metrics["all_audit_closures"] is True
        ),
    }


def _validate_gate(
    value: Any, authoritative_metrics: Mapping[str, Any], denominator: int,
    label: str, errors: list[str],
) -> bool:
    gate = _exact_keys(value, GATE_FIELDS, label, errors)
    if gate is None:
        return False
    before = len(errors)
    metrics = _validate_metrics(gate["metrics"], denominator, f"{label}.metrics", errors)
    if metrics is None or len(errors) != before:
        return False
    if metrics != authoritative_metrics:
        errors.append(f"{label}.metrics do not match charging-candidate metrics")
        return False
    checks = _exact_keys(
        gate["checks"], set(PRIMARY_GATE_CHECK_KEYS),
        f"{label}.checks", errors,
    )
    recomputed = _gate_checks(metrics, denominator)
    if checks is None or dict(checks) != recomputed:
        errors.append(f"{label}.checks do not match recomputed gates")
    expected_failed = [
        field for field in PRIMARY_GATE_CHECK_KEYS if not recomputed[field]
    ]
    if not isinstance(gate["failed_checks"], list) or gate["failed_checks"] != expected_failed:
        errors.append(f"{label}.failed_checks are inconsistent")
    expected_passed = all(recomputed.values()) and len(errors) == before
    if not isinstance(gate["passed"], bool) or gate["passed"] != expected_passed:
        errors.append(f"{label}.passed is inconsistent")
    return expected_passed


def _validate_status_counts(
    value: Any, label: str, errors: list[str],
) -> bool:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be a mapping")
        return False
    if not set(value).issubset(VALID_TERMINAL_STATUSES):
        errors.append(f"{label} contains an illegal terminal status")
        return False
    counts = [
        _strict_int(count, f"{label}.{status}", errors)
        for status, count in value.items()
    ]
    return all(count is not None for count in counts) and sum(counts) == (
        EXPECTED_SCHEDULER_REQUESTS
    )


def _validate_dataset(value: Any, errors: list[str]) -> bool:
    dataset = _exact_keys(value, DATASET_FIELDS, "dataset_integrity", errors)
    if dataset is None:
        return False
    before = len(errors)
    if dataset["passed"] is not True:
        errors.append("dataset_integrity.passed is not true")
    if not isinstance(dataset["errors"], list) or dataset["errors"]:
        errors.append("dataset_integrity.errors is not an empty list")
    expected_counts = {
        "generated_taskset_count": EXPECTED_PAIRED_INSTANCES,
        "accepted_generation_attempt_count": EXPECTED_PAIRED_INSTANCES,
        "logical_attempt_group_count": EXPECTED_PAIRED_INSTANCES,
        "complete_attempt_history_group_count": EXPECTED_PAIRED_INSTANCES,
        "incomplete_attempt_history_group_count": 0,
        "missing_attempt_index_count": 0,
        "duplicate_attempt_index_count": 0,
        "accepted_not_last_count": 0,
        "multiple_accepted_count": 0,
        "no_accepted_count": 0,
        "paired_instance_count": EXPECTED_PAIRED_INSTANCES,
        "scheduler_request_count": EXPECTED_SCHEDULER_REQUESTS,
        "terminal_result_count": EXPECTED_SCHEDULER_REQUESTS,
        "calibration_unit_count": EXPECTED_CALIBRATION_UNITS,
        "runner_failure_count": 0,
        "illegal_timing_transition_count": 0,
        "unclassifiable_timing_transition_count": 0,
        "b3_timing_audit_error_count": 0,
        "b3_timing_open_audit_count": 0,
    }
    for field, expected in expected_counts.items():
        observed = _strict_int(dataset[field], f"dataset_integrity.{field}", errors)
        if observed is not None and observed != expected:
            errors.append(f"dataset_integrity.{field} mismatch")
    generation_attempt_count = _strict_int(
        dataset["generation_attempt_count"],
        "dataset_integrity.generation_attempt_count", errors,
    )
    if generation_attempt_count is not None and not (
        EXPECTED_PAIRED_INSTANCES
        <= generation_attempt_count
        <= EXPECTED_PAIRED_INSTANCES * 24
    ):
        errors.append("dataset_integrity.generation_attempt_count is invalid")
    for field in (
        "logical_index_domain_closed", "attempt_sequence_audit_closed",
        "accepted_cross_table_identity_audit_closed", "file_hashes_valid",
    ):
        if dataset[field] is not True:
            errors.append(f"dataset_integrity.{field} is not true")
    if dataset[
        "sim_deadline_miss_is_schedulability_or_performance_evidence"
    ] is not False:
        errors.append("dataset_integrity misuses SIM_DEADLINE_MISS")
    checkpoint = _exact_keys(
        dataset["checkpoint"],
        {
            "schema", "config_hash", "requested", "terminal", "pending",
            "stop_requested", "updated_at_utc",
        },
        "dataset_integrity.checkpoint", errors,
    )
    if checkpoint is not None:
        if checkpoint["schema"] != "ASAP_BLOCK_V9_3_EXT1B_CHECKPOINT_V1":
            errors.append("dataset_integrity.checkpoint.schema mismatch")
        if (
            not isinstance(checkpoint["config_hash"], str)
            or not checkpoint["config_hash"]
        ):
            errors.append("dataset_integrity.checkpoint.config_hash is invalid")
        if (
            not isinstance(checkpoint["updated_at_utc"], str)
            or not checkpoint["updated_at_utc"]
        ):
            errors.append("dataset_integrity.checkpoint.updated_at_utc is invalid")
        checkpoint_expected = {
            "requested": EXPECTED_SCHEDULER_REQUESTS,
            "terminal": EXPECTED_SCHEDULER_REQUESTS,
            "pending": 0,
        }
        for field, expected in checkpoint_expected.items():
            observed = _strict_int(
                checkpoint[field], f"dataset_integrity.checkpoint.{field}",
                errors,
            )
            if observed is not None and observed != expected:
                errors.append(f"dataset_integrity.checkpoint.{field} mismatch")
        if checkpoint["stop_requested"] is not False:
            errors.append("dataset_integrity.checkpoint.stop_requested mismatch")
    if not _validate_status_counts(
        dataset["terminal_status_counts"],
        "dataset_integrity.terminal_status_counts", errors,
    ):
        errors.append("terminal status counts are incomplete")
    if not _validate_status_counts(
        dataset["simulation_attempt_status_counts"],
        "dataset_integrity.simulation_attempt_status_counts", errors,
    ):
        errors.append("simulation-attempt status counts are incomplete")
    return len(errors) == before


def _validate_candidates(
    value: Any, errors: list[str],
) -> tuple[Mapping[str, Any] | None, bool]:
    if not isinstance(value, list) or len(value) != 8:
        errors.append("charging_candidates must contain exactly eight candidates")
        return None, False
    expected_parameters = {
        (margin, rho, eta)
        for margin in FROZEN_MARGINS
        for rho in FROZEN_RHOS
        for eta in FROZEN_ETAS
    }
    observed_parameters: list[tuple[int, str, str]] = []
    primary: Mapping[str, Any] | None = None
    candidates_closed = True
    for index, value_row in enumerate(value):
        label = f"charging_candidates[{index}]"
        row = _exact_keys(value_row, CANDIDATE_FIELDS, label, errors)
        if row is None:
            candidates_closed = False
            continue
        parameters = _exact_keys(
            row["parameters"], set(FROZEN_PRIMARY_CANDIDATE),
            f"{label}.parameters", errors,
        )
        if parameters is None:
            candidates_closed = False
            continue
        key = (
            parameters["recovery_margin_ticks"],
            parameters["interpolation_rho"],
            parameters["nominal_energy_supply_ratio"],
        )
        if (
            isinstance(key[0], bool) or not isinstance(key[0], int)
            or not isinstance(key[1], str) or not isinstance(key[2], str)
        ):
            errors.append(f"{label}.parameters have invalid types")
            candidates_closed = False
            continue
        observed_parameters.append(key)
        is_primary = dict(parameters) == FROZEN_PRIMARY_CANDIDATE
        expected_role = "PRECOMMITTED_PRIMARY" if is_primary else "DIAGNOSTIC_ONLY"
        if row["role"] != expected_role:
            errors.append(f"{label}.role mismatch")
            candidates_closed = False
        if row["formal_selection_eligible"] is not is_primary:
            errors.append(f"{label}.formal_selection_eligible mismatch")
            candidates_closed = False
        per_utilization = _exact_keys(
            row["per_utilization"], set(FROZEN_UTILIZATIONS),
            f"{label}.per_utilization", errors,
        )
        if per_utilization is None:
            candidates_closed = False
            continue
        parsed_per: dict[str, Mapping[str, Any]] = {}
        for utilization in FROZEN_UTILIZATIONS:
            metric_errors_before = len(errors)
            metrics = _validate_metrics(
                per_utilization[utilization], FROZEN_TASKSETS_PER_UNIT,
                f"{label}.per_utilization[{utilization}]", errors,
            )
            if metrics is not None and len(errors) == metric_errors_before:
                parsed_per[utilization] = metrics
                if metrics["all_audit_closures"] is not True:
                    errors.append(f"{label} has an open per-utilization audit")
                    candidates_closed = False
            else:
                candidates_closed = False
        overall_errors_before = len(errors)
        overall = _validate_metrics(
            row["overall"],
            FROZEN_TASKSETS_PER_UNIT * len(FROZEN_UTILIZATIONS),
            f"{label}.overall", errors,
        )
        overall_valid = (
            overall is not None and len(errors) == overall_errors_before
        )
        if not overall_valid:
            candidates_closed = False
        if len(parsed_per) == len(FROZEN_UTILIZATIONS) and overall_valid:
            if dict(overall) != _combine_metrics(parsed_per):
                errors.append(f"{label}.overall is not the exact utilization sum")
                candidates_closed = False
        if is_primary:
            if primary is not None:
                errors.append("multiple PRECOMMITTED_PRIMARY candidates")
                candidates_closed = False
            primary = row
    if set(observed_parameters) != expected_parameters:
        errors.append("charging-candidate parameter set is incomplete or changed")
        candidates_closed = False
    if len(observed_parameters) != len(set(observed_parameters)):
        errors.append("charging-candidate parameter set contains duplicates")
        candidates_closed = False
    if primary is None:
        errors.append("frozen primary candidate is missing")
        candidates_closed = False
    return primary, candidates_closed


def _validate_positive_controls(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != len(FROZEN_UTILIZATIONS):
        errors.append("positive_controls must contain one row per utilization")
        return
    observed = set()
    for index, control in enumerate(value):
        row = _exact_keys(
            control, {"normalized_utilization", "metrics"},
            f"positive_controls[{index}]", errors,
        )
        if row is None:
            continue
        utilization = row["normalized_utilization"]
        if not isinstance(utilization, str):
            errors.append(
                f"positive_controls[{index}].normalized_utilization "
                "must be a string"
            )
            continue
        observed.add(utilization)
        _validate_metrics(
            row["metrics"], 0, f"positive_controls[{index}].metrics", errors,
            expected_structurally_accepted=FROZEN_TASKSETS_PER_UNIT,
        )
    if observed != set(FROZEN_UTILIZATIONS):
        errors.append("positive-control utilization set mismatch")


def decide_candidate(report: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly recompute every authorization condition from report metrics."""

    errors: list[str] = []
    top = _exact_keys(report, TOP_LEVEL_FIELDS, "acceptance report", errors)
    if top is None:
        return _fail_closed(errors)
    top_expected = {
        "schema": REPORT_SCHEMA,
        "protocol_state": "CALIBRATION_ONLY",
        "sim_deadline_miss_use": SIM_DEADLINE_MISS_USE,
        "on_primary_failure": ON_PRIMARY_FAILURE,
    }
    for field, expected in top_expected.items():
        if top[field] != expected:
            errors.append(f"acceptance report {field} mismatch")
    frozen = _exact_keys(
        top["frozen_primary_candidate"], set(FROZEN_PRIMARY_CANDIDATE),
        "frozen_primary_candidate", errors,
    )
    if frozen is not None and (
        isinstance(frozen["recovery_margin_ticks"], bool)
        or not isinstance(frozen["recovery_margin_ticks"], int)
        or not isinstance(frozen["interpolation_rho"], str)
        or not isinstance(frozen["nominal_energy_supply_ratio"], str)
        or dict(frozen) != FROZEN_PRIMARY_CANDIDATE
    ):
        errors.append("acceptance report frozen_primary_candidate mismatch")
    for field, expected in (
        ("automatic_parameter_replacement_permitted", False),
        ("alternate_candidates_are_diagnostic_only", True),
        ("formal_profile_created_or_authorized", False),
    ):
        if top[field] is not expected:
            errors.append(f"acceptance report {field} mismatch")
    config_audit = top["config_audit"]
    if not isinstance(config_audit, Mapping):
        errors.append("config_audit must be a mapping")
    else:
        description = config_audit.get("runner_description")
        expected_description = {
            "cell_count": EXPECTED_CALIBRATION_UNITS,
            "paired_instance_count": EXPECTED_PAIRED_INSTANCES,
            "simulation_request_count": EXPECTED_SCHEDULER_REQUESTS,
        }
        description_closed = isinstance(description, Mapping)
        if description_closed:
            for field, expected in expected_description.items():
                observed = _strict_int(
                    description.get(field), f"config_audit.{field}", errors,
                )
                if observed != expected:
                    description_closed = False
        if not description_closed:
            errors.append("config_audit runner scale mismatch")
    dataset_passed = _validate_dataset(top["dataset_integrity"], errors)
    _validate_positive_controls(top["positive_controls"], errors)
    primary_candidate, candidates_closed = _validate_candidates(
        top["charging_candidates"], errors,
    )

    numeric_gate_passed = False
    primary_gate = _exact_keys(
        top["primary_gate"],
        {"passed", "dataset_integrity_passed", "numeric_gate_passed",
         "per_utilization", "overall"},
        "primary_gate", errors,
    )
    if primary_gate is not None and primary_candidate is not None:
        per_metrics = primary_candidate.get("per_utilization")
        per_gate = _exact_keys(
            primary_gate["per_utilization"], set(FROZEN_UTILIZATIONS),
            "primary_gate.per_utilization", errors,
        )
        utilization_passes = []
        per_metrics_closed = (
            isinstance(per_metrics, Mapping)
            and set(per_metrics) == set(FROZEN_UTILIZATIONS)
        )
        if per_gate is not None and per_metrics_closed:
            for utilization in FROZEN_UTILIZATIONS:
                utilization_passes.append(_validate_gate(
                    per_gate[utilization], per_metrics[utilization],
                    FROZEN_TASKSETS_PER_UNIT,
                    f"primary_gate.per_utilization[{utilization}]", errors,
                ))
        primary_overall = primary_candidate.get("overall")
        overall_passed = False
        if isinstance(primary_overall, Mapping):
            overall_passed = _validate_gate(
                primary_gate["overall"], primary_overall,
                FROZEN_TASKSETS_PER_UNIT * len(FROZEN_UTILIZATIONS),
                "primary_gate.overall", errors,
            )
        else:
            errors.append("primary candidate overall metrics must be a mapping")
        numeric_gate_passed = (
            len(utilization_passes) == len(FROZEN_UTILIZATIONS)
            and all(utilization_passes)
            and overall_passed
        )
        expected_primary_passed = (
            dataset_passed and candidates_closed and numeric_gate_passed
        )
        consistency = {
            "dataset_integrity_passed": dataset_passed,
            "numeric_gate_passed": numeric_gate_passed,
            "passed": expected_primary_passed,
        }
        for field, expected in consistency.items():
            if primary_gate[field] is not expected:
                errors.append(f"primary_gate.{field} is inconsistent")
        if top["calibration_passed"] is not expected_primary_passed:
            errors.append("calibration_passed is inconsistent")
    else:
        expected_primary_passed = False
        if top["calibration_passed"] is not False:
            errors.append("calibration_passed cannot be true without a complete gate")

    if errors or not expected_primary_passed:
        if not errors:
            errors.append("the frozen primary candidate did not pass every gate")
        return _fail_closed(errors)
    return {
        "schema": DECISION_SCHEMA,
        "decision": "CALIBRATION_PASS_PRIMARY_ONLY",
        "frozen_primary_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
        "selected_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
        "automatic_parameter_replacement_permitted": False,
        "alternate_candidates_are_diagnostic_only": True,
        "formal_profile_pr_permitted": True,
        "parameter_status_formal_authorized": False,
        "formal_profile_created": False,
        "failed_results_must_be_retained": True,
        "required_next_action": "SEPARATE_PR_MAY_DEFINE_NEW_FORMAL_PROFILE",
        "no_substitution_rule": NO_SUBSTITUTION_RULE,
        "formal_profile_requirements": {
            "parameter_status": "FORMAL",
            "new_experiment_id": True,
            "new_formal_seed_space": True,
            "new_base_seed": True,
            "new_bootstrap_seed": True,
            "new_output_root": True,
            "new_taskset_store": True,
            "tasksets_per_cell": 200,
            "utilization_count": 2,
            "timing_cell_count": 2,
            "paired_instance_count": 800,
            "scheduler_request_count": 2400,
            "st_gate_metric": "initial_target_job.target_positive_slack_transition",
        },
        "errors": [],
    }


def _strict_json_object(path: Path) -> dict[str, Any]:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateDecisionError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateDecisionError("cannot read acceptance report") from exc
    if not isinstance(value, dict):
        raise CandidateDecisionError("acceptance report must be a JSON object")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def decide_from_evidence(
    config_path: Path, output_root: Path,
    acceptance_report: Path | None = None,
) -> dict[str, Any]:
    """Re-audit raw evidence; an optional report is equality-only evidence."""

    try:
        authoritative = audit_calibration(config_path, output_root.resolve())
    except Exception as exc:
        return _fail_closed([f"authoritative calibration audit failed: {exc}"])
    if acceptance_report is not None:
        try:
            supplied = _strict_json_object(acceptance_report)
        except CandidateDecisionError as exc:
            return _fail_closed([str(exc)])
        if _canonical_json(supplied) != _canonical_json(authoritative):
            return _fail_closed([
                "supplied acceptance report differs from authoritative re-audit"
            ])
    return decide_candidate(authoritative)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--acceptance-report", type=Path)
    args = parser.parse_args()
    decision = decide_from_evidence(
        args.config, args.output_root, args.acceptance_report,
    )
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if decision["formal_profile_pr_permitted"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
