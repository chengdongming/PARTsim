from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3.rta4_formal_config import RTA4_CORES, domain_hash
from experiments.v9_3 import rta4_core0a_pilot_v2 as core0a


EXPECTED_COVERAGE = {
    "by_core": {core: 64 for core in RTA4_CORES},
    "by_method": {
        "CORE3_SIMULATION_V2": 64,
        "CW_D": 11,
        "CW_THETA_CW": 55,
        "LOC_D": 14,
        "LOC_THETA_LOC": 69,
        "PH_D": 8,
        "PH_THETA_PH": 74,
        "SEQ_D": 9,
        "SEQ_THETA_SEQ": 80,
    },
    "by_e0": {
        "0": 104, "1": 44, "1/100": 4, "1/20": 219,
        "1/5": 4, "1/50": 3, "3/100": 6,
    },
    "by_utilization_stratum": {
        "1/10": 23, "1/2": 112, "1/5": 21, "2/5": 58,
        "3/10": 48, "3/5": 55, "4/5": 25, "7/10": 42,
    },
    "by_service_scale": {"1": 373, "1/2": 2, "3/2": 1, "3/4": 6, "5/4": 2},
    "by_time_scale": {"1": 368, "2": 2, "4": 9, "8": 5},
    "by_track": {
        "CORE4_OFAT": 64,
        "CORE5B_WORKER_CONSISTENCY": 64,
        "FINITE_BATTERY_EMPIRICAL:ASYNC_HASH_PHASE_V1": 31,
        "INTEGER_TIME_SCALE": 18,
        "MAIN": 128,
        "PROCESSORS": 22,
        "TASK_COUNT": 24,
        "THEOREM_ALIGNED:ASYNC_HASH_PHASE_V1": 20,
        "THEOREM_ALIGNED:SYNC_V1": 13,
    },
    "by_replica": {"1": 16, "2": 16, "4": 16, "8": 16, "PRIMARY": 320},
    "unique_taskset_slot_count": 321,
    "unique_service_spec_count": 6,
}

V2_CONFIG_SHAS = {
    "CORE-1": "ce9b6de4457b906addd485591da0079e1c7ea9c24c80ba0db424cf0a65bb80b7",
    "CORE-2": "3fcc84f4c7d3d82675254c0d1812ec5a6c1254928c9ef71719764746272f0157",
    "CORE-3": "db8ed7010be123f5e39ff9399a93bf2d5efadd99abf6a0afa0f8a0e1cfad7447",
    "CORE-4": "cbf28ea6b4f5051aa0acaef8a30d8899b0335a0d6879f0f1a4850de461f0fbc9",
    "CORE-5A": "25bca583116514b518cfe23a8ebe4303a3ed2102b9b4d0f075b1ab0003c6184e",
    "CORE-5B": "7b20c68484f894ac702caeb132648e5b0f48b36f216d35891ab5280ca9cfee8b",
}


@pytest.fixture(scope="module")
def selection():
    return core0a.build_core0a_selection_v2()


@pytest.fixture(scope="module")
def portable(selection):
    return core0a.build_portable_candidate_bundle_v2(
        selection=selection,
        source_commit="a" * 40,
        source_tree="b" * 40,
        require_clean=False,
    )


@pytest.fixture(scope="module")
def live_portable(selection):
    return core0a.build_portable_candidate_bundle_v2(
        selection=selection,
        require_clean=True,
    )


def _rehash_selection(document):
    unsigned = deepcopy(document)
    unsigned.pop("core0a_selection_identity", None)
    unsigned["core0a_selection_identity"] = domain_hash(
        core0a.CORE0A_SELECTION_DOMAIN, unsigned,
    )
    return unsigned


def _production_manifest(portable):
    material = {
        "manifest_schema": core0a.PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "formal_authorization": False,
        "repository": {
            "source_root": str(core0a.PROJECT_ROOT.resolve()),
            "git_commit": portable["source"]["git_commit"],
            "git_tree": portable["source"]["git_tree"],
        },
        "python": {"identity_fixture": "python"},
        "cpp_toolchain": {"identity_fixture": "toolchain"},
        "simulator": {"identity_fixture": "simulator"},
        "solar_verifier": {"identity_fixture": "verifier"},
        "environment": {"identity_fixture": "environment"},
    }
    return {
        **material,
        "manifest_id": domain_hash(
            core0a.PRODUCTION_BUILD_MANIFEST_DOMAIN, material,
        ),
    }


