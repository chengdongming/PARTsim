"""Long-form plot-data exports constructed only from persisted result CSVs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from fractions import Fraction
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

CORE1_PLOT_COLUMNS = (
    "plot_schema", "plot_schema_version", "plot", "cell_id", "taskset_id",
    "utilization", "exact_e0", "variant",
    "task_id", "x", "y", "outcome",
)
CORE2_PLOT_COLUMNS = (
    "plot_schema", "plot_schema_version", "plot", "cell_id", "taskset_id",
    "variant", "relation", "task_id",
    "x", "y", "outcome",
)
PLOT_SCHEMA = "ASAP_BLOCK_V9_3_CANONICAL_PLOT_ROWS_V3"
PLOT_SCHEMA_VERSION = 3

CORE1_VARIANTS = ("CW_THETA_CW", "LOC_THETA_LOC")
CORE2_VARIANTS = (
    "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC",
)
ANALYSIS_SOLVER_STATUSES = (
    "COMPLETED", "NO_CANDIDATE", "TIMEOUT", "NUMERIC_ERROR",
    "NOT_APPLICABLE_DEPENDENCY", "INTERNAL_CONFORMANCE_FAILURE",
    "UNSUPPORTED_EXPERIMENT_VARIANT",
)
COMPARISON_OUTCOMES = ("TIGHTER", "EQUAL", "VIOLATION")
CORE1_COMPARISON_RELATION = "MAIN_METHOD_LOCAL_VS_COMPLETE"
CORE2_RELATION_SPECS = (
    ("LOC_D_VS_CW_D", "CW_D", "LOC_D", True),
    (
        "LOC_THETA_CW_VS_CW_THETA_CW", "CW_THETA_CW",
        "LOC_THETA_CW", True,
    ),
    (
        "LOC_THETA_LOC_VS_CW_THETA_CW", "CW_THETA_CW",
        "LOC_THETA_LOC", True,
    ),
    ("CW_THETA_CW_VS_CW_D", "CW_D", "CW_THETA_CW", False),
    ("LOC_THETA_LOC_VS_LOC_D", "LOC_D", "LOC_THETA_LOC", False),
)
CORE2_RELATION_RIGHT_VARIANT = {
    relation: right for relation, _left, right, _dominance
    in CORE2_RELATION_SPECS
}


@dataclass(frozen=True)
class PlotRowContract:
    """Immutable machine-consumable semantics for one canonical plot row."""

    schema: str
    schema_version: int
    exact_header: tuple[str, ...]
    plot_type: str
    core: str
    level: str
    relation_rule: str
    x_domain: str
    y_domain: str
    outcomes: tuple[str, ...]
    primary_key: tuple[str, ...]
    sort_key: tuple[str, ...]
    task_id_rule: str
    relations: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    relation_variants: tuple[tuple[str, str], ...] = ()
    x_required: bool = True
    y_required: bool = True
    association: str = "none"
    source_relation: str | None = None
    y_source_field: str | None = None
    outcome_y_rules: tuple[tuple[str, str], ...] = ()


def _contract(
    plot_type: str,
    core: str,
    level: str,
    x_domain: str,
    y_domain: str,
    outcomes: tuple[str, ...],
    **kwargs: Any,
) -> PlotRowContract:
    primary_key = (
        ("plot", "cell_id", "taskset_id", "variant", "task_id")
        if core == "CORE-1" else
        ("plot", "cell_id", "taskset_id", "variant", "relation", "task_id")
    )
    return PlotRowContract(
        schema=PLOT_SCHEMA, schema_version=PLOT_SCHEMA_VERSION,
        exact_header=(CORE1_PLOT_COLUMNS if core == "CORE-1" else CORE2_PLOT_COLUMNS),
        plot_type=plot_type, core=core, level=level,
        x_domain=x_domain, y_domain=y_domain,
        outcomes=outcomes,
        task_id_rule="required" if level == "task" else "empty",
        primary_key=primary_key, sort_key=primary_key,
        **kwargs,
    )


PLOT_ROW_SCHEMA = {
    "certification_ratio": _contract(
        "certification_ratio", "CORE-1", "taskset", "utilization", "binary",
        ANALYSIS_SOLVER_STATUSES, relation_rule="absent",
        variants=CORE1_VARIANTS, association="certification_status",
    ),
    "certification_ratio_e0": _contract(
        "certification_ratio_e0", "CORE-1", "taskset", "exact_e0", "binary",
        ANALYSIS_SOLVER_STATUSES, relation_rule="absent",
        variants=CORE1_VARIANTS, association="certification_status",
    ),
    "runtime": _contract(
        "runtime", "CORE-1", "taskset", "utilization", "runtime",
        ANALYSIS_SOLVER_STATUSES, relation_rule="absent",
        variants=CORE1_VARIANTS,
    ),
    "timeout_rate": _contract(
        "timeout_rate", "CORE-1", "taskset", "utilization", "binary",
        ANALYSIS_SOLVER_STATUSES, relation_rule="absent",
        variants=CORE1_VARIANTS, association="timeout_indicator",
    ),
    "loc_vs_cw_scatter": _contract(
        "loc_vs_cw_scatter", "CORE-1", "task", "candidate", "candidate", COMPARISON_OUTCOMES,
        relation_rule="absent", variants=("LOC_THETA_LOC",),
        association="candidate_comparison",
        source_relation=CORE1_COMPARISON_RELATION,
    ),
    "response_reduction_distribution": _contract(
        "response_reduction_distribution", "CORE-1", "task", "signed_integer", "one", COMPARISON_OUTCOMES,
        relation_rule="absent", variants=("LOC_THETA_LOC",),
        association="reduction_sign",
        source_relation=CORE1_COMPARISON_RELATION,
    ),
    "certification_outcome_matrix": _contract(
        "certification_outcome_matrix", "CORE-1", "taskset", "binary", "binary", ("00", "01", "10", "11"),
        relation_rule="absent", variants=("LOC_THETA_LOC",),
        association="matrix_bits", source_relation=CORE1_COMPARISON_RELATION,
    ),
    "variant_certification": _contract(
        "variant_certification", "CORE-2", "taskset", "utilization", "binary",
        ANALYSIS_SOLVER_STATUSES, relation_rule="empty", variants=CORE2_VARIANTS,
        association="certification_status",
    ),
    "variant_runtime": _contract(
        "variant_runtime", "CORE-2", "taskset", "utilization", "runtime",
        ANALYSIS_SOLVER_STATUSES, relation_rule="empty", variants=CORE2_VARIANTS,
    ),
    "first_failed_priority": _contract(
        "first_failed_priority", "CORE-2", "taskset", "utilization", "optional_priority",
        ANALYSIS_SOLVER_STATUSES, relation_rule="empty", variants=CORE2_VARIANTS,
        y_required=False, association="first_failed_status",
        y_source_field="first_failed_priority",
        outcome_y_rules=(
            ("COMPLETED", "forbidden"),
            ("NO_CANDIDATE", "required"),
            ("TIMEOUT", "optional"),
            ("NUMERIC_ERROR", "required"),
            ("NOT_APPLICABLE_DEPENDENCY", "forbidden"),
            ("INTERNAL_CONFORMANCE_FAILURE", "forbidden"),
            ("UNSUPPORTED_EXPERIMENT_VARIANT", "forbidden"),
        ),
    ),
    "envelope_search_cost": _contract(
        "envelope_search_cost", "CORE-2", "taskset", "utilization", "nonnegative_integer",
        ANALYSIS_SOLVER_STATUSES, relation_rule="empty", variants=CORE2_VARIANTS,
    ),
    "ablation": _contract(
        "ablation", "CORE-2", "task", "priority", "signed_integer", COMPARISON_OUTCOMES,
        relation_rule="mapped", relations=tuple(CORE2_RELATION_RIGHT_VARIANT),
        relation_variants=tuple(CORE2_RELATION_RIGHT_VARIANT.items()),
        association="reduction_sign",
    ),
    "ablation_gain_loss": _contract(
        "ablation_gain_loss", "CORE-2", "taskset", "exact_e0", "gain_loss",
        ("GAIN", "LOSS", "UNCHANGED"), relation_rule="mapped",
        relations=tuple(CORE2_RELATION_RIGHT_VARIANT),
        relation_variants=tuple(CORE2_RELATION_RIGHT_VARIANT.items()),
        association="gain_loss",
    ),
    "dependency_applicability": _contract(
        "dependency_applicability", "CORE-2", "taskset", "exact_e0", "binary", ("VALID", "INVALID"),
        relation_rule="fixed", relations=("FIXED_CW_DEPENDENCY",),
        variants=("LOC_THETA_CW",), association="dependency_validity",
    ),
}

CORE_PLOT_TYPES = {
    core: tuple(
        plot_type for plot_type, contract in PLOT_ROW_SCHEMA.items()
        if contract.core == core
    )
    for core in ("CORE-1", "CORE-2")
}
PLOT_COLUMNS = {"CORE-1": CORE1_PLOT_COLUMNS, "CORE-2": CORE2_PLOT_COLUMNS}
PLOT_PRIMARY_KEYS = {
    core: PLOT_ROW_SCHEMA[plot_types[0]].primary_key
    for core, plot_types in CORE_PLOT_TYPES.items()
}
TASK_LEVEL_PLOTS = {
    core: frozenset(
        plot_type for plot_type in CORE_PLOT_TYPES[core]
        if PLOT_ROW_SCHEMA[plot_type].level == "task"
    )
    for core in CORE_PLOT_TYPES
}
RELATION_PLOTS = {
    "CORE-2": frozenset(
        plot_type for plot_type in CORE_PLOT_TYPES["CORE-2"]
        if PLOT_ROW_SCHEMA[plot_type].relation_rule in {"fixed", "mapped"}
    ),
}
_PLAIN_TASK_ID = re.compile(r"0|[1-9][0-9]*")
_PLAIN_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)")
_NONNEGATIVE_INTEGER = re.compile(r"0|[1-9][0-9]*")
_DECIMAL_FLOAT = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)


class PlotTableError(RuntimeError):
    """Raised before a plot consumer creates any presentation artifact."""


def _validate_plot_contract_matrix(expected_core: str) -> None:
    """Detect runtime drift between exported constants and the sole matrix."""

    expected_columns = PLOT_COLUMNS.get(expected_core)
    if expected_columns is None:
        raise PlotTableError(f"plot schema has unsupported core: {expected_core}")
    contracts = [
        PLOT_ROW_SCHEMA[plot_type]
        for plot_type in CORE_PLOT_TYPES[expected_core]
    ]
    if not contracts or any(
        contract.schema != PLOT_SCHEMA
        or contract.schema_version != PLOT_SCHEMA_VERSION
        or contract.exact_header != expected_columns
        or contract.plot_type != plot_type
        or contract.sort_key != contract.primary_key
        for plot_type, contract in zip(CORE_PLOT_TYPES[expected_core], contracts)
    ):
        raise PlotTableError(
            f"plot schema contract matrix is internally inconsistent: {expected_core}"
        )


def _text(row: Mapping[str, Any], column: str) -> str:
    value = row.get(column, "")
    return "" if value is None else str(value)


def _plot_number(
    value: str, path: Path, number: int, column: str,
) -> Fraction | None:
    if value == "":
        return None
    try:
        exact = Fraction(value)
        converted = float(exact)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise PlotTableError(
            f"plot schema invalid numeric {column}: {path}:{number}:{value!r}"
        ) from exc
    if not math.isfinite(converted):
        raise PlotTableError(
            f"plot schema non-finite numeric {column}: {path}:{number}"
        )
    return exact


def _validate_domain(
    value: str,
    domain: str,
    *,
    required: bool,
    path: Path,
    number: int,
    column: str,
) -> Fraction | float | None:
    if not value:
        if required:
            raise PlotTableError(
                f"plot schema requires numeric {column}: {path}:{number}"
            )
        return None
    if domain == "runtime":
        if not _DECIMAL_FLOAT.fullmatch(value):
            raise PlotTableError(
                f"plot schema {column} must be a canonical decimal runtime: "
                f"{path}:{number}:{value!r}"
            )
        try:
            converted = float(value)
        except (ValueError, OverflowError) as exc:
            raise PlotTableError(
                f"plot schema invalid decimal runtime: {path}:{number}:{value!r}"
            ) from exc
        if not math.isfinite(converted):
            raise PlotTableError(
                f"plot schema non-finite decimal runtime: {path}:{number}"
            )
        return converted
    exact = _plot_number(value, path, number, column)
    assert exact is not None
    if domain in {"binary", "one", "gain_loss"}:
        allowed = {
            "binary": {"0", "1"},
            "one": {"1"},
            "gain_loss": {"-1", "0", "1"},
        }[domain]
        if value not in allowed:
            raise PlotTableError(
                f"plot schema {column} is outside {domain}: "
                f"{path}:{number}:{value!r}"
            )
    elif domain in {"candidate", "priority", "nonnegative_integer"}:
        if not _NONNEGATIVE_INTEGER.fullmatch(value):
            raise PlotTableError(
                f"plot schema {column} must be a canonical nonnegative integer: "
                f"{path}:{number}:{value!r}"
            )
    elif domain == "optional_priority":
        if not _NONNEGATIVE_INTEGER.fullmatch(value):
            raise PlotTableError(
                f"plot schema {column} must be an optional canonical priority: "
                f"{path}:{number}:{value!r}"
            )
    elif domain == "signed_integer":
        if not _PLAIN_INTEGER.fullmatch(value):
            raise PlotTableError(
                f"plot schema {column} must be a canonical integer: "
                f"{path}:{number}:{value!r}"
            )
    elif domain in {"utilization", "exact_e0"}:
        if exact < 0 or (exact == 0 and value.startswith("-")):
            raise PlotTableError(
                f"plot schema {column} must be nonnegative {domain}: "
                f"{path}:{number}:{value!r}"
            )
    else:
        raise PlotTableError(f"plot schema has unknown numeric domain: {domain}")
    return exact


def _expected_comparison_outcome(delta: Fraction) -> str:
    return "TIGHTER" if delta > 0 else "EQUAL" if delta == 0 else "VIOLATION"


def validate_plot_row_semantics(
    row: Mapping[str, Any],
    *,
    expected_core: str,
    path: Path | str = Path("<generated-plot-row>"),
    number: int = 1,
) -> None:
    """Validate one row against the sole per-plot-type semantic matrix."""

    path = Path(path)
    plot_type = _text(row, "plot")
    contract = PLOT_ROW_SCHEMA.get(plot_type)
    if contract is None or contract.core != expected_core:
        raise PlotTableError(
            f"plot type is not frozen for {expected_core}: "
            f"{path}:{number}:{plot_type!r}"
        )
    if (
        _text(row, "plot_schema") != contract.schema
        or _text(row, "plot_schema_version") != str(contract.schema_version)
    ):
        raise PlotTableError(
            f"plot schema artifact identity mismatch: "
            f"{path}:{number}:{plot_type}"
        )
    task_id = _text(row, "task_id")
    if contract.task_id_rule == "required":
        if not _PLAIN_TASK_ID.fullmatch(task_id):
            raise PlotTableError(
                f"plot schema task-level row requires canonical task_id: "
                f"{path}:{number}:{plot_type}"
            )
    elif contract.task_id_rule == "empty" and task_id:
        raise PlotTableError(
            f"plot schema taskset-level row forbids task_id: "
            f"{path}:{number}:{plot_type}"
        )
    elif contract.task_id_rule not in {"required", "empty"}:
        raise PlotTableError(
            f"plot schema has unknown task_id rule: {plot_type}"
        )

    relation = _text(row, "relation")
    if contract.relation_rule in {"absent", "empty"}:
        if relation:
            raise PlotTableError(
                f"plot schema forbids relation: {path}:{number}:{plot_type}"
            )
    elif relation not in contract.relations:
        raise PlotTableError(
            f"plot schema relation is not frozen: "
            f"{path}:{number}:{plot_type}:{relation!r}"
        )

    variant = _text(row, "variant")
    if contract.relation_rule == "mapped":
        expected_variant = dict(contract.relation_variants).get(relation)
        if variant != expected_variant:
            raise PlotTableError(
                f"plot schema relation/right-variant mismatch: "
                f"{path}:{number}:{plot_type}:{relation!r}:{variant!r}"
            )
    elif variant not in contract.variants:
        raise PlotTableError(
            f"plot schema variant is not frozen: "
            f"{path}:{number}:{plot_type}:{variant!r}"
        )

    outcome = _text(row, "outcome")
    if outcome not in contract.outcomes:
        raise PlotTableError(
            f"plot schema outcome is not frozen: "
            f"{path}:{number}:{plot_type}:{outcome!r}"
        )
    x_text = _text(row, "x")
    y_text = _text(row, "y")
    x = _validate_domain(
        x_text, contract.x_domain, required=contract.x_required,
        path=path, number=number, column="x",
    )
    y = _validate_domain(
        y_text, contract.y_domain, required=contract.y_required,
        path=path, number=number, column="y",
    )

    if expected_core == "CORE-1":
        exact_e0 = _text(row, "exact_e0")
        if exact_e0:
            _validate_domain(
                exact_e0, "exact_e0", required=True, path=path,
                number=number, column="exact_e0",
            )
        if contract.x_domain == "utilization":
            utilization = _validate_domain(
                _text(row, "utilization"), "utilization", required=True,
                path=path, number=number, column="utilization",
            )
            if x != utilization:
                raise PlotTableError(
                    f"plot schema x/utilization mismatch: {path}:{number}:{plot_type}"
                )
        elif contract.x_domain == "exact_e0":
            frozen_e0 = _validate_domain(
                exact_e0, "exact_e0", required=True, path=path,
                number=number, column="exact_e0",
            )
            if x != frozen_e0:
                raise PlotTableError(
                    f"plot schema x/exact_e0 mismatch: {path}:{number}:{plot_type}"
                )

    association = contract.association
    if association == "certification_status":
        if y_text == "1" and outcome != "COMPLETED":
            raise PlotTableError(
                f"plot schema certified outcome/status mismatch: {path}:{number}"
            )
    elif association == "timeout_indicator":
        expected_y = "1" if outcome == "TIMEOUT" else "0"
        if y_text != expected_y:
            raise PlotTableError(
                f"plot schema timeout outcome/y mismatch: {path}:{number}"
            )
    elif association == "matrix_bits":
        if outcome != f"{x_text}{y_text}":
            raise PlotTableError(
                f"plot schema certification matrix mismatch: {path}:{number}"
            )
    elif association == "candidate_comparison":
        assert x is not None and y is not None
        if outcome != _expected_comparison_outcome(x - y):
            raise PlotTableError(
                f"plot schema candidate comparison mismatch: {path}:{number}"
            )
    elif association == "reduction_sign":
        reduction = y if plot_type == "ablation" else x
        assert reduction is not None
        if outcome != _expected_comparison_outcome(reduction):
            raise PlotTableError(
                f"plot schema reduction/outcome mismatch: {path}:{number}"
            )
    elif association == "gain_loss":
        expected = {"1": "GAIN", "-1": "LOSS", "0": "UNCHANGED"}[y_text]
        if outcome != expected:
            raise PlotTableError(
                f"plot schema gain/loss mismatch: {path}:{number}"
            )
    elif association == "dependency_validity":
        expected = "1" if outcome == "VALID" else "0"
        if y_text != expected:
            raise PlotTableError(
                f"plot schema dependency outcome/y mismatch: {path}:{number}"
            )
    elif association == "first_failed_status":
        y_rule = dict(contract.outcome_y_rules).get(outcome)
        if y_rule == "required" and not y_text:
            raise PlotTableError(
                f"plot schema solver status requires first failure: {path}:{number}"
            )
        if y_rule == "forbidden" and y_text:
            raise PlotTableError(
                f"plot schema solver status forbids first failure: {path}:{number}"
            )
        if y_rule not in {"required", "optional", "forbidden"}:
            raise PlotTableError(
                f"plot schema solver status has no first-failure rule: {path}:{number}"
            )


def _bind_plot_artifact(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Physically bind producer rows to the sole per-type schema contract."""

    output = []
    for row in rows:
        plot_type = str(row.get("plot", ""))
        contract = PLOT_ROW_SCHEMA.get(plot_type)
        if contract is None:
            raise PlotTableError(f"plot type has no producer contract: {plot_type!r}")
        output.append({
            "plot_schema": contract.schema,
            "plot_schema_version": contract.schema_version,
            **row,
        })
    return output


