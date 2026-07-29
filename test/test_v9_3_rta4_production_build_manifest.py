from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from experiments.v9_3 import rta4_production_build_manifest as production_build
from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES,
    ENVIRONMENT_ALLOWLIST,
    PRODUCTION_BUILD_CLASSIFICATION,
    ProductionBuildManifestError,
    generate_production_build_manifest,
    load_and_validate_production_build_manifest,
    validate_production_build_manifest,
    write_production_build_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "asap_block_rta_v9_3.py",
    "experiments/v9_3/exact_energy.py",
    "experiments/v9_3/rta4_formal_schema.py",
    "librtsim/include/rtsim/scheduler/gpfp_asap_block_scheduler.hpp",
    "librtsim/scheduler/gpfp_asap_block_scheduler.cpp",
    "system_config_unified_template.yml",
    "tools/rta4_solar_stod_verifier.cpp",
)


@pytest.fixture(scope="module")
def verifier(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("rta4-g2-build") / "solar-verifier"
    subprocess.run(
        [
            "c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-pedantic",
            str(ROOT / "tools/rta4_solar_stod_verifier.cpp"),
            "-o", str(output),
        ],
        check=True,
    )
    return output


def _build(
    verifier: Path, *, simulator: Path | None = None,
    sources=SOURCES,
):
    environment = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
    }
    return generate_production_build_manifest(
        source_root=ROOT,
        simulator_binary=simulator or ROOT / "rtsim/rtsim",
        verifier_binary=verifier,
        compiler="c++",
        build_commands={
            "simulator": ["cmake", "--build", "/tmp/rta4-build", "--target", "rtsim-exe"],
            "verifier": ["c++", "-std=c++17", "tools/rta4_solar_stod_verifier.cpp"],
        },
        relevant_source_paths=sources,
        environ=environment,
        require_clean=False,
    )


def test_manifest_unifies_simulator_verifier_toolchain_and_threat_model(verifier):
    manifest = _build(verifier)
    assert manifest["classification"] == PRODUCTION_BUILD_CLASSIFICATION
    assert manifest["formal_authorization"] is False
    assert manifest["threat_model"]["malicious_local_administrator_out_of_scope"]
    assert manifest["cpp_toolchain"]["compiler"] == manifest["solar_verifier"]["compiler"]
    assert (
        manifest["simulator"]["linked_libraries"]
        and manifest["solar_verifier"]["linked_libraries"]
    )
    assert (
        manifest["cpp_toolchain"]["libstdcxx"]["sha256"]
        == manifest["solar_verifier"]["libstdcxx"]["sha256"]
    )
    assert (
        manifest["cpp_toolchain"]["glibc"]["sha256"]
        == manifest["solar_verifier"]["glibc"]["sha256"]
    )
    assert set(manifest["environment"]["values"]) <= set(ENVIRONMENT_ALLOWLIST)
    assert not any(
        marker in key.upper()
        for key in manifest["environment"]["values"]
        for marker in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")
    )
    assert manifest["repository"]["repository_lineage"][
        "repository_lineage_identity"
    ] == manifest["repository"]["repository_lineage_identity"]
    assert manifest["repository"]["repository_lineage"][
        "lineage_mode"
    ] == "CORE0A_STANDALONE_REVIEWED_LINEAGE"


def test_manifest_round_trip_check_and_binary_drift_rejection(verifier, tmp_path):
    manifest = _build(verifier)
    path = tmp_path / "production-manifest.json"
    write_production_build_manifest(path, manifest)
    environment = manifest["environment"]["values"]
    checked = load_and_validate_production_build_manifest(
        path, require_clean=False, environ=environment,
    )
    assert checked == manifest

    simulator = tmp_path / "simulator"
    shutil.copy2(ROOT / "rtsim/rtsim", simulator)
    os.chmod(simulator, 0o755)
    drift_manifest = _build(verifier, simulator=simulator)
    simulator.write_bytes(simulator.read_bytes() + b"drift")
    with pytest.raises(ProductionBuildManifestError, match="drift"):
        validate_production_build_manifest(
            drift_manifest, require_clean=False, environ=environment,
        )


def test_manifest_identity_and_environment_drift_are_fail_closed(verifier):
    manifest = _build(verifier)
    changed = copy.deepcopy(manifest)
    changed["environment"]["values"]["TZ"] = "Asia/Shanghai"
    with pytest.raises(ProductionBuildManifestError, match="identity"):
        validate_production_build_manifest(changed, require_clean=False)
    with pytest.raises(ProductionBuildManifestError, match="drift"):
        validate_production_build_manifest(
            manifest,
            require_clean=False,
            environ={**manifest["environment"]["values"], "TZ": "Asia/Shanghai"},
        )


