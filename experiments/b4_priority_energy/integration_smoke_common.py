#!/usr/bin/env python3
"""Strict, fixed validation for one non-campaign integration-smoke record."""

import hashlib
import json
import math
import re
import stat
from pathlib import Path, PurePosixPath


PROTOCOL_PATH = Path(__file__).with_name("integration_smoke_protocol_v1.json")
REPO_ROOT = Path(__file__).resolve().parents[2]
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CASE_ID_PATTERN = re.compile(r"smoke-[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?")


class IntegrationSmokeError(ValueError):
    """The integration-smoke record violates its fixed gateway contract."""


def _require(condition, message):
    if not condition:
        raise IntegrationSmokeError(message)


def compact_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_protocol(path=PROTOCOL_PATH):
    with Path(path).open("r", encoding="utf-8") as handle:
        protocol = json.load(handle)
    expected_top_level = {
        "protocol_name",
        "schema_version",
        "record_type",
        "fixed_identity",
        "record_fields",
        "provenance_fields",
        "path_rules",
        "command_rules",
        "retry_rules",
        "gateway_rules",
    }
    _require(set(protocol) == expected_top_level, "smoke protocol fields mismatch")
    _require(
        protocol["schema_version"] == "b4-pe-integration-smoke-v1",
        "smoke schema mismatch",
    )
    _require(protocol["record_type"] == "integration_smoke", "record type mismatch")
    _require(
        len(protocol["record_fields"]) == len(set(protocol["record_fields"])),
        "duplicate smoke record field",
    )
    _require(
        len(protocol["provenance_fields"])
        == len(set(protocol["provenance_fields"])),
        "duplicate smoke provenance field",
    )
    _require(
        protocol["gateway_rules"]
        == {
            "campaign_summary": False,
            "formal_validator_for_smoke": False,
            "identity_protocol_mutation": False,
            "production_validator_injection": False,
            "smoke_validator_for_formal": False,
            "validated_case_count": 1,
        },
        "smoke gateway rules mismatch",
    )
    return protocol


PROTOCOL = _load_protocol()
PROTOCOL_SHA256 = file_sha256(PROTOCOL_PATH)


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_record_file(path):
    raw = Path(path)
    _require(raw.is_absolute(), "integration-smoke record path must be absolute")
    _require(not raw.is_symlink(), "integration-smoke record must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise IntegrationSmokeError("integration-smoke record is unreadable") from exc
    _require(stat.S_ISREG(metadata.st_mode), "integration-smoke record is not regular")
    _require(not _is_within(resolved, REPO_ROOT), "integration-smoke record must be outside repository")
    return resolved


def _load_single_record(path):
    record_path = _validate_record_file(path)
    try:
        lines = [
            line
            for line in record_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError) as exc:
        raise IntegrationSmokeError("integration-smoke record is unreadable") from exc
    _require(len(lines) == 1, "integration-smoke file must contain exactly one record")
    try:
        record = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise IntegrationSmokeError("integration-smoke record is invalid JSON") from exc
    _require(isinstance(record, dict), "integration-smoke record must be an object")
    return record_path, record


def _validate_relative_path(value, field):
    _require(isinstance(value, str) and value, f"{field} must be a non-empty string")
    path = PurePosixPath(value)
    _require(not path.is_absolute(), f"{field} must be relative")
    _require(
        value == path.as_posix()
        and all(part not in {"", ".", ".."} for part in path.parts),
        f"{field} must be canonical relative",
    )
    return value


def _validate_output_root(value):
    _require(isinstance(value, str) and value, "output_root must be a string")
    raw = Path(value)
    _require(raw.is_absolute(), "output_root must be absolute")
    resolved = raw.resolve(strict=False)
    _require(not _is_within(resolved, REPO_ROOT), "output_root must be outside repository")
    return str(resolved)


def _validate_retry(record):
    policy = record["retry_policy"]
    expected_fields = {
        "initial_timeout_seconds",
        "max_attempts",
        "on_final_failure",
        "retry_on",
        "retry_timeout_seconds",
    }
    _require(isinstance(policy, dict) and set(policy) == expected_fields, "retry fields mismatch")
    timeout = record["timeout_seconds"]
    _require(
        isinstance(timeout, (int, float))
        and not isinstance(timeout, bool)
        and math.isfinite(timeout)
        and timeout > 0,
        "timeout_seconds must be finite and positive",
    )
    _require(policy["initial_timeout_seconds"] == timeout, "initial timeout mismatch")
    _require(
        type(policy["max_attempts"]) is int
        and 1 <= policy["max_attempts"] <= PROTOCOL["retry_rules"]["max_attempts"],
        "max_attempts must be one or two",
    )
    _require(policy["retry_on"] == ["timeout"], "retry_on must be timeout only")
    _require(policy["on_final_failure"] == "fail_closed", "final failure must fail closed")
    retry_timeout = policy["retry_timeout_seconds"]
    _require(
        isinstance(retry_timeout, (int, float))
        and not isinstance(retry_timeout, bool)
        and math.isfinite(retry_timeout)
        and retry_timeout > 0,
        "retry timeout must be finite and positive",
    )


def _validate_provenance(record):
    provenance = record["provenance"]
    _require(
        isinstance(provenance, dict)
        and set(provenance) == set(PROTOCOL["provenance_fields"]),
        "provenance fields mismatch",
    )
    generator_path = provenance["generator_path"]
    _require(
        isinstance(generator_path, str) and Path(generator_path).is_absolute(),
        "generator_path must be absolute",
    )
    generator_argv = provenance["generator_argv"]
    _require(
        isinstance(generator_argv, list)
        and generator_argv
        and all(isinstance(item, str) for item in generator_argv),
        "generator_argv must be a non-empty string array",
    )
    for field in set(PROTOCOL["provenance_fields"]) - {
        "generator_path",
        "generator_argv",
    }:
        value = provenance[field]
        _require(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value),
            f"{field} must be lowercase SHA-256",
        )


