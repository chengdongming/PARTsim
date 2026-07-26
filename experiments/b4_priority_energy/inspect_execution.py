#!/usr/bin/env python3
"""Read-only integrity inspection for a B4-PE I4B-1 output root."""

import argparse
import fcntl
import json
import os
from collections import Counter
from pathlib import Path

import manifest_common as manifest
from execution_common import (
    EXECUTION_PROTOCOL_SHA256,
    ExecutionError,
    InputIntegrityError,
    PUBLICATION_ACTIVE,
    PUBLICATION_STATUSES,
    PROTOCOL,
    _is_within,
    _regular_file_sha,
    bytes_sha256,
    file_sha256,
    record_sha256,
    safe_output_path,
    validate_output_root,
    validate_simulator_binary,
)


def _lock_is_held(path):
    if not path.exists():
        return False
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def inspect_output(output_root, manifest_path=None, simulator_binary=None):
    root = validate_output_root(output_root, create=False)
    manifest_sha = None
    record_shas = None
    if manifest_path is not None:
        records = manifest.validate_manifest(manifest_path)
        manifest_sha = file_sha256(manifest_path)
        record_shas = {record["case_id"]: record_sha256(record) for record in records}
    simulator_path = None
    simulator_sha = None
    if simulator_binary is not None:
        simulator_path, simulator_sha = validate_simulator_binary(simulator_binary)
        simulator_path = str(simulator_path)

    state_directory = safe_output_path(root, ".b4pe/state")
    state_paths = [] if not state_directory.exists() else sorted(state_directory.glob("*.json"))
    statuses = Counter()
    missing_results = 0
    sha_mismatches = 0
    unfinished_attempts = 0
    fingerprint_drift = 0
    lock_conflicts = 0
    invalid_states = 0
    publication_statuses = Counter()
    publication_integrity_errors = 0
    orphan_results = 0
    snapshot_missing = 0
    snapshot_sha_mismatches = 0
    snapshot_provenance_drift = 0
    seen_cases = set()
    for path in state_paths:
        try:
            if path.is_symlink() or not _is_within(path.resolve(strict=True), root):
                raise InputIntegrityError("state path escapes output-root")
            state = json.loads(path.read_text(encoding="utf-8"))
            status = state["current_status"]
            if status not in PROTOCOL["states"]:
                raise InputIntegrityError("invalid state status")
            statuses[status] += 1
            seen_cases.add(state.get("case_id"))
            attempts = state.get("attempts")
            if not isinstance(attempts, list):
                raise InputIntegrityError("invalid attempts")
            unfinished_attempts += sum(
                attempt.get("ended_at") is None
                or attempt.get("termination_reason") is None
                for attempt in attempts
            )

            drift = state.get("execution_protocol_sha256") != EXECUTION_PROTOCOL_SHA256
            if manifest_sha is not None:
                drift |= state.get("manifest_file_sha256") != manifest_sha
                drift |= record_shas.get(state.get("case_id")) != state.get(
                    "manifest_record_sha256"
                )
            if simulator_path is not None:
                drift |= state.get("simulator_binary_path") != simulator_path
                drift |= state.get("simulator_binary_sha256") != simulator_sha
            for role in ("taskset", "source", "system_config"):
                relative = state.get(f"{role}_artifact_relpath")
                expected = state.get(f"{role}_artifact_sha256")
                try:
                    actual = _regular_file_sha(
                        safe_output_path(root, relative), f"{role} artifact"
                    )
                except (ExecutionError, OSError, TypeError):
                    drift = True
                else:
                    drift |= actual != expected
            fingerprint_drift += bool(drift)

            for role in ("simulator", "system", "taskset", "source"):
                relative = state.get(f"{role}_snapshot_relpath")
                expected = state.get(f"{role}_snapshot_sha256")
                observed = state.get(f"{role}_observed_original_sha256")
                legacy = state.get(
                    "simulator_binary_sha256"
                    if role == "simulator"
                    else f"{role if role != 'system' else 'system_config'}_artifact_sha256"
                )
                snapshot_provenance_drift += bool(
                    not isinstance(relative, str)
                    or not isinstance(expected, str)
                    or expected != observed
                    or expected != legacy
                )
                if isinstance(relative, str):
                    try:
                        actual = _regular_file_sha(
                            safe_output_path(root, relative), f"{role} snapshot"
                        )
                    except (ExecutionError, OSError, TypeError):
                        snapshot_missing += 1
                    else:
                        snapshot_sha_mismatches += actual != expected

            publication = attempts[-1].get("publication") if attempts else None
            publication_status = (
                publication.get("publication_status")
                if isinstance(publication, dict)
                else "none"
            )
            if publication_status not in PUBLICATION_STATUSES:
                raise InputIntegrityError("invalid publication status")
            publication_statuses[publication_status] += 1
            if publication_status in PUBLICATION_ACTIVE:
                for stream, allow_empty in (
                    ("result", False),
                    ("stdout", True),
                    ("stderr", True),
                ):
                    temporary = publication.get(f"temporary_{stream}_relpath")
                    final = publication.get(f"final_{stream}_relpath")
                    expected = publication.get(f"expected_{stream}_sha256")
                    candidates = []
                    for relative in (temporary, final):
                        try:
                            candidate = safe_output_path(root, relative)
                            if candidate.exists():
                                candidates.append(
                                    _regular_file_sha(
                                        candidate,
                                        f"publication {stream}",
                                        allow_empty=allow_empty,
                                    )
                                )
                        except (ExecutionError, OSError, TypeError):
                            publication_integrity_errors += 1
                    if not candidates or any(value != expected for value in candidates):
                        publication_integrity_errors += 1
            elif status != "succeeded":
                result_relative = state.get("result_relpath")
                try:
                    if safe_output_path(root, result_relative).exists():
                        orphan_results += 1
                except (ExecutionError, OSError, TypeError):
                    publication_integrity_errors += 1

            if status == "succeeded":
                result = safe_output_path(root, state["result_relpath"])
                if not result.exists():
                    missing_results += 1
                else:
                    try:
                        actual_result = _regular_file_sha(result, "result")
                    except ExecutionError:
                        sha_mismatches += 1
                    else:
                        sha_mismatches += actual_result != state.get("final_result_sha256")
                for stream in ("stdout", "stderr"):
                    log = safe_output_path(
                        root, f".b4pe/logs/{state['case_id']}.{stream}"
                    )
                    try:
                        actual_log = _regular_file_sha(
                            log, stream, allow_empty=True
                        )
                    except ExecutionError:
                        sha_mismatches += 1
                    else:
                        sha_mismatches += actual_log != state.get(f"{stream}_sha256")
            lock = safe_output_path(
                root, f".b4pe/locks/{state['case_id']}.lock"
            )
            lock_conflicts += _lock_is_held(lock)
        except (ExecutionError, OSError, UnicodeError, json.JSONDecodeError, KeyError):
            invalid_states += 1

    if record_shas is not None:
        record_by_case = {record["case_id"]: record for record in records}
        for case_id, record in record_by_case.items():
            if case_id not in seen_cases:
                try:
                    orphan_results += safe_output_path(
                        root, record["result_relpath"]
                    ).exists()
                except (ExecutionError, OSError):
                    publication_integrity_errors += 1

    summary_sha_mismatches = 0
    summary_directory = safe_output_path(root, ".b4pe/summaries")
    if summary_directory.exists():
        for path in summary_directory.glob("*.json"):
            try:
                if path.is_symlink() or not path.is_file():
                    raise InputIntegrityError("summary is not a regular file")
                data = path.read_bytes()
                json.loads(data.decode("utf-8"))
                summary_sha_mismatches += bytes_sha256(data) != path.stem
            except (ExecutionError, OSError, UnicodeError, json.JSONDecodeError):
                summary_sha_mismatches += 1

    status_counts = {state: statuses.get(state, 0) for state in PROTOCOL["states"]}
    return {
        "input_fingerprint_drift": fingerprint_drift,
        "invalid_states": invalid_states,
        "lock_conflicts": lock_conflicts,
        "missing_results": missing_results,
        "orphan_results": orphan_results,
        "publication_integrity_errors": publication_integrity_errors,
        "publication_status_counts": {
            name: publication_statuses.get(name, 0) for name in PUBLICATION_STATUSES
        },
        "sha_mismatches": sha_mismatches,
        "snapshot_missing": snapshot_missing,
        "snapshot_provenance_drift": snapshot_provenance_drift,
        "snapshot_sha_mismatches": snapshot_sha_mismatches,
        "state_count": len(state_paths),
        "status_counts": status_counts,
        "summary_sha_mismatches": summary_sha_mismatches,
        "unfinished_attempts": unfinished_attempts,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--simulator-binary")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = inspect_output(
            args.output_root,
            manifest_path=args.manifest,
            simulator_binary=args.simulator_binary,
        )
    except (ExecutionError, manifest.ManifestError, OSError) as exc:
        print(f"inspection failed: {exc}")
        return 1
    if args.json:
        print(manifest.compact_json(summary))
    else:
        for name, value in summary.items():
            print(f"{name}: {manifest.compact_json(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
