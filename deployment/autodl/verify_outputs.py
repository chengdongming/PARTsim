#!/usr/bin/env python3
"""Fail-closed integrity and cardinality checks for bounded/formal runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.aggregation import (  # noqa: E402
    validate_run_closure_read_only,
)
from experiments.v9_3.config import load_config  # noqa: E402
from experiments.v9_3.derived_outputs import (  # noqa: E402
    build_core1_derived_outputs, build_core2_derived_outputs,
    validate_persisted_derived_bundle,
)
from experiments.v9_3.output_inventory import (  # noqa: E402
    AUTHORITATIVE_RAW, PACKAGE_ARCHIVE_SCHEMA, PACKAGE_ARCHIVE_SCHEMA_VERSION,
    PACKAGE_FILE_TYPE,
    VERIFIED_PACKAGE_MANIFEST_SCHEMA, VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
    OutputInventory, inventory_for_closure, validate_hash_manifest,
    validate_inventory_paths, verified_package_entries,
)
from experiments.v9_3.result_writer import atomic_write_json  # noqa: E402


EXPERIMENTS = ("core1", "core2", "core3", "core4", "core5", "ext1", "ext2", "ext4")
FORMAL_EXPERIMENTS = ("core1", "core2")


def rows(path: Path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_hashes(root: Path) -> None:
    manifest = root / "file_hashes.sha256"
    if not manifest.is_file():
        raise RuntimeError(f"missing hash manifest: {manifest}")
    recorded = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise RuntimeError(f"malformed hash manifest: {manifest}") from exc
        if relative in recorded:
            raise RuntimeError(f"duplicate hash path: {relative}")
        recorded[relative] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "file_hashes.sha256"
    }
    if set(recorded) != actual:
        raise RuntimeError(f"hash manifest path set mismatch: {root}")
    for relative, digest in recorded.items():
        path = root / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
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


def _required_core12_paths(root: Path, name: str) -> None:
    required_files = (
        "run_config.yaml",
        "run_metadata.json",
        "formal_authorization_seal.json",
        "checkpoint.json",
        "analysis_requests.csv",
        "analysis_attempts.csv",
        "generated_tasksets.csv",
        "cells.csv",
        "per_taskset_results.csv",
        "per_task_results.csv",
        "dependency_records.csv",
        "dominance_checks.csv",
        "failures.csv",
    )
    missing = [item for item in required_files if not (root / item).is_file()]
    if missing:
        raise RuntimeError(f"{name}: missing required files: {missing}")
    for dirname in ("terminal_results", "result_state"):
        if not (root / dirname).is_dir():
            raise RuntimeError(f"{name}: missing required directory: {dirname}")


def verify_core12_output(name: str, root: Path) -> OutputInventory:
    expected_core = {"core1": "CORE-1", "core2": "CORE-2"}[name]
    _required_core12_paths(root, name)

    # Parse frozen identity inputs before validating any derived artifact.
    config = load_config(root / "run_config.yaml", expected_core=expected_core)
    metadata = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
    if config["core"] != expected_core or metadata.get("core") != expected_core:
        raise RuntimeError(f"{name}: output directory identity mismatch")

    # This is the exact same authoritative, write-free gate used immediately
    # before aggregation produces any summary or plot artifact.
    closure = validate_run_closure_read_only(root, expected_core)
    bundle = (
        build_core1_derived_outputs(closure)
        if expected_core == "CORE-1"
        else build_core2_derived_outputs(closure)
    )
    inventory = inventory_for_closure(
        expected_core, closure, bundle.required_paths
    )
    actual = validate_inventory_paths(root, inventory)
    validate_persisted_derived_bundle(root, expected_core, bundle)

    # Hashes are an integrity check only and deliberately run last.
    validate_hash_manifest(root, inventory, actual)
    return inventory


def _legacy_verified_package_entries(root: Path):
    """Bound packaging of out-of-scope experiment types to hash-verified files."""

    output = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"package source contains symlink: {path}")
        if not path.is_file():
            continue
        if not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("platform lacks O_NOFOLLOW for package metadata")
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as exc:
            raise RuntimeError(
                f"cannot open package source without following links: {path}"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError(f"package source is not regular: {path}")
            digest = hashlib.sha256()
            size = 0
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                size += len(block)
            after = os.fstat(descriptor)
            frozen = lambda value: (
                value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns,
            )
            if frozen(before) != frozen(after) or size != before.st_size:
                raise RuntimeError(
                    f"package source changed while recording metadata: {path}"
                )
        finally:
            os.close(descriptor)
        output.append({
            "relative_path": path.relative_to(root).as_posix(),
            "file_type": PACKAGE_FILE_TYPE,
            "sha256": digest.hexdigest(),
            "mode": stat.S_IMODE(before.st_mode),
            "size": size,
            "category": AUTHORITATIVE_RAW,
            "package": True,
            "inventory_schema": "ASAP_BLOCK_V9_3_LEGACY_HASH_VERIFIED_V1",
            "inventory_schema_version": 1,
            "authoritative_scientific_content": True,
        })
    return tuple(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--profile", choices=("smoke", "formal"), default="smoke")
    parser.add_argument(
        "--experiment",
        choices=FORMAL_EXPERIMENTS,
        help="verify one bounded CORE-1/CORE-2 output (for canaries)",
    )
    parser.add_argument(
        "--package-manifest",
        type=Path,
        help="write the verified package allowlist outside the output root",
    )
    args = parser.parse_args()
    expected = (
        (args.experiment,)
        if args.experiment else
        EXPERIMENTS if args.profile == "smoke" else FORMAL_EXPERIMENTS
    )
    package_entries = []
    for name in expected:
        root = args.output_root / name
        if not root.is_dir():
            raise RuntimeError(f"missing experiment output directory: {root}")
        if name in FORMAL_EXPERIMENTS:
            inventory = verify_core12_output(name, root)
            if args.package_manifest is not None:
                entries = verified_package_entries(root, inventory)
                package_entries.extend(
                    {
                        **entry,
                        "relative_path": f"{name}/{entry['relative_path']}",
                    }
                    for entry in entries
                )
            continue
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
        if args.package_manifest is not None:
            package_entries.extend(
                {
                    **entry,
                    "relative_path": f"{name}/{entry['relative_path']}",
                }
                for entry in _legacy_verified_package_entries(root)
            )
    if args.package_manifest is not None:
        output_root = args.output_root.resolve()
        destination = args.package_manifest.resolve()
        if destination == output_root or output_root in destination.parents:
            raise RuntimeError("package manifest must be outside the output root")
        atomic_write_json(destination, {
            "schema": VERIFIED_PACKAGE_MANIFEST_SCHEMA,
            "schema_version": VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION,
            "archive_schema": PACKAGE_ARCHIVE_SCHEMA,
            "archive_schema_version": PACKAGE_ARCHIVE_SCHEMA_VERSION,
            "output_root": str(output_root),
            "profile": args.profile,
            "experiments": list(expected),
            "inventory_schemas": sorted({
                (
                    entry["inventory_schema"],
                    entry["inventory_schema_version"],
                )
                for entry in package_entries
            }),
            "entries": package_entries,
        })
    print(f"verified {len(expected)} {args.profile} experiment output directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
