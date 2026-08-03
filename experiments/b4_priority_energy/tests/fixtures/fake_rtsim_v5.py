#!/usr/bin/env python3
"""Test-only V5 wrapper around the existing B4 fake trace publisher."""

import hashlib
import json
import os
import sys
from pathlib import Path

import yaml


def parse_args(argv):
    if len(argv) < 7:
        raise ValueError("expected system taskset duration -t result --run-id id")
    int(argv[2])
    return (
        Path(argv[0]),
        Path(argv[1]),
        Path(argv[argv.index("-t") + 1]),
        argv[argv.index("--run-id") + 1],
    )


def publish_trace_atomically(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(payload)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return 0


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    system_path, taskset_path, result_path, run_id = parse_args(arguments)
    source_path = Path(os.environ["B4PE_SOURCE_SNAPSHOT"])
    if not all(path.is_file() for path in (system_path, taskset_path, source_path)):
        print("missing V5 snapshot input", file=sys.stderr)
        return 9
    document = yaml.safe_load(system_path.read_text(encoding="utf-8"))
    harvesting = document.get("harvesting", {})
    if (
        harvesting.get("source") != "scaled_piecewise"
        or not harvesting.get("scaled_piecewise", {}).get("segments")
    ):
        print("missing V5 piecewise source", file=sys.stderr)
        return 10
    payload = {
        "fixture": "B4_PE_V5_STATE_MACHINE_ONLY_NOT_SCHEDULING_EVIDENCE",
        "run_id": run_id,
        "system_sha256": hashlib.sha256(system_path.read_bytes()).hexdigest(),
        "taskset_sha256": hashlib.sha256(taskset_path.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    return publish_trace_atomically(
        result_path, json.dumps(payload, sort_keys=True) + "\n",
    )


if __name__ == "__main__":
    raise SystemExit(main())