def _observation(*, cpu=8, memory=64 << 30, free=64 << 30):
    return core0a.AutoDLResourceObservation(
        logical_cpu_count=cpu,
        physical_memory_bytes=memory,
        free_disk_bytes=free,
    )


def _rehash_deployment(document):
    unsigned = deepcopy(document)
    unsigned.pop("deployment_manifest_identity", None)
    unsigned["deployment_manifest_identity"] = domain_hash(
        core0a.CORE0A_DEPLOYMENT_MANIFEST_DOMAIN, unsigned,
    )
    return unsigned


def _set_deployment_field(document, dotted, value):
    target = document
    fields = dotted.split(".")
    for field in fields[:-1]:
        target = target[field]
    target[fields[-1]] = value


def _build_deployment(
    monkeypatch, portable, workspace, *, observation=None,
):
    production = _production_manifest(portable)
    observed = observation or _observation()
    monkeypatch.setattr(
        core0a, "_observe_autodl_resources", lambda _root: observed,
    )
    deployment = core0a.build_autodl_deployment_manifest_v2(
        bundle=portable,
        production_manifest=production,
        source_root=core0a.PROJECT_ROOT,
        deployment_workspace_root=workspace,
    )
    return production, observed, deployment


def _validate_fixture(
    tmp_path, monkeypatch, live_portable, *, mutate=None,
):
    workspace = tmp_path / "autodl-workspace"
    workspace.mkdir()
    production, observed, deployment = _build_deployment(
        monkeypatch, live_portable, workspace,
    )
    if mutate is not None:
        mutate(deployment)
        deployment = _rehash_deployment(deployment)
    bundle_path = tmp_path / "portable.json"
    production_path = tmp_path / "production.json"
    deployment_path = tmp_path / "deployment.json"
    core0a.write_canonical_json(bundle_path, live_portable)
    core0a.write_canonical_json(production_path, production)
    core0a.write_canonical_json(deployment_path, deployment)
    monkeypatch.setattr(
        core0a,
        "load_and_validate_production_build_manifest",
        lambda *args, **kwargs: production,
    )
    validated = core0a.validate_autodl_deployment_manifest_v2(
        portable_bundle_path=bundle_path,
        selection_artifact_path=(
            core0a.PROJECT_ROOT / core0a.SELECTION_ARTIFACT_PATH
        ),
        candidate_config_path=(
            core0a.PROJECT_ROOT / core0a.CANDIDATE_CONFIG_PATH
        ),
        production_manifest_path=production_path,
        deployment_manifest_path=deployment_path,
        source_root=core0a.PROJECT_ROOT,
        deployment_workspace_root=workspace,
        require_clean=True,
    )
    assert validated.deployment_manifest == deployment
    assert validated.execution_identity == core0a.core0a_execution_identity(
        validated,
    )
    return validated, deployment_path, production, observed, workspace


def test_selection_is_exact_canonical_and_stable(selection):
    assert len(selection["ordered_records"]) == 384
    assert selection["coverage_matrix"] == EXPECTED_COVERAGE
    assert core0a.build_core0a_selection_v2() is selection
    payload = core0a.canonical_json_bytes(selection)
    path = core0a.PROJECT_ROOT / core0a.SELECTION_ARTIFACT_PATH
    assert path.read_bytes() == payload
    assert core0a.load_core0a_selection_v2(path) == selection
    assert selection["core0a_selection_identity"] == (
        "3e14cd615c5dbaaa6a392afdcbbb569dfddc7d0dc786c3a19e8d8823658908c1"
    )


def test_historical_math_selection_is_ported_by_stable_ordinals(selection):
    historical = core0a._historical_ordered_rows()
    assert hashlib.sha256(
        core0a.canonical_json(historical).encode("utf-8")
    ).hexdigest() == core0a.HISTORICAL_ORDERED_SELECTION_IDENTITY
    assert [row["ordinal"] for row in selection["ordered_records"]] == [
        row["ordinal"] for row in historical
    ]
    ordinals = core0a.selected_ordinals_by_core(selection)
    assert all(len(ordinals[core]) == 64 for core in RTA4_CORES)
    assert all(tuple(sorted(values)) == values for values in ordinals.values())


