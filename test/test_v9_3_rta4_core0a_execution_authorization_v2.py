from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import inspect
from pathlib import Path

import pytest

from experiments.v9_3 import rta4_core0a_authorization_v2 as candidate_auth
from experiments.v9_3 import rta4_core0a_execution_authorization_v2 as execution_auth
from experiments.v9_3 import rta4_core0a_pilot_v2 as core0a
from experiments.v9_3.rta4_formal_config import RTA4_CORES, domain_hash


SELECTION_SHA256 = (
    "0cb353a069f8925c612ca47faa4cafd1d175e2ddd26e9b9054f3606a2648f1b7"
)
SELECTION_IDENTITY = (
    "3e14cd615c5dbaaa6a392afdcbbb569dfddc7d0dc786c3a19e8d8823658908c1"
)
ISSUED_AT = "2099-01-01T00:00:00Z"
REVIEWED_AT = "2099-01-01T01:00:00Z"
VERIFIED_AT = "2099-01-01T02:00:00Z"
CURRENT_UTC = "2099-01-01T03:00:00Z"
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
def path_arguments(tmp_path, validated_deployment):
    return {
        "portable_bundle_path": tmp_path / "portable.json",
        "selection_artifact_path": tmp_path / "selection.json",
        "candidate_config_path": tmp_path / "candidate.yaml",
        "production_manifest_path": tmp_path / "production.json",
        "deployment_manifest_path": tmp_path / "deployment.json",
        "source_root": core0a.PROJECT_ROOT,
        "deployment_workspace_root": Path(
            validated_deployment.deployment_workspace_root
        ),
    }


@pytest.fixture
def formal_validators(monkeypatch, validated_deployment):
    calls = []

    def validate(**arguments):
        calls.append(arguments)
        return validated_deployment

    monkeypatch.setattr(
        candidate_auth, "validate_autodl_deployment_manifest_v2", validate,
    )
    monkeypatch.setattr(
        execution_auth, "validate_autodl_deployment_manifest_v2", validate,
    )
    return calls


def _build_candidate(
    path, path_arguments, *, nonce="CORE0A-TEST-001",
    issued_at=ISSUED_AT, expires_at=EXPIRES_AT,
):
    return candidate_auth.build_core0a_authorization_candidate_v2(
        **path_arguments,
        authorization_output_path=path,
        run_nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )


@pytest.fixture
def candidate_path(tmp_path, path_arguments, formal_validators):
    path = tmp_path / "authorization-candidate.json"
    _build_candidate(path, path_arguments)
    return path


@pytest.fixture
def pass_report(tmp_path):
    path = tmp_path / "candidate-review.md"
    path.write_text(
        "# TEST_ONLY independent review\n\n"
        "P0=0, P1=0.\n\n"
        f"`{execution_auth.CORE0A_CANDIDATE_REAUDIT_PASS}`\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def review_receipt_path(
    tmp_path, candidate_path, pass_report, path_arguments,
):
    path = tmp_path / "review-receipt.json"
    execution_auth.build_core0a_candidate_review_receipt_v2(
        candidate_path=candidate_path,
        review_report_path=pass_report,
        reviewer_label="TEST_ONLY_REVIEWER",
        reviewed_at=REVIEWED_AT,
        review_receipt_output_path=path,
        **path_arguments,
    )
    return path


@pytest.fixture
def executable_authorization_path(
    tmp_path, candidate_path, review_receipt_path, path_arguments,
):
    path = tmp_path / "test-only-executable-authorization.json"
    execution_auth.build_core0a_executable_engineering_authorization_v2(
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        authorization_output_path=path,
        verification_time=VERIFIED_AT,
        **path_arguments,
    )
    return path


def _rehash_candidate(candidate):
    material = deepcopy(candidate)
    material.pop("authorization_candidate_identity", None)
    return {
        **material,
        "authorization_candidate_identity": domain_hash(
            candidate_auth.CORE0A_AUTHORIZATION_CANDIDATE_DOMAIN, material,
        ),
    }


def _rehash_receipt(receipt):
    material = deepcopy(receipt)
    material.pop("review_receipt_identity", None)
    return {
        **material,
        "review_receipt_identity": domain_hash(
            execution_auth.CORE0A_REVIEW_RECEIPT_DOMAIN, material,
        ),
    }


