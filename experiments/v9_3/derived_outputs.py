"""Canonical, pure derived-output builders for validated CORE-1/CORE-2 runs.

The builders in this module consume only a validated persisted closure.  They
never write files, execute analyses, or mutate closure rows.  Aggregation and
deployment verification intentionally share these exact builders.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Protocol, Sequence

from .config import canonical_json
from .plotting_data import (
    CORE1_COMPARISON_RELATION, CORE1_PLOT_COLUMNS, CORE2_PLOT_COLUMNS,
    CORE2_RELATION_SPECS, CORE_PLOT_TYPES, PLOT_PRIMARY_KEYS,
    core1_plot_rows, core2_plot_rows, validate_canonical_plot_table,
)
from .tightness import by_taskset, compare_tasks


DERIVED_OUTPUT_SCHEMA_VERSION = 1
RUNTIME_FLOAT_POLICY = "STABLE_IDENTITY_SORTED_BINARY64_MATH_FSUM_V1"


class DerivedOutputError(RuntimeError):
    """Raised when a supposedly validated closure cannot be derived uniquely."""


class ClosureLike(Protocol):
    metadata: Mapping[str, Any]
    cells: Sequence[Mapping[str, str]]
    requests: Sequence[Mapping[str, str]]
    attempts: Sequence[Mapping[str, str]]
    tasksets: Sequence[Mapping[str, str]]
    tasks: Sequence[Mapping[str, str]]
    dependencies: Sequence[Mapping[str, str]]
    dominance: Sequence[Mapping[str, str]]
    failures: Sequence[Mapping[str, str]]


@dataclass(frozen=True)
class DerivedCsvTable:
    filename: str
    schema: str
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    primary_key: tuple[str, ...]
    row_type_column: str | None
    allowed_row_types: tuple[str, ...]
    field_types: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class DerivedOutputBundle:
    schema: str
    summary: Mapping[str, Any]
    tables: tuple[DerivedCsvTable, ...]

    @property
    def required_paths(self) -> tuple[str, ...]:
        return ("summary.json", *(table.filename for table in self.tables))

    def table(self, filename: str) -> DerivedCsvTable:
        matches = [table for table in self.tables if table.filename == filename]
        if len(matches) != 1:
            raise DerivedOutputError(f"derived table is not unique: {filename}")
        return matches[0]


VARIANT_COLUMNS = (
    "cell_id", "variant", "unconditional_denominator", "terminal_count",
    "completed_only_denominator", "certified_count", "no_candidate_count",
    "timeout_count", "not_applicable_count", "numeric_error_count",
    "internal_failure_count", "runtime_censored_count",
    "certification_ratio_unconditional", "certification_ratio_completed_only",
    "runtime_mean", "runtime_median", "runtime_p95", "runtime_max",
)
CORE2_VARIANT_COLUMNS = (
    *VARIANT_COLUMNS,
    "mean_candidate_task_count", "first_failed_priority_observation_count",
    "checked_w_total", "checked_h_total", "checked_q_total",
    "envelope_call_total",
)
CORE1_METHOD_COLUMNS = (
    "cell_id", "unconditional_requested_tasksets", "completed_only_pairs",
    "common_candidate_task_count", "loc_tighter_count", "equal_count",
    "violation_count", "mean_response_reduction", "median_response_reduction",
    "max_response_reduction", "mean_normalized_reduction",
    "certification_gain", "certification_loss", "both_certified",
    "neither_certified",
)
TASK_COMPARISON_COLUMNS = (
    "cell_id", "taskset_id", "exact_e0", "relation", "left_variant",
    "right_variant", "task_id", "priority_rank", "left_candidate",
    "right_candidate", "reduction", "normalized_reduction", "status",
    "dominance_expected",
)
TASKSET_TIGHTNESS_COLUMNS = (
    "cell_id", "taskset_id", "exact_e0", "relation", "left_variant",
    "right_variant", "dominance_expected", "common_candidate_task_count",
    "tighter_count", "equal_count", "violation_count", "mean_reduction",
    "median_reduction", "max_reduction", "mean_normalized_reduction",
)
CORE1_CERTIFICATION_COLUMNS = (
    "cell_id", "taskset_id", "exact_e0", "left_variant", "right_variant",
    "left_certified", "right_certified", "certification_gain",
    "certification_loss", "both_certified", "neither_certified",
    "left_status", "right_status",
)
CORE1_RUNTIME_COLUMNS = (
    "cell_id", "taskset_id", "exact_e0", "cw_runtime", "loc_runtime",
    "cw_status", "loc_status",
)
CORE2_TASKSET_COLUMNS = (
    *TASKSET_TIGHTNESS_COLUMNS,
    "left_status", "right_status", "left_certified", "right_certified",
    "certification_gain", "certification_loss",
)
DEPENDENCY_SUMMARY_COLUMNS = (
    "dependency_check_status", "count", "applicable_count",
    "source_certified_count", "fallback_count",
)
DOMINANCE_SUMMARY_COLUMNS = (
    "relation", "common_candidate_task_count", "tighter_count", "equal_count",
    "violation_count",
)
CORE1_PLOT_TYPES = CORE_PLOT_TYPES["CORE-1"]
CORE2_PLOT_TYPES = CORE_PLOT_TYPES["CORE-2"]


RECORD_SORT_KEYS = {
    "cell": ("cell_id",),
    "request": ("cell_id", "taskset_id", "variant", "analysis_id"),
    "attempt": ("analysis_id", "attempt_number", "attempt_id"),
    "taskset": (
        "cell_id", "taskset_id", "analysis_variant", "analysis_id",
    ),
    "task": (
        "cell_id", "taskset_id", "analysis_variant", "analysis_id", "task_id",
    ),
    "dependency": (
        "cell_id", "taskset_id", "target_variant", "analysis_id",
        "source_analysis_id", "dependency_check_status",
    ),
    "dominance": (
        "cell_id", "taskset_id", "relation", "task_id", "left_variant",
        "right_variant",
    ),
}


def stable_records(
    record_type: str, rows: Iterable[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Freeze row order by the identity key for one persisted record type."""

    try:
        keys = RECORD_SORT_KEYS[record_type]
    except KeyError as exc:
        raise DerivedOutputError(f"unknown record sort type: {record_type}") from exc

    def key(row: Mapping[str, Any]) -> tuple[str, ...]:
        identity = tuple(canonical_json(row.get(name)) for name in keys)
        return (*identity, canonical_json(dict(row)))

    return sorted(rows, key=key)