def _validate_command(record):
    argv = record["command_argv"]
    _require(
        isinstance(argv, list)
        and argv
        and all(isinstance(item, str) and item for item in argv),
        "command_argv must be a non-empty string array",
    )
    _require(not any(item in {"sh", "bash", "-c", "shell=True"} for item in argv), "shell execution is forbidden")
    for field in ("simulator_path", "system_config_path", "taskset_path", "result_relpath"):
        _require(argv.count(record[field]) == 1, f"{field} must occur exactly once in command_argv")
    _require(argv[0] == record["simulator_path"], "simulator_path must be argv[0]")
    _require(argv.count("--run-id") == 1, "command_argv must contain one --run-id")
    run_index = argv.index("--run-id")
    _require(run_index + 1 < len(argv) and argv[run_index + 1] == record["case_id"], "run-id must equal case_id")
    _require(
        argv.count("--taskset-semantic-hash") == 1,
        "command_argv must contain one semantic hash option",
    )
    hash_index = argv.index("--taskset-semantic-hash")
    _require(
        hash_index + 1 < len(argv)
        and argv[hash_index + 1] == record["provenance"]["taskset_semantic_hash"],
        "semantic hash argument mismatch",
    )
    forbidden = PROTOCOL["path_rules"]["campaign_path_fragments_forbidden"]
    for item in argv:
        lowered = item.lower()
        _require(
            not any(fragment in lowered for fragment in forbidden),
            "formal campaign path is forbidden in smoke command",
        )


def _normalise_case(record):
    normalised = dict(record)
    normalised.update(
        {
            "taskset_artifact_relpath": record["taskset_path"],
            "source_artifact_relpath": record["source_artifact_path"],
            "system_config_artifact_relpath": record["system_config_path"],
        }
    )
    return normalised


def validate_integration_smoke_record(path):
    """Validate one fixed smoke record and return its execution envelope."""
    record_path, record = _load_single_record(path)
    _require(set(record) == set(PROTOCOL["record_fields"]), "smoke record fields mismatch")
    for field, expected in PROTOCOL["fixed_identity"].items():
        _require(record[field] == expected, f"fixed smoke field mismatch: {field}")
    _require(record["schema_version"] == PROTOCOL["schema_version"], "schema version mismatch")
    _require(record["record_type"] == PROTOCOL["record_type"], "record type mismatch")
    _require(
        isinstance(record["case_id"], str)
        and CASE_ID_PATTERN.fullmatch(record["case_id"]),
        "case_id must use smoke- namespace",
    )
    _require(isinstance(record["algorithm"], str) and record["algorithm"], "algorithm must be non-empty")
    simulator = record["simulator_path"]
    _require(isinstance(simulator, str) and Path(simulator).is_absolute(), "simulator_path must be absolute")
    output_root = _validate_output_root(record["output_root"])
    _require(output_root == record["output_root"], "output_root must be resolved canonical")
    for field in (
        "system_config_path",
        "taskset_path",
        "source_artifact_path",
        "result_relpath",
    ):
        _validate_relative_path(record[field], field)
    _require(
        record["result_relpath"].startswith(PROTOCOL["path_rules"]["result_prefix"]),
        "result_relpath must use integration-smoke namespace",
    )
    forbidden = PROTOCOL["path_rules"]["campaign_path_fragments_forbidden"]
    for field in (
        "system_config_path",
        "taskset_path",
        "source_artifact_path",
        "result_relpath",
    ):
        lowered = record[field].lower()
        _require(
            not any(fragment in lowered for fragment in forbidden),
            f"formal campaign path forbidden: {field}",
        )
    _validate_retry(record)
    _validate_provenance(record)
    _validate_command(record)
    return {
        "record_path": str(record_path),
        "output_root": output_root,
        "simulator_path": simulator,
        "records": [_normalise_case(record)],
    }