def _rehash_authorization(authorization):
    material = deepcopy(authorization)
    material.pop("executable_authorization_identity", None)
    return {
        **material,
        "executable_authorization_identity": domain_hash(
            execution_auth.CORE0A_EXECUTABLE_AUTHORIZATION_DOMAIN, material,
        ),
    }


def _mutate(document, field_path, value):
    changed = deepcopy(document)
    target = changed
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = value
    return changed


def test_public_authorization_boundaries_are_path_only():
    assert tuple(inspect.signature(
        execution_auth.build_core0a_executable_engineering_authorization_v2,
    ).parameters) == (
        "candidate_path", "review_receipt_path",
        "portable_bundle_path", "selection_artifact_path",
        "candidate_config_path", "production_manifest_path",
        "deployment_manifest_path", "source_root",
        "deployment_workspace_root", "authorization_output_path",
        "verification_time",
    )
    assert tuple(inspect.signature(
        execution_auth.validate_core0a_executable_engineering_authorization_v2,
    ).parameters) == (
        "executable_authorization_path", "candidate_path",
        "review_receipt_path", "portable_bundle_path",
        "selection_artifact_path", "candidate_config_path",
        "production_manifest_path", "deployment_manifest_path",
        "source_root", "deployment_workspace_root",
    )
    assert tuple(inspect.signature(
        execution_auth.preflight_core0a_engineering_pilot_execution_v2,
    ).parameters) == (
        "executable_authorization_path", "candidate_path",
        "review_receipt_path", "portable_bundle_path",
        "selection_artifact_path", "candidate_config_path",
        "production_manifest_path", "deployment_manifest_path",
        "source_root", "deployment_workspace_root",
        "consumption_receipt_path", "current_utc",
    )


@pytest.mark.parametrize("forbidden", [
    "validated_deployment", "candidate", "review_receipt",
    "selection_identity", "execution_identity", "run_nonce",
    "authorization_scope", "output_root", "max_runs",
])
def test_authorization_builder_rejects_objects_mappings_and_overrides(
    forbidden, path_arguments, validated_deployment,
):
    arguments = {
        "candidate_path": Path("/tmp/candidate.json"),
        "review_receipt_path": Path("/tmp/receipt.json"),
        **path_arguments,
        "authorization_output_path": Path("/tmp/authorization.json"),
        "verification_time": VERIFIED_AT,
        forbidden: (
            validated_deployment
            if forbidden == "validated_deployment" else {}
        ),
    }
    with pytest.raises(TypeError):
        execution_auth.build_core0a_executable_engineering_authorization_v2(
            **arguments,
        )


def test_valid_nonce_and_time_changes_create_distinct_valid_candidates(
    tmp_path, path_arguments, formal_validators,
):
    first_path = tmp_path / "candidate-one.json"
    second_path = tmp_path / "candidate-two.json"
    first = _build_candidate(first_path, path_arguments)
    second = _build_candidate(
        second_path, path_arguments,
        nonce="CORE0A-TEST-002",
        issued_at="2099-01-02T00:00:00Z",
        expires_at="2099-01-02T12:00:00Z",
    )
    assert candidate_auth.validate_core0a_authorization_candidate_v2(
        authorization_candidate_path=first_path, **path_arguments,
    ) == first
    assert candidate_auth.validate_core0a_authorization_candidate_v2(
        authorization_candidate_path=second_path, **path_arguments,
    ) == second
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


def test_pass_report_builds_exact_test_only_review_receipt(
    candidate_path, pass_report, review_receipt_path, path_arguments,
):
    receipt = execution_auth.validate_core0a_candidate_review_receipt_v2(
        review_receipt_path=review_receipt_path,
        candidate_path=candidate_path,
        **path_arguments,
    )
    candidate = core0a.load_strict_canonical_json(candidate_path)
    assert receipt["status"] == execution_auth.CORE0A_REVIEW_RECEIPT_STATUS
    assert receipt["candidate_identity"] == candidate[
        "authorization_candidate_identity"
    ]
    assert receipt["candidate_artifact_sha256"] == hashlib.sha256(
        candidate_path.read_bytes()
    ).hexdigest()
    assert receipt["request_identity"] == candidate["request"][
        "authorization_request_identity"
    ]
    assert receipt["review_report_sha256"] == hashlib.sha256(
        pass_report.read_bytes()
    ).hexdigest()
    assert receipt["review_decision"] == "P0_0_P1_0_PASS"
    assert receipt["formal_authorization"] is False
    assert receipt["production_authorization"] is False
    assert receipt["receipt_classification"] == (
        execution_auth.CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION
    )


