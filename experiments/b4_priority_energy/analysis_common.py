#!/usr/bin/env python3
"""Deterministic, fail-closed B4-PE analysis extraction support."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

import yaml


sys.dont_write_bytecode = True

B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
CONTRACT_PATH = B4_DIR / "analysis_contract_v1.json"
OBSERVABILITY_CONTRACT_PATH = (
    B4_DIR / "observability_summary_contract_v1.json"
)
CANDIDATE_V2_PATH = B4_DIR / "b4_pe_freeze_candidate_v2.json"
EXTRACTOR_PATH = B4_DIR / "extract_analysis.py"

import integration_smoke_common as smoke
import manifest_common as manifest
import observability_validation as observability


class AnalysisError(ValueError):
    """An input or output violates the frozen analysis contract."""


CONTRACT_TOP_LEVEL_FIELDS = (
    "contract_name",
    "contract_version",
    "analysis_schema_version",
    "accepted_trace_schemas",
    "authoritative_outputs",
    "convenience_outputs",
    "metadata_outputs",
    "case_primary_key",
    "task_primary_key",
    "case_identity_field_order",
    "case_field_order",
    "task_field_order",
    "mechanism_field_order",
    "energy_field_order",
    "task_metric_field_order",
    "pairing_contract",
    "pass_contract",
    "ordering_contract",
    "numeric_contract",
    "determinism_contract",
    "audit_contract",
    "governance",
)


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                AnalysisError(f"non-finite JSON number: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError("invalid JSON input") from exc


CONTRACT = _load_json(CONTRACT_PATH)
ANALYSIS_SCHEMA_VERSION = CONTRACT["analysis_schema_version"]
CASE_IDENTITY_FIELDS = tuple(CONTRACT["case_identity_field_order"])
CASE_FIELDS = tuple(CONTRACT["case_field_order"])
TASK_FIELDS = tuple(CONTRACT["task_field_order"])
MECHANISM_FIELDS = tuple(CONTRACT["mechanism_field_order"])
ENERGY_FIELDS = tuple(CONTRACT["energy_field_order"])
TASK_METRIC_FIELDS = tuple(CONTRACT["task_metric_field_order"])
PAIRING_FIELDS = tuple(
    CONTRACT["pairing_contract"]["pairing_dimension_order"]
)
PHASE_ORDER = tuple(
    CONTRACT["ordering_contract"]["phase_canonical_order"]
)
ALGORITHMS = tuple(
    CONTRACT["ordering_contract"]["algorithm_canonical_order"]
)
ALGORITHM_INDEX = {name: index for index, name in enumerate(ALGORITHMS)}
FORMAL_PHASES = frozenset(
    CONTRACT["accepted_trace_schemas"]["formal_phases"]["phases"]
)
OUTPUT_NAMES = tuple(
    CONTRACT["authoritative_outputs"]
    + CONTRACT["convenience_outputs"]
    + CONTRACT["metadata_outputs"]
)


def _require(condition, message):
    if not condition:
        raise AnalysisError(message)


def _validate_contract():
    _require(
        tuple(CONTRACT) == CONTRACT_TOP_LEVEL_FIELDS,
        "analysis contract top-level fields mismatch",
    )
    _require(
        CONTRACT["contract_version"] == 1
        and CONTRACT["analysis_schema_version"] == 1,
        "analysis contract version mismatch",
    )
    for label, fields in (
        ("case identity", CASE_IDENTITY_FIELDS),
        ("case", CASE_FIELDS),
        ("task", TASK_FIELDS),
        ("mechanism", MECHANISM_FIELDS),
        ("energy", ENERGY_FIELDS),
        ("task metric", TASK_METRIC_FIELDS),
        ("pairing", PAIRING_FIELDS),
    ):
        _require(fields and len(fields) == len(set(fields)), f"duplicate {label} field")
    _require(
        CASE_FIELDS[: len(CASE_IDENTITY_FIELDS)] == CASE_IDENTITY_FIELDS
        and TASK_FIELDS[: len(CASE_IDENTITY_FIELDS)] == CASE_IDENTITY_FIELDS,
        "case identity prefix mismatch",
    )
    frozen_algorithms = tuple(
        manifest.IDENTITY.RESOLUTION["phase_algorithms"]["formal_main"]
    )
    _require(ALGORITHMS == frozen_algorithms, "algorithm canonical order mismatch")
    _require(
        set(OUTPUT_NAMES)
        == {
            "cases.jsonl",
            "tasks.jsonl",
            "cases.csv",
            "tasks.csv",
            "analysis_manifest.json",
            "analysis_audit.json",
        },
        "analysis output set mismatch",
    )


_validate_contract()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(material):
    return hashlib.sha256(material).hexdigest()


def compact_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def pretty_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _is_within(path, parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False


def _absolute_regular_file(value, label):
    path = Path(value)
    _require(path.is_absolute(), f"{label} must be absolute")
    _require(not path.is_symlink(), f"{label} must not be a symlink")
    try:
        path = path.resolve(strict=True)
        metadata = path.stat()
    except OSError as exc:
        raise AnalysisError(f"{label} is unavailable") from exc
    _require(stat.S_ISREG(metadata.st_mode), f"{label} must be a file")
    return path


def validate_output_root(value):
    path = Path(value)
    _require(path.is_absolute(), "output-root must be absolute")
    _require(not path.is_symlink(), "output-root must not be a symlink")
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError("output-root is unavailable") from exc
    _require(path.is_dir(), "output-root must be a directory")
    return path


def validate_analysis_root(value, *, require_unused=True):
    raw = Path(value)
    _require(raw.is_absolute(), "analysis-root must be absolute")
    resolved = raw.resolve(strict=False)
    _require(
        not _is_within(resolved, REPO_ROOT),
        "analysis-root must be outside repository",
    )
    _require(resolved != Path("/"), "analysis-root cannot be filesystem root")
    _require(
        resolved.parent.exists() and resolved.parent.is_dir(),
        "analysis-root parent must exist",
    )
    if raw.exists() or raw.is_symlink():
        _require(not raw.is_symlink(), "analysis-root must not be a symlink")
        _require(raw.is_dir(), "analysis-root must be a directory")
        if require_unused:
            _require(
                not any(raw.iterdir()),
                "analysis-root must be absent or empty",
            )
    return resolved


def _safe_child(root, relative, label):
    _require(isinstance(relative, str) and relative, f"{label} is invalid")
    posix = PurePosixPath(relative)
    _require(
        not posix.is_absolute()
        and str(posix) == relative
        and all(part not in {"", ".", ".."} for part in posix.parts),
        f"{label} is not canonical relative",
    )
    unresolved = root / Path(*posix.parts)
    probe = unresolved
    while probe != root:
        _require(not probe.is_symlink(), f"{label} contains a symlink")
        probe = probe.parent
    candidate = unresolved.resolve(strict=True)
    _require(_is_within(candidate, root), f"{label} escapes output root")
    return candidate


def _validate_embedded_smoke_record(record):
    protocol = smoke.PROTOCOLS_BY_SCHEMA.get(record.get("schema_version"))
    _require(protocol is not None, "unknown smoke schema version")
    _require(
        set(record) == set(protocol["record_fields"]),
        "smoke record fields mismatch",
    )
    for field, expected in protocol["fixed_identity"].items():
        _require(record[field] == expected, f"smoke identity mismatch: {field}")
    _require(
        isinstance(record.get("case_id"), str)
        and smoke.CASE_ID_PATTERN.fullmatch(record["case_id"]),
        "invalid smoke case id",
    )
    _require(
        isinstance(record.get("algorithm"), str)
        and record["algorithm"] in ALGORITHMS,
        "invalid smoke algorithm",
    )
    try:
        smoke._validate_output_root(record["output_root"])
        smoke._validate_retry(record, protocol)
        smoke._validate_provenance(record, protocol)
        smoke._validate_command(record, protocol)
        for field in (
            "system_config_path",
            "taskset_path",
            "source_artifact_path",
            "result_relpath",
        ):
            smoke._validate_relative_path(record[field], field)
    except smoke.IntegrationSmokeError as exc:
        raise AnalysisError(str(exc)) from exc
    if protocol is smoke.PROTOCOL:
        for field in (
            "candidate_v1_ref",
            "candidate_v1_sha256",
            "observability_contract_ref",
            "observability_contract_sha256",
            "observability_summary_contract_version",
            "result_audit_policy",
            "trace_schema_version",
        ):
            _require(
                record[field] == protocol[field],
                f"schema3 smoke binding mismatch: {field}",
            )
    normalized = dict(record)
    normalized.update(
        {
            "taskset_artifact_relpath": record["taskset_path"],
            "source_artifact_relpath": record["source_artifact_path"],
            "system_config_artifact_relpath": record["system_config_path"],
        }
    )
    return normalized


def load_expected_records(path):
    path = _absolute_regular_file(path, "expected-records")
    material = path.read_bytes()
    try:
        text = material.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnalysisError("expected-records is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    _require(lines, "expected-records is empty")
    records = []
    for line_number, line in enumerate(lines, 1):
        _require(line.endswith("\n"), f"expected-record line {line_number} lacks LF")
        _require(line != "\n", f"expected-record line {line_number} is empty")
        try:
            record = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    AnalysisError(f"non-finite JSON number: {value}")
                ),
            )
        except json.JSONDecodeError as exc:
            raise AnalysisError(
                f"expected-record line {line_number} is invalid"
            ) from exc
        _require(isinstance(record, dict), "expected record must be an object")
        if record.get("record_type") == "integration_smoke":
            normalized = _validate_embedded_smoke_record(record)
            kind = "smoke"
        else:
            try:
                protocol = manifest._validate_record_structure(
                    record, line_number
                )
            except manifest.ManifestError as exc:
                raise AnalysisError(str(exc)) from exc
            _require(protocol is manifest.PROTOCOL, "formal record must use v2")
            normalized = record
            kind = "formal"
        records.append({"record": normalized, "kind": kind})
    case_ids = [entry["record"].get("case_id") for entry in records]
    _require(
        all(isinstance(value, str) and value for value in case_ids),
        "expected record has invalid case id",
    )
    _require(len(case_ids) == len(set(case_ids)), "duplicate expected case")
    return records, bytes_sha256(material)


def validate_trace_admission(record, trace_schema_version):
    phase = record.get("phase")
    _require(type(trace_schema_version) is int, "trace schema must be integer")
    if phase in FORMAL_PHASES:
        _require(trace_schema_version == 3, "formal phase requires schema3")
        return "schema3"
    _require(phase == "integration_smoke", "unknown analysis phase")
    _require(
        record.get("not_for_paper") is True,
        "compatibility smoke must be not_for_paper",
    )
    _require(
        trace_schema_version in {2, 3},
        "unsupported compatibility smoke schema",
    )
    return "schema3" if trace_schema_version == 3 else "schema2_compatibility"


def validate_audit_report(document):
    _require(isinstance(document, dict), "audit report must be an object")
    _require(document.get("strict") is True, "audit report is not strict")
    _require(document.get("overall_pass") is True, "audit overall_pass is false")
    _require(
        document.get("infrastructure_failure_count") == 0,
        "audit has infrastructure failures",
    )
    _require(
        document.get("audit_failure_count") == 0,
        "audit has audit failures",
    )
    per_case = document.get("per_case")
    _require(isinstance(per_case, list) and per_case, "audit per_case is invalid")
    case_ids = [case.get("case_id") for case in per_case if isinstance(case, dict)]
    _require(len(case_ids) == len(per_case), "audit case is invalid")
    _require(
        all(isinstance(value, str) and value for value in case_ids),
        "audit case id is invalid",
    )
    _require(len(case_ids) == len(set(case_ids)), "duplicate audit case")
    _require(
        document.get("case_count") == len(per_case),
        "audit case count mismatch",
    )
    return {case["case_id"]: case for case in per_case}


def _discover_states(output_root):
    states = {}
    state_paths = sorted(
        (
            path
            for path in output_root.rglob("*.json")
            if path.parent.name == "state" and path.parent.parent.name == ".b4pe"
        ),
        key=lambda path: path.relative_to(output_root).as_posix(),
    )
    _require(state_paths, "output-root contains no execution states")
    for path in state_paths:
        state = _load_json(path)
        _require(isinstance(state, dict), "execution state must be an object")
        case_id = state.get("case_id")
        _require(isinstance(case_id, str) and case_id, "state case id is invalid")
        _require(case_id not in states, "duplicate execution state case")
        states[case_id] = (path.resolve(), state)
    return states


def _discover_results(output_root):
    results = set()
    for path in sorted(output_root.rglob("*")):
        relative = path.relative_to(output_root)
        if "results" not in relative.parts or ".b4pe" in relative.parts:
            continue
        if path.is_symlink():
            raise AnalysisError("result must not be a symlink")
        if path.is_file():
            results.add(path.resolve())
    return results


def _require_finite(value, label="value"):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        _require(math.isfinite(value), f"{label} is non-finite")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _require_finite(child, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _require_finite(child, f"{label}.{key}")
        return
    raise AnalysisError(f"{label} has unsupported type")


def task_pass(task):
    for name in TASK_METRIC_FIELDS:
        value = task.get(name)
        _require(
            type(value) is int and value >= 0,
            f"task metric is invalid: {name}",
        )
    return (
        task["deadline_miss_jobs"] == 0
        and task["terminated_jobs"] == 0
        and task["unfinished_at_horizon_jobs"] == 0
        and task["completed_jobs"] == task["released_jobs"]
    )


def aggregate_tasks(tasks):
    _require(isinstance(tasks, list) and tasks, "task rows are empty")
    result = {}
    for field in TASK_METRIC_FIELDS:
        values = [task[field] for task in tasks]
        result[field] = (
            max(values)
            if field == "completed_response_time_max_ms"
            else sum(values)
        )
    return result


def pairing_key(dimensions):
    _require(
        isinstance(dimensions, dict)
        and tuple(dimensions) == PAIRING_FIELDS,
        "pairing dimensions fields or order mismatch",
    )
    _require_finite(dimensions, "pairing_dimensions")
    return bytes_sha256(compact_json(dimensions).encode("utf-8"))


def _algorithm_parts(algorithm):
    _require(algorithm in ALGORITHM_INDEX, "unknown algorithm")
    family, policy = algorithm.split("-", 1)
    return family, policy, ALGORITHM_INDEX[algorithm]


def _ordered(fields, values, label):
    _require(set(values) == set(fields), f"{label} fields mismatch")
    row = {name: values[name] for name in fields}
    _require(tuple(row) == tuple(fields), f"{label} field order mismatch")
    _require_finite(row, label)
    return row


def _source_metadata(source):
    _require(isinstance(source, dict), "source snapshot is invalid")
    body = source.get("source")
    _require(isinstance(body, dict), "source descriptor body is invalid")
    values = {
        "E0_j": source.get("E0_j"),
        "Emax_j": source.get("Emax_j"),
        "alpha_w": body.get("scale_w"),
        "lambda_E": source.get("lambda_E"),
        "rho_E": source.get("rho_E"),
        "source_profile": source.get("profile_id"),
    }
    for name in ("E0_j", "Emax_j", "alpha_w"):
        value = values[name]
        _require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value)),
            f"source {name} is invalid",
        )
    for name in ("lambda_E", "rho_E", "source_profile"):
        _require(
            isinstance(values[name], str) and values[name],
            f"source {name} is invalid",
        )
    return values


def _taskset_metadata(taskset):
    _require(isinstance(taskset, dict), "taskset snapshot is invalid")
    metadata = taskset.get("metadata")
    _require(isinstance(metadata, dict), "taskset metadata is invalid")
    values = {
        "target_normalized_utilization": metadata.get(
            "target_normalized_utilization"
        ),
        "target_total_utilization": metadata.get("target_total_utilization"),
        "M": metadata.get("M", metadata.get("num_cores")),
    }
    for name in ("target_normalized_utilization", "target_total_utilization"):
        value = values[name]
        _require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value)),
            f"taskset {name} is invalid",
        )
    _require(type(values["M"]) is int and values["M"] > 0, "taskset M is invalid")
    return values


def _scheduling_outcomes(audit_case, all_aggregate):
    issues = []
    for issue in audit_case.get("issues", []):
        if not isinstance(issue, dict) or issue.get("classification") != "scheduling_outcome":
            continue
        value = {"code": issue.get("code")}
        if "detail" in issue:
            value["detail"] = issue["detail"]
        _require(isinstance(value["code"], str), "scheduling issue code invalid")
        issues.append(value)
    issues.sort(key=lambda item: (item["code"], str(item.get("detail", ""))))
    return {
        "audit_issues": issues,
        "deadline_miss_jobs": all_aggregate["deadline_miss_jobs"],
        "terminated_jobs": all_aggregate["terminated_jobs"],
        "unfinished_at_horizon_jobs": all_aggregate[
            "unfinished_at_horizon_jobs"
        ],
    }


def _protocol_identity(record, kind):
    if kind == "formal":
        return {
            "manifest_schema_version": record["schema_version"],
            "protocol_name": record["protocol_name"],
            "base_commit": record["base_commit"],
            "frozen_document_sha256": record["frozen_document_sha256"],
            "identity_protocol_sha256": record["identity_protocol_sha256"],
            "system_template_sha256": record["system_template_sha256"],
            "candidate_v1_sha256": record["candidate_v1_sha256"],
            "observability_contract_sha256": record[
                "observability_contract_sha256"
            ],
        }
    protocol = smoke.PROTOCOLS_BY_SCHEMA[record["schema_version"]]
    provenance = record["provenance"]
    return {
        "manifest_schema_version": record["schema_version"],
        "protocol_name": protocol["protocol_name"],
        "base_commit": None,
        "frozen_document_sha256": None,
        "identity_protocol_sha256": None,
        "system_template_sha256": None,
        "candidate_v1_sha256": record.get("candidate_v1_sha256"),
        "observability_contract_sha256": record.get(
            "observability_contract_sha256"
        ),
        "generator_sha256": provenance.get("generator_sha256"),
        "simulator_sha256": provenance.get("simulator_sha256"),
    }


def _extract_one(entry, audit_case, state_path, state):
    record = entry["record"]
    kind = entry["kind"]
    case_id = record["case_id"]
    _require(state.get("case_id") == case_id, "state case mismatch")
    _require(state.get("current_status") == "succeeded", "state is not succeeded")
    _require(state.get("algorithm") == record.get("algorithm"), "state algorithm mismatch")
    _require(audit_case.get("status") == "succeeded", "audit case is not succeeded")
    _require(audit_case.get("algorithm") == record.get("algorithm"), "audit algorithm mismatch")

    case_root_value = audit_case.get("output_root")
    _require(isinstance(case_root_value, str), "audit output root is invalid")
    case_root = Path(case_root_value).resolve(strict=True)
    _require(case_root.is_dir(), "audit case output root is unavailable")
    _require(
        len(state_path.parents) >= 3 and case_root == state_path.parents[2],
        "audit case root/state layout mismatch",
    )
    if kind == "smoke":
        _require(
            record.get("output_root") == str(case_root),
            "smoke record/audit output root mismatch",
        )
    audit_state = Path(audit_case.get("state_path", "")).resolve(strict=True)
    _require(audit_state == state_path, "audit/state path mismatch")

    result_relative = state.get("result_relpath")
    _require(
        result_relative == audit_case.get("result_relpath")
        == record.get("result_relpath"),
        "result path mismatch",
    )
    result_path = _safe_child(case_root, result_relative, "result path")
    taskset_path = _safe_child(
        case_root, state.get("taskset_snapshot_relpath"), "taskset snapshot"
    )
    source_path = _safe_child(
        case_root, state.get("source_snapshot_relpath"), "source snapshot"
    )
    _require(
        file_sha256(result_path) == state.get("final_result_sha256"),
        "result SHA mismatch",
    )
    _require(
        file_sha256(taskset_path) == state.get("taskset_snapshot_sha256"),
        "taskset snapshot SHA mismatch",
    )
    _require(
        file_sha256(source_path) == state.get("source_snapshot_sha256"),
        "source snapshot SHA mismatch",
    )

    result = _load_json(result_path)
    source = _load_json(source_path)
    try:
        taskset = yaml.safe_load(taskset_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AnalysisError("invalid taskset snapshot") from exc
    _require(isinstance(result, dict), "result must be an object")
    _require(result.get("run_id") == case_id, "result case id mismatch")
    trace_schema = result.get("trace_schema_version")
    admission = validate_trace_admission(record, trace_schema)
    _require(
        admission == "schema3",
        "schema2 compatibility smoke has no schema3 summary to extract",
    )

    ranks = observability.task_ranks_from_taskset(taskset)
    pairing = audit_case.get("pairing")
    _require(isinstance(pairing, dict), "audit pairing metadata is missing")
    source_values = _source_metadata(source)
    taskset_values = _taskset_metadata(taskset)
    processor_count = pairing.get("processors")
    horizon_ms = result.get("expected_simulation_horizon_ms")
    _require(
        record.get("trace_schema_version") == trace_schema,
        "record/result trace schema mismatch",
    )
    expected_contract_version = record.get(
        "observability_summary_contract_version"
    )
    _require(
        expected_contract_version
        == result.get("observability_summary_contract_version"),
        "record/result observability contract mismatch",
    )
    expected_summary_horizon = record.get(
        "summary_horizon_ms",
        record.get("observability_summary_horizon_ms"),
    )
    _require(
        expected_summary_horizon
        == result.get("observability_summary_horizon_ms")
        == horizon_ms,
        "record/result summary horizon mismatch",
    )
    if record.get("lambda_E") is not None:
        _require(
            record["lambda_E"] == source_values["lambda_E"],
            "record/source lambda_E mismatch",
        )
    if record.get("rho_E") is not None:
        _require(
            record["rho_E"] == source_values["rho_E"],
            "record/source rho_E mismatch",
        )
    if record.get("source_profile") is not None:
        _require(
            record["source_profile"] == source_values["source_profile"],
            "record/source profile mismatch",
        )
    if record.get("utilization") is not None:
        _require(
            float(record["utilization"])
            == float(taskset_values["target_normalized_utilization"]),
            "record/taskset utilization mismatch",
        )
    if record.get("M") is not None:
        _require(record["M"] == taskset_values["M"], "record/taskset M mismatch")
    if record.get("horizon_ms") is not None:
        _require(record["horizon_ms"] == horizon_ms, "record/result horizon mismatch")
    try:
        observability.validate_schema3_summary(
            result,
            expected_horizon_ms=horizon_ms,
            initial_energy_j=source_values["E0_j"],
            capacity_j=source_values["Emax_j"],
            processor_count=processor_count,
            expected_task_ranks=ranks,
        )
    except observability.ObservabilityValidationError as exc:
        raise AnalysisError(str(exc)) from exc

    _require(
        result.get("taskset_semantic_hash") == pairing.get("semantic_hash"),
        "taskset semantic identity mismatch",
    )
    _require(
        source_values["E0_j"] == pairing.get("E0_j")
        and source_values["Emax_j"] == pairing.get("Emax_j")
        and source_values["alpha_w"] == pairing.get("alpha_w")
        and source_values["source_profile"] == pairing.get("source_profile"),
        "source/audit pairing mismatch",
    )
    _require(
        processor_count == taskset_values["M"],
        "processor count/taskset M mismatch",
    )

    algorithm = record["algorithm"]
    family, policy, algorithm_order = _algorithm_parts(algorithm)
    expected_scheduler = manifest.PROTOCOL["algorithm_cli_mapping"][algorithm]
    _require(
        result.get("configured_scheduler") == expected_scheduler,
        "configured scheduler mismatch",
    )
    for field in ("scheduler_display_name", "scheduler_implementation"):
        _require(
            isinstance(result.get(field), str) and result[field],
            f"result {field} is invalid",
        )

    task_rows_payload = []
    for reported in result["per_task_summary"]:
        task = {name: reported[name] for name in TASK_METRIC_FIELDS}
        rank = reported["priority_rank"]
        task.update(
            {
                "task_name": reported["task_name"],
                "priority_rank": rank,
                "is_top4": reported["is_top4"],
                "is_bottom6": reported["is_bottom6"],
            }
        )
        task["task_pass"] = task_pass(task)
        task_rows_payload.append(task)
    task_rows_payload.sort(key=lambda item: item["priority_rank"])
    _require(
        [item["priority_rank"] for item in task_rows_payload] == list(range(10)),
        "task ranks are not contiguous",
    )

    all_aggregate = aggregate_tasks(task_rows_payload)
    hp_aggregate = aggregate_tasks(task_rows_payload[:4])
    lp_aggregate = aggregate_tasks(task_rows_payload[4:])
    whole_pass = all(item["task_pass"] for item in task_rows_payload)
    hp_pass = all(item["task_pass"] for item in task_rows_payload[:4])
    lp_pass = all(item["task_pass"] for item in task_rows_payload[4:])

    protocol_identity = _protocol_identity(record, kind)
    normalized_system = pairing.get("normalized_system")
    _require(
        isinstance(normalized_system, str) and normalized_system,
        "normalized system identity is invalid",
    )
    taskset_id = record.get("taskset_id") or result["taskset_semantic_hash"]
    source_sha = state["source_snapshot_sha256"]
    source_identity = record.get("source_id") or source_sha
    identity = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "phase": record["phase"],
        "case_id": case_id,
        "pairing_key": None,
        "pairing_dimensions": None,
        "taskset_id": taskset_id,
        "taskset_semantic_hash": result["taskset_semantic_hash"],
        "taskset_sha256": state["taskset_snapshot_sha256"],
        "taskset_seed": record.get("taskset_seed"),
        "replicate_index": record.get("replicate_index"),
        "taskset_pool": record.get("taskset_pool"),
        "source_identity": source_identity,
        "source_sha256": source_sha,
        "source_seed": record.get("source_seed"),
        "source_profile": source_values["source_profile"],
        "utilization": record.get("utilization"),
        "target_normalized_utilization": taskset_values[
            "target_normalized_utilization"
        ],
        "target_total_utilization": taskset_values["target_total_utilization"],
        "lambda_E": record.get("lambda_E", source_values["lambda_E"]),
        "rho_E": record.get("rho_E", source_values["rho_E"]),
        "E0_j": source_values["E0_j"],
        "Emax_j": source_values["Emax_j"],
        "alpha_w": source_values["alpha_w"],
        "E0_rule": record.get("E0_rule"),
        "Emax_rule": record.get("Emax_rule"),
        "alpha_rule": record.get("alpha_rule"),
        "M": record.get("M", taskset_values["M"]),
        "horizon_ms": record.get("horizon_ms", horizon_ms),
        "not_for_paper": record.get("not_for_paper", False),
        "algorithm": algorithm,
        "configured_scheduler": result["configured_scheduler"],
        "scheduler_display_name": result["scheduler_display_name"],
        "scheduler_implementation": result["scheduler_implementation"],
        "scheduler_family": family,
        "blocking_policy": policy,
        "algorithm_order": algorithm_order,
        "trace_schema_version": trace_schema,
        "observability_summary_contract_version": result[
            "observability_summary_contract_version"
        ],
        "observability_summary_horizon_ms": result[
            "observability_summary_horizon_ms"
        ],
        "processor_count": processor_count,
        "task_count": len(task_rows_payload),
    }
    pairing_values = {
        "phase": identity["phase"],
        "taskset_id": identity["taskset_id"],
        "taskset_semantic_hash": identity["taskset_semantic_hash"],
        "taskset_sha256": identity["taskset_sha256"],
        "taskset_seed": identity["taskset_seed"],
        "replicate_index": identity["replicate_index"],
        "taskset_pool": identity["taskset_pool"],
        "source_identity": identity["source_identity"],
        "source_sha256": identity["source_sha256"],
        "source_seed": identity["source_seed"],
        "source_profile": identity["source_profile"],
        "utilization": identity["utilization"],
        "target_normalized_utilization": identity[
            "target_normalized_utilization"
        ],
        "target_total_utilization": identity["target_total_utilization"],
        "lambda_E": identity["lambda_E"],
        "rho_E": identity["rho_E"],
        "E0_j": identity["E0_j"],
        "Emax_j": identity["Emax_j"],
        "alpha_w": identity["alpha_w"],
        "E0_rule": identity["E0_rule"],
        "Emax_rule": identity["Emax_rule"],
        "alpha_rule": identity["alpha_rule"],
        "M": identity["M"],
        "task_count": identity["task_count"],
        "horizon_ms": identity["horizon_ms"],
        "observability_summary_contract_version": identity[
            "observability_summary_contract_version"
        ],
        "observability_summary_horizon_ms": identity[
            "observability_summary_horizon_ms"
        ],
        "not_for_paper": identity["not_for_paper"],
        "manifest_schema_version": protocol_identity["manifest_schema_version"],
        "protocol_name": protocol_identity["protocol_name"],
        "base_commit": protocol_identity["base_commit"],
        "frozen_document_sha256": protocol_identity[
            "frozen_document_sha256"
        ],
        "identity_protocol_sha256": protocol_identity[
            "identity_protocol_sha256"
        ],
        "system_template_sha256": protocol_identity[
            "system_template_sha256"
        ],
        "candidate_v1_sha256": protocol_identity["candidate_v1_sha256"],
        "observability_contract_sha256": protocol_identity[
            "observability_contract_sha256"
        ],
        "generator_sha256": protocol_identity.get(
            "generator_sha256", pairing.get("generator_sha256")
        ),
        "simulator_sha256": protocol_identity.get(
            "simulator_sha256", pairing.get("simulator_binary_sha256")
        ),
        "normalized_system_sha256": bytes_sha256(
            normalized_system.encode("utf-8")
        ),
    }
    pairing_values = _ordered(PAIRING_FIELDS, pairing_values, "pairing dimensions")
    identity["pairing_dimensions"] = pairing_values
    identity["pairing_key"] = pairing_key(pairing_values)
    identity = _ordered(CASE_IDENTITY_FIELDS, identity, "case identity")

    case_values = dict(identity)
    case_values.update(
        {
            "scheduling_outcomes": _scheduling_outcomes(
                audit_case, all_aggregate
            ),
            "whole_pass": whole_pass,
            "hp_pass": hp_pass,
            "lp_pass": lp_pass,
        }
    )
    case_values.update(
        {name: result["mechanism_summary"][name] for name in MECHANISM_FIELDS}
    )
    case_values.update(
        {name: result["energy_summary"][name] for name in ENERGY_FIELDS}
    )
    for prefix, values in (
        ("all", all_aggregate),
        ("hp", hp_aggregate),
        ("lp", lp_aggregate),
    ):
        case_values.update(
            {f"{prefix}_{name}": values[name] for name in TASK_METRIC_FIELDS}
        )
    case_row = _ordered(CASE_FIELDS, case_values, "case row")

    task_rows = []
    for payload in task_rows_payload:
        values = dict(identity)
        values.update(payload)
        task_rows.append(_ordered(TASK_FIELDS, values, "task row"))
    raw = {
        "mechanism_summary": result["mechanism_summary"],
        "energy_summary": result["energy_summary"],
    }
    return case_row, task_rows, raw


def _case_sort_key(row):
    try:
        phase_index = PHASE_ORDER.index(row["phase"])
    except ValueError as exc:
        raise AnalysisError("unknown phase order") from exc
    return (
        phase_index,
        compact_json(row["pairing_dimensions"]),
        row["algorithm_order"],
        row["case_id"],
    )


def validate_pairing_groups(case_rows):
    groups = defaultdict(list)
    for row in case_rows:
        if row["trace_schema_version"] == 2:
            _require(
                row["phase"] == "integration_smoke"
                and row["not_for_paper"] is True,
                "schema2 case entered formal pairing",
            )
            continue
        groups[row["pairing_key"]].append(row)
    for key, rows in groups.items():
        _require(len(rows) == len(ALGORITHMS), f"pairing group size mismatch: {key}")
        orders = [row["algorithm_order"] for row in rows]
        algorithms = [row["algorithm"] for row in rows]
        _require(
            sorted(orders) == list(range(len(ALGORITHMS))),
            "pairing algorithm order coverage mismatch",
        )
        _require(
            Counter(algorithms) == Counter(ALGORITHMS),
            "pairing algorithm coverage mismatch",
        )
        canonical = compact_json(rows[0]["pairing_dimensions"])
        _require(
            all(compact_json(row["pairing_dimensions"]) == canonical for row in rows),
            "pairing dimension mismatch",
        )
    return len(groups)


def jsonl_bytes(rows, fields):
    lines = []
    for row in rows:
        _require(tuple(row) == tuple(fields), "JSONL row field order mismatch")
        lines.append(compact_json(row))
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _csv_cell(value):
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        _require(math.isfinite(value), "CSV float is non-finite")
        return format(value, ".17g")
    if isinstance(value, (dict, list)):
        return compact_json(value)
    _require(isinstance(value, str), "unsupported CSV value")
    return value


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(fields)
    for row in rows:
        _require(tuple(row) == tuple(fields), "CSV row field order mismatch")
        writer.writerow([_csv_cell(row[name]) for name in fields])
    return stream.getvalue().encode("utf-8")


def verify_jsonl_csv_parity(rows, fields, jsonl_material, csv_material):
    try:
        json_lines = jsonl_material.decode("utf-8").splitlines()
        decoded = [
            json.loads(line, object_pairs_hook=_reject_duplicate_pairs)
            for line in json_lines
        ]
        csv_rows = list(
            csv.reader(io.StringIO(csv_material.decode("utf-8"), newline=""))
        )
    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise AnalysisError("rendered output is not parseable") from exc
    _require(decoded == rows, "JSONL round-trip mismatch")
    _require(all(tuple(row) == tuple(fields) for row in decoded), "JSONL key order drift")
    _require(csv_rows and tuple(csv_rows[0]) == tuple(fields), "CSV header mismatch")
    _require(len(csv_rows) - 1 == len(rows), "CSV row count mismatch")
    expected = [[_csv_cell(row[name]) for name in fields] for row in rows]
    _require(csv_rows[1:] == expected, "CSV/JSONL parity mismatch")
    return True


def _self_audit(case_rows, task_rows, raw_by_case, pairing_group_count, input_count):
    case_ids = [row["case_id"] for row in case_rows]
    task_keys = [(row["case_id"], row["priority_rank"]) for row in task_rows]
    checks = {
        "input_audit_overall_pass": True,
        "input_case_set_closed": len(case_rows) == input_count,
        "case_id_unique": len(case_ids) == len(set(case_ids)),
        "task_primary_key_unique": len(task_keys) == len(set(task_keys)),
        "ten_tasks_per_case": True,
        "rank_coverage_0_9": True,
        "top4_count_4": True,
        "bottom6_count_6": True,
        "schema3_pairing_complete": pairing_group_count > 0,
        "case_task_aggregates_match": True,
        "pass_flags_recomputable": True,
        "mechanism_exact_copy": True,
        "energy_exact_copy": True,
        "output_fields_exact": True,
        "finite_numbers_only": True,
    }
    tasks_by_case = defaultdict(list)
    for row in task_rows:
        tasks_by_case[row["case_id"]].append(row)
    for case in case_rows:
        rows = sorted(
            tasks_by_case.get(case["case_id"], []),
            key=lambda item: item["priority_rank"],
        )
        checks["ten_tasks_per_case"] &= len(rows) == 10
        checks["rank_coverage_0_9"] &= (
            [row["priority_rank"] for row in rows] == list(range(10))
        )
        checks["top4_count_4"] &= sum(row["is_top4"] for row in rows) == 4
        checks["bottom6_count_6"] &= sum(row["is_bottom6"] for row in rows) == 6
        if len(rows) != 10:
            continue
        expected_pass = [task_pass(row) for row in rows]
        checks["pass_flags_recomputable"] &= (
            [row["task_pass"] for row in rows] == expected_pass
            and case["whole_pass"] == all(expected_pass)
            and case["hp_pass"] == all(expected_pass[:4])
            and case["lp_pass"] == all(expected_pass[4:])
        )
        for prefix, selected in (("all", rows), ("hp", rows[:4]), ("lp", rows[4:])):
            aggregate = aggregate_tasks(selected)
            checks["case_task_aggregates_match"] &= all(
                case[f"{prefix}_{name}"] == aggregate[name]
                for name in TASK_METRIC_FIELDS
            )
        raw = raw_by_case[case["case_id"]]
        checks["mechanism_exact_copy"] &= all(
            case[name] == raw["mechanism_summary"][name]
            for name in MECHANISM_FIELDS
        )
        checks["energy_exact_copy"] &= all(
            case[name] == raw["energy_summary"][name]
            for name in ENERGY_FIELDS
        )
    checks["output_fields_exact"] &= all(tuple(row) == CASE_FIELDS for row in case_rows)
    checks["output_fields_exact"] &= all(tuple(row) == TASK_FIELDS for row in task_rows)
    try:
        _require_finite(case_rows, "cases")
        _require_finite(task_rows, "tasks")
    except AnalysisError:
        checks["finite_numbers_only"] = False
    failed = [name for name, passed in checks.items() if not passed]
    _require(not failed, f"analysis self-audit failed: {','.join(failed)}")
    return checks


def _source_version_identity():
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        )
    except subprocess.CalledProcessError as exc:
        raise AnalysisError("cannot determine source version identity") from exc
    digest = hashlib.sha256()
    for path in (CONTRACT_PATH, Path(__file__), EXTRACTOR_PATH):
        relative = path.relative_to(REPO_ROOT).as_posix().encode("utf-8")
        material = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(material).to_bytes(8, "big"))
        digest.update(material)
    return {
        "source_code_commit": None if dirty else head,
        "source_base_commit": head,
        "extractor_version_sha256": digest.hexdigest(),
    }


def build_outputs(output_root, expected_records_path, audit_report_path, strict):
    _require(strict is True, "--strict is required")
    output_root = validate_output_root(output_root)
    expected_path = _absolute_regular_file(expected_records_path, "expected-records")
    audit_path = _absolute_regular_file(audit_report_path, "audit-report")
    entries, expected_sha = load_expected_records(expected_path)
    audit_document = _load_json(audit_path)
    audit_by_case = validate_audit_report(audit_document)
    states = _discover_states(output_root)

    expected_by_case = {entry["record"]["case_id"]: entry for entry in entries}
    expected_ids = set(expected_by_case)
    _require(expected_ids == set(audit_by_case), "expected/audit case set mismatch")
    _require(expected_ids == set(states), "expected/state case set mismatch")
    expected_results = set()
    for audit_case in audit_by_case.values():
        case_root = Path(audit_case.get("output_root", "")).resolve(strict=True)
        expected_results.add(
            _safe_child(
                case_root,
                audit_case.get("result_relpath"),
                "audit result path",
            )
        )
    _require(
        expected_results == _discover_results(output_root),
        "expected/result case set mismatch",
    )

    case_rows = []
    task_rows = []
    raw_by_case = {}
    for case_id in sorted(expected_ids):
        state_path, state = states[case_id]
        _require(_is_within(state_path, output_root), "state is outside output-root")
        case_row, case_tasks, raw = _extract_one(
            expected_by_case[case_id],
            audit_by_case[case_id],
            state_path,
            state,
        )
        case_rows.append(case_row)
        task_rows.extend(case_tasks)
        raw_by_case[case_id] = raw

    case_rows.sort(key=_case_sort_key)
    case_order = {row["case_id"]: index for index, row in enumerate(case_rows)}
    task_rows.sort(
        key=lambda row: (case_order[row["case_id"]], row["priority_rank"])
    )
    pairing_group_count = validate_pairing_groups(case_rows)
    checks = _self_audit(
        case_rows,
        task_rows,
        raw_by_case,
        pairing_group_count,
        len(entries),
    )

    cases_jsonl = jsonl_bytes(case_rows, CASE_FIELDS)
    tasks_jsonl = jsonl_bytes(task_rows, TASK_FIELDS)
    cases_csv = csv_bytes(case_rows, CASE_FIELDS)
    tasks_csv = csv_bytes(task_rows, TASK_FIELDS)
    checks["cases_jsonl_csv_parity"] = verify_jsonl_csv_parity(
        case_rows, CASE_FIELDS, cases_jsonl, cases_csv
    )
    checks["tasks_jsonl_csv_parity"] = verify_jsonl_csv_parity(
        task_rows, TASK_FIELDS, tasks_jsonl, tasks_csv
    )

    data_files = {
        "cases.jsonl": cases_jsonl,
        "tasks.jsonl": tasks_jsonl,
        "cases.csv": cases_csv,
        "tasks.csv": tasks_csv,
    }
    data_hashes = {name: bytes_sha256(value) for name, value in data_files.items()}
    analysis_audit = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "overall_pass": all(checks.values()),
        "checks": checks,
        "input_case_count": len(entries),
        "case_row_count": len(case_rows),
        "task_row_count": len(task_rows),
        "pairing_group_count": pairing_group_count,
        "scheduling_outcome_case_count": sum(
            bool(row["scheduling_outcomes"]["audit_issues"])
            or row["scheduling_outcomes"]["deadline_miss_jobs"] > 0
            or row["scheduling_outcomes"]["terminated_jobs"] > 0
            or row["scheduling_outcomes"]["unfinished_at_horizon_jobs"] > 0
            for row in case_rows
        ),
        "output_file_sha256": data_hashes,
        "issues": [],
        "no_paper_data_generated": True,
    }
    _require(analysis_audit["overall_pass"], "analysis audit did not pass")
    audit_bytes = pretty_json_bytes(analysis_audit)

    source_identity = _source_version_identity()
    output_hashes = dict(data_hashes)
    output_hashes["analysis_audit.json"] = bytes_sha256(audit_bytes)
    analysis_manifest = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_contract_path": CONTRACT_PATH.relative_to(REPO_ROOT).as_posix(),
        "analysis_contract_sha256": file_sha256(CONTRACT_PATH),
        "observability_contract_path": OBSERVABILITY_CONTRACT_PATH.relative_to(
            REPO_ROOT
        ).as_posix(),
        "observability_contract_sha256": file_sha256(
            OBSERVABILITY_CONTRACT_PATH
        ),
        "candidate_v2_path": CANDIDATE_V2_PATH.relative_to(REPO_ROOT).as_posix(),
        "candidate_v2_sha256": file_sha256(CANDIDATE_V2_PATH),
        "source_code_commit": source_identity["source_code_commit"],
        "source_base_commit": source_identity["source_base_commit"],
        "extractor_version_sha256": source_identity[
            "extractor_version_sha256"
        ],
        "input_expected_records_sha256": expected_sha,
        "input_audit_report_sha256": file_sha256(audit_path),
        "case_row_count": len(case_rows),
        "task_row_count": len(task_rows),
        "pairing_group_count": pairing_group_count,
        "algorithm_canonical_order": list(ALGORITHMS),
        "output_file_sha256": output_hashes,
        "no_paper_data_generated": True,
    }
    manifest_bytes = pretty_json_bytes(analysis_manifest)
    outputs = dict(data_files)
    outputs["analysis_audit.json"] = audit_bytes
    outputs["analysis_manifest.json"] = manifest_bytes
    _require(set(outputs) == set(OUTPUT_NAMES), "output set mismatch")
    return outputs, analysis_manifest, analysis_audit


def publish_outputs(analysis_root, outputs):
    root = validate_analysis_root(analysis_root)
    created = False
    try:
        if not root.exists():
            root.mkdir(mode=0o755)
            created = True
        for name in (
            "cases.jsonl",
            "tasks.jsonl",
            "cases.csv",
            "tasks.csv",
            "analysis_audit.json",
            "analysis_manifest.json",
        ):
            temporary = root / f".{name}.tmp"
            with temporary.open("xb") as handle:
                handle.write(outputs[name])
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, root / name)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if root.exists():
            for name in OUTPUT_NAMES:
                for candidate in (root / name, root / f".{name}.tmp"):
                    try:
                        candidate.unlink()
                    except FileNotFoundError:
                        pass
        if created and root.exists():
            root.rmdir()
        raise
    return root


def write_failure_audit(analysis_root):
    try:
        root = validate_analysis_root(analysis_root)
    except AnalysisError:
        return
    created = False
    try:
        if not root.exists():
            root.mkdir(mode=0o755)
            created = True
        failure = {
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "overall_pass": False,
            "checks": {},
            "issues": [{"code": "extraction_failed"}],
            "no_paper_data_generated": True,
        }
        (root / "analysis_audit.json").write_bytes(pretty_json_bytes(failure))
        manifest_path = root / "analysis_manifest.json"
        if manifest_path.exists():
            manifest_path.unlink()
    except OSError:
        if created and root.exists():
            shutil.rmtree(root)
