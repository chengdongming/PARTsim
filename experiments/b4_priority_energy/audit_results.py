#!/usr/bin/env python3
"""Read-only result-integrity and pairing auditor for B4-PE outputs."""

from __future__ import annotations

import argparse
import json
import math
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml


sys.dont_write_bytecode = True

B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]

import execution_common as execution
import inspect_execution
import integration_smoke_common as smoke
import manifest_common as manifest
import observability_validation as observability


SCHEMA_VERSION = 1
ALGORITHMS = tuple(
    manifest.IDENTITY.RESOLUTION["phase_algorithms"]["formal_main"]
)
ALGORITHM_CLI_MAPPING = dict(manifest.PROTOCOL["algorithm_cli_mapping"])
SCHEDULERS = tuple(ALGORITHM_CLI_MAPPING[name] for name in ALGORITHMS)
CLASSIFICATIONS = (
    "infrastructure_failure",
    "scheduling_outcome",
    "audit_failure",
)
FAILURE_PATTERN = re.compile(
    r"(?i)(yaml::(?:parse|parser)exception|pre[- ]?flight (?:error|failed)|"
    r"taskset semantic hash (?:missing|mismatch)|invalid trace extension|"
    r"trace_target_exists_with_different_content|\bnan\b|\binf\b|"
    r"incomplete trace)"
)


class AuditInputError(ValueError):
    """The CLI paths or expected-record source are invalid."""


def _require_input(condition, message):
    if not condition:
        raise AuditInputError(message)


def _is_within(path, parent):
    path = Path(path)
    parent = Path(parent)
    return path == parent or parent in path.parents


