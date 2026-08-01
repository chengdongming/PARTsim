from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES, PRODUCTION_BUILD_MANIFEST_SCHEMA,
    PRODUCTION_BUILD_PROFILE,
)
from experiments.v9_3.rta4_production_build_manifest_v3 import (
    PRODUCTION_BUILD_MANIFEST_DOMAIN_V3,
    PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
    PRODUCTION_BUILD_PROFILE_V3,
    V3_RELEVANT_SOURCES,
    V3_LINEAGE_ANCHOR,
    ProductionBuildManifestV3Error,
    load_production_build_manifest_v3,
)


def _signed(material):
    return {
        **material,
        "manifest_id": domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, material),
    }


def test_v3_closure_contains_every_parameterized_execution_source():
    required = {
        "experiments/v9_3/rta4_formal_config_v3.py",
        "experiments/v9_3/rta4_formal_plan_v3.py",
        "experiments/v9_3/rta4_formal_lifecycle_v3.py",
        "experiments/v9_3/rta4_formal_schema_v3.py",
        "experiments/v9_3/rta4_formal_runner_v3.py",
        "experiments/v9_3/rta4_formal_workers_v3.py",
        "experiments/v9_3/rta4_physical_core_slots_v3.py",
        "experiments/v9_3/rta4_production_build_manifest_v3.py",
        "scripts/run_v9_3_rta4_formal.py",
        "scripts/create_v9_3_rta4_campaign.py",
        "scripts/build_v9_3_rta4_production_manifest.py",
        "scripts/benchmark_v9_3_rta4_physical_workers.py",
    }
    assert required <= set(V3_RELEVANT_SOURCES)
    assert set(DEFAULT_RELEVANT_SOURCES) <= set(V3_RELEVANT_SOURCES)
    assert V3_LINEAGE_ANCHOR == (
        "5acde530eb6b68f6e3a5bc2e6c496307690a054d"
    )


def test_thread_era_v3_manifest_is_structurally_rejected(tmp_path):
    material = {
        "manifest_schema": (
            "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_"
            "V3_PARAMETERIZED"
        ),
        "formal_profile": (
            "ASAP_BLOCK_V9_3_RTA4_FORMAL_V3_PARAMETERIZED_SHARED_ENERGY"
        ),
    }
    old_domain = "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST:v3"
    path = tmp_path / "thread-era-v3.json"
    path.write_text(json.dumps({
        **material, "manifest_id": domain_hash(old_domain, material),
    }), encoding="utf-8")
    with pytest.raises(ProductionBuildManifestV3Error, match="identity/profile"):
        load_production_build_manifest_v3(path, live=False)


def test_process_pool_v3_manifest_is_structurally_rejected(tmp_path):
    material = {
        "manifest_schema": (
            "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_V3_"
            "PARAMETERIZED_PROCESS_POOL_R1"
        ),
        "formal_profile": (
            "ASAP_BLOCK_V9_3_RTA4_FORMAL_V3_PARAMETERIZED_SHARED_ENERGY_"
            "PROCESS_POOL_R1"
        ),
    }
    old_domain = (
        "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST:"
        "v3-process-pool-r1"
    )
    path = tmp_path / "process-pool-v3.json"
    path.write_text(json.dumps({
        **material, "manifest_id": domain_hash(old_domain, material),
    }), encoding="utf-8")
    with pytest.raises(ProductionBuildManifestV3Error, match="identity/profile"):
        load_production_build_manifest_v3(path, live=False)


def test_external_campaign_is_excluded_from_v3_source_closure():
    assert "configs/v9_3_rta4_e1_critical_e0_v1.yaml" not in V3_RELEVANT_SOURCES
    assert all(not path.endswith("_campaign.yaml") for path in V3_RELEVANT_SOURCES)


def test_v2_manifest_cannot_masquerade_as_v3(tmp_path):
    path = tmp_path / "v2.json"
    path.write_text(json.dumps({
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "formal_profile": PRODUCTION_BUILD_PROFILE,
        "manifest_id": "a" * 64,
    }), encoding="utf-8")
    with pytest.raises(ProductionBuildManifestV3Error, match="identity/profile"):
        load_production_build_manifest_v3(path, live=False)


def test_v3_structural_loader_rejects_identity_and_profile_drift(tmp_path):
    material = {
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
        "formal_profile": PRODUCTION_BUILD_PROFILE_V3,
    }
    path = tmp_path / "v3.json"
    path.write_text(json.dumps(_signed(material)), encoding="utf-8")
    assert load_production_build_manifest_v3(path, live=False)[
        "formal_profile"
    ] == PRODUCTION_BUILD_PROFILE_V3
    changed = _signed({**material, "formal_profile": "V2"})
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ProductionBuildManifestV3Error, match="identity/profile"):
        load_production_build_manifest_v3(path, live=False)


def test_campaign_bytes_change_campaign_identity_without_entering_code_closure(tmp_path):
    first = tmp_path / "campaign-one.yaml"
    second = tmp_path / "campaign-two.yaml"
    first.write_text('campaign_id: "one"\n', encoding="utf-8")
    second.write_text('campaign_id: "two"\n', encoding="utf-8")
    import hashlib

    assert hashlib.sha256(first.read_bytes()).hexdigest() != hashlib.sha256(
        second.read_bytes()
    ).hexdigest()
    assert first.name not in V3_RELEVANT_SOURCES
    assert second.name not in V3_RELEVANT_SOURCES


def test_v3_execution_source_digest_changes_manifest_identity():
    material = {
        "manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA_V3,
        "formal_profile": PRODUCTION_BUILD_PROFILE_V3,
        "repository": {"relevant_sources": [{
            "path": "experiments/v9_3/rta4_formal_runner_v3.py",
            "sha256": "a" * 64,
        }]},
    }
    changed = json.loads(json.dumps(material))
    changed["repository"]["relevant_sources"][0]["sha256"] = "b" * 64
    assert domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, material) != domain_hash(
        PRODUCTION_BUILD_MANIFEST_DOMAIN_V3, changed,
    )


def test_v2_schema_profile_and_default_source_closure_are_unchanged():
    assert PRODUCTION_BUILD_MANIFEST_SCHEMA == (
        "ASAP_BLOCK_V9_3_RTA4_PRODUCTION_BUILD_ENVIRONMENT_MANIFEST_V2"
    )
    assert PRODUCTION_BUILD_PROFILE == (
        "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY"
    )
    assert "experiments/v9_3/rta4_formal_runner_v3.py" not in DEFAULT_RELEVANT_SOURCES
