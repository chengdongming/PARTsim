#!/usr/bin/env python3
"""Shared, fail-closed support for the B4-PE I4A manifest layer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
MANIFEST_PROTOCOL_V1_PATH = B4_DIR / "manifest_protocol_v1.json"
MANIFEST_PROTOCOL_PATH = B4_DIR / "manifest_protocol_v2.json"
IDENTITY_PROTOCOL_PATH = B4_DIR / "protocol_resolution_v1.json"
OBSERVABILITY_CONTRACT_PATH = (
    B4_DIR / "observability_summary_contract_v1.json"
)
CANDIDATE_V1_PATH = B4_DIR / "b4_pe_freeze_candidate_v1.json"
IDENTITY_REFERENCE_PATH = B4_DIR / "tests" / "test_protocol_resolution.py"
FROZEN_DOCUMENT_PATH = (
    REPO_ROOT / "docs" / "experiments" /
    "ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md"
)
SYSTEM_TEMPLATE_PATH = REPO_ROOT / "v9_3_b4_priority_energy_system_template.yml"


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
    schema_version = protocol.get("schema_version")
    required = common | (v2_fields if schema_version == 2 else set())
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
    for name, template in plan["path_templates"].items():
        _require(name in {"taskset", "source", "system_config", "result"}, "path role")
        probe = template.format(
            algorithm_cli="gpfp_asap_block",
            phase="pilot",
            case_id="case-probe",
            taskset_id="ts-probe",
            source_id="src-probe",
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


PROTOCOL_V1 = load_manifest_protocol(MANIFEST_PROTOCOL_V1_PATH)
PROTOCOL = load_manifest_protocol()
PROTOCOLS_BY_SCHEMA = {
    PROTOCOL_V1["schema_version"]: PROTOCOL_V1,
    PROTOCOL["schema_version"]: PROTOCOL,
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
    if protocol["schema_version"] == 2:
        activation = protocol["observability_activation"]
        command.extend(
            [
                activation["summary_flag"],
                activation["horizon_option"],
                str(activation["horizon_ms"]),
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
    if protocol["schema_version"] == 2:
        case.update(
            {
                "candidate_v1_ref": protocol["candidate_v1_ref"],
                "candidate_v1_sha256": protocol["candidate_v1_sha256"],
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
        "taskset_artifact_relpath",
        "source_artifact_relpath",
        "system_config_artifact_relpath",
        "result_relpath",
    ):
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
