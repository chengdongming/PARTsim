from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import inspect
from pathlib import Path
import subprocess

import pytest

from experiments.v9_3 import rta4_core0a_authorization_v2 as authorization
from experiments.v9_3 import rta4_core0a_pilot_v2 as core0a
from experiments.v9_3.rta4_formal_config import RTA4_CORES, domain_hash


SELECTION_SHA256 = (
    "0cb353a069f8925c612ca47faa4cafd1d175e2ddd26e9b9054f3606a2648f1b7"
)
SELECTION_IDENTITY = (
    "3e14cd615c5dbaaa6a392afdcbbb569dfddc7d0dc786c3a19e8d8823658908c1"
)
ISSUED_AT = "2099-01-01T00:00:00Z"
EXPIRES_AT = "2099-01-01T12:00:00Z"


@pytest.fixture
def validated_deployment(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = workspace / core0a.CORE0A_OUTPUT_NAMESPACE
    store = workspace / core0a.CORE0A_TASKSET_STORE_NAMESPACE
    terminal = output / core0a.CORE0A_TERMINAL_DIRECTORY
    selection = {
        "core0a_selection_identity": SELECTION_IDENTITY,
        "ordered_records": [
            {
                "plan_record_identity": f"{ordinal:064x}",
                "execution_identity": f"{ordinal + 1000:064x}",
            }
            for ordinal in range(core0a.EXPECTED_EXECUTION_COUNT)
        ],
    }
    portable = {
        "portable_freeze_identity": "1" * 64,
        "source": {
            "git_commit": "2" * 40,
            "git_tree": "3" * 40,
            "observed_clean": True,
            "clean_state_required": True,
        },
        "selection": {
            "artifact_sha256": SELECTION_SHA256,
            "core0a_selection_identity": SELECTION_IDENTITY,
            "execution_count": core0a.EXPECTED_EXECUTION_COUNT,
        },
    }
    scientific_inputs = {
        "profile": "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY",
        "plan_version": "ASAP_BLOCK_V9_3_RTA4_FORMAL_PLAN_V2_SHARED_ENERGY",
        "formal_schema_sha256": "4" * 64,
        "numeric_contract_sha256": "5" * 64,
        "theory_document_sha256": "6" * 64,
        "all_plan_digest": "7" * 64,
        "plans": {
            core: {"plan_identity": f"{index + 8:064x}"}
            for index, core in enumerate(RTA4_CORES)
        },
        "config_identities": {
            core: f"{index + 20:064x}"
            for index, core in enumerate(RTA4_CORES)
        },
        "candidate_config_identity": "8" * 64,
    }
    deployment = {
        "deployment_manifest_identity": "9" * 64,
        "execution_environment_classification": (
            core0a.CORE0A_TEST_ONLY_EXECUTION_ENVIRONMENT_CLASSIFICATION
        ),
        "portable_freeze_identity": portable["portable_freeze_identity"],
        "source_commit": portable["source"]["git_commit"],
        "source_tree": portable["source"]["git_tree"],
        "source_root": str(core0a.PROJECT_ROOT.resolve()),
        "selection_identity": SELECTION_IDENTITY,
        "selection_count": core0a.EXPECTED_EXECUTION_COUNT,
        "authorization_scope": core0a.CORE0A_AUTHORIZATION_SCOPE,
        "max_runs": core0a.CORE0A_MAX_RUNS,
        "scientific_inputs": scientific_inputs,
        "production_build_manifest_identity": "a" * 64,
        "python_identity": "b" * 64,
        "toolchain_identity": "c" * 64,
        "simulator_identity": "d" * 64,
        "verifier_identity": "e" * 64,
        "environment_identity": "f" * 64,
        "deployment_workspace_root": str(workspace.resolve()),
        "deployment_workspace_identity": "0" * 64,
        "expected_output_namespace": core0a.CORE0A_OUTPUT_NAMESPACE,
        "actual_output_root": str(output.resolve()),
        "taskset_store_namespace": core0a.CORE0A_TASKSET_STORE_NAMESPACE,
        "taskset_store_root": str(store.resolve()),
        "terminal_directory_name": core0a.CORE0A_TERMINAL_DIRECTORY,
        "terminal_directory": str(terminal.resolve()),
        "resource_policy_version": core0a.CORE0A_RESOURCE_POLICY_VERSION,
        "logical_cpu_count": 16,
        "physical_memory_bytes": 64 << 30,
        "free_disk_bytes": 128 << 30,
        "resource_observation_identity": "1a" * 32,
        "worker_count": 4,
        "max_in_flight": 8,
        "memory_soft_limit_fraction": "7/10",
        "memory_soft_limit_bytes": (64 << 30) * 7 // 10,
        "checkpoint_frequency_records": 8,
        "resume_policy": "TRANSACTIONAL_EVIDENCE_STATE_MACHINE_V3",
        "retry_contract": deepcopy(core0a.CORE0A_RETRY_CONTRACT),
        "timeout_resource_identity": "1b" * 32,
        "disk_preflight_passed": True,
        "estimate_version": core0a.CORE0A_DISK_ESTIMATE_VERSION,
        "estimate_source": "FROZEN_384_SCOPE_COMPONENT_BUDGET",
        "execution_count": core0a.EXPECTED_EXECUTION_COUNT,
        "unique_taskset_slot_count": 321,
        "bytes_per_execution": 16 << 20,
        "bytes_per_unique_taskset": 8 << 20,
        "fixed_overhead_bytes": 1 << 30,
        "estimated_required_disk_bytes": 10208935936,
        "safety_margin_version": core0a.CORE0A_DISK_SAFETY_MARGIN_VERSION,
        "safety_margin_algorithm": "max(1_GiB,ceil(estimate/10))",
        "explicit_safety_margin_bytes": 1 << 30,
        "required_free_disk_bytes": 11282677760,
        "disk_estimate_identity": "1c" * 32,
        "formal_authorization": False,
        "production_authorization": False,
        "engineering_pilot_authorization": False,
    }
    initial = core0a.ValidatedCore0ADeployment(
        portable_bundle=portable,
        selection=selection,
        candidate_config={"status": "UNAUTHORIZED_PRE_PILOT"},
        production_manifest={"manifest_id": deployment[
            "production_build_manifest_identity"
        ]},
        deployment_manifest=deployment,
        source_root=str(core0a.PROJECT_ROOT.resolve()),
        deployment_workspace_root=str(workspace.resolve()),
        execution_identity="",
    )
    return replace(
        initial,
        execution_identity=core0a._combined_execution_identity(
            portable, deployment,
        ),
    )


@pytest.fixture
def path_arguments(tmp_path):
    return {
        "portable_bundle_path": tmp_path / "portable.json",
        "selection_artifact_path": tmp_path / "selection.json",
        "candidate_config_path": tmp_path / "candidate.yaml",
        "production_manifest_path": tmp_path / "production.json",
        "deployment_manifest_path": tmp_path / "deployment.json",
        "source_root": core0a.PROJECT_ROOT,
        "deployment_workspace_root": tmp_path / "workspace",
    }


@pytest.fixture
def formal_validator(monkeypatch, validated_deployment):
    calls = []

    def validate(**arguments):
        calls.append(arguments)
        return validated_deployment

    monkeypatch.setattr(
        authorization, "validate_autodl_deployment_manifest_v2", validate,
    )
    return calls


@pytest.fixture
def candidate_path(
    tmp_path, path_arguments, formal_validator,
):
    output = tmp_path / "authorization-candidate.json"
    authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=output,
        run_nonce="reaudit-run-001",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    return output


def _rehash(candidate):
    material = deepcopy(candidate)
    material.pop("authorization_candidate_identity", None)
    return {
        **material,
        "authorization_candidate_identity": domain_hash(
            authorization.CORE0A_AUTHORIZATION_CANDIDATE_DOMAIN, material,
        ),
    }


def _write_changed(path, original, field_path, value):
    changed = deepcopy(original)
    target = changed
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = value
    core0a.write_canonical_json(path, _rehash(changed))


def test_public_builder_has_only_seven_paths_output_nonce_and_time():
    assert tuple(inspect.signature(
        authorization.build_core0a_authorization_candidate_v2,
    ).parameters) == (
        "portable_bundle_path",
        "selection_artifact_path",
        "candidate_config_path",
        "production_manifest_path",
        "deployment_manifest_path",
        "source_root",
        "deployment_workspace_root",
        "authorization_output_path",
        "run_nonce",
        "issued_at",
        "expires_at",
    )


def test_public_validator_has_only_candidate_and_seven_deployment_paths():
    assert tuple(inspect.signature(
        authorization.validate_core0a_authorization_candidate_v2,
    ).parameters) == (
        "authorization_candidate_path",
        "portable_bundle_path",
        "selection_artifact_path",
        "candidate_config_path",
        "production_manifest_path",
        "deployment_manifest_path",
        "source_root",
        "deployment_workspace_root",
    )


@pytest.mark.parametrize("forbidden", [
    "validated_deployment",
    "portable_bundle",
    "deployment_manifest",
    "selection_identity",
    "portable_identity",
    "deployment_identity",
    "execution_identity",
    "source_commit",
    "source_tree",
    "output_root",
    "record_count",
    "authorization_scope",
])
def test_public_builder_rejects_validated_objects_mappings_and_identity_inputs(
    forbidden, path_arguments, validated_deployment,
):
    arguments = {
        **path_arguments,
        "authorization_output_path": Path("/tmp/not-written.json"),
        "run_nonce": "n",
        "issued_at": ISSUED_AT,
        "expires_at": EXPIRES_AT,
        forbidden: (
            validated_deployment
            if forbidden == "validated_deployment" else {}
        ),
    }
    with pytest.raises(TypeError):
        authorization.build_core0a_authorization_candidate_v2(**arguments)


def test_builder_calls_formal_file_path_validator_with_exact_seven_inputs(
    tmp_path, path_arguments, formal_validator,
):
    output = tmp_path / "candidate.json"
    authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=output,
        run_nonce="formal-call",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    assert formal_validator == [path_arguments]


def test_formal_clean_source_failure_prevents_candidate(
    monkeypatch, tmp_path, path_arguments,
):
    output = tmp_path / "candidate.json"

    def reject(**_arguments):
        raise core0a.RTA4Core0APilotV2Error(
            "portable CORE-0A bundle requires a clean worktree"
        )

    monkeypatch.setattr(
        authorization, "validate_autodl_deployment_manifest_v2", reject,
    )
    with pytest.raises(
        core0a.RTA4Core0APilotV2Error, match="clean worktree",
    ):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=output,
            run_nonce="dirty-rejected",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )
    assert not output.exists()


