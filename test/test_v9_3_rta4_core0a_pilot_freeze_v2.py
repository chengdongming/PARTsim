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


def _rehash_selection(document):
    unsigned = deepcopy(document)
    unsigned.pop("core0a_selection_identity", None)
    unsigned["core0a_selection_identity"] = domain_hash(
        core0a.CORE0A_SELECTION_DOMAIN, unsigned,
    )
    return unsigned


def _deployment(portable, *, workers=2, suffix="1"):
    values = {
        "production_build_manifest_identity": "1" * 64,
        "python_identity": "2" * 64,
        "toolchain_identity": "3" * 64,
        "simulator_identity": "4" * 64,
        "verifier_identity": "5" * 64,
        "environment_identity": "6" * 64,
        "worker_count": workers,
        "max_in_flight": max(4, workers),
        "memory_soft_limit_bytes": 1024,
        "timeout_resource_identity": suffix * 64,
        "output_root": "/autodl/core0a/output",
        "taskset_store": "/autodl/core0a/taskset-store",
        "source_root": "/autodl/PARTSim",
    }
    return core0a.build_autodl_deployment_manifest_v2(portable, values)


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


def test_worker_count_is_deployment_not_selection_or_science(portable):
    two = _deployment(portable, workers=2)
    four = _deployment(portable, workers=4)
    assert two["selection_identity"] == four["selection_identity"]
    assert two["portable_freeze_identity"] == four["portable_freeze_identity"]
    assert two["deployment_manifest_identity"] != four["deployment_manifest_identity"]
    assert core0a.core0a_execution_identity(portable, two) != (
        core0a.core0a_execution_identity(portable, four)
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


def test_candidate_and_pending_bundle_cannot_execute(portable):
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.require_authorized_core0a_engineering_pilot(portable, None, None)
    deployment = _deployment(portable)
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        core0a.require_authorized_core0a_engineering_pilot(
            portable, {"status": core0a.UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE},
            deployment,
        )
    assert portable["authorization_gate"]["current_engineering_authorization"] is False
    assert portable["formal_authorization"] is False
    assert portable["production_authorization"] is False


def test_only_exact_independently_reviewed_authorization_can_pass(portable):
    deployment = _deployment(portable)
    material = {
        "authorization_schema": core0a.CORE0A_AUTHORIZATION_SCHEMA,
        "status": core0a.AUTHORIZED_CORE0A_ENGINEERING_PILOT,
        "independent_read_only_review": True,
        "review_identity": "9" * 64,
        "portable_freeze_identity": portable["portable_freeze_identity"],
        "selection_identity": portable["selection"]["core0a_selection_identity"],
        "source_commit": portable["source"]["git_commit"],
        "source_tree": portable["source"]["git_tree"],
        "deployment_manifest_identity": deployment["deployment_manifest_identity"],
        "execution_identity": core0a.core0a_execution_identity(portable, deployment),
        "output_namespace": core0a.CORE0A_OUTPUT_NAMESPACE,
        "authorized_execution_count": 384,
        "max_runs": 1,
        "scope": "EXACT_384_RECORD_CORE0A_ONLY",
        "formal_authorization": False,
        "production_authorization": False,
    }
    authorization = {
        **material,
        "authorization_id": domain_hash(core0a.CORE0A_AUTHORIZATION_DOMAIN, material),
    }
    assert core0a.require_authorized_core0a_engineering_pilot(
        portable, authorization, deployment,
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
                portable, damaged, deployment,
            )


def test_deployment_schema_and_source_drift_are_rejected(portable):
    deployment = _deployment(portable)
    assert core0a.validate_autodl_deployment_manifest_v2(
        deployment, portable,
    ) == deployment
    for field, value in (
        ("deployment_manifest_schema", "UNKNOWN"),
        ("source_commit", "f" * 40),
        ("source_tree", "e" * 40),
    ):
        damaged = deepcopy(deployment)
        damaged[field] = value
        unsigned = deepcopy(damaged)
        unsigned.pop("deployment_manifest_identity")
        damaged["deployment_manifest_identity"] = domain_hash(
            core0a.CORE0A_DEPLOYMENT_MANIFEST_DOMAIN, unsigned,
        )
        with pytest.raises(core0a.RTA4Core0APilotV2Error):
            core0a.validate_autodl_deployment_manifest_v2(damaged, portable)


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


def test_bundle_and_handoff_are_complete_and_non_sensitive(portable):
    handoff = core0a.build_autodl_handoff_v2(portable)
    assert core0a.validate_autodl_handoff_v2(handoff, portable) == handoff
    assert portable["required_source_files"]["production_default_closure_count"] == 53
    assert len(portable["required_source_files"]["production_default_closure"]) == 53
    assert len(handoff["steps"]) == 15
    assert handoff["authorization_required_before_any_record"] is True
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
