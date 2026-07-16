"""Verifiable file authorization for CORE-1/CORE-2 formal execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Optional

from .config import config_hash, domain_hash


AUTHORIZATION_SCHEMA = "ASAP_BLOCK_V9_3_FORMAL_AUTHORIZATION_V1"
SEAL_SCHEMA = "ASAP_BLOCK_V9_3_FORMAL_AUTHORIZATION_SEAL_V1"
FORMAL_CONFIRMATION_TOKEN = "RUN_V9_3_FORMAL"


class FormalAuthorizationError(RuntimeError):
    """Raised when a claimed formal run is not exactly authorized."""


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise FormalAuthorizationError(f"authorization-bound file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    return domain_hash(
        "ASAP_BLOCK:V9.3:TASKSET_STORE_IDENTITY:v1",
        {
            "path": str(root),
            "pairing_manifest_sha256": _sha256(manifest),
        },
    )


def expected_binding(
    config: Mapping[str, Any],
    *,
    project_root: Path,
    source_freeze_config: Path,
    prepared_config: Path,
) -> Dict[str, Any]:
    repository = _repository_identity(project_root)
    store = Path(config["execution"]["taskset_store"]).resolve()
    return {
        **repository,
        "source_freeze_config": str(source_freeze_config.resolve()),
        "source_freeze_config_sha256": _sha256(source_freeze_config),
        "prepared_config": str(prepared_config.resolve()),
        "prepared_config_sha256": _sha256(prepared_config),
        "config_semantic_hash": config_hash(config),
        "worker_count": int(config["analysis"]["worker_count"]),
        "core": config["core"],
        "output_root": str(Path(config["execution"]["output_root"]).resolve()),
        "taskset_store": str(store),
        "taskset_store_identity": taskset_store_identity(store),
    }


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
            "worker_count": int(config["analysis"]["worker_count"]),
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
    if authorization_path is None:
        return nonformal_seal(config)
    if source_freeze_config is None or prepared_config is None:
        raise FormalAuthorizationError(
            "formal authorization requires source and prepared config paths"
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
