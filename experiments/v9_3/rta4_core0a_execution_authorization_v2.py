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
    CORE0A_AUTHORIZATION_SCOPE,
    CORE0A_MAX_RUNS,
    EXPECTED_EXECUTION_COUNT,
    RTA4Core0APilotV2Error,
    ValidatedCore0ADeployment,
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
    "CONTROLLED_RESEARCH_EXECUTION_ENVIRONMENT"
)
CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION = (
    "TEST_ONLY_NON_EXECUTABLE_FIXTURE"
)

CORE0A_NONCE_CONSUMPTION_RECEIPT_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_NONCE_CONSUMPTION_RECEIPT_V1"
)
CORE0A_NONCE_CONSUMPTION_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_NONCE_CONSUMPTION_STATE_MACHINE_V1"
)
CORE0A_NONCE_CONSUMPTION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:NONCE_CONSUMPTION_RECEIPT:v1"
)
CORE0A_RESULT_ROOT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTHORIZED_RESULT_ROOT:v1"
)
CORE0A_RUN_STARTED = "RUN_STARTED"
CORE0A_RUN_COMPLETED = "RUN_COMPLETED"
CORE0A_RUN_FAILED = "RUN_FAILED"

_REVIEWER_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@ -]{0,127}\Z", re.ASCII,
)
_TEST_ONLY_REVIEWER_PREFIX = "TEST_ONLY_"
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
    authorization_classification: str
    test_only_non_executable_fixture: bool
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
    classification = (
        CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION
        if reviewer.startswith(_TEST_ONLY_REVIEWER_PREFIX)
        else CORE0A_CONTROLLED_AUTHORIZATION_CLASSIFICATION
    )
    return {
        "review_receipt_schema": CORE0A_REVIEW_RECEIPT_SCHEMA,
        "review_receipt_contract_version": (
            CORE0A_REVIEW_RECEIPT_CONTRACT_VERSION
        ),
        "status": CORE0A_REVIEW_RECEIPT_STATUS,
        "receipt_classification": classification,
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
    receipt = _build_review_receipt(
        candidate=candidate,
        candidate_path=candidate_path,
        review_report_path=review_report_path,
        reviewer_label=reviewer_label,
        reviewed_at=reviewed_at,
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
    return document


def _authorization_material(
    *,
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> Dict[str, Any]:
    if receipt["candidate_identity"] != candidate[
        "authorization_candidate_identity"
    ]:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "review receipt does not bind the exact candidate"
        )
    authorization_state = {
        "engineering_pilot_authorization": True,
        "executable_authorization": True,
        "authorization_review_passed": True,
        "pilot_execution_allowed": True,
        "formal_authorization": False,
        "production_authorization": False,
    }
    classification = receipt["receipt_classification"]
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
        "authorization_classification": classification,
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
        "authorization_state": authorization_state,
    }


def _build_executable_authorization(
    *,
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> Dict[str, Any]:
    material = _authorization_material(
        candidate=candidate,
        receipt=receipt,
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


def _consumption_receipt_material(
    *,
    authorization: Mapping[str, Any],
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
        "status": status,
        "run_nonce": authorization["request"]["run_nonce"],
        "authorization_identity": authorization[
            "executable_authorization_identity"
        ],
        "execution_identity": authorization["identities"][
            "combined_execution_identity"
        ],
        "started_at": started_text,
        "completed_at": completed_text,
        "result_root_identity": _result_root_identity(authorization),
    }


def _build_consumption_receipt(
    *,
    authorization: Mapping[str, Any],
    status: str,
    started_at: str,
    completed_at: str | None,
) -> Dict[str, Any]:
    material = _consumption_receipt_material(
        authorization=authorization,
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
) -> Dict[str, Any]:
    try:
        expected = _build_consumption_receipt(
            authorization=authorization,
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


def validate_core0a_nonce_consumption_receipt_v2(
    *,
    consumption_receipt_path: Path | str,
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
    receipt = _resolved_existing_file(
        consumption_receipt_path, "nonce consumption receipt",
    )
    return _validate_consumption_receipt(
        load_strict_canonical_json(receipt),
        validated.authorization,
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


def _execution_context(
    validated: ValidatedCore0AExecutableAuthorization,
    *,
    execution_mode: str,
    new_run_allowed: bool,
    resume_allowed: bool,
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
        authorization_classification=authorization[
            "authorization_classification"
        ],
        test_only_non_executable_fixture=authorization[
            "test_only_non_executable_fixture"
        ],
        runner_invocation_allowed=not authorization[
            "test_only_non_executable_fixture"
        ],
        execution_mode=execution_mode,
        new_run_allowed=new_run_allowed,
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
    consumption_receipt_path: Path | str,
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
    consumption = Path(consumption_receipt_path)
    if consumption.exists():
        document = load_strict_canonical_json(consumption)
        _validate_consumption_receipt(document, validated.authorization)
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "run nonce is already consumed; a second run is forbidden"
        )
    return _execution_context(
        validated,
        execution_mode="NEW_RUN_ONLY",
        new_run_allowed=True,
        resume_allowed=False,
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
    consumption_receipt_path: Path | str,
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
    consumption = _resolved_existing_file(
        consumption_receipt_path, "nonce consumption receipt",
    )
    document = _validate_consumption_receipt(
        load_strict_canonical_json(consumption),
        validated.authorization,
    )
    if document["status"] != CORE0A_RUN_STARTED:
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "only the same RUN_STARTED authorization may resume"
        )
    return _execution_context(
        validated,
        execution_mode="RESUME_EXISTING_RUN_ONLY",
        new_run_allowed=False,
        resume_allowed=True,
    )


def write_test_only_core0a_run_started_receipt_v2(
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
    consumption_receipt_path: Path | str,
    started_at: str,
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
    authorization = validated.authorization
    if authorization["authorization_classification"] != (
        CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION
    ):
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "this helper only writes TEST_ONLY nonce receipts"
        )
    _preflight_time(authorization, started_at)
    target = Path(consumption_receipt_path)
    if target.exists():
        raise RTA4Core0AExecutionAuthorizationV2Error(
            "nonce consumption receipt already exists"
        )
    output = _authorization_output_path(
        target, validated.validated_deployment,
    )
    receipt = _build_consumption_receipt(
        authorization=authorization,
        status=CORE0A_RUN_STARTED,
        started_at=started_at,
        completed_at=None,
    )
    _write_atomic_canonical_json(output, receipt)
    return receipt


__all__ = [
    "CORE0A_CANDIDATE_REAUDIT_PASS",
    "CORE0A_EXECUTABLE_AUTHORIZATION_SCHEMA",
    "CORE0A_NONCE_CONSUMPTION_RECEIPT_SCHEMA",
    "CORE0A_REVIEW_RECEIPT_SCHEMA",
    "CORE0A_REVIEW_RECEIPT_STATUS",
    "CORE0A_RUN_COMPLETED",
    "CORE0A_RUN_FAILED",
    "CORE0A_RUN_STARTED",
    "CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION",
    "Core0AExecutionContext",
    "RTA4Core0AExecutionAuthorizationV2Error",
    "ValidatedCore0AExecutableAuthorization",
    "build_core0a_candidate_review_receipt_v2",
    "build_core0a_executable_engineering_authorization_v2",
    "preflight_core0a_engineering_pilot_execution_v2",
    "preflight_core0a_engineering_pilot_resume_v2",
    "validate_core0a_candidate_review_receipt_v2",
    "validate_core0a_executable_engineering_authorization_v2",
    "validate_core0a_nonce_consumption_receipt_v2",
    "write_test_only_core0a_run_started_receipt_v2",
]