def test_v1_v2_migration_preserves_axes_and_reissues_all_native_seeds(selection):
    migration = core0a.build_seed_migration_contract_v2()
    axes = migration["version_neutral_axis_comparison"]
    seeds = migration["derived_generation_seed_migration"]
    pairing = migration["v2_taskset_pairing_validation"]
    assert migration["migration_mode"] == core0a.CORE0A_SEED_MIGRATION_MODE
    assert axes["record_count"] == axes["matching_record_count"] == 384
    assert axes["all_match"] is True
    assert seeds["field_is_profile_and_domain_scoped"] is True
    assert seeds["equality_required"] is False
    assert seeds["different_seed_count"] == 384
    assert seeds["v2_native_seed_record_count"] == 384
    assert pairing["unique_taskset_slot_count"] == 321
    assert pairing["reused_execution_count"] == 63
    assert pairing["same_slot_uses_same_source"] is True
    assert migration["core5b_group_validation"] == {
        "complete_group_count": 16,
        "replicas_per_group": ["1", "2", "4", "8"],
        "all_groups_complete": True,
    }
    assert migration["historical_v1_selection"][
        "selection_source_sha256"
    ] == core0a.HISTORICAL_SELECTION_SOURCE_SHA256
    assert [row["ordinal"] for row in selection["ordered_records"]] == [
        ordinal
        for core in RTA4_CORES
        for ordinal in migration["historical_v1_selection"][
            "selected_ordinals_by_core"
        ][core]
    ]


def test_all_selected_v2_seeds_recompute_and_v1_seed_substitution_is_rejected(
    selection,
):
    v2_configs = core0a._load_configs(version=2)
    v2_records = core0a._selected_v2_records(v2_configs)
    expected = [
        core0a._taskset_seed(record, v2_configs[record.core])
        for record in v2_records
    ]
    assert expected == [row["seed"] for row in selection["ordered_records"]]
    v1_configs = core0a._load_configs(version=1)
    v1_records = core0a._selected_v1_records(v1_configs)
    v1_seeds = [
        core0a._v1_taskset_seed(record, v1_configs[record.core])
        for record in v1_records
    ]
    assert all(left != right for left, right in zip(v1_seeds, expected))
    damaged = deepcopy(selection)
    damaged["ordered_records"][0]["seed"] = v1_seeds[0]
    damaged = _rehash_selection(damaged)
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.validate_core0a_selection_v2(damaged)


def test_selected_records_are_unique_v2_plan_members(selection):
    records = selection["ordered_records"]
    assert len({row["plan_record_identity"] for row in records}) == 384
    assert len({row["execution_identity"] for row in records}) == 384
    assert all(row["plan_record_identity"] for row in records)
    assert core0a.validate_core0a_selection_v2(selection) == selection


def test_core5b_preserves_complete_execution_replica_groups(selection):
    rows = [row for row in selection["ordered_records"] if row["core"] == "CORE-5B"]
    assert len(rows) == 64
    for offset in range(0, 64, 4):
        group = rows[offset:offset + 4]
        assert [row["replica"] for row in group] == ["1", "2", "4", "8"]
        assert len({row["mathematical_cell_identity"] for row in group}) == 1
        assert len({row["taskset_slot"] for row in group}) == 1


def test_record_mutation_and_reordering_change_identity_and_are_rejected(selection):
    changed = deepcopy(selection)
    changed["ordered_records"][0]["method"] = "UNKNOWN"
    changed["coverage_matrix"] = core0a.coverage_matrix(changed["ordered_records"])
    changed = _rehash_selection(changed)
    assert changed["core0a_selection_identity"] != selection[
        "core0a_selection_identity"
    ]
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.validate_core0a_selection_v2(changed)

    reordered = deepcopy(selection)
    reordered["ordered_records"][0:2] = reversed(reordered["ordered_records"][0:2])
    reordered = _rehash_selection(reordered)
    assert reordered["core0a_selection_identity"] != selection[
        "core0a_selection_identity"
    ]
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.validate_core0a_selection_v2(reordered)


@pytest.mark.parametrize("delta", (-1, 1))
def test_selection_rejects_383_385_and_duplicates(selection, delta):
    damaged = deepcopy(selection)
    if delta < 0:
        damaged["ordered_records"].pop()
    else:
        damaged["ordered_records"].append(deepcopy(damaged["ordered_records"][-1]))
    damaged = _rehash_selection(damaged)
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.validate_core0a_selection_v2(damaged)


def test_selection_rejects_unknown_plan_record(selection):
    damaged = deepcopy(selection)
    damaged["ordered_records"][0]["plan_record_identity"] = "f" * 64
    damaged = _rehash_selection(damaged)
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.validate_core0a_selection_v2(damaged)


