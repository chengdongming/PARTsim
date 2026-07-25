"""Independent two-step authorization for the RTA4 formal execution domain.

This contract intentionally shares no schema, identity domain, seal, or
verification function with ``formal_authorization.py``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping

from .rta4_formal_config import RTA4_FORMAL_PROFILE, domain_hash
from .rta4_formal_environment import (
    RTA4_COMMAND_DOMAIN, RTA4_COMMAND_MANIFEST_VERSION,
    RTA4_DEPENDENCY_DOMAIN, RTA4_DEPENDENCY_MANIFEST_VERSION,
    RTA4_ENVIRONMENT_DOMAIN, RTA4_ENVIRONMENT_MANIFEST_VERSION,
    RTA4_HARDWARE_DOMAIN, RTA4_HARDWARE_MANIFEST_VERSION,
    RTA4_SIMULATOR_DOMAIN, RTA4_SIMULATOR_MANIFEST_VERSION,
    build_dependency_manifest, build_environment_manifest,
    build_hardware_manifest, build_simulator_manifest,
    validate_identity_manifest, validate_source_manifest,
)
from .rta4_formal_freeze import (
    RTA4_FROZEN_PARAMETER_STATUS, validate_freeze_manifest,
    validate_prepared_config,
)
from .rta4_formal_pilot import validate_pilot_manifest, validate_pilot_report


RTA4_PRODUCTION_AUTHORIZATION_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_AUTHORIZATION_V1"
)
RTA4_TEST_AUTHORIZATION_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_SYNTHETIC_TEST_AUTHORIZATION_V1"
)
RTA4_PRODUCTION_AUTHORIZATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_FORMAL_AUTHORIZATION:v1"
)
RTA4_TEST_AUTHORIZATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_SYNTHETIC_TEST_AUTHORIZATION:v1"
)
RTA4_PRODUCTION_SEAL_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_AUTHORIZATION_SEAL:v1"
RTA4_TEST_SEAL_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SYNTHETIC_TEST_SEAL:v1"
RTA4_AUTHORIZATION_FILENAME = "rta4_formal_authorization.json"


class RTA4AuthorizationError(RuntimeError):
    """Raised before any production mutation when authorization is invalid."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _absolute_existing_file(value: Path | str, label: str) -> Path:
    try:
        path = Path(value).resolve(strict=True)
    except OSError as exc:
        raise RTA4AuthorizationError(f"{label} does not exist") from exc
    if not path.is_file():
        raise RTA4AuthorizationError(f"{label} must be a file")
    return path