@pytest.mark.parametrize("terminal", [
    execution_auth.CORE0A_CANDIDATE_REAUDIT_REPAIR_REQUIRED,
    execution_auth.CORE0A_CANDIDATE_REAUDIT_BLOCKED,
])
def test_non_pass_review_report_is_rejected(
    terminal, tmp_path, candidate_path, path_arguments,
):
    report = tmp_path / "failed-review.md"
    report.write_text(f"`{terminal}`\n", encoding="utf-8")
    output = tmp_path / "must-not-exist-receipt.json"
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="not PASS",
    ):
        execution_auth.build_core0a_candidate_review_receipt_v2(
            candidate_path=candidate_path,
            review_report_path=report,
            reviewer_label="TEST_ONLY_REVIEWER",
            reviewed_at=REVIEWED_AT,
            review_receipt_output_path=output,
            **path_arguments,
        )
    assert not output.exists()


def test_old_receipt_rejects_new_valid_candidate_and_request(
    tmp_path, candidate_path, review_receipt_path, path_arguments,
):
    new_candidate_path = tmp_path / "new-candidate.json"
    _build_candidate(
        new_candidate_path, path_arguments, nonce="CORE0A-TEST-NEW",
    )
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="reconstruction",
    ):
        execution_auth.validate_core0a_candidate_review_receipt_v2(
            review_receipt_path=review_receipt_path,
            candidate_path=new_candidate_path,
            **path_arguments,
        )


def test_candidate_byte_drift_is_rejected_by_receipt_validation(
    candidate_path, review_receipt_path, path_arguments,
):
    candidate_path.write_bytes(candidate_path.read_bytes() + b" ")
    with pytest.raises(
        core0a.RTA4Core0APilotV2Error, match="not canonical",
    ):
        execution_auth.validate_core0a_candidate_review_receipt_v2(
            review_receipt_path=review_receipt_path,
            candidate_path=candidate_path,
            **path_arguments,
        )


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("candidate_identity",), "f" * 64),
        (("request_identity",), "f" * 64),
        (("deployment_manifest_identity",), "f" * 64),
    ],
)
def test_rehashed_review_receipt_binding_drift_is_rejected(
    field_path, value, candidate_path, review_receipt_path, path_arguments,
):
    receipt = core0a.load_strict_canonical_json(review_receipt_path)
    changed = _rehash_receipt(_mutate(receipt, field_path, value))
    core0a.write_canonical_json(review_receipt_path, changed)
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="reconstruction",
    ):
        execution_auth.validate_core0a_candidate_review_receipt_v2(
            review_receipt_path=review_receipt_path,
            candidate_path=candidate_path,
            **path_arguments,
        )


def test_review_report_sha_drift_is_rejected(
    candidate_path, pass_report, review_receipt_path, path_arguments,
):
    pass_report.write_text(
        "changed reviewed evidence\n"
        f"`{execution_auth.CORE0A_CANDIDATE_REAUDIT_PASS}`\n",
        encoding="utf-8",
    )
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="reconstruction",
    ):
        execution_auth.validate_core0a_candidate_review_receipt_v2(
            review_receipt_path=review_receipt_path,
            candidate_path=candidate_path,
            **path_arguments,
        )


def test_missing_or_mismatched_receipt_prevents_authorization(
    tmp_path, candidate_path, pass_report, path_arguments,
):
    missing = tmp_path / "missing-receipt.json"
    with pytest.raises(core0a.RTA4Core0APilotV2Error):
        execution_auth.build_core0a_executable_engineering_authorization_v2(
            candidate_path=candidate_path,
            review_receipt_path=missing,
            authorization_output_path=tmp_path / "authorization.json",
            verification_time=VERIFIED_AT,
            **path_arguments,
        )
    other_candidate = tmp_path / "other-candidate.json"
    _build_candidate(
        other_candidate, path_arguments, nonce="CORE0A-OTHER",
    )
    receipt_path = tmp_path / "other-receipt.json"
    execution_auth.build_core0a_candidate_review_receipt_v2(
        candidate_path=other_candidate,
        review_report_path=pass_report,
        reviewer_label="TEST_ONLY_REVIEWER",
        reviewed_at=REVIEWED_AT,
        review_receipt_output_path=receipt_path,
        **path_arguments,
    )
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="reconstruction",
    ):
        execution_auth.build_core0a_executable_engineering_authorization_v2(
            candidate_path=candidate_path,
            review_receipt_path=receipt_path,
            authorization_output_path=tmp_path / "authorization.json",
            verification_time=VERIFIED_AT,
            **path_arguments,
        )


