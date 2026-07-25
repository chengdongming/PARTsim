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
    RTA4_DEPENDENCY_DOMAIN, RTA4_DEPENDENCY_MANIFEST_VERSION,
    RTA4_ENVIRONMENT_DOMAIN, RTA4_ENVIRONMENT_MANIFEST_VERSION,
    RTA4_HARDWARE_DOMAIN, RTA4_HARDWARE_MANIFEST_VERSION,
    RTA4_SIMULATOR_DOMAIN, RTA4_SIMULATOR_MANIFEST_VERSION,
    build_dependency_manifest, build_environment_manifest,
    build_hardware_manifest, build_simulator_manifest,
    load_strict_json, validate_command_manifest, validate_identity_manifest,
    validate_source_manifest,
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
RTA4_AUTHORIZATION_BINDING_FIELDS = frozenset({
    "authorization_schema", "profile", "parameter_status",
    "authorization_domain", "core", "prepared_config_id",
    "freeze_manifest_id", "pilot_manifest_id", "pilot_report_id",
    "pilot_closure_id",
    "documents", "authorization_absolute_path", "output_root",
    "taskset_store", "timeout_contract_id", "worker_contract",
    "checkpoint_contract", "source_manifest", "dependency_manifest",
    "environment_manifest", "hardware_manifest", "command_manifest",
    "simulator_manifest", "source_closure_bindings",
    "scientific_assertions", "config_semantic_hash",
    "source_config_binding", "formal_contract_bindings",
})


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
    try:
        observed = load_strict_json(source)
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


def _source_config_binding(prepared: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: prepared["source_config"][key]
        for key in (
            "absolute_path", "file_sha256", "config_semantic_hash",
            "pre_pilot_parameter_status",
        )
    }