def test_v1_v2_artifacts_and_namespaces_are_isolated(selection):
    encoded = json.dumps(selection["ordered_records"], sort_keys=True)
    assert "ASAP_BLOCK_V9_3_RTA4_FORMAL_V1" not in encoded
    assert "FORMAL_PLAN_V1" not in encoded
    assert "FORMAL_SCHEMA_V1" not in encoded
    assert all(
        row["parameter_status"] == "UNAUTHORIZED_PRE_PILOT"
        for row in selection["v2_configs"].values()
    )
    assert core0a.CORE0A_OUTPUT_NAMESPACE not in {
        f"results/v9_3_rta4_{core.lower().replace('-', '')}_formal_v2_shared_energy"
        for core in RTA4_CORES
    }
    assert hashlib.sha256(
        (core0a.PROJECT_ROOT / "experiments/v9_3/rta4_formal_pilot.py").read_bytes()
    ).hexdigest() == core0a.HISTORICAL_SELECTION_SOURCE_SHA256


def test_six_v2_configs_remain_byte_frozen(selection):
    for core, expected in V2_CONFIG_SHAS.items():
        path = core0a.PROJECT_ROOT / core0a.V2_CONFIG_PATHS[core]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        assert selection["v2_configs"][core]["file_sha256"] == expected


def test_six_v2_config_status_drift_cannot_be_rehashed_into_scope(selection):
    for core in RTA4_CORES:
        damaged = deepcopy(selection)
        damaged["v2_configs"][core]["parameter_status"] = "AUTHORIZED"
        damaged = _rehash_selection(damaged)
        with pytest.raises(core0a.RTA4Core0APilotV2Error):
            core0a.validate_core0a_selection_v2(damaged)


def test_portable_bundle_is_path_independent_and_stable(tmp_path, selection, portable):
    other = core0a.build_portable_candidate_bundle_v2(
        selection=selection, source_commit="a" * 40, source_tree="b" * 40,
        require_clean=False,
    )
    assert other == portable
    left = tmp_path / "left" / "bundle.json"
    right = tmp_path / "right" / "bundle.json"
    core0a.write_canonical_json(left, portable)
    core0a.write_canonical_json(right, other)
    assert left.read_bytes() == right.read_bytes()
    encoded = left.read_text()
    assert str(tmp_path) not in encoded
    assert "/tmp/" not in encoded


def test_source_config_and_selection_material_change_bundle_identity(selection, portable):
    source_changed = core0a.build_portable_candidate_bundle_v2(
        selection=selection, source_commit="c" * 40, source_tree="b" * 40,
        require_clean=False,
    )
    assert source_changed["portable_freeze_identity"] != portable[
        "portable_freeze_identity"
    ]
    material = deepcopy(portable)
    material.pop("portable_freeze_identity")
    material["candidate_config"]["sha256"] = "0" * 64
    config_changed = domain_hash(core0a.CORE0A_PORTABLE_BUNDLE_DOMAIN, material)
    assert config_changed != portable["portable_freeze_identity"]
    material = deepcopy(portable)
    material.pop("portable_freeze_identity")
    material["selection"]["core0a_selection_identity"] = "0" * 64
    selection_changed = domain_hash(core0a.CORE0A_PORTABLE_BUNDLE_DOMAIN, material)
    assert selection_changed != portable["portable_freeze_identity"]


