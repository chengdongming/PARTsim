"""Unified, fail-closed build/environment manifest for RTA4 formal V2.

The manifest records the controlled-research execution environment.  It is a
reproducibility binding, not an authorization document and not a claim that a
malicious local administrator can be detected.
"""

from __future__ import annotations

from importlib import metadata
import hashlib
import json
import locale
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
from typing import Any, Dict, Iterable, Mapping, Sequence

from . import exact_energy
from .rta4_core0a_repository_lineage_v1 import (
    Core0ARepositoryLineageV1Error,
    validate_core0a_repository_lineage_v1,
)
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_environment import load_strict_json
from .solar_parse_proof import (
    SOLAR_STOD_BUILD_STANDARD,
    SOLAR_STOD_NORMALIZED_COMPILE_ARGUMENTS,
    SOLAR_STOD_NUMERIC_LOCALE,
    SOLAR_STOD_PARSER_CONTRACT,
    VERIFIER_SOURCE_RELATIVE_PATH,
)


PRODUCTION_BUILD_MANIFEST_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_V2"
)
PRODUCTION_BUILD_MANIFEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST:v2"
)
PRODUCTION_BUILD_CLASSIFICATION = "CONTROLLED_RESEARCH_EXECUTION_ENVIRONMENT"
PRODUCTION_BUILD_PROFILE = "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY"
PRODUCTION_BUILD_NOT_AUTHORIZATION = True
PRODUCTION_CPP_STANDARD = "C++17"

THREAT_MODEL = {
    "classification": PRODUCTION_BUILD_CLASSIFICATION,
    "controlled_hosts": True,
    "sha256_binds_repository_inputs_and_binaries": True,
    "active_same_host_replacement_out_of_scope": True,
    "malicious_local_administrator_out_of_scope": True,
    "trusted_boot_hardware_attestation_out_of_scope": True,
    "purpose": "BUILD_AND_RUNTIME_REPRODUCIBILITY",
    "formal_authorization": False,
}

ENVIRONMENT_ALLOWLIST = (
    "PATH", "CC", "CXX", "LC_ALL", "LANG", "TZ", "PYTHONHASHSEED",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "RTA4_WORKER_COUNT", "RTA4_MAX_IN_FLIGHT",
)
PACKAGE_ALLOWLIST = ("PyYAML", "numpy", "pytest")