def _formal_contract_bindings(prepared: Mapping[str, Any]) -> Dict[str, Any]:
    scientific_config = prepared["source_config"][
        "validated_pre_pilot_config"
    ]
    assertions = prepared["scientific_assertions"]
    core_plan = assertions["core_plans"][prepared["core"]]
    return {
        "schema_version": assertions["schema_version"],
        "schema_sha256": assertions["schema_sha256"],
        "plan_version": assertions["plan_version"],
        "core_plan_count": core_plan["count"],
        "core_plan_ordered_digest": core_plan["ordered_digest"],
        "core_plan_sha256": core_plan["plan_sha256"],
        "all_plan_digest": assertions["all_plan_digest"],
        "theory_document_sha256": scientific_config["identity"][
            "theory_document_sha256"
        ],
        "numeric_contract_sha256": scientific_config["identity"][
            "numeric_contract_sha256"
        ],
        "method_registry_identity": scientific_config["identity"][
            "method_registry_identity"
        ],
        "taskset_identity_contract": scientific_config["generation"][
            "taskset_identity_contract"
        ],
        "taskset_store_version": scientific_config["identity"][
            "taskset_store_version"
        ],
        "release_projection_contract": scientific_config["identity"][
            "release_projection_contract"
        ],
        "simulation_applicability_contract": scientific_config["identity"][
            "simulation_applicability_contract"
        ],
    }


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
        or prepared["pilot_closure_id"] != pilot_report["pilot_closure_id"]
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
    validate_command_manifest(command_manifest)
    if not test_mode and "commands" not in command_manifest:
        raise RTA4AuthorizationError(
            "production authorization requires the complete command chain"
        )
    validate_identity_manifest(
        simulator_manifest, version=RTA4_SIMULATOR_MANIFEST_VERSION,
        domain=RTA4_SIMULATOR_DOMAIN,
    )
    runtime = prepared["runtime_environment"]
    if (
        dict(dependency_manifest) != runtime["dependency_manifest"]
        or dict(environment_manifest) != runtime["environment_manifest"]
        or dict(hardware_manifest) != runtime["hardware_manifest"]
    ):
        raise RTA4AuthorizationError(
            "authorization runtime differs from prepared freeze"
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
    if any(
        source_bindings[core]["absolute_root"]
        != str(Path(operational["source_closures"][core]).resolve())
        for core in required_sources
    ):
        raise RTA4AuthorizationError(
            "source closure path differs from prepared configuration"
        )
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
    source_root = Path(source["repository_root"])
    for label, raw_path in (
        ("output root", operational["output_root"]),
        ("taskset store", operational["taskset_store"]),
        ("authorization document", authorization_absolute),
    ):
        try:
            Path(raw_path).resolve().relative_to(source_root)
        except ValueError:
            pass
        else:
            raise RTA4AuthorizationError(
                f"{label} must be outside the authorized source worktree"
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
        "pilot_closure_id": pilot_report["pilot_closure_id"],
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
        "config_semantic_hash": prepared["source_config"][
            "config_semantic_hash"
        ],
        "source_config_binding": _source_config_binding(prepared),
        "formal_contract_bindings": _formal_contract_bindings(prepared),
    }
    if set(binding) != RTA4_AUTHORIZATION_BINDING_FIELDS:
        raise RTA4AuthorizationError("authorization binding field set drift")
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
    if set(binding) != RTA4_AUTHORIZATION_BINDING_FIELDS:
        raise RTA4AuthorizationError("authorization candidate field set mismatch")
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
    if set(document) != (
        RTA4_AUTHORIZATION_BINDING_FIELDS
        | {"authorization_id", "authorization_status", "authorization_seal"}
    ):
        raise RTA4AuthorizationError("authorization document field set mismatch")
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
    validate_command_manifest(normalized["command_manifest"])
    _validate_source_bindings(normalized["source_closure_bindings"])
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
    prepared = validate_prepared_config(load_strict_json(
        normalized["documents"]["prepared_config"]["absolute_path"]
    ))
    operational = prepared["operational"]
    if (
        prepared["prepared_config_id"] != normalized["prepared_config_id"]
        or prepared["core"] != normalized["core"]
        or prepared["pilot_manifest_id"] != normalized["pilot_manifest_id"]
        or prepared["pilot_report_id"] != normalized["pilot_report_id"]
        or prepared["pilot_closure_id"] != normalized["pilot_closure_id"]
        or prepared["timeout_contract_id"] != normalized["timeout_contract_id"]
        or prepared["scientific_assertions"] != normalized[
            "scientific_assertions"
        ]
        or prepared["source_config"]["config_semantic_hash"]
        != normalized["config_semantic_hash"]
        or _source_config_binding(prepared) != normalized[
            "source_config_binding"
        ]
        or _formal_contract_bindings(prepared) != normalized[
            "formal_contract_bindings"
        ]
        or operational["output_root"] != normalized["output_root"]
        or operational["taskset_store"] != normalized["taskset_store"]
        or {
            "worker_count": operational["worker_count"],
            "max_in_flight": operational["max_in_flight"],
            "memory_limit_bytes": operational["memory_limit_bytes"],
        } != normalized["worker_contract"]
        or {
            "checkpoint_interval_records": operational[
                "checkpoint_interval_records"
            ],
            "resume_policy": operational["resume_policy"],
        } != normalized["checkpoint_contract"]
        or prepared["runtime_environment"]["dependency_manifest"]
        != normalized["dependency_manifest"]
        or prepared["runtime_environment"]["environment_manifest"]
        != normalized["environment_manifest"]
        or prepared["runtime_environment"]["hardware_manifest"]
        != normalized["hardware_manifest"]
    ):
        raise RTA4AuthorizationError(
            "authorization differs from its bound prepared configuration"
        )
    source_bindings = _validate_source_bindings(
        normalized["source_closure_bindings"]
    )
    if (
        set(source_bindings) != set(operational["source_closures"])
        or any(
            source_bindings[core]["absolute_root"]
            != operational["source_closures"][core]
            for core in source_bindings
        )
    ):
        raise RTA4AuthorizationError("live authorization source DAG drift")
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