def validate_generated_plot_rows(
    rows: Iterable[Mapping[str, Any]], *, expected_core: str,
) -> list[dict[str, Any]]:
    """Fail closed if the producer itself emits a semantically invalid row."""

    _validate_plot_contract_matrix(expected_core)
    frozen = [dict(row) for row in rows]
    expected_columns = set(PLOT_COLUMNS[expected_core])
    for number, row in enumerate(frozen, start=1):
        if set(row) != expected_columns:
            raise PlotTableError(
                f"generated plot row schema mismatch: {expected_core}:{number}"
            )
        validate_plot_row_semantics(
            row, expected_core=expected_core, number=number,
        )
    return frozen


def validate_canonical_plot_table(
    path: Path, *, expected_core: str
) -> list[dict[str, str]]:
    """Validate the sole frozen plot-input CSV contract, without writing."""

    path = Path(path)
    _validate_plot_contract_matrix(expected_core)
    expected_name = (
        "core1_plot_data.csv" if expected_core == "CORE-1"
        else "core2_plot_data.csv"
    )
    if path.name != expected_name:
        raise PlotTableError(
            f"plot schema filename/core mismatch: expected {expected_name}, got {path.name}"
        )
    if not path.is_file():
        raise PlotTableError(f"plot schema input is missing: {path}")
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            physical = list(csv.reader(handle))
    except Exception as exc:
        raise PlotTableError(f"plot schema input is unreadable: {path}") from exc
    if not physical:
        raise PlotTableError(f"plot schema has no header: {path}")
    header = physical[0]
    columns = PLOT_COLUMNS[expected_core]
    if header != list(columns):
        raise PlotTableError(
            f"plot schema header mismatch for {expected_name}: "
            f"expected {list(columns)}, got {header}"
        )
    allowed_types = frozenset(CORE_PLOT_TYPES[expected_core])
    primary_key = PLOT_PRIMARY_KEYS[expected_core]
    rows: list[dict[str, str]] = []
    seen = set()
    prior_key: tuple[str, ...] | None = None
    for number, values in enumerate(physical[1:], start=2):
        if len(values) != len(header):
            raise PlotTableError(
                f"plot schema row width mismatch: {path}:{number}"
            )
        row = dict(zip(header, values))
        for column, value in row.items():
            if value != value.strip():
                raise PlotTableError(
                    f"plot schema contains non-canonical whitespace: "
                    f"{path}:{number}:{column}"
                )
        plot_type = row["plot"]
        if plot_type not in allowed_types:
            raise PlotTableError(
                f"plot type is not frozen for {expected_core}: "
                f"{path}:{number}:{plot_type!r}"
            )
        for column in ("cell_id", "taskset_id", "variant", "outcome"):
            if not row[column]:
                raise PlotTableError(
                    f"plot schema requires {column}: {path}:{number}:{plot_type}"
                )
        validate_plot_row_semantics(
            row, expected_core=expected_core, path=path, number=number,
        )
        key = tuple(row[column] for column in primary_key)
        if key in seen:
            raise PlotTableError(
                f"plot schema duplicate primary key: {path}:{number}:{key}"
            )
        if prior_key is not None and key < prior_key:
            raise PlotTableError(
                f"plot schema row order is not canonical: {path}:{number}"
            )
        seen.add(key)
        prior_key = key
        rows.append(row)
    return rows


