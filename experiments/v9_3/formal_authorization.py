"""Verifiable file authorization for CORE-1/CORE-2 formal execution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Optional

from .config import (
    TASK_WORKLOAD_CONTRACT_VERSION,
    config_hash,
    domain_hash,
)
from . import exact_energy


AUTHORIZATION_SCHEMA = "ASAP_BLOCK_V9_3_FORMAL_AUTHORIZATION_V1"
SEAL_SCHEMA = "ASAP_BLOCK_V9_3_FORMAL_AUTHORIZATION_SEAL_V1"
FORMAL_CONFIRMATION_TOKEN = "RUN_V9_3_FORMAL"
FORMAL_PARAMETER_STATUS = "FROZEN_FOR_FORMAL_EXECUTION"
FORMAL_TASKSET_STORE_SCHEMA = (
    "ASAP_BLOCK_V9_3_CORE12_PAIRING_MANIFEST_V3_EXACT_BINARY64"
)
CONFIG_BOUND_TASKSET_STORE_DOMAIN = (
    "ASAP_BLOCK:V9.3:CONFIG_BOUND_TASKSET_STORE_IDENTITY:v1"
)


class FormalAuthorizationError(RuntimeError):
    """Raised when a claimed formal run is not exactly authorized."""


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise FormalAuthorizationError(f"authorization-bound file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_path(value: Path | str, project_root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _repository_identity(project_root: Path) -> Dict[str, Any]:
    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=str(project_root), capture_output=True,
                text=True, check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise FormalAuthorizationError("cannot establish Git identity") from exc

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_tree": git("rev-parse", "HEAD^{tree}"),
        "repository_clean": not bool(git("status", "--porcelain")),
    }


def taskset_store_identity(path: Path | str) -> str:
    root = Path(path).resolve()
    manifest = root / "pairing_manifest.json"
    if not manifest.is_file():
        raise FormalAuthorizationError(
            "formal taskset store requires pairing_manifest.json"
        )
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormalAuthorizationError(
            "cannot read formal taskset store manifest"
        ) from exc
    contract = document.get("contract") if isinstance(document, dict) else None
    workload_contract = (
        contract.get("task_workload_contract")
        if isinstance(contract, dict) else None
    )
    if (
        not isinstance(document, dict)
        or document.get("schema") != FORMAL_TASKSET_STORE_SCHEMA
        or not isinstance(workload_contract, dict)
        or workload_contract.get("version") != TASK_WORKLOAD_CONTRACT_VERSION
    ):
        raise FormalAuthorizationError(
            "formal taskset store requires the exact-binary64 workload schema"
        )
    return domain_hash(
        "ASAP_BLOCK:V9.3:TASKSET_STORE_IDENTITY:v1",
        {
            "path": str(root),
            "pairing_manifest_sha256": _sha256(manifest),
        },
    )


def _worker_count(config: Mapping[str, Any]) -> int:
    analysis = config.get("analysis")
    if isinstance(analysis, Mapping) and "worker_count" in analysis:
        return int(analysis["worker_count"])
    execution = config.get("execution")
    if isinstance(execution, Mapping) and "worker_count" in execution:
        return int(execution["worker_count"])
    raise FormalAuthorizationError("formal config has no worker_count")


def _formal_profile_requires_authorization(config: Mapping[str, Any]) -> bool:
    core = config.get("core")
    experiment_id = str(config.get("experiment_id", ""))
    if core == "CORE-1":
        return experiment_id.startswith("asap-block-v9.3-core1-formal-final-")
    if core == "CORE-2":
        return experiment_id.startswith("asap-block-v9.3-core2-formal-final-")
    if core == "CORE-3":
        return experiment_id.startswith("asap-block-v9.3-core3-formal-")
    if core == "CORE-4":
        sensitivity = config.get("sensitivity")
        return (
            isinstance(sensitivity, Mapping)
            and sensitivity.get("profile") == "formal-sustainability-v1"
        )
    if core == "CORE-5":
        scalability = config.get("scalability")
        return (
            isinstance(scalability, Mapping)
            and scalability.get("profile")
            in {"formal-algorithmic-v1", "formal-workers-v1"}
        )
    return False


def requires_formal_authorization(config: Mapping[str, Any]) -> bool:
    return (
        config.get("parameter_status") == FORMAL_PARAMETER_STATUS
        or _formal_profile_requires_authorization(config)
    )


def _configured_store_identity(
    config: Mapping[str, Any], *, project_root: Path, store: Path,
) -> str:
    generation = config.get("generation")
    workload_contract = (
        generation.get("workload_contract")
        if isinstance(generation, Mapping) else None
    )
    return domain_hash(CONFIG_BOUND_TASKSET_STORE_DOMAIN, {
        "path": str(store),
        "core": config.get("core"),
        "experiment_id": config.get("experiment_id"),
        "config_semantic_hash": config_hash(config),
        "task_workload_contract_version": (
            workload_contract.get("version")
            if isinstance(workload_contract, Mapping) else None
        ),
        "project_root": str(project_root.resolve()),
    })


def expected_binding(
    config: Mapping[str, Any],
    *,
    project_root: Path,
    source_freeze_config: Path,
    prepared_config: Path,
) -> Dict[str, Any]:
    repository = _repository_identity(project_root)
    store = _project_path(config["execution"]["taskset_store"], project_root)
    if config.get("core") in {"CORE-1", "CORE-2"}:
        store_binding_mode = "PREMATERIALIZED_MANIFEST_V3_EXACT_BINARY64"
        store_identity = taskset_store_identity(store)
    else:
        store_binding_mode = "CONFIG_BOUND_RUNTIME_STORE_V1"
        store_identity = _configured_store_identity(
            config, project_root=project_root, store=store,
        )
    binding = {
        **repository,
        "source_freeze_config": str(source_freeze_config.resolve()),
        "source_freeze_config_sha256": _sha256(source_freeze_config),
        "prepared_config": str(prepared_config.resolve()),
        "prepared_config_sha256": _sha256(prepared_config),
        "config_semantic_hash": config_hash(config),
        "worker_count": _worker_count(config),
        "core": config["core"],
        "output_root": str(_project_path(
            config["execution"]["output_root"], project_root,
        )),
        "taskset_store": str(store),
        "taskset_store_binding_mode": store_binding_mode,
        "taskset_store_identity": store_identity,
        "theory_document_path": exact_energy.THEORY_DOCUMENT_PATH,
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
        "source_numeric_model": exact_energy.SOURCE_NUMERIC_MODEL,
    }
    simulation = config.get("simulation")
    if config.get("core") == "CORE-3" and isinstance(simulation, Mapping):
        simulator = _project_path(simulation["simulator_bin"], project_root)
        if not simulator.is_file() or not os.access(simulator, os.X_OK):
            raise FormalAuthorizationError(
                f"formal simulator binary is missing or not executable: {simulator}"
            )
        binding.update({
            "simulator_binary": str(simulator),
            "simulator_binary_sha256": _sha256(simulator),
        })
    return binding


def authorization_id(binding: Mapping[str, Any]) -> str:
    return domain_hash("ASAP_BLOCK:V9.3:FORMAL_AUTHORIZATION:v1", binding)


def make_authorization_document(binding: Mapping[str, Any]) -> Dict[str, Any]:
    """Create the exact document an independent authorization step signs off."""

    frozen = dict(binding)
    return {
        "schema": AUTHORIZATION_SCHEMA,
        "formal_confirmation_token": FORMAL_CONFIRMATION_TOKEN,
        "authorization_id": authorization_id(frozen),
        "binding": frozen,
    }


def nonformal_seal(config: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": SEAL_SCHEMA,
        "formal_large_scale_run": False,
        "authorization_id": None,
        "authorization_file": None,
        "authorization_file_sha256": None,
        "binding": {
            "config_semantic_hash": config_hash(config),
            "worker_count": _worker_count(config),
            "core": config["core"],
            "output_root": str(Path(config["execution"]["output_root"]).resolve()),
            "taskset_store": str(Path(config["execution"]["taskset_store"]).resolve()),
        },
    }


def verify_authorization(
    config: Mapping[str, Any],
    *,
    authorization_path: Optional[Path],
    source_freeze_config: Optional[Path],
    prepared_config: Optional[Path],
    project_root: Path,
) -> Dict[str, Any]:
    profile_requires_authorization = _formal_profile_requires_authorization(config)
    frozen = config.get("parameter_status") == FORMAL_PARAMETER_STATUS
    if profile_requires_authorization and not frozen:
        raise FormalAuthorizationError(
            "formal profile requires parameter_status=FROZEN_FOR_FORMAL_EXECUTION"
        )
    requires_authorization = frozen or profile_requires_authorization
    if authorization_path is None:
        if requires_authorization:
            raise FormalAuthorizationError(
                "frozen formal execution requires --formal-authorization and "
                "--source-freeze-config"
            )
        return nonformal_seal(config)
    if not frozen:
        raise FormalAuthorizationError(
            "formal authorization is valid only for a frozen formal config"
        )
    if source_freeze_config is None or prepared_config is None:
        raise FormalAuthorizationError(
            "formal authorization requires --source-freeze-config and the "
            "prepared config path"
        )
    try:
        document = json.loads(authorization_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FormalAuthorizationError("cannot read formal authorization") from exc
    binding = expected_binding(
        config,
        project_root=project_root,
        source_freeze_config=source_freeze_config,
        prepared_config=prepared_config,
    )
    expected = make_authorization_document(binding)
    if document != expected:
        raise FormalAuthorizationError("formal authorization binding mismatch")
    if not binding["repository_clean"]:
        raise FormalAuthorizationError("formal authorization requires a clean repository")
    return {
        "schema": SEAL_SCHEMA,
        "formal_large_scale_run": True,
        "authorization_id": expected["authorization_id"],
        "authorization_file": str(authorization_path.resolve()),
        "authorization_file_sha256": _sha256(authorization_path),
        "binding": binding,
    }


def revalidate_authorization_seal(
    config: Mapping[str, Any],
    seal: Mapping[str, Any],
    *,
    project_root: Path,
) -> Dict[str, Any]:
    binding = seal.get("binding")
    if (
        seal.get("schema") != SEAL_SCHEMA
        or seal.get("formal_large_scale_run") is not True
        or not isinstance(binding, Mapping)
    ):
        raise FormalAuthorizationError("invalid persisted formal authorization seal")
    try:
        authorization_path = Path(str(seal["authorization_file"]))
        source_path = Path(str(binding["source_freeze_config"]))
        prepared_path = Path(str(binding["prepared_config"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalAuthorizationError(
            "persisted formal authorization seal lacks bound paths"
        ) from exc
    current = verify_authorization(
        config,
        authorization_path=authorization_path,
        source_freeze_config=source_path,
        prepared_config=prepared_path,
        project_root=project_root,
    )
    if current != dict(seal):
        raise FormalAuthorizationError(
            "persisted formal authorization seal is no longer valid"
        )
    return current