def test_production_identity_binds_live_repository_lineage(verifier):
    manifest = _build(verifier)
    changed = copy.deepcopy(manifest)
    changed["repository"]["repository_lineage_identity"] = "f" * 64
    changed["manifest_id"] = domain_hash(
        production_build.PRODUCTION_BUILD_MANIFEST_DOMAIN,
        {
            key: value
            for key, value in changed.items()
            if key != "manifest_id"
        },
    )
    assert changed["manifest_id"] != manifest["manifest_id"]
    with pytest.raises(ProductionBuildManifestError, match="drift"):
        validate_production_build_manifest(
            changed,
            require_clean=False,
            environ=manifest["environment"]["values"],
        )


def test_require_clean_false_cannot_bypass_lineage_gate(
    verifier, monkeypatch,
):
    def reject_lineage(*, source_root):
        raise production_build.Core0ARepositoryLineageV1Error(
            f"rejected {source_root}"
        )

    monkeypatch.setattr(
        production_build,
        "validate_core0a_repository_lineage_v1",
        reject_lineage,
    )
    with pytest.raises(
        ProductionBuildManifestError,
        match="repository lineage validation failed",
    ):
        _build(verifier)


def test_manifest_loader_rejects_duplicate_json_keys(verifier, tmp_path):
    manifest = _build(verifier)
    path = tmp_path / "duplicate.json"
    path.write_text(
        json.dumps(manifest)[:-1] + ',"manifest_id":"duplicate"}',
        encoding="utf-8",
    )
    with pytest.raises(ProductionBuildManifestError, match="strict"):
        load_and_validate_production_build_manifest(path, require_clean=False)


V1_ONLY_EXECUTION_SCOPES = {
    "_load_v1_runtime", "_certificate_from_closure", "_adapter_result",
    "_resume_required_inventory", "_preflight_taskset_store",
    "ProductionTasksetProvider", "ProductionRTAExecutor",
    "ProductionSimulationExecutor", "AuthorizedRTA4Runner",
}


def _module_path(module: str) -> Path | None:
    source = ROOT / (module.replace(".", "/") + ".py")
    if source.is_file():
        return source
    package = ROOT / module.replace(".", "/") / "__init__.py"
    return package if package.is_file() else None


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _static_imports(path: Path) -> set[str]:
    module = _module_name(path)
    found = set()

    class Imports(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            if (
                path.name == "rta4_formal_execution.py"
                and node.name in V1_ONLY_EXECUTION_SCOPES
            ):
                return
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if (
                path.name == "rta4_formal_execution.py"
                and node.name in V1_ONLY_EXECUTION_SCOPES
            ):
                return
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node):
            found.update(alias.name for alias in node.names)

        def visit_ImportFrom(self, node):
            if node.level:
                package = module.split(".")[:-1]
                keep = len(package) - (node.level - 1)
                prefix = package[:keep]
                base = ".".join(prefix + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                found.add(base)
            for alias in node.names:
                candidate = ".".join(part for part in (base, alias.name) if part)
                if candidate and _module_path(candidate) is not None:
                    found.add(candidate)

    Imports().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def _official_v2_static_closure() -> set[str]:
    pending = ["experiments.v9_3.rta4_formal_runner_v2"]
    visited = set()
    sources = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        path = _module_path(module)
        if path is None:
            continue
        sources.add(path.relative_to(ROOT).as_posix())
        pending.extend(_static_imports(path).difference(visited))
    return sources


def test_default_manifest_covers_recursive_static_v2_import_closure():
    closure = _official_v2_static_closure()
    missing = closure.difference(DEFAULT_RELEVANT_SOURCES)
    assert not missing
    assert "experiments/v9_3/rta4_formal_plan_grid.py" in closure
    assert "experiments/v9_3/rta4_formal_plan_v2.py" in closure
    assert (
        "experiments/v9_3/rta4_core0a_repository_lineage_v1.py"
        in DEFAULT_RELEVANT_SOURCES
    )
    assert "experiments/v9_3/rta4_formal_plan.py" not in closure


def test_required_default_file_removal_and_live_source_drift_fail_closed(
    verifier, monkeypatch,
):
    critical = "experiments/v9_3/result_writer.py"
    incomplete_sources = tuple(
        source for source in DEFAULT_RELEVANT_SOURCES if source != critical
    )
    incomplete = _build(verifier, sources=incomplete_sources)
    with pytest.raises(ProductionBuildManifestError, match="incomplete"):
        validate_production_build_manifest(
            incomplete, require_clean=False, require_default_closure=True,
            environ=incomplete["environment"]["values"],
        )

    complete = _build(verifier, sources=DEFAULT_RELEVANT_SOURCES)
    original_read = Path.read_bytes
    target = (ROOT / critical).resolve()

    def drifted_read(path):
        payload = original_read(path)
        return payload + b"\n# bounded test drift" if path.resolve() == target else payload

    monkeypatch.setattr(Path, "read_bytes", drifted_read)
    with pytest.raises(ProductionBuildManifestError, match="drift"):
        validate_production_build_manifest(
            complete, require_clean=False, require_default_closure=True,
            environ=complete["environment"]["values"],
        )
