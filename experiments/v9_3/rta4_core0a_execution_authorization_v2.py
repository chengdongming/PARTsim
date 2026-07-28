"""Reviewed execution authorization contracts for the RTA4 CORE-0A pilot.

This module binds one validated candidate to one independent review receipt.
It contains no pilot runner.  Development tests use an explicit
``TEST_ONLY_NON_EXECUTABLE_FIXTURE`` classification; a real authorization must
be built later from the exact reviewed deployment on AutoDL.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Dict, Mapping

from .rta4_core0a_authorization_v2 import (
    _authorization_output_path,
    _canonical_timestamp,
    _write_atomic_canonical_json,
    validate_core0a_authorization_candidate_v2,
)
from .rta4_core0a_pilot_v2 import (
    AUTHORIZED_CORE0A_ENGINEERING_PILOT,
    CORE0A_AUTODL_CONTROLLED_EXECUTION_ENVIRONMENT_CLASSIFICATION,
    CORE0A_AUTHORIZATION_SCOPE,
    CORE0A_MAX_RUNS,
    CORE0A_TEST_ONLY_EXECUTION_ENVIRONMENT_CLASSIFICATION,
    EXPECTED_EXECUTION_COUNT,
    RTA4Core0APilotV2Error,
    ValidatedCore0ADeployment,
    canonical_json_bytes,
    load_strict_canonical_json,
    validate_autodl_deployment_manifest_v2,
)
from .rta4_formal_config import RTA4_CORES, domain_hash


CORE0A_REVIEW_RECEIPT_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_CANDIDATE_REVIEW_RECEIPT_V1"
)
CORE0A_REVIEW_RECEIPT_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_CANDIDATE_REVIEW_RECEIPT_CONTRACT_V1"
)
CORE0A_REVIEW_RECEIPT_STATUS = (
    "CORE0A_AUTHORIZATION_CANDIDATE_REVIEW_PASS"
)
CORE0A_REVIEW_RECEIPT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:CANDIDATE_REVIEW_RECEIPT:v1"
)
CORE0A_REVIEW_DECISION = "P0_0_P1_0_PASS"
CORE0A_CANDIDATE_REAUDIT_PASS = (
    "RTA4_CORE0A_AUTHORIZATION_CANDIDATE_REAUDIT_PASS"
)
CORE0A_CANDIDATE_REAUDIT_REPAIR_REQUIRED = (
    "RTA4_CORE0A_AUTHORIZATION_CANDIDATE_REPAIR_REQUIRED"
)
CORE0A_CANDIDATE_REAUDIT_BLOCKED = (
    "RTA4_CORE0A_AUTHORIZATION_CANDIDATE_REAUDIT_BLOCKED"
)

CORE0A_EXECUTABLE_AUTHORIZATION_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_EXECUTABLE_ENGINEERING_AUTHORIZATION_V1"
)
CORE0A_EXECUTABLE_AUTHORIZATION_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_EXECUTABLE_ENGINEERING_AUTHORIZATION_CONTRACT_V1"
)
CORE0A_EXECUTABLE_AUTHORIZATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:"
    "EXECUTABLE_ENGINEERING_AUTHORIZATION:v1"
)
CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION = (
    CORE0A_AUTODL_CONTROLLED_EXECUTION_ENVIRONMENT_CLASSIFICATION
)
CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION = (
    CORE0A_TEST_ONLY_EXECUTION_ENVIRONMENT_CLASSIFICATION
)

CORE0A_NONCE_CONSUMPTION_RECEIPT_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_NONCE_CONSUMPTION_RECEIPT_V2"
)
CORE0A_NONCE_CONSUMPTION_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_NONCE_CONSUMPTION_STATE_MACHINE_V2"
)
CORE0A_NONCE_CONSUMPTION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:NONCE_CONSUMPTION_RECEIPT:v2"
)
CORE0A_RESULT_ROOT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTHORIZED_RESULT_ROOT:v1"
)
CORE0A_RUN_CLAIM_SCHEMA = "ASAP_BLOCK_V9_3_RTA4_CORE0A_RUN_CLAIM_V2"
CORE0A_RUN_CLAIM_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:CORE0A:RUN_CLAIM:v2"
CORE0A_RUN_STATE_LOCATOR_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:RUN_STATE_LOCATOR:v1"
)
CORE0A_RUN_STATE_RELATIVE_LOCATOR_V1 = (
    ".core0a_authorization/{executable_authorization_identity}/run-state"
)
CORE0A_RUN_STATE_RECEIPT_NAME = "receipt.json"
CORE0A_RUN_STATE_CLAIM_NAME = "claim"
CORE0A_RUN_STATE_TERMINAL_CLAIM_NAME = "terminal-claim"
CORE0A_UNCLAIMED = "UNCLAIMED"
CORE0A_CLAIMED_INCOMPLETE = "CLAIMED_INCOMPLETE"
CORE0A_RUN_STARTED = "RUN_STARTED"
CORE0A_RUN_COMPLETED = "RUN_COMPLETED"
CORE0A_RUN_FAILED = "RUN_FAILED"

_REVIEWER_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@ -]{0,127}\Z", re.ASCII,
)
_TERMINAL_REVIEW_STATUSES = {
    CORE0A_CANDIDATE_REAUDIT_PASS,
    CORE0A_CANDIDATE_REAUDIT_REPAIR_REQUIRED,
    CORE0A_CANDIDATE_REAUDIT_BLOCKED,
}


class RTA4Core0AExecutionAuthorizationV2Error(RTA4Core0APilotV2Error):
    """Raised when reviewed CORE-0A execution authorization is invalid."""


@dataclass(frozen=True)
class ValidatedCore0AExecutableAuthorization:
    candidate: Mapping[str, Any]
    review_receipt: Mapping[str, Any]
    authorization: Mapping[str, Any]
    validated_deployment: ValidatedCore0ADeployment


@dataclass(frozen=True)
class Core0AExecutionContext:
    authorization_identity: str
    candidate_identity: str
    review_receipt_identity: str
    execution_identity: str
    selection_identity: str
    run_nonce: str
    issued_at: str
    expires_at: str
    deployment_workspace_root: str
    actual_output_root: str
    taskset_store_root: str
    terminal_directory: str
    max_runs: int
    authorized_cores: tuple[str, ...]
    forbidden_cores: tuple[str, ...]
    result_usage: str
    execution_environment_classification: str
    authorization_classification: str
    test_only_non_executable_fixture: bool
    validation_passed: bool
    run_state: str
    run_state_locator: str
    run_state_locator_identity: str
    claim_acquired: bool
    new_run_eligible: bool
    resume_only: bool
    runner_invocation_allowed: bool
    execution_mode: str
    new_run_allowed: bool
    resume_allowed: bool


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolved_existing_file(path: Path | str, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RTA4Core0AExecutionAuthorizationV2Error(
            f"{label} must be absolute"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            f"cannot resolve {label}"
        ) from exc
    if not resolved.is_file():
        raise RTA4Core0AExecutionAuthorizationV2Error(
            f"{label} must be a file"
        )
    return resolved


def _canonical_reviewer_label(value: str) -> str:
    if (
        type(value) is not str
        or value != value.strip()
        or not value.isascii()
        or "/" in value
        or "\\" in value
        or _REVIEWER_PATTERN.fullmatch(value) is None
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "reviewer_label must be bounded canonical ASCII"
        )
    return value


def _review_report_terminal_status(path: Path | str) -> str:
    source = _resolved_existing_file(path, "candidate review report")
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "candidate review report must be UTF-8"
        ) from exc
    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    if not nonempty:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "candidate review report is empty"
        )
    terminal = nonempty[-1].strip("`")
    if terminal not in _TERMINAL_REVIEW_STATUSES:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "candidate review report lacks an exact terminal status"
        )
    if terminal != CORE0A_CANDIDATE_REAUDIT_PASS:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            f"candidate review report is not PASS: {terminal}"
        )
    return terminal


def _candidate_times(candidate: Mapping[str, Any]) -> tuple[datetime, datetime]:
    try:
        issued_text = candidate["request"]["issued_at"]
        expires_text = candidate["request"]["expires_at"]
    except (KeyError, TypeError) as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "candidate request time binding is absent"
        ) from exc
    _, issued = _canonical_timestamp(issued_text, "issued_at")
    _, expires = _canonical_timestamp(expires_text, "expires_at")
    return issued, expires


def _require_time_within_candidate(
    candidate: Mapping[str, Any], value: str, label: str,
) -> tuple[str, datetime]:
    text, observed = _canonical_timestamp(value, label)
    issued, expires = _candidate_times(candidate)
    if observed < issued or observed >= expires:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            f"{label} must be inside the candidate validity window"
        )
    return text, observed


def _validated_candidate(
    *,
    candidate_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    return validate_core0a_authorization_candidate_v2(
        authorization_candidate_path=candidate_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )


def _validated_deployment(
    *,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> ValidatedCore0ADeployment:
    return validate_autodl_deployment_manifest_v2(
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )


def _require_classification_chain(
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    validated: ValidatedCore0ADeployment,
) -> str:
    candidate_classification = candidate.get(
        "execution_environment_classification"
    )
    deployment_classification = validated.deployment_manifest.get(
        "execution_environment_classification"
    )
    if (
        candidate_classification
        not in {
            CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION,
            CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION,
        }
        or candidate_classification != deployment_classification
        or (
            receipt is not None
            and receipt.get("execution_environment_classification")
            != candidate_classification
        )
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "deployment/candidate/review execution classification mismatch"
        )
    return str(candidate_classification)


def _review_receipt_material(
    *,
    candidate: Mapping[str, Any],
    candidate_path: Path | str,
    review_report_path: Path | str,
    reviewer_label: str,
    reviewed_at: str,
) -> Dict[str, Any]:
    candidate_source = _resolved_existing_file(
        candidate_path, "authorization candidate",
    )
    report = _resolved_existing_file(
        review_report_path, "candidate review report",
    )
    reviewer = _canonical_reviewer_label(reviewer_label)
    reviewed_text, _ = _require_time_within_candidate(
        candidate, reviewed_at, "reviewed_at",
    )
    terminal = _review_report_terminal_status(report)
    classification = candidate.get(
        "execution_environment_classification"
    )
    if classification not in {
        CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION,
        CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION,
    }:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "candidate execution environment classification is invalid"
        )
    return {
        "review_receipt_schema": CORE0A_REVIEW_RECEIPT_SCHEMA,
        "review_receipt_contract_version": (
            CORE0A_REVIEW_RECEIPT_CONTRACT_VERSION
        ),
        "status": CORE0A_REVIEW_RECEIPT_STATUS,
        "execution_environment_classification": classification,
        "candidate_identity": candidate["authorization_candidate_identity"],
        "candidate_artifact_sha256": _sha256(candidate_source),
        "request_identity": candidate["request"][
            "authorization_request_identity"
        ],
        "source_commit": candidate["source"]["git_commit"],
        "source_tree": candidate["source"]["git_tree"],
        "selection_identity": candidate["selection"]["selection_identity"],
        "portable_freeze_identity": candidate["identities"][
            "portable_freeze_identity"
        ],
        "production_build_manifest_identity": candidate["identities"][
            "production_build_manifest_identity"
        ],
        "deployment_manifest_identity": candidate["identities"][
            "deployment_manifest_identity"
        ],
        "execution_identity": candidate["identities"][
            "combined_execution_identity"
        ],
        "review_report_path": str(report),
        "review_report_sha256": _sha256(report),
        "review_report_terminal_status": terminal,
        "review_decision": CORE0A_REVIEW_DECISION,
        "reviewer_label": reviewer,
        "reviewed_at": reviewed_text,
        "formal_authorization": False,
        "production_authorization": False,
    }


def _build_review_receipt(
    *,
    candidate: Mapping[str, Any],
    candidate_path: Path | str,
    review_report_path: Path | str,
    reviewer_label: str,
    reviewed_at: str,
) -> Dict[str, Any]:
    material = _review_receipt_material(
        candidate=candidate,
        candidate_path=candidate_path,
        review_report_path=review_report_path,
        reviewer_label=reviewer_label,
        reviewed_at=reviewed_at,
    )
    return {
        **material,
        "review_receipt_identity": domain_hash(
            CORE0A_REVIEW_RECEIPT_DOMAIN, material,
        ),
    }


def build_core0a_candidate_review_receipt_v2(
    *,
    candidate_path: Path | str,
    review_report_path: Path | str,
    reviewer_label: str,
    reviewed_at: str,
    review_receipt_output_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    candidate = _validated_candidate(
        candidate_path=candidate_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    validated = _validated_deployment(
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    _require_classification_chain(candidate, None, validated)
    receipt = _build_review_receipt(
        candidate=candidate,
        candidate_path=candidate_path,
        review_report_path=review_report_path,
        reviewer_label=reviewer_label,
        reviewed_at=reviewed_at,
    )
    output = _authorization_output_path(
        review_receipt_output_path, validated,
    )
    _write_atomic_canonical_json(output, receipt)
    return receipt


def validate_core0a_candidate_review_receipt_v2(
    *,
    review_receipt_path: Path | str,
    candidate_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    candidate = _validated_candidate(
        candidate_path=candidate_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    validated = _validated_deployment(
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    document = load_strict_canonical_json(review_receipt_path)
    try:
        expected = _build_review_receipt(
            candidate=candidate,
            candidate_path=candidate_path,
            review_report_path=document["review_report_path"],
            reviewer_label=document["reviewer_label"],
            reviewed_at=document["reviewed_at"],
        )
    except (KeyError, TypeError) as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "review receipt reconstruction fields are absent"
        ) from exc
    if document != expected:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "review receipt differs from candidate/report reconstruction"
        )
    _require_classification_chain(candidate, document, validated)
    return document


def _authorization_material(
    *,
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any],
    review_receipt_artifact_sha256: str,
) -> Dict[str, Any]:
    if receipt["candidate_identity"] != candidate[
        "authorization_candidate_identity"
    ]:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "review receipt does not bind the exact candidate"
        )
    classification = candidate["execution_environment_classification"]
    if receipt.get(
        "execution_environment_classification"
    ) != classification:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "review receipt cannot change deployment execution classification"
        )
    controlled = (
        classification == CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION
    )
    authorization_state = {
        "engineering_pilot_authorization": controlled,
        "executable_authorization": controlled,
        "authorization_review_passed": True,
        "pilot_execution_allowed": controlled,
        "formal_authorization": False,
        "production_authorization": False,
    }
    if classification not in {
        CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION,
        CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION,
    }:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "review receipt classification is invalid"
        )
    return {
        "authorization_schema": CORE0A_EXECUTABLE_AUTHORIZATION_SCHEMA,
        "authorization_contract_version": (
            CORE0A_EXECUTABLE_AUTHORIZATION_CONTRACT_VERSION
        ),
        "status": AUTHORIZED_CORE0A_ENGINEERING_PILOT,
        "execution_environment_classification": classification,
        "test_only_non_executable_fixture": (
            classification == CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION
        ),
        "authorization_origin": "REVIEWED_EXACT_CANDIDATE",
        "candidate_binding": {
            "candidate_identity": candidate[
                "authorization_candidate_identity"
            ],
            "candidate_artifact_sha256": receipt[
                "candidate_artifact_sha256"
            ],
            "request_identity": candidate["request"][
                "authorization_request_identity"
            ],
        },
        "review_binding": {
            "review_receipt_identity": receipt["review_receipt_identity"],
            "review_receipt_artifact_sha256": (
                review_receipt_artifact_sha256
            ),
            "review_report_sha256": receipt["review_report_sha256"],
            "reviewer_label": receipt["reviewer_label"],
            "reviewed_at": receipt["reviewed_at"],
            "review_decision": receipt["review_decision"],
        },
        "source": deepcopy(candidate["source"]),
        "selection": deepcopy(candidate["selection"]),
        "identities": deepcopy(candidate["identities"]),
        "scientific_inputs": deepcopy(candidate["scientific_inputs"]),
        "paths": deepcopy(candidate["paths"]),
        "resources": deepcopy(candidate["resources"]),
        "disk_contract": deepcopy(candidate["disk_contract"]),
        "scope": deepcopy(candidate["scope"]),
        "request": deepcopy(candidate["request"]),
        "run_state_binding": {
            "relative_locator_template": (
                CORE0A_RUN_STATE_RELATIVE_LOCATOR_V1
            ),
            "claim_schema": CORE0A_RUN_CLAIM_SCHEMA,
            "receipt_schema": CORE0A_NONCE_CONSUMPTION_RECEIPT_SCHEMA,
        },
        "authorization_state": authorization_state,
    }


def _build_executable_authorization(
    *,
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any],
    review_receipt_artifact_sha256: str,
) -> Dict[str, Any]:
    material = _authorization_material(
        candidate=candidate,
        receipt=receipt,
        review_receipt_artifact_sha256=review_receipt_artifact_sha256,
    )
    return {
        **material,
        "executable_authorization_identity": domain_hash(
            CORE0A_EXECUTABLE_AUTHORIZATION_DOMAIN, material,
        ),
    }


def build_core0a_executable_engineering_authorization_v2(
    *,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    authorization_output_path: Path | str,
    verification_time: str,
) -> Dict[str, Any]:
    common = {
        "portable_bundle_path": portable_bundle_path,
        "selection_artifact_path": selection_artifact_path,
        "candidate_config_path": candidate_config_path,
        "production_manifest_path": production_manifest_path,
        "deployment_manifest_path": deployment_manifest_path,
        "source_root": source_root,
        "deployment_workspace_root": deployment_workspace_root,
    }
    candidate = _validated_candidate(
        candidate_path=candidate_path, **common,
    )
    validated = _validated_deployment(**common)
    receipt = validate_core0a_candidate_review_receipt_v2(
        review_receipt_path=review_receipt_path,
        candidate_path=candidate_path,
        **common,
    )
    _, verified = _require_time_within_candidate(
        candidate, verification_time, "verification_time",
    )
    _, reviewed = _canonical_timestamp(receipt["reviewed_at"], "reviewed_at")
    if verified < reviewed:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "verification_time precedes independent review"
        )
    authorization = _build_executable_authorization(
        candidate=candidate,
        receipt=receipt,
        review_receipt_artifact_sha256=_sha256(review_receipt_path),
    )
    output = _authorization_output_path(
        authorization_output_path, validated,
    )
    _write_atomic_canonical_json(output, authorization)
    return authorization


def validate_core0a_executable_engineering_authorization_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> ValidatedCore0AExecutableAuthorization:
    common = {
        "portable_bundle_path": portable_bundle_path,
        "selection_artifact_path": selection_artifact_path,
        "candidate_config_path": candidate_config_path,
        "production_manifest_path": production_manifest_path,
        "deployment_manifest_path": deployment_manifest_path,
        "source_root": source_root,
        "deployment_workspace_root": deployment_workspace_root,
    }
    candidate = _validated_candidate(
        candidate_path=candidate_path, **common,
    )
    validated = _validated_deployment(**common)
    receipt = validate_core0a_candidate_review_receipt_v2(
        review_receipt_path=review_receipt_path,
        candidate_path=candidate_path,
        **common,
    )
    document = load_strict_canonical_json(executable_authorization_path)
    expected = _build_executable_authorization(
        candidate=candidate,
        receipt=receipt,
        review_receipt_artifact_sha256=_sha256(review_receipt_path),
    )
    if document != expected:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "executable authorization differs from exact reconstruction"
        )
    return ValidatedCore0AExecutableAuthorization(
        candidate=candidate,
        review_receipt=receipt,
        authorization=document,
        validated_deployment=validated,
    )


def _result_root_identity(
    authorization: Mapping[str, Any],
) -> str:
    return domain_hash(CORE0A_RESULT_ROOT_DOMAIN, {
        "executable_authorization_identity": authorization[
            "executable_authorization_identity"
        ],
        "deployment_workspace_root": authorization["paths"][
            "deployment_workspace_root"
        ],
        "actual_output_root": authorization["paths"]["actual_output_root"],
        "taskset_store_root": authorization["paths"]["taskset_store_root"],
        "terminal_directory": authorization["paths"]["terminal_directory"],
    })


def _validated_authorization_from_paths(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> ValidatedCore0AExecutableAuthorization:
    return validate_core0a_executable_engineering_authorization_v2(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )


def _preflight_time(
    authorization: Mapping[str, Any], current_utc: str,
) -> str:
    current_text, current = _canonical_timestamp(current_utc, "current_utc")
    issued, expires = _candidate_times({"request": authorization["request"]})
    _, reviewed = _canonical_timestamp(
        authorization["review_binding"]["reviewed_at"],
        "reviewed_at",
    )
    if current < issued or current < reviewed or current >= expires:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "executable authorization is not currently valid"
        )
    return current_text


@dataclass(frozen=True)
class _Core0ARunStatePaths:
    workspace_root: Path
    output_root: Path
    control_root: Path
    authorization_directory: Path
    run_state_directory: Path
    claim_path: Path
    receipt_path: Path
    terminal_claim_path: Path
    locator_identity: str


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _run_state_paths(
    validated: ValidatedCore0AExecutableAuthorization,
) -> _Core0ARunStatePaths:
    authorization = validated.authorization
    deployment = validated.validated_deployment.deployment_manifest
    authorization_identity = authorization[
        "executable_authorization_identity"
    ]
    workspace_root = Path(
        authorization["paths"]["deployment_workspace_root"]
    )
    output_root = Path(authorization["paths"]["actual_output_root"])
    if (
        not workspace_root.is_absolute()
        or ".." in workspace_root.parts
        or not output_root.is_absolute()
        or ".." in output_root.parts
        or str(output_root) != deployment.get("actual_output_root")
        or str(workspace_root)
        != deployment.get("deployment_workspace_root")
        or not _is_within(output_root, workspace_root)
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "validated authorization output root is not canonical"
        )
    control_root = output_root / ".core0a_authorization"
    authorization_directory = control_root / authorization_identity
    run_state_directory = authorization_directory / "run-state"
    if (
        authorization["run_state_binding"].get(
            "relative_locator_template"
        )
        != CORE0A_RUN_STATE_RELATIVE_LOCATOR_V1
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "authorization run-state locator contract mismatch"
        )
    for path in (
        control_root,
        authorization_directory,
        run_state_directory,
    ):
        if ".." in path.parts or not _is_within(path, output_root):
            raise RTA4Core0AExecutionAuthorizationV2Error(
                "run-state locator escapes the CORE-0A output root"
            )
        if path.resolve(strict=False) != path:
            raise RTA4Core0AExecutionAuthorizationV2Error(
                "run-state locator contains a symlink or path alias"
            )
    locator_material = {
        "relative_locator_template": CORE0A_RUN_STATE_RELATIVE_LOCATOR_V1,
        "authorization_identity": authorization_identity,
        "deployment_identity": authorization["identities"][
            "deployment_manifest_identity"
        ],
        "execution_identity": authorization["identities"][
            "combined_execution_identity"
        ],
        "canonical_output_root": str(output_root),
        "run_nonce": authorization["request"]["run_nonce"],
    }
    return _Core0ARunStatePaths(
        workspace_root=workspace_root,
        output_root=output_root,
        control_root=control_root,
        authorization_directory=authorization_directory,
        run_state_directory=run_state_directory,
        claim_path=run_state_directory / CORE0A_RUN_STATE_CLAIM_NAME,
        receipt_path=(
            run_state_directory / CORE0A_RUN_STATE_RECEIPT_NAME
        ),
        terminal_claim_path=(
            run_state_directory / CORE0A_RUN_STATE_TERMINAL_CLAIM_NAME
        ),
        locator_identity=domain_hash(
            CORE0A_RUN_STATE_LOCATOR_DOMAIN, locator_material,
        ),
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_safe_directory(path: Path, output_root: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "cannot create deterministic run-state parent"
        ) from exc
    if (
        path.is_symlink()
        or not path.is_dir()
        or path.resolve(strict=True) != path
        or (path != output_root and not _is_within(path, output_root))
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run-state parent is not a safe canonical directory"
        )
    _fsync_directory(path.parent)


def _prepare_run_state_parent(paths: _Core0ARunStatePaths) -> None:
    workspace = paths.workspace_root
    if not workspace.is_dir() or workspace.is_symlink():
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "deployment workspace is not a safe directory"
        )
    current = workspace
    for component in paths.output_root.relative_to(workspace).parts:
        current = current / component
        _ensure_safe_directory(current, workspace)
    _ensure_safe_directory(paths.control_root, workspace)
    _ensure_safe_directory(
        paths.authorization_directory, workspace,
    )


def _run_binding_material(
    authorization: Mapping[str, Any],
    paths: _Core0ARunStatePaths,
) -> Dict[str, Any]:
    return {
        "authorization_identity": authorization[
            "executable_authorization_identity"
        ],
        "candidate_identity": authorization["candidate_binding"][
            "candidate_identity"
        ],
        "review_receipt_identity": authorization["review_binding"][
            "review_receipt_identity"
        ],
        "source_commit": authorization["source"]["git_commit"],
        "source_tree": authorization["source"]["git_tree"],
        "deployment_identity": authorization["identities"][
            "deployment_manifest_identity"
        ],
        "execution_identity": authorization["identities"][
            "combined_execution_identity"
        ],
        "canonical_output_root": authorization["paths"][
            "actual_output_root"
        ],
        "run_nonce": authorization["request"]["run_nonce"],
        "run_state_relative_locator": (
            CORE0A_RUN_STATE_RELATIVE_LOCATOR_V1
        ),
        "run_state_locator_identity": paths.locator_identity,
        "execution_environment_classification": authorization[
            "execution_environment_classification"
        ],
    }


def _build_run_claim(
    authorization: Mapping[str, Any],
    paths: _Core0ARunStatePaths,
    *,
    claimed_at: str,
) -> Dict[str, Any]:
    claimed_text, _ = _canonical_timestamp(claimed_at, "claimed_at")
    material = {
        "run_claim_schema": CORE0A_RUN_CLAIM_SCHEMA,
        **_run_binding_material(authorization, paths),
        "claimed_at": claimed_text,
    }
    return {
        **material,
        "run_claim_identity": domain_hash(
            CORE0A_RUN_CLAIM_DOMAIN, material,
        ),
    }


def _validate_run_claim(
    document: Mapping[str, Any],
    authorization: Mapping[str, Any],
    paths: _Core0ARunStatePaths,
) -> Dict[str, Any]:
    try:
        expected = _build_run_claim(
            authorization,
            paths,
            claimed_at=document["claimed_at"],
        )
    except (KeyError, TypeError) as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run claim fields are absent"
        ) from exc
    if dict(document) != expected:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run claim differs from exact authorization binding"
        )
    return dict(document)


def _write_exclusive_canonical_json(
    path: Path,
    document: Mapping[str, Any],
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        payload = canonical_json_bytes(document)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short exclusive claim write")
            offset += written
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _fsync_directory(path.parent)
    _fsync_directory(path.parent.parent)


def _consumption_receipt_material(
    *,
    authorization: Mapping[str, Any],
    paths: _Core0ARunStatePaths,
    status: str,
    started_at: str,
    completed_at: str | None,
) -> Dict[str, Any]:
    started_text, started = _canonical_timestamp(started_at, "started_at")
    if status == CORE0A_RUN_STARTED:
        if completed_at is not None:
            raise RTA4Core0AExecutionAuthorizationV2Error(
                "RUN_STARTED cannot have completed_at"
            )
        completed_text = None
        result_identity = "INCOMPLETE_PENDING_TERMINAL"
    elif status in {CORE0A_RUN_COMPLETED, CORE0A_RUN_FAILED}:
        if completed_at is None:
            raise RTA4Core0AExecutionAuthorizationV2Error(
                "terminal consumption receipt requires completed_at"
            )
        completed_text, completed = _canonical_timestamp(
            completed_at, "completed_at",
        )
        if completed < started:
            raise RTA4Core0AExecutionAuthorizationV2Error(
                "completed_at precedes started_at"
            )
        result_identity = _result_root_identity(authorization)
    else:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "nonce consumption status is invalid"
        )
    return {
        "consumption_receipt_schema": (
            CORE0A_NONCE_CONSUMPTION_RECEIPT_SCHEMA
        ),
        "consumption_contract_version": (
            CORE0A_NONCE_CONSUMPTION_CONTRACT_VERSION
        ),
        **_run_binding_material(authorization, paths),
        "status": status,
        "started_at": started_text,
        "completed_at": completed_text,
        "result_root_identity": result_identity,
    }


def _build_consumption_receipt(
    *,
    authorization: Mapping[str, Any],
    paths: _Core0ARunStatePaths,
    status: str,
    started_at: str,
    completed_at: str | None,
) -> Dict[str, Any]:
    material = _consumption_receipt_material(
        authorization=authorization,
        paths=paths,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
    )
    return {
        **material,
        "consumption_receipt_identity": domain_hash(
            CORE0A_NONCE_CONSUMPTION_DOMAIN, material,
        ),
    }


def _validate_consumption_receipt(
    document: Mapping[str, Any],
    authorization: Mapping[str, Any],
    paths: _Core0ARunStatePaths,
) -> Dict[str, Any]:
    try:
        expected = _build_consumption_receipt(
            authorization=authorization,
            paths=paths,
            status=document["status"],
            started_at=document["started_at"],
            completed_at=document["completed_at"],
        )
    except (KeyError, TypeError) as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "nonce consumption receipt fields are absent"
        ) from exc
    if dict(document) != expected:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "nonce consumption receipt differs from authorization binding"
        )
    return dict(document)


def _read_run_state(
    validated: ValidatedCore0AExecutableAuthorization,
) -> tuple[str, _Core0ARunStatePaths, Dict[str, Any] | None]:
    paths = _run_state_paths(validated)
    run_state = paths.run_state_directory
    if not run_state.exists():
        return CORE0A_UNCLAIMED, paths, None
    if (
        run_state.is_symlink()
        or not run_state.is_dir()
        or run_state.resolve(strict=True) != run_state
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run-state claim locator is not a canonical directory"
        )
    if not paths.claim_path.exists():
        if paths.receipt_path.exists():
            raise RTA4Core0AExecutionAuthorizationV2Error(
                "run-state receipt exists without its prior claim"
            )
        return CORE0A_CLAIMED_INCOMPLETE, paths, None
    if paths.claim_path.is_symlink() or not paths.claim_path.is_file():
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run claim is not a regular file"
        )
    _validate_run_claim(
        load_strict_canonical_json(paths.claim_path),
        validated.authorization,
        paths,
    )
    if not paths.receipt_path.exists():
        return CORE0A_CLAIMED_INCOMPLETE, paths, None
    if paths.receipt_path.is_symlink() or not paths.receipt_path.is_file():
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run-state receipt is not a regular file"
        )
    receipt = _validate_consumption_receipt(
        load_strict_canonical_json(paths.receipt_path),
        validated.authorization,
        paths,
    )
    return str(receipt["status"]), paths, receipt


def _execution_context(
    validated: ValidatedCore0AExecutableAuthorization,
    *,
    run_state: str,
    paths: _Core0ARunStatePaths,
    execution_mode: str,
    claim_acquired: bool,
    new_run_eligible: bool,
    resume_allowed: bool,
    runner_invocation_allowed: bool,
) -> Core0AExecutionContext:
    authorization = validated.authorization
    scope = authorization["scope"]
    if (
        scope["authorization_scope"] != CORE0A_AUTHORIZATION_SCOPE
        or scope["selection_count"] != EXPECTED_EXECUTION_COUNT
        or scope["max_runs"] != CORE0A_MAX_RUNS
        or scope["authorized_cores"] != ["CORE-0A"]
        or scope["forbidden_cores"] != list(RTA4_CORES)
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "execution scope is not exact CORE-0A/384/max-runs-1"
        )
    return Core0AExecutionContext(
        authorization_identity=authorization[
            "executable_authorization_identity"
        ],
        candidate_identity=authorization["candidate_binding"][
            "candidate_identity"
        ],
        review_receipt_identity=authorization["review_binding"][
            "review_receipt_identity"
        ],
        execution_identity=authorization["identities"][
            "combined_execution_identity"
        ],
        selection_identity=authorization["selection"][
            "selection_identity"
        ],
        run_nonce=authorization["request"]["run_nonce"],
        issued_at=authorization["request"]["issued_at"],
        expires_at=authorization["request"]["expires_at"],
        deployment_workspace_root=authorization["paths"][
            "deployment_workspace_root"
        ],
        actual_output_root=authorization["paths"]["actual_output_root"],
        taskset_store_root=authorization["paths"]["taskset_store_root"],
        terminal_directory=authorization["paths"]["terminal_directory"],
        max_runs=scope["max_runs"],
        authorized_cores=tuple(scope["authorized_cores"]),
        forbidden_cores=tuple(scope["forbidden_cores"]),
        result_usage=scope["result_usage"],
        execution_environment_classification=authorization[
            "execution_environment_classification"
        ],
        authorization_classification=authorization[
            "execution_environment_classification"
        ],
        test_only_non_executable_fixture=authorization[
            "test_only_non_executable_fixture"
        ],
        validation_passed=True,
        run_state=run_state,
        run_state_locator=str(paths.run_state_directory),
        run_state_locator_identity=paths.locator_identity,
        claim_acquired=claim_acquired,
        new_run_eligible=new_run_eligible,
        resume_only=resume_allowed,
        runner_invocation_allowed=runner_invocation_allowed,
        execution_mode=execution_mode,
        new_run_allowed=new_run_eligible,
        resume_allowed=resume_allowed,
    )


def preflight_core0a_engineering_pilot_execution_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    current_utc: str,
) -> Core0AExecutionContext:
    common = {
        "executable_authorization_path": executable_authorization_path,
        "candidate_path": candidate_path,
        "review_receipt_path": review_receipt_path,
        "portable_bundle_path": portable_bundle_path,
        "selection_artifact_path": selection_artifact_path,
        "candidate_config_path": candidate_config_path,
        "production_manifest_path": production_manifest_path,
        "deployment_manifest_path": deployment_manifest_path,
        "source_root": source_root,
        "deployment_workspace_root": deployment_workspace_root,
    }
    validated = _validated_authorization_from_paths(**common)
    _preflight_time(validated.authorization, current_utc)
    run_state, paths, _ = _read_run_state(validated)
    return _execution_context(
        validated,
        run_state=run_state,
        paths=paths,
        execution_mode="READ_ONLY_PREFLIGHT",
        claim_acquired=False,
        new_run_eligible=run_state == CORE0A_UNCLAIMED,
        resume_allowed=run_state in {
            CORE0A_CLAIMED_INCOMPLETE,
            CORE0A_RUN_STARTED,
        },
        runner_invocation_allowed=False,
    )


def preflight_core0a_engineering_pilot_resume_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    current_utc: str,
) -> Core0AExecutionContext:
    common = {
        "executable_authorization_path": executable_authorization_path,
        "candidate_path": candidate_path,
        "review_receipt_path": review_receipt_path,
        "portable_bundle_path": portable_bundle_path,
        "selection_artifact_path": selection_artifact_path,
        "candidate_config_path": candidate_config_path,
        "production_manifest_path": production_manifest_path,
        "deployment_manifest_path": deployment_manifest_path,
        "source_root": source_root,
        "deployment_workspace_root": deployment_workspace_root,
    }
    validated = _validated_authorization_from_paths(**common)
    _preflight_time(validated.authorization, current_utc)
    run_state, paths, _ = _read_run_state(validated)
    if run_state not in {
        CORE0A_CLAIMED_INCOMPLETE,
        CORE0A_RUN_STARTED,
    }:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "only the same claimed incomplete or RUN_STARTED run may resume"
        )
    return _execution_context(
        validated,
        run_state=run_state,
        paths=paths,
        execution_mode="RESUME_ONLY",
        claim_acquired=False,
        new_run_eligible=False,
        resume_allowed=True,
        runner_invocation_allowed=False,
    )


def acquire_core0a_run_claim_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    started_at: str,
) -> Core0AExecutionContext:
    validated = _validated_authorization_from_paths(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    authorization = validated.authorization
    _preflight_time(authorization, started_at)
    state, paths, _ = _read_run_state(validated)
    if state != CORE0A_UNCLAIMED:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run state is already claimed; a second NEW_RUN is forbidden"
        )
    _prepare_run_state_parent(paths)
    try:
        os.mkdir(paths.run_state_directory, mode=0o700)
    except FileExistsError as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run claim already exists; a second NEW_RUN is forbidden"
        ) from exc
    _fsync_directory(paths.authorization_directory)
    claim = _build_run_claim(
        authorization,
        paths,
        claimed_at=started_at,
    )
    _write_exclusive_canonical_json(paths.claim_path, claim)
    receipt = _build_consumption_receipt(
        authorization=authorization,
        paths=paths,
        status=CORE0A_RUN_STARTED,
        started_at=started_at,
        completed_at=None,
    )
    _write_atomic_canonical_json(paths.receipt_path, receipt)
    controlled = (
        authorization["execution_environment_classification"]
        == CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION
        and authorization["authorization_state"] == {
            "engineering_pilot_authorization": True,
            "executable_authorization": True,
            "authorization_review_passed": True,
            "pilot_execution_allowed": True,
            "formal_authorization": False,
            "production_authorization": False,
        }
        and authorization["test_only_non_executable_fixture"] is False
    )
    return _execution_context(
        validated,
        run_state=CORE0A_RUN_STARTED,
        paths=paths,
        execution_mode="CLAIMED_NEW_RUN",
        claim_acquired=True,
        new_run_eligible=False,
        resume_allowed=False,
        runner_invocation_allowed=controlled,
    )


def validate_core0a_nonce_consumption_receipt_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    validated = _validated_authorization_from_paths(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    state, _, receipt = _read_run_state(validated)
    if receipt is None:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            f"run-state receipt is unavailable in state {state}"
        )
    return receipt


def _write_terminal_run_state(
    *,
    status: str,
    completed_at: str,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    validated = _validated_authorization_from_paths(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    state, paths, receipt = _read_run_state(validated)
    if state != CORE0A_RUN_STARTED or receipt is None:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "terminal transition requires the exact RUN_STARTED state"
        )
    transition = {
        "authorization_identity": validated.authorization[
            "executable_authorization_identity"
        ],
        "from_status": CORE0A_RUN_STARTED,
        "to_status": status,
        "completed_at": completed_at,
    }
    try:
        _write_exclusive_canonical_json(
            paths.terminal_claim_path,
            transition,
        )
    except FileExistsError as exc:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "terminal transition was already claimed"
        ) from exc
    terminal = _build_consumption_receipt(
        authorization=validated.authorization,
        paths=paths,
        status=status,
        started_at=receipt["started_at"],
        completed_at=completed_at,
    )
    _write_atomic_canonical_json(paths.receipt_path, terminal)
    return terminal


def complete_core0a_run_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    completed_at: str,
) -> Dict[str, Any]:
    return _write_terminal_run_state(
        status=CORE0A_RUN_COMPLETED,
        completed_at=completed_at,
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )


def fail_core0a_run_v2(
    *,
    executable_authorization_path: Path | str,
    candidate_path: Path | str,
    review_receipt_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    completed_at: str,
) -> Dict[str, Any]:
    return _write_terminal_run_state(
        status=CORE0A_RUN_FAILED,
        completed_at=completed_at,
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )


__all__ = [
    "CORE0A_CANDIDATE_REAUDIT_PASS",
    "CORE0A_CLAIMED_INCOMPLETE",
    "CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION",
    "CORE0A_EXECUTABLE_AUTHORIZATION_SCHEMA",
    "CORE0A_NONCE_CONSUMPTION_RECEIPT_SCHEMA",
    "CORE0A_REVIEW_RECEIPT_SCHEMA",
    "CORE0A_REVIEW_RECEIPT_STATUS",
    "CORE0A_RUN_COMPLETED",
    "CORE0A_RUN_FAILED",
    "CORE0A_RUN_STATE_RELATIVE_LOCATOR_V1",
    "CORE0A_RUN_STARTED",
    "CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION",
    "CORE0A_UNCLAIMED",
    "Core0AExecutionContext",
    "RTA4Core0AExecutionAuthorizationV2Error",
    "ValidatedCore0AExecutableAuthorization",
    "acquire_core0a_run_claim_v2",
    "build_core0a_candidate_review_receipt_v2",
    "build_core0a_executable_engineering_authorization_v2",
    "complete_core0a_run_v2",
    "fail_core0a_run_v2",
    "preflight_core0a_engineering_pilot_execution_v2",
    "preflight_core0a_engineering_pilot_resume_v2",
    "validate_core0a_candidate_review_receipt_v2",
    "validate_core0a_executable_engineering_authorization_v2",
    "validate_core0a_nonce_consumption_receipt_v2",
]
