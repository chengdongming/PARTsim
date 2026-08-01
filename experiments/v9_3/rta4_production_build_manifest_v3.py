"""Independent production build manifest for parameterized RTA4 V3."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .rta4_formal_config import domain_hash
from .rta4_formal_schema_v3 import formal_schema_hash_v3
from .rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES, generate_production_build_manifest,
)


PRODUCTION_BUILD_MANIFEST_SCHEMA_V3 = (
    "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_V3_"
    "PARAMETERIZED_PHYSICAL_CORE_SLOTS_R1"
)
PRODUCTION_BUILD_PROFILE_V3 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_V3_PARAMETERIZED_SHARED_ENERGY_"
    "PHYSICAL_CORE_SLOTS_R1"
)
PRODUCTION_BUILD_MANIFEST_DOMAIN_V3 = (
    "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST:"
    "v3-physical-core-slots-r1"
)
V3_LINEAGE_SCHEMA = "ASAP_BLOCK_V9_3_RTA4_V3_EXECUTION_LINEAR_LINEAGE_V3"
V3_LINEAGE_ANCHOR = "5acde530eb6b68f6e3a5bc2e6c496307690a054d"
V3_EXECUTION_PATHS = frozenset({
    "experiments/v9_3/rta4_formal_config_v3.py",
    "experiments/v9_3/rta4_formal_execution.py",
    "experiments/v9_3/rta4_formal_plan_v3.py",
    "experiments/v9_3/rta4_formal_workers_v3.py",
    "experiments/v9_3/rta4_formal_lifecycle_v3.py",
    "experiments/v9_3/rta4_physical_core_slots_v3.py",
    "experiments/v9_3/rta4_formal_schema_v3.py",
    "experiments/v9_3/rta4_formal_runner_v3.py",
    "experiments/v9_3/rta4_production_build_manifest.py",
    "experiments/v9_3/rta4_production_build_manifest_v3.py",
    "experiments/v9_3/rta4_shared_energy.py",
    "scripts/run_v9_3_rta4_formal.py",
    "scripts/create_v9_3_rta4_campaign.py",
    "scripts/build_v9_3_rta4_production_manifest.py",
    "scripts/benchmark_v9_3_rta4_physical_workers.py",
    "test/test_v9_3_rta4_parameterized_campaign_v3.py",
    "test/test_v9_3_rta4_formal_runner_v3.py",
    "test/test_v9_3_rta4_physical_core_slots_v3.py",
    "test/test_v9_3_rta4_production_build_manifest_v3.py",
    "docs/experiments/v9_3_rta4_parameterized_campaigns_v3.md",
})
V3_RELEVANT_SOURCES = tuple(dict.fromkeys((*DEFAULT_RELEVANT_SOURCES,
    "experiments/v9_3/rta4_formal_config_v3.py",
    "experiments/v9_3/rta4_formal_plan_v3.py",
    "experiments/v9_3/rta4_formal_lifecycle_v3.py",
    "experiments/v9_3/rta4_formal_schema_v3.py",
    "experiments/v9_3/rta4_formal_runner_v3.py",
    "experiments/v9_3/rta4_formal_workers_v3.py",
    "experiments/v9_3/rta4_physical_core_slots_v3.py",
    "experiments/v9_3/rta4_production_build_manifest_v3.py",
    "scripts/create_v9_3_rta4_campaign.py",
    "scripts/benchmark_v9_3_rta4_physical_workers.py",
)))


class ProductionBuildManifestV3Error(ValueError):
    pass


def _git(root: Path, *args: str) -> str:
    import subprocess
    completed = subprocess.run(
        ("git", *args), cwd=root, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise ProductionBuildManifestV3Error(f"git {' '.join(args)} failed")
    return completed.stdout.strip()


@dataclass(frozen=True)
class V3Lineage:
    current_head_commit: str
    current_head_tree: str
    repository_lineage_identity: str
    material: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.material)


def validate_v3_execution_lineage(*, source_root: Path) -> V3Lineage:
    root = Path(source_root).resolve(strict=True)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ProductionBuildManifestV3Error("V3 production lineage requires a clean worktree")
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    import subprocess
    if subprocess.run(("git", "merge-base", "--is-ancestor", V3_LINEAGE_ANCHOR, head),
                      cwd=root, check=False).returncode:
        raise ProductionBuildManifestV3Error("V3 lineage anchor is not an ancestor")
    if _git(root, "rev-list", "--merges", f"{V3_LINEAGE_ANCHOR}..{head}"):
        raise ProductionBuildManifestV3Error("V3 execution lineage contains a merge")
    changed = tuple(filter(None, _git(
        root, "diff", "--name-only", V3_LINEAGE_ANCHOR, head,
    ).splitlines()))
    forbidden = sorted(set(changed).difference(V3_EXECUTION_PATHS))
    if forbidden:
        raise ProductionBuildManifestV3Error(f"V3 lineage changed forbidden paths: {forbidden}")
    material = {
        "schema": V3_LINEAGE_SCHEMA, "anchor_commit": V3_LINEAGE_ANCHOR,
        "current_head_commit": head, "current_head_tree": tree,
        "linear_non_merge": True, "changed_paths": sorted(changed),
    }
    identity = domain_hash("ASAP_BLOCK:V9.3:RTA4:V3_EXECUTION_LINEAGE:v2", material)
    return V3Lineage(head, tree, identity, {**material, "repository_lineage_identity": identity})


def generate_production_build_manifest_v3(
    *, source_root: Path | str, simulator_binary: Path | str,
    verifier_binary: Path | str, compiler: Path | str,
    build_commands: Mapping[str, Sequence[str]],
    relevant_source_paths: Sequence[Path | str] = V3_RELEVANT_SOURCES,
    environ: Mapping[str, str] | None = None, require_clean: bool = True,
) -> Dict[str, Any]:
    selected = tuple(str(Path(path).as_posix()) for path in relevant_source_paths)
    missing = sorted(set(V3_RELEVANT_SOURCES).difference(selected))
    if missing:
        raise ProductionBuildManifestV3Error(
            f"V3 production source closure is incomplete: {missing}"
        )
    base = generate_production_build_manifest(
        source_root=source_root, simulator_binary=simulator_binary,
        verifier_binary=verifier_binary, compiler=compiler,
        build_commands=build_commands, relevant_source_paths=selected,
        environ=environ, require_clean=require_clean,
        _lineage_validator=validate_v3_execution_lineage,
    )
    document = dict(base)
    document.pop("manifest_id")
    document["manifest_schema"] = PRODUCTION_BUILD_MANIFEST_SCHEMA_V3
    document["formal_profile"] = PRODUCTION_BUILD_PROFILE_V3
    repository = dict(document["repository"])
    repository["formal_profile_id"] = PRODUCTION_BUILD_PROFILE_V3
    repository["formal_schema_sha256"] = formal_schema_hash_v3()
    document["repository"] = repository
    return {**document, "manifest_id": domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, document)}


def validate_production_build_manifest_v3(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ProductionBuildManifestV3Error("V3 manifest must be a mapping")
    unsigned = dict(manifest)
    observed = unsigned.pop("manifest_id", None)
    if (manifest.get("manifest_schema") != PRODUCTION_BUILD_MANIFEST_SCHEMA_V3
            or manifest.get("formal_profile") != PRODUCTION_BUILD_PROFILE_V3
            or observed != domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, unsigned)):
        raise ProductionBuildManifestV3Error("V3 manifest identity/profile mismatch")
    repository = manifest.get("repository")
    if (
        not isinstance(repository, Mapping)
        or repository.get("formal_profile_id") != PRODUCTION_BUILD_PROFILE_V3
        or repository.get("formal_schema_sha256") != formal_schema_hash_v3()
        or sorted(row.get("path") for row in repository.get("relevant_sources", []))
        != sorted(V3_RELEVANT_SOURCES)
    ):
        raise ProductionBuildManifestV3Error("V3 manifest source closure/schema mismatch")
    root = Path(str(repository["source_root"]))
    expected = generate_production_build_manifest_v3(
        source_root=root,
        simulator_binary=manifest["simulator"]["binary"]["path"],
        verifier_binary=manifest["solar_verifier"]["binary"]["path"],
        compiler=manifest["cpp_toolchain"]["compiler"]["path"],
        build_commands={
            "simulator": manifest["simulator"]["build_command"],
            "verifier": manifest["solar_verifier"]["build_command"],
        },
        relevant_source_paths=tuple(row["path"] for row in manifest["repository"]["relevant_sources"]),
    )
    if dict(manifest) != expected:
        raise ProductionBuildManifestV3Error("V3 production build/environment drift")
    return expected


def load_production_build_manifest_v3(path: Path | str, *, live: bool = True) -> Dict[str, Any]:
    from .rta4_formal_environment import load_strict_json
    document = load_strict_json(path)
    if live:
        return validate_production_build_manifest_v3(document)
    unsigned = dict(document)
    observed = unsigned.pop("manifest_id", None)
    if (document.get("manifest_schema") != PRODUCTION_BUILD_MANIFEST_SCHEMA_V3
            or document.get("formal_profile") != PRODUCTION_BUILD_PROFILE_V3
            or observed != domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, unsigned)):
        raise ProductionBuildManifestV3Error("V3 manifest identity/profile mismatch")
    return dict(document)


__all__ = [
    "PRODUCTION_BUILD_MANIFEST_DOMAIN_V3", "PRODUCTION_BUILD_MANIFEST_SCHEMA_V3",
    "PRODUCTION_BUILD_PROFILE_V3", "V3_RELEVANT_SOURCES",
    "generate_production_build_manifest_v3", "load_production_build_manifest_v3",
    "validate_production_build_manifest_v3", "validate_v3_execution_lineage",
]