def test_worker_count_is_deployment_not_selection_or_science(
    tmp_path, monkeypatch, portable,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    production, _, two = _build_deployment(
        monkeypatch, portable, workspace, observation=_observation(cpu=2),
    )
    monkeypatch.setattr(
        core0a, "_observe_autodl_resources",
        lambda _root: _observation(cpu=4),
    )
    four = core0a.build_autodl_deployment_manifest_v2(
        bundle=portable,
        production_manifest=production,
        source_root=core0a.PROJECT_ROOT,
        deployment_workspace_root=workspace,
    )
    assert two["selection_identity"] == four["selection_identity"]
    assert two["portable_freeze_identity"] == four["portable_freeze_identity"]
    assert two["deployment_manifest_identity"] != four["deployment_manifest_identity"]
    assert core0a._combined_execution_identity(portable, two) != (
        core0a._combined_execution_identity(portable, four)
    )


def test_runtime_rss_and_worker_change_only_terminal_evidence(selection):
    record = selection["ordered_records"][0]
    common = dict(
        record=record,
        taskset_identity="1" * 64,
        task_energy_material_identity="2" * 64,
        service_material_identity="3" * 64,
        beta_material_identity="4" * 64,
        response_status="TIMEOUT",
        mathematical_result={"solver_status": "TIMEOUT"},
        attempt_index_timeout_status=[{
            "attempt_index": 0, "timeout_seconds": 300, "status": "TIMEOUT",
            "runtime_wall_seconds": "1", "peak_rss_bytes": 10,
        }],
    )
    scientific_a = core0a.scientific_analysis_identity(**common)
    common["attempt_index_timeout_status"][0]["runtime_wall_seconds"] = "999"
    common["attempt_index_timeout_status"][0]["peak_rss_bytes"] = 999
    scientific_b = core0a.scientific_analysis_identity(**common)
    assert scientific_a == scientific_b
    terminal_a = core0a.terminal_evidence_identity(
        scientific_identity=scientific_a, runtime_wall_seconds="1",
        runtime_cpu_seconds="1", peak_rss_bytes=10, worker_count=1,
        terminal_content={"status": "TIMEOUT"},
    )
    terminal_b = core0a.terminal_evidence_identity(
        scientific_identity=scientific_b, runtime_wall_seconds="2",
        runtime_cpu_seconds="2", peak_rss_bytes=20, worker_count=4,
        terminal_content={"status": "TIMEOUT"},
    )
    assert terminal_a != terminal_b


def test_candidate_and_pending_bundle_cannot_execute(
    tmp_path, monkeypatch, live_portable,
):
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.require_authorized_core0a_engineering_pilot(None, None)
    validated, _, _, _, _ = _validate_fixture(
        tmp_path, monkeypatch, live_portable,
    )
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.require_authorized_core0a_engineering_pilot(
            validated,
            {"status": core0a.UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE},
        )
    assert live_portable[
        "authorization_gate"
    ]["current_engineering_authorization"] is False
    assert live_portable["formal_authorization"] is False
    assert live_portable["production_authorization"] is False


def test_only_exact_independently_reviewed_authorization_can_pass(
    tmp_path, monkeypatch, live_portable,
):
    validated, _, _, _, _ = _validate_fixture(
        tmp_path, monkeypatch, live_portable,
    )
    deployment = validated.deployment_manifest
    material = {
        "authorization_schema": core0a.CORE0A_AUTHORIZATION_SCHEMA,
        "status": core0a.AUTHORIZED_CORE0A_ENGINEERING_PILOT,
        "independent_read_only_review": True,
        "review_identity": "9" * 64,
        "portable_freeze_identity": live_portable["portable_freeze_identity"],
        "selection_identity": live_portable[
            "selection"
        ]["core0a_selection_identity"],
        "source_commit": live_portable["source"]["git_commit"],
        "source_tree": live_portable["source"]["git_tree"],
        "deployment_manifest_identity": deployment["deployment_manifest_identity"],
        "execution_identity": core0a.core0a_execution_identity(validated),
        "output_namespace": core0a.CORE0A_OUTPUT_NAMESPACE,
        "actual_output_root": deployment["actual_output_root"],
        "taskset_store_root": deployment["taskset_store_root"],
        "deployment_workspace_identity": deployment[
            "deployment_workspace_identity"
        ],
        "authorized_execution_count": 384,
        "max_runs": 1,
        "scope": "EXACT_384_RECORD_CORE0A_ONLY",
        "run_nonce": "INDEPENDENT_REVIEW_MUST_SUPPLY",
        "expires_at_utc": "2099-01-01T00:00:00Z",
        "formal_authorization": False,
        "production_authorization": False,
    }
    authorization = {
        **material,
        "authorization_id": domain_hash(core0a.CORE0A_AUTHORIZATION_DOMAIN, material),
    }
    assert core0a.require_authorized_core0a_engineering_pilot(
        validated, authorization,
    ) is None
    for field, foreign in (
        ("independent_read_only_review", False),
        ("source_commit", "f" * 40),
        ("selection_identity", "f" * 64),
        ("authorized_execution_count", 383),
        ("max_runs", 2),
        ("formal_authorization", True),
    ):
        damaged = {**material, field: foreign}
        damaged["authorization_id"] = domain_hash(
            core0a.CORE0A_AUTHORIZATION_DOMAIN, damaged,
        )
        with pytest.raises(core0a.RTA4Core0APilotV2Error):
            core0a.require_authorized_core0a_engineering_pilot(
                validated, damaged,
            )


def test_file_path_rebuild_validator_accepts_exact_manifest(
    tmp_path, monkeypatch, live_portable,
):
    validated, _, _, _, workspace = _validate_fixture(
        tmp_path, monkeypatch, live_portable,
    )
    deployment = validated.deployment_manifest
    assert deployment["selection_count"] == 384
    assert deployment["max_runs"] == 1
    assert deployment["formal_authorization"] is False
    assert deployment["production_authorization"] is False
    assert deployment["actual_output_root"] == str(
        workspace / core0a.CORE0A_OUTPUT_NAMESPACE
    )
    assert deployment["taskset_store_root"] == str(
        workspace / core0a.CORE0A_TASKSET_STORE_NAMESPACE
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("selection_count", 385),
        ("selection_count", 383),
        ("selection_identity", "1" * 64),
        ("portable_freeze_identity", "2" * 64),
        ("expected_output_namespace", "results/not-core0a"),
        ("actual_output_root", "/autodl/results/not-core0a"),
        ("source_commit", "3" * 40),
        ("source_tree", "4" * 40),
        ("scientific_inputs.all_plan_digest", "5" * 64),
        ("scientific_inputs.config_identities.CORE-1", "6" * 64),
        ("scientific_inputs.candidate_config_identity", "7" * 64),
        ("max_runs", 2),
        ("formal_authorization", True),
        ("production_authorization", True),
        ("engineering_pilot_authorization", True),
        ("worker_count", 3),
        ("max_in_flight", 7),
        ("memory_soft_limit_fraction", "3/4"),
        ("memory_soft_limit_bytes", 1),
        ("checkpoint_frequency_records", 9),
        ("retry_contract.rta_methods.initial_timeout_seconds", 301),
        ("retry_contract.rta_methods.retry_timeout_seconds", 301),
        ("retry_contract.rta_methods.maximum_attempts", 3),
        ("retry_contract.rta_methods.retry_condition", "ALWAYS"),
        ("retry_contract.core3_simulation.initial_timeout_seconds", 301),
        ("retry_contract.core3_simulation.maximum_attempts", 2),
    ),
)
def test_rehashed_scope_and_resource_drift_is_rejected_by_rebuild(
    tmp_path, monkeypatch, live_portable, field, value,
):
    def mutate(document):
        _set_deployment_field(document, field, value)

    with pytest.raises(
        core0a.RTA4Core0APilotV2Error,
        match="reconstructed frozen scope",
    ):
        _validate_fixture(
            tmp_path, monkeypatch, live_portable, mutate=mutate,
        )
    assert core0a.CORE0A_RETRY_CONTRACT[
        "rta_methods"
    ]["initial_timeout_seconds"] == 300
    assert core0a.CORE0A_RETRY_CONTRACT[
        "core3_simulation"
    ]["maximum_attempts"] == 1


