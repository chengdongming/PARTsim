#!/usr/bin/env python3
"""Build/check reviewed CORE-0A authorization artifacts without running a pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_core0a_execution_authorization_v2 import (
    acquire_core0a_run_claim_v2,
    build_core0a_candidate_review_receipt_v2,
    build_core0a_executable_engineering_authorization_v2,
    complete_core0a_run_v2,
    fail_core0a_run_v2,
    preflight_core0a_engineering_pilot_execution_v2,
    preflight_core0a_engineering_pilot_resume_v2,
    validate_core0a_candidate_review_receipt_v2,
    validate_core0a_executable_engineering_authorization_v2,
    validate_core0a_nonce_consumption_receipt_v2,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", required=True,
        choices=(
            "receipt", "receipt-check",
            "authorization", "authorization-check",
            "preflight", "resume-preflight",
            "acquire", "complete", "fail", "consumption-check",
        ),
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--review-receipt", type=Path)
    parser.add_argument("--review-report", type=Path)
    parser.add_argument("--reviewer-label")
    parser.add_argument("--reviewed-at")
    parser.add_argument("--executable-authorization", type=Path)
    parser.add_argument("--verification-time")
    parser.add_argument("--current-utc")
    parser.add_argument("--started-at")
    parser.add_argument("--completed-at")
    parser.add_argument("--portable-bundle", type=Path, required=True)
    parser.add_argument("--selection-artifact", type=Path, required=True)
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--production-manifest", type=Path, required=True)
    parser.add_argument("--deployment-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--deployment-workspace-root", type=Path, required=True)
    args = parser.parse_args()
    common = {
        "portable_bundle_path": args.portable_bundle,
        "selection_artifact_path": args.selection_artifact,
        "candidate_config_path": args.candidate_config,
        "production_manifest_path": args.production_manifest,
        "deployment_manifest_path": args.deployment_manifest,
        "source_root": args.source_root,
        "deployment_workspace_root": args.deployment_workspace_root,
    }
    try:
        if args.mode == "receipt":
            receipt = build_core0a_candidate_review_receipt_v2(
                candidate_path=args.candidate,
                review_report_path=args.review_report,
                reviewer_label=args.reviewer_label,
                reviewed_at=args.reviewed_at,
                review_receipt_output_path=args.review_receipt,
                **common,
            )
            result = {
                "mode": args.mode,
                "review_receipt_identity": receipt[
                    "review_receipt_identity"
                ],
                "status": receipt["status"],
                "execution_environment_classification": receipt[
                    "execution_environment_classification"
                ],
            }
        elif args.mode == "receipt-check":
            receipt = validate_core0a_candidate_review_receipt_v2(
                review_receipt_path=args.review_receipt,
                candidate_path=args.candidate,
                **common,
            )
            result = {
                "mode": args.mode,
                "review_receipt_identity": receipt[
                    "review_receipt_identity"
                ],
                "status": receipt["status"],
                "execution_environment_classification": receipt[
                    "execution_environment_classification"
                ],
            }
        elif args.mode == "authorization":
            authorization = build_core0a_executable_engineering_authorization_v2(
                candidate_path=args.candidate,
                review_receipt_path=args.review_receipt,
                authorization_output_path=args.executable_authorization,
                verification_time=args.verification_time,
                **common,
            )
            result = {
                "mode": args.mode,
                "execution_environment_classification": authorization[
                    "execution_environment_classification"
                ],
                "executable_authorization_identity": authorization[
                    "executable_authorization_identity"
                ],
                "status": authorization["status"],
            }
        elif args.mode == "authorization-check":
            validated = validate_core0a_executable_engineering_authorization_v2(
                executable_authorization_path=args.executable_authorization,
                candidate_path=args.candidate,
                review_receipt_path=args.review_receipt,
                **common,
            )
            result = {
                "mode": args.mode,
                "execution_environment_classification": validated.authorization[
                    "execution_environment_classification"
                ],
                "executable_authorization_identity": validated.authorization[
                    "executable_authorization_identity"
                ],
                "status": validated.authorization["status"],
            }
        elif args.mode in {"preflight", "resume-preflight"}:
            preflight = (
                preflight_core0a_engineering_pilot_execution_v2
                if args.mode == "preflight"
                else preflight_core0a_engineering_pilot_resume_v2
            )
            context = preflight(
                executable_authorization_path=args.executable_authorization,
                candidate_path=args.candidate,
                review_receipt_path=args.review_receipt,
                current_utc=args.current_utc,
                **common,
            )
            result = {
                "mode": args.mode,
                "authorization_identity": context.authorization_identity,
                "validation_passed": context.validation_passed,
                "run_state": context.run_state,
                "claim_acquired": context.claim_acquired,
                "new_run_eligible": context.new_run_eligible,
                "resume_only": context.resume_only,
                "runner_invocation_allowed": (
                    context.runner_invocation_allowed
                ),
                "execution_environment_classification": (
                    context.execution_environment_classification
                ),
                "execution_mode": context.execution_mode,
            }
        elif args.mode == "acquire":
            context = acquire_core0a_run_claim_v2(
                executable_authorization_path=args.executable_authorization,
                candidate_path=args.candidate,
                review_receipt_path=args.review_receipt,
                started_at=args.started_at,
                **common,
            )
            result = {
                "mode": args.mode,
                "authorization_identity": context.authorization_identity,
                "validation_passed": context.validation_passed,
                "run_state": context.run_state,
                "claim_acquired": context.claim_acquired,
                "new_run_eligible": context.new_run_eligible,
                "resume_only": context.resume_only,
                "runner_invocation_allowed": (
                    context.runner_invocation_allowed
                ),
                "execution_environment_classification": (
                    context.execution_environment_classification
                ),
            }
        elif args.mode in {"complete", "fail"}:
            transition = (
                complete_core0a_run_v2
                if args.mode == "complete"
                else fail_core0a_run_v2
            )
            receipt = transition(
                executable_authorization_path=args.executable_authorization,
                candidate_path=args.candidate,
                review_receipt_path=args.review_receipt,
                completed_at=args.completed_at,
                **common,
            )
            result = {
                "mode": args.mode,
                "consumption_receipt_identity": receipt[
                    "consumption_receipt_identity"
                ],
                "status": receipt["status"],
                "execution_environment_classification": receipt[
                    "execution_environment_classification"
                ],
            }
        else:
            receipt = validate_core0a_nonce_consumption_receipt_v2(
                executable_authorization_path=args.executable_authorization,
                candidate_path=args.candidate,
                review_receipt_path=args.review_receipt,
                **common,
            )
            result = {
                "mode": args.mode,
                "consumption_receipt_identity": receipt[
                    "consumption_receipt_identity"
                ],
                "status": receipt["status"],
                "execution_environment_classification": receipt[
                    "execution_environment_classification"
                ],
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
