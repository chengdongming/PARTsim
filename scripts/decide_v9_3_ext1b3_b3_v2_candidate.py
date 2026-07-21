#!/usr/bin/env python3
"""Fail-closed B3-v2 decision with an absolute no-substitution rule."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_v9_3_ext1b3_b3_v2_calibration import (  # noqa: E402
    FROZEN_PRIMARY_CANDIDATE,
    REPORT_SCHEMA,
)


DECISION_SCHEMA = "ASAP_BLOCK_V9_3_EXT1B3_B3_V2_CANDIDATE_DECISION_V1"
NO_SUBSTITUTION_RULE = (
    "Only the precommitted primary candidate may satisfy the calibration gate; "
    "all other charging combinations are diagnostic-only and can never replace it."
)


class CandidateDecisionError(RuntimeError):
    """The acceptance report cannot authorize even a later formal-profile PR."""


def _fail_closed(errors: list[str]) -> dict[str, Any]:
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
        "errors": errors,
    }


def decide_candidate(report: Mapping[str, Any]) -> dict[str, Any]:
    """Decide solely from the frozen primary; alternate results are ignored."""

    errors: list[str] = []
    if report.get("schema") != REPORT_SCHEMA:
        errors.append("acceptance report schema mismatch")
    if report.get("frozen_primary_candidate") != FROZEN_PRIMARY_CANDIDATE:
        errors.append("acceptance report changed the precommitted primary candidate")
    if report.get("automatic_parameter_replacement_permitted") is not False:
        errors.append("acceptance report does not enforce the no-substitution rule")
    if report.get("alternate_candidates_are_diagnostic_only") is not True:
        errors.append("acceptance report gives alternates selection authority")

    dataset = report.get("dataset_integrity")
    primary_gate = report.get("primary_gate")
    if not isinstance(dataset, Mapping) or dataset.get("passed") is not True:
        errors.append("calibration dataset integrity did not close")
    if not isinstance(primary_gate, Mapping) or primary_gate.get("passed") is not True:
        errors.append("precommitted primary candidate failed its gate")
    if report.get("calibration_passed") is not True:
        errors.append("calibration acceptance is not PASS")
    if report.get("formal_profile_created_or_authorized") is not False:
        errors.append("calibration report improperly claims FORMAL authorization")

    candidates = report.get("charging_candidates")
    if not isinstance(candidates, list):
        errors.append("acceptance report lacks the complete charging-candidate report")
    else:
        primary_rows = [
            row for row in candidates
            if isinstance(row, Mapping)
            and row.get("parameters") == FROZEN_PRIMARY_CANDIDATE
            and row.get("role") == "PRECOMMITTED_PRIMARY"
            and row.get("formal_selection_eligible") is True
        ]
        alternate_selection_rows = [
            row for row in candidates
            if isinstance(row, Mapping)
            and row.get("parameters") != FROZEN_PRIMARY_CANDIDATE
            and row.get("formal_selection_eligible") is not False
        ]
        if len(primary_rows) != 1:
            errors.append("acceptance report does not identify exactly one frozen primary")
        if alternate_selection_rows:
            errors.append("an alternate candidate was marked selection-eligible")

    if errors:
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


def _load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateDecisionError("cannot read acceptance report") from exc
    if not isinstance(value, dict):
        raise CandidateDecisionError("acceptance report must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-report", type=Path, required=True)
    args = parser.parse_args()
    try:
        decision = decide_candidate(_load_report(args.acceptance_report))
    except CandidateDecisionError as exc:
        decision = _fail_closed([str(exc)])
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if decision["formal_profile_pr_permitted"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