@pytest.mark.parametrize(
    "namespace",
    tuple(core0a.FORBIDDEN_FORMAL_OUTPUT_NAMESPACES),
)
def test_all_v1_v2_core1_to_core5_formal_output_roots_are_rejected(
    tmp_path, monkeypatch, live_portable, namespace,
):
    def mutate(document):
        document["actual_output_root"] = f"/autodl/{namespace}"

    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        _validate_fixture(
            tmp_path, monkeypatch, live_portable, mutate=mutate,
        )


@pytest.mark.parametrize(
    "namespace",
    tuple(core0a.FORBIDDEN_FORMAL_STORE_NAMESPACES),
)
def test_v1_v2_formal_taskset_store_roots_are_rejected(
    tmp_path, monkeypatch, live_portable, namespace,
):
    def mutate(document):
        document["taskset_store_root"] = f"/autodl/{namespace}"

    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        _validate_fixture(
            tmp_path, monkeypatch, live_portable, mutate=mutate,
        )


@pytest.mark.parametrize(
    "replacement",
    (
        "/autodl/results/another_core0a_namespace",
        "/autodl/workspace/../escape",
        "/autodl/results/v9_3_rta4_core0a_engineering_pilot_v2/../escape",
    ),
)
def test_non_core0a_and_dotdot_output_roots_are_rejected(
    tmp_path, monkeypatch, live_portable, replacement,
):
    def mutate(document):
        document["actual_output_root"] = replacement

    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        _validate_fixture(
            tmp_path, monkeypatch, live_portable, mutate=mutate,
        )