def _exact_x(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return int(numerator) / int(denominator)
    return float(value)


def core1_plot_rows(
    tasksets: Iterable[Mapping[str, str]],
    comparisons: Iterable[Mapping[str, Any]],
    certification: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for row in tasksets:
        rows.extend((
            {"plot": "certification_ratio", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "task_id": "", "x": row["utilization"], "y": 1 if row["taskset_proven"] == "True" else 0, "outcome": row["solver_status"]},
            {"plot": "certification_ratio_e0", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "task_id": "", "x": _exact_x(row["exact_e0"]), "y": 1 if row["taskset_proven"] == "True" else 0, "outcome": row["solver_status"]},
            {"plot": "runtime", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "task_id": "", "x": row["utilization"], "y": row["runtime_wall_seconds"], "outcome": row["solver_status"]},
            {"plot": "timeout_rate", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "task_id": "", "x": row["utilization"], "y": 1 if row["solver_status"] == "TIMEOUT" else 0, "outcome": row["solver_status"]},
        ))
    for row in comparisons:
        common = {
            "cell_id": row["cell_id"],
            "taskset_id": row["taskset_id"], "utilization": "",
            "exact_e0": row["exact_e0"], "variant": row["right_variant"],
            "task_id": row["task_id"],
            "outcome": row["status"],
        }
        rows.append({"plot": "loc_vs_cw_scatter", **common, "x": row["left_candidate"], "y": row["right_candidate"]})
        rows.append({"plot": "response_reduction_distribution", **common, "x": row["reduction"], "y": 1})
    for row in certification:
        rows.append({
            "plot": "certification_outcome_matrix", "cell_id": row["cell_id"],
            "taskset_id": row["taskset_id"], "utilization": "",
            "exact_e0": row["exact_e0"], "variant": row["right_variant"],
            "task_id": "",
            "x": int(row["left_certified"]), "y": int(row["right_certified"]),
            "outcome": f"{int(row['left_certified'])}{int(row['right_certified'])}",
        })
    return validate_generated_plot_rows(
        _bind_plot_artifact(rows), expected_core="CORE-1"
    )


def core2_plot_rows(
    tasksets: Iterable[Mapping[str, str]],
    task_results: Iterable[Mapping[str, str]],
    comparisons: Iterable[Mapping[str, Any]],
    taskset_comparisons: Iterable[Mapping[str, Any]],
    dependencies: Iterable[Mapping[str, str]],
) -> list[dict[str, Any]]:
    task_results = list(task_results)
    rows = []
    for row in tasksets:
        rows.extend((
            {"plot": "variant_certification", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["analysis_variant"], "relation": "", "task_id": "", "x": row["utilization"], "y": 1 if row["taskset_proven"] == "True" else 0, "outcome": row["solver_status"]},
            {"plot": "variant_runtime", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["analysis_variant"], "relation": "", "task_id": "", "x": row["utilization"], "y": row["runtime_wall_seconds"], "outcome": row["solver_status"]},
            {"plot": "first_failed_priority", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["analysis_variant"], "relation": "", "task_id": "", "x": row["utilization"], "y": row["first_failed_priority"], "outcome": row["solver_status"]},
        ))
        members = [task for task in task_results if task["analysis_id"] == row["analysis_id"]]
        rows.append({
            "plot": "envelope_search_cost", "cell_id": row["cell_id"],
            "taskset_id": row["taskset_id"], "variant": row["analysis_variant"],
            "relation": "", "task_id": "", "x": row["utilization"],
            "y": sum(int(task["envelope_call_count"] or 0) for task in members),
            "outcome": row["solver_status"],
        })
    for row in comparisons:
        rows.append({"plot": "ablation", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["right_variant"], "relation": row["relation"], "task_id": row["task_id"], "x": row["priority_rank"], "y": row["reduction"], "outcome": row["status"]})
    for row in taskset_comparisons:
        outcome = "GAIN" if row["certification_gain"] else "LOSS" if row["certification_loss"] else "UNCHANGED"
        rows.append({"plot": "ablation_gain_loss", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["right_variant"], "relation": row["relation"], "task_id": "", "x": _exact_x(str(row["exact_e0"])), "y": int(row["certification_gain"]) - int(row["certification_loss"]), "outcome": outcome})
    for row in dependencies:
        rows.append({"plot": "dependency_applicability", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["target_variant"], "relation": "FIXED_CW_DEPENDENCY", "task_id": "", "x": row["target_e0"], "y": 1 if row["applicable"] == "True" else 0, "outcome": row["dependency_check_status"]})
    return validate_generated_plot_rows(
        _bind_plot_artifact(rows), expected_core="CORE-2"
    )
