"""Canonical runtime and source manifests for authorized RTA4 execution.

The manifests in this module are deliberately data-only.  They never copy
environment variables wholesale and never serialize command output which may
contain credentials.
"""

from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Dict, Iterable, Mapping, Sequence

from .rta4_formal_config import domain_hash


RTA4_SOURCE_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_SOURCE_MANIFEST_V1"
RTA4_DEPENDENCY_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_DEPENDENCY_MANIFEST_V1"
RTA4_ENVIRONMENT_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_ENVIRONMENT_MANIFEST_V1"
RTA4_HARDWARE_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_HARDWARE_MANIFEST_V1"
RTA4_COMMAND_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_COMMAND_MANIFEST_V1"
RTA4_SIMULATOR_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_SIMULATOR_MANIFEST_V1"

RTA4_SOURCE_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SOURCE_MANIFEST:v1"
RTA4_DEPENDENCY_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_DEPENDENCY_MANIFEST:v1"
RTA4_ENVIRONMENT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_ENVIRONMENT_MANIFEST:v1"
RTA4_HARDWARE_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_HARDWARE_MANIFEST:v1"
RTA4_COMMAND_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_COMMAND_MANIFEST:v1"
RTA4_SIMULATOR_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SIMULATOR_MANIFEST:v1"

SAFE_ENVIRONMENT_VARIABLES = (
    "LANG", "LC_ALL", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "PYTHONHASHSEED", "TZ",
)
DEFAULT_DEPENDENCIES = ("matplotlib", "numpy", "pytest", "PyYAML")