def test_derived_paths_accept_exact_isolated_namespace_without_creating_it(
    tmp_path,
):
    paths = core0a._derived_deployment_paths(tmp_path)
    output = tmp_path / core0a.CORE0A_OUTPUT_NAMESPACE
    store = tmp_path / core0a.CORE0A_TASKSET_STORE_NAMESPACE
    assert paths["actual_output_root"] == str(output)
    assert paths["taskset_store_root"] == str(store)
    assert paths["terminal_directory"] == str(
        output / core0a.CORE0A_TERMINAL_DIRECTORY
    )
    assert not output.exists()
    assert not store.exists()


def test_output_symlink_escape_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    results = workspace / "results"
    outside = tmp_path / "outside"
    results.mkdir(parents=True)
    outside.mkdir()
    (results / Path(core0a.CORE0A_OUTPUT_NAMESPACE).name).symlink_to(
        outside, target_is_directory=True,
    )
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="escapes"):
        core0a._derived_deployment_paths(workspace)


def test_output_and_store_symlink_conflict_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    results = workspace / "results"
    shared = workspace / "shared"
    results.mkdir(parents=True)
    shared.mkdir()
    (results / Path(core0a.CORE0A_OUTPUT_NAMESPACE).name).symlink_to(
        shared, target_is_directory=True,
    )
    (results / Path(core0a.CORE0A_TASKSET_STORE_NAMESPACE).name).symlink_to(
        shared, target_is_directory=True,
    )
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="overlap"):
        core0a._derived_deployment_paths(workspace)


def test_terminal_symlink_escape_is_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    output = workspace / core0a.CORE0A_OUTPUT_NAMESPACE
    outside = tmp_path / "outside"
    output.mkdir(parents=True)
    outside.mkdir()
    (output / core0a.CORE0A_TERMINAL_DIRECTORY).symlink_to(
        outside, target_is_directory=True,
    )
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="escapes"):
        core0a._derived_deployment_paths(workspace)


@pytest.mark.parametrize(
    ("cpu", "workers", "in_flight"),
    ((1, 1, 1), (3, 3, 3), (8, 4, 8)),
)
def test_resource_policy_derives_workers_and_max_in_flight(
    selection, cpu, workers, in_flight,
):
    disk = core0a._disk_estimate(selection)
    policy = core0a._resource_policy(
        _observation(cpu=cpu), disk,
    )
    assert policy["worker_count"] == workers
    assert policy["max_in_flight"] == in_flight
    assert policy["memory_soft_limit_fraction"] == "7/10"
    assert policy["memory_soft_limit_bytes"] == (64 << 30) * 7 // 10
    assert policy["checkpoint_frequency_records"] == 8


def test_disk_margin_is_fail_closed_and_exact_boundary_passes(selection):
    disk = core0a._disk_estimate(selection)
    required = disk["required_free_disk_bytes"]
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="free disk"):
        core0a._resource_policy(
            _observation(free=required - 1), disk,
        )
    policy = core0a._resource_policy(
        _observation(free=required), disk,
    )
    assert policy["disk_preflight_passed"] is True
    assert disk["explicit_safety_margin_bytes"] >= 1 << 30


def test_unvalidated_or_noncanonical_frozen_inputs_are_rejected(
    tmp_path, monkeypatch, live_portable,
):
    validated, _, production, observed, workspace = _validate_fixture(
        tmp_path, monkeypatch, live_portable,
    )
    bundle_path = tmp_path / "portable.json"
    bundle_path.write_text(
        json.dumps(live_portable, indent=2), encoding="utf-8",
    )
    production_path = tmp_path / "production.json"
    deployment_path = tmp_path / "deployment.json"
    core0a.write_canonical_json(production_path, production)
    core0a.write_canonical_json(
        deployment_path, validated.deployment_manifest,
    )
    monkeypatch.setattr(
        core0a, "_observe_autodl_resources", lambda _root: observed,
    )
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="not canonical"):
        core0a.validate_autodl_deployment_manifest_v2(
            portable_bundle_path=bundle_path,
            selection_artifact_path=(
                core0a.PROJECT_ROOT / core0a.SELECTION_ARTIFACT_PATH
            ),
            candidate_config_path=(
                core0a.PROJECT_ROOT / core0a.CANDIDATE_CONFIG_PATH
            ),
            production_manifest_path=production_path,
            deployment_manifest_path=deployment_path,
            source_root=core0a.PROJECT_ROOT,
            deployment_workspace_root=workspace,
            require_clean=True,
        )