def test_expired_candidate_cannot_build_authorization(
    tmp_path, pass_report, path_arguments, formal_validators,
):
    candidate_path = tmp_path / "expired-candidate.json"
    _build_candidate(
        candidate_path, path_arguments,
        nonce="CORE0A-EXPIRED",
        issued_at="2020-01-01T00:00:00Z",
        expires_at="2020-01-01T01:00:00Z",
    )
    with pytest.raises(
        candidate_auth.RTA4Core0AAuthorizationV2Error, match="expired",
    ):
        execution_auth.build_core0a_candidate_review_receipt_v2(
            candidate_path=candidate_path,
            review_report_path=pass_report,
            reviewer_label="TEST_ONLY_REVIEWER",
            reviewed_at="2020-01-01T00:30:00Z",
            review_receipt_output_path=tmp_path / "receipt.json",
            **path_arguments,
        )


def test_valid_reviewed_candidate_builds_test_only_authorization(
    executable_authorization_path, candidate_path,
    review_receipt_path, path_arguments,
):
    validated = (
        execution_auth.validate_core0a_executable_engineering_authorization_v2(
            executable_authorization_path=executable_authorization_path,
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            **path_arguments,
        )
    )
    authorization = validated.authorization
    candidate = validated.candidate
    assert authorization["authorization_schema"] == (
        execution_auth.CORE0A_EXECUTABLE_AUTHORIZATION_SCHEMA
    )
    assert authorization["status"] == (
        core0a.AUTHORIZED_CORE0A_ENGINEERING_PILOT
    )
    assert authorization["authorization_classification"] == (
        execution_auth.CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION
    )
    assert authorization["test_only_non_executable_fixture"] is True
    assert authorization["candidate_binding"]["candidate_identity"] == (
        candidate["authorization_candidate_identity"]
    )
    assert authorization["selection"]["selection_count"] == 384
    assert authorization["scope"] == candidate["scope"]
    assert authorization["scope"]["authorized_cores"] == ["CORE-0A"]
    assert authorization["scope"]["forbidden_cores"] == list(RTA4_CORES)
    assert authorization["scope"]["max_runs"] == 1
    assert authorization["authorization_state"] == {
        "engineering_pilot_authorization": True,
        "executable_authorization": True,
        "authorization_review_passed": True,
        "pilot_execution_allowed": True,
        "formal_authorization": False,
        "production_authorization": False,
    }


def test_verification_time_before_review_is_rejected(
    tmp_path, candidate_path, review_receipt_path, path_arguments,
):
    output = tmp_path / "must-not-exist-authorization.json"
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="precedes independent review",
    ):
        execution_auth.build_core0a_executable_engineering_authorization_v2(
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            authorization_output_path=output,
            verification_time="2099-01-01T00:30:00Z",
            **path_arguments,
        )
    assert not output.exists()


AUTHORIZATION_MUTATIONS = (
    (("candidate_binding", "candidate_identity"), "f" * 64),
    (("review_binding", "review_receipt_identity"), "f" * 64),
    (("review_binding", "reviewed_at"), "2099-01-01T01:30:00Z"),
    (("selection", "selection_identity"), "f" * 64),
    (("selection", "selection_count"), 383),
    (("selection", "ordered_record_identities_digest"), "f" * 64),
    (("identities", "portable_freeze_identity"), "f" * 64),
    (("identities", "production_build_manifest_identity"), "f" * 64),
    (("identities", "deployment_manifest_identity"), "f" * 64),
    (("identities", "combined_execution_identity"), "f" * 64),
    (("source", "git_commit"), "f" * 40),
    (("source", "git_tree"), "f" * 40),
    (("paths", "deployment_workspace_root"), "/tmp/drift-workspace"),
    (("paths", "actual_output_root"), "/tmp/drift-output"),
    (("paths", "taskset_store_root"), "/tmp/drift-store"),
    (("resources", "worker_count"), 5),
    (("resources", "retry_contract"), {"drift": True}),
    (("disk_contract", "required_free_disk_bytes"), 1),
    (("request", "run_nonce"), "CORE0A-DRIFT"),
    (("request", "issued_at"), "2099-01-01T00:30:00Z"),
    (("request", "expires_at"), "2099-01-01T11:00:00Z"),
    (("scope", "max_runs"), 2),
    (("scope", "authorized_cores"), ["CORE-0A", "CORE-1"]),
    (("scope", "forbidden_cores"), list(RTA4_CORES[:-1])),
    (("scope", "result_usage"), "PAPER"),
    (("scope", "paper_result_eligible"), True),
    (("authorization_state", "engineering_pilot_authorization"), False),
    (("authorization_state", "executable_authorization"), False),
    (("authorization_state", "authorization_review_passed"), False),
    (("authorization_state", "pilot_execution_allowed"), False),
    (("authorization_state", "formal_authorization"), True),
    (("authorization_state", "production_authorization"), True),
)