def test_clean_material_builds_exact_non_executable_core0a_candidate(
    candidate_path,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    assert candidate["authorization_schema"] == (
        authorization.CORE0A_AUTHORIZATION_CANDIDATE_SCHEMA
    )
    assert candidate["status"] == (
        authorization.CORE0A_AUTHORIZATION_CANDIDATE_STATUS
    )
    assert candidate["selection"] == {
        "artifact_sha256": SELECTION_SHA256,
        "selection_identity": SELECTION_IDENTITY,
        "selection_count": 384,
        "ordered_record_identity_count": 384,
        "ordered_record_identities_digest": candidate["selection"][
            "ordered_record_identities_digest"
        ],
    }
    assert candidate["scope"] == {
        "authorization_scope": core0a.CORE0A_AUTHORIZATION_SCOPE,
        "authorized_cores": ["CORE-0A"],
        "forbidden_cores": list(RTA4_CORES),
        "selection_count": 384,
        "max_runs": 1,
        "result_usage": "ENGINEERING_AUDIT_ONLY",
        "paper_result_eligible": False,
    }
    assert candidate["authorization_state"] == {
        "formal_authorization": False,
        "production_authorization": False,
        "engineering_pilot_authorization": False,
        "executable_authorization": False,
        "authorization_review_passed": False,
        "pilot_execution_allowed": False,
    }


def test_candidate_binds_source_science_paths_resources_and_disk(
    candidate_path, validated_deployment,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    deployment = validated_deployment.deployment_manifest
    assert candidate["source"] == {
        "git_commit": deployment["source_commit"],
        "git_tree": deployment["source_tree"],
        "clean_tracked_and_untracked_required": True,
        "portable_observed_clean": True,
    }
    assert candidate["scientific_inputs"] == deployment["scientific_inputs"]
    for group, names in (
        ("paths", authorization._PATH_FIELDS),
        ("resources", authorization._RESOURCE_FIELDS),
        ("disk_contract", authorization._DISK_FIELDS),
    ):
        assert candidate[group] == {
            name: deployment[name] for name in names
        }
    assert candidate["identities"]["combined_execution_identity"] == (
        validated_deployment.execution_identity
    )


def test_candidate_binds_versioned_nonce_time_and_builder_sources(
    candidate_path,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    assert candidate["request"]["validity_contract"] == (
        authorization.CORE0A_AUTHORIZATION_VALIDITY_CONTRACT
    )
    assert candidate["request"]["max_validity_seconds"] == 86400
    assert candidate["request"]["run_nonce"] == "reaudit-run-001"
    assert candidate["request"]["issued_at"] == ISSUED_AT
    assert candidate["request"]["expires_at"] == EXPIRES_AT
    assert candidate["builder_source"][
        "authorization_builder_source_identity"
    ]
    assert [row["path"] for row in candidate[
        "builder_source"
    ]["ordered_sources"]] == list(authorization._BUILDER_SOURCE_PATHS)


def test_candidate_validator_revalidates_and_reconstructs_every_field(
    candidate_path, path_arguments, formal_validator,
):
    formal_validator.clear()
    checked = authorization.validate_core0a_authorization_candidate_v2(
        authorization_candidate_path=candidate_path,
        **path_arguments,
    )
    assert checked["authorization_candidate_identity"]
    assert formal_validator == [path_arguments]


MUTATIONS = (
    (("selection", "selection_identity"), "f" * 64),
    (("selection", "selection_count"), 383),
    (("selection", "ordered_record_identities_digest"), "f" * 64),
    (("identities", "portable_freeze_identity"), "f" * 64),
    (("identities", "production_build_manifest_identity"), "f" * 64),
    (("identities", "deployment_manifest_identity"), "f" * 64),
    (("identities", "combined_execution_identity"), "f" * 64),
    (("source", "git_commit"), "f" * 40),
    (("source", "git_tree"), "f" * 40),
    (("source", "clean_tracked_and_untracked_required"), False),
    (("paths", "deployment_workspace_root"), "/tmp/drift-workspace"),
    (("paths", "actual_output_root"), "/tmp/drift-output"),
    (("paths", "taskset_store_root"), "/tmp/drift-store"),
    (("paths", "terminal_directory"), "/tmp/drift-terminal"),
    (("resources", "worker_count"), 5),
    (("resources", "max_in_flight"), 10),
    (("resources", "free_disk_bytes"), 1),
    (("resources", "checkpoint_frequency_records"), 9),
    (("resources", "resume_policy"), "DRIFT"),
    (("disk_contract", "required_free_disk_bytes"), 1),
    (("disk_contract", "disk_estimate_identity"), "f" * 64),
    (("scope", "max_runs"), 2),
    (("scope", "authorized_cores"), ["CORE-0A", "CORE-1"]),
    (("scope", "forbidden_cores"), list(RTA4_CORES[:-1])),
    (("scope", "result_usage"), "PAPER"),
    (("scope", "paper_result_eligible"), True),
    (("authorization_state", "formal_authorization"), True),
    (("authorization_state", "production_authorization"), True),
    (("authorization_state", "executable_authorization"), True),
    (("authorization_state", "authorization_review_passed"), True),
    (("authorization_state", "pilot_execution_allowed"), True),
    (("status",), "AUTHORIZED_CORE0A_ENGINEERING_PILOT"),
    (("artifact_kind",), "EXECUTABLE_ENGINEERING_AUTHORIZATION"),
    (("scope", "authorization_scope"), "EXPANDED_SCOPE"),
    (("authorization_state", "engineering_pilot_authorization"), True),
)


@pytest.mark.parametrize(
    ("field_path", "value"),
    MUTATIONS,
    ids=["-".join(path) for path, _value in MUTATIONS],
)
def test_rehashed_candidate_field_drift_is_rejected(
    field_path, value, candidate_path, path_arguments,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    _write_changed(candidate_path, candidate, field_path, value)
    with pytest.raises(
        authorization.RTA4Core0AAuthorizationV2Error,
        match="reconstructed frozen scope",
    ):
        authorization.validate_core0a_authorization_candidate_v2(
            authorization_candidate_path=candidate_path,
            **path_arguments,
        )


@pytest.mark.parametrize("nonce", [
    "",
    "not canonical",
    "非ascii",
    "path/segment",
    r"path\\segment",
    "a" * (authorization.CORE0A_AUTHORIZATION_NONCE_MAX_LENGTH + 1),
])
def test_invalid_nonce_is_rejected(
    nonce, tmp_path, path_arguments, formal_validator,
):
    with pytest.raises(
        authorization.RTA4Core0AAuthorizationV2Error, match="run_nonce",
    ):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=tmp_path / "candidate.json",
            run_nonce=nonce,
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "message"),
    [
        (ISSUED_AT, ISSUED_AT, "later"),
        ("2099-01-01T01:00:00Z", ISSUED_AT, "later"),
        (ISSUED_AT, "2099-01-02T00:00:01Z", "24 hours"),
        ("2099-01-01 00:00:00Z", EXPIRES_AT, "canonical UTC"),
        ("2099-01-01T00:00:00+00:00", EXPIRES_AT, "canonical UTC"),
        (ISSUED_AT, "2099-01-01T12:00:00.000Z", "canonical UTC"),
    ],
)
def test_invalid_time_contract_is_rejected(
    issued_at, expires_at, message, tmp_path, path_arguments, formal_validator,
):
    with pytest.raises(
        authorization.RTA4Core0AAuthorizationV2Error, match=message,
    ):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=tmp_path / "candidate.json",
            run_nonce="time-contract",
            issued_at=issued_at,
            expires_at=expires_at,
        )


def test_expired_candidate_validation_fails_closed(
    tmp_path, path_arguments, formal_validator,
):
    output = tmp_path / "expired.json"
    authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=output,
        run_nonce="expired",
        issued_at="2020-01-01T00:00:00Z",
        expires_at="2020-01-01T01:00:00Z",
    )
    with pytest.raises(
        authorization.RTA4Core0AAuthorizationV2Error, match="expired",
    ):
        authorization.validate_core0a_authorization_candidate_v2(
            authorization_candidate_path=output,
            **path_arguments,
        )