def _float(row: Mapping[str, Any], key: str) -> float:
    try:
        raw = row[key]
    except KeyError as exc:
        raise DerivedOutputError(f"missing numeric field: {key}") from exc
    if type(raw) is bool:
        raise DerivedOutputError(f"{key} must not be bool")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise DerivedOutputError(f"{key} is not binary64 numeric text") from exc
    if not math.isfinite(value):
        raise DerivedOutputError(f"{key} must be finite")
    return value


def _runtime(row: Mapping[str, Any]) -> float:
    value = _float(row, "runtime_wall_seconds")
    if value < 0.0 or (value == 0.0 and math.copysign(1.0, value) < 0.0):
        raise DerivedOutputError("runtime_wall_seconds must be nonnegative")
    return value


def _quantile(values: Sequence[float], ratio: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = math.ceil(ratio * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _median(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return (
        ordered[middle]
        if len(ordered) % 2
        else math.fsum((ordered[middle - 1], ordered[middle])) / 2
    )


def variant_summary(
    requests: Iterable[Mapping[str, str]],
    results: Iterable[Mapping[str, str]],
) -> list[Dict[str, Any]]:
    results = list(results)
    for row in results:
        _runtime(row)
    requests = stable_records("request", requests)
    results = stable_records("taskset", results)
    requested = Counter((row["cell_id"], row["variant"]) for row in requests)
    groups: Dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in results:
        groups[(row["cell_id"], row["analysis_variant"])].append(row)
    output = []
    for key in sorted(requested):
        members = groups.get(key, [])
        count = requested[key]
        statuses = Counter(row["solver_status"] for row in members)
        certified = sum(row["taskset_proven"] == "True" for row in members)
        excluded = {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"}
        completed_members = [
            row for row in members if row["solver_status"] not in excluded
        ]
        # Validate every runtime at the builder boundary, including censored
        # results, then aggregate the completed subset in stable identity order.
        for row in members:
            _runtime(row)
        runtime = [_runtime(row) for row in completed_members]
        output.append({
            "cell_id": key[0],
            "variant": key[1],
            "unconditional_denominator": count,
            "terminal_count": len(members),
            "completed_only_denominator": len(completed_members),
            "certified_count": certified,
            "no_candidate_count": statuses["NO_CANDIDATE"],
            "timeout_count": statuses["TIMEOUT"],
            "not_applicable_count": statuses["NOT_APPLICABLE_DEPENDENCY"],
            "numeric_error_count": statuses["NUMERIC_ERROR"],
            "internal_failure_count": statuses["INTERNAL_CONFORMANCE_FAILURE"],
            "runtime_censored_count": statuses["TIMEOUT"],
            "certification_ratio_unconditional": (
                certified / count if count else None
            ),
            "certification_ratio_completed_only": (
                certified / len(completed_members) if completed_members else None
            ),
            "runtime_mean": (
                math.fsum(runtime) / len(runtime) if runtime else None
            ),
            "runtime_median": _median(runtime),
            "runtime_p95": _quantile(runtime, .95),
            "runtime_max": max(runtime, default=None),
        })
    return output


def _unique_index(
    rows: Iterable[Mapping[str, Any]], *keys: str, label: str
) -> Dict[tuple[Any, ...], Mapping[str, Any]]:
    result: Dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in rows:
        key = tuple(row[name] for name in keys)
        if key in result:
            raise DerivedOutputError(f"duplicate {label}: {key}")
        result[key] = row
    return result


def _certification_pairs(
    results: Iterable[Mapping[str, str]], left: str, right: str
) -> list[Dict[str, Any]]:
    rows = list(results)
    index = _unique_index(
        rows, "cell_id", "taskset_id", "analysis_variant",
        label="certification result",
    )
    bases = sorted(
        (cell, taskset_id)
        for cell, taskset_id, variant in index
        if variant == left
    )
    output = []
    for cell, taskset_id in bases:
        lrow = index.get((cell, taskset_id, left))
        rrow = index.get((cell, taskset_id, right))
        if lrow is None or rrow is None:
            raise DerivedOutputError(
                f"missing certification pair for {(cell, taskset_id)}"
            )
        left_certified = lrow["taskset_proven"] == "True"
        right_certified = rrow["taskset_proven"] == "True"
        output.append({
            "cell_id": cell,
            "taskset_id": taskset_id,
            "exact_e0": lrow["exact_e0"],
            "left_variant": left,
            "right_variant": right,
            "left_certified": left_certified,
            "right_certified": right_certified,
            "certification_gain": int(right_certified and not left_certified),
            "certification_loss": int(left_certified and not right_certified),
            "both_certified": int(left_certified and right_certified),
            "neither_certified": int(not left_certified and not right_certified),
            "left_status": lrow["solver_status"],
            "right_status": rrow["solver_status"],
        })
    return output


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is float:
        return "float"
    if type(value) is str:
        return "str"
    raise DerivedOutputError(f"unsupported derived field type: {type(value).__name__}")


def _canonical_sort_key(row: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(canonical_json(row[key]) for key in keys)


def _table(
    filename: str,
    columns: tuple[str, ...],
    rows: Iterable[Mapping[str, Any]],
    primary_key: tuple[str, ...],
    *,
    row_type_column: str | None = None,
    allowed_row_types: tuple[str, ...] = (),
) -> DerivedCsvTable:
    frozen_rows = [dict(row) for row in rows]
    expected_columns = set(columns)
    for row in frozen_rows:
        if set(row) != expected_columns:
            raise DerivedOutputError(
                f"{filename} row schema mismatch: "
                f"expected={columns}, actual={tuple(row)}"
            )
        if row_type_column is not None and row[row_type_column] not in allowed_row_types:
            raise DerivedOutputError(
                f"{filename} contains unknown {row_type_column}: "
                f"{row[row_type_column]}"
            )
    frozen_rows.sort(key=lambda row: _canonical_sort_key(row, primary_key))
    seen = set()
    for row in frozen_rows:
        key = tuple(canonical_json(row[name]) for name in primary_key)
        if key in seen:
            raise DerivedOutputError(f"duplicate canonical key in {filename}: {key}")
        seen.add(key)
    field_types = tuple(
        (
            column,
            tuple(sorted({_type_name(row[column]) for row in frozen_rows})),
        )
        for column in columns
    )
    return DerivedCsvTable(
        filename=filename,
        schema=f"ASAP_BLOCK_V9_3_DERIVED_CSV_{filename.upper().replace('.', '_')}_V1",
        columns=columns,
        rows=tuple(frozen_rows),
        primary_key=primary_key,
        row_type_column=row_type_column,
        allowed_row_types=allowed_row_types,
        field_types=field_types,
    )


def csv_projection(rows: Iterable[Mapping[str, Any]]) -> list[Dict[str, str]]:
    return [
        {key: "" if value is None else str(value) for key, value in row.items()}
        for row in rows
    ]


def strict_derived_json(path: Path) -> Any:
    """Read JSON while rejecting duplicate keys and non-finite constants."""

    path = Path(path)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise DerivedOutputError(f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> Any:
        raise DerivedOutputError(f"non-finite JSON constant in {path}: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except DerivedOutputError:
        raise
    except Exception as exc:
        raise DerivedOutputError(f"invalid JSON derived output: {path}") from exc


def read_exact_derived_csv(
    path: Path, table: DerivedCsvTable
) -> list[Dict[str, str]]:
    path = Path(path)
    if table.filename in {"core1_plot_data.csv", "core2_plot_data.csv"}:
        expected_core = "CORE-1" if table.filename.startswith("core1_") else "CORE-2"
        return validate_canonical_plot_table(path, expected_core=expected_core)
    if not path.is_file():
        raise DerivedOutputError(f"missing derived CSV: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        physical = list(csv.reader(handle))
    if not physical:
        raise DerivedOutputError(f"derived CSV has no header: {path}")
    header = physical[0]
    if header != list(table.columns):
        raise DerivedOutputError(f"derived CSV header mismatch: {path}")
    actual: list[Dict[str, str]] = []
    for number, values in enumerate(physical[1:], start=2):
        if len(values) != len(header):
            raise DerivedOutputError(
                f"derived CSV row width mismatch: {path}:{number}"
            )
        row = dict(zip(header, values))
        if (
            table.row_type_column is not None
            and row[table.row_type_column] not in table.allowed_row_types
        ):
            raise DerivedOutputError(
                f"derived CSV unknown row type: {path}:{number}:"
                f"{row[table.row_type_column]}"
            )
        actual.append(row)
    seen = set()
    for row in actual:
        key = tuple(row[column] for column in table.primary_key)
        if key in seen:
            raise DerivedOutputError(f"duplicate derived CSV primary key: {path}: {key}")
        seen.add(key)
    return actual


def validate_persisted_derived_bundle(
    root: Path, expected_core: str, bundle: DerivedOutputBundle
) -> None:
    """Execute the exact current derived schema and raw-closure equality gate."""

    root = Path(root)
    missing = [path for path in bundle.required_paths if not (root / path).is_file()]
    if missing:
        raise DerivedOutputError(
            f"{expected_core}: missing derived outputs: {missing}"
        )
    actual_summary = strict_derived_json(root / "summary.json")
    if canonical_json(actual_summary) != canonical_json(bundle.summary):
        raise DerivedOutputError(f"{expected_core}: summary/raw closure mismatch")
    for table in bundle.tables:
        actual = read_exact_derived_csv(root / table.filename, table)
        expected = csv_projection(table.rows)
        if actual != expected:
            raise DerivedOutputError(
                f"{expected_core}: derived CSV/raw closure mismatch: "
                f"{table.filename}"
            )


def _common_summary(
    closure: ClosureLike, core: str, variants: list[Dict[str, Any]]
) -> Dict[str, Any]:
    requested_count = len(closure.requests)
    terminal_count = len(closure.tasksets)
    completed_count = sum(row["completed_only_denominator"] for row in variants)
    certified_count = sum(row["certified_count"] for row in variants)
    timeout_count = sum(row["timeout_count"] for row in variants)
    ordered_tasksets = stable_records("taskset", closure.tasksets)
    for row in ordered_tasksets:
        _runtime(row)
    finite_runtime = [
        _runtime(row)
        for row in ordered_tasksets
        if row["solver_status"]
        not in {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"}
    ]
    return {
        "schema": f"ASAP_BLOCK_V9_3_{core.replace('-', '')}_DERIVED_SUMMARY_V1",
        "schema_version": DERIVED_OUTPUT_SCHEMA_VERSION,
        "runtime_float_policy": RUNTIME_FLOAT_POLICY,
        "core": core,
        "config_semantic_hash": closure.metadata["config_hash"],
        "formal_large_scale_run": bool(
            closure.metadata.get("formal_large_scale_run", False)
        ),
        "formal_authorization_id": closure.metadata.get("formal_authorization_id"),
        "requested_count": requested_count,
        "terminal_count": terminal_count,
        "completed_count": completed_count,
        "certified_count": certified_count,
        "timeout_count": timeout_count,
        "certification_ratio_unconditional": (
            certified_count / requested_count if requested_count else None
        ),
        "certification_ratio_completed_only": (
            certified_count / completed_count if completed_count else None
        ),
        "runtime_summary": {
            "sample_count": len(finite_runtime),
            "censored_count": timeout_count,
            "mean": (
                math.fsum(finite_runtime) / len(finite_runtime)
                if finite_runtime else None
            ),
            "median": _median(finite_runtime),
            "p95": _quantile(finite_runtime, .95),
            "max": max(finite_runtime, default=None),
        },
        "variants": variants,
    }


def build_core1_summary(
    closure: ClosureLike,
    variants: list[Dict[str, Any]],
    comparisons: list[Mapping[str, Any]],
    taskset_comparisons: list[Mapping[str, Any]],
    certification: list[Mapping[str, Any]],
    runtime: list[Mapping[str, Any]],
    comparison_by_cell: list[Mapping[str, Any]],
) -> Dict[str, Any]:
    summary = _common_summary(closure, "CORE-1", variants)
    summary.update({
        "common_candidate_tasks": len(comparisons),
        "dominance_violations": sum(
            row["status"] == "VIOLATION" for row in comparisons
        ),
        "tightness_outcomes": {
            "task_count": len(comparisons),
            "taskset_count": len(taskset_comparisons),
            "tighter_count": sum(row["status"] == "TIGHTER" for row in comparisons),
            "equal_count": sum(row["status"] == "EQUAL" for row in comparisons),
            "violation_count": sum(
                row["status"] == "VIOLATION" for row in comparisons
            ),
        },
        "certification_outcomes": {
            "pair_count": len(certification),
            "gain_count": sum(row["certification_gain"] for row in certification),
            "loss_count": sum(row["certification_loss"] for row in certification),
            "both_certified_count": sum(
                row["both_certified"] for row in certification
            ),
            "neither_certified_count": sum(
                row["neither_certified"] for row in certification
            ),
        },
        "runtime_comparison_count": len(runtime),
        "comparison_by_cell": comparison_by_cell,
    })
    return summary


def build_core1_derived_outputs(closure: ClosureLike) -> DerivedOutputBundle:
    tasksets = list(closure.tasksets)
    for row in tasksets:
        _runtime(row)
    tasksets = stable_records("taskset", tasksets)
    tasks = stable_records("task", closure.tasks)
    requests = stable_records("request", closure.requests)
    variants = variant_summary(requests, tasksets)
    comparisons = compare_tasks(
        tasks, "CW_THETA_CW", "LOC_THETA_LOC",
        CORE1_COMPARISON_RELATION, assume_dominance=True,
    )
    taskset_comparisons = by_taskset(comparisons)
    certification = _certification_pairs(
        tasksets, "CW_THETA_CW", "LOC_THETA_LOC"
    )
    result_index = _unique_index(
        tasksets, "cell_id", "taskset_id", "analysis_variant",
        label="runtime result",
    )
    runtime = []
    for pair in certification:
        key = (pair["cell_id"], pair["taskset_id"])
        left = result_index[key + ("CW_THETA_CW",)]
        right = result_index[key + ("LOC_THETA_LOC",)]
        runtime.append({
            "cell_id": key[0],
            "taskset_id": key[1],
            "exact_e0": pair["exact_e0"],
            "cw_runtime": left["runtime_wall_seconds"],
            "loc_runtime": right["runtime_wall_seconds"],
            "cw_status": left["solver_status"],
            "loc_status": right["solver_status"],
        })
    comparison_by_cell = []
    for cell in sorted({row["cell_id"] for row in requests}):
        tight = [row for row in comparisons if row["cell_id"] == cell]
        cert = [row for row in certification if row["cell_id"] == cell]
        reductions = [row["reduction"] for row in tight]
        comparison_by_cell.append({
            "cell_id": cell,
            "unconditional_requested_tasksets": sum(
                row["cell_id"] == cell and row["variant"] == "CW_THETA_CW"
                for row in requests
            ),
            "completed_only_pairs": sum(
                row["left_status"]
                not in {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"}
                and row["right_status"]
                not in {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"}
                for row in cert
            ),
            "common_candidate_task_count": len(tight),
            "loc_tighter_count": sum(row["status"] == "TIGHTER" for row in tight),
            "equal_count": sum(row["status"] == "EQUAL" for row in tight),
            "violation_count": sum(row["status"] == "VIOLATION" for row in tight),
            "mean_response_reduction": (
                math.fsum(reductions) / len(reductions) if reductions else None
            ),
            "median_response_reduction": _median(reductions),
            "max_response_reduction": max(reductions, default=None),
            "mean_normalized_reduction": (
                math.fsum(row["normalized_reduction"] for row in tight) / len(tight)
                if tight else None
            ),
            "certification_gain": sum(row["certification_gain"] for row in cert),
            "certification_loss": sum(row["certification_loss"] for row in cert),
            "both_certified": sum(row["both_certified"] for row in cert),
            "neither_certified": sum(row["neither_certified"] for row in cert),
        })
    plots = core1_plot_rows(tasksets, comparisons, certification)
    tables = (
        _table("summary.csv", VARIANT_COLUMNS, variants, ("cell_id", "variant")),
        _table(
            "core1_method_comparison.csv", CORE1_METHOD_COLUMNS,
            comparison_by_cell, ("cell_id",),
        ),
        _table(
            "core1_tightness_by_task.csv", TASK_COMPARISON_COLUMNS, comparisons,
            ("cell_id", "taskset_id", "relation", "task_id"),
        ),
        _table(
            "core1_tightness_by_taskset.csv", TASKSET_TIGHTNESS_COLUMNS,
            taskset_comparisons, ("cell_id", "taskset_id", "relation"),
        ),
        _table(
            "core1_certification_comparison.csv", CORE1_CERTIFICATION_COLUMNS,
            certification,
            ("cell_id", "taskset_id", "left_variant", "right_variant"),
        ),
        _table(
            "core1_runtime_comparison.csv", CORE1_RUNTIME_COLUMNS, runtime,
            ("cell_id", "taskset_id"),
        ),
        _table(
            "core1_plot_data.csv", CORE1_PLOT_COLUMNS, plots,
            PLOT_PRIMARY_KEYS["CORE-1"],
            row_type_column="plot", allowed_row_types=CORE1_PLOT_TYPES,
        ),
    )
    summary = build_core1_summary(
        closure, variants, comparisons, taskset_comparisons, certification,
        runtime, comparison_by_cell,
    )
    return DerivedOutputBundle(
        schema="ASAP_BLOCK_V9_3_CORE1_DERIVED_OUTPUT_BUNDLE_V1",
        summary=summary,
        tables=tables,
    )


CORE2_RELATIONS = CORE2_RELATION_SPECS


def build_core2_summary(
    closure: ClosureLike,
    variants: list[Dict[str, Any]],
    task_rows: list[Mapping[str, Any]],
    taskset_rows: list[Mapping[str, Any]],
    dependency_summary: list[Mapping[str, Any]],
    dominance_summary: list[Mapping[str, Any]],
) -> Dict[str, Any]:
    summary = _common_summary(closure, "CORE-2", variants)
    summary.update({
        "candidate_task_count_total": sum(
            int(row["n_tasks_candidate_found"]) for row in closure.tasksets
        ),
        "checked_w_total": sum(int(row["checked_w_count"] or 0) for row in closure.tasks),
        "checked_h_total": sum(int(row["checked_h_count"] or 0) for row in closure.tasks),
        "checked_q_total": sum(int(row["checked_q_count"] or 0) for row in closure.tasks),
        "envelope_call_total": sum(
            int(row["envelope_call_count"] or 0) for row in closure.tasks
        ),
        "dependency_records": len(closure.dependencies),
        "dependency_valid_count": sum(
            row["dependency_check_status"] == "VALID"
            for row in closure.dependencies
        ),
        "dependency_invalid_count": sum(
            row["dependency_check_status"] == "INVALID"
            for row in closure.dependencies
        ),
        "dependency_applicable_count": sum(
            row["applicable"] == "True" for row in closure.dependencies
        ),
        "dependency_fallback_count": sum(
            row["fallback_used"] == "True" for row in closure.dependencies
        ),
        "dominance_violations": sum(
            row["status"] == "VIOLATION" for row in closure.dominance
        ),
        "ablation_task_count": len(task_rows),
        "ablation_taskset_count": len(taskset_rows),
        "dependency_summary": dependency_summary,
        "dominance_summary": dominance_summary,
    })
    return summary


def build_core2_derived_outputs(closure: ClosureLike) -> DerivedOutputBundle:
    tasksets = list(closure.tasksets)
    for row in tasksets:
        _runtime(row)
    tasksets = stable_records("taskset", tasksets)
    tasks = stable_records("task", closure.tasks)
    requests = stable_records("request", closure.requests)
    dependencies = stable_records("dependency", closure.dependencies)
    variants = variant_summary(requests, tasksets)
    task_groups: Dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    result_groups: Dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in tasks:
        task_groups[(row["cell_id"], row["analysis_variant"])].append(row)
    for row in tasksets:
        result_groups[(row["cell_id"], row["analysis_variant"])].append(row)
    for row in variants:
        key = (row["cell_id"], row["variant"])
        task_members = task_groups.get(key, [])
        result_members = result_groups.get(key, [])
        row.update({
            "mean_candidate_task_count": (
                sum(int(item["n_tasks_candidate_found"]) for item in result_members)
                / len(result_members) if result_members else None
            ),
            "first_failed_priority_observation_count": sum(
                bool(item["first_failed_priority"]) for item in result_members
            ),
            "checked_w_total": sum(
                int(item["checked_w_count"] or 0) for item in task_members
            ),
            "checked_h_total": sum(
                int(item["checked_h_count"] or 0) for item in task_members
            ),
            "checked_q_total": sum(
                int(item["checked_q_count"] or 0) for item in task_members
            ),
            "envelope_call_total": sum(
                int(item["envelope_call_count"] or 0) for item in task_members
            ),
        })
    task_rows = []
    for relation, left, right, dominance in CORE2_RELATIONS:
        task_rows.extend(
            compare_tasks(
                tasks, left, right, relation, assume_dominance=dominance
            )
        )
    tightness_by_set = {
        (row["cell_id"], row["taskset_id"], row["relation"]): row
        for row in by_taskset(task_rows)
    }
    taskset_rows = []
    for relation, left, right, dominance in CORE2_RELATIONS:
        for pair in _certification_pairs(tasksets, left, right):
            tight = tightness_by_set.get(
                (pair["cell_id"], pair["taskset_id"], relation), {}
            )
            taskset_rows.append({
                "cell_id": pair["cell_id"],
                "taskset_id": pair["taskset_id"],
                "exact_e0": pair["exact_e0"],
                "relation": relation,
                "left_variant": left,
                "right_variant": right,
                "dominance_expected": dominance,
                "common_candidate_task_count": tight.get(
                    "common_candidate_task_count", 0
                ),
                "tighter_count": tight.get("tighter_count", 0),
                "equal_count": tight.get("equal_count", 0),
                "violation_count": tight.get("violation_count", 0),
                "mean_reduction": tight.get("mean_reduction"),
                "median_reduction": tight.get("median_reduction"),
                "max_reduction": tight.get("max_reduction"),
                "mean_normalized_reduction": tight.get(
                    "mean_normalized_reduction"
                ),
                "left_status": pair["left_status"],
                "right_status": pair["right_status"],
                "left_certified": pair["left_certified"],
                "right_certified": pair["right_certified"],
                "certification_gain": pair["certification_gain"],
                "certification_loss": pair["certification_loss"],
            })
    dominance_rows = [row for row in task_rows if row["dominance_expected"]]
    dominance_summary = []
    for relation in sorted({row["relation"] for row in dominance_rows}):
        members = [row for row in dominance_rows if row["relation"] == relation]
        dominance_summary.append({
            "relation": relation,
            "common_candidate_task_count": len(members),
            "tighter_count": sum(row["status"] == "TIGHTER" for row in members),
            "equal_count": sum(row["status"] == "EQUAL" for row in members),
            "violation_count": sum(row["status"] == "VIOLATION" for row in members),
        })
    dependency_summary = []
    for status in sorted({row["dependency_check_status"] for row in dependencies}):
        members = [
            row for row in dependencies
            if row["dependency_check_status"] == status
        ]
        dependency_summary.append({
            "dependency_check_status": status,
            "count": len(members),
            "applicable_count": sum(row["applicable"] == "True" for row in members),
            "source_certified_count": sum(
                row["source_certified"] == "True" for row in members
            ),
            "fallback_count": sum(
                row["fallback_used"] == "True" for row in members
            ),
        })
    plots = core2_plot_rows(
        tasksets, tasks, task_rows, taskset_rows, dependencies
    )
    tables = (
        _table("summary.csv", CORE2_VARIANT_COLUMNS, variants, ("cell_id", "variant")),
        _table(
            "core2_variant_summary.csv", CORE2_VARIANT_COLUMNS, variants,
            ("cell_id", "variant"),
        ),
        _table(
            "core2_ablation_by_task.csv", TASK_COMPARISON_COLUMNS, task_rows,
            ("cell_id", "taskset_id", "relation", "task_id"),
        ),
        _table(
            "core2_ablation_by_taskset.csv", CORE2_TASKSET_COLUMNS, taskset_rows,
            ("cell_id", "taskset_id", "relation"),
        ),
        _table(
            "core2_dependency_summary.csv", DEPENDENCY_SUMMARY_COLUMNS,
            dependency_summary, ("dependency_check_status",),
        ),
        _table(
            "core2_dominance_summary.csv", DOMINANCE_SUMMARY_COLUMNS,
            dominance_summary, ("relation",),
        ),
        _table(
            "core2_plot_data.csv", CORE2_PLOT_COLUMNS, plots,
            PLOT_PRIMARY_KEYS["CORE-2"],
            row_type_column="plot", allowed_row_types=CORE2_PLOT_TYPES,
        ),
    )
    summary = build_core2_summary(
        closure, variants, task_rows, taskset_rows, dependency_summary,
        dominance_summary,
    )
    return DerivedOutputBundle(
        schema="ASAP_BLOCK_V9_3_CORE2_DERIVED_OUTPUT_BUNDLE_V1",
        summary=summary,
        tables=tables,
    )