def test_candidate_config_retry_resume_and_output_contracts():
    config = core0a.load_candidate_config_v2()
    assert config["status"] == core0a.UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE
    assert config["retry_contract"]["rta_methods"] == {
        "initial_timeout_seconds": 300,
        "retry_timeout_seconds": 300,
        "maximum_attempts": 2,
        "retry_condition": "TIMEOUT_ONLY",
    }
    assert config["retry_contract"]["core3_simulation"]["maximum_attempts"] == 1
    assert config["deployment_policy"]["checkpoint_interval_records"] == 8
    assert config["output_namespace"] == core0a.CORE0A_OUTPUT_NAMESPACE
    assert "formal_v2" not in config["output_namespace"]
    migration = config["seed_migration_contract"]
    assert migration["migration_mode"] == core0a.CORE0A_SEED_MIGRATION_MODE
    assert migration["derived_generation_seed_is_profile_and_domain_scoped"] is True
    assert migration["v1_v2_seed_equality_required"] is False
    assert migration["historical_positions_not_v1_taskset_instances"] is True


def test_bundle_and_handoff_are_complete_and_non_sensitive(portable):
    handoff = core0a.build_autodl_handoff_v2(portable)
    assert core0a.validate_autodl_handoff_v2(handoff, portable) == handoff
    assert portable["required_source_files"]["production_default_closure_count"] == 53
    assert len(portable["required_source_files"]["production_default_closure"]) == 53
    assert len(handoff["steps"]) == 15
    assert handoff["authorization_required_before_any_record"] is True
    assert portable["contract_version"] == core0a.CORE0A_PORTABLE_CONTRACT_VERSION
    assert portable["seed_migration_contract"] == (
        core0a.build_seed_migration_contract_v2()
    )
    assert portable["autodl_deployment_contract"][
        "formal_validator_accepts_file_paths_only"
    ] is True
    encoded = json.dumps({"bundle": portable, "handoff": handoff}, sort_keys=True)
    assert "/tmp/" not in encoded
    assert "simulator_binary" not in encoded
    assert "acceptance_ratio" not in encoded
    assert "schedulability_conclusion" not in encoded

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key.lower()
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    assert not {"credential", "credentials", "password", "secret", "token"}.intersection(
        keys({"bundle": portable, "handoff": handoff})
    )


def test_duplicate_json_key_and_noncanonical_json_are_rejected(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="duplicate"):
        core0a.load_strict_canonical_json(duplicate)
    noncanonical = tmp_path / "pretty.json"
    noncanonical.write_text('{\n  "a": 1\n}\n', encoding="utf-8")
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="not canonical"):
        core0a.load_strict_canonical_json(noncanonical)


def test_selection_and_candidate_file_drift_are_rejected(tmp_path, selection):
    damaged = deepcopy(selection)
    damaged["ordered_records"][0]["plan_record_identity"] = "0" * 64
    damaged = _rehash_selection(damaged)
    path = tmp_path / "selection.json"
    core0a.write_canonical_json(path, damaged)
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.load_core0a_selection_v2(path)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        (core0a.PROJECT_ROOT / core0a.CANDIDATE_CONFIG_PATH).read_text().replace(
            "expected_execution_count: 384", "expected_execution_count: 383",
        ),
        encoding="utf-8",
    )
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.load_candidate_config_v2(candidate)


def test_formal_input_diff_outside_pilot_scope_is_rejected(monkeypatch):
    real_git = core0a._git

    def fake_git(*arguments):
        if arguments[:2] == ("diff", "--name-only"):
            return "asap_block_rta_v9_3.py"
        return real_git(*arguments)

    monkeypatch.setattr(core0a, "_git", fake_git)
    with pytest.raises(core0a.RTA4Core0APilotV2Error, match="outside CORE-0A"):
        core0a.repository_identity(require_clean=False)


def test_builder_generate_and_check_are_byte_identical(tmp_path):
    output = tmp_path / "selection.json"
    command = [
        os.sys.executable,
        str(core0a.PROJECT_ROOT / "scripts/build_v9_3_rta4_core0a_pilot_bundle.py"),
        "--artifact", "selection", "--selection-output", str(output),
    ]
    env = {**os.environ, "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache")}
    generated = subprocess.run(
        command, cwd=core0a.PROJECT_ROOT, env=env, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert generated.returncode == 0, generated.stderr
    before = output.read_bytes()
    checked = subprocess.run(
        [*command, "--check"], cwd=core0a.PROJECT_ROOT, env=env, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert output.read_bytes() == before
    assert json.loads(generated.stdout)["core0a_selection_identity"] == (
        json.loads(checked.stdout)["core0a_selection_identity"]
    )