@pytest.mark.parametrize(
    ("field_path", "value"),
    AUTHORIZATION_MUTATIONS,
    ids=["-".join(path) for path, _value in AUTHORIZATION_MUTATIONS],
)
def test_rehashed_executable_authorization_drift_is_rejected(
    field_path, value, executable_authorization_path,
    candidate_path, review_receipt_path, path_arguments,
):
    authorization = core0a.load_strict_canonical_json(
        executable_authorization_path,
    )
    changed = _rehash_authorization(
        _mutate(authorization, field_path, value),
    )
    core0a.write_canonical_json(executable_authorization_path, changed)
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="exact reconstruction",
    ):
        execution_auth.validate_core0a_executable_engineering_authorization_v2(
            executable_authorization_path=executable_authorization_path,
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            **path_arguments,
        )


def test_candidate_cannot_substitute_for_executable_authorization(
    candidate_path, review_receipt_path, path_arguments,
):
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="exact reconstruction",
    ):
        execution_auth.validate_core0a_executable_engineering_authorization_v2(
            executable_authorization_path=candidate_path,
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            **path_arguments,
        )


def test_unconsumed_preflight_returns_immutable_test_only_context(
    tmp_path, executable_authorization_path,
    candidate_path, review_receipt_path, path_arguments,
):
    context = execution_auth.preflight_core0a_engineering_pilot_execution_v2(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        consumption_receipt_path=tmp_path / "absent-consumption.json",
        current_utc=CURRENT_UTC,
        **path_arguments,
    )
    assert context.execution_mode == "NEW_RUN_ONLY"
    assert context.new_run_allowed is True
    assert context.resume_allowed is False
    assert context.max_runs == 1
    assert context.authorized_cores == ("CORE-0A",)
    assert context.authorization_classification == (
        execution_auth.CORE0A_TEST_ONLY_AUTHORIZATION_CLASSIFICATION
    )
    assert context.test_only_non_executable_fixture is True
    assert context.runner_invocation_allowed is False
    with pytest.raises((AttributeError, TypeError)):
        context.max_runs = 2


def test_run_started_blocks_second_run_but_allows_same_run_resume(
    tmp_path, executable_authorization_path,
    candidate_path, review_receipt_path, path_arguments,
):
    consumption = tmp_path / "consumption.json"
    started = execution_auth.write_test_only_core0a_run_started_receipt_v2(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        consumption_receipt_path=consumption,
        started_at=CURRENT_UTC,
        **path_arguments,
    )
    assert started["status"] == execution_auth.CORE0A_RUN_STARTED
    assert consumption.read_bytes() == core0a.canonical_json_bytes(started)
    assert not list(tmp_path.glob(f".{consumption.name}.*.tmp"))
    assert execution_auth.validate_core0a_nonce_consumption_receipt_v2(
        consumption_receipt_path=consumption,
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        **path_arguments,
    ) == started
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="second run",
    ):
        execution_auth.preflight_core0a_engineering_pilot_execution_v2(
            executable_authorization_path=executable_authorization_path,
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            consumption_receipt_path=consumption,
            current_utc="2099-01-01T04:00:00Z",
            **path_arguments,
        )
    context = execution_auth.preflight_core0a_engineering_pilot_resume_v2(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        consumption_receipt_path=consumption,
        current_utc="2099-01-01T04:00:00Z",
        **path_arguments,
    )
    assert context.execution_mode == "RESUME_EXISTING_RUN_ONLY"
    assert context.new_run_allowed is False
    assert context.resume_allowed is True


