from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from experiments.v9_3.rta4_production_build_manifest import (
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


def _build(verifier: Path, *, simulator: Path | None = None):
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
        relevant_source_paths=SOURCES,
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


def test_manifest_loader_rejects_duplicate_json_keys(verifier, tmp_path):
    manifest = _build(verifier)
    path = tmp_path / "duplicate.json"
    path.write_text(
        json.dumps(manifest)[:-1] + ',"manifest_id":"duplicate"}',
        encoding="utf-8",
    )
    with pytest.raises(ProductionBuildManifestError, match="strict"):
        load_and_validate_production_build_manifest(path, require_clean=False)