class RTA4EnvironmentError(ValueError):
    """Raised when runtime/source evidence is incomplete or non-canonical."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_strict_json(path: Path | str) -> Any:
    """Load JSON while rejecting duplicate keys and non-finite constants."""

    source = Path(path)

    def pairs(values: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise RTA4EnvironmentError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise RTA4EnvironmentError(f"non-finite JSON constant: {value}")

    try:
        return json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
    except RTA4EnvironmentError:
        raise
    except Exception as exc:
        raise RTA4EnvironmentError(
            f"cannot parse strict JSON: {source}"
        ) from exc


def _identity(version: str, domain: str, material: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {"manifest_version": version, **dict(material)}
    return {**payload, "manifest_id": domain_hash(domain, payload)}


def _git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *args), cwd=repo, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RTA4EnvironmentError(f"cannot inspect git state: {' '.join(args)}") from exc
    return completed.stdout.strip()


def build_source_manifest(
    repo_root: Path | str, source_paths: Iterable[Path | str], *,
    require_clean: bool = True,
) -> Dict[str, Any]:
    """Bind an explicit tracked source closure to commit, tree, and bytes."""

    root = Path(repo_root).resolve(strict=True)
    if not (root / ".git").exists():
        # Linked worktrees use a .git control file.
        if not (root / ".git").is_file():
            raise RTA4EnvironmentError("source root is not a git worktree")
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise RTA4EnvironmentError("source worktree must be clean")
    rows = []
    seen: set[str] = set()
    for raw in source_paths:
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError) as exc:
            raise RTA4EnvironmentError("source closure path escapes or is absent") from exc
        if relative in seen or not resolved.is_file():
            raise RTA4EnvironmentError("source closure paths must be unique files")
        tracked = _git(root, "ls-files", "--error-unmatch", "--", relative)
        if tracked != relative:
            raise RTA4EnvironmentError(f"source closure is not tracked: {relative}")
        seen.add(relative)
        rows.append({
            "path": relative,
            "size_bytes": resolved.stat().st_size,
            "sha256": _sha256(resolved),
        })
    if not rows:
        raise RTA4EnvironmentError("source closure must not be empty")
    rows.sort(key=lambda row: row["path"])
    return _identity(RTA4_SOURCE_MANIFEST_VERSION, RTA4_SOURCE_DOMAIN, {
        "repository_root": str(root),
        "git_commit": commit,
        "git_tree": tree,
        "git_clean": not bool(status),
        "files": rows,
    })


def validate_source_manifest(
    manifest: Mapping[str, Any], *, require_clean: bool = True,
) -> Dict[str, Any]:
    try:
        root = Path(str(manifest["repository_root"]))
        paths = [row["path"] for row in manifest["files"]]
    except Exception as exc:
        raise RTA4EnvironmentError("source manifest is incomplete") from exc
    expected = build_source_manifest(root, paths, require_clean=require_clean)
    if dict(manifest) != expected:
        raise RTA4EnvironmentError("source manifest is stale or mismatched")
    return expected


def build_dependency_manifest(
    distributions: Sequence[str] = DEFAULT_DEPENDENCIES,
) -> Dict[str, Any]:
    if (
        not isinstance(distributions, (tuple, list))
        or not distributions
        or any(not isinstance(name, str) or not name for name in distributions)
        or tuple(sorted(set(distributions), key=str.casefold)) != tuple(distributions)
    ):
        raise RTA4EnvironmentError(
            "dependency names must be non-empty, unique, casefold-sorted"
        )
    rows = []
    for name in distributions:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "NOT_INSTALLED"
        rows.append({"distribution": name, "version": version})
    return _identity(RTA4_DEPENDENCY_MANIFEST_VERSION, RTA4_DEPENDENCY_DOMAIN, {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "dependencies": rows,
    })


def build_environment_manifest(
    dependency_manifest: Mapping[str, Any], *,
    environ: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    environment = os.environ if environ is None else environ
    safe = {
        name: environment[name]
        for name in SAFE_ENVIRONMENT_VARIABLES if name in environment
    }
    return _identity(RTA4_ENVIRONMENT_MANIFEST_VERSION, RTA4_ENVIRONMENT_DOMAIN, {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_cache_tag": sys.implementation.cache_tag,
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "dependency_manifest_id": dependency_manifest.get("manifest_id"),
        "safe_environment": safe,
    })


def _memory_bytes() -> int:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            fields = line.split()
            return int(fields[1]) * 1024
    return 0


def build_hardware_manifest() -> Dict[str, Any]:
    return _identity(RTA4_HARDWARE_MANIFEST_VERSION, RTA4_HARDWARE_DOMAIN, {
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count() or 0,
        "physical_memory_bytes": _memory_bytes(),
    })


def build_command_manifest(
    argv: Sequence[str], *, cwd: Path | str,
    operation: str, core: str,
) -> Dict[str, Any]:
    if (
        not isinstance(argv, (tuple, list))
        or not argv
        or any(not isinstance(value, str) or not value for value in argv)
    ):
        raise RTA4EnvironmentError("command argv must be a non-empty string vector")
    if operation not in {"execute", "resume", "validate-only"}:
        raise RTA4EnvironmentError("unknown formal command operation")
    workdir = Path(cwd).resolve(strict=True)
    return _identity(RTA4_COMMAND_MANIFEST_VERSION, RTA4_COMMAND_DOMAIN, {
        "argv": list(argv),
        "cwd": str(workdir),
        "operation": operation,
        "core": core,
    })


def build_command_chain_manifest(
    commands: Mapping[str, Sequence[str]], *, cwd: Path | str, core: str,
) -> Dict[str, Any]:
    required = ("execute", "resume", "validate-only", "audit", "aggregate", "plot")
    if not isinstance(commands, Mapping) or set(commands) != set(required):
        raise RTA4EnvironmentError(
            "formal command chain must cover execute/resume/validate/audit/aggregate/plot"
        )
    normalized = {}
    for operation in required:
        argv = commands[operation]
        if (
            not isinstance(argv, (tuple, list))
            or not argv
            or any(not isinstance(value, str) or not value for value in argv)
        ):
            raise RTA4EnvironmentError(
                f"command chain {operation} argv is invalid"
            )
        normalized[operation] = list(argv)
    return _identity(RTA4_COMMAND_MANIFEST_VERSION, RTA4_COMMAND_DOMAIN, {
        "command_order": list(required),
        "commands": normalized,
        "cwd": str(Path(cwd).resolve(strict=True)),
        "core": core,
    })


def validate_command_invocation(
    manifest: Mapping[str, Any], *, argv: Sequence[str],
    cwd: Path | str, operation: str, core: str,
) -> None:
    validate_command_manifest(manifest)
    expected_cwd = str(Path(cwd).resolve(strict=True))
    if manifest.get("core") != core or manifest.get("cwd") != expected_cwd:
        raise RTA4EnvironmentError("command invocation core/cwd drift")
    if "commands" in manifest:
        expected = manifest["commands"].get(operation)
    else:
        expected = (
            manifest.get("argv")
            if manifest.get("operation") == operation else None
        )
    if list(argv) != expected:
        raise RTA4EnvironmentError("command invocation argv/operation drift")


def validate_command_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate either the legacy single operation or the complete command chain."""

    normalized = validate_identity_manifest(
        manifest, version=RTA4_COMMAND_MANIFEST_VERSION,
        domain=RTA4_COMMAND_DOMAIN,
    )
    if not isinstance(normalized.get("core"), str) or not normalized["core"]:
        raise RTA4EnvironmentError("command manifest core is invalid")
    cwd = normalized.get("cwd")
    if (
        not isinstance(cwd, str)
        or not Path(cwd).is_absolute()
        or str(Path(cwd).resolve()) != cwd
    ):
        raise RTA4EnvironmentError("command manifest cwd is not canonical")
    if "commands" in normalized:
        required = (
            "execute", "resume", "validate-only", "audit", "aggregate", "plot",
        )
        if (
            set(normalized) != {
                "manifest_version", "command_order", "commands", "cwd",
                "core", "manifest_id",
            }
            or normalized.get("command_order") != list(required)
            or not isinstance(normalized["commands"], Mapping)
            or set(normalized["commands"]) != set(required)
        ):
            raise RTA4EnvironmentError("command chain structure is invalid")
        vectors = normalized["commands"].values()
    else:
        if (
            set(normalized) != {
                "manifest_version", "argv", "cwd", "operation", "core",
                "manifest_id",
            }
            or normalized.get("operation")
            not in {"execute", "resume", "validate-only"}
        ):
            raise RTA4EnvironmentError("single command structure is invalid")
        vectors = (normalized.get("argv"),)
    if any(
        not isinstance(vector, (tuple, list))
        or not vector
        or any(not isinstance(value, str) or not value for value in vector)
        for vector in vectors
    ):
        raise RTA4EnvironmentError("command manifest argv is invalid")
    return normalized