def test_consumption_receipt_cannot_be_reused_by_other_authorization(
    tmp_path, executable_authorization_path, candidate_path,
    review_receipt_path, pass_report, path_arguments,
):
    consumption = tmp_path / "consumption.json"
    execution_auth.write_test_only_core0a_run_started_receipt_v2(
        executable_authorization_path=executable_authorization_path,
        candidate_path=candidate_path,
        review_receipt_path=review_receipt_path,
        consumption_receipt_path=consumption,
        started_at=CURRENT_UTC,
        **path_arguments,
    )
    other_candidate = tmp_path / "other-candidate.json"
    _build_candidate(
        other_candidate, path_arguments, nonce="CORE0A-OTHER-AUTH",
    )
    other_receipt = tmp_path / "other-review-receipt.json"
    execution_auth.build_core0a_candidate_review_receipt_v2(
        candidate_path=other_candidate,
        review_report_path=pass_report,
        reviewer_label="TEST_ONLY_REVIEWER",
        reviewed_at=REVIEWED_AT,
        review_receipt_output_path=other_receipt,
        **path_arguments,
    )
    other_authorization = tmp_path / "other-authorization.json"
    execution_auth.build_core0a_executable_engineering_authorization_v2(
        candidate_path=other_candidate,
        review_receipt_path=other_receipt,
        authorization_output_path=other_authorization,
        verification_time=VERIFIED_AT,
        **path_arguments,
    )
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="differs from authorization",
    ):
        execution_auth.preflight_core0a_engineering_pilot_resume_v2(
            executable_authorization_path=other_authorization,
            candidate_path=other_candidate,
            review_receipt_path=other_receipt,
            consumption_receipt_path=consumption,
            current_utc="2099-01-01T04:00:00Z",
            **path_arguments,
        )


def test_interrupted_test_only_consumption_write_leaves_no_partial_file(
    monkeypatch, tmp_path, executable_authorization_path,
    candidate_path, review_receipt_path, path_arguments,
):
    output = tmp_path / "interrupted-consumption.json"

    def fail_replace(_source, _target):
        raise OSError("bounded consumption replace failure")

    monkeypatch.setattr(candidate_auth.os, "replace", fail_replace)
    with pytest.raises(OSError, match="bounded consumption replace failure"):
        execution_auth.write_test_only_core0a_run_started_receipt_v2(
            executable_authorization_path=executable_authorization_path,
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            consumption_receipt_path=output,
            started_at=CURRENT_UTC,
            **path_arguments,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_expired_authorization_preflight_is_rejected(
    tmp_path, executable_authorization_path,
    candidate_path, review_receipt_path, path_arguments,
):
    with pytest.raises(
        execution_auth.RTA4Core0AExecutionAuthorizationV2Error,
        match="not currently valid",
    ):
        execution_auth.preflight_core0a_engineering_pilot_execution_v2(
            executable_authorization_path=executable_authorization_path,
            candidate_path=candidate_path,
            review_receipt_path=review_receipt_path,
            consumption_receipt_path=tmp_path / "absent.json",
            current_utc=EXPIRES_AT,
            **path_arguments,
        )


def test_selection_and_execution_isolation_invariants(
    validated_deployment, executable_authorization_path,
):
    selection_path = core0a.PROJECT_ROOT / core0a.SELECTION_ARTIFACT_PATH
    assert hashlib.sha256(selection_path.read_bytes()).hexdigest() == (
        SELECTION_SHA256
    )
    candidate = core0a.load_strict_canonical_json(
        executable_authorization_path,
    )
    assert candidate["selection"]["selection_identity"] == SELECTION_IDENTITY
    workspace = Path(validated_deployment.deployment_workspace_root)
    assert not Path(
        validated_deployment.deployment_manifest["actual_output_root"]
    ).exists()
    assert not Path(
        validated_deployment.deployment_manifest["taskset_store_root"]
    ).exists()
    assert not Path(
        validated_deployment.deployment_manifest["terminal_directory"]
    ).exists()
    assert list(workspace.iterdir()) == []
    source = Path(execution_auth.__file__).read_text(encoding="utf-8")
    assert "rta4_pilot_execution" not in source
    assert "execute_plan(" not in source