DEFAULT_RELEVANT_SOURCES = (
    "asap_block_rta.py",
    "asap_block_rta_v9_3.py",
    "asap_block_rta_v9_3_methods.py",
    "asap_block_rta_v9_3_ph.py",
    "asap_block_rta_v9_3_seq.py",
    "asap_block_rta_v9_3_taskset.py",
    "energy_manager.py",
    "global_task_generator.py",
    "solar_data_loader.py",
    "utils/unified_logger.py",
    "experiments/v9_3/__init__.py",
    "experiments/v9_3/cell_model.py",
    "experiments/v9_3/censoring.py",
    "experiments/v9_3/config.py",
    "experiments/v9_3/constrained_taskset_identity.py",
    "experiments/v9_3/exact_energy.py",
    "experiments/v9_3/release_applicability.py",
    "experiments/v9_3/result_writer.py",
    "experiments/v9_3/rta4_formal_config.py",
    "experiments/v9_3/rta4_formal_config_v2.py",
    "experiments/v9_3/rta4_formal_environment.py",
    "experiments/v9_3/rta4_formal_execution.py",
    "experiments/v9_3/rta4_formal_lifecycle_v2.py",
    "experiments/v9_3/rta4_formal_plan_grid.py",
    "experiments/v9_3/rta4_formal_plan_v2.py",
    "experiments/v9_3/rta4_formal_runner_v2.py",
    "experiments/v9_3/rta4_formal_schema.py",
    "experiments/v9_3/rta4_formal_schema_v2.py",
    "experiments/v9_3/rta4_formal_store.py",
    "experiments/v9_3/rta4_numeric_contract_v2.py",
    "experiments/v9_3/rta4_core0a_repository_lineage_v1.py",
    "experiments/v9_3/rta4_core3_contracts_v6.py",
    "experiments/v9_3/rta4_production_build_manifest.py",
    "experiments/v9_3/rta4_shared_energy.py",
    "experiments/v9_3/rta4_taskset_v2.py",
    "experiments/v9_3/simulation_engine.py",
    "experiments/v9_3/simulation_result.py",
    "experiments/v9_3/solar_parse_proof.py",
    "experiments/v9_3/task_identity.py",
    "scripts/build_v9_3_rta4_production_manifest.py",
    "scripts/build_v9_3_rta4_v2_contracts.py",
    "scripts/run_v9_3_rta4_formal.py",
    "configs/v9_3_rta4_shared_energy_support_v2.yaml",
    "configs/v9_3_rta4_core1_unauthorized_pre_pilot_v2_shared_energy.yaml",
    "configs/v9_3_rta4_core2_unauthorized_pre_pilot_v2_shared_energy.yaml",
    "configs/v9_3_rta4_core3_unauthorized_pre_pilot_v2_shared_energy.yaml",
    "configs/v9_3_rta4_core4_unauthorized_pre_pilot_v2_shared_energy.yaml",
    "configs/v9_3_rta4_core5a_unauthorized_pre_pilot_v2_shared_energy.yaml",
    "configs/v9_3_rta4_core5b_unauthorized_pre_pilot_v2_shared_energy.yaml",
    "data/processed/shenyang_solar_minute.csv",
    "librtsim/include/rtsim/scheduler/gpfp_asap_block_scheduler.hpp",
    "librtsim/scheduler/gpfp_asap_block_scheduler.cpp",
    "librtsim/scheduler/energy_bridge.cpp",
    "system_config_unified_template.yml",
    VERIFIER_SOURCE_RELATIVE_PATH,
)


