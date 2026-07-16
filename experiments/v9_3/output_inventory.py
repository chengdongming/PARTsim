"""Frozen CORE-1/CORE-2 output inventory and hash/package policy.

The inventory is constructed from an already validated request/result closure.
It is the single path allowlist shared by aggregation, deployment verification,
and packaging.  Presentation images are allowed and packaged, but they are not
authoritative scientific inputs and are not members of ``file_hashes.sha256``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterable, Mapping, Sequence

from .plotting_data import CORE_PLOT_TYPES
from .result_writer import atomic_write_text


OUTPUT_INVENTORY_SCHEMA = "ASAP_BLOCK_V9_3_OUTPUT_INVENTORY_V1"
OUTPUT_INVENTORY_SCHEMA_VERSION = 1
VERIFIED_PACKAGE_MANIFEST_SCHEMA = "ASAP_BLOCK_V9_3_VERIFIED_PACKAGE_PATHS_V2"
VERIFIED_PACKAGE_MANIFEST_SCHEMA_VERSION = 2
PACKAGE_ARCHIVE_SCHEMA = "ASAP_BLOCK_V9_3_VERIFIED_PACKAGE_ARCHIVE_V1"
PACKAGE_ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_MANIFEST_SCHEMA = "ASAP_BLOCK_V9_3_ARCHIVE_MANIFEST_V1"
ARCHIVE_MANIFEST_SCHEMA_VERSION = 1
PACKAGE_FILE_TYPE = "regular"

AUTHORITATIVE_RAW = "authoritative_raw"
AUTHORITATIVE_DERIVED = "authoritative_derived"
OPTIONAL_PRESENTATION = "optional_presentation"
ADMINISTRATIVE_EVIDENCE = "administrative_evidence"
PACKAGE_CATEGORIES = frozenset({
    AUTHORITATIVE_RAW,
    AUTHORITATIVE_DERIVED,
    OPTIONAL_PRESENTATION,
    ADMINISTRATIVE_EVIDENCE,
})

STATIC_RAW_PATHS = (
    "run_config.yaml",
    "system_config.yaml",
    "run_metadata.json",
    "formal_authorization_seal.json",
    "checkpoint.json",
    "taskset_pairing_manifest.json",
    "cells.csv",
    "generated_tasksets.csv",
    "analysis_requests.csv",
    "analysis_attempts.csv",
    "per_taskset_results.csv",
    "per_task_results.csv",
    "dependency_records.csv",
    "dominance_checks.csv",
    "failures.csv",
)

FORBIDDEN_LEGACY_DERIVED_NAMESPACES = (
    "summary_v*.json",
    "summary.old.json",
    "*_plot_data_v*.csv",
    "unknown comparison/ablation CSV",
    "unknown plot type under plots/",
    "temporary or hidden derived file",
    "legacy derived schema namespace",
)


class OutputInventoryError(RuntimeError):
    """Raised when an output root is not exactly one frozen inventory."""


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    category: str
    required: bool
    include_in_hash: bool
    include_in_package: bool
    authoritative_scientific_content: bool


@dataclass(frozen=True)
class OutputInventory:
    schema: str
    schema_version: int
    core: str
    entries: tuple[InventoryEntry, ...]
    forbidden_legacy_derived_namespaces: tuple[str, ...]

    @property
    def by_path(self) -> Mapping[str, InventoryEntry]:
        return {entry.path: entry for entry in self.entries}

    @property
    def allowed_paths(self) -> frozenset[str]:
        return frozenset(self.by_path)

    @property
    def required_paths(self) -> frozenset[str]:
        return frozenset(entry.path for entry in self.entries if entry.required)

    @property
    def optional_presentation_paths(self) -> frozenset[str]:
        return frozenset(
            entry.path for entry in self.entries
            if entry.category == OPTIONAL_PRESENTATION
        )

    @property
    def derived_paths(self) -> frozenset[str]:
        return frozenset(
            entry.path for entry in self.entries
            if entry.category == AUTHORITATIVE_DERIVED
        )


def _safe_relative_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OutputInventoryError(f"{label} is empty")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OutputInventoryError(f"unsafe {label}: {value!r}")
    normalized = path.as_posix()
    if normalized != value or "\\" in value:
        raise OutputInventoryError(f"non-canonical {label}: {value!r}")
    return value


def _artifact_id(value: str, label: str) -> str:
    value = _safe_relative_path(str(value), label)
    if "/" in value:
        raise OutputInventoryError(f"{label} is not a filename atom: {value!r}")
    return value


def build_expected_output_inventory(
    expected_core: str,
    *,
    analysis_ids: Iterable[str],
    state_analysis_ids: Iterable[str],
    failure_analysis_ids: Iterable[str] = (),
    derived_paths: Sequence[str],
) -> OutputInventory:
    """Build the sole machine-consumable path policy from frozen identities."""

    if expected_core not in CORE_PLOT_TYPES:
        raise OutputInventoryError(f"unsupported inventory core: {expected_core}")
    analysis = tuple(sorted({_artifact_id(item, "analysis ID") for item in analysis_ids}))
    states = tuple(sorted({_artifact_id(item, "state analysis ID") for item in state_analysis_ids}))
    failures = tuple(sorted({_artifact_id(item, "failure analysis ID") for item in failure_analysis_ids}))
    if not set(states).issubset(analysis):
        raise OutputInventoryError("result-state IDs are not a subset of analysis IDs")
    if not set(failures).issubset(analysis):
        raise OutputInventoryError("failure-input IDs are not a subset of analysis IDs")

    entries: dict[str, InventoryEntry] = {}

    def add(
        path: str,
        category: str,
        *,
        required: bool = True,
        include_in_hash: bool = True,
        include_in_package: bool = True,
        authoritative: bool,
    ) -> None:
        path = _safe_relative_path(path, "inventory path")
        if path in entries:
            raise OutputInventoryError(f"duplicate inventory path: {path}")
        entries[path] = InventoryEntry(
            path, category, required, include_in_hash, include_in_package,
            authoritative,
        )

    for path in STATIC_RAW_PATHS:
        add(path, AUTHORITATIVE_RAW, authoritative=True)
    for analysis_id in analysis:
        add(
            f"terminal_results/{analysis_id}.json",
            AUTHORITATIVE_RAW,
            authoritative=True,
        )
    for analysis_id in states:
        add(
            f"result_state/{analysis_id}.pickle",
            AUTHORITATIVE_RAW,
            authoritative=True,
        )
    for analysis_id in failures:
        add(
            f"failure_inputs/{analysis_id}.json",
            AUTHORITATIVE_RAW,
            authoritative=True,
        )
    for path in derived_paths:
        add(path, AUTHORITATIVE_DERIVED, authoritative=True)
    for plot_type in CORE_PLOT_TYPES[expected_core]:
        for extension in ("png", "pdf"):
            add(
                f"plots/{plot_type}.{extension}",
                OPTIONAL_PRESENTATION,
                required=False,
                include_in_hash=False,
                authoritative=False,
            )
    add(
        "file_hashes.sha256",
        ADMINISTRATIVE_EVIDENCE,
        include_in_hash=False,
        authoritative=False,
    )
    return OutputInventory(
        schema=OUTPUT_INVENTORY_SCHEMA,
        schema_version=OUTPUT_INVENTORY_SCHEMA_VERSION,
        core=expected_core,
        entries=tuple(entries[path] for path in sorted(entries)),
        forbidden_legacy_derived_namespaces=FORBIDDEN_LEGACY_DERIVED_NAMESPACES,
    )


def inventory_for_closure(
    expected_core: str, closure: object, derived_paths: Sequence[str]
) -> OutputInventory:
    requests = tuple(getattr(closure, "requests"))
    tasksets = tuple(getattr(closure, "tasksets"))
    failures = tuple(getattr(closure, "failures", ()))
    return build_expected_output_inventory(
        expected_core,
        analysis_ids=(row["analysis_id"] for row in requests),
        state_analysis_ids=(
            row["analysis_id"] for row in tasksets
            if row["terminal_origin"] == "PRODUCTION_ANALYZER"
        ),
        failure_analysis_ids=(
            row["analysis_id"] for row in failures if row.get("analysis_id")
        ),
        derived_paths=derived_paths,
    )


def actual_output_files(root: Path) -> frozenset[str]:
    """List regular files without following symlink-based namespace escapes."""

    root = Path(root)
    actual: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise OutputInventoryError(f"output inventory contains symlink: {relative}")
        if path.is_dir():
            continue
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            raise OutputInventoryError(f"cannot stat output path: {relative}") from exc
        if not stat.S_ISREG(mode):
            raise OutputInventoryError(f"output path is not a regular file: {relative}")
        actual.add(_safe_relative_path(relative, "actual output path"))
    return frozenset(actual)


def validate_inventory_paths(
    root: Path,
    inventory: OutputInventory,
    *,
    allowed_missing: Iterable[str] = (),
) -> frozenset[str]:
    actual = actual_output_files(root)
    unknown = sorted(actual - inventory.allowed_paths)
    if unknown:
        raise OutputInventoryError(
            f"{inventory.core}: unknown or forbidden output paths: {unknown}"
        )
    missing_allowed = frozenset(allowed_missing)
    missing = sorted(inventory.required_paths - actual - missing_allowed)
    if missing:
        raise OutputInventoryError(
            f"{inventory.core}: missing required output paths: {missing}"
        )
    return actual


def validate_aggregation_inventory(
    root: Path, inventory: OutputInventory
) -> str:
    """Accept exactly a fresh raw closure or a complete current-schema root."""

    derived = inventory.derived_paths
    hash_path = "file_hashes.sha256"
    allowed_missing = derived | {hash_path}
    actual = validate_inventory_paths(root, inventory, allowed_missing=allowed_missing)
    present_derived = actual & derived
    presentations = actual & inventory.optional_presentation_paths
    if not present_derived and hash_path not in actual and not presentations:
        return "fresh"
    if present_derived == derived and hash_path in actual:
        validate_inventory_paths(root, inventory)
        return "complete"
    raise OutputInventoryError(
        f"{inventory.core}: partial, legacy, or mixed derived output state"
    )


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def validate_hash_manifest(
    root: Path, inventory: OutputInventory, actual: frozenset[str] | None = None
) -> Mapping[str, str]:
    actual = actual if actual is not None else validate_inventory_paths(root, inventory)
    manifest = Path(root) / "file_hashes.sha256"
    if not manifest.is_file():
        raise OutputInventoryError(f"missing hash manifest: {manifest}")
    recorded: dict[str, str] = {}
    for number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise OutputInventoryError(
                f"malformed hash manifest line {number}: {manifest}"
            ) from exc
        relative = _safe_relative_path(relative, "hash manifest path")
        if not _SHA256_RE.fullmatch(digest):
            raise OutputInventoryError(
                f"invalid SHA-256 in hash manifest line {number}: {manifest}"
            )
        if relative in recorded:
            raise OutputInventoryError(f"duplicate hash path: {relative}")
        entry = inventory.by_path.get(relative)
        if entry is None or not entry.include_in_hash:
            raise OutputInventoryError(f"hash manifest contains forbidden path: {relative}")
        recorded[relative] = digest
    expected = {
        path for path in actual if inventory.by_path[path].include_in_hash
    }
    if set(recorded) != expected:
        raise OutputInventoryError(
            f"hash manifest/inventory path set mismatch: {root}"
        )
    for relative, digest in recorded.items():
        if hashlib.sha256((Path(root) / relative).read_bytes()).hexdigest() != digest:
            raise OutputInventoryError(f"hash mismatch: {Path(root) / relative}")
    return recorded


def write_inventory_hashes(root: Path, inventory: OutputInventory) -> None:
    root = Path(root)
    actual = validate_inventory_paths(
        root, inventory, allowed_missing={"file_hashes.sha256"}
    )
    rows = []
    for relative in sorted(actual):
        if not inventory.by_path[relative].include_in_hash:
            continue
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        rows.append(f"{digest}  {relative}")
    atomic_write_text(root / "file_hashes.sha256", "\n".join(rows) + "\n")


def verified_package_entries(
    root: Path, inventory: OutputInventory
) -> tuple[Mapping[str, object], ...]:
    root = Path(root)
    actual = validate_inventory_paths(root, inventory)
    validate_hash_manifest(root, inventory, actual)
    output = []
    for relative in sorted(actual):
        entry = inventory.by_path[relative]
        if not entry.include_in_package:
            continue
        if not hasattr(os, "O_NOFOLLOW"):
            raise OutputInventoryError(
                "platform lacks O_NOFOLLOW for verified package metadata"
            )
        path = root / relative
        try:
            descriptor = os.open(
                path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            )
        except OSError as exc:
            raise OutputInventoryError(
                f"cannot open package source without following links: {relative}"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise OutputInventoryError(
                    f"package source is not a regular file: {relative}"
                )
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
                raise OutputInventoryError(
                    f"package source changed while recording metadata: {relative}"
                )
        finally:
            os.close(descriptor)
        output.append({
            "relative_path": relative,
            "file_type": PACKAGE_FILE_TYPE,
            "mode": stat.S_IMODE(before.st_mode),
            "size": size,
            "sha256": digest.hexdigest(),
            "category": entry.category,
            "package": True,
            "inventory_schema": inventory.schema,
            "inventory_schema_version": inventory.schema_version,
            "authoritative_scientific_content": (
                entry.authoritative_scientific_content
            ),
        })
    return tuple(output)