def test_nonce_and_time_change_only_candidate_not_frozen_identities(
    tmp_path, path_arguments, formal_validator,
):
    first = authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=tmp_path / "first.json",
        run_nonce="identity-one",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    second = authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=tmp_path / "second.json",
        run_nonce="identity-two",
        issued_at="2099-01-02T00:00:00Z",
        expires_at="2099-01-02T12:00:00Z",
    )
    assert first["authorization_candidate_identity"] != second[
        "authorization_candidate_identity"
    ]
    assert first["request"]["authorization_request_identity"] != second[
        "request"
    ]["authorization_request_identity"]
    assert first["selection"] == second["selection"]
    assert first["identities"]["portable_freeze_identity"] == second[
        "identities"
    ]["portable_freeze_identity"]


def test_rehashed_invalid_nonce_or_time_request_is_rejected_before_deployment(
    candidate_path, path_arguments, formal_validator,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    candidate["request"]["run_nonce"] = "path/bad"
    request_material = deepcopy(candidate["request"])
    request_material.pop("authorization_request_identity")
    candidate["request"]["authorization_request_identity"] = domain_hash(
        authorization.CORE0A_AUTHORIZATION_REQUEST_DOMAIN, request_material,
    )
    core0a.write_canonical_json(candidate_path, _rehash(candidate))
    formal_validator.clear()
    with pytest.raises(
        authorization.RTA4Core0AAuthorizationV2Error, match="run_nonce",
    ):
        authorization.validate_core0a_authorization_candidate_v2(
            authorization_candidate_path=candidate_path,
            **path_arguments,
        )
    assert formal_validator == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_nonce", "another-valid-nonce"),
        ("issued_at", "2099-01-01T01:00:00Z"),
        ("expires_at", "2099-01-01T13:00:00Z"),
    ],
)
def test_nonce_or_time_drift_with_rehashed_candidate_is_rejected(
    field, value, candidate_path, path_arguments, formal_validator,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    candidate["request"][field] = value
    core0a.write_canonical_json(candidate_path, _rehash(candidate))
    formal_validator.clear()
    with pytest.raises(
        authorization.RTA4Core0AAuthorizationV2Error,
        match="request identity/material mismatch",
    ):
        authorization.validate_core0a_authorization_candidate_v2(
            authorization_candidate_path=candidate_path,
            **path_arguments,
        )
    assert formal_validator == []


def test_candidate_cannot_pass_existing_executable_authorization_gate(
    candidate_path, validated_deployment,
):
    candidate = core0a.load_strict_canonical_json(candidate_path)
    with pytest.raises(
        core0a.RTA4Core0APilotV2Error,
        match="authorization field set mismatch",
    ):
        core0a.require_authorized_core0a_engineering_pilot(
            validated_deployment, candidate,
        )


def test_build_only_writes_candidate_not_result_store_or_terminal(
    tmp_path, path_arguments, formal_validator, validated_deployment,
):
    workspace = Path(validated_deployment.deployment_workspace_root)
    output = Path(
        validated_deployment.deployment_manifest["actual_output_root"]
    )
    store = Path(
        validated_deployment.deployment_manifest["taskset_store_root"]
    )
    terminal = Path(
        validated_deployment.deployment_manifest["terminal_directory"]
    )
    artifact = tmp_path / "artifact" / "candidate.json"
    authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=artifact,
        run_nonce="no-execution",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    assert artifact.is_file()
    assert workspace.is_dir()
    assert not output.exists()
    assert not store.exists()
    assert not terminal.exists()


def test_builder_rejects_candidate_output_inside_source_or_workspace(
    path_arguments, formal_validator, validated_deployment,
):
    forbidden = (
        core0a.PROJECT_ROOT / "candidate-must-not-be-written.json",
        Path(validated_deployment.deployment_workspace_root)
        / "candidate-must-not-be-written.json",
        Path(validated_deployment.deployment_manifest["actual_output_root"])
        / "candidate-must-not-be-written.json",
    )
    for output in forbidden:
        with pytest.raises(
            authorization.RTA4Core0AAuthorizationV2Error,
            match="outside source and deployment workspace",
        ):
            authorization.build_core0a_authorization_candidate_v2(
                **path_arguments,
                authorization_output_path=output,
                run_nonce="isolated-output",
                issued_at=ISSUED_AT,
                expires_at=EXPIRES_AT,
            )
        assert not output.exists()


def test_candidate_write_is_atomic_canonical_and_leaves_no_temp_file(
    tmp_path, path_arguments, formal_validator,
):
    output = tmp_path / "atomic-candidate.json"
    candidate = authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=output,
        run_nonce="atomic-write",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    assert output.read_bytes() == core0a.canonical_json_bytes(candidate)
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_interrupted_atomic_candidate_write_leaves_no_partial_target(
    monkeypatch, tmp_path, path_arguments, formal_validator,
):
    output = tmp_path / "interrupted-candidate.json"

    def fail_replace(_source, _target):
        raise OSError("bounded replace failure")

    monkeypatch.setattr(authorization.os, "replace", fail_replace)
    with pytest.raises(OSError, match="bounded replace failure"):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=output,
            run_nonce="atomic-interruption",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("existing_target", [False, True])
def test_candidate_atomic_write_flush_failure_preserves_target_and_cleans_temp(
    monkeypatch, tmp_path, path_arguments, formal_validator, existing_target,
):
    output = tmp_path / "flush-failure.json"
    original = b"existing-candidate-bytes"
    if existing_target:
        output.write_bytes(original)
    real_fdopen = authorization.os.fdopen

    class FlushFailureStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.stream.close()

        def write(self, payload):
            return self.stream.write(payload)

        def flush(self):
            raise OSError("bounded flush failure")

        def fileno(self):
            return self.stream.fileno()

    def failing_fdopen(descriptor, mode):
        return FlushFailureStream(real_fdopen(descriptor, mode))

    monkeypatch.setattr(authorization.os, "fdopen", failing_fdopen)
    with pytest.raises(OSError, match="bounded flush failure"):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=output,
            run_nonce="atomic-flush-failure",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )
    assert output.exists() is existing_target
    if existing_target:
        assert output.read_bytes() == original
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("existing_target", [False, True])
def test_candidate_atomic_write_file_fsync_failure_preserves_target(
    monkeypatch, tmp_path, path_arguments, formal_validator, existing_target,
):
    output = tmp_path / "file-fsync-failure.json"
    original = b"existing-candidate-bytes"
    if existing_target:
        output.write_bytes(original)

    def fail_file_fsync(_descriptor):
        raise OSError("bounded file fsync failure")

    monkeypatch.setattr(authorization.os, "fsync", fail_file_fsync)
    with pytest.raises(OSError, match="bounded file fsync failure"):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=output,
            run_nonce="atomic-file-fsync-failure",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )
    assert output.exists() is existing_target
    if existing_target:
        assert output.read_bytes() == original
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_candidate_atomic_write_replace_failure_preserves_existing_target(
    monkeypatch, tmp_path, path_arguments, formal_validator,
):
    output = tmp_path / "replace-existing-failure.json"
    original = b"existing-candidate-bytes"
    output.write_bytes(original)

    def fail_replace(_source, _target):
        raise OSError("bounded replace failure")

    monkeypatch.setattr(authorization.os, "replace", fail_replace)
    with pytest.raises(OSError, match="bounded replace failure"):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=output,
            run_nonce="atomic-replace-existing",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_candidate_atomic_write_parent_fsync_failure_has_canonical_target(
    monkeypatch, tmp_path, path_arguments, formal_validator,
):
    expected = authorization.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=tmp_path / "expected.json",
        run_nonce="atomic-parent-fsync",
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
    )
    output = tmp_path / "parent-fsync-failure.json"
    real_fsync = authorization.os.fsync
    calls = 0

    def fail_parent_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("bounded parent fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(authorization.os, "fsync", fail_parent_fsync)
    with pytest.raises(OSError, match="bounded parent fsync failure"):
        authorization.build_core0a_authorization_candidate_v2(
            **path_arguments,
            authorization_output_path=output,
            run_nonce="atomic-parent-fsync",
            issued_at=ISSUED_AT,
            expires_at=EXPIRES_AT,
        )
    assert output.read_bytes() == core0a.canonical_json_bytes(expected)
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_module_and_cli_expose_no_execution_or_upgrade_entry_point():
    public_functions = {
        name for name, value in vars(authorization).items()
        if inspect.isfunction(value) and not name.startswith("_")
        and value.__module__ == authorization.__name__
    }
    assert public_functions == {
        "build_core0a_authorization_candidate_v2",
        "validate_core0a_authorization_candidate_v2",
    }
    cli = (
        core0a.PROJECT_ROOT
        / "scripts/build_v9_3_rta4_core0a_authorization.py"
    )
    help_result = subprocess.run(
        ["python3", str(cli), "--help"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert "--check" in help_result.stdout
    assert "--execute" not in help_result.stdout
    assert "--upgrade" not in help_result.stdout
