"""Crash-resilient attempt journal and atomic terminal-result materialization."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import canonical_json


CELL_COLUMNS = (
    "cell_id", "experiment_id", "core", "M", "task_n", "utilization",
    "utilization_index",
    "exact_e0", "deadline_mode", "deadline_profile", "power_mode",
    "priority_policy", "service_curve_id", "numerical_mode", "generation_id",
)
GENERATED_COLUMNS = (
    "generation_id", "taskset_id", "taskset_index", "generation_seed", "M",
    "task_n", "target_total_utilization", "actual_total_utilization",
    "utilization_error_total", "deadline_mode", "d_over_t_min_actual",
    "d_over_t_max_actual", "d_over_t_values_json", "taskset_hash",
    "priority_hash", "power_hash", "service_curve_reference",
    "numeric_contract_sha256",
    "generation_seconds", "canonical_taskset_json", "task_input_json",
)
REQUEST_COLUMNS = (
    "request_id", "analysis_id", "cell_id", "taskset_id", "taskset_hash",
    "exact_e0", "variant", "numerical_mode", "theory_document_sha256",
    "numeric_contract_sha256", "exact_input_identity", "timeout_seconds",
    "retry_timeout_seconds", "source_analysis_id", "request_status",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "analysis_id", "attempt_number", "parent_attempt_id",
    "timeout_budget_seconds", "solver_status", "failure_origin", "outer_timeout",
    "solver_wall_seconds", "solver_cpu_seconds", "worker_startup_seconds",
    "serialization_seconds", "ipc_seconds", "payload_received",
    "worker_cleanup_status", "worker_exitcode", "worker_cleanup_seconds",
    "total_wall_seconds", "exception_type", "exception_message",
    "traceback", "started_at_utc",
)
TASKSET_RESULT_COLUMNS = (
    "analysis_id", "request_id", "cell_id", "taskset_id", "taskset_hash",
    "generation_seed", "M", "task_n", "utilization", "exact_e0",
    "deadline_mode", "analysis_variant", "method_role", "solver_status",
    "theory_document_path", "theory_document_sha256",
    "numeric_contract_name", "numeric_contract_version",
    "numeric_contract_sha256", "source_numeric_model",
    "demand_rounding_mode", "supply_rounding_mode", "e0_rounding_mode",
    "exact_input_identity", "float_decision_path",
    "failure_origin", "certification_status", "taskset_proven",
    "first_failed_priority",
    "n_tasks_total", "n_tasks_evaluated", "n_tasks_candidate_found",
    "n_tasks_certified", "source_analysis_id", "source_vector_hash",
    "target_carry_in_vector_hash", "dependency_check_status",
    "fixed_carry_in_interface_status", "dominance_invariant_status",
    "dominance_violation_count", "diagnostic_mode", "final_attempt_id",
    "attempt_count", "timeout_budget_seconds", "runtime_wall_seconds",
    "runtime_cpu_seconds", "worker_startup_seconds", "serialization_seconds",
    "ipc_seconds", "outer_timeout", "terminal_origin",
)
TASK_RESULT_COLUMNS = (
    "analysis_id", "cell_id", "taskset_id", "exact_e0", "analysis_variant",
    "task_id", "priority_rank", "C", "D", "T", "P", "D_over_T",
    "task_solver_status", "task_certification_status",
    "candidate_response_time", "closing_w", "witness_h", "checked_w_count",
    "checked_h_count", "checked_q_count", "envelope_call_count",
    "failure_reason", "carry_in_vector_hash",
)
DEPENDENCY_COLUMNS = (
    "analysis_id", "cell_id", "taskset_id", "target_variant",
    "source_analysis_id", "source_variant", "source_certified",
    "dependency_check_status", "source_taskset_hash", "target_taskset_hash",
    "source_e0", "target_e0", "source_numerical_mode",
    "target_numerical_mode", "source_vector_hash", "target_vector_hash",
    "applicable", "fallback_used",
)
DOMINANCE_COLUMNS = (
    "cell_id", "taskset_id", "exact_e0", "relation", "left_variant",
    "right_variant", "task_id", "priority_rank", "left_candidate",
    "right_candidate", "reduction", "status",
)
FAILURE_COLUMNS = (
    "severity", "stage", "analysis_id", "cell_id", "taskset_id", "variant",
    "code", "detail", "traceback", "failure_input",
)


TABLES = {
    "cells.csv": CELL_COLUMNS,
    "generated_tasksets.csv": GENERATED_COLUMNS,
    "analysis_requests.csv": REQUEST_COLUMNS,
    "analysis_attempts.csv": ATTEMPT_COLUMNS,
    "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
    "per_task_results.csv": TASK_RESULT_COLUMNS,
    "dependency_records.csv": DEPENDENCY_COLUMNS,
    "dominance_checks.csv": DOMINANCE_COLUMNS,
    "failures.csv": FAILURE_COLUMNS,
}


class ResultWriterError(RuntimeError):
    pass


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            extra = set(row) - set(columns)
            if extra:
                raise ResultWriterError(f"unexpected columns for {path.name}: {sorted(extra)}")
            writer.writerow({column: row.get(column) for column in columns})
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_csv_row(path: Path, columns: Sequence[str], row: Mapping[str, Any]) -> None:
    extra = set(row) - set(columns)
    if extra:
        raise ResultWriterError(f"unexpected append columns: {sorted(extra)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow({column: row.get(column) for column in columns})
        handle.flush()
        os.fsync(handle.fileno())


def read_csv(path: Path) -> list[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_csv_header(
    path: Path,
    columns: Sequence[str],
) -> None:
    """Reject an existing table whose physical schema is not exact."""

    if not path.is_file():
        return

    with path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.reader(handle)

        try:
            actual = next(reader)
        except StopIteration as exc:
            raise ResultWriterError(
                f"existing table has no header: "
                f"{path.name}"
            ) from exc

    expected = list(columns)

    if actual != expected:
        raise ResultWriterError(
            f"existing table header mismatch for "
            f"{path.name}: expected {expected}, "
            f"got {actual}"
        )


class ResultWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.finals = self.root / "terminal_results"
        self.states = self.root / "result_state"
        self.fail_inputs = self.root / "failure_inputs"
        # Validate every pre-existing table before creating even one directory
        # or missing table.  A legacy exact-header mismatch must leave the old
        # output root byte-for-byte untouched.
        for name, columns in TABLES.items():
            validate_csv_header(self.root / name, columns)
        for path in (self.root, self.finals, self.states, self.fail_inputs):
            path.mkdir(parents=True, exist_ok=True)
        for name, columns in TABLES.items():
            path = self.root / name
            if not path.exists():
                write_csv(path, columns, [])

    def append_attempt(self, row: Mapping[str, Any]) -> None:
        attempt_id = str(row.get("attempt_id", ""))
        if not attempt_id:
            raise ResultWriterError("attempt_id must be non-empty")
        existing = read_csv(self.root / "analysis_attempts.csv")
        if any(item.get("attempt_id") == attempt_id for item in existing):
            raise ResultWriterError(f"duplicate attempt_id: {attempt_id}")
        append_csv_row(self.root / "analysis_attempts.csv", ATTEMPT_COLUMNS, row)

    def write_terminal(self, analysis_id: str, payload: Mapping[str, Any]) -> None:
        path = self.finals / f"{analysis_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if canonical_json(existing) != canonical_json(payload):
                raise ResultWriterError(f"terminal result conflict for {analysis_id}")
            return
        atomic_write_json(path, payload)

    def terminal(self, analysis_id: str) -> Dict[str, Any] | None:
        path = self.finals / f"{analysis_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def terminal_payloads(self) -> list[Dict[str, Any]]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.finals.glob("*.json"))
        ]

    def materialize(
        self,
        cells: Iterable[Mapping[str, Any]],
        generated: Iterable[Mapping[str, Any]],
        requests: Iterable[Mapping[str, Any]],
        dependencies: Iterable[Mapping[str, Any]],
        dominance: Iterable[Mapping[str, Any]],
        failures: Iterable[Mapping[str, Any]],
    ) -> None:
        write_csv(self.root / "cells.csv", CELL_COLUMNS, cells)
        write_csv(self.root / "generated_tasksets.csv", GENERATED_COLUMNS, generated)
        write_csv(self.root / "analysis_requests.csv", REQUEST_COLUMNS, requests)
        payloads = self.terminal_payloads()
        write_csv(
            self.root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS,
            [payload["taskset_row"] for payload in payloads],
        )
        write_csv(
            self.root / "per_task_results.csv", TASK_RESULT_COLUMNS,
            [row for payload in payloads for row in payload.get("task_rows", [])],
        )
        write_csv(self.root / "dependency_records.csv", DEPENDENCY_COLUMNS, dependencies)
        write_csv(self.root / "dominance_checks.csv", DOMINANCE_COLUMNS, dominance)
        write_csv(self.root / "failures.csv", FAILURE_COLUMNS, failures)


def write_file_hashes(root: Path) -> None:
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "file_hashes.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(root).as_posix()}")
    atomic_write_text(root / "file_hashes.sha256", "\n".join(rows) + "\n")
