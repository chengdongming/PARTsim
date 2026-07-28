#!/usr/bin/env python3
"""Reliable sequential execution support for B4-PE I4B-1."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import manifest_common as manifest
import materialization_common as materialization


B4_DIR = Path(__file__).resolve().parent
EXECUTION_PROTOCOL_V1_PATH = B4_DIR / "execution_protocol_v1.json"
EXECUTION_PROTOCOL_PATH = B4_DIR / "execution_protocol_v2.json"
EXECUTION_PROTOCOL_V3_PATH = B4_DIR / "execution_protocol_v3.json"
EXECUTION_PROTOCOL_V4_PATH = B4_DIR / "execution_protocol_v4.json"
PROC_FD_ROOT = "/proc/self/fd"
SNAPSHOT_ROLES = ("simulator", "system", "taskset", "source")
TRACE_SUFFIXES = (".txt", ".json")
PUBLICATION_ACTIVE = {"prepared", "result_published", "logs_published"}
PUBLICATION_STATUSES = [
    "none",
    "prepared",
    "result_published",
    "logs_published",
    "committed",
]


class ExecutionError(RuntimeError):
    """Base class for fail-closed execution errors."""


class SafetyError(ExecutionError):
    pass


class InputIntegrityError(ExecutionError):
    pass


class PublicationIntegrityError(InputIntegrityError):
    pass


class InfrastructureError(ExecutionError):
    pass


class LockConflictError(ExecutionError):
    pass


class ResumeError(ExecutionError):
    pass


class StagingTraceError(InputIntegrityError):
    def __init__(
        self,
        reason,
        message,
        *,
        observed_final_sha256=None,
        preserve_publication=False,
    ):
        super().__init__(message)
        self.reason = reason
        self.observed_final_sha256 = observed_final_sha256
        self.preserve_publication = preserve_publication


def _require(condition, message, error_type=ExecutionError):
    if not condition:
        raise error_type(message)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(data):
    return hashlib.sha256(data).hexdigest()


def record_sha256(record):
    return bytes_sha256(manifest.compact_json(record).encode("utf-8"))


def load_execution_protocol(path=EXECUTION_PROTOCOL_PATH):
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    if protocol.get("schema_version") == 4:
        required = {
            "candidate_v4_ref", "candidate_v4_sha256", "governance",
            "inherits_execution_protocol_ref",
            "inherits_execution_protocol_sha256",
            "manifest_protocol_ref", "manifest_protocol_sha256",
            "materialization_protocol_ref",
            "materialization_protocol_sha256",
            "protocol_name", "schema_version", "status", "supersedes",
            "taskset_semantic_hash_source",
        }
        _require(set(protocol) == required, "execution v4 protocol fields mismatch")
        _require(
            protocol["inherits_execution_protocol_ref"]
            == EXECUTION_PROTOCOL_V3_PATH.name
            and protocol["inherits_execution_protocol_sha256"]
            == file_sha256(EXECUTION_PROTOCOL_V3_PATH),
            "execution v3 inheritance identity mismatch",
        )
        manifest.load_manifest_protocol(manifest.MANIFEST_PROTOCOL_V4_PATH)
        _require(
            protocol["manifest_protocol_ref"]
            == manifest.MANIFEST_PROTOCOL_V4_PATH.name
            and protocol["manifest_protocol_sha256"]
            == file_sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
            "execution v4 manifest identity mismatch",
        )
        candidate_v4 = B4_DIR / "b4_pe_freeze_candidate_v4.json"
        manifest.load_candidate_v4(candidate_v4)
        _require(
            protocol["candidate_v4_ref"] == candidate_v4.name
            and protocol["candidate_v4_sha256"] == file_sha256(candidate_v4),
            "execution v4 candidate identity mismatch",
        )
        materialization.load_materialization_protocol(
            materialization.MATERIALIZATION_PROTOCOL_PATH
        )
        _require(
            protocol["materialization_protocol_ref"]
            == materialization.MATERIALIZATION_PROTOCOL_PATH.name
            and protocol["materialization_protocol_sha256"]
            == file_sha256(materialization.MATERIALIZATION_PROTOCOL_PATH),
            "execution v4 materialization identity mismatch",
        )
        governance = {
            "formal_runs_authorized": False,
            "negative_control_runs_authorized": False,
            "paper_result_authorized": False,
            "pilot_runs_authorized": False,
        }
        _require(
            protocol["status"] == "draft"
            and protocol["governance"] == governance
            and protocol["supersedes"]
            == {
                "path":
                    "experiments/b4_priority_energy/execution_protocol_v3.json",
                "sha256": file_sha256(EXECUTION_PROTOCOL_V3_PATH),
            }
            and protocol["taskset_semantic_hash_source"]
            == (
                "recomputed_from_verified_rho_specific_execution_snapshot_"
                "and_matched_to_materialization_inventory"
            ),
            "execution v4 draft contract mismatch",
        )
        inherited = load_execution_protocol(EXECUTION_PROTOCOL_V3_PATH)
        inherited.update(protocol)
        return inherited
    if protocol.get("schema_version") == 3:
        required = {
            "candidate_v2_ref", "candidate_v2_sha256",
            "analysis_contract_ref", "analysis_contract_sha256",
            "identity_protocol_ref", "identity_protocol_sha256",
            "inherits_execution_protocol_ref",
            "inherits_execution_protocol_sha256",
            "manifest_protocol_ref", "manifest_protocol_sha256",
            "observability_activation", "observability_contract_ref",
            "observability_contract_sha256",
            "observability_summary_contract_version", "protocol_name",
            "result_audit_policy", "schema_version", "trace_schema_version",
            "minimum_adjudicable_jobs_per_task", "mechanism_fields",
            "jmr_denominator_contract",
        }
        _require(set(protocol) == required, "execution v3 protocol fields mismatch")
        _require(
            protocol["inherits_execution_protocol_ref"]
            == EXECUTION_PROTOCOL_PATH.name
            and protocol["inherits_execution_protocol_sha256"]
            == file_sha256(EXECUTION_PROTOCOL_PATH),
            "execution v2 inheritance identity mismatch",
        )
        _require(
            protocol["manifest_protocol_ref"]
            == manifest.MANIFEST_PROTOCOL_V3_PATH.name
            and protocol["manifest_protocol_sha256"]
            == file_sha256(manifest.MANIFEST_PROTOCOL_V3_PATH)
            and protocol["candidate_v2_ref"] == manifest.CANDIDATE_V2_PATH.name
            and protocol["candidate_v2_sha256"]
            == file_sha256(manifest.CANDIDATE_V2_PATH),
            "execution v3 manifest/candidate identity mismatch",
        )
        _require(
            protocol["observability_contract_ref"]
            == manifest.OBSERVABILITY_CONTRACT_V2_PATH.name
            and protocol["observability_contract_sha256"]
            == file_sha256(manifest.OBSERVABILITY_CONTRACT_V2_PATH)
            and protocol["analysis_contract_ref"]
            == manifest.ANALYSIS_CONTRACT_V2_PATH.name
            and protocol["analysis_contract_sha256"]
            == file_sha256(manifest.ANALYSIS_CONTRACT_V2_PATH),
            "execution v3 contract identity mismatch",
        )
        _require(
            protocol["trace_schema_version"] == 3
            and protocol["observability_summary_contract_version"] == 2
            and protocol["result_audit_policy"]
            == "strict_schema3_observability_v2"
            and protocol["observability_activation"]
            == manifest.PROTOCOL_V3["observability_activation"]
            and protocol["minimum_adjudicable_jobs_per_task"] == 100
            and len(protocol["mechanism_fields"]) == 13
            and protocol["jmr_denominator_contract"]["zero_denominator"] == "NA",
            "execution v3 observability binding mismatch",
        )
        inherited = load_execution_protocol(EXECUTION_PROTOCOL_PATH)
        inherited.update(protocol)
        return inherited
    if protocol.get("schema_version") == 2:
        required = {
            "candidate_v1_ref",
            "candidate_v1_sha256",
            "identity_protocol_ref",
            "identity_protocol_sha256",
            "inherits_execution_protocol_ref",
            "inherits_execution_protocol_sha256",
            "manifest_protocol_ref",
            "manifest_protocol_sha256",
            "observability_activation",
            "observability_contract_ref",
            "observability_contract_sha256",
            "observability_summary_contract_version",
            "protocol_name",
            "result_audit_policy",
            "schema_version",
            "trace_schema_version",
        }
        _require(set(protocol) == required, "execution v2 protocol fields mismatch")
        _require(
            protocol["inherits_execution_protocol_ref"]
            == EXECUTION_PROTOCOL_V1_PATH.name
            and protocol["inherits_execution_protocol_sha256"]
            == file_sha256(EXECUTION_PROTOCOL_V1_PATH),
            "execution v1 inheritance identity mismatch",
        )
        _require(
            protocol["manifest_protocol_ref"]
            == manifest.MANIFEST_PROTOCOL_PATH.name
            and protocol["manifest_protocol_sha256"]
            == file_sha256(manifest.MANIFEST_PROTOCOL_PATH),
            "manifest v2 protocol identity mismatch",
        )
        _require(
            protocol["candidate_v1_ref"]
            == manifest.CANDIDATE_V1_PATH.name
            and protocol["candidate_v1_sha256"]
            == file_sha256(manifest.CANDIDATE_V1_PATH),
            "candidate v1 identity mismatch",
        )
        _require(
            protocol["observability_contract_ref"]
            == manifest.OBSERVABILITY_CONTRACT_PATH.name
            and protocol["observability_contract_sha256"]
            == file_sha256(manifest.OBSERVABILITY_CONTRACT_PATH),
            "observability contract identity mismatch",
        )
        _require(
            protocol["identity_protocol_ref"]
            == manifest.IDENTITY_PROTOCOL_PATH.name
            and protocol["identity_protocol_sha256"]
            == file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
            "identity protocol mismatch",
        )
        _require(
            protocol["trace_schema_version"] == 3
            and protocol["observability_summary_contract_version"] == 1
            and protocol["result_audit_policy"]
            == "strict_schema3_observability_v1"
            and protocol["observability_activation"]
            == manifest.PROTOCOL["observability_activation"],
            "execution v2 observability binding mismatch",
        )
        inherited = load_execution_protocol(
            EXECUTION_PROTOCOL_V1_PATH
        )
        inherited.update(protocol)
        return inherited
    required = {
        "atomic_write_rules",
        "dirfd_safety_rules",
        "identity_protocol_ref",
        "identity_protocol_sha256",
        "input_snapshot_rules",
        "inspection_rules",
        "lock_rules",
        "manifest_protocol_ref",
        "manifest_protocol_sha256",
        "process_group_rules",
        "protocol_name",
        "publication_rules",
        "result_integrity_rules",
        "resume_rules",
        "schema_version",
        "state_schema_version",
        "state_transitions",
        "states",
        "stdout_stderr_rules",
        "subprocess_rules",
        "summary_fields",
        "summary_rules",
        "timeout_retry_rules",
        "trace_staging_rules",
    }
    _require(set(protocol) == required, "execution protocol fields mismatch")
    _require(protocol["schema_version"] == 1, "execution protocol schema mismatch")
    _require(protocol["state_schema_version"] == 1, "state schema mismatch")
    _require(
        protocol["manifest_protocol_ref"]
        == manifest.MANIFEST_PROTOCOL_V1_PATH.name,
        "manifest protocol reference mismatch",
    )
    _require(
        protocol["manifest_protocol_sha256"]
        == file_sha256(manifest.MANIFEST_PROTOCOL_V1_PATH),
        "manifest protocol SHA mismatch",
    )
    _require(
        protocol["identity_protocol_ref"] == manifest.IDENTITY_PROTOCOL_PATH.name,
        "identity protocol reference mismatch",
    )
    _require(
        protocol["identity_protocol_sha256"]
        == file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
        "identity protocol SHA mismatch",
    )
    states = protocol["states"]
    expected_states = [
        "planned",
        "running",
        "succeeded",
        "failed",
        "timed_out",
        "interrupted",
    ]
    _require(states == expected_states, "execution state list mismatch")
    _require(set(protocol["state_transitions"]) == set(states), "state transition mismatch")
    for source, targets in protocol["state_transitions"].items():
        _require(
            isinstance(targets, list)
            and len(targets) == len(set(targets))
            and set(targets) <= set(states),
            f"invalid transitions from {source}",
        )
    _require(
        protocol["publication_rules"]["statuses"] == PUBLICATION_STATUSES,
        "publication statuses mismatch",
    )
    _require(
        protocol["subprocess_rules"]
        == {
            "attempt_trace_path": "direct_/proc/self/fd/<attempt-directory-fd>/<trace-basename>",
            "argv_source": "manifest_command_argv_with_snapshot_substitution",
            "inherited_attempt_directory_descriptors": 1,
            "output_root_rootfd_inherited": False,
            "os_system": False,
            "real_rtsim_end_to_end_validation": "i4b2_first_gate",
            "shell": False,
            "shell_expansion": False,
            "simulator_argv0_source": "direct_inherited_snapshot_file_descriptor",
            "snapshot_input_paths": "direct_/proc/self/fd/<snapshot-file-fd>",
            "snapshot_transport": "inherited_final_file_descriptors",
            "taskset_semantic_hash_source": "upstream_manifest_command_bridge_i4b2",
            "test_argument_hook": False,
            "trace_target_precreated": False,
            "unit_test_campaign_execution": False,
        },
        "subprocess safety rules mismatch",
    )
    _require(
        protocol["trace_staging_rules"]
        == {
            "attempt_directory": ".b4pe/attempt-results/<case-id>/attempt-<index>-<runtime-token>",
            "attempt_directory_mode": "0700",
            "attempt_directory_reuse": False,
            "executor_existing_target_policy": "require_absent_fail_closed_without_popen",
            "orphan_adoption": False,
            "producer": "rtsim_internal_temporary_validate_atomic_publish",
            "publication_boundary": "rtsim_staging_atomic_then_i4b1_final_atomic",
            "retry": "fresh_attempt_directory_and_absent_target",
            "rtsim_existing_target_contract": "absent_or_byte_identical",
            "successful_handoff": "open_O_RDONLY_O_NOFOLLOW_regular_nonempty_sha256",
            "successful_staging_retention": "retain_as_attempt_evidence",
            "target_basename_by_result_suffix": {
                ".json": "trace.json",
                ".txt": "trace.txt",
            },
            "target_must_not_exist_before_popen": True,
            "target_precreation": False,
        },
        "trace staging rules mismatch",
    )
    expected_summary = [
        "manifest_sha256",
        "selected_cases",
        "executed_cases",
        "succeeded",
        "failed",
        "timed_out",
        "interrupted",
        "skipped_succeeded",
        "skipped_failed",
        "lock_conflicts",
        "infrastructure_errors",
    ]
    _require(protocol["summary_fields"] == expected_summary, "summary fields mismatch")
    return protocol


PROTOCOL = load_execution_protocol()
PROTOCOL_V2 = PROTOCOL
PROTOCOL_V3 = load_execution_protocol(EXECUTION_PROTOCOL_V3_PATH)
PROTOCOL_V4 = load_execution_protocol(EXECUTION_PROTOCOL_V4_PATH)
EXECUTION_PROTOCOL_SHA256 = file_sha256(EXECUTION_PROTOCOL_PATH)
EXECUTION_PROTOCOL_V3_SHA256 = file_sha256(EXECUTION_PROTOCOL_V3_PATH)
EXECUTION_PROTOCOL_V4_SHA256 = file_sha256(EXECUTION_PROTOCOL_V4_PATH)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _is_within(path, root):
    return path == root or root in path.parents


def validate_output_root(value, create=True):
    raw = Path(value)
    _require(raw.is_absolute(), "output-root must be absolute", SafetyError)
    _require(not raw.is_symlink(), "output-root must not be a symlink", SafetyError)
    root = raw.resolve(strict=False)
    repo = manifest.REPO_ROOT.resolve(strict=True)
    _require(not _is_within(root, repo), "output-root must be outside repository", SafetyError)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    _require(root.exists() and root.is_dir(), "output-root does not exist", SafetyError)
    _require(not raw.is_symlink(), "output-root must not be a symlink", SafetyError)
    resolved = root.resolve(strict=True)
    _require(not _is_within(resolved, repo), "output-root resolves inside repository", SafetyError)
    return resolved


def _check_no_symlink_components(root, path):
    _require(_is_within(path.resolve(strict=False), root), "path escapes output-root", SafetyError)
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise SafetyError(f"symlink path component is forbidden: {current}")
        if not current.exists():
            break


def safe_output_path(root, relative, create_parent=False):
    """Read-only/display helper; mutation code below always uses trusted dirfds."""
    manifest.validate_relative_path(relative, "execution path")
    path = root.joinpath(*PurePosixPath(relative).parts)
    _check_no_symlink_components(root, path)
    resolved = path.resolve(strict=False)
    _require(_is_within(resolved, root), "resolved path escapes output-root", SafetyError)
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
        _check_no_symlink_components(root, path)
    return path


def _relative_parts(relative):
    _require(isinstance(relative, str), "execution path must be a string", SafetyError)
    _require(not relative.startswith("/"), "execution path must be relative", SafetyError)
    parts = PurePosixPath(relative).parts
    _require(parts and all(part not in {"", ".", ".."} for part in parts), "invalid execution path", SafetyError)
    manifest.validate_relative_path(relative, "execution path")
    return parts


def _open_directory_at(root_fd, parts, create=False):
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        if exc.errno in {getattr(os, "ELOOP", 40), getattr(os, "ENOTDIR", 20)}:
            raise SafetyError("symlink or non-directory path component is forbidden") from exc
        raise
    except BaseException:
        os.close(descriptor)
        raise


def _open_parent_at(context, relative, create=False):
    parts = _relative_parts(relative)
    return _open_directory_at(context["root_fd"], parts[:-1], create=create), parts[-1]


def _proc_fd_path(descriptor):
    _require(type(descriptor) is int and descriptor >= 0, "invalid execution fd", SafetyError)
    try:
        opened = os.fstat(descriptor)
        proc_root = os.stat(PROC_FD_ROOT)
        _require(stat.S_ISDIR(proc_root.st_mode), "/proc/self/fd is unavailable", InfrastructureError)
        path = f"{PROC_FD_ROOT}/{descriptor}"
        through_proc = os.stat(path)
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise InfrastructureError("/proc/self/fd transport is unavailable") from exc
    _require(
        (opened.st_dev, opened.st_ino) == (through_proc.st_dev, through_proc.st_ino),
        "/proc/self/fd does not identify the opened file",
        InfrastructureError,
    )
    return path


def _proc_fd_child_path(descriptor, filename):
    _require(
        isinstance(filename, str)
        and filename not in {"", ".", ".."}
        and "/" not in filename
        and "\\" not in filename,
        "result temporary name must be one path component",
        SafetyError,
    )
    return f"{_proc_fd_path(descriptor)}/{filename}"


def _trace_basename(result_relative):
    suffix = PurePosixPath(result_relative).suffix
    _require(
        suffix in TRACE_SUFFIXES,
        "result trace path must end with .txt or .json",
        SafetyError,
    )
    return f"trace{suffix}"


def _create_attempt_staging(context, case_id, attempt_index, trace_basename):
    _require(
        trace_basename in {"trace.txt", "trace.json"},
        "invalid staging trace basename",
        SafetyError,
    )
    parent_relative = f".b4pe/attempt-results/{case_id}"
    parent_parts = _relative_parts(parent_relative)
    parent = _open_directory_at(context["root_fd"], parent_parts, create=True)
    attempt_name = (
        f"attempt-{attempt_index:04d}-{secrets.token_hex(12)}"
    )
    descriptor = None
    try:
        os.mkdir(attempt_name, 0o700, dir_fd=parent)
        os.fsync(parent)
        descriptor = os.open(
            attempt_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISDIR(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o700,
            "attempt staging directory is not a private directory",
            SafetyError,
        )
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)
    relative = f"{parent_relative}/{attempt_name}"
    return descriptor, relative


def _require_trace_target_absent(directory_fd, trace_basename):
    _require(
        trace_basename in {"trace.txt", "trace.json"},
        "invalid staging trace basename",
        SafetyError,
    )
    try:
        os.stat(trace_basename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise SafetyError("staging trace target already exists before Popen")


def _validate_staging_trace(directory_fd, trace_basename):
    _require(
        trace_basename in {"trace.txt", "trace.json"},
        "invalid staging trace basename",
        SafetyError,
    )
    entries = os.listdir(directory_fd)
    if trace_basename not in entries:
        reason = "missing_result" if not entries else "invalid_result"
        raise StagingTraceError(reason, "staging trace is missing")
    if entries != [trace_basename] and set(entries) != {trace_basename}:
        raise StagingTraceError(
            "invalid_result", "attempt staging directory contains extra targets"
        )
    before = os.stat(trace_basename, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise StagingTraceError(
            "invalid_result", "staging trace is not a regular file"
        )
    try:
        descriptor = os.open(
            trace_basename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise StagingTraceError(
            "invalid_result", "staging trace cannot be opened without following links"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise StagingTraceError(
                "invalid_result", "staging trace changed while opening"
            )
        if opened.st_size == 0:
            raise StagingTraceError("empty_result", "staging trace is empty")
        digest = _sha_from_fd(descriptor)
        after = os.fstat(descriptor)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise StagingTraceError(
                "invalid_result", "staging trace changed while hashing"
            )
        current = os.stat(
            trace_basename, dir_fd=directory_fd, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise StagingTraceError(
                "invalid_result", "staging trace namespace changed while hashing"
            )
        return {
            "sha256": digest,
            "st_dev": opened.st_dev,
            "st_ino": opened.st_ino,
            "st_size": opened.st_size,
            "st_mtime_ns": opened.st_mtime_ns,
            "st_ctime_ns": opened.st_ctime_ns,
        }
    finally:
        os.close(descriptor)


def _require_staging_trace_identity(context, attempt, expected_identity):
    staging_parts = _relative_parts(attempt["staging_directory_relpath"])
    directory = None
    try:
        directory = _open_directory_at(context["root_fd"], staging_parts)
        observed = _validate_staging_trace(
            directory, attempt["staging_trace_basename"]
        )
    except (ExecutionError, OSError) as exc:
        raise StagingTraceError(
            "trace_integrity_error",
            "staging trace cannot be revalidated",
        ) from exc
    finally:
        if directory is not None:
            os.close(directory)
    if observed != expected_identity:
        raise StagingTraceError(
            "trace_integrity_error",
            "staging trace identity changed after child exit",
        )
    return observed


def _sha_from_fd(descriptor):
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _read_bytes_at(context, relative, label, allow_empty=True):
    parent, name = _open_parent_at(context, relative)
    try:
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        try:
            metadata = os.fstat(descriptor)
            _require(stat.S_ISREG(metadata.st_mode), f"{label} is not a regular file", InputIntegrityError)
            _require(allow_empty or metadata.st_size > 0, f"{label} is empty", InputIntegrityError)
            data = bytearray()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
            return bytes(data)
        finally:
            os.close(descriptor)
    except FileNotFoundError as exc:
        raise InputIntegrityError(f"missing {label}: {relative}") from exc
    finally:
        os.close(parent)


def _file_sha_at(context, relative, label, allow_empty=False):
    return bytes_sha256(_read_bytes_at(context, relative, label, allow_empty=allow_empty))


def _lstat_at(context, relative):
    parent, name = _open_parent_at(context, relative)
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def _exists_at(context, relative):
    try:
        _lstat_at(context, relative)
        return True
    except FileNotFoundError:
        return False


def _destination_is_safe(parent_fd, name, allow_existing):
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    _require(stat.S_ISREG(metadata.st_mode), f"final target is not a regular file: {name}", SafetyError)
    _require(allow_existing, f"final target already exists: {name}", InputIntegrityError)
    return True


def _verify_parent_binding(context, parts, trusted_fd):
    current = _open_directory_at(context["root_fd"], parts)
    try:
        trusted = os.fstat(trusted_fd)
        observed = os.fstat(current)
        _require(
            (trusted.st_dev, trusted.st_ino) == (observed.st_dev, observed.st_ino),
            "publication parent changed during operation",
            SafetyError,
        )
    finally:
        os.close(current)


def _atomic_write_at(context, relative, data, mode=0o600):
    parent_parts = _relative_parts(relative)[:-1]
    parent, final_name = _open_parent_at(context, relative, create=True)
    temporary_name = f".{final_name}.{secrets.token_hex(12)}.tmp"
    descriptor = None
    try:
        _destination_is_safe(parent, final_name, allow_existing=True)
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent,
        )
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_name, final_name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        _verify_parent_binding(context, parent_parts, parent)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent)


def _atomic_write_json_at(context, relative, value):
    _atomic_write_at(context, relative, manifest.compact_json(value).encode("utf-8") + b"\n")


def _replace_at(
    context,
    temporary_relative,
    final_relative,
    allow_existing=False,
    expected_sha256=None,
    post_replace_verifier=None,
):
    temporary_parts = _relative_parts(temporary_relative)
    final_parts = _relative_parts(final_relative)
    _require(
        temporary_parts[:-1] == final_parts[:-1],
        "publication replace must stay in one directory",
        SafetyError,
    )
    parent = _open_directory_at(context["root_fd"], temporary_parts[:-1])
    source_descriptor = None
    try:
        try:
            source_descriptor = os.open(
                temporary_parts[-1],
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
        except OSError as exc:
            raise PublicationIntegrityError(
                "publication source cannot be opened"
            ) from exc
        source = os.fstat(source_descriptor)
        _require(
            stat.S_ISREG(source.st_mode),
            "publication source is not regular",
            PublicationIntegrityError,
        )
        if expected_sha256 is not None:
            _require(
                _sha_from_fd(source_descriptor) == expected_sha256,
                "publication source SHA mismatch",
                PublicationIntegrityError,
            )
        _destination_is_safe(
            parent, final_parts[-1], allow_existing=allow_existing
        )
        _before_replace_hook(
            context, temporary_relative, final_relative, parent
        )
        try:
            current = os.stat(
                temporary_parts[-1],
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise PublicationIntegrityError(
                "publication source disappeared during operation"
            ) from exc
        _require(
            (current.st_dev, current.st_ino) == (source.st_dev, source.st_ino),
            "publication source changed during operation",
            PublicationIntegrityError,
        )
        try:
            os.replace(
                temporary_parts[-1],
                final_parts[-1],
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
        except OSError as exc:
            raise PublicationIntegrityError(
                "publication source could not be replaced"
            ) from exc
        os.fsync(parent)
        _verify_parent_binding(context, temporary_parts[:-1], parent)
        if post_replace_verifier is not None:
            return post_replace_verifier(parent, final_parts[-1])
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(parent)


def _unlink_at(context, relative):
    parent, name = _open_parent_at(context, relative)
    try:
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _copy_file_at(context, source_relative, destination_relative, allow_existing=False):
    source_parent, source_name = _open_parent_at(context, source_relative)
    destination_parts = _relative_parts(destination_relative)
    destination_parent, destination_name = _open_parent_at(
        context, destination_relative, create=True
    )
    temporary_name = f".{destination_name}.{secrets.token_hex(12)}.tmp"
    source = None
    temporary = None
    try:
        source = os.open(
            source_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_parent,
        )
        _require(
            stat.S_ISREG(os.fstat(source).st_mode),
            "copy source is not regular",
            InputIntegrityError,
        )
        _destination_is_safe(
            destination_parent, destination_name, allow_existing=allow_existing
        )
        temporary = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=destination_parent,
        )
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                view = view[os.write(temporary, view):]
        os.fsync(temporary)
        os.close(temporary)
        temporary = None
        os.replace(
            temporary_name,
            destination_name,
            src_dir_fd=destination_parent,
            dst_dir_fd=destination_parent,
        )
        os.fsync(destination_parent)
        _verify_parent_binding(context, destination_parts[:-1], destination_parent)
    except BaseException:
        if temporary is not None:
            os.close(temporary)
        try:
            os.unlink(temporary_name, dir_fd=destination_parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        if source is not None:
            os.close(source)
        os.close(source_parent)
        os.close(destination_parent)


def _before_replace_hook(context, temporary_relative, final_relative, parent_fd):
    """Test hook invoked with the trusted publication parent still open."""


def _publication_integrity_hook(stage, record, context, attempt, publication):
    """Test hook around the post-exit trace-integrity checkpoints."""


def _snapshot_copy_hook(role, stage, original_path):
    """Test hook for deterministic input mutation fault injection."""


def _before_popen_hook(record, context):
    """Test hook after immutable snapshots exist and before Popen."""


def _open_root_descriptor(root):
    return os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )


def ensure_layout(root_or_context):
    owns_descriptor = not isinstance(root_or_context, dict)
    if owns_descriptor:
        root = validate_output_root(root_or_context, create=True)
        context = {"root_fd": _open_root_descriptor(root), "output_root": root}
    else:
        context = root_or_context
    try:
        for relative in (
            ".b4pe/state",
            ".b4pe/locks",
            ".b4pe/logs",
            ".b4pe/attempt-results",
            ".b4pe/tmp",
            ".b4pe/summaries",
            ".b4pe/snapshots/simulator",
            ".b4pe/snapshots/system",
            ".b4pe/snapshots/taskset",
            ".b4pe/snapshots/source",
        ):
            descriptor = _open_directory_at(context["root_fd"], _relative_parts(relative), create=True)
            os.close(descriptor)
    finally:
        if owns_descriptor:
            os.close(context["root_fd"])


def _regular_file_sha(path, label, allow_empty=False):
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise InputIntegrityError(f"missing {label}: {path}") from exc
    _require(stat.S_ISREG(metadata.st_mode), f"{label} is not a regular file", InputIntegrityError)
    _require(allow_empty or metadata.st_size > 0, f"{label} is empty", InputIntegrityError)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode)
            and (metadata.st_dev, metadata.st_ino) == (opened.st_dev, opened.st_ino),
            f"{label} changed during validation",
            InputIntegrityError,
        )
        return _sha_from_fd(descriptor)
    finally:
        os.close(descriptor)


def _open_absolute_nofollow(path):
    raw = Path(path)
    _require(raw.is_absolute(), "simulator-binary must be absolute", SafetyError)
    parts = raw.parts[1:]
    _require(parts, "simulator-binary path is invalid", SafetyError)
    parent = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[:-1]:
            next_parent = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
            os.close(parent)
            parent = next_parent
        return os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
    finally:
        os.close(parent)


def validate_simulator_binary(value):
    raw = Path(value)
    _require(raw.is_absolute(), "simulator-binary must be absolute", SafetyError)
    _require(not raw.is_symlink(), "simulator-binary must not be a symlink", SafetyError)
    try:
        descriptor = _open_absolute_nofollow(raw)
    except FileNotFoundError as exc:
        raise SafetyError("simulator-binary does not exist") from exc
    try:
        metadata = os.fstat(descriptor)
        _require(stat.S_ISREG(metadata.st_mode), "simulator-binary is not a file", SafetyError)
        _require(metadata.st_mode & 0o111, "simulator-binary is not executable", SafetyError)
        sha = _sha_from_fd(descriptor)
    finally:
        os.close(descriptor)
    return raw, sha


def _state_relpath(case_id):
    return f".b4pe/state/{case_id}.json"


def _lock_relpath(case_id):
    return f".b4pe/locks/{case_id}.lock"


def _log_relpath(case_id, stream, attempt_index=None):
    if attempt_index is None:
        return f".b4pe/logs/{case_id}.{stream}"
    return f".b4pe/logs/{case_id}.attempt-{attempt_index}.{stream}"


def _unique_temp_relpath(parent_relative, prefix, suffix):
    return f"{parent_relative}/.{prefix}.{secrets.token_hex(12)}.{suffix}.tmp"


def _coerce_context(root_or_context):
    if isinstance(root_or_context, dict):
        return root_or_context, False
    root = validate_output_root(root_or_context, create=False)
    return {"root_fd": _open_root_descriptor(root), "output_root": root}, True


@contextmanager
def case_lock(root_or_context, case_id):
    context, owns_descriptor = _coerce_context(root_or_context)
    parent, name = _open_parent_at(context, _lock_relpath(case_id), create=True)
    try:
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        try:
            _require(stat.S_ISREG(os.fstat(descriptor).st_mode), "lock is not regular", SafetyError)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockConflictError(f"lock conflict for {case_id}") from exc
            yield _lock_relpath(case_id)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
    finally:
        os.close(parent)
        if owns_descriptor:
            os.close(context["root_fd"])


def build_context(
    manifest_path,
    output_root,
    simulator_binary,
    execution_protocol_sha256=EXECUTION_PROTOCOL_SHA256,
):
    manifest_path = Path(manifest_path).resolve(strict=True)
    root = validate_output_root(output_root, create=True)
    root_fd = _open_root_descriptor(root)
    try:
        simulator_path, initial_sha = validate_simulator_binary(simulator_binary)
        context = {
            "manifest_path": str(manifest_path),
            "manifest_sha256": file_sha256(manifest_path),
            "output_root": root,
            "root_fd": root_fd,
            "simulator_binary_path": str(simulator_path),
            "simulator_binary_initial_sha256": initial_sha,
            "execution_protocol_sha256": execution_protocol_sha256,
        }
        ensure_layout(context)
        return context
    except BaseException:
        os.close(root_fd)
        raise


def close_context(context):
    descriptor = context.pop("root_fd", None)
    if descriptor is not None:
        os.close(descriptor)


def _open_artifact(context, relative, label):
    parent, name = _open_parent_at(context, relative)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
    except FileNotFoundError as exc:
        raise InputIntegrityError(f"missing {label}: {relative}") from exc
    finally:
        os.close(parent)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise InputIntegrityError(f"{label} is not a regular file")
    return descriptor


def _stability_fingerprint(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_from_fd(context, role, descriptor, original_path, executable=False):
    directory_relative = f".b4pe/snapshots/{role}"
    parent = _open_directory_at(context["root_fd"], _relative_parts(directory_relative), create=True)
    temporary_name = f".snapshot.{secrets.token_hex(12)}.tmp"
    temporary = None
    try:
        before = os.fstat(descriptor)
        _require(stat.S_ISREG(before.st_mode), f"{role} input is not regular", InputIntegrityError)
        temporary = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500 if executable else 0o400,
            dir_fd=parent,
        )
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                view = view[os.write(temporary, view):]
        os.fsync(temporary)
        copied_sha = digest.hexdigest()
        _snapshot_copy_hook(role, "after_first_read", str(original_path))
        after_first = os.fstat(descriptor)
        _require(
            _stability_fingerprint(before) == _stability_fingerprint(after_first),
            f"{role} input changed during snapshot copy",
            InputIntegrityError,
        )
        second_sha = _sha_from_fd(descriptor)
        after_second = os.fstat(descriptor)
        _require(
            copied_sha == second_sha
            and _stability_fingerprint(before) == _stability_fingerprint(after_second),
            f"{role} input changed during snapshot verification",
            InputIntegrityError,
        )
        os.fchmod(temporary, 0o500 if executable else 0o400)
        os.fsync(temporary)
        os.close(temporary)
        temporary = None
        final_name = copied_sha
        try:
            os.link(
                temporary_name,
                final_name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            os.fsync(parent)
        except FileExistsError:
            existing = os.open(final_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
            try:
                existing_metadata = os.fstat(existing)
                _require(
                    stat.S_ISREG(existing_metadata.st_mode)
                    and existing_metadata.st_size == before.st_size
                    and _sha_from_fd(existing) == copied_sha,
                    f"existing {role} snapshot integrity mismatch",
                    InputIntegrityError,
                )
                _require(
                    not (existing_metadata.st_mode & 0o222)
                    and (not executable or existing_metadata.st_mode & 0o111),
                    f"existing {role} snapshot mode mismatch",
                    InputIntegrityError,
                )
            finally:
                os.close(existing)
        os.unlink(temporary_name, dir_fd=parent)
        os.fsync(parent)
        relative = f"{directory_relative}/{final_name}"
        return {
            "original_path": str(original_path),
            "observed_original_sha256": copied_sha,
            "snapshot_relpath": relative,
            "snapshot_sha256": copied_sha,
            "executed_snapshot_path": str(
                context["output_root"].joinpath(*_relative_parts(relative))
            ),
            "execution_transport": "inherited_file_descriptor",
            "executed_snapshot_sha256": copied_sha,
        }
    except BaseException:
        if temporary is not None:
            os.close(temporary)
        try:
            os.unlink(temporary_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent)


def _snapshot_artifact(context, role, relative):
    descriptor = _open_artifact(context, relative, f"{role} artifact")
    try:
        original_path = context["output_root"].joinpath(*_relative_parts(relative))
        return _snapshot_from_fd(context, role, descriptor, original_path)
    finally:
        os.close(descriptor)


def _snapshot_simulator(context):
    descriptor = _open_absolute_nofollow(context["simulator_binary_path"])
    try:
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_mode & 0o111,
            "simulator-binary is not executable",
            SafetyError,
        )
        return _snapshot_from_fd(
            context,
            "simulator",
            descriptor,
            context["simulator_binary_path"],
            executable=True,
        )
    finally:
        os.close(descriptor)


def open_snapshot_for_execution(context, role, provenance):
    _require(
        role in SNAPSHOT_ROLES or role == "inventory",
        f"unknown snapshot role: {role}",
        SafetyError,
    )
    relative = provenance[f"{role}_snapshot_relpath"]
    expected_sha = provenance[f"{role}_snapshot_sha256"]
    parts = _relative_parts(relative)
    _require(
        parts[:-1] == (".b4pe", "snapshots", role)
        and parts[-1] == expected_sha,
        f"invalid {role} snapshot identity",
        InputIntegrityError,
    )
    parent = _open_directory_at(context["root_fd"], parts[:-1])
    try:
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode),
            f"{role} execution snapshot is not a regular file",
            InputIntegrityError,
        )
        _require(
            role != "simulator" or metadata.st_mode & 0o111,
            "simulator execution snapshot is not executable",
            InputIntegrityError,
        )
        actual_sha = _sha_from_fd(descriptor)
        _require(
            actual_sha == expected_sha
            and actual_sha == provenance[f"{role}_executed_snapshot_sha256"],
            f"{role} execution snapshot SHA mismatch",
            InputIntegrityError,
        )
        os.lseek(descriptor, 0, os.SEEK_SET)
        proc_path = _proc_fd_path(descriptor)
        return {
            "fd": descriptor,
            "proc_fd_path": proc_path,
            "sha256": actual_sha,
            "transport": "inherited_file_descriptor",
        }
    except BaseException:
        os.close(descriptor)
        raise


def _open_execution_snapshots(context, provenance):
    opened = {}
    try:
        for role in SNAPSHOT_ROLES:
            opened[role] = open_snapshot_for_execution(
                context, role, provenance
            )
        return opened
    except BaseException:
        for item in opened.values():
            os.close(item["fd"])
        raise


def _close_execution_snapshots(opened):
    for role in SNAPSHOT_ROLES:
        item = opened.get(role)
        if item is not None:
            os.close(item["fd"])


def _bytes_from_open_fd(descriptor):
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return b"".join(chunks)


def _validate_v4_inventory_bytes(
    inventory_bytes, record, context, taskset_sha, semantic_hash
):
    try:
        inventory = json.loads(inventory_bytes.decode("utf-8"))
        _require(
            inventory_bytes == materialization.canonical_json_bytes(inventory),
            "materialization inventory bytes are not canonical",
            InputIntegrityError,
        )
        _require(
            inventory.get("manifest_file_sha256") == context["manifest_sha256"],
            "materialization inventory manifest identity mismatch",
            InputIntegrityError,
        )
        materialization.validate_inventory_for_record(
            inventory,
            record,
            taskset_sha,
            semantic_hash,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        materialization.MaterializationError,
    ) as exc:
        raise InputIntegrityError(
            f"materialization inventory validation failed: {exc}"
        ) from exc
    return inventory


def _open_and_validate_v4_inventory_snapshot(
    record, context, provenance, taskset_snapshot
):
    inventory_snapshot = open_snapshot_for_execution(
        context, "inventory", provenance
    )
    try:
        inventory_bytes = _bytes_from_open_fd(inventory_snapshot["fd"])
        _require(
            bytes_sha256(inventory_bytes)
            == provenance["materialization_inventory_sha256"],
            "materialization inventory snapshot SHA mismatch",
            InputIntegrityError,
        )
        semantic_hash = materialization.acceptance.taskset_semantic_hash(
            Path(taskset_snapshot["proc_fd_path"])
        )
        _require(
            semantic_hash == provenance["taskset_semantic_hash"],
            "execution taskset snapshot semantic hash mismatch",
            InputIntegrityError,
        )
        _validate_v4_inventory_bytes(
            inventory_bytes,
            record,
            context,
            taskset_snapshot["sha256"],
            semantic_hash,
        )
        return inventory_snapshot
    except BaseException:
        os.close(inventory_snapshot["fd"])
        raise


def build_provenance(record, context):
    snapshots = {
        "simulator": _snapshot_simulator(context),
        "system": _snapshot_artifact(context, "system", record["system_config_artifact_relpath"]),
        "taskset": _snapshot_artifact(context, "taskset", record["taskset_artifact_relpath"]),
        "source": _snapshot_artifact(context, "source", record["source_artifact_relpath"]),
    }
    provenance = {
        "schema_version": PROTOCOL["state_schema_version"],
        "case_id": record["case_id"],
        "phase": record["phase"],
        "algorithm": record["algorithm"],
        "manifest_file_sha256": context["manifest_sha256"],
        "manifest_record_sha256": record_sha256(record),
        "execution_protocol_sha256": context["execution_protocol_sha256"],
        "simulator_binary_path": context["simulator_binary_path"],
        "simulator_binary_sha256": snapshots["simulator"]["snapshot_sha256"],
        "taskset_artifact_relpath": record["taskset_artifact_relpath"],
        "taskset_artifact_sha256": snapshots["taskset"]["snapshot_sha256"],
        "source_artifact_relpath": record["source_artifact_relpath"],
        "source_artifact_sha256": snapshots["source"]["snapshot_sha256"],
        "system_config_artifact_relpath": record["system_config_artifact_relpath"],
        "system_config_artifact_sha256": snapshots["system"]["snapshot_sha256"],
        "result_relpath": record["result_relpath"],
        "resolved_taskset_path": snapshots["taskset"]["original_path"],
        "resolved_source_path": snapshots["source"]["original_path"],
        "resolved_system_config_path": snapshots["system"]["original_path"],
        "resolved_result_path": str(context["output_root"].joinpath(*_relative_parts(record["result_relpath"]))),
    }
    if record.get("schema_version") == 4:
        inventory_relative = record["materialization_inventory_relpath"]
        inventory_snapshot = _snapshot_artifact(
            context, "inventory", inventory_relative
        )
        try:
            semantic_hash = materialization.acceptance.taskset_semantic_hash(
                Path(snapshots["taskset"]["executed_snapshot_path"])
            )
            inventory_descriptor = _open_absolute_nofollow(
                Path(inventory_snapshot["executed_snapshot_path"])
            )
            try:
                inventory_bytes = _bytes_from_open_fd(inventory_descriptor)
            finally:
                os.close(inventory_descriptor)
            _validate_v4_inventory_bytes(
                inventory_bytes,
                record,
                context,
                snapshots["taskset"]["snapshot_sha256"],
                semantic_hash,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            materialization.MaterializationError,
        ) as exc:
            raise InputIntegrityError(
                f"materialization inventory validation failed: {exc}"
            ) from exc
        provenance.update(
            {
                "base_taskset_artifact_relpath":
                    record["base_taskset_artifact_relpath"],
                "materialization_inventory_relpath": inventory_relative,
                "materialization_inventory_sha256":
                    bytes_sha256(inventory_bytes),
                "taskset_semantic_hash": semantic_hash,
            }
        )
        for field, value in inventory_snapshot.items():
            provenance[f"inventory_{field}"] = value
    for role, snapshot in snapshots.items():
        for field, value in snapshot.items():
            provenance[f"{role}_{field}"] = value
    return provenance


FINGERPRINT_FIELDS = (
    "schema_version",
    "case_id",
    "phase",
    "algorithm",
    "manifest_file_sha256",
    "manifest_record_sha256",
    "execution_protocol_sha256",
    "simulator_binary_path",
    "simulator_binary_sha256",
    "taskset_artifact_relpath",
    "taskset_artifact_sha256",
    "source_artifact_relpath",
    "source_artifact_sha256",
    "system_config_artifact_relpath",
    "system_config_artifact_sha256",
    "result_relpath",
    "resolved_taskset_path",
    "resolved_source_path",
    "resolved_system_config_path",
    "resolved_result_path",
    "simulator_original_path",
    "simulator_observed_original_sha256",
    "simulator_snapshot_relpath",
    "simulator_snapshot_sha256",
    "simulator_executed_snapshot_path",
    "simulator_execution_transport",
    "simulator_executed_snapshot_sha256",
    "system_original_path",
    "system_observed_original_sha256",
    "system_snapshot_relpath",
    "system_snapshot_sha256",
    "system_executed_snapshot_path",
    "system_execution_transport",
    "system_executed_snapshot_sha256",
    "taskset_original_path",
    "taskset_observed_original_sha256",
    "taskset_snapshot_relpath",
    "taskset_snapshot_sha256",
    "taskset_executed_snapshot_path",
    "taskset_execution_transport",
    "taskset_executed_snapshot_sha256",
    "source_original_path",
    "source_observed_original_sha256",
    "source_snapshot_relpath",
    "source_snapshot_sha256",
    "source_executed_snapshot_path",
    "source_execution_transport",
    "source_executed_snapshot_sha256",
)

V4_FINGERPRINT_FIELDS = (
    "base_taskset_artifact_relpath",
    "materialization_inventory_relpath",
    "materialization_inventory_sha256",
    "taskset_semantic_hash",
    "inventory_original_path",
    "inventory_observed_original_sha256",
    "inventory_snapshot_relpath",
    "inventory_snapshot_sha256",
    "inventory_executed_snapshot_path",
    "inventory_execution_transport",
    "inventory_executed_snapshot_sha256",
)


def _fingerprint_fields(provenance):
    return FINGERPRINT_FIELDS + (
        V4_FINGERPRINT_FIELDS
        if "taskset_semantic_hash" in provenance else ()
    )


def new_state(provenance):
    state = {name: provenance[name] for name in _fingerprint_fields(provenance)}
    for role in SNAPSHOT_ROLES:
        state[f"{role}_executed_proc_fd_path"] = None
    state.update(
        {
            "attempt_count": 0,
            "current_status": "planned",
            "attempts": [],
            "final_result_sha256": None,
            "stdout_sha256": None,
            "stderr_sha256": None,
        }
    )
    return state


def _new_publication():
    return {
        "publication_status": "none",
        "staging_result_relpath": None,
        "temporary_result_relpath": None,
        "temporary_stdout_relpath": None,
        "temporary_stderr_relpath": None,
        "final_result_relpath": None,
        "final_stdout_relpath": None,
        "final_stderr_relpath": None,
        "attempt_stdout_relpath": None,
        "attempt_stderr_relpath": None,
        "expected_result_sha256": None,
        "observed_final_result_sha256": None,
        "integrity_failure_reason": None,
        "expected_stdout_sha256": None,
        "expected_stderr_sha256": None,
    }


def _validate_state_shape(state):
    _require(isinstance(state, dict), "state is not an object", ResumeError)
    _require(state.get("current_status") in PROTOCOL["states"], "state status invalid", ResumeError)
    attempts = state.get("attempts")
    _require(isinstance(attempts, list), "state attempts invalid", ResumeError)
    _require(state.get("attempt_count") == len(attempts), "state attempt count mismatch", ResumeError)
    for role in SNAPSHOT_ROLES:
        field = f"{role}_executed_proc_fd_path"
        _require(field in state, f"state missing {field}", ResumeError)
        value = state[field]
        _require(
            value is None
            or (
                isinstance(value, str)
                and value.startswith(f"{PROC_FD_ROOT}/")
                and value[len(PROC_FD_ROOT) + 1 :].isdigit()
            ),
            f"invalid runtime proc fd path: {field}",
            ResumeError,
        )
    for attempt in attempts:
        staging_directory = attempt.get("staging_directory_relpath")
        staging_basename = attempt.get("staging_trace_basename")
        staging_sha = attempt.get("staging_trace_sha256")
        _relative_parts(staging_directory)
        _require(
            staging_directory.startswith(
                f".b4pe/attempt-results/{state.get('case_id')}/attempt-"
            ),
            "attempt staging directory mismatch",
            ResumeError,
        )
        _require(
            staging_basename in {"trace.txt", "trace.json"},
            "attempt staging trace basename invalid",
            ResumeError,
        )
        _require(
            attempt.get("temporary_result_path")
            == f"{staging_directory}/{staging_basename}",
            "attempt staging trace path mismatch",
            ResumeError,
        )
        _require(
            staging_sha is None
            or (isinstance(staging_sha, str) and len(staging_sha) == 64),
            "attempt staging trace SHA invalid",
            ResumeError,
        )
        publication = attempt.get("publication")
        _require(isinstance(publication, dict), "attempt publication missing", ResumeError)
        _require(set(publication) == set(_new_publication()), "attempt publication fields mismatch", ResumeError)
        _require(publication["publication_status"] in PUBLICATION_STATUSES, "publication status invalid", ResumeError)
        _require(
            publication["staging_result_relpath"]
            == attempt.get("temporary_result_path"),
            "publication staging trace path mismatch",
            ResumeError,
        )
    return state


def _validate_state(state, provenance):
    _validate_state_shape(state)
    for field in _fingerprint_fields(provenance):
        _require(field in state, f"state missing {field}", ResumeError)
        _require(
            state[field] == provenance[field],
            f"state fingerprint mismatch: {field}",
            InputIntegrityError,
        )
    return state


def _load_state_raw(context, relative):
    if not _exists_at(context, relative):
        return None
    try:
        state = json.loads(_read_bytes_at(context, relative, "state").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResumeError(f"invalid state file: {relative}") from exc
    return _validate_state_shape(state)


def _load_state(context, relative, provenance):
    state = _load_state_raw(context, relative)
    return None if state is None else _validate_state(state, provenance)


def _validate_recovery_fingerprint(record, context, state):
    expected = {
        "schema_version": PROTOCOL["state_schema_version"],
        "case_id": record["case_id"],
        "phase": record["phase"],
        "algorithm": record["algorithm"],
        "manifest_file_sha256": context["manifest_sha256"],
        "manifest_record_sha256": record_sha256(record),
        "execution_protocol_sha256": context["execution_protocol_sha256"],
        "simulator_binary_path": context["simulator_binary_path"],
        "taskset_artifact_relpath": record["taskset_artifact_relpath"],
        "source_artifact_relpath": record["source_artifact_relpath"],
        "system_config_artifact_relpath": record["system_config_artifact_relpath"],
        "result_relpath": record["result_relpath"],
    }
    for field, value in expected.items():
        _require(state.get(field) == value, f"state fingerprint mismatch: {field}", InputIntegrityError)
    for role in SNAPSHOT_ROLES:
        relative = state.get(f"{role}_snapshot_relpath")
        expected_sha = state.get(f"{role}_snapshot_sha256")
        _require(
            isinstance(relative, str)
            and relative.startswith(f".b4pe/snapshots/{role}/")
            and PurePosixPath(relative).name == expected_sha,
            f"invalid {role} snapshot provenance",
            InputIntegrityError,
        )
        _require(
            state.get(f"{role}_observed_original_sha256") == expected_sha,
            f"{role} observed SHA mismatch",
            InputIntegrityError,
        )
        legacy_field = (
            "simulator_binary_sha256"
            if role == "simulator"
            else f"{role if role != 'system' else 'system_config'}_artifact_sha256"
        )
        _require(state.get(legacy_field) == expected_sha, f"{role} provenance SHA mismatch", InputIntegrityError)
        _require(
            state.get(f"{role}_executed_snapshot_path")
            == str(context["output_root"].joinpath(*_relative_parts(relative))),
            f"{role} executed snapshot path mismatch",
            InputIntegrityError,
        )
        _require(
            state.get(f"{role}_execution_transport")
            == "inherited_file_descriptor",
            f"{role} execution transport mismatch",
            InputIntegrityError,
        )
        _require(
            state.get(f"{role}_executed_snapshot_sha256") == expected_sha,
            f"{role} executed snapshot SHA mismatch",
            InputIntegrityError,
        )
        _require(
            _file_sha_at(context, relative, f"{role} recovery snapshot") == expected_sha,
            f"{role} recovery snapshot SHA mismatch",
            InputIntegrityError,
        )
    return state


def _write_state(root_or_context, state):
    context, owns_descriptor = _coerce_context(root_or_context)
    try:
        _atomic_write_json_at(context, _state_relpath(state["case_id"]), state)
    finally:
        if owns_descriptor:
            os.close(context["root_fd"])


def _verify_succeeded_state(context, state):
    checks = (
        (_file_sha_at(context, state["result_relpath"], "succeeded result"), state.get("final_result_sha256"), "result"),
        (_file_sha_at(context, _log_relpath(state["case_id"], "stdout"), "succeeded stdout", allow_empty=True), state.get("stdout_sha256"), "stdout"),
        (_file_sha_at(context, _log_relpath(state["case_id"], "stderr"), "succeeded stderr", allow_empty=True), state.get("stderr_sha256"), "stderr"),
    )
    for actual, expected, label in checks:
        _require(expected and actual == expected, f"succeeded {label} SHA mismatch", InputIntegrityError)
    attempt = state["attempts"][-1]
    _require(attempt["publication"]["publication_status"] == "committed", "succeeded publication is not committed", InputIntegrityError)
    _require(
        attempt["publication"].get("observed_final_result_sha256")
        == attempt["publication"].get("expected_result_sha256"),
        "succeeded publication final verification mismatch",
        InputIntegrityError,
    )
    _require(
        attempt["publication"].get("integrity_failure_reason") is None,
        "succeeded publication records an integrity failure",
        InputIntegrityError,
    )


def _create_temp_file(context, relative, initial=b""):
    parent, name = _open_parent_at(context, relative, create=True)
    try:
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        if initial:
            os.write(descriptor, initial)
        os.fsync(descriptor)
        return descriptor
    finally:
        os.close(parent)


def build_execution_argv(
    record,
    context,
    attempt_index,
    attempt_directory_fd,
    trace_basename,
    execution_snapshots,
):
    argv = record["command_argv"]
    _require(
        isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv),
        "command_argv must be a string array",
        SafetyError,
    )
    result_relative = record["result_relpath"]
    _require(argv.count(result_relative) == 1, "result_relpath must occur exactly once in argv", SafetyError)
    replacements = {
        record["system_config_artifact_relpath"]: execution_snapshots["system"][
            "proc_fd_path"
        ],
        record["taskset_artifact_relpath"]: execution_snapshots["taskset"][
            "proc_fd_path"
        ],
        result_relative: _proc_fd_child_path(
            attempt_directory_fd, trace_basename
        ),
    }
    replaced = [replacements.get(item, item) for item in argv]
    if record.get("schema_version") == 4:
        placeholder = materialization.SEMANTIC_HASH_PLACEHOLDER
        _require(
            replaced.count(placeholder) == 1,
            "semantic hash placeholder must occur exactly once",
            SafetyError,
        )
        replaced = [
            context["active_provenance"]["taskset_semantic_hash"]
            if item == placeholder else item
            for item in replaced
        ]
    replaced[0] = execution_snapshots["simulator"]["proc_fd_path"]
    return replaced


def _record_snapshot_execution(state, attempt, execution_snapshots):
    attempt["snapshot_execution"] = {}
    for role in SNAPSHOT_ROLES:
        item = execution_snapshots[role]
        state[f"{role}_executed_proc_fd_path"] = item["proc_fd_path"]
        attempt["snapshot_execution"][role] = {
            "execution_transport": item["transport"],
            "executed_proc_fd_path": item["proc_fd_path"],
            "executed_snapshot_sha256": item["sha256"],
        }


def _terminate_process_group(process, grace_seconds):
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        group_exists = False
    else:
        group_exists = True
    if group_exists:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait()


def _attempt_timeout(record, attempt_index):
    if attempt_index == 1:
        return record["timeout_seconds"]
    return record["retry_policy"]["retry_timeout_seconds"]


def _publish_log(context, temporary, final, attempt_final):
    _replace_at(context, temporary, final, allow_existing=True)
    _copy_file_at(context, final, attempt_final, allow_existing=False)


def _publish_failure_logs(context, attempt, publication):
    for stream in ("stdout", "stderr"):
        temporary = publication[f"temporary_{stream}_relpath"]
        final = publication[f"final_{stream}_relpath"]
        attempt_final = publication[f"attempt_{stream}_relpath"]
        if _exists_at(context, temporary):
            sha = _file_sha_at(
                context, temporary, f"temporary {stream}", allow_empty=True
            )
            _replace_at(
                context,
                temporary,
                final,
                allow_existing=True,
                expected_sha256=sha,
            )
        else:
            sha = _file_sha_at(
                context, final, f"published {stream}", allow_empty=True
            )
        if _exists_at(context, attempt_final):
            _require(
                _file_sha_at(
                    context,
                    attempt_final,
                    f"attempt {stream}",
                    allow_empty=True,
                )
                == sha,
                f"attempt {stream} SHA mismatch",
                InputIntegrityError,
            )
        else:
            _copy_file_at(context, final, attempt_final, allow_existing=False)
        attempt[f"{stream}_sha256"] = sha
    return attempt["stdout_sha256"], attempt["stderr_sha256"]


def _result_publication_temp_relpath(record, attempt_index):
    final_parts = _relative_parts(record["result_relpath"])
    temporary_name = (
        f".{final_parts[-1]}.attempt-{attempt_index:04d}-"
        f"{secrets.token_hex(12)}.publication.tmp"
    )
    return "/".join(final_parts[:-1] + (temporary_name,))


def _discard_result_publication_temp(context, publication):
    temporary = publication["temporary_result_relpath"]
    try:
        if temporary is not None and _exists_at(context, temporary):
            _unlink_at(context, temporary)
    except (ExecutionError, OSError):
        pass


def _copy_verified_staging_for_publication(
    record,
    context,
    attempt,
    publication,
    validated_result_identity,
):
    staging = publication["staging_result_relpath"]
    temporary = publication["temporary_result_relpath"]
    expected_sha = validated_result_identity["sha256"]
    try:
        _publication_integrity_hook(
            "after_initial_validation",
            record,
            context,
            attempt,
            publication,
        )
        _require_staging_trace_identity(
            context, attempt, validated_result_identity
        )
        _copy_file_at(context, staging, temporary, allow_existing=False)
        _publication_integrity_hook(
            "after_result_temp_fsync",
            record,
            context,
            attempt,
            publication,
        )
        _require_staging_trace_identity(
            context, attempt, validated_result_identity
        )
        copied_sha = _file_sha_at(
            context, temporary, "result publication temporary"
        )
        if copied_sha != expected_sha:
            raise StagingTraceError(
                "trace_integrity_error",
                "result publication temporary SHA differs from staging trace",
            )
        return copied_sha
    except (ExecutionError, OSError) as exc:
        _discard_result_publication_temp(context, publication)
        if isinstance(exc, StagingTraceError):
            raise
        raise StagingTraceError(
            "trace_integrity_error",
            "staging trace changed before publication preparation",
        ) from exc


def _prepare_publication(
    record,
    context,
    state,
    attempt,
    publication,
    validated_result_identity=None,
):
    _require(
        isinstance(validated_result_identity, dict)
        and isinstance(validated_result_identity.get("sha256"), str)
        and len(validated_result_identity["sha256"]) == 64,
        "validated staging trace identity missing",
        InfrastructureError,
    )
    result_sha = _copy_verified_staging_for_publication(
        record,
        context,
        attempt,
        publication,
        validated_result_identity,
    )
    stdout_sha = _file_sha_at(context, publication["temporary_stdout_relpath"], "temporary stdout", allow_empty=True)
    stderr_sha = _file_sha_at(context, publication["temporary_stderr_relpath"], "temporary stderr", allow_empty=True)
    publication.update(
        {
            "publication_status": "prepared",
            "expected_result_sha256": result_sha,
            "expected_stdout_sha256": stdout_sha,
            "expected_stderr_sha256": stderr_sha,
        }
    )
    attempt["final_result_sha256"] = result_sha
    attempt["stdout_sha256"] = stdout_sha
    attempt["stderr_sha256"] = stderr_sha
    _write_state(context, state)


def _verify_published_result(
    context,
    attempt,
    publication,
    *,
    final_parent_fd=None,
    final_name=None,
):
    relative = publication["final_result_relpath"]
    expected_sha = publication["expected_result_sha256"]
    owns_parent = final_parent_fd is None
    if owns_parent:
        parent, name = _open_parent_at(context, relative)
    else:
        parent = final_parent_fd
        name = final_name
    descriptor = None
    try:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
        except OSError as exc:
            raise PublicationIntegrityError(
                "published result cannot be reopened"
            ) from exc
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode),
            "published result is not a regular file",
            PublicationIntegrityError,
        )
        _require(
            before.st_size > 0,
            "published result is empty",
            PublicationIntegrityError,
        )
        _publication_integrity_hook(
            "after_final_reopen_before_hash",
            None,
            context,
            attempt,
            publication,
        )
        first_sha = _sha_from_fd(descriptor)
        _publication_integrity_hook(
            "after_final_first_hash",
            None,
            context,
            attempt,
            publication,
        )
        middle = os.fstat(descriptor)
        second_sha = _sha_from_fd(descriptor)
        after = os.fstat(descriptor)
        publication["observed_final_result_sha256"] = second_sha
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        _require(
            identity(before) == identity(middle) == identity(after)
            and first_sha == second_sha,
            "published result changed while hashing",
            PublicationIntegrityError,
        )
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(
            (current.st_dev, current.st_ino) == (after.st_dev, after.st_ino)
            and stat.S_ISREG(current.st_mode),
            "published result namespace changed while hashing",
            PublicationIntegrityError,
        )
        _require(
            second_sha == expected_sha,
            "published result SHA mismatch",
            PublicationIntegrityError,
        )
        return second_sha
    except StagingTraceError:
        raise
    except (ExecutionError, OSError) as exc:
        observed = publication.get("observed_final_result_sha256")
        raise StagingTraceError(
            "trace_integrity_error",
            str(exc),
            observed_final_sha256=observed,
            preserve_publication=True,
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if owns_parent:
            os.close(parent)


def _commit_publication(
    context, state, attempt, validated_result_identity
):
    publication = attempt["publication"]
    _publication_integrity_hook(
        "before_result_replace",
        None,
        context,
        attempt,
        publication,
    )
    _require_staging_trace_identity(
        context, attempt, validated_result_identity
    )
    def verify_replaced_result(parent, name):
        _publication_integrity_hook(
            "after_result_replace_before_final_reopen",
            None,
            context,
            attempt,
            publication,
        )
        return _verify_published_result(
            context,
            attempt,
            publication,
            final_parent_fd=parent,
            final_name=name,
        )

    try:
        _replace_at(
            context,
            publication["temporary_result_relpath"],
            publication["final_result_relpath"],
            allow_existing=False,
            expected_sha256=publication["expected_result_sha256"],
            post_replace_verifier=verify_replaced_result,
        )
    except PublicationIntegrityError as exc:
        raise StagingTraceError(
            "trace_integrity_error",
            str(exc),
            preserve_publication=True,
        ) from exc
    publication["publication_status"] = "result_published"
    _write_state(context, state)
    for stream in ("stdout", "stderr"):
        _publish_log(
            context,
            publication[f"temporary_{stream}_relpath"],
            publication[f"final_{stream}_relpath"],
            publication[f"attempt_{stream}_relpath"],
        )
    publication["publication_status"] = "logs_published"
    _write_state(context, state)
    publication["publication_status"] = "committed"
    state["current_status"] = "succeeded"
    state["final_result_sha256"] = publication["expected_result_sha256"]
    state["stdout_sha256"] = publication["expected_stdout_sha256"]
    state["stderr_sha256"] = publication["expected_stderr_sha256"]
    _write_state(context, state)


def _record_trace_integrity_failure(context, state, attempt, error):
    publication = attempt["publication"]
    publication["integrity_failure_reason"] = str(error)
    publication["observed_final_result_sha256"] = (
        error.observed_final_sha256
    )
    if not error.preserve_publication:
        _discard_result_publication_temp(context, publication)
        publication["publication_status"] = "none"
        publication["expected_result_sha256"] = None
        publication["expected_stdout_sha256"] = None
        publication["expected_stderr_sha256"] = None
        attempt["final_result_sha256"] = None
    stdout_sha, stderr_sha = _publish_failure_logs(
        context, attempt, publication
    )
    state["stdout_sha256"] = stdout_sha
    state["stderr_sha256"] = stderr_sha
    attempt["termination_reason"] = error.reason
    state["current_status"] = "failed"
    _write_state(context, state)
    return "failed"


def run_attempt(record, context, state, attempt_index):
    timeout_seconds = _attempt_timeout(record, attempt_index)
    _require(
        isinstance(timeout_seconds, (int, float))
        and not isinstance(timeout_seconds, bool)
        and timeout_seconds > 0,
        "attempt timeout invalid",
        SafetyError,
    )
    case_id = record["case_id"]
    trace_basename = _trace_basename(record["result_relpath"])
    stdout_relative = _unique_temp_relpath(".b4pe/logs", f"{case_id}.attempt-{attempt_index}", "stdout")
    stderr_relative = _unique_temp_relpath(".b4pe/logs", f"{case_id}.attempt-{attempt_index}", "stderr")
    attempt_directory_fd, attempt_directory_relative = _create_attempt_staging(
        context, case_id, attempt_index, trace_basename
    )
    trace_relative = f"{attempt_directory_relative}/{trace_basename}"
    result_publication_temporary = _result_publication_temp_relpath(
        record, attempt_index
    )
    publication = _new_publication()
    publication.update(
        {
            "staging_result_relpath": trace_relative,
            "temporary_result_relpath": result_publication_temporary,
            "temporary_stdout_relpath": stdout_relative,
            "temporary_stderr_relpath": stderr_relative,
            "final_result_relpath": record["result_relpath"],
            "final_stdout_relpath": _log_relpath(case_id, "stdout"),
            "final_stderr_relpath": _log_relpath(case_id, "stderr"),
            "attempt_stdout_relpath": _log_relpath(case_id, "stdout", attempt_index),
            "attempt_stderr_relpath": _log_relpath(case_id, "stderr", attempt_index),
        }
    )
    attempt = {
        "attempt_index": attempt_index,
        "timeout_seconds": timeout_seconds,
        "started_at": utc_now(),
        "ended_at": None,
        "exit_code": None,
        "termination_reason": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "temporary_result_path": trace_relative,
        "staging_directory_relpath": attempt_directory_relative,
        "staging_trace_basename": trace_basename,
        "staging_trace_sha256": None,
        "final_result_sha256": None,
        "publication": publication,
        "snapshot_execution": {},
    }
    process = None
    timed_out = False
    interrupted = False
    popen_error = None
    staging_trace_error = None
    staging_trace_identity = None
    resources = ExitStack()
    resources.callback(os.close, attempt_directory_fd)
    try:
        _require_trace_target_absent(attempt_directory_fd, trace_basename)
        stdout_fd = _create_temp_file(context, stdout_relative)
        resources.callback(os.close, stdout_fd)
        stderr_fd = _create_temp_file(context, stderr_relative)
        resources.callback(os.close, stderr_fd)

        state["current_status"] = "running"
        state["attempt_count"] = attempt_index
        state["attempts"].append(attempt)
        _write_state(context, state)

        execution_snapshots = _open_execution_snapshots(
            context, context["active_provenance"]
        )
        resources.callback(_close_execution_snapshots, execution_snapshots)
        context["active_snapshot_execution"] = execution_snapshots
        context["active_attempt_staging"] = {
            "directory_fd": attempt_directory_fd,
            "directory_relpath": attempt_directory_relative,
            "trace_basename": trace_basename,
            "trace_relpath": trace_relative,
        }
        _record_snapshot_execution(state, attempt, execution_snapshots)
        _write_state(context, state)
        argv = build_execution_argv(
            record,
            context,
            attempt_index,
            attempt_directory_fd,
            trace_basename,
            execution_snapshots,
        )
        try:
            _before_popen_hook(record, context)
            _require_trace_target_absent(
                attempt_directory_fd, trace_basename
            )
            if record.get("schema_version") == 4:
                inventory_snapshot = (
                    _open_and_validate_v4_inventory_snapshot(
                        record,
                        context,
                        context["active_provenance"],
                        execution_snapshots["taskset"],
                    )
                )
                resources.callback(os.close, inventory_snapshot["fd"])
        except (ExecutionError, OSError) as exc:
            popen_error = exc
        snapshot_fds = tuple(
            execution_snapshots[role]["fd"] for role in SNAPSHOT_ROLES
        )
        pass_fds = snapshot_fds + (attempt_directory_fd,)
        _require(
            context["root_fd"] not in pass_fds,
            "output-root rootfd must not be inherited",
            SafetyError,
        )
        with os.fdopen(os.dup(stdout_fd), "wb") as stdout_handle, os.fdopen(
            os.dup(stderr_fd), "wb"
        ) as stderr_handle:
            try:
                if popen_error is not None:
                    raise popen_error
                process = subprocess.Popen(
                    argv,
                    shell=False,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    close_fds=True,
                    pass_fds=pass_fds,
                    env={
                        **os.environ,
                        "B4PE_SOURCE_SNAPSHOT": execution_snapshots["source"][
                            "proc_fd_path"
                        ],
                    },
                )
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_process_group(process, PROTOCOL["process_group_rules"]["grace_seconds"])
                except KeyboardInterrupt:
                    interrupted = True
                    _terminate_process_group(process, PROTOCOL["process_group_rules"]["grace_seconds"])
            except (ExecutionError, OSError) as exc:
                popen_error = exc
                if process is not None and process.poll() is None:
                    _terminate_process_group(process, PROTOCOL["process_group_rules"]["grace_seconds"])
            finally:
                stdout_handle.flush()
                stderr_handle.flush()
                os.fsync(stdout_handle.fileno())
                os.fsync(stderr_handle.fileno())
        if (
            popen_error is None
            and not interrupted
            and not timed_out
            and process.returncode == 0
        ):
            try:
                staging_trace_identity = _validate_staging_trace(
                    attempt_directory_fd, trace_basename
                )
            except (StagingTraceError, OSError) as exc:
                staging_trace_error = (
                    exc
                    if isinstance(exc, StagingTraceError)
                    else StagingTraceError(
                        "trace_integrity_error",
                        "staging trace changed during post-exit validation",
                    )
                )
    finally:
        context.pop("active_snapshot_execution", None)
        context.pop("active_attempt_staging", None)
        resources.close()
    attempt["ended_at"] = utc_now()
    if popen_error is not None:
        stdout_sha, stderr_sha = _publish_failure_logs(context, attempt, publication)
        state["stdout_sha256"] = stdout_sha
        state["stderr_sha256"] = stderr_sha
        attempt["termination_reason"] = "infrastructure_error"
        state["current_status"] = "failed"
        _write_state(context, state)
        raise InfrastructureError(
            f"cannot start simulator safely: {popen_error}"
        ) from popen_error
    attempt["exit_code"] = process.returncode
    if interrupted or timed_out or process.returncode != 0:
        stdout_sha, stderr_sha = _publish_failure_logs(context, attempt, publication)
        state["stdout_sha256"] = stdout_sha
        state["stderr_sha256"] = stderr_sha
        if interrupted:
            outcome, reason = "interrupted", "interrupted"
        elif timed_out:
            outcome, reason = "timed_out", "timeout"
        else:
            outcome, reason = "failed", "nonzero_exit"
        attempt["termination_reason"] = reason
        state["current_status"] = outcome
        _write_state(context, state)
        return outcome
    if staging_trace_error is not None:
        stdout_sha, stderr_sha = _publish_failure_logs(context, attempt, publication)
        state["stdout_sha256"] = stdout_sha
        state["stderr_sha256"] = stderr_sha
        attempt["termination_reason"] = staging_trace_error.reason
        state["current_status"] = "failed"
        _write_state(context, state)
        return "failed"
    _require(
        isinstance(staging_trace_identity, dict)
        and isinstance(staging_trace_identity.get("sha256"), str)
        and len(staging_trace_identity["sha256"]) == 64,
        "validated staging trace identity missing",
        InfrastructureError,
    )
    attempt["staging_trace_sha256"] = staging_trace_identity["sha256"]
    attempt["termination_reason"] = "succeeded"
    try:
        _prepare_publication(
            record,
            context,
            state,
            attempt,
            publication,
            validated_result_identity=staging_trace_identity,
        )
        _commit_publication(
            context, state, attempt, staging_trace_identity
        )
    except StagingTraceError as exc:
        return _record_trace_integrity_failure(
            context, state, attempt, exc
        )
    return "succeeded"


def _active_publication(state):
    if not state["attempts"]:
        return None
    attempt = state["attempts"][-1]
    publication = attempt["publication"]
    if (
        publication["publication_status"] in PUBLICATION_ACTIVE
        and publication.get("integrity_failure_reason") is None
    ):
        return attempt
    return None


def _validate_publication(record, state, attempt):
    publication = attempt["publication"]
    attempt_index = attempt["attempt_index"]
    expected_paths = {
        "final_result_relpath": record["result_relpath"],
        "final_stdout_relpath": _log_relpath(record["case_id"], "stdout"),
        "final_stderr_relpath": _log_relpath(record["case_id"], "stderr"),
        "attempt_stdout_relpath": _log_relpath(record["case_id"], "stdout", attempt_index),
        "attempt_stderr_relpath": _log_relpath(record["case_id"], "stderr", attempt_index),
    }
    for field, expected in expected_paths.items():
        _require(publication.get(field) == expected, f"publication path mismatch: {field}", InputIntegrityError)
    expected_staging_trace = (
        f"{attempt['staging_directory_relpath']}/"
        f"{attempt['staging_trace_basename']}"
    )
    _require(
        publication.get("staging_result_relpath") == expected_staging_trace,
        "publication staging trace path mismatch",
        InputIntegrityError,
    )
    final_result_parts = _relative_parts(publication["final_result_relpath"])
    temporary_result_parts = _relative_parts(
        publication["temporary_result_relpath"]
    )
    _require(
        temporary_result_parts[:-1] == final_result_parts[:-1],
        "result publication temporary is not in the final parent",
        InputIntegrityError,
    )
    _require(
        temporary_result_parts[-1].startswith(
            f".{final_result_parts[-1]}.attempt-{attempt_index:04d}-"
        )
        and temporary_result_parts[-1].endswith(".publication.tmp"),
        "result publication temporary name mismatch",
        InputIntegrityError,
    )
    for field in (
        "staging_result_relpath",
        "temporary_result_relpath",
        "temporary_stdout_relpath",
        "temporary_stderr_relpath",
    ):
        _relative_parts(publication.get(field))
    for field in ("expected_result_sha256", "expected_stdout_sha256", "expected_stderr_sha256"):
        value = publication.get(field)
        _require(isinstance(value, str) and len(value) == 64, f"publication SHA missing: {field}", InputIntegrityError)
    _require(
        attempt.get("staging_trace_sha256")
        == publication["expected_result_sha256"],
        "prepared staging trace SHA mismatch",
        InputIntegrityError,
    )
    _require(state["attempt_count"] == len(state["attempts"]), "publication attempt count mismatch", ResumeError)


def _recover_one_file(context, temporary, final, expected, label, allow_empty):
    final_exists = _exists_at(context, final)
    temporary_exists = _exists_at(context, temporary)
    if final_exists:
        actual = _file_sha_at(context, final, f"published {label}", allow_empty=allow_empty)
        _require(actual == expected, f"published {label} SHA mismatch", InputIntegrityError)
        if temporary_exists:
            temporary_sha = _file_sha_at(context, temporary, f"temporary {label}", allow_empty=allow_empty)
            _require(temporary_sha == expected, f"temporary {label} SHA mismatch", InputIntegrityError)
        return
    _require(temporary_exists, f"missing temporary and final {label}", InputIntegrityError)
    temporary_sha = _file_sha_at(context, temporary, f"temporary {label}", allow_empty=allow_empty)
    _require(temporary_sha == expected, f"temporary {label} SHA mismatch", InputIntegrityError)
    _replace_at(
        context,
        temporary,
        final,
        allow_existing=False,
        expected_sha256=expected,
    )


def _recover_result_publication(context, attempt, publication):
    temporary = publication["temporary_result_relpath"]
    final = publication["final_result_relpath"]
    expected = publication["expected_result_sha256"]
    final_exists = _exists_at(context, final)
    temporary_exists = _exists_at(context, temporary)
    if final_exists:
        if temporary_exists:
            temporary_sha = _file_sha_at(
                context, temporary, "temporary result"
            )
            if temporary_sha != expected:
                raise StagingTraceError(
                    "trace_integrity_error",
                    "temporary result SHA mismatch",
                    preserve_publication=True,
                )
        return _verify_published_result(context, attempt, publication)
    _require(
        temporary_exists,
        "missing temporary and final result",
        InputIntegrityError,
    )
    def verify_replaced_result(parent, name):
        _publication_integrity_hook(
            "after_result_replace_before_final_reopen",
            None,
            context,
            attempt,
            publication,
        )
        return _verify_published_result(
            context,
            attempt,
            publication,
            final_parent_fd=parent,
            final_name=name,
        )

    try:
        _replace_at(
            context,
            temporary,
            final,
            allow_existing=False,
            expected_sha256=expected,
            post_replace_verifier=verify_replaced_result,
        )
    except PublicationIntegrityError as exc:
        raise StagingTraceError(
            "trace_integrity_error",
            str(exc),
            preserve_publication=True,
        ) from exc
    return publication["observed_final_result_sha256"]


def _recover_publication(record, context, state, attempt):
    _validate_publication(record, state, attempt)
    publication = attempt["publication"]
    staging_sha = _file_sha_at(
        context,
        publication["staging_result_relpath"],
        "prepared staging trace",
    )
    _require(
        staging_sha == publication["expected_result_sha256"],
        "prepared staging trace SHA mismatch",
        InputIntegrityError,
    )
    try:
        _recover_result_publication(context, attempt, publication)
    except StagingTraceError as exc:
        return _record_trace_integrity_failure(
            context, state, attempt, exc
        )
    publication["publication_status"] = "result_published"
    _write_state(context, state)
    for stream in ("stdout", "stderr"):
        temporary = publication[f"temporary_{stream}_relpath"]
        final = publication[f"final_{stream}_relpath"]
        expected = publication[f"expected_{stream}_sha256"]
        _recover_one_file(context, temporary, final, expected, stream, True)
        attempt_final = publication[f"attempt_{stream}_relpath"]
        if _exists_at(context, attempt_final):
            _require(
                _file_sha_at(context, attempt_final, f"attempt {stream}", allow_empty=True) == expected,
                f"attempt {stream} SHA mismatch",
                InputIntegrityError,
            )
        else:
            _copy_file_at(context, final, attempt_final)
    publication["publication_status"] = "logs_published"
    _write_state(context, state)
    publication["publication_status"] = "committed"
    state["current_status"] = "succeeded"
    state["final_result_sha256"] = publication["expected_result_sha256"]
    state["stdout_sha256"] = publication["expected_stdout_sha256"]
    state["stderr_sha256"] = publication["expected_stderr_sha256"]
    _write_state(context, state)
    return "succeeded"


def _mark_stale_running_interrupted(context, state):
    if state["attempts"]:
        attempt = state["attempts"][-1]
        if attempt.get("ended_at") is None:
            attempt["ended_at"] = utc_now()
            attempt["termination_reason"] = "interrupted"
    state["current_status"] = "interrupted"
    _write_state(context, state)


def execute_case(record, context, resume=False, retry_failed=False):
    with case_lock(context, record["case_id"]):
        raw_state = _load_state_raw(context, _state_relpath(record["case_id"]))
        if raw_state is not None:
            active = _active_publication(raw_state)
            if active is not None:
                _validate_recovery_fingerprint(record, context, raw_state)
                if not resume:
                    return "interrupted"
                return _recover_publication(record, context, raw_state, active)
        provenance = build_provenance(record, context)
        context["active_provenance"] = provenance
        state = None if raw_state is None else _validate_state(raw_state, provenance)
        final_exists = _exists_at(context, record["result_relpath"])
        if state is None:
            _require(not final_exists, "result exists without state", InputIntegrityError)
            state = new_state(provenance)
            _write_state(context, state)
        elif state["current_status"] == "succeeded":
            _verify_succeeded_state(context, state)
            return "skipped_succeeded"
        else:
            _require(not final_exists, "orphan result without prepared publication", InputIntegrityError)
            if state["current_status"] == "running":
                _mark_stale_running_interrupted(context, state)
                if not resume:
                    return "interrupted"
            elif state["current_status"] == "interrupted":
                if not resume:
                    return "interrupted"
            elif state["current_status"] in {"failed", "timed_out"}:
                if not retry_failed:
                    return "skipped_failed"
                _require(
                    state["attempt_count"] < record["retry_policy"]["max_attempts"],
                    "attempts exhausted",
                    ResumeError,
                )
                last_reason = state["attempts"][-1]["termination_reason"]
                _require(
                    last_reason in record["retry_policy"]["retry_on"],
                    "failure reason is not retryable",
                    ResumeError,
                )

        max_attempts = record["retry_policy"]["max_attempts"]
        _require(type(max_attempts) is int and 0 < max_attempts <= 2, "max attempts invalid", SafetyError)
        while state["attempt_count"] < max_attempts:
            attempt_index = state["attempt_count"] + 1
            outcome = run_attempt(record, context, state, attempt_index)
            if outcome in {"succeeded", "interrupted"}:
                return outcome
            reason = state["attempts"][-1]["termination_reason"]
            if reason not in record["retry_policy"]["retry_on"]:
                return outcome
            if state["attempt_count"] >= max_attempts:
                return outcome
        raise ResumeError("attempts exhausted")


def new_summary(manifest_sha256, selected_cases):
    summary = {}
    for field in PROTOCOL["summary_fields"]:
        if field == "manifest_sha256":
            summary[field] = manifest_sha256
        elif field == "selected_cases":
            summary[field] = selected_cases
        else:
            summary[field] = 0
    return summary


def _publish_summary(context, summary):
    data = manifest.compact_json(summary).encode("utf-8") + b"\n"
    sha = bytes_sha256(data)
    relative = f".b4pe/summaries/{sha}.json"
    if _exists_at(context, relative):
        _require(_read_bytes_at(context, relative, "summary") == data, "content-addressed summary mismatch", InputIntegrityError)
    else:
        _atomic_write_at(context, relative, data)
    return relative


def execute_records(records, context, resume=False, retry_failed=False):
    summary = new_summary(context["manifest_sha256"], len(records))
    for record in records:
        try:
            outcome = execute_case(record, context, resume, retry_failed)
        except LockConflictError:
            summary["lock_conflicts"] += 1
            continue
        except (ExecutionError, OSError):
            summary["infrastructure_errors"] += 1
            continue
        if outcome in {"succeeded", "failed", "timed_out", "interrupted"}:
            summary["executed_cases"] += 1
        summary[outcome] += 1
        if outcome == "interrupted":
            break
    try:
        _publish_summary(context, summary)
    except (ExecutionError, OSError):
        summary["infrastructure_errors"] += 1
    return summary


def prepare_and_execute(
    manifest_path,
    output_root,
    simulator_binary,
    limit=None,
    resume=False,
    retry_failed=False,
):
    if limit is not None:
        _require(type(limit) is int and limit >= 0, "limit must be non-negative", SafetyError)
    records = manifest.validate_manifest(manifest_path)
    return execute_validated_cases(
        records,
        manifest_path,
        output_root,
        simulator_binary,
        limit=limit,
        resume=resume,
        retry_failed=retry_failed,
    )


def execute_validated_cases(
    records,
    record_source_path,
    output_root,
    simulator_binary,
    limit=None,
    resume=False,
    retry_failed=False,
):
    """Run cases already accepted by one of the two fixed validators."""
    _require(isinstance(records, list) and records, "validated cases missing", SafetyError)
    if limit is not None:
        _require(type(limit) is int and limit >= 0, "limit must be non-negative", SafetyError)
    schema_versions = {record.get("schema_version") for record in records}
    _require(
        len(schema_versions) == 1,
        "validated cases mix protocol versions",
        SafetyError,
    )
    schema_version = next(iter(schema_versions))
    _require(
        schema_version != 4,
        "manifest v4 is a draft and campaign execution is not authorized",
        SafetyError,
    )
    execution_protocol_sha256 = (
        EXECUTION_PROTOCOL_V3_SHA256
        if schema_version in {3, "b4-pe-integration-smoke-v3"}
        else EXECUTION_PROTOCOL_SHA256
    )
    context = build_context(
        record_source_path,
        output_root,
        simulator_binary,
        execution_protocol_sha256,
    )
    try:
        selected = records if limit is None else records[:limit]
        return execute_records(selected, context, resume=resume, retry_failed=retry_failed)
    finally:
        close_context(context)


def summary_succeeded(summary):
    return not any(
        summary[name]
        for name in (
            "failed",
            "timed_out",
            "interrupted",
            "skipped_failed",
            "lock_conflicts",
            "infrastructure_errors",
        )
    )