def validate_bound_source_file(
    manifest: Mapping[str, Any], path: Path | str,
) -> Dict[str, Any]:
    """Require a runtime support file to be present byte-for-byte in source closure."""

    normalized = validate_source_manifest(manifest)
    root = Path(normalized["repository_root"])
    try:
        resolved = Path(path).resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise RTA4EnvironmentError(
            "runtime support file is outside the authorized source closure"
        ) from exc
    matches = [row for row in normalized["files"] if row["path"] == relative]
    if len(matches) != 1:
        raise RTA4EnvironmentError(
            "runtime support file is absent from the authorized source closure"
        )
    row = matches[0]
    if (
        not resolved.is_file()
        or resolved.stat().st_size != row["size_bytes"]
        or _sha256(resolved) != row["sha256"]
    ):
        raise RTA4EnvironmentError("runtime support file byte identity drift")
    return dict(row)


def build_simulator_manifest(binary: Path | str | None) -> Dict[str, Any]:
    if binary is None:
        return _identity(RTA4_SIMULATOR_MANIFEST_VERSION, RTA4_SIMULATOR_DOMAIN, {
            "required": False, "absolute_path": None, "sha256": None,
            "size_bytes": None, "executable": None,
        })
    path = Path(binary).resolve(strict=True)
    if not path.is_file():
        raise RTA4EnvironmentError("simulator path must identify a file")
    return _identity(RTA4_SIMULATOR_MANIFEST_VERSION, RTA4_SIMULATOR_DOMAIN, {
        "required": True,
        "absolute_path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "executable": os.access(path, os.X_OK),
    })


def validate_identity_manifest(
    manifest: Mapping[str, Any], *, version: str, domain: str,
) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise RTA4EnvironmentError("identity manifest must be a mapping")
    material = dict(manifest)
    observed = material.pop("manifest_id", None)
    if material.get("manifest_version") != version:
        raise RTA4EnvironmentError("manifest version mismatch")
    if domain_hash(domain, material) != observed:
        raise RTA4EnvironmentError("manifest identity mismatch")
    return dict(manifest)


__all__ = [
    "DEFAULT_DEPENDENCIES", "RTA4_COMMAND_DOMAIN",
    "RTA4_COMMAND_MANIFEST_VERSION", "RTA4_DEPENDENCY_DOMAIN",
    "RTA4_DEPENDENCY_MANIFEST_VERSION", "RTA4_ENVIRONMENT_DOMAIN",
    "RTA4_ENVIRONMENT_MANIFEST_VERSION", "RTA4_HARDWARE_DOMAIN",
    "RTA4_HARDWARE_MANIFEST_VERSION", "RTA4_SIMULATOR_DOMAIN",
    "RTA4_SIMULATOR_MANIFEST_VERSION", "RTA4_SOURCE_DOMAIN",
    "RTA4_SOURCE_MANIFEST_VERSION", "RTA4EnvironmentError",
    "SAFE_ENVIRONMENT_VARIABLES", "build_command_manifest",
    "build_command_chain_manifest",
    "build_dependency_manifest", "build_environment_manifest",
    "build_hardware_manifest", "build_simulator_manifest",
    "build_source_manifest", "load_strict_json", "validate_identity_manifest",
    "validate_bound_source_file", "validate_command_invocation",
    "validate_command_manifest", "validate_source_manifest",
]