def _absolute_directory(value, label):
    path = Path(value)
    _require_input(path.is_absolute(), f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AuditInputError(f"{label} cannot be resolved: {exc}") from exc
    _require_input(resolved.is_dir(), f"{label} must be a directory")
    return resolved


def _report_path(value, output_root):
    path = Path(value)
    _require_input(path.is_absolute(), "report must be absolute")
    resolved = path.resolve(strict=False)
    _require_input(
        not _is_within(resolved, output_root),
        "report must be outside audited output-root",
    )
    _require_input(
        not _is_within(resolved, REPO_ROOT),
        "report must be outside the repository",
    )
    _require_input(
        resolved.parent.is_dir(),
        "report parent must be an existing directory",
    )
    _require_input(
        not resolved.exists() or (resolved.is_file() and not resolved.is_symlink()),
        "existing report must be a regular non-symlink file",
    )
    return resolved


def _new_issue(classification, code, detail=None):
    issue = {"classification": classification, "code": code}
    if detail is not None:
        issue["detail"] = str(detail)
    return issue


def _add_issue(subject, classification, code, detail=None):
    subject["issues"].append(_new_issue(classification, code, detail))


def _sort_issues(issues):
    return sorted(
        issues,
        key=lambda item: (
            item["classification"],
            item["code"],
            item.get("detail", ""),
        ),
    )


def _classification_list(issues):
    observed = {issue["classification"] for issue in issues}
    return [name for name in CLASSIFICATIONS if name in observed]


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _expected_entry(record, source_path, output_root):
    case_root = Path(record.get("output_root", output_root)).resolve(strict=False)
    return {
        "record": record,
        "source_path": str(Path(source_path).resolve(strict=True)),
        "output_root": str(case_root),
    }


def _load_smoke_record(path, output_root):
    envelope = smoke.validate_integration_smoke_record(path)
    record = envelope["records"][0]
    case_root = Path(envelope["output_root"]).resolve(strict=True)
    _require_input(
        _is_within(case_root, output_root),
        "expected smoke record output-root is outside audited output-root",
    )
    return _expected_entry(record, path, case_root)


def _load_formal_manifest(path, output_root):
    records = manifest.parse_manifest(path)
    for index, record in enumerate(records, 1):
        manifest._validate_record_structure(record, index)
    return [
        _expected_entry(record, path, output_root)
        for record in records
    ], manifest.audit_records(records)


def _load_record_list(path, output_root):
    entries = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        value = raw.strip()
        _require_input(value, f"expected-record list line {line_number} is empty")
        record_path = Path(value)
        if not record_path.is_absolute():
            record_path = path.parent / record_path
        entries.append(_load_smoke_record(record_path, output_root))
    _require_input(entries, "expected-record list is empty")
    return entries


def load_expected_records(value, output_root):
    """Load smoke records, a formal JSONL manifest, or a path list."""
    if value is None:
        return [], [], None
    path = Path(value)
    _require_input(path.is_absolute(), "expected-records must be absolute")
    path = path.resolve(strict=True)
    entries = []
    errors = []
    manifest_audit = None
    if path.is_dir():
        record_paths = sorted(path.rglob("*.json"))
        _require_input(record_paths, "expected-record directory has no JSON records")
        for record_path in record_paths:
            try:
                entries.append(_load_smoke_record(record_path, output_root))
            except (smoke.IntegrationSmokeError, OSError, ValueError) as exc:
                errors.append(
                    _new_issue(
                        "audit_failure",
                        "malformed_metadata",
                        f"{record_path}: {exc}",
                    )
                )
    elif path.is_file() and path.suffix == ".jsonl":
        try:
            entries, manifest_audit = _load_formal_manifest(path, output_root)
        except (manifest.ManifestError, OSError, ValueError) as exc:
            errors.append(
                _new_issue("audit_failure", "malformed_metadata", exc)
            )
    elif path.is_file() and path.suffix == ".json":
        try:
            document = _load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AuditInputError(f"expected-records JSON is invalid: {exc}") from exc
        if isinstance(document, list):
            for index, item in enumerate(document):
                if not isinstance(item, str) or not item:
                    errors.append(
                        _new_issue(
                            "audit_failure",
                            "malformed_metadata",
                            f"expected-record list item {index} is invalid",
                        )
                    )
                    continue
                record_path = Path(item)
                if not record_path.is_absolute():
                    record_path = path.parent / record_path
                try:
                    entries.append(_load_smoke_record(record_path, output_root))
                except (smoke.IntegrationSmokeError, OSError, ValueError) as exc:
                    errors.append(
                        _new_issue(
                            "audit_failure",
                            "malformed_metadata",
                            f"{record_path}: {exc}",
                        )
                    )
        else:
            try:
                entries.append(_load_smoke_record(path, output_root))
            except (smoke.IntegrationSmokeError, OSError, ValueError) as exc:
                errors.append(
                    _new_issue("audit_failure", "malformed_metadata", exc)
                )
    elif path.is_file():
        try:
            entries = _load_record_list(path, output_root)
        except (smoke.IntegrationSmokeError, OSError, ValueError) as exc:
            errors.append(
                _new_issue("audit_failure", "malformed_metadata", exc)
            )
    else:
        raise AuditInputError("expected-records must be a file or directory")
    return entries, _sort_issues(errors), manifest_audit


def _discover_state_paths(output_root):
    return sorted(
        output_root.rglob(".b4pe/state/*.json"),
        key=lambda path: path.relative_to(output_root).as_posix(),
    )


def _state_root(path):
    return path.parents[2]


def _safe_path(root, relative):
    return execution.safe_output_path(root, relative)


def _expected_duration(record, result):
    if record is not None and type(record.get("horizon_ms")) is int:
        return record["horizon_ms"]
    if record is not None:
        argv = record.get("command_argv")
        taskset = record.get(
            "taskset_artifact_relpath",
            record.get("taskset_path"),
        )
        if isinstance(argv, list) and taskset in argv:
            index = argv.index(taskset)
            if index + 1 < len(argv):
                try:
                    return int(argv[index + 1])
                except (TypeError, ValueError):
                    pass
    value = result.get("expected_simulation_horizon_ms")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _expected_semantic_hash(record):
    if record is None:
        return None
    provenance = record.get("provenance")
    if isinstance(provenance, dict):
        value = provenance.get("taskset_semantic_hash")
        if isinstance(value, str) and value:
            return value
    argv = record.get("command_argv")
    if isinstance(argv, list) and argv.count("--taskset-semantic-hash") == 1:
        index = argv.index("--taskset-semantic-hash")
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def _result_task_count(result):
    value = result.get("task_count")
    if type(value) is int:
        return value
    events = result.get("events")
    if not isinstance(events, list):
        return None
    names = {
        event.get("task_name")
        for event in events
        if isinstance(event, dict)
        and event.get("event_type") == "arrival"
        and isinstance(event.get("task_name"), str)
    }
    return len(names) if names else None


def _walk_numbers(value, path="$"):
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _walk_numbers(value[key], f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_numbers(item, f"{path}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield path, value


def _source_metadata(case, root, state):
    relative = state.get("source_snapshot_relpath")
    if not isinstance(relative, str):
        _add_issue(case, "audit_failure", "malformed_metadata", "source snapshot path")
        return {}
    try:
        document = _load_json(_safe_path(root, relative))
    except (execution.ExecutionError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        _add_issue(case, "audit_failure", "malformed_metadata", f"source: {exc}")
        return {}
    source = document.get("source", {}) if isinstance(document, dict) else {}
    return {
        "E0_j": document.get("E0_j"),
        "Emax_j": document.get("Emax_j"),
        "alpha_w": (
            source.get("scale_w")
            if isinstance(source, dict)
            else document.get("alpha_w")
        ),
        "source_profile": document.get("profile_id"),
    }


def _system_metadata(case, root, state, scheduler):
    relative = state.get("system_snapshot_relpath")
    if not isinstance(relative, str):
        _add_issue(case, "audit_failure", "malformed_metadata", "system snapshot path")
        return {}
    try:
        text = _safe_path(root, relative).read_text(encoding="utf-8")
        document = yaml.safe_load(text)
        island = document["cpu_islands"][0]
        processors = island["numcpus"]
    except (
        execution.ExecutionError,
        OSError,
        UnicodeError,
        yaml.YAMLError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        _add_issue(case, "audit_failure", "malformed_metadata", f"system: {exc}")
        return {}
    pattern = re.compile(
        r"(?m)^([ \t]*scheduler:[ \t]*)([^ \t\r\n#]+)"
        r"([ \t]*(?:#.*)?)(\r?\n|$)"
    )
    matches = list(pattern.finditer(text))
    if (
        len(matches) != 1
        or not isinstance(scheduler, str)
        or matches[0].group(2) != scheduler
    ):
        _add_issue(
            case,
            "audit_failure",
            "malformed_metadata",
            "system scheduler declaration",
        )
        normalized = None
    else:
        normalized = pattern.sub(
            r"\1<scheduler>\3\4",
            text,
            count=1,
        )
    if processors != 4:
        _add_issue(case, "audit_failure", "processor_count_mismatch", processors)
    return {
        "processors": processors,
        "normalized_system": normalized,
    }


def _taskset_ranks(case, root, state):
    relative = state.get("taskset_snapshot_relpath")
    if not isinstance(relative, str):
        _add_issue(
            case,
            "audit_failure",
            "malformed_metadata",
            "taskset snapshot path",
        )
        return None
    try:
        document = yaml.safe_load(
            _safe_path(root, relative).read_text(encoding="utf-8")
        )
        return {
            "ranks": observability.task_ranks_from_taskset(document),
            "document": document,
        }
    except (
        execution.ExecutionError,
        OSError,
        UnicodeError,
        yaml.YAMLError,
        observability.ObservabilityValidationError,
    ) as exc:
        _add_issue(
            case,
            "audit_failure",
            "malformed_metadata",
            f"taskset: {exc}",
        )
        return None


def _schema3_required(record):
    if not isinstance(record, dict):
        return True
    return not (
        record.get("phase") == "integration_smoke"
        and record.get("not_for_paper") is True
        and record.get("schema_version")
        == smoke.PROTOCOL_V1["schema_version"]
    )


def _classify_result(
    case,
    result,
    record,
    source,
    system,
    taskset_metadata,
):
    case_id = case["case_id"]
    if result.get("run_id") != case_id:
        _add_issue(case, "audit_failure", "run_id_mismatch")
    scheduler = result.get("configured_scheduler")
    if not isinstance(scheduler, str) or scheduler not in SCHEDULERS:
        _add_issue(case, "audit_failure", "unknown_scheduler", scheduler)
    expected_scheduler = ALGORITHM_CLI_MAPPING.get(case.get("algorithm"))
    if expected_scheduler is not None and scheduler != expected_scheduler:
        _add_issue(case, "audit_failure", "scheduler_mismatch", scheduler)
    task_count = _result_task_count(result)
    if task_count != 10:
        _add_issue(case, "audit_failure", "task_count_mismatch", task_count)
    duration = _expected_duration(record, result)
    try:
        observed_duration = int(result.get("observed_simulation_end_ms"))
    except (TypeError, ValueError):
        observed_duration = None
    if duration is None or observed_duration != duration:
        _add_issue(
            case,
            "audit_failure",
            "duration_mismatch",
            f"{observed_duration}/{duration}",
        )
    required_schema = 3 if _schema3_required(record) else 2
    observed_schema = result.get("trace_schema_version")
    if type(observed_schema) is not int or observed_schema != required_schema:
        _add_issue(
            case,
            "audit_failure",
            "trace_schema_mismatch",
            f"{observed_schema}/{required_schema}",
        )
    if required_schema == 3:
        expected_contract_version = (
            record.get("observability_summary_contract_version")
            if isinstance(record, dict) else None
        )
        try:
            expected_contract = observability.contract_identity(
                expected_contract_version
            )
        except observability.ObservabilityValidationError:
            expected_contract = None
        expected_summary_horizon = (
            record.get("summary_horizon_ms")
            if isinstance(record, dict)
            else None
        )
        if expected_summary_horizon is None and isinstance(record, dict):
            expected_summary_horizon = record.get(
                "observability_summary_horizon_ms"
            )
        contract_identity_valid = (
            expected_contract is not None
            and isinstance(record, dict)
            and record.get("observability_contract_ref")
            == expected_contract["path"].name
            and record.get("observability_contract_sha256")
            == expected_contract["sha256"]
            and record.get(
                "observability_summary_contract_version"
            )
            == expected_contract["contract"]["contract_version"]
            and record.get("trace_schema_version")
            == expected_contract["contract"]["trace_schema_version"]
            and expected_summary_horizon == duration
        )
        if not contract_identity_valid:
            _add_issue(
                case,
                "audit_failure",
                "observability_contract_identity_mismatch",
            )
        else:
            try:
                case["observability_summary"] = (
                    observability.validate_schema3_summary(
                        result,
                        expected_horizon_ms=duration,
                        initial_energy_j=source.get("E0_j"),
                        capacity_j=source.get("Emax_j"),
                        processor_count=system.get("processors"),
                        expected_task_ranks=taskset_metadata["ranks"],
                        taskset_document=taskset_metadata["document"],
                        expected_contract_version=expected_contract_version,
                    )
                )
            except (
                observability.ObservabilityValidationError,
                TypeError,
            ) as exc:
                _add_issue(
                    case,
                    "audit_failure",
                    "observability_summary_invalid",
                    exc,
                )
    expected_semantic = _expected_semantic_hash(record)
    observed_semantic = result.get("taskset_semantic_hash")
    if (
        not isinstance(observed_semantic, str)
        or not observed_semantic
        or (
            expected_semantic is not None
            and observed_semantic != expected_semantic
        )
    ):
        _add_issue(
            case,
            "infrastructure_failure",
            "semantic_hash_error",
        )

    emax = source.get("Emax_j")
    for path, raw in _walk_numbers(result):
        value = float(raw)
        if not math.isfinite(value):
            _add_issue(
                case,
                "infrastructure_failure",
                "invalid_numeric",
                path,
            )
            continue
        lowered = path.lower()
        if "energy" in lowered and value < 0:
            _add_issue(
                case,
                "infrastructure_failure",
                "negative_energy",
                path,
            )
        is_battery = "energy" in lowered and any(
            token in lowered
            for token in ("current", "available", "battery", "residual")
        ) and not any(
            token in lowered
            for token in ("_ticks", "_intervals")
        )
        if is_battery and isinstance(emax, (int, float)):
            if "mj" in lowered:
                outside = not smoke.legacy_trace_battery_mj_is_valid(
                    value, emax
                )
            else:
                outside = value > float(emax) + 1e-8
            if outside:
                _add_issue(
                    case,
                    "infrastructure_failure",
                    "battery_out_of_bounds",
                    path,
                )

    events = result.get("events")
    if isinstance(events, list):
        misses = sum(
            isinstance(event, dict)
            and event.get("event_type") in {"dline_miss", "deadline_miss"}
            for event in events
        )
        if misses:
            _add_issue(
                case,
                "scheduling_outcome",
                "deadline_miss",
                misses,
            )
    if required_schema == 3 and isinstance(
        result.get("per_task_summary"), list
    ):
        unfinished = sum(
            item.get("unfinished_at_horizon_jobs", 0)
            for item in result["per_task_summary"]
            if isinstance(item, dict)
            and type(item.get("unfinished_at_horizon_jobs")) is int
        )
        if unfinished:
            _add_issue(
                case,
                "scheduling_outcome",
                "unfinished_at_horizon",
                unfinished,
            )
    incomplete = result.get("incomplete_jobs")
    if isinstance(incomplete, (int, float)) and incomplete > 0:
        _add_issue(
            case,
            "scheduling_outcome",
            "incomplete_jobs",
            incomplete,
        )
    if result.get("schedulable") is False:
        _add_issue(
            case,
            "scheduling_outcome",
            "schedulability_failure",
        )
    ratio = result.get("acceptance_ratio")
    if isinstance(ratio, (int, float)) and ratio == 0:
        _add_issue(
            case,
            "scheduling_outcome",
            "zero_acceptance_ratio",
        )
    return {
        "scheduler": scheduler,
        "task_count": task_count,
        "duration_ms": duration,
        "semantic_hash": observed_semantic,
    }


def _diagnostic_check(case, root, state, result=None):
    texts = [] if result is None else [manifest.compact_json(result)]
    for stream in ("stdout", "stderr"):
        try:
            path = _safe_path(
                root,
                f".b4pe/logs/{state['case_id']}.{stream}",
            )
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except (execution.ExecutionError, OSError, KeyError):
            continue
    if FAILURE_PATTERN.search("\n".join(texts)):
        _add_issue(
            case,
            "infrastructure_failure",
            "parser_preflight_error",
        )


def _case_pairing_metadata(
    case,
    state,
    record,
    result_metadata,
    source_metadata,
    system_metadata,
):
    provenance = record.get("provenance", {}) if record else {}
    semantic = result_metadata.get("semantic_hash")
    taskset_identity = (
        record.get("taskset_id")
        if record is not None
        else None
    ) or semantic or state.get("taskset_snapshot_sha256")
    return {
        "group_id": taskset_identity,
        "algorithm": case.get("algorithm"),
        "raw_taskset_sha256": provenance.get(
            "taskset_raw_sha256",
            state.get("taskset_artifact_sha256"),
        ),
        "materialized_taskset_sha256": state.get(
            "taskset_artifact_sha256"
        ),
        "taskset_snapshot_sha256": state.get(
            "taskset_snapshot_sha256"
        ),
        "semantic_hash": semantic,
        "source_original_sha256": state.get(
            "source_observed_original_sha256"
        ),
        "source_snapshot_sha256": state.get("source_snapshot_sha256"),
        "E0_j": source_metadata.get("E0_j"),
        "Emax_j": source_metadata.get("Emax_j"),
        "alpha_w": source_metadata.get("alpha_w"),
        "source_profile": source_metadata.get("source_profile"),
        "duration_ms": result_metadata.get("duration_ms"),
        "processors": system_metadata.get("processors"),
        "generator_sha256": provenance.get(
            "generator_sha256",
            state.get("generator_sha256"),
        ),
        "simulator_binary_sha256": state.get(
            "simulator_binary_sha256"
        ),
        "normalized_system": system_metadata.get("normalized_system"),
    }


def _audit_case(state_path, root, state, record_entry):
    record = record_entry["record"] if record_entry is not None else None
    case = {
        "case_id": state.get("case_id", state_path.stem),
        "state_path": str(state_path),
        "output_root": str(root),
        "algorithm": state.get("algorithm"),
        "status": state.get("current_status"),
        "result_relpath": state.get("result_relpath"),
        "issues": [],
    }
    algorithm = case.get("algorithm")
    if algorithm not in ALGORITHMS:
        _add_issue(case, "audit_failure", "unknown_algorithm", algorithm)
    if record is not None and record.get("algorithm") != algorithm:
        _add_issue(
            case,
            "audit_failure",
            "algorithm_mismatch",
            f"{algorithm}/{record.get('algorithm')}",
        )
    if record is not None:
        expected_execution_protocol = (
            execution.EXECUTION_PROTOCOL_V3_SHA256
            if record.get("schema_version")
            in {3, "b4-pe-integration-smoke-v3"}
            else execution.EXECUTION_PROTOCOL_SHA256
        )
        if state.get("execution_protocol_sha256") != expected_execution_protocol:
            _add_issue(
                case,
                "audit_failure",
                "execution_protocol_identity_mismatch",
            )
    if case["status"] != "succeeded":
        _add_issue(
            case,
            "infrastructure_failure",
            "case_not_succeeded",
            case["status"],
        )

    attempts = state.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempt = attempts[-1] if attempts else {}
    exit_code = attempt.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        _add_issue(case, "infrastructure_failure", "nonzero_exit", exit_code)
    if (
        case["status"] == "timed_out"
        or (
            case["status"] == "failed"
            and attempt.get("termination_reason") == "timeout"
        )
    ):
        _add_issue(
            case,
            "infrastructure_failure",
            "timeout_exhausted",
        )

    result = None
    result_path = None
    relative = case.get("result_relpath")
    if isinstance(relative, str):
        try:
            result_path = _safe_path(root, relative)
        except (execution.ExecutionError, OSError, TypeError) as exc:
            _add_issue(case, "audit_failure", "malformed_metadata", exc)
    if case["status"] == "succeeded":
        if (
            result_path is None
            or not result_path.exists()
            or result_path.is_symlink()
        ):
            _add_issue(case, "infrastructure_failure", "missing_result")
        else:
            try:
                metadata = result_path.stat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
                    raise ValueError("result is not a non-empty regular file")
                result = _load_json(result_path)
                if not isinstance(result, dict):
                    raise ValueError("result is not a JSON object")
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                _add_issue(
                    case,
                    "infrastructure_failure",
                    "corrupt_result",
                    exc,
                )
        if result_path is not None and result is not None:
            source_metadata = _source_metadata(case, root, state)
            scheduler = result.get("configured_scheduler")
            system_metadata = _system_metadata(
                case,
                root,
                state,
                scheduler,
            )
            task_ranks = (
                _taskset_ranks(case, root, state)
                if _schema3_required(record)
                else None
            )
            result_metadata = _classify_result(
                case,
                result,
                record,
                source_metadata,
                system_metadata,
                task_ranks,
            )
            case["pairing"] = _case_pairing_metadata(
                case,
                state,
                record,
                result_metadata,
                source_metadata,
                system_metadata,
            )
            case["scheduler"] = result_metadata.get("scheduler")
    elif result_path is not None and result_path.exists():
        _add_issue(
            case,
            "audit_failure",
            "failed_case_has_result",
        )

    _diagnostic_check(case, root, state, result)
    case["issues"] = _sort_issues(case["issues"])
    case["classifications"] = _classification_list(case["issues"])
    return case


def _inspect_roots(roots):
    summaries = []
    issues = []
    for root in sorted(roots, key=str):
        try:
            summary = inspect_execution.inspect_output(root)
        except (execution.ExecutionError, manifest.ManifestError, OSError) as exc:
            issues.append(
                _new_issue(
                    "infrastructure_failure",
                    "inspect_failed",
                    f"{root}: {exc}",
                )
            )
            continue
        summaries.append(
            {
                "output_root": str(root),
                "summary": summary,
                "integrity_errors": (
                    inspect_execution.inspection_has_integrity_errors(
                        summary
                    )
                ),
            }
        )
        for counter in inspect_execution.INTEGRITY_ERROR_COUNTERS:
            count = summary.get(counter, 0)
            if count:
                issues.append(
                    _new_issue(
                        "infrastructure_failure",
                        f"inspect_{counter}",
                        f"{root}:{count}",
                    )
                )
    return summaries, _sort_issues(issues)


def _orphan_results(output_root, known_results):
    directories = {
        path.parent for path in known_results
    }
    directories.update(
        path
        for path in output_root.rglob("results")
        if path.is_dir() and ".b4pe" not in path.parts
    )
    found = set()
    for directory in sorted(directories, key=str):
        if not directory.exists() or not _is_within(directory, output_root):
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink():
                resolved = path.resolve()
                if resolved not in known_results:
                    found.add(resolved)
    return sorted(str(path) for path in found)


def _pairing_groups(cases, strict):
    grouped = defaultdict(list)
    for case in cases:
        pairing = case.get("pairing")
        if isinstance(pairing, dict) and pairing.get("group_id"):
            grouped[str(pairing["group_id"])].append(case)
    fields = (
        "raw_taskset_sha256",
        "materialized_taskset_sha256",
        "taskset_snapshot_sha256",
        "semantic_hash",
        "source_original_sha256",
        "source_snapshot_sha256",
        "E0_j",
        "Emax_j",
        "alpha_w",
        "source_profile",
        "duration_ms",
        "processors",
        "generator_sha256",
        "simulator_binary_sha256",
        "normalized_system",
    )
    groups = []
    mismatches = []
    incomplete = []
    algorithm_order = {name: index for index, name in enumerate(ALGORITHMS)}
    for group_id in sorted(grouped):
        members = sorted(
            grouped[group_id],
            key=lambda case: (
                algorithm_order.get(case.get("algorithm"), len(ALGORITHMS)),
                case["case_id"],
                case["state_path"],
            ),
        )
        algorithms = [case.get("algorithm") for case in members]
        algorithm_counts = Counter(algorithms)
        coverage = [
            name for name in ALGORITHMS if name in set(algorithms)
        ]
        missing = [name for name in ALGORITHMS if name not in set(algorithms)]
        duplicates = sorted(
            str(name) for name, count in algorithm_counts.items()
            if count > 1
        )
        unknown_algorithms = sorted(
            str(name) for name in algorithm_counts
            if name not in ALGORITHMS
        )
        schedulers = [case.get("scheduler") for case in members]
        scheduler_counts = Counter(schedulers)
        scheduler_coverage = [
            name for name in SCHEDULERS if name in scheduler_counts
        ]
        missing_schedulers = [
            name for name in SCHEDULERS if name not in scheduler_counts
        ]
        duplicate_schedulers = sorted(
            str(name) for name, count in scheduler_counts.items()
            if count > 1
        )
        unknown_schedulers = sorted(
            str(name) for name in scheduler_counts
            if name not in SCHEDULERS
        )
        group = {
            "group_id": group_id,
            "case_ids": [case["case_id"] for case in members],
            "algorithm_coverage": coverage,
            "missing_algorithms": missing,
            "duplicate_algorithms": duplicates,
            "unknown_algorithms": unknown_algorithms,
            "scheduler_coverage": scheduler_coverage,
            "missing_schedulers": missing_schedulers,
            "duplicate_schedulers": duplicate_schedulers,
            "unknown_schedulers": unknown_schedulers,
            "issues": [],
        }
        if (
            missing
            or duplicates
            or unknown_algorithms
            or missing_schedulers
            or duplicate_schedulers
            or unknown_schedulers
        ):
            incomplete.append(
                {
                    "group_id": group_id,
                    "missing_algorithms": missing,
                    "duplicate_algorithms": duplicates,
                    "unknown_algorithms": unknown_algorithms,
                    "missing_schedulers": missing_schedulers,
                    "duplicate_schedulers": duplicate_schedulers,
                    "unknown_schedulers": unknown_schedulers,
                }
            )
        if unknown_algorithms:
            _add_issue(
                group,
                "audit_failure",
                "unknown_algorithm",
                ",".join(unknown_algorithms),
            )
        if duplicates:
            _add_issue(
                group,
                "audit_failure",
                "duplicate_algorithm",
                ",".join(duplicates),
            )
        if unknown_schedulers:
            _add_issue(
                group,
                "audit_failure",
                "unknown_scheduler",
                ",".join(unknown_schedulers),
            )
        if strict and missing:
            _add_issue(
                group,
                "audit_failure",
                "incomplete_algorithm_group",
                ",".join(missing),
            )
        if strict and (
            missing_schedulers
            or duplicate_schedulers
            or unknown_schedulers
        ):
            detail = manifest.compact_json(
                {
                    "duplicate": duplicate_schedulers,
                    "missing": missing_schedulers,
                    "unknown": unknown_schedulers,
                }
            )
            _add_issue(
                group,
                "audit_failure",
                "scheduler_coverage_mismatch",
                detail,
            )
        for field in fields:
            values = {
                manifest.compact_json(
                    case["pairing"].get(field)
                )
                for case in members
            }
            if "null" in values:
                _add_issue(
                    group,
                    "audit_failure",
                    "malformed_metadata",
                    field,
                )
            if len(values) > 1:
                mismatch = {"group_id": group_id, "field": field}
                mismatches.append(mismatch)
                _add_issue(
                    group,
                    "audit_failure",
                    "pairing_mismatch",
                    field,
                )
        group["issues"] = _sort_issues(group["issues"])
        group["classifications"] = _classification_list(group["issues"])
        groups.append(group)
    return (
        groups,
        sorted(
            incomplete,
            key=lambda item: item["group_id"],
        ),
        sorted(
            mismatches,
            key=lambda item: (item["group_id"], item["field"]),
        ),
    )


def _issue_subject_count(classification, cases, groups, global_issues):
    return (
        sum(classification in case["classifications"] for case in cases)
        + sum(classification in group["classifications"] for group in groups)
        + sum(
            issue["classification"] == classification
            for issue in global_issues
        )
    )


def audit_output(
    output_root,
    expected_records=None,
    strict=False,
    audit_timestamp=None,
):
    """Audit one output tree without modifying it and return a stable report."""
    output_root = _absolute_directory(output_root, "output-root")
    expected, expected_errors, manifest_audit = load_expected_records(
        expected_records,
        output_root,
    )
    expected_by_case = defaultdict(list)
    for entry in expected:
        expected_by_case[entry["record"].get("case_id")].append(entry)

    state_paths = _discover_state_paths(output_root)
    observed_case_ids = []
    observed_result_paths = []
    cases = []
    roots = set()
    for state_path in state_paths:
        root = _state_root(state_path)
        roots.add(root)
        try:
            state = _load_json(state_path)
            if not isinstance(state, dict):
                raise ValueError("state is not a JSON object")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            case = {
                "case_id": state_path.stem,
                "state_path": str(state_path),
                "output_root": str(root),
                "algorithm": None,
                "status": None,
                "result_relpath": None,
                "issues": [
                    _new_issue(
                        "audit_failure",
                        "malformed_metadata",
                        exc,
                    )
                ],
            }
            case["classifications"] = ["audit_failure"]
            cases.append(case)
            continue
        case_id = state.get("case_id")
        observed_case_ids.append(case_id)
        observed_result_paths.append(
            (str(root), state.get("result_relpath"))
        )
        record_entries = expected_by_case.get(case_id, [])
        record_entry = record_entries[0] if record_entries else None
        cases.append(
            _audit_case(
                state_path,
                root,
                state,
                record_entry,
            )
        )

    for case_id in sorted(expected_by_case, key=lambda value: str(value)):
        entries = expected_by_case[case_id]
        if case_id not in observed_case_ids:
            entry = entries[0]
            case = {
                "case_id": case_id,
                "state_path": None,
                "output_root": entry["output_root"],
                "algorithm": entry["record"].get("algorithm"),
                "status": None,
                "result_relpath": entry["record"].get("result_relpath"),
                "issues": [
                    _new_issue(
                        "infrastructure_failure",
                        "missing_state",
                    )
                ],
                "classifications": ["infrastructure_failure"],
            }
            cases.append(case)

    cases.sort(
        key=lambda case: (
            str(case["case_id"]),
            str(case.get("state_path")),
        )
    )
    inspect_summaries, inspect_issues = _inspect_roots(roots)
    global_issues = list(expected_errors) + inspect_issues

    state_duplicates = {
        value for value, count in Counter(observed_case_ids).items()
        if count > 1
    }
    expected_duplicates = {
        value for value, entries in expected_by_case.items()
        if len(entries) > 1
    }
    duplicate_case_ids = sorted(
        str(value) for value in state_duplicates | expected_duplicates
    )
    for value in duplicate_case_ids:
        global_issues.append(
            _new_issue("audit_failure", "duplicate_case_id", value)
        )

    expected_results = [
        (entry["output_root"], entry["record"].get("result_relpath"))
        for entry in expected
    ]
    state_result_duplicates = {
        value for value, count in Counter(observed_result_paths).items()
        if count > 1
    }
    expected_result_duplicates = {
        value for value, count in Counter(expected_results).items()
        if count > 1
    }
    duplicate_result_paths = sorted(
        f"{root}/{relative}"
        for root, relative in (
            state_result_duplicates | expected_result_duplicates
        )
    )
    for value in duplicate_result_paths:
        global_issues.append(
            _new_issue("audit_failure", "duplicate_result_path", value)
        )

    known_results = set()
    for root, relative in set(observed_result_paths + expected_results):
        if isinstance(root, str) and isinstance(relative, str):
            try:
                known_results.add(
                    _safe_path(Path(root), relative).resolve(strict=False)
                )
            except (execution.ExecutionError, OSError, TypeError):
                continue
    orphan_artifacts = _orphan_results(output_root, known_results)
    for value in orphan_artifacts:
        global_issues.append(
            _new_issue("audit_failure", "orphan_artifact", value)
        )

    groups, incomplete_groups, pairing_mismatches = _pairing_groups(
        cases,
        strict,
    )
    global_issues = _sort_issues(global_issues)
    scheduler_counts = dict(
        sorted(
            Counter(
                case.get("scheduler")
                for case in cases
                if isinstance(case.get("scheduler"), str)
            ).items()
        )
    )
    succeeded_count = sum(case.get("status") == "succeeded" for case in cases)
    case_count = len(cases)
    failed_count = case_count - succeeded_count
    infrastructure_count = _issue_subject_count(
        "infrastructure_failure",
        cases,
        groups,
        global_issues,
    )
    scheduling_count = _issue_subject_count(
        "scheduling_outcome",
        cases,
        groups,
        global_issues,
    )
    audit_count = _issue_subject_count(
        "audit_failure",
        cases,
        groups,
        global_issues,
    )

    def cases_with(code):
        return sorted(
            case["case_id"]
            for case in cases
            if any(issue["code"] == code for issue in case["issues"])
        )

    sha_mismatches = []
    provenance_errors = []
    unfinished_publications = []
    for inspected in inspect_summaries:
        root = inspected["output_root"]
        summary = inspected["summary"]
        if summary.get("sha_mismatches", 0):
            sha_mismatches.append(
                f"{root}:{summary['sha_mismatches']}"
            )
        provenance_total = sum(
            summary.get(name, 0)
            for name in (
                "input_fingerprint_drift",
                "snapshot_missing",
                "snapshot_provenance_drift",
                "snapshot_sha_mismatches",
            )
        )
        if provenance_total:
            provenance_errors.append(f"{root}:{provenance_total}")
        unfinished_count = summary.get(
            "unfinished_publication_transactions",
            0,
        )
        if unfinished_count:
            unfinished_publications.append(f"{root}:{unfinished_count}")

    timestamp = audit_timestamp
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    _require_input(
        isinstance(timestamp, str) and timestamp,
        "audit timestamp must be a non-empty string",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "audited_output_root": str(output_root),
        "audit_timestamp": timestamp,
        "strict": bool(strict),
        "case_count": case_count,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "infrastructure_failure_count": infrastructure_count,
        "scheduling_outcome_count": scheduling_count,
        "audit_failure_count": audit_count,
        "duplicate_case_ids": duplicate_case_ids,
        "duplicate_result_paths": duplicate_result_paths,
        "missing_results": cases_with("missing_result"),
        "corrupt_results": cases_with("corrupt_result"),
        "sha_mismatches": sorted(set(sha_mismatches)),
        "provenance_errors": sorted(set(provenance_errors)),
        "unfinished_publications": sorted(set(unfinished_publications)),
        "orphan_artifacts": orphan_artifacts,
        "unknown_schedulers": sorted(
            {
                str(issue.get("detail"))
                for case in cases
                for issue in case["issues"]
                if issue["code"] == "unknown_scheduler"
            }
        ),
        "pairing_group_count": len(groups),
        "incomplete_algorithm_groups": incomplete_groups,
        "pairing_mismatches": pairing_mismatches,
        "scheduler_counts": scheduler_counts,
        "per_case": cases,
        "per_pairing_group": groups,
        "inspect_summaries": inspect_summaries,
        "manifest_audit": manifest_audit,
        "global_issues": global_issues,
        "overall_pass": infrastructure_count == 0 and audit_count == 0,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-records")
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        output_root = _absolute_directory(args.output_root, "output-root")
        report_path = _report_path(args.report, output_root)
        report = audit_output(
            output_root,
            expected_records=args.expected_records,
            strict=args.strict,
        )
        report_path.write_text(
            manifest.compact_json(report) + "\n",
            encoding="utf-8",
        )
    except (
        AuditInputError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        print(f"result audit input failed: {exc}", file=sys.stderr)
        return 2
    print(
        manifest.compact_json(
            {
                "audit_failure_count": report["audit_failure_count"],
                "infrastructure_failure_count": report[
                    "infrastructure_failure_count"
                ],
                "overall_pass": report["overall_pass"],
                "report": str(report_path),
                "scheduling_outcome_count": report[
                    "scheduling_outcome_count"
                ],
            }
        )
    )
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
