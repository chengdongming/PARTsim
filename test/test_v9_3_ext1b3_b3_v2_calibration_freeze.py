"""Frozen B3-v2 calibration and no-substitution decision tests."""

from __future__ import annotations

from pathlib import Path

from experiments.v9_3.ext1b_config import load_ext1b_config
from experiments.v9_3.ext1b_engine import Ext1BRunner
from scripts.audit_v9_3_ext1b3_b3_v2_calibration import (
    CALIBRATION_CLOSURE_FIELDS,
    FROZEN_PRIMARY_CANDIDATE,
    REPORT_SCHEMA,
    evaluate_primary_numeric_gate,
)
from scripts.decide_v9_3_ext1b3_b3_v2_candidate import decide_candidate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    PROJECT_ROOT
    / "configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml"
)


def _metric_row(*, transitions: int = 50) -> dict[str, object]:
    row: dict[str, object] = {
        "target_observation_denominator": 50,
        "target_wait_observed_count": 50,
        "target_positive_slack_transition_count": transitions,
        "full_release_prefix_affordable_count": 50,
        "recovery_prefix_audit_closed_count": 50,
        "target_audit_closed_count": 50,
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
        "structurally_accepted_count": 50,
        "structural_rejection_attempt_count": 0,
    }
    row.update({field: True for field in CALIBRATION_CLOSURE_FIELDS})
    return row


def _candidate(
    parameters: dict[str, object], *, primary: bool = False,
) -> dict[str, object]:
    return {
        "parameters": parameters,
        "role": "PRECOMMITTED_PRIMARY" if primary else "DIAGNOSTIC_ONLY",
        "formal_selection_eligible": primary,
        "per_utilization": {},
        "overall": {},
        # Deliberately tempting alternate outcome; it has no decision authority.
        "diagnostic_numeric_gate_passed": True,
    }


def _acceptance_report(*, primary_passed: bool) -> dict[str, object]:
    return {
        "schema": REPORT_SCHEMA,
        "frozen_primary_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
        "automatic_parameter_replacement_permitted": False,
        "alternate_candidates_are_diagnostic_only": True,
        "dataset_integrity": {"passed": True},
        "primary_gate": {"passed": primary_passed},
        "calibration_passed": primary_passed,
        "formal_profile_created_or_authorized": False,
        "charging_candidates": [
            _candidate(dict(FROZEN_PRIMARY_CANDIDATE), primary=True),
            _candidate({
                "recovery_margin_ticks": 3,
                "interpolation_rho": "1/3",
                "nominal_energy_supply_ratio": "1/3",
            }),
        ],
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
        [_metric_row(transitions=48)], expected_denominator=50,
    )["passed"] is True
    failed = evaluate_primary_numeric_gate(
        [_metric_row(transitions=47)], expected_denominator=50,
    )
    assert failed["passed"] is False
    assert (
        "target_positive_slack_transition_at_least_95_percent"
        in failed["failed_checks"]
    )


def test_failed_primary_never_auto_selects_better_alternate():
    decision = decide_candidate(_acceptance_report(primary_passed=False))
    assert decision["decision"] == "REJECTED"
    assert decision["selected_candidate"] is None
    assert decision["automatic_parameter_replacement_permitted"] is False
    assert decision["formal_profile_pr_permitted"] is False
    assert decision["parameter_status_formal_authorized"] is False
    assert decision["required_next_action"] == "NEW_PR_AND_NEW_PROTOCOL_REDESIGN"


def test_pass_only_permits_a_separate_formal_profile_pr():
    decision = decide_candidate(_acceptance_report(primary_passed=True))
    assert decision["selected_candidate"] == FROZEN_PRIMARY_CANDIDATE
    assert decision["formal_profile_pr_permitted"] is True
    assert decision["parameter_status_formal_authorized"] is False
    assert decision["formal_profile_created"] is False
    assert decision["formal_profile_requirements"]["st_gate_metric"] == (
        "initial_target_job.target_positive_slack_transition"
    )