def _absolute_path(value: Path | str, label: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        raise RTA4AuthorizationError(f"{label} must be absolute")
    return str(path.resolve())


def _document_binding(
    path: Path | str, expected: Mapping[str, Any], identity_field: str,
) -> Dict[str, Any]:
    source = _absolute_existing_file(path, identity_field)
    import json
    try:
        observed = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RTA4AuthorizationError(
            f"cannot parse authorization input {source.name}"
        ) from exc
    if observed != dict(expected):
        raise RTA4AuthorizationError(
            f"authorization input bytes do not match {identity_field}"
        )
    return {
        "absolute_path": str(source),
        "sha256": _sha256(source),
        identity_field: expected[identity_field],
    }


def _validate_source_bindings(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4AuthorizationError("source closure bindings must be a mapping")
    normalized: Dict[str, Any] = {}
    for core, row in value.items():
        if not isinstance(core, str) or not isinstance(row, Mapping):
            raise RTA4AuthorizationError("invalid source closure binding")
        exact = {
            "source_core", "absolute_root", "plan_sha256",
            "closure_sha256", "authorization_id",
        }
        if set(row) != exact or row["source_core"] != core:
            raise RTA4AuthorizationError("source closure binding field mismatch")
        root = _absolute_path(row["absolute_root"], "source closure root")
        for key in ("plan_sha256", "closure_sha256", "authorization_id"):
            if not isinstance(row[key], str) or len(row[key]) != 64:
                raise RTA4AuthorizationError("source closure identity must be SHA-256")
        normalized[core] = {**dict(row), "absolute_root": root}
    return normalized


def _domains(test_mode: bool) -> tuple[str, str, str]:
    if type(test_mode) is not bool:
        raise RTA4AuthorizationError("test_mode must be a strict boolean")
    return (
        (
            RTA4_TEST_AUTHORIZATION_SCHEMA
            if test_mode else RTA4_PRODUCTION_AUTHORIZATION_SCHEMA
        ),
        (
            RTA4_TEST_AUTHORIZATION_DOMAIN
            if test_mode else RTA4_PRODUCTION_AUTHORIZATION_DOMAIN
        ),
        RTA4_TEST_SEAL_DOMAIN if test_mode else RTA4_PRODUCTION_SEAL_DOMAIN,
    )


def build_authorization_candidate(
    *, prepared_config: Mapping[str, Any],
    freeze_manifest: Mapping[str, Any],
    all_prepared_configs: Mapping[str, Mapping[str, Any]],
    pilot_manifest: Mapping[str, Any],
    pilot_report: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    dependency_manifest: Mapping[str, Any],
    environment_manifest: Mapping[str, Any],
    hardware_manifest: Mapping[str, Any],
    command_manifest: Mapping[str, Any],
    simulator_manifest: Mapping[str, Any],
    prepared_config_path: Path | str,
    freeze_manifest_path: Path | str,
    pilot_manifest_path: Path | str,
    pilot_report_path: Path | str,
    authorization_path: Path | str,
    source_closure_bindings: Mapping[str, Any] | None = None,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """Build an immutable candidate which still cannot authorize execution."""

    schema, domain, _ = _domains(test_mode)
    prepared = validate_prepared_config(prepared_config)
    validate_freeze_manifest(freeze_manifest, all_prepared_configs)
    validate_pilot_manifest(
        pilot_manifest,
        {
            core: row["source_config"]["validated_pre_pilot_config"]
            for core, row in all_prepared_configs.items()
        },
    )
    validate_pilot_report(pilot_report, pilot_manifest)
    if prepared["prepared_config_id"] != freeze_manifest[
        "prepared_config_ids"
    ].get(prepared["core"]):
        raise RTA4AuthorizationError("prepared config is absent from the freeze")
    if (
        prepared["pilot_manifest_id"] != pilot_manifest["pilot_manifest_id"]
        or prepared["pilot_report_id"] != pilot_report["pilot_report_id"]
    ):
        raise RTA4AuthorizationError("prepared config/pilot mismatch")
    source = validate_source_manifest(source_manifest)
    validate_identity_manifest(
        dependency_manifest, version=RTA4_DEPENDENCY_MANIFEST_VERSION,
        domain=RTA4_DEPENDENCY_DOMAIN,
    )
    validate_identity_manifest(
        environment_manifest, version=RTA4_ENVIRONMENT_MANIFEST_VERSION,
        domain=RTA4_ENVIRONMENT_DOMAIN,
    )
    validate_identity_manifest(
        hardware_manifest, version=RTA4_HARDWARE_MANIFEST_VERSION,
        domain=RTA4_HARDWARE_DOMAIN,
    )
    validate_identity_manifest(
        command_manifest, version=RTA4_COMMAND_MANIFEST_VERSION,
        domain=RTA4_COMMAND_DOMAIN,
    )
    validate_identity_manifest(
        simulator_manifest, version=RTA4_SIMULATOR_MANIFEST_VERSION,
        domain=RTA4_SIMULATOR_DOMAIN,
    )
    if command_manifest.get("core") != prepared["core"]:
        raise RTA4AuthorizationError("command/core mismatch")
    required_simulator = prepared["core"] == "CORE-3"
    if bool(simulator_manifest.get("required")) != required_simulator:
        raise RTA4AuthorizationError("simulator binding/core mismatch")
    operational = prepared["operational"]
    if required_simulator and simulator_manifest.get("absolute_path") != str(
        Path(operational["simulator_binary"]).resolve()
    ):
        raise RTA4AuthorizationError("prepared/simulator path mismatch")
    required_sources = set(operational["source_closures"])
    source_bindings = _validate_source_bindings(source_closure_bindings or {})
    if set(source_bindings) != required_sources:
        raise RTA4AuthorizationError("authorization source DAG mismatch")
    documents = {
        "prepared_config": _document_binding(
            prepared_config_path, prepared, "prepared_config_id",
        ),
        "freeze_manifest": _document_binding(
            freeze_manifest_path, freeze_manifest, "freeze_manifest_id",
        ),
        "pilot_manifest": _document_binding(
            pilot_manifest_path, pilot_manifest, "pilot_manifest_id",
        ),
        "pilot_report": _document_binding(
            pilot_report_path, pilot_report, "pilot_report_id",
        ),
    }
    authorization_absolute = _absolute_path(
        authorization_path, "authorization path",
    )
    binding = {
        "authorization_schema": schema,
        "profile": RTA4_FORMAL_PROFILE,
        "parameter_status": RTA4_FROZEN_PARAMETER_STATUS,
        "authorization_domain": (
            "SYNTHETIC_TEST" if test_mode else "FORMAL_PRODUCTION"
        ),
        "core": prepared["core"],
        "prepared_config_id": prepared["prepared_config_id"],
        "freeze_manifest_id": freeze_manifest["freeze_manifest_id"],
        "pilot_manifest_id": pilot_manifest["pilot_manifest_id"],
        "pilot_report_id": pilot_report["pilot_report_id"],
        "documents": documents,
        "authorization_absolute_path": authorization_absolute,
        "output_root": str(Path(operational["output_root"]).resolve()),
        "taskset_store": str(Path(operational["taskset_store"]).resolve()),
        "timeout_contract_id": prepared["timeout_contract_id"],
        "worker_contract": {
            "worker_count": operational["worker_count"],
            "max_in_flight": operational["max_in_flight"],
            "memory_limit_bytes": operational["memory_limit_bytes"],
        },
        "checkpoint_contract": {
            "checkpoint_interval_records": operational[
                "checkpoint_interval_records"
            ],
            "resume_policy": operational["resume_policy"],
        },
        "source_manifest": dict(source),
        "dependency_manifest": dict(dependency_manifest),
        "environment_manifest": dict(environment_manifest),
        "hardware_manifest": dict(hardware_manifest),
        "command_manifest": dict(command_manifest),
        "simulator_manifest": dict(simulator_manifest),
        "source_closure_bindings": source_bindings,
        "scientific_assertions": prepared["scientific_assertions"],
    }
    authorization_id = domain_hash(domain, binding)
    return {
        **binding,
        "authorization_id": authorization_id,
        "authorization_status": "CANDIDATE_REQUIRES_EXPLICIT_CONFIRMATION",
    }


def authorize_candidate(
    candidate: Mapping[str, Any], *,
    confirm_authorization_id: str,
    test_mode: bool = False,
) -> Dict[str, Any]:
    """Convert a candidate into a capability only on exact 256-bit confirmation."""

    schema, domain, seal_domain = _domains(test_mode)
    if not isinstance(candidate, Mapping):
        raise RTA4AuthorizationError("authorization candidate must be a mapping")
    if candidate.get("authorization_schema") != schema:
        raise RTA4AuthorizationError("authorization schema/domain mismatch")
    if candidate.get("authorization_status") != (
        "CANDIDATE_REQUIRES_EXPLICIT_CONFIRMATION"
    ):
        raise RTA4AuthorizationError("authorization input is not a candidate")
    binding = dict(candidate)
    observed_id = binding.pop("authorization_id", None)
    binding.pop("authorization_status", None)
    expected_id = domain_hash(domain, binding)
    if observed_id != expected_id:
        raise RTA4AuthorizationError("authorization candidate identity mismatch")
    if (
        not isinstance(confirm_authorization_id, str)
        or len(confirm_authorization_id) != 64
        or confirm_authorization_id != expected_id
    ):
        raise RTA4AuthorizationError(
            "exact --confirm-authorization-id is required"
        )
    status = (
        "AUTHORIZED_FOR_SYNTHETIC_TEST"
        if test_mode else "AUTHORIZED_FOR_FORMAL_EXECUTION"
    )
    material = {
        **binding,
        "authorization_id": expected_id,
        "authorization_status": status,
    }
    return {
        **material,
        "authorization_seal": domain_hash(seal_domain, material),
    }


def validate_authorization_document(
    document: Mapping[str, Any], *, allow_test: bool = False,
) -> Dict[str, Any]:
    """Validate sealed bytes; production rejects the TEST domain by default."""

    if not isinstance(document, Mapping):
        raise RTA4AuthorizationError("authorization document must be a mapping")
    test_mode = document.get("authorization_schema") == RTA4_TEST_AUTHORIZATION_SCHEMA
    if test_mode and not allow_test:
        raise RTA4AuthorizationError("TEST authorization is invalid for production")
    schema, domain, seal_domain = _domains(test_mode)
    if document.get("authorization_schema") != schema:
        raise RTA4AuthorizationError("unknown authorization schema")
    expected_status = (
        "AUTHORIZED_FOR_SYNTHETIC_TEST"
        if test_mode else "AUTHORIZED_FOR_FORMAL_EXECUTION"
    )
    if document.get("authorization_status") != expected_status:
        raise RTA4AuthorizationError("authorization is not active")
    material = dict(document)
    seal = material.pop("authorization_seal", None)
    if seal != domain_hash(seal_domain, material):
        raise RTA4AuthorizationError("authorization seal mismatch")
    binding = dict(material)
    observed = binding.pop("authorization_id", None)
    binding.pop("authorization_status", None)
    if observed != domain_hash(domain, binding):
        raise RTA4AuthorizationError("authorization binding identity mismatch")
    return dict(document)


def verify_live_authorization(
    document: Mapping[str, Any], *,
    command_manifest: Mapping[str, Any] | None = None,
    allow_test: bool = False,
) -> Dict[str, Any]:
    """Revalidate every live machine/source/path binding before each operation."""

    normalized = validate_authorization_document(document, allow_test=allow_test)
    validate_source_manifest(normalized["source_manifest"])
    dependencies = build_dependency_manifest(tuple(
        row["distribution"]
        for row in normalized["dependency_manifest"]["dependencies"]
    ))
    if dependencies != normalized["dependency_manifest"]:
        raise RTA4AuthorizationError("dependency environment drift")
    environment = build_environment_manifest(dependencies)
    if environment != normalized["environment_manifest"]:
        raise RTA4AuthorizationError("runtime environment drift")
    if build_hardware_manifest() != normalized["hardware_manifest"]:
        raise RTA4AuthorizationError("hardware environment drift")
    binary = normalized["simulator_manifest"].get("absolute_path")
    if build_simulator_manifest(binary) != normalized["simulator_manifest"]:
        raise RTA4AuthorizationError("simulator binary drift")
    if command_manifest is not None and dict(command_manifest) != normalized[
        "command_manifest"
    ]:
        raise RTA4AuthorizationError("formal command drift")
    for row in normalized["documents"].values():
        path = _absolute_existing_file(row["absolute_path"], "bound document")
        if _sha256(path) != row["sha256"]:
            raise RTA4AuthorizationError("bound authorization input changed")
    auth_path = Path(normalized["authorization_absolute_path"])
    if not auth_path.is_absolute():
        raise RTA4AuthorizationError("authorization path is not absolute")
    if str(Path(normalized["output_root"]).resolve()) != normalized["output_root"]:
        raise RTA4AuthorizationError("output root binding is not canonical")
    if str(Path(normalized["taskset_store"]).resolve()) != normalized[
        "taskset_store"
    ]:
        raise RTA4AuthorizationError("taskset store binding is not canonical")
    return normalized


__all__ = [
    "RTA4_AUTHORIZATION_FILENAME", "RTA4_PRODUCTION_AUTHORIZATION_DOMAIN",
    "RTA4_PRODUCTION_AUTHORIZATION_SCHEMA", "RTA4_PRODUCTION_SEAL_DOMAIN",
    "RTA4_TEST_AUTHORIZATION_DOMAIN", "RTA4_TEST_AUTHORIZATION_SCHEMA",
    "RTA4_TEST_SEAL_DOMAIN", "RTA4AuthorizationError",
    "authorize_candidate", "build_authorization_candidate",
    "validate_authorization_document", "verify_live_authorization",
]