class ProductionBuildManifestError(ValueError):
    """Raised when production build evidence is incomplete or has drifted."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_file(path: Path, label: str) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise OSError("not a regular file")
        return resolved.read_bytes()
    except OSError as exc:
        raise ProductionBuildManifestError(f"cannot read {label}: {path}") from exc


def _file_material(path: Path, *, root: Path | None = None) -> Dict[str, Any]:
    resolved = path.resolve(strict=True)
    payload = _read_file(resolved, str(path))
    display = str(resolved)
    if root is not None:
        try:
            display = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ProductionBuildManifestError(
                f"source path escapes repository: {resolved}"
            ) from exc
    return {
        "path": display,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _run(arguments: Sequence[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            list(arguments), cwd=None if cwd is None else str(cwd), check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as exc:
        raise ProductionBuildManifestError(
            f"cannot execute build identity command: {arguments[0]}"
        ) from exc
    if completed.returncode:
        raise ProductionBuildManifestError(
            f"build identity command failed: {' '.join(arguments)}"
        )
    return (completed.stdout + completed.stderr).strip()


def _git(root: Path, *arguments: str) -> str:
    return _run(("git", *arguments), cwd=root)


def _resolved_executable(value: Path | str, label: str) -> Path:
    candidate = str(value)
    selected = shutil.which(candidate) if not Path(candidate).is_absolute() else candidate
    if selected is None:
        raise ProductionBuildManifestError(f"cannot resolve {label}: {candidate}")
    path = Path(selected).resolve(strict=True)
    if not path.is_file() or not os.access(str(path), os.X_OK):
        raise ProductionBuildManifestError(f"{label} is not executable: {path}")
    return path


def _linked_libraries(binary: Path) -> list[Dict[str, str]]:
    output = _run(("ldd", str(binary)))
    rows = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=>" in line:
            name, remainder = line.split("=>", 1)
            location = remainder.strip().split(" ", 1)[0]
        else:
            fields = line.split()
            name = fields[0]
            location = fields[0] if fields[0].startswith("/") else "VIRTUAL"
        row: Dict[str, str] = {"soname": name.strip(), "resolved_path": location}
        if location.startswith("/") and Path(location).is_file():
            material = _file_material(Path(location))
            row["sha256"] = str(material["sha256"])
            row["size_bytes"] = str(material["size_bytes"])
        rows.append(row)
    return sorted(rows, key=lambda item: (item["soname"], item["resolved_path"]))


def _library_identity(rows: Sequence[Mapping[str, str]], prefix: str) -> Mapping[str, str]:
    matches = [row for row in rows if row["soname"].startswith(prefix)]
    if len(matches) != 1:
        raise ProductionBuildManifestError(f"cannot identify linked {prefix}")
    return dict(matches[0])


def _package_versions() -> list[Dict[str, str]]:
    result = []
    for name in PACKAGE_ALLOWLIST:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "NOT_INSTALLED"
        result.append({"distribution": name, "version": version})
    return result


def _normalized_commands(value: Mapping[str, Sequence[str]]) -> Dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != {"simulator", "verifier"}:
        raise ProductionBuildManifestError(
            "build_commands must contain exactly simulator and verifier"
        )
    normalized: Dict[str, list[str]] = {}
    for name in ("simulator", "verifier"):
        command = value[name]
        if (
            not isinstance(command, (list, tuple)) or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise ProductionBuildManifestError(f"invalid {name} build command")
        normalized[name] = list(command)
    return normalized


def _environment(environ: Mapping[str, str]) -> Dict[str, Any]:
    return {
        "allowlist": list(ENVIRONMENT_ALLOWLIST),
        "values": {
            name: environ[name]
            for name in ENVIRONMENT_ALLOWLIST if name in environ
        },
    }


def generate_production_build_manifest(
    *,
    source_root: Path | str,
    simulator_binary: Path | str,
    verifier_binary: Path | str,
    compiler: Path | str,
    build_commands: Mapping[str, Sequence[str]],
    relevant_source_paths: Iterable[Path | str] = DEFAULT_RELEVANT_SOURCES,
    environ: Mapping[str, str] | None = None,
    require_clean: bool = True,
    _lineage_validator: Any = None,
) -> Dict[str, Any]:
    """Capture the one environment selected before formal workers start."""

    from .rta4_formal_schema_v2 import formal_schema_hash_v2
    from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256

    root = Path(source_root).resolve(strict=True)
    if not (root / ".git").exists():
        raise ProductionBuildManifestError("source_root is not a git worktree")
    try:
        validator = validate_core0a_repository_lineage_v1 if _lineage_validator is None else _lineage_validator
        lineage = validator(
            source_root=root,
        )
    except Core0ARepositoryLineageV1Error as exc:
        raise ProductionBuildManifestError(
            "production repository lineage validation failed"
        ) from exc
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise ProductionBuildManifestError("production source worktree is dirty")
    commit = lineage.current_head_commit
    tree = lineage.current_head_tree
    sources = []
    seen = set()
    for raw in relevant_source_paths:
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else root / candidate
        material = _file_material(path, root=root)
        if material["path"] in seen:
            raise ProductionBuildManifestError("duplicate relevant source path")
        seen.add(material["path"])
        if _git(root, "ls-files", "--error-unmatch", "--", str(material["path"])) != material["path"]:
            raise ProductionBuildManifestError(
                f"relevant source is not tracked: {material['path']}"
            )
        sources.append(material)
    sources.sort(key=lambda row: row["path"])
    if not sources:
        raise ProductionBuildManifestError("relevant source closure is empty")

    compiler_path = _resolved_executable(compiler, "C++ compiler")
    simulator_path = _resolved_executable(simulator_binary, "simulator binary")
    verifier_path = _resolved_executable(verifier_binary, "solar verifier binary")
    simulator_libraries = _linked_libraries(simulator_path)
    verifier_libraries = _linked_libraries(verifier_path)
    simulator_stdlib = _library_identity(simulator_libraries, "libstdc++")
    verifier_stdlib = _library_identity(verifier_libraries, "libstdc++")
    simulator_glibc = _library_identity(simulator_libraries, "libc.so")
    verifier_glibc = _library_identity(verifier_libraries, "libc.so")
    if (
        simulator_stdlib.get("sha256") != verifier_stdlib.get("sha256")
        or simulator_glibc.get("sha256") != verifier_glibc.get("sha256")
    ):
        raise ProductionBuildManifestError(
            "simulator and verifier do not share stdlib/glibc identity"
        )

    commands = _normalized_commands(build_commands)
    env = os.environ if environ is None else environ
    verifier_source = _file_material(root / VERIFIER_SOURCE_RELATIVE_PATH, root=root)
    scheduler_sources = [
        row for row in sources
        if row["path"].startswith("librtsim/") and "asap" in row["path"].lower()
    ]
    if not scheduler_sources:
        raise ProductionBuildManifestError("scheduler source is absent from closure")
    system_template = _file_material(root / "system_config_unified_template.yml", root=root)
    python_path = Path(sys.executable).resolve(strict=True)
    manifest: Dict[str, Any] = {
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "classification": PRODUCTION_BUILD_CLASSIFICATION,
        "formal_profile": PRODUCTION_BUILD_PROFILE,
        "threat_model": THREAT_MODEL,
        "formal_authorization": False,
        "repository": {
            "source_root": str(root),
            "source_root_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_SOURCE_ROOT:v2",
                {
                    "git_commit": commit,
                    "git_tree": tree,
                    "repository_lineage_identity": (
                        lineage.repository_lineage_identity
                    ),
                    "sources": sources,
                },
            ),
            "git_commit": commit,
            "git_tree": tree,
            "repository_lineage": lineage.as_dict(),
            "repository_lineage_identity": (
                lineage.repository_lineage_identity
            ),
            "tracked_and_untracked_clean": not bool(status),
            "status_porcelain": status,
            "relevant_sources": sources,
            "theory_document": _file_material(
                root / exact_energy.THEORY_DOCUMENT_PATH, root=root,
            ),
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "formal_schema_sha256": formal_schema_hash_v2(),
            "formal_profile_id": PRODUCTION_BUILD_PROFILE,
        },
        "python": {
            "executable": _file_material(python_path),
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "packages": _package_versions(),
            "locale": locale.setlocale(locale.LC_NUMERIC),
            "timezone": env.get("TZ", "SYSTEM_DEFAULT"),
            "hash_seed_policy": env.get("PYTHONHASHSEED", "RANDOMIZED_DEFAULT"),
        },
        "cpp_toolchain": {
            "compiler": _file_material(compiler_path),
            "compiler_version": _run((str(compiler_path), "--version")),
            "cpp_standard": PRODUCTION_CPP_STANDARD,
            "cmake_version": _run(("cmake", "--version")).splitlines()[0],
            "numeric_locale": SOLAR_STOD_NUMERIC_LOCALE,
            "double_abi": {
                "size_bytes": struct.calcsize("d"),
                "binary64": sys.float_info.radix == 2 and sys.float_info.mant_dig == 53,
                "byteorder": sys.byteorder,
            },
            "libstdcxx": simulator_stdlib,
            "glibc": simulator_glibc,
        },
        "simulator": {
            "source_commit": commit,
            "build_command": commands["simulator"],
            "binary": _file_material(simulator_path),
            "linked_libraries": simulator_libraries,
            "scheduler_sources": scheduler_sources,
            "system_template": system_template,
        },
        "solar_verifier": {
            "source": verifier_source,
            "parser_contract": SOLAR_STOD_PARSER_CONTRACT,
            "compiler": _file_material(compiler_path),
            "compiler_version": _run((str(compiler_path), "--version")),
            "normalized_build_arguments": list(SOLAR_STOD_NORMALIZED_COMPILE_ARGUMENTS),
            "build_command": commands["verifier"],
            "binary": _file_material(verifier_path),
            "linked_libraries": verifier_libraries,
            "libstdcxx": verifier_stdlib,
            "glibc": verifier_glibc,
            "numeric_locale": SOLAR_STOD_NUMERIC_LOCALE,
            "cpp_standard": SOLAR_STOD_BUILD_STANDARD,
            "double_abi": {
                "size_bytes": struct.calcsize("d"),
                "binary64": sys.float_info.radix == 2 and sys.float_info.mant_dig == 53,
            },
        },
        "environment": _environment(env),
    }
    manifest["manifest_id"] = domain_hash(
        PRODUCTION_BUILD_MANIFEST_DOMAIN, manifest,
    )
    return manifest


def validate_production_build_manifest(
    manifest: Mapping[str, Any], *, require_clean: bool = True,
    environ: Mapping[str, str] | None = None,
    require_default_closure: bool = False,
) -> Dict[str, Any]:
    """Rebuild all live bindings and reject any manifest drift."""

    if not isinstance(manifest, Mapping):
        raise ProductionBuildManifestError("production manifest must be a mapping")
    document = dict(manifest)
    observed_id = document.pop("manifest_id", None)
    if (
        manifest.get("manifest_schema") != PRODUCTION_BUILD_MANIFEST_SCHEMA
        or manifest.get("classification") != PRODUCTION_BUILD_CLASSIFICATION
        or manifest.get("threat_model") != THREAT_MODEL
        or manifest.get("formal_authorization") is not False
        or domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN, document) != observed_id
    ):
        raise ProductionBuildManifestError("production manifest identity mismatch")
    try:
        root = Path(str(manifest["repository"]["source_root"]))
        sources = [row["path"] for row in manifest["repository"]["relevant_sources"]]
        simulator = Path(str(manifest["simulator"]["binary"]["path"]))
        verifier = Path(str(manifest["solar_verifier"]["binary"]["path"]))
        compiler = Path(str(manifest["cpp_toolchain"]["compiler"]["path"]))
        commands = {
            "simulator": manifest["simulator"]["build_command"],
            "verifier": manifest["solar_verifier"]["build_command"],
        }
    except Exception as exc:
        raise ProductionBuildManifestError("production manifest is incomplete") from exc
    missing_default = sorted(set(DEFAULT_RELEVANT_SOURCES).difference(sources))
    if require_default_closure and missing_default:
        raise ProductionBuildManifestError(
            f"production manifest source closure is incomplete: {missing_default}"
        )
    expected = generate_production_build_manifest(
        source_root=root,
        simulator_binary=simulator,
        verifier_binary=verifier,
        compiler=compiler,
        build_commands=commands,
        relevant_source_paths=sources,
        environ=environ,
        require_clean=require_clean,
    )
    if dict(manifest) != expected:
        raise ProductionBuildManifestError("production build/environment drift")
    return expected


def load_and_validate_production_build_manifest(
    path: Path | str, *, require_clean: bool = True,
    environ: Mapping[str, str] | None = None,
    require_default_closure: bool = False,
) -> Dict[str, Any]:
    try:
        document = load_strict_json(path)
    except Exception as exc:
        raise ProductionBuildManifestError(
            "cannot load strict production manifest JSON"
        ) from exc
    return validate_production_build_manifest(
        document, require_clean=require_clean, environ=environ,
        require_default_closure=require_default_closure,
    )


def write_production_build_manifest(path: Path | str, manifest: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(dict(manifest)) + "\n", encoding="utf-8")


__all__ = [
    "DEFAULT_RELEVANT_SOURCES", "ENVIRONMENT_ALLOWLIST",
    "PRODUCTION_BUILD_CLASSIFICATION", "PRODUCTION_BUILD_MANIFEST_DOMAIN",
    "PRODUCTION_BUILD_MANIFEST_SCHEMA", "PRODUCTION_BUILD_PROFILE",
    "ProductionBuildManifestError", "THREAT_MODEL",
    "generate_production_build_manifest",
    "load_and_validate_production_build_manifest",
    "validate_production_build_manifest", "write_production_build_manifest",
]
