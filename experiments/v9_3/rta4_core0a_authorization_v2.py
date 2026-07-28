"""Non-executable authorization candidate for the RTA4 CORE-0A pilot.

The public entry points in this module accept frozen artifact paths, not an
in-process ``ValidatedCore0ADeployment`` object.  They always re-run the formal
file-path deployment validator before constructing or checking a candidate.
This module neither creates executable authorization nor runs pilot records.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Mapping

from .rta4_core0a_pilot_v2 import (
    CORE0A_AUTHORIZATION_SCOPE,
    CORE0A_MAX_RUNS,
    EXPECTED_EXECUTION_COUNT,
    RTA4Core0APilotV2Error,
    ValidatedCore0ADeployment,
    canonical_json_bytes,
    core0a_execution_identity,
    load_strict_canonical_json,
    validate_autodl_deployment_manifest_v2,
)
from .rta4_formal_config import RTA4_CORES, domain_hash


CORE0A_AUTHORIZATION_CANDIDATE_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_AUTHORIZATION_V1"
)
CORE0A_AUTHORIZATION_CANDIDATE_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_AUTHORIZATION_CANDIDATE_V1"
)
CORE0A_AUTHORIZATION_CANDIDATE_STATUS = (
    "AUTHORIZED_CORE0A_ENGINEERING_PILOT_CANDIDATE_PENDING_REAUDIT"
)
CORE0A_AUTHORIZATION_CANDIDATE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:"
    "ENGINEERING_AUTHORIZATION_CANDIDATE:v1"
)
CORE0A_AUTHORIZATION_REQUEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:ENGINEERING_AUTHORIZATION_REQUEST:v1"
)
CORE0A_ORDERED_RECORD_IDENTITIES_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:ORDERED_384_RECORD_IDENTITIES:v1"
)
CORE0A_AUTHORIZATION_BUILDER_SOURCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTHORIZATION_BUILDER_SOURCE:v1"
)
CORE0A_AUTHORIZATION_VALIDITY_CONTRACT = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTHORIZATION_VALIDITY_24H_V1"
)
CORE0A_AUTHORIZATION_MAX_VALIDITY_SECONDS = 24 * 60 * 60
CORE0A_AUTHORIZATION_NONCE_MAX_LENGTH = 128
CORE0A_AUTHORIZATION_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CORE0A_RESULT_USAGE = "ENGINEERING_AUDIT_ONLY"

_NONCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z", re.ASCII)
_BUILDER_SOURCE_PATHS = (
    "experiments/v9_3/rta4_core0a_authorization_v2.py",
    "scripts/build_v9_3_rta4_core0a_authorization.py",
)
_AUTHORIZED_CORES = ("CORE-0A",)
_FORBIDDEN_CORES = tuple(RTA4_CORES)
_RESOURCE_FIELDS = (
    "resource_policy_version",
    "logical_cpu_count",
    "physical_memory_bytes",
    "free_disk_bytes",
    "resource_observation_identity",
    "worker_count",
    "max_in_flight",
    "memory_soft_limit_fraction",
    "memory_soft_limit_bytes",
    "checkpoint_frequency_records",
    "resume_policy",
    "retry_contract",
    "timeout_resource_identity",
    "disk_preflight_passed",
)
_DISK_FIELDS = (
    "estimate_version",
    "estimate_source",
    "execution_count",
    "unique_taskset_slot_count",
    "bytes_per_execution",
    "bytes_per_unique_taskset",
    "fixed_overhead_bytes",
    "estimated_required_disk_bytes",
    "safety_margin_version",
    "safety_margin_algorithm",
    "explicit_safety_margin_bytes",
    "required_free_disk_bytes",
    "disk_estimate_identity",
)
_PRODUCTION_IDENTITY_FIELDS = (
    "production_build_manifest_identity",
    "python_identity",
    "toolchain_identity",
    "simulator_identity",
    "verifier_identity",
    "environment_identity",
)
_PATH_FIELDS = (
    "source_root",
    "deployment_workspace_root",
    "deployment_workspace_identity",
    "expected_output_namespace",
    "actual_output_root",
    "taskset_store_namespace",
    "taskset_store_root",
    "terminal_directory_name",
    "terminal_directory",
)


class RTA4Core0AAuthorizationV2Error(RTA4Core0APilotV2Error):
    """Raised when a CORE-0A authorization candidate is invalid."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic_canonical_json(
    path: Path | str, value: Mapping[str, Any],
) -> None:
    """Durably replace a canonical JSON artifact without partial targets."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(target.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _builder_source_binding(source_root: Path | str) -> Dict[str, Any]:
    root = Path(source_root).resolve(strict=True)
    rows = []
    for relative in _BUILDER_SOURCE_PATHS:
        source = root / relative
        if not source.is_file():
            raise RTA4Core0AAuthorizationV2Error(
                f"authorization builder source is absent: {relative}"
            )
        rows.append({
            "path": relative,
            "sha256": _sha256(source),
            "size_bytes": source.stat().st_size,
        })
    material = {
        "source_contract": (
            "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTHORIZATION_BUILDER_SOURCE_V1"
        ),
        "ordered_sources": rows,
    }
    return {
        **material,
        "authorization_builder_source_identity": domain_hash(
            CORE0A_AUTHORIZATION_BUILDER_SOURCE_DOMAIN, material,
        ),
    }


def _canonical_nonce(value: str) -> str:
    if type(value) is not str:
        raise RTA4Core0AAuthorizationV2Error("run_nonce must be a string")
    if (
        not value
        or len(value) > CORE0A_AUTHORIZATION_NONCE_MAX_LENGTH
        or not value.isascii()
        or _NONCE_PATTERN.fullmatch(value) is None
        or "/" in value
        or "\\" in value
    ):
        raise RTA4Core0AAuthorizationV2Error(
            "run_nonce is not bounded canonical path-safe ASCII"
        )
    return value


def _canonical_timestamp(value: str, label: str) -> tuple[str, datetime]:
    if type(value) is not str:
        raise RTA4Core0AAuthorizationV2Error(f"{label} must be a string")
    try:
        parsed = datetime.strptime(
            value, CORE0A_AUTHORIZATION_TIMESTAMP_FORMAT,
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RTA4Core0AAuthorizationV2Error(
            f"{label} must use canonical UTC YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    if parsed.strftime(CORE0A_AUTHORIZATION_TIMESTAMP_FORMAT) != value:
        raise RTA4Core0AAuthorizationV2Error(
            f"{label} must use canonical UTC YYYY-MM-DDTHH:MM:SSZ"
        )
    return value, parsed


def _request_material(
    *, run_nonce: str, issued_at: str, expires_at: str,
) -> Dict[str, Any]:
    nonce = _canonical_nonce(run_nonce)
    issued_text, issued = _canonical_timestamp(issued_at, "issued_at")
    expires_text, expires = _canonical_timestamp(expires_at, "expires_at")
    validity = expires - issued
    if validity <= timedelta(0):
        raise RTA4Core0AAuthorizationV2Error(
            "expires_at must be later than issued_at"
        )
    if validity > timedelta(
        seconds=CORE0A_AUTHORIZATION_MAX_VALIDITY_SECONDS,
    ):
        raise RTA4Core0AAuthorizationV2Error(
            "authorization candidate validity exceeds 24 hours"
        )
    material = {
        "validity_contract": CORE0A_AUTHORIZATION_VALIDITY_CONTRACT,
        "max_validity_seconds": CORE0A_AUTHORIZATION_MAX_VALIDITY_SECONDS,
        "run_nonce": nonce,
        "issued_at": issued_text,
        "expires_at": expires_text,
    }
    return {
        **material,
        "authorization_request_identity": domain_hash(
            CORE0A_AUTHORIZATION_REQUEST_DOMAIN, material,
        ),
    }


def _selected_fields(
    source: Mapping[str, Any], names: tuple[str, ...],
) -> Dict[str, Any]:
    try:
        return {name: deepcopy(source[name]) for name in names}
    except KeyError as exc:
        raise RTA4Core0AAuthorizationV2Error(
            f"validated deployment lacks candidate field: {exc.args[0]}"
        ) from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _authorization_output_path(
    value: Path | str, validated: ValidatedCore0ADeployment,
) -> Path:
    target = Path(value)
    if not target.is_absolute():
        raise RTA4Core0AAuthorizationV2Error(
            "authorization_output_path must be absolute"
        )
    resolved = target.resolve(strict=False)
    source_root = Path(validated.source_root).resolve(strict=True)
    workspace_root = Path(
        validated.deployment_workspace_root,
    ).resolve(strict=True)
    if _is_within(resolved, source_root) or _is_within(
        resolved, workspace_root,
    ):
        raise RTA4Core0AAuthorizationV2Error(
            "authorization candidate output must be outside source and "
            "deployment workspace roots"
        )
    return resolved


def _ordered_record_identities(
    selection: Mapping[str, Any],
) -> tuple[list[str], str]:
    try:
        identities = [
            str(record["plan_record_identity"])
            for record in selection["ordered_records"]
        ]
    except (KeyError, TypeError) as exc:
        raise RTA4Core0AAuthorizationV2Error(
            "validated selection lacks ordered record identities"
        ) from exc
    if len(identities) != EXPECTED_EXECUTION_COUNT:
        raise RTA4Core0AAuthorizationV2Error(
            "authorization candidate requires exactly 384 records"
        )
    return identities, domain_hash(
        CORE0A_ORDERED_RECORD_IDENTITIES_DOMAIN, identities,
    )


def _candidate_material(
    validated: ValidatedCore0ADeployment,
    *, run_nonce: str, issued_at: str, expires_at: str,
) -> Dict[str, Any]:
    if type(validated) is not ValidatedCore0ADeployment:
        raise RTA4Core0AAuthorizationV2Error(
            "internal candidate construction requires formal deployment output"
        )
    portable = validated.portable_bundle
    deployment = validated.deployment_manifest
    selection = validated.selection
    request = _request_material(
        run_nonce=run_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    ordered_identities, ordered_digest = _ordered_record_identities(selection)
    if (
        deployment["selection_count"] != EXPECTED_EXECUTION_COUNT
        or deployment["max_runs"] != CORE0A_MAX_RUNS
        or deployment["authorization_scope"] != CORE0A_AUTHORIZATION_SCOPE
    ):
        raise RTA4Core0AAuthorizationV2Error(
            "validated deployment is outside the fixed CORE-0A scope"
        )
    if any(
        deployment[name] is not False
        for name in (
            "formal_authorization",
            "production_authorization",
            "engineering_pilot_authorization",
        )
    ):
        raise RTA4Core0AAuthorizationV2Error(
            "deployment candidate authorization flags must remain false"
        )
    source = {
        "git_commit": deployment["source_commit"],
        "git_tree": deployment["source_tree"],
        "clean_tracked_and_untracked_required": True,
        "portable_observed_clean": portable["source"]["observed_clean"],
    }
    selection_binding = {
        "artifact_sha256": portable["selection"]["artifact_sha256"],
        "selection_identity": deployment["selection_identity"],
        "selection_count": deployment["selection_count"],
        "ordered_record_identity_count": len(ordered_identities),
        "ordered_record_identities_digest": ordered_digest,
    }
    identity_binding = {
        "portable_freeze_identity": deployment["portable_freeze_identity"],
        **_selected_fields(deployment, _PRODUCTION_IDENTITY_FIELDS),
        "deployment_manifest_identity": deployment[
            "deployment_manifest_identity"
        ],
        "combined_execution_identity": core0a_execution_identity(validated),
        "candidate_config_identity": deployment["scientific_inputs"][
            "candidate_config_identity"
        ],
    }
    scope = {
        "authorization_scope": CORE0A_AUTHORIZATION_SCOPE,
        "authorized_cores": list(_AUTHORIZED_CORES),
        "forbidden_cores": list(_FORBIDDEN_CORES),
        "selection_count": EXPECTED_EXECUTION_COUNT,
        "max_runs": CORE0A_MAX_RUNS,
        "result_usage": CORE0A_RESULT_USAGE,
        "paper_result_eligible": False,
    }
    authorization_state = {
        "formal_authorization": False,
        "production_authorization": False,
        "engineering_pilot_authorization": False,
        "executable_authorization": False,
        "authorization_review_passed": False,
        "pilot_execution_allowed": False,
    }
    return {
        "authorization_schema": CORE0A_AUTHORIZATION_CANDIDATE_SCHEMA,
        "authorization_contract_version": (
            CORE0A_AUTHORIZATION_CANDIDATE_CONTRACT_VERSION
        ),
        "status": CORE0A_AUTHORIZATION_CANDIDATE_STATUS,
        "artifact_kind": (
            "NON_EXECUTABLE_ENGINEERING_AUTHORIZATION_CANDIDATE"
        ),
        "execution_environment_classification": deployment[
            "execution_environment_classification"
        ],
        "source": source,
        "selection": selection_binding,
        "identities": identity_binding,
        "scientific_inputs": deepcopy(deployment["scientific_inputs"]),
        "paths": _selected_fields(deployment, _PATH_FIELDS),
        "resources": _selected_fields(deployment, _RESOURCE_FIELDS),
        "disk_contract": _selected_fields(deployment, _DISK_FIELDS),
        "scope": scope,
        "authorization_state": authorization_state,
        "request": request,
        "builder_source": _builder_source_binding(validated.source_root),
    }


def _build_candidate(
    validated: ValidatedCore0ADeployment,
    *, run_nonce: str, issued_at: str, expires_at: str,
) -> Dict[str, Any]:
    material = _candidate_material(
        validated,
        run_nonce=run_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return {
        **material,
        "authorization_candidate_identity": domain_hash(
            CORE0A_AUTHORIZATION_CANDIDATE_DOMAIN, material,
        ),
    }


def build_core0a_authorization_candidate_v2(
    *,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    authorization_output_path: Path | str,
    run_nonce: str,
    issued_at: str,
    expires_at: str,
) -> Dict[str, Any]:
    """Revalidate seven frozen inputs and write a non-executable candidate."""

    validated = validate_autodl_deployment_manifest_v2(
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    candidate = _build_candidate(
        validated,
        run_nonce=run_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    output = _authorization_output_path(
        authorization_output_path, validated,
    )
    _write_atomic_canonical_json(output, candidate)
    return candidate


def validate_core0a_authorization_candidate_v2(
    *,
    authorization_candidate_path: Path | str,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    """Revalidate deployment paths, reconstruct every field, and fail closed."""

    candidate = load_strict_canonical_json(authorization_candidate_path)
    try:
        request = candidate["request"]
        nonce = request["run_nonce"]
        issued_at = request["issued_at"]
        expires_at = request["expires_at"]
    except (KeyError, TypeError) as exc:
        raise RTA4Core0AAuthorizationV2Error(
            "authorization candidate request material is absent"
        ) from exc
    request_expected = _request_material(
        run_nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    if request != request_expected:
        raise RTA4Core0AAuthorizationV2Error(
            "authorization request identity/material mismatch"
        )
    _, expiry = _canonical_timestamp(expires_at, "expires_at")
    if datetime.now(timezone.utc) >= expiry:
        raise RTA4Core0AAuthorizationV2Error(
            "authorization candidate has expired"
        )
    validated = validate_autodl_deployment_manifest_v2(
        portable_bundle_path=portable_bundle_path,
        selection_artifact_path=selection_artifact_path,
        candidate_config_path=candidate_config_path,
        production_manifest_path=production_manifest_path,
        deployment_manifest_path=deployment_manifest_path,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
    )
    expected = _build_candidate(
        validated,
        run_nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    if candidate != expected:
        raise RTA4Core0AAuthorizationV2Error(
            "authorization candidate differs from reconstructed frozen scope"
        )
    return candidate


__all__ = [
    "CORE0A_AUTHORIZATION_CANDIDATE_CONTRACT_VERSION",
    "CORE0A_AUTHORIZATION_CANDIDATE_SCHEMA",
    "CORE0A_AUTHORIZATION_CANDIDATE_STATUS",
    "CORE0A_AUTHORIZATION_MAX_VALIDITY_SECONDS",
    "CORE0A_AUTHORIZATION_NONCE_MAX_LENGTH",
    "RTA4Core0AAuthorizationV2Error",
    "build_core0a_authorization_candidate_v2",
    "validate_core0a_authorization_candidate_v2",
]
