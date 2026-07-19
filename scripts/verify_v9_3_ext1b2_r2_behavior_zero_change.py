#!/usr/bin/env python3
"""Compare pre/post native behavior while permitting additive exact fields.

The caller supplies already-materialized native input directories.  The script
runs both binaries in isolated temporary working directories, compares exit
status and the complete semantic trace after recursively removing only keys
ending in ``_exact``, and emits aggregate identities rather than trace rows.
It does not generate tasksets or execute a screening plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Iterable, Mapping, Sequence


EXACT_SUFFIX = "_exact"
PROJECTION_EVENT_TYPES = {
    "selected_tasks": {"scheduler_decision"},
    "running_tasks": {
        "scheduled", "descheduled", "end_instance", "dline_miss", "kill",
    },
    "dispatch_sequence": {"scheduled"},
    "preemption_sequence": {"descheduled"},
    "battery_trajectory": {
        "scheduler_decision", "sync_batch_block", "sync_batch_candidate_wait",
    },
    "energy_consumption": {
        "scheduler_decision", "scheduled", "sync_batch_block",
        "sync_batch_candidate_wait",
    },
    "deadline_misses": {"dline_miss"},
    "completion": {"end_instance"},
    "synchronization_wait": {
        "sync_batch_block", "sync_batch_candidate_wait", "simulation_run_outcome",
    },
    "terminal_result": {"simulation_run_outcome"},
}


class BehaviorProofError(RuntimeError):
    """A native behavior comparison failed closed."""


def strip_additive_exact_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_additive_exact_fields(item)
            for key, item in value.items()
            if not key.endswith(EXACT_SUFFIX)
        }
    if isinstance(value, list):
        return [strip_additive_exact_fields(item) for item in value]
    return value


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _walk_exact_fields(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith(EXACT_SUFFIX):
                yield key, item
            yield from _walk_exact_fields(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_exact_fields(item)


def _validate_exact_fields(document: Mapping[str, Any]) -> int:
    count = 0
    for key, value in _walk_exact_fields(document):
        if not isinstance(value, str) or not value:
            raise BehaviorProofError(f"non-text additive exact field: {key}")
        try:
            number = float(value)
        except ValueError as exc:
            raise BehaviorProofError(f"invalid additive exact field: {key}") from exc
        if not math.isfinite(number):
            raise BehaviorProofError(f"non-finite additive exact field: {key}")
        count += 1
    return count


def _projection(document: Mapping[str, Any], event_types: set[str]) -> list[Any]:
    events = document.get("events")
    if not isinstance(events, list):
        raise BehaviorProofError("semantic trace has no event list")
    return [
        event for event in events
        if isinstance(event, dict) and event.get("event_type") in event_types
    ]


def _run(
    binary: Path, input_dir: Path, work_dir: Path, data_dir: Path,
    duration: int, run_id: str,
) -> tuple[int, Dict[str, Any]]:
    work_dir.mkdir(parents=True)
    (work_dir / "data").symlink_to(data_dir, target_is_directory=True)
    trace_path = work_dir / "trace.json"
    command = [
        str(binary), "--semantic-traces", "--run-id", run_id,
        "--taskset-semantic-hash", hashlib.sha256(
            (input_dir / "taskset.yaml").read_bytes()
        ).hexdigest(),
        "-t", str(trace_path), str(input_dir / "system_config.yaml"),
        str(input_dir / "taskset.yaml"), str(duration),
    ]
    completed = subprocess.run(
        command, cwd=work_dir, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False,
    )
    if not trace_path.is_file():
        raise BehaviorProofError("native comparison produced no semantic trace")
    try:
        document = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BehaviorProofError("native comparison produced invalid JSON") from exc
    if not isinstance(document, dict):
        raise BehaviorProofError("native semantic trace must be an object")
    return completed.returncode, document


def compare_case(
    old_binary: Path, new_binary: Path, input_dir: Path, data_dir: Path,
    duration: int, scratch: Path, case_index: int,
) -> Dict[str, Any]:
    run_id = f"EXT1B_B2_R2_BEHAVIOR_PROOF_CASE_{case_index}"
    old_code, old_document = _run(
        old_binary, input_dir, scratch / "old", data_dir, duration, run_id,
    )
    new_code, new_document = _run(
        new_binary, input_dir, scratch / "new", data_dir, duration, run_id,
    )
    if old_code != new_code:
        raise BehaviorProofError("native exit status changed")
    old_exact_count = _validate_exact_fields(old_document)
    new_exact_count = _validate_exact_fields(new_document)
    old_compat = strip_additive_exact_fields(old_document)
    new_compat = strip_additive_exact_fields(new_document)
    if old_compat != new_compat:
        raise BehaviorProofError("semantic trace changed outside additive exact fields")
    projections = {
        name: {
            "event_count": len(_projection(old_compat, event_types)),
            "identity": _canonical_hash(_projection(old_compat, event_types)),
        }
        for name, event_types in sorted(PROJECTION_EVENT_TYPES.items())
    }
    events = old_compat.get("events", [])
    return {
        "case_index": case_index,
        "scheduler": old_compat.get("configured_scheduler"),
        "exit_code": old_code,
        "event_count": len(events),
        "compatibility_trace_identity": _canonical_hash(old_compat),
        "old_exact_field_count": old_exact_count,
        "new_exact_field_count": new_exact_count,
        "projections": projections,
    }


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-binary", required=True, type=Path)
    parser.add_argument("--new-binary", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--duration", type=int, default=400)
    parser.add_argument("inputs", nargs="+", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args.duration <= 0:
        raise BehaviorProofError("duration must be positive")
    for path in (args.old_binary, args.new_binary):
        if not path.is_file():
            raise BehaviorProofError("native binary does not exist")
    if not args.data_dir.is_dir():
        raise BehaviorProofError("data directory does not exist")
    summaries = []
    with tempfile.TemporaryDirectory(prefix="ext1b_b2_r2_behavior_proof.") as tmp:
        root = Path(tmp)
        for index, input_dir in enumerate(args.inputs):
            if not (input_dir / "system_config.yaml").is_file():
                raise BehaviorProofError("input is missing system_config.yaml")
            if not (input_dir / "taskset.yaml").is_file():
                raise BehaviorProofError("input is missing taskset.yaml")
            summaries.append(compare_case(
                args.old_binary, args.new_binary, input_dir, args.data_dir,
                args.duration, root / f"case_{index}", index,
            ))
    result = {
        "schema": "EXT1B_B2_R2_BEHAVIOR_ZERO_CHANGE_PROOF_V1",
        "status": "PASSED",
        "case_count": len(summaries),
        "allowed_difference": "additive keys ending in _exact only",
        "cases": summaries,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
