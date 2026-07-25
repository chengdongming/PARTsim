"""Fail-closed writer for the independent RTA4 formal result namespace."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .result_writer import (
    TABLES as LEGACY_TABLES,
    atomic_write_json,
    append_csv_row,
    read_csv,
    validate_csv_header,
    write_csv,
)
from .rta4_formal_config import RTA4_FORMAL_SCHEMA_VERSION, canonical_json
from .rta4_formal_config import validate_rta4_formal_config
from .rta4_formal_manifest import (
    NONFORMAL_TEST_FIXTURE, RTA4_CONFIG_CHECKPOINT, RTA4_PLAN_MANIFEST,
    build_trusted_plan_manifest, config_checkpoint,
)
from .rta4_formal_rows import RTA4FormalRowError, normalize_formal_row
from .rta4_formal_schema import (
    FORMAL_TABLES, RTA4_FORMAL_SCHEMA_MANIFEST, formal_schema_hash,
    formal_schema_manifest,
)
from .rta4_formal_store import RTA4FormalTasksetStore


FORMAL_RUN_METADATA = "formal_run_metadata.json"
FORMAL_TERMINAL_DIRECTORY = "formal_terminal_results"
FORMAL_FAILURE_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})


class RTA4FormalWriterError(RuntimeError):
    """Raised before a mismatched namespace or conflicting row is mutated."""


def _strict_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RTA4FormalWriterError(f"cannot read canonical JSON: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise RTA4FormalWriterError(f"JSON root is not a mapping: {path.name}")
    return value


def _root_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {path.name for path in root.iterdir()}


class RTA4FormalResultWriter:
    """Exact-header CSV writer plus atomic, idempotent terminal JSONs."""

    def __init__(
        self, root: Path | str, *, config: Mapping[str, Any],
        fixture_ordinals: Iterable[int] = (),
        execution_class: str = NONFORMAL_TEST_FIXTURE,
    ) -> None:
        if execution_class != NONFORMAL_TEST_FIXTURE:
            raise RTA4FormalWriterError(
                "PR-D refuses every FORMAL execution; PR-E authorization does not exist"
            )
        self.config = validate_rta4_formal_config(config)
        self.core = self.config["core"]
        self.config_checkpoint = config_checkpoint(self.config)
        self.plan_manifest = build_trusted_plan_manifest(
            self.config, execution_class=execution_class,
            fixture_ordinals=tuple(fixture_ordinals),
        )
        self.root = Path(root)
        self.plan_sha256 = self.plan_manifest["manifest_sha256"]
        self.config_semantic_hash = self.config_checkpoint["config_semantic_hash"]
        self.schema_sha256 = formal_schema_hash()
        self.terminals = self.root / FORMAL_TERMINAL_DIRECTORY
        marker = self.root / RTA4_FORMAL_SCHEMA_MANIFEST
        metadata_path = self.root / FORMAL_RUN_METADATA
        checkpoint_path = self.root / RTA4_CONFIG_CHECKPOINT
        plan_manifest_path = self.root / RTA4_PLAN_MANIFEST
        existing_names = _root_files(self.root)
        if existing_names.intersection(LEGACY_TABLES):
            raise RTA4FormalWriterError(
                "legacy ResultWriter tables cannot be opened as the RTA4 schema"
            )
        if existing_names and not marker.is_file():
            raise RTA4FormalWriterError(
                "refusing output root without the RTA4 schema namespace marker"
            )
        expected_manifest = formal_schema_manifest()
        if marker.is_file() and dict(_strict_json(marker)) != expected_manifest:
            raise RTA4FormalWriterError("formal schema mismatch; refusing resume")
        if checkpoint_path.is_file() and dict(_strict_json(checkpoint_path)) != self.config_checkpoint:
            raise RTA4FormalWriterError("trusted config checkpoint mismatch; refusing resume")
        if plan_manifest_path.is_file() and dict(_strict_json(plan_manifest_path)) != self.plan_manifest:
            raise RTA4FormalWriterError("trusted plan manifest mismatch; refusing resume")
        metadata = {
            "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
            "schema_sha256": self.schema_sha256,
            "plan_sha256": self.plan_sha256,
            "full_plan_sha256": self.plan_manifest["full_plan_sha256"],
            "config_semantic_hash": self.config_semantic_hash,
            "core": self.core,
            "parameter_status": self.config["experiment_contract"]["parameter_status"],
            "execution_class": execution_class,
            "formal_authorized": False,
        }
        if metadata_path.is_file() and dict(_strict_json(metadata_path)) != metadata:
            raise RTA4FormalWriterError("plan/config/run metadata mismatch; refusing resume")
        for name, columns in FORMAL_TABLES.items():
            try:
                validate_csv_header(self.root / name, columns)
            except Exception as exc:
                raise RTA4FormalWriterError(str(exc)) from exc
        self.terminals.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            atomic_write_json(marker, expected_manifest)
        if not checkpoint_path.is_file():
            atomic_write_json(checkpoint_path, self.config_checkpoint)
        if not plan_manifest_path.is_file():
            atomic_write_json(plan_manifest_path, self.plan_manifest)
        if not metadata_path.is_file():
            atomic_write_json(metadata_path, metadata)
        for name, columns in FORMAL_TABLES.items():
            path = self.root / name
            if not path.exists():
                write_csv(path, columns, ())
        self._attempt_ids = {
            row["attempt_id"] for row in read_csv(self.root / "formal_rta_attempts.csv")
        }

    def _complete_row(self, table: str, row: Mapping[str, Any]) -> Dict[str, Any]:
        common = {
            "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
            "schema_sha256": self.schema_sha256,
            "plan_sha256": self.plan_sha256,
            "config_semantic_hash": self.config_semantic_hash,
        }
        try:
            return normalize_formal_row(table, row, common)
        except RTA4FormalRowError as exc:
            raise RTA4FormalWriterError(str(exc)) from exc

    def append(self, table: str, row: Mapping[str, Any]) -> None:
        completed = self._complete_row(table, row)
        if table == "formal_failures.csv":
            severity = completed.get("severity")
            if severity not in FORMAL_FAILURE_SEVERITIES:
                raise RTA4FormalWriterError("unknown failure severity")
        append_csv_row(self.root / table, FORMAL_TABLES[table], completed)

    def append_unique(self, table: str, identity_column: str, row: Mapping[str, Any]) -> None:
        """Idempotently append one canonical identity row or reject conflict."""

        if identity_column not in FORMAL_TABLES.get(table, ()):
            raise RTA4FormalWriterError("unique identity column is not in table schema")
        completed = self._complete_row(table, row)
        existing = {
            item[identity_column]: item for item in read_csv(self.root / table)
        }
        identity = completed[identity_column]
        if identity in existing:
            if existing[identity] != completed:
                raise RTA4FormalWriterError(f"{table} identity conflict")
            return
        append_csv_row(self.root / table, FORMAL_TABLES[table], completed)

    def append_attempt(self, row: Mapping[str, Any]) -> None:
        attempt_id = str(row.get("attempt_id", ""))
        if not attempt_id:
            raise RTA4FormalWriterError("attempt_id must be non-empty")
        if attempt_id in self._attempt_ids:
            raise RTA4FormalWriterError(f"duplicate attempt_id: {attempt_id}")
        self.append("formal_rta_attempts.csv", row)
        self._attempt_ids.add(attempt_id)

    def write_terminal(self, execution_run_id: str, payload: Mapping[str, Any]) -> None:
        if not isinstance(execution_run_id, str) or len(execution_run_id) != 64:
            raise RTA4FormalWriterError("execution_run_id must be a SHA-256 identity")
        required = {"plan_record_id", "analysis_id", "request_id", "solver_status", "exact_result_hash"}
        if not required.issubset(payload):
            raise RTA4FormalWriterError(
                f"terminal payload lacks required fields: {sorted(required - set(payload))}"
            )
        completed = {
            "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
            "schema_sha256": self.schema_sha256,
            "plan_sha256": self.plan_sha256,
            "config_semantic_hash": self.config_semantic_hash,
            "execution_run_id": execution_run_id,
            **dict(payload),
        }
        path = self.terminals / f"{execution_run_id}.json"
        if path.is_file():
            existing = _strict_json(path)
            if canonical_json(existing) != canonical_json(completed):
                raise RTA4FormalWriterError(f"terminal result conflict for {execution_run_id}")
            return
        atomic_write_json(path, completed)

    def persist_taskset(
        self, store: RTA4FormalTasksetStore, certificate: Any,
    ) -> None:
        rows = store.put(certificate)
        certificate_directory = self.root / "formal_taskset_certificates"
        certificate_directory.mkdir(parents=True, exist_ok=True)
        certificate_path = certificate_directory / f"{certificate.taskset_id}.json"
        payload = certificate.canonical_bytes()
        if certificate_path.is_file() and certificate_path.read_bytes() != payload:
            raise RTA4FormalWriterError("run-local taskset certificate conflict")
        if not certificate_path.is_file():
            from .result_writer import atomic_write_text
            atomic_write_text(certificate_path, payload.decode("utf-8"))
        local_path = certificate_path.relative_to(self.root).as_posix()
        payload_hash = hashlib.sha256(payload).hexdigest()
        skeleton_row = {
            **rows.skeleton,
            "certificate_path": local_path,
            "certificate_sha256": payload_hash,
        }
        taskset_row = {
            **rows.taskset,
            "certificate_path": local_path,
            "certificate_sha256": payload_hash,
        }
        self._append_skeleton_unique(skeleton_row)
        self._append_identity_unique(
            "formal_tasksets.csv", "taskset_id", taskset_row,
        )
        existing_tasks = {
            (row["taskset_id"], row["task_id"]): row
            for row in read_csv(self.root / "formal_tasks.csv")
        }
        for row in rows.tasks:
            key = (str(row["taskset_id"]), str(row["task_id"]))
            if key in existing_tasks:
                comparable = self._complete_row("formal_tasks.csv", row)
                if canonical_json(existing_tasks[key]) != canonical_json({
                    column: "" if comparable.get(column) is None else str(comparable.get(column))
                    for column in FORMAL_TABLES["formal_tasks.csv"]
                }):
                    raise RTA4FormalWriterError("formal task row conflict")
                continue
            self.append("formal_tasks.csv", row)

    def _append_identity_unique(
        self, table: str, identity_column: str, row: Mapping[str, Any],
    ) -> None:
        existing = {
            item[identity_column]: item for item in read_csv(self.root / table)
        }
        identity = str(row[identity_column])
        if identity in existing:
            comparable = self._complete_row(table, row)
            serialized = {
                column: "" if comparable.get(column) is None else str(comparable.get(column))
                for column in FORMAL_TABLES[table]
            }
            if existing[identity] != serialized:
                raise RTA4FormalWriterError(f"{table} identity conflict")
            return
        self.append(table, row)

    def _append_skeleton_unique(self, row: Mapping[str, Any]) -> None:
        """Keep one canonical certificate witness for a shared skeleton."""

        table = "formal_taskset_skeletons.csv"
        existing = {
            item["taskset_skeleton_id"]: item for item in read_csv(self.root / table)
        }
        identity = str(row["taskset_skeleton_id"])
        if identity not in existing:
            self.append(table, row)
            return
        comparable = self._complete_row(table, row)
        stable = set(FORMAL_TABLES[table]) - {"certificate_path", "certificate_sha256"}
        if any(existing[identity][key] != comparable[key] for key in stable):
            raise RTA4FormalWriterError("formal skeleton identity conflict")


def write_formal_file_hashes(root: Path | str) -> Path:
    root = Path(root)
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "formal_file_hashes.sha256":
            continue
        rows.append(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
            f"{path.relative_to(root).as_posix()}"
        )
    target = root / "formal_file_hashes.sha256"
    from .result_writer import atomic_write_text
    atomic_write_text(target, "\n".join(rows) + "\n")
    return target


__all__ = [
    "FORMAL_RUN_METADATA", "FORMAL_TERMINAL_DIRECTORY",
    "RTA4FormalResultWriter", "RTA4FormalWriterError",
    "write_formal_file_hashes",
]
