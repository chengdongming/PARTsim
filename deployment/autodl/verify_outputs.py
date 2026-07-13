#!/usr/bin/env python3
"""Fail-closed integrity and cardinality checks for bounded/formal runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


EXPERIMENTS = ("core1", "core2", "core3", "core4", "core5", "ext1", "ext2", "ext4")
FORMAL_EXPERIMENTS = tuple(name for name in EXPERIMENTS if name != "ext2")


def rows(path: Path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_hashes(root: Path) -> None:
    manifest = root / "file_hashes.sha256"
    if not manifest.is_file():
        raise RuntimeError(f"missing hash manifest: {manifest}")
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"hash mismatch: {path}")


def assert_unique(root: Path, filename: str, field: str) -> None:
    values = [row[field] for row in rows(root / filename)]
    if len(values) != len(set(values)):
        raise RuntimeError(f"duplicate {field} in {root / filename}")


def checkpoint_counts(name: str, value: dict) -> tuple[int, int]:
    if name in {"core1", "core2"}:
        return int(value["requested_count"]), int(value["completed_count"])
    if name in {"core4", "core5"}:
        return int(value["requested_count"]), int(value["terminal_count"])
    if name in {"ext1", "ext2"}:
        return int(value["requested"]), int(value["terminal"])
    if name == "core3":
        requested = int(value["requested_rta_count"]) + int(value["requested_simulation_count"])
        terminal = int(value["completed_rta_count"]) + int(value["completed_simulation_count"])
        return requested, terminal
    requested = int(value["rta_requested"]) + int(value["simulation_requested"])
    terminal = int(value["rta_terminal"]) + int(value["simulation_terminal"])
    return requested, terminal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--profile", choices=("smoke", "formal"), default="smoke")
    args = parser.parse_args()
    expected = EXPERIMENTS if args.profile == "smoke" else FORMAL_EXPERIMENTS
    for name in expected:
        root = args.output_root / name
        if not root.is_dir():
            raise RuntimeError(f"missing experiment output directory: {root}")
        checkpoint = root / "checkpoint.json"
        summary_name = {
            "core4": "sensitivity_summary.json",
            "core5": "scalability_summary.json",
        }.get(name, "summary.json")
        summary = root / summary_name
        if not checkpoint.is_file() or not summary.is_file():
            raise RuntimeError(f"{name}: missing checkpoint or summary")
        requested, terminal = checkpoint_counts(
            name, json.loads(checkpoint.read_text(encoding="utf-8"))
        )
        if requested != terminal:
            raise RuntimeError(f"{name}: requested={requested}, terminal={terminal}")
        for filename, field in (
            ("analysis_attempts.csv", "attempt_id"),
            ("analysis_requests.csv", "analysis_id"),
            ("simulation_requests.csv", "request_id"),
        ):
            if (root / filename).is_file():
                assert_unique(root, filename, field)
        verify_hashes(root)
    print(f"verified {len(expected)} {args.profile} experiment output directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
