#!/usr/bin/env python3
"""Shared, fail-closed support for the B4-PE I4A manifest layer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
MANIFEST_PROTOCOL_V1_PATH = B4_DIR / "manifest_protocol_v1.json"
MANIFEST_PROTOCOL_PATH = B4_DIR / "manifest_protocol_v2.json"
MANIFEST_PROTOCOL_V3_PATH = B4_DIR / "manifest_protocol_v3.json"
MANIFEST_PROTOCOL_V4_PATH = B4_DIR / "manifest_protocol_v4.json"
IDENTITY_PROTOCOL_PATH = B4_DIR / "protocol_resolution_v1.json"
OBSERVABILITY_CONTRACT_PATH = (
    B4_DIR / "observability_summary_contract_v1.json"
)
CANDIDATE_V1_PATH = B4_DIR / "b4_pe_freeze_candidate_v1.json"
CANDIDATE_V2_PATH = B4_DIR / "b4_pe_freeze_candidate_v2.json"
CANDIDATE_V3_PATH = B4_DIR / "b4_pe_freeze_candidate_v3.json"
CANDIDATE_V4_PATH = B4_DIR / "b4_pe_freeze_candidate_v4.json"
OBSERVABILITY_CONTRACT_V2_PATH = B4_DIR / "observability_summary_contract_v2.json"
ANALYSIS_CONTRACT_V2_PATH = B4_DIR / "analysis_contract_v2.json"
IDENTITY_REFERENCE_PATH = B4_DIR / "tests" / "test_protocol_resolution.py"
FROZEN_DOCUMENT_PATH = (
    REPO_ROOT / "docs" / "experiments" /
    "ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md"
)
SYSTEM_TEMPLATE_PATH = REPO_ROOT / "v9_3_b4_priority_energy_system_template.yml"
SEMANTIC_HASH_PLACEHOLDER = "__B4PE_MATERIALIZED_TASKSET_SEMANTIC_HASH__"
V4_CANDIDATE_CODE_COMMIT = "681409e35012d2bc883045e4d10a048b36a6483f"
V4_CANDIDATE_CODE_TREE = "266ecfdca0c1bc194e2ce77a295254893b9737ca"
V4_GOVERNANCE = {
    "formal_runs_authorized": False,
    "negative_control_runs_authorized": False,
    "not_final_until_independent_review": True,
    "paper_result_authorized": False,
    "pilot_runs_authorized": True,
    "silent_changes_forbidden": True,
}
V4_RUNTIME_ARTIFACTS = {
    "dual_python_launcher": {
        "logical_path": "run_with_b4pe_dual_pythonhome_v2.sh",
        "role": "dual_python_launcher",
        "sha256":
            "e000fd8bb4e12505b86abb7b33573d2d8a0ce4d3948fb1d801235bc0be5c6f25",
    },
    "libcmdarg": {
        "logical_path": "pilot-runtime/lib/libcmdarg.so.0",
        "role": "command_line_parser_shared_library",
        "sha256":
            "02aa859ea7eee6a5b3c3c6c32826656349ee629f19d5a86c245acfb44186c5fd",
    },
    "libmetasim": {
        "logical_path": "pilot-runtime/lib/libmetasim.so.3",
        "role": "simulation_kernel_shared_library",
        "sha256":
            "20734b7ffff7db8352593aa1c89f20716dbcec8462e638591e9855d20525e324",
    },
    "libpython3_8": {
        "logical_path": "host-runtime-lib/libpython3.8.so.1.0",
        "role": "embedded_python38_shared_library",
        "sha256":
            "d6b4470a33290dd9203b9a497b4fa9744e55ff63f59b788d75128973571a66a6",
    },
    "librtsim": {
        "logical_path": "pilot-runtime/lib/librtsim.so.3",
        "role": "real_time_simulation_shared_library",
        "sha256":
            "f566e702435da6070059ff5ec1b47b7b8063e5081db1eb2351de57bc3f6245de",
    },
    "simulator": {
        "logical_path": "pilot-runtime/bin/rtsim",
        "role": "simulator",
        "sha256":
            "96004d1aec42cac73bea72d4fe0d5c2a5e814453bfeeb16d09026c4ff8746f7d",
    },
}
V4_RUNTIME_EVIDENCE = {
    "normalized_dynamic_dependency_manifest": {
        "container_evidence": "stage2b_supplemental_seal",
        "filename": "dynamic_dependencies.normalized.json",
        "role": "normalized_dynamic_dependency_manifest",
        "serialization": "canonical_json",
        "sha256":
            "876abaa6b8812578c93ff12ac4a977f9da65240bb81f623e0233b57fcb8e9e3b",
        "supersedes": {
            "filename": "dynamic_dependency_manifest.jsonl",
            "sha256":
                "b259ea7727798fa1fc38319a73d16e0699b2d15408a09244a8b72a0ed039ee5f",
        },
        "verification_status": "independently_verified",
    },
    "python310_tree_manifest": {
        "container_evidence": "stage2b_supplemental_seal",
        "filename": "python310_tree.normalized.jsonl",
        "role": "python310_tree_manifest",
        "serialization": "canonical_jsonl",
        "sha256":
            "1e37cdfa1c0fd7a9ed4f7c0f650a363509530f06e5d609216c0392afc1993e99",
        "verification_status": "independently_verified",
    },
    "python38_tree_manifest": {
        "container_evidence": "stage2b_supplemental_seal",
        "filename": "python38_tree.normalized.jsonl",
        "role": "python38_tree_manifest",
        "serialization": "canonical_jsonl",
        "sha256":
            "6d903c9a25e20cfee4023ddc9bb163d5a1bfd6e14370cd6ade48d88e3ae1bfbe",
        "verification_status": "independently_verified",
    },
    "stage2a_runtime_execution_closure": {
        "filename": "runtime_execution_closure_v1.json",
        "role": "noncampaign_runtime_execution_closure",
        "schema_version": 1,
        "sha256":
            "795533ed3ea3dadb950eef1dc1057be0a9efcdc7668706fe92752853322fdc91",
        "verification_status": "independently_verified",
    },
    "stage2b_supplemental_seal": {
        "filename": "pilot_deployment_runtime_supplemental_seal_v2.json",
        "role": "deterministic_runtime_supplemental_seal",
        "schema_version": 2,
        "sha256":
            "c3be2ef579f9650723237213dd778ac2ef57c804ad9ae5100787f6ea9eba9f60",
        "supersedes": {
            "filename": "pilot_deployment_runtime_seal_v1.json",
            "sha256":
                "1f9dd5b512b2318b0e4395c7253d7c5c3102caa45eb212cf75c80baf48e3ea24",
        },
        "verification_status": "independently_verified",
    },
    "v1_aslr_defect_evidence": {
        "container_evidence": "stage2b_supplemental_seal",
        "filename": "prior_v1_aslr_defect.json",
        "role": "v1_nondeterministic_aslr_defect_evidence",
        "serialization": "canonical_json",
        "sha256":
            "054aef3c6fe36eed8291a88952c38c07fd03acfa5c084e400377aca499290ace",
        "verification_status": "independently_verified",
    },
}


class ManifestError(ValueError):
    pass


class DuplicateCaseError(ManifestError):
    pass


class SeedCollisionError(ManifestError):
    pass


class IDCollisionError(ManifestError):
    pass


class OutputConflictError(ManifestError):
    pass


def _load_identity_reference():
    spec = importlib.util.spec_from_file_location(
        "b4_pe_i4a0_identity_reference", IDENTITY_REFERENCE_PATH
    )
    if spec is None or spec.loader is None:
        raise ManifestError("cannot load I4A-0 identity reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IDENTITY = _load_identity_reference()


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compact_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require(condition, message, error_type=ManifestError):
    if not condition:
        raise error_type(message)


def validate_relative_path(value, field_name="path"):
    _require(isinstance(value, str) and value, f"{field_name} must be a string")
    _require("\\" not in value, f"{field_name} contains a backslash")
    path = PurePosixPath(value)
    _require(not path.is_absolute(), f"{field_name} must be relative")
    _require(".." not in path.parts, f"{field_name} contains parent traversal")
    _require("." not in path.parts, f"{field_name} contains dot traversal")
    _require(str(path) == value and "//" not in value, f"{field_name} is not canonical")
    return value


def _validate_string_list(values, name):
    _require(
        isinstance(values, list)
        and values
        and all(isinstance(item, str) and item for item in values),
        f"{name} must be a non-empty string list",
    )
    _require(len(values) == len(set(values)), f"{name} contains duplicates")


def _canonical_pretty_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def load_candidate_v4(path=CANDIDATE_V4_PATH):
    try:
        raw = Path(path).read_bytes()
        candidate = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("candidate v4 is not readable JSON") from exc
    _require(
        raw == _canonical_pretty_json_bytes(candidate),
        "candidate v4 JSON is not canonical",
    )
    forbidden_autodl_root = b"/root/" + b"autodl-tmp/"
    _require(
        forbidden_autodl_root not in raw,
        "candidate v4 contains an absolute AutoDL path",
    )
    _require(
        re.search(rb"\(0x[0-9A-Fa-f]+\)", raw) is None,
        "candidate v4 contains a raw ASLR address",
    )
    required = {
        "candidate_code_commit",
        "candidate_code_tree",
        "candidate_name",
        "final_code_commit",
        "final_git_tag",
        "formal_runtime_binary_path",
        "formal_runtime_binary_sha256",
        "freeze_status",
        "governance",
        "governance_history",
        "reason",
        "runtime_binding_status",
        "runtime_closure",
        "schema_version",
        "scientific_identity",
        "supersedes",
    }
    _require(set(candidate) == required, "candidate v4 fields mismatch")
    _require(
        candidate["schema_version"] == 4
        and candidate["candidate_name"] == "B4-PE-freeze-candidate-v4"
        and candidate["freeze_status"] == "candidate"
        and candidate["runtime_binding_status"]
        == "candidate_bound_pilot_authorized",
        "candidate v4 pilot identity mismatch",
    )
    _require(
        candidate["candidate_code_commit"] == V4_CANDIDATE_CODE_COMMIT
        and candidate["candidate_code_tree"] == V4_CANDIDATE_CODE_TREE,
        "candidate v4 code identity mismatch",
    )
    _require(
        candidate["final_code_commit"] is None
        and candidate["final_git_tag"] is None
        and candidate["formal_runtime_binary_path"] is None
        and candidate["formal_runtime_binary_sha256"] is None,
        "candidate v4 final identity must remain unset",
    )
    _require(
        candidate["governance"] == V4_GOVERNANCE,
        "candidate v4 governance mismatch",
    )
    _require(
        candidate["governance_history"]
        == [
            {
                "algorithm_changes": False,
                "formal_authorized": False,
                "negative_control_authorized": False,
                "parameter_changes": False,
                "paper_result_authorized": False,
                "pre_authorization_candidate_sha256":
                    "e697e9c218a19abe621d82547e2e4e948857874e6fb8d223c49f3f92c600fabe",
                "rta_changes": False,
                "runtime_identity_changes": False,
                "scheduler_changes": False,
                "science_code_commit": V4_CANDIDATE_CODE_COMMIT,
                "task_generation_changes": False,
                "transition_type": "pilot_authorization",
            }
        ],
        "candidate v4 governance history mismatch",
    )
    _require(
        candidate["reason"]
        == (
            "P1 repair for rho-specific task_energy_factor materialization "
            "and execution-input identity closure"
        ),
        "candidate v4 reason mismatch",
    )
    _require(
        candidate["scientific_identity"]
        == {
            "algorithm_order_unchanged": True,
            "case_counts_unchanged": True,
            "identity_protocol_unchanged": True,
            "parameter_matrix_unchanged": True,
            "rho_specific_execution_taskset_path_added": True,
            "source_identity_unchanged": True,
            "taskset_identity_unchanged": True,
        },
        "candidate v4 scientific identity mismatch",
    )
    _require(
        candidate["supersedes"]
        == {
            "path":
                "experiments/b4_priority_energy/b4_pe_freeze_candidate_v3.json",
            "sha256": file_sha256(CANDIDATE_V3_PATH),
        },
        "candidate v4 supersedes identity mismatch",
    )
    closure = candidate["runtime_closure"]
    _require(
        set(closure)
        == {
            "artifacts",
            "deterministic_dependency_representation",
            "evidence",
            "schema_version",
        }
        and closure["schema_version"] == 2,
        "candidate v4 runtime closure identity mismatch",
    )
    _require(
        closure["artifacts"] == V4_RUNTIME_ARTIFACTS,
        "candidate v4 runtime artifact identity mismatch",
    )
    _require(
        closure["evidence"] == V4_RUNTIME_EVIDENCE,
        "candidate v4 runtime evidence identity mismatch",
    )
    _require(
        closure["deterministic_dependency_representation"]
        == {
            "independent_normalizations_byte_identical": True,
            "pilot_authorization_effect": "none",
            "raw_aslr_addresses_stored": False,
            "raw_ldd_lines_stored": False,
            "supersedes_v1_nondeterministic_representation": True,
        },
        "candidate v4 deterministic dependency contract mismatch",
    )
    for name, entry in closure["artifacts"].items():
        validate_relative_path(
            entry["logical_path"],
            f"candidate runtime artifact {name}",
        )
    evidence = closure["evidence"]
    _require(
        {
            name: entry["schema_version"]
            for name, entry in evidence.items()
            if "schema_version" in entry
        }
        == {
            "stage2a_runtime_execution_closure": 1,
            "stage2b_supplemental_seal": 2,
        },
        "candidate v4 external evidence schema claims mismatch",
    )
    _require(
        {
            name: entry["serialization"]
            for name, entry in evidence.items()
            if "serialization" in entry
        }
        == {
            "normalized_dynamic_dependency_manifest": "canonical_json",
            "python310_tree_manifest": "canonical_jsonl",
            "python38_tree_manifest": "canonical_jsonl",
            "v1_aslr_defect_evidence": "canonical_json",
        },
        "candidate v4 external evidence serialization mismatch",
    )
    for name, entry in evidence.items():
        filename = validate_relative_path(
            entry["filename"],
            f"candidate runtime evidence filename {name}",
        )
        _require(
            PurePosixPath(filename).name == filename,
            f"candidate runtime evidence filename {name} is not one component",
        )
        if "supersedes" in entry:
            superseded = validate_relative_path(
                entry["supersedes"]["filename"],
                f"candidate superseded runtime evidence filename {name}",
            )
            _require(
                PurePosixPath(superseded).name == superseded,
                (
                    "candidate superseded runtime evidence filename "
                    f"{name} is not one component"
                ),
            )
    return candidate


def load_manifest_protocol(path=MANIFEST_PROTOCOL_PATH):
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    common = {
        "algorithm_cli_mapping",
        "base_commit",
        "execution_plan",
        "frozen_document_sha256",
        "identity_protocol_ref",
        "identity_protocol_sha256",
        "manifest_case_fields",
        "phase_matrix",
        "protocol_name",
        "schema_version",
        "system_template_sha256",
    }
    v2_fields = {
        "candidate_v1_ref",
        "candidate_v1_sha256",
        "observability_activation",
        "observability_contract_ref",
        "observability_contract_sha256",
        "observability_summary_contract_version",
        "result_audit_policy",
        "trace_schema_version",
    }
    v3_fields = {
        "candidate_v2_ref", "candidate_v2_sha256",
        "analysis_contract_ref", "analysis_contract_sha256",
        "observability_activation", "observability_contract_ref",
        "observability_contract_sha256",
        "observability_summary_contract_version", "result_audit_policy",
        "trace_schema_version", "minimum_adjudicable_jobs_per_task",
        "mechanism_fields", "jmr_denominator_contract",
    }
    v4_fields = {
        "candidate_v4_ref", "candidate_v4_sha256",
        "analysis_contract_ref", "analysis_contract_sha256",
        "observability_activation", "observability_contract_ref",
        "observability_contract_sha256",
        "observability_summary_contract_version", "result_audit_policy",
        "trace_schema_version", "minimum_adjudicable_jobs_per_task",
        "mechanism_fields", "jmr_denominator_contract",
        "governance", "status", "supersedes",
    }
    schema_version = protocol.get("schema_version")
    required = common | (
        v2_fields if schema_version == 2
        else v3_fields if schema_version == 3
        else v4_fields if schema_version == 4 else set()
    )
    _require(required == set(protocol), "manifest protocol fields mismatch")
    _require(type(protocol["schema_version"]) is int, "invalid protocol schema")
    _require(protocol["schema_version"] > 0, "invalid protocol schema")
    _require(
        protocol["identity_protocol_ref"] == IDENTITY_PROTOCOL_PATH.name,
        "identity protocol reference mismatch",
    )
    _require(
        protocol["identity_protocol_sha256"] == file_sha256(IDENTITY_PROTOCOL_PATH),
        "identity protocol SHA mismatch",
    )
    _require(
        protocol["frozen_document_sha256"] == file_sha256(FROZEN_DOCUMENT_PATH),
        "frozen document SHA mismatch",
    )
    _require(
        protocol["system_template_sha256"] == file_sha256(SYSTEM_TEMPLATE_PATH),
        "system template SHA mismatch",
    )
    if schema_version == 2:
        _require(
            protocol["candidate_v1_ref"] == CANDIDATE_V1_PATH.name
            and protocol["candidate_v1_sha256"]
            == file_sha256(CANDIDATE_V1_PATH),
            "candidate v1 identity mismatch",
        )
        _require(
            protocol["observability_contract_ref"]
            == OBSERVABILITY_CONTRACT_PATH.name
            and protocol["observability_contract_sha256"]
            == file_sha256(OBSERVABILITY_CONTRACT_PATH),
            "observability contract identity mismatch",
        )
        _require(
            protocol["trace_schema_version"] == 3
            and protocol["observability_summary_contract_version"] == 1
            and protocol["result_audit_policy"]
            == "strict_schema3_observability_v1",
            "schema3 result audit identity mismatch",
        )
        _require(
            protocol["observability_activation"]
            == {
                "summary_flag": "--b4-observability-summary",
                "horizon_option": "--b4-summary-horizon",
                "horizon_ms": protocol["execution_plan"]["horizon_ms"],
            },
            "schema3 activation mismatch",
        )
    if schema_version == 3:
        _require(
            protocol["candidate_v2_ref"] == CANDIDATE_V2_PATH.name
            and protocol["candidate_v2_sha256"] == file_sha256(CANDIDATE_V2_PATH),
            "candidate v2 identity mismatch",
        )
        _require(
            protocol["observability_contract_ref"]
            == OBSERVABILITY_CONTRACT_V2_PATH.name
            and protocol["observability_contract_sha256"]
            == file_sha256(OBSERVABILITY_CONTRACT_V2_PATH),
            "observability contract v2 identity mismatch",
        )
        _require(
            protocol["analysis_contract_ref"] == ANALYSIS_CONTRACT_V2_PATH.name
            and protocol["analysis_contract_sha256"]
            == file_sha256(ANALYSIS_CONTRACT_V2_PATH),
            "analysis contract v2 identity mismatch",
        )
        activation = protocol["observability_activation"]
        _require(
            protocol["trace_schema_version"] == 3
            and protocol["observability_summary_contract_version"] == 2
            and protocol["result_audit_policy"]
            == "strict_schema3_observability_v2"
            and protocol["minimum_adjudicable_jobs_per_task"] == 100
            and activation == {
                "summary_flag": "--b4-observability-summary",
                "horizon_option": "--b4-summary-horizon",
                "horizon_ms": protocol["execution_plan"]["horizon_ms"],
                "contract_version_option":
                    "--b4-observability-contract-version",
                "contract_version": 2,
            }
            and len(protocol["mechanism_fields"]) == 13
            and protocol["jmr_denominator_contract"]["zero_denominator"] == "NA",
            "schema3 v2 activation or denominator binding mismatch",
        )
    if schema_version == 4:
        _require(
            protocol["candidate_v4_ref"] == CANDIDATE_V4_PATH.name
            and protocol["candidate_v4_sha256"] == file_sha256(CANDIDATE_V4_PATH)
            and load_candidate_v4() == CANDIDATE_V4,
            "candidate v4 identity mismatch",
        )
        _require(
            protocol["observability_contract_ref"]
            == OBSERVABILITY_CONTRACT_V2_PATH.name
            and protocol["observability_contract_sha256"]
            == file_sha256(OBSERVABILITY_CONTRACT_V2_PATH),
            "observability contract v2 identity mismatch",
        )
        _require(
            protocol["analysis_contract_ref"] == ANALYSIS_CONTRACT_V2_PATH.name
            and protocol["analysis_contract_sha256"]
            == file_sha256(ANALYSIS_CONTRACT_V2_PATH),
            "analysis contract v2 identity mismatch",
        )
        _require(
            protocol["supersedes"]
            == {
                "path":
                    "experiments/b4_priority_energy/manifest_protocol_v3.json",
                "sha256": file_sha256(MANIFEST_PROTOCOL_V3_PATH),
            },
            "manifest v4 supersedes identity mismatch",
        )
        _require(
            protocol["protocol_name"] == "B4-PE-I5B-manifest-v4"
            and protocol["status"] == "pilot_authorized"
            and protocol["governance"]
            == {
                "formal_runs_authorized": False,
                "negative_control_runs_authorized": False,
                "paper_result_authorized": False,
                "pilot_runs_authorized": True,
            },
            "manifest v4 pilot governance mismatch",
        )
        activation = protocol["observability_activation"]
        _require(
            protocol["trace_schema_version"] == 3
            and protocol["observability_summary_contract_version"] == 2
            and protocol["result_audit_policy"]
            == "strict_schema3_observability_v2"
            and protocol["minimum_adjudicable_jobs_per_task"] == 100
            and activation == {
                "summary_flag": "--b4-observability-summary",
                "horizon_option": "--b4-summary-horizon",
                "horizon_ms": protocol["execution_plan"]["horizon_ms"],
                "contract_version_option":
                    "--b4-observability-contract-version",
                "contract_version": 2,
            }
            and len(protocol["mechanism_fields"]) == 13
            and protocol["jmr_denominator_contract"]["zero_denominator"] == "NA",
            "manifest v4 activation or denominator binding mismatch",
        )
    _validate_string_list(protocol["manifest_case_fields"], "manifest fields")

    identity = IDENTITY.RESOLUTION
    phases = identity["phase_algorithms"]
    _require(set(protocol["phase_matrix"]) == set(phases), "phase matrix mismatch")
    for phase, matrix in protocol["phase_matrix"].items():
        _require(
            set(matrix) == {"utilization", "lambda_E", "rho_E", "replicate_count"},
            f"{phase} matrix fields mismatch",
        )
        for name in ("utilization", "lambda_E", "rho_E"):
            _validate_string_list(matrix[name], f"{phase}.{name}")
        _require(
            type(matrix["replicate_count"]) is int
            and matrix["replicate_count"] > 0,
            f"{phase} replicate count invalid",
        )
        product = (
            len(matrix["utilization"])
            * len(matrix["lambda_E"])
            * len(matrix["rho_E"])
            * matrix["replicate_count"]
            * len(phases[phase])
        )
        _require(
            product == identity["phase_counts"][phase]["expected"],
            f"{phase} matrix count mismatch",
        )

    mapping = protocol["algorithm_cli_mapping"]
    _require(isinstance(mapping, dict), "algorithm CLI mapping invalid")
    algorithms = {name for names in phases.values() for name in names}
    _require(set(mapping) == algorithms, "algorithm CLI mapping incomplete")
    _require(
        all(isinstance(value, str) and value for value in mapping.values()),
        "algorithm CLI value invalid",
    )
    _require(len(mapping.values()) == len(set(mapping.values())), "algorithm CLI duplicate")

    plan = protocol["execution_plan"]
    _require(
        set(plan)
        == {
            "M",
            "horizon_ms",
            "path_templates",
            "retry_policy",
            "simulator_argv0",
            "system_template_relpath",
            "task_count",
            "timeout_seconds",
        },
        "execution plan fields mismatch",
    )
    for name in ("M", "task_count", "horizon_ms", "timeout_seconds"):
        _require(type(plan[name]) is int and plan[name] > 0, f"{name} invalid")
    validate_relative_path(plan["simulator_argv0"], "simulator_argv0")
    validate_relative_path(plan["system_template_relpath"], "system template path")
    path_roles = {"taskset", "source", "system_config", "result"}
    if schema_version == 4:
        path_roles.update({
            "base_pool_admission_inventory",
            "base_taskset",
            "materialization_inventory",
        })
    for name, template in plan["path_templates"].items():
        _require(name in path_roles, "path role")
        probe = template.format(
            algorithm_cli="gpfp_asap_block",
            phase="pilot",
            case_id="case-probe",
            taskset_id="ts-probe",
            source_id="src-probe",
            rho_E="2",
        )
        validate_relative_path(probe, f"{name} template")
    retry = plan["retry_policy"]
    _require(
        set(retry)
        == {
            "initial_timeout_seconds",
            "max_attempts",
            "on_final_failure",
            "retry_on",
            "retry_timeout_seconds",
        },
        "retry policy fields mismatch",
    )
    _require(retry["initial_timeout_seconds"] == plan["timeout_seconds"], "retry timeout")
    _require(type(retry["max_attempts"]) is int and retry["max_attempts"] == 2, "retry count")
    _require(retry["retry_on"] == ["timeout"], "retry trigger")
    _require(retry["on_final_failure"] == "fail_closed", "retry final policy")
    _require(
        type(retry["retry_timeout_seconds"]) is int
        and retry["retry_timeout_seconds"] > retry["initial_timeout_seconds"],
        "retry timeout invalid",
    )
    return protocol


CANDIDATE_V4 = load_candidate_v4()
PROTOCOL_V1 = load_manifest_protocol(MANIFEST_PROTOCOL_V1_PATH)
PROTOCOL = load_manifest_protocol()
PROTOCOL_V2 = PROTOCOL
PROTOCOL_V3 = load_manifest_protocol(MANIFEST_PROTOCOL_V3_PATH)
PROTOCOL_V4 = load_manifest_protocol(MANIFEST_PROTOCOL_V4_PATH)
PROTOCOLS_BY_SCHEMA = {
    PROTOCOL_V1["schema_version"]: PROTOCOL_V1,
    PROTOCOL_V2["schema_version"]: PROTOCOL_V2,
    PROTOCOL_V3["schema_version"]: PROTOCOL_V3,
    PROTOCOL_V4["schema_version"]: PROTOCOL_V4,
}


def selected_phases(phase, protocol=PROTOCOL):
    if phase == "all":
        return ("pilot", "formal_main", "negative_control")
    _require(phase in protocol["phase_matrix"], f"unknown phase: {phase}")
    return (phase,)


def expected_phase_count(phase):
    return IDENTITY.RESOLUTION["phase_counts"][phase]["expected"]


def _identity_parts(phase, utilization, replicate_index, lambda_E, rho_E, algorithm):
    contract = IDENTITY.RESOLUTION
    record = IDENTITY.semantic_record(
        contract,
        phase=phase,
        utilization=utilization,
        replicate_index=replicate_index,
        lambda_E=lambda_E,
        rho_E=rho_E,
        algorithm=algorithm,
    )
    taskset_key = IDENTITY.key_from_record(contract, "taskset_key", record)
    taskset_seed = IDENTITY.derive_seed(contract, "taskset", taskset_key)
    taskset_id = IDENTITY.derive_id(contract, "taskset", taskset_key)
    with_taskset = dict(record, taskset_id=taskset_id)
    source_key = IDENTITY.key_from_record(contract, "source_key", with_taskset)
    source_seed = IDENTITY.derive_source_seed(source_key, contract=contract)
    source_id = IDENTITY.derive_id(contract, "source", source_key)
    with_source = dict(with_taskset, source_id=source_id)
    case_key = IDENTITY.key_from_record(contract, "case_key", with_source)
    case_id = IDENTITY.derive_id(contract, "case", case_key)
    return {
        "record": record,
        "taskset_key": taskset_key,
        "taskset_seed": taskset_seed,
        "taskset_id": taskset_id,
        "source_key": source_key,
        "source_seed": source_seed,
        "source_id": source_id,
        "case_key": case_key,
        "case_id": case_id,
    }


def build_case(
    phase,
    utilization,
    replicate_index,
    lambda_E,
    rho_E,
    algorithm,
    protocol=PROTOCOL,
):
    plan = protocol["execution_plan"]
    identity_contract = IDENTITY.RESOLUTION
    parts = _identity_parts(
        phase, utilization, replicate_index, lambda_E, rho_E, algorithm
    )
    source = identity_contract["source_contract"]
    pool = identity_contract["reuse_dimensions"]["phase_taskset_pool"][phase]
    template_values = {
        "algorithm_cli": protocol["algorithm_cli_mapping"][algorithm],
        "phase": phase,
        "case_id": parts["case_id"],
        "taskset_id": parts["taskset_id"],
        "source_id": parts["source_id"],
        "rho_E": rho_E,
    }
    paths = {
        name: template.format(**template_values)
        for name, template in plan["path_templates"].items()
    }
    for name, value in paths.items():
        validate_relative_path(value, f"{name} artifact path")
    command = [
        plan["simulator_argv0"],
        paths["system_config"],
        paths["taskset"],
        str(plan["horizon_ms"]),
        "-t",
        paths["result"],
        "--run-id",
        parts["case_id"],
    ]
    if protocol["schema_version"] in (2, 3, 4):
        activation = protocol["observability_activation"]
        command.extend(
            [
                activation["summary_flag"],
                activation["horizon_option"],
                str(activation["horizon_ms"]),
            ]
        )
        if protocol["schema_version"] in (3, 4):
            command.extend(
                [
                    activation["contract_version_option"],
                    str(activation["contract_version"]),
                ]
            )
        if protocol["schema_version"] == 4:
            command.extend(
                [
                    "--taskset-semantic-hash",
                    SEMANTIC_HASH_PLACEHOLDER,
                ]
            )
    case = {
        "schema_version": protocol["schema_version"],
        "protocol_name": protocol["protocol_name"],
        "phase": phase,
        "case_id": parts["case_id"],
        "taskset_id": parts["taskset_id"],
        "taskset_seed": parts["taskset_seed"],
        "source_id": parts["source_id"],
        "source_seed": parts["source_seed"],
        "taskset_pool": pool,
        "replicate_index": replicate_index,
        "algorithm": algorithm,
        "algorithm_cli": protocol["algorithm_cli_mapping"][algorithm],
        "utilization": utilization,
        "lambda_E": lambda_E,
        "rho_E": rho_E,
        "M": plan["M"],
        "task_count": plan["task_count"],
        "horizon_ms": plan["horizon_ms"],
        "source_profile": source["source_profile"],
        "E0_rule": source["E0_rule"],
        "Emax_rule": source["Emax_rule"],
        "alpha_rule": source["alpha_rule"],
        "frozen_document_sha256": protocol["frozen_document_sha256"],
        "system_template_sha256": protocol["system_template_sha256"],
        "identity_protocol_sha256": protocol["identity_protocol_sha256"],
        "base_commit": protocol["base_commit"],
        "taskset_artifact_relpath": paths["taskset"],
        "source_artifact_relpath": paths["source"],
        "system_config_artifact_relpath": paths["system_config"],
        "result_relpath": paths["result"],
        "timeout_seconds": plan["timeout_seconds"],
        "retry_policy": plan["retry_policy"],
        "command_argv": command,
    }
    if protocol["schema_version"] in (2, 3, 4):
        candidate_version = (
            1 if protocol["schema_version"] == 2
            else 2 if protocol["schema_version"] == 3 else 4
        )
        case.update(
            {
                f"candidate_v{candidate_version}_ref":
                    protocol[f"candidate_v{candidate_version}_ref"],
                f"candidate_v{candidate_version}_sha256":
                    protocol[f"candidate_v{candidate_version}_sha256"],
                "observability_contract_ref":
                    protocol["observability_contract_ref"],
                "observability_contract_sha256":
                    protocol["observability_contract_sha256"],
                "trace_schema_version":
                    protocol["trace_schema_version"],
                "observability_summary_contract_version":
                    protocol["observability_summary_contract_version"],
                "summary_horizon_ms":
                    protocol["observability_activation"]["horizon_ms"],
                "result_audit_policy":
                    protocol["result_audit_policy"],
            }
        )
        if protocol["schema_version"] in (3, 4):
            case.update(
                {
                    "analysis_contract_ref": protocol["analysis_contract_ref"],
                    "analysis_contract_sha256": protocol["analysis_contract_sha256"],
                    "minimum_adjudicable_jobs_per_task":
                        protocol["minimum_adjudicable_jobs_per_task"],
                    "mechanism_fields": protocol["mechanism_fields"],
                    "jmr_denominator_contract":
                        protocol["jmr_denominator_contract"],
                }
            )
    if protocol["schema_version"] == 4:
        case.update(
            {
                "base_pool_admission_inventory_relpath":
                    paths["base_pool_admission_inventory"],
                "base_taskset_artifact_relpath": paths["base_taskset"],
                "materialization_inventory_relpath":
                    paths["materialization_inventory"],
                "manifest_protocol_sha256":
                    file_sha256(MANIFEST_PROTOCOL_V4_PATH),
            }
        )
    _require(set(case) == set(protocol["manifest_case_fields"]), "case shape mismatch")
    return case


def iter_cases(phase="all", protocol=PROTOCOL):
    identity_contract = IDENTITY.RESOLUTION
    for current_phase in selected_phases(phase, protocol):
        matrix = protocol["phase_matrix"][current_phase]
        algorithms = identity_contract["phase_algorithms"][current_phase]
        for utilization in matrix["utilization"]:
            for lambda_E in matrix["lambda_E"]:
                for rho_E in matrix["rho_E"]:
                    for replicate_index in range(1, matrix["replicate_count"] + 1):
                        for algorithm in algorithms:
                            yield build_case(
                                current_phase,
                                utilization,
                                replicate_index,
                                lambda_E,
                                rho_E,
                                algorithm,
                                protocol,
                            )


def render_manifest(phase="all", protocol=PROTOCOL):
    return b"".join(
        compact_json(case).encode("utf-8") + b"\n"
        for case in iter_cases(phase, protocol)
    )


def parse_manifest(path):
    records = []
    try:
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, 1):
                _require(line.endswith("\n"), f"line {line_number} lacks newline")
                _require(line != "\n", f"line {line_number} is empty")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ManifestError(f"invalid JSONL at line {line_number}: {exc}") from exc
                _require(isinstance(record, dict), f"line {line_number} is not an object")
                records.append(record)
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest is not UTF-8") from exc
    _require(records, "manifest is empty")
    return records


def _record_identity_keys(record):
    contract = IDENTITY.RESOLUTION
    semantic = IDENTITY.semantic_record(
        contract,
        phase=record["phase"],
        utilization=record["utilization"],
        replicate_index=record["replicate_index"],
        lambda_E=record["lambda_E"],
        rho_E=record["rho_E"],
        algorithm=record["algorithm"],
    )
    taskset_key = IDENTITY.key_from_record(contract, "taskset_key", semantic)
    with_taskset = dict(semantic, taskset_id=record["taskset_id"])
    source_key = IDENTITY.key_from_record(contract, "source_key", with_taskset)
    with_source = dict(with_taskset, source_id=record["source_id"])
    case_key = IDENTITY.key_from_record(contract, "case_key", with_source)
    return (
        IDENTITY.canonical_json(contract, "taskset_key", taskset_key),
        IDENTITY.canonical_json(contract, "source_key", source_key),
        IDENTITY.canonical_json(contract, "case_key", case_key),
    )


def _protocol_for_record(record, line_number):
    schema_version = record.get("schema_version")
    _require(
        type(schema_version) is int
        and schema_version in PROTOCOLS_BY_SCHEMA,
        f"line {line_number} unknown manifest schema",
    )
    protocol = PROTOCOLS_BY_SCHEMA[schema_version]
    _require(
        record.get("protocol_name") == protocol["protocol_name"],
        f"line {line_number} protocol name mismatch",
    )
    return protocol


def _validate_record_structure(record, line_number):
    protocol = _protocol_for_record(record, line_number)
    fields = set(protocol["manifest_case_fields"])
    _require(set(record) == fields, f"line {line_number} fields mismatch")
    string_fields = fields - {
        "schema_version",
        "taskset_seed",
        "source_seed",
        "replicate_index",
        "M",
        "task_count",
        "horizon_ms",
        "timeout_seconds",
        "retry_policy",
        "command_argv",
        "trace_schema_version",
        "observability_summary_contract_version",
        "summary_horizon_ms",
        "minimum_adjudicable_jobs_per_task",
        "mechanism_fields",
        "jmr_denominator_contract",
    }
    _require(
        all(isinstance(record[name], str) and record[name] for name in string_fields),
        f"line {line_number} string field invalid",
    )
    for name in (
        "schema_version",
        "taskset_seed",
        "replicate_index",
        "M",
        "task_count",
        "horizon_ms",
        "timeout_seconds",
    ):
        _require(type(record[name]) is int, f"line {line_number} {name} type")
    for name in (
        "trace_schema_version",
        "observability_summary_contract_version",
        "summary_horizon_ms",
        "minimum_adjudicable_jobs_per_task",
    ):
        if name in record:
            _require(
                type(record[name]) is int,
                f"line {line_number} {name} type",
            )
    _require(record["source_seed"] is None, f"line {line_number} source seed")
    _require(isinstance(record["retry_policy"], dict), f"line {line_number} retry")
    _require(
        isinstance(record["command_argv"], list)
        and record["command_argv"]
        and all(isinstance(item, str) for item in record["command_argv"]),
        f"line {line_number} command_argv must be a string array",
    )
    _require(
        not any(item in {"sh", "bash", "-c", "shell=True"} for item in record["command_argv"]),
        f"line {line_number} shell execution is forbidden",
    )
    for name in (
        "base_pool_admission_inventory_relpath",
        "base_taskset_artifact_relpath",
        "taskset_artifact_relpath",
        "source_artifact_relpath",
        "system_config_artifact_relpath",
        "materialization_inventory_relpath",
        "result_relpath",
    ):
        if name in record:
            validate_relative_path(record[name], name)
    return protocol


def validate_records(records):
    _require(isinstance(records, list) and records, "manifest records invalid")
    seen_case_keys = set()
    seed_owners = {}
    id_owners = {"taskset": {}, "source": {}, "case": {}}
    output_owners = {}
    phase_units = defaultdict(lambda: defaultdict(set))
    taskset_by_semantic = {}
    source_by_semantic = {}
    manifest_protocol = None

    for line_number, record in enumerate(records, 1):
        protocol = _validate_record_structure(record, line_number)
        if manifest_protocol is None:
            manifest_protocol = protocol
        _require(
            protocol is manifest_protocol,
            "manifest mixes protocol versions",
        )
        phase = record["phase"]
        _require(phase in protocol["phase_matrix"], f"line {line_number} unknown phase")
        matrix = protocol["phase_matrix"][phase]
        _require(record["algorithm"] in IDENTITY.RESOLUTION["phase_algorithms"][phase], "unknown algorithm")
        _require(record["utilization"] in matrix["utilization"], "utilization outside matrix")
        _require(record["lambda_E"] in matrix["lambda_E"], "lambda_E outside matrix")
        _require(record["rho_E"] in matrix["rho_E"], "rho_E outside matrix")
        _require(
            1 <= record["replicate_index"] <= matrix["replicate_count"],
            "replicate outside matrix",
        )
        task_key, source_key, case_key = _record_identity_keys(record)
        if case_key in seen_case_keys:
            raise DuplicateCaseError("duplicate canonical case key")
        seen_case_keys.add(case_key)

        seed = record["taskset_seed"]
        if seed in seed_owners and seed_owners[seed] != task_key:
            raise SeedCollisionError("taskset seed collision")
        seed_owners[seed] = task_key
        for kind, key, value in (
            ("taskset", task_key, record["taskset_id"]),
            ("source", source_key, record["source_id"]),
            ("case", case_key, record["case_id"]),
        ):
            owners = id_owners[kind]
            if value in owners and owners[value] != key:
                raise IDCollisionError(f"{kind} ID collision")
            owners[value] = key
        result_path = record["result_relpath"]
        if result_path in output_owners and output_owners[result_path] != case_key:
            raise OutputConflictError("output path conflict")
        output_owners[result_path] = case_key

        expected = build_case(
            phase,
            record["utilization"],
            record["replicate_index"],
            record["lambda_E"],
            record["rho_E"],
            record["algorithm"],
            protocol,
        )
        for field in protocol["manifest_case_fields"]:
            _require(record[field] == expected[field], f"line {line_number} {field} mismatch")

        unit = (
            record["utilization"],
            record["lambda_E"],
            record["rho_E"],
            record["replicate_index"],
        )
        phase_units[phase][unit].add(record["algorithm"])
        task_semantic = (record["taskset_pool"], record["utilization"], record["replicate_index"])
        task_value = (record["taskset_id"], record["taskset_seed"])
        if task_semantic in taskset_by_semantic:
            _require(taskset_by_semantic[task_semantic] == task_value, "taskset identity coupled")
        taskset_by_semantic[task_semantic] = task_value
        source_semantic = (record["taskset_id"], record["lambda_E"])
        source_value = (record["source_id"], record["source_seed"])
        if source_semantic in source_by_semantic:
            _require(source_by_semantic[source_semantic] == source_value, "source identity coupled")
        source_by_semantic[source_semantic] = source_value

    by_phase = Counter(record["phase"] for record in records)
    for phase, count in by_phase.items():
        _require(count == expected_phase_count(phase), f"{phase} case count mismatch")
        expected_algorithms = set(IDENTITY.RESOLUTION["phase_algorithms"][phase])
        expected_units = expected_phase_count(phase) // len(expected_algorithms)
        _require(len(phase_units[phase]) == expected_units, f"{phase} basic unit count")
        for algorithms in phase_units[phase].values():
            _require(algorithms == expected_algorithms, f"{phase} algorithm coverage mismatch")

    phases = set(by_phase)
    if {"formal_main", "negative_control"} <= phases:
        formal_tasks = {
            (r["utilization"], r["replicate_index"]): (r["taskset_id"], r["taskset_seed"])
            for r in records
            if r["phase"] == "formal_main"
        }
        formal_sources = {
            (r["utilization"], r["replicate_index"], r["lambda_E"]): r["source_id"]
            for r in records
            if r["phase"] == "formal_main"
        }
        for record in records:
            if record["phase"] != "negative_control":
                continue
            task_key = (record["utilization"], record["replicate_index"])
            source_key = task_key + (record["lambda_E"],)
            _require(
                formal_tasks.get(task_key)
                == (record["taskset_id"], record["taskset_seed"]),
                "Formal/Negative taskset reuse mismatch",
            )
            _require(
                formal_sources.get(source_key) == record["source_id"],
                "Formal/Negative source reuse mismatch",
            )
    return records


def validate_manifest(path):
    return validate_records(parse_manifest(path))


def audit_records(records):
    algorithms_by_phase = IDENTITY.RESOLUTION["phase_algorithms"]
    case_ids = [record.get("case_id") for record in records]
    outputs = [record.get("result_relpath") for record in records]
    phase_units = defaultdict(lambda: defaultdict(set))
    for record in records:
        phase = record.get("phase")
        unit = (
            record.get("utilization"),
            record.get("lambda_E"),
            record.get("rho_E"),
            record.get("replicate_index"),
        )
        phase_units[phase][unit].add(record.get("algorithm"))
    missing_algorithms = 0
    complete_units = 0
    for phase, units in phase_units.items():
        expected = set(algorithms_by_phase.get(phase, ()))
        for observed in units.values():
            missing_algorithms += len(expected - observed)
            complete_units += observed == expected

    task_counts = Counter(record.get("taskset_id") for record in records)
    source_counts = Counter(record.get("source_id") for record in records)
    formal_tasks = {
        record.get("taskset_id") for record in records if record.get("phase") == "formal_main"
    }
    negative_tasks = {
        record.get("taskset_id") for record in records if record.get("phase") == "negative_control"
    }
    formal_sources = {
        record.get("source_id") for record in records if record.get("phase") == "formal_main"
    }
    negative_sources = {
        record.get("source_id") for record in records if record.get("phase") == "negative_control"
    }
    protocol = _protocol_for_record(records[0], 1)
    sha_status = {
        "frozen_document": all(
            record.get("frozen_document_sha256") == protocol["frozen_document_sha256"]
            for record in records
        ),
        "identity_protocol": all(
            record.get("identity_protocol_sha256") == protocol["identity_protocol_sha256"]
            for record in records
        ),
        "system_template": all(
            record.get("system_template_sha256") == protocol["system_template_sha256"]
            for record in records
        ),
    }
    phase_names = sorted({record.get("phase") for record in records})
    summary = {
        "phase": phase_names[0] if len(phase_names) == 1 else "all",
        "case_count": len(records),
        "unique_taskset_count": len(task_counts),
        "unique_source_count": len(source_counts),
        "case_count_by_phase": dict(sorted(Counter(r.get("phase") for r in records).items())),
        "case_count_by_algorithm": dict(sorted(Counter(r.get("algorithm") for r in records).items())),
        "case_count_by_utilization": dict(sorted(Counter(r.get("utilization") for r in records).items())),
        "case_count_by_lambda_E": dict(sorted(Counter(r.get("lambda_E") for r in records).items())),
        "case_count_by_rho_E": dict(sorted(Counter(r.get("rho_E") for r in records).items())),
        "basic_unit_count": sum(len(units) for units in phase_units.values()),
        "complete_basic_unit_count": complete_units,
        "taskset_reuse_multiplicity": dict(sorted(Counter(task_counts.values()).items())),
        "source_reuse_multiplicity": dict(sorted(Counter(source_counts.values()).items())),
        "formal_negative_taskset_reuse_count": len(formal_tasks & negative_tasks),
        "formal_negative_source_reuse_count": len(formal_sources & negative_sources),
        "duplicate_count": len(case_ids) - len(set(case_ids)),
        "missing_algorithm_count": missing_algorithms,
        "output_path_conflict_count": len(outputs) - len(set(outputs)),
        "protocol_sha_status": sha_status,
    }
    return summary
