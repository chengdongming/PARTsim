"""Minimal fail-closed identity and artifact contract for v9.3 CORE-4."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, Mapping, Sequence

from .cell_model import expand_cells
from .config import canonical_json, config_hash, domain_hash, fraction_text, load_config
from .monotonicity import service_curve_relation, terminal_status_class
from .paired_sweep import make_sweep, paired_analysis_id
from .result_writer import (
    ATTEMPT_COLUMNS,
    FAILURE_COLUMNS,
    GENERATED_COLUMNS,
    REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS,
    ResultWriterError,
    atomic_write_text,
    read_csv,
    validate_csv_header,
)


CORE4_RUN_SCHEMA = "ASAP_BLOCK_V9_3_CORE4_RUN_V2"
CORE4_CHECKPOINT_SCHEMA = "ASAP_BLOCK_V9_3_CORE4_CHECKPOINT_V2"
CORE4_ANALYSIS_INPUT_DOMAIN = "ASAP_BLOCK:V9.3:CORE4_ANALYSIS_INPUT:v2"

SENSITIVITY_REQUEST_COLUMNS = (
    "experiment_id",
    "sweep_id",
    "base_taskset_id",
    "base_taskset_hash",
    "taskset_index",
    "M",
    "task_n",
    "base_priority_hash",
    "base_power_hash",
    "base_service_curve_identity",
    "base_task_input_json",
    "parameter_name",
    "ordered_parameter_levels",
    "level_index",
    "level_encoding",
    "variant",
    "analysis_id",
    "analysis_input_hash",
    "exact_e0",
    "service_curve_declaration_id",
    "service_curve_identity",
    "service_curve_values_json",
    "power_scale",
    "analysis_power_hash",
    "analysis_task_input_json",
    "numerical_mode",
    "availability",
    "availability_reason",
    "service_curve_relation_to_previous",
    "paired_analysis_ids",
)

CORE4_PRIMARY_TABLES = {
    "sensitivity_requests.csv": SENSITIVITY_REQUEST_COLUMNS,
    "generated_tasksets.csv": GENERATED_COLUMNS,
    "analysis_requests.csv": REQUEST_COLUMNS,
    "analysis_attempts.csv": ATTEMPT_COLUMNS,
    "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
    "per_task_results.csv": TASK_RESULT_COLUMNS,
    "failures.csv": FAILURE_COLUMNS,
}

# checkpoint.json is the final atomic commit marker. It is validated directly
# by the artifact contract and deliberately excluded from the immutable file
# inventory so FINALIZING can become COMPLETED without invalidating the
# manifest. The manifest must never hash itself.
CORE4_MANIFEST_EXCLUDED_PATHS = frozenset({
    "checkpoint.json",
    "file_hashes.sha256",
})
CORE4_COMPLETED_REQUIRED_FILES = frozenset({
    "run_metadata.json",
    "run_config.yaml",
    *CORE4_PRIMARY_TABLES,
    "paired_parameter_results.csv",
    "monotonicity_checks.csv",
    "sensitivity_summary.csv",
    "sensitivity_summary.json",
    "core4_plot_data.csv",
})


class Core4ContractError(RuntimeError):
    """Raised when persisted CORE-4 evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class ValidatedCore4Rows:
    requests: tuple[Mapping[str, str], ...]
    generated: tuple[Mapping[str, str], ...]
    results: tuple[Mapping[str, str], ...]
    tasks: tuple[Mapping[str, str], ...]
    failures: tuple[Mapping[str, str], ...]


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Core4ContractError(f"missing CORE-4 {label}: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Core4ContractError(f"unreadable CORE-4 {label}: {path.name}") from exc
    if not isinstance(value, dict):
        raise Core4ContractError(f"CORE-4 {label} must be a JSON object")
    return value


def _json_value(text: Any, label: str, expected_type: type | tuple[type, ...]) -> Any:
    if not isinstance(text, str) or not text:
        raise Core4ContractError(f"{label} must be non-empty canonical JSON")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Core4ContractError(f"{label} is not JSON") from exc
    if not isinstance(value, expected_type):
        raise Core4ContractError(f"{label} has the wrong JSON type")
    if canonical_json(value) != text:
        raise Core4ContractError(f"{label} is not canonical JSON")
    return value


def _plain_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise Core4ContractError(f"{label} is not a plain integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise Core4ContractError(f"{label} is not a plain integer") from exc
    if str(parsed) != str(value) or parsed < minimum:
        raise Core4ContractError(f"{label} is not a canonical integer")
    return parsed


def _unique(rows: Sequence[Mapping[str, str]], fields: Sequence[str], label: str) -> None:
    seen: Dict[tuple[str, ...], Mapping[str, str]] = {}
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in fields)
        if any(not value for value in key):
            raise Core4ContractError(f"{label} has an empty identity field: {fields}")
        if key in seen:
            conflict = canonical_json(seen[key]) != canonical_json(row)
            qualifier = "conflicting " if conflict else ""
            raise Core4ContractError(f"{qualifier}duplicate {label}: {key}")
        seen[key] = row


def _read_table(root: Path, name: str, columns: Sequence[str]) -> list[Dict[str, str]]:
    path = root / name
    if not path.is_file():
        raise Core4ContractError(f"missing required CORE-4 table: {name}")
    try:
        validate_csv_header(path, columns)
    except ResultWriterError as exc:
        raise Core4ContractError(str(exc)) from exc
    return read_csv(path)


def _task_payload(text: str, label: str) -> list[Dict[str, Any]]:
    rows = _json_value(text, label, list)
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise Core4ContractError(f"{label} must contain task objects")
    task_ids = [str(row.get("task_id", "")) for row in rows]
    if not all(task_ids) or len(task_ids) != len(set(task_ids)):
        raise Core4ContractError(f"{label} has duplicate or empty task IDs")
    ranks = []
    for row in rows:
        for field in ("C", "D", "T", "priority_rank"):
            _plain_int(row.get(field), f"{label}.{field}")
        try:
            power = Fraction(str(row.get("P")))
        except (ValueError, ZeroDivisionError) as exc:
            raise Core4ContractError(f"{label}.P is not an exact rational") from exc
        if power <= 0 or fraction_text(power) != str(row.get("P")):
            raise Core4ContractError(f"{label}.P is not a canonical positive rational")
        ranks.append(int(row["priority_rank"]))
    if ranks != list(range(len(rows))):
        raise Core4ContractError(f"{label} priority ranks are not canonical RM order")
    return rows


def _priority_hash(tasks: Sequence[Mapping[str, Any]]) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
        [
            {"task_id": str(row["task_id"]), "priority_rank": int(row["priority_rank"])}
            for row in tasks
        ],
    )


def _power_hash(tasks: Sequence[Mapping[str, Any]], *, sensitivity: bool) -> str:
    domain = (
        "ASAP_BLOCK:V9.3:SENSITIVITY_POWER_VECTOR:v1"
        if sensitivity
        else "ASAP_BLOCK:V9.3:POWER_VECTOR:v1"
    )
    return domain_hash(
        domain,
        [{"task_id": str(row["task_id"]), "P": str(row["P"])} for row in tasks],
    )


def core4_analysis_input_hash(row: Mapping[str, Any]) -> str:
    base_tasks = _json_value(
        str(row.get("base_task_input_json", "")), "base_task_input_json", list
    )
    analysis_tasks = _json_value(
        str(row.get("analysis_task_input_json", "")),
        "analysis_task_input_json",
        list,
    )
    service_values_text = str(row.get("service_curve_values_json", ""))
    service_values = (
        _json_value(service_values_text, "service_curve_values_json", list)
        if service_values_text
        else None
    )
    preimage = {
        "experiment_id": row.get("experiment_id"),
        "base_taskset_id": row.get("base_taskset_id"),
        "base_taskset_hash": row.get("base_taskset_hash"),
        "taskset_index": _plain_int(row.get("taskset_index"), "taskset_index"),
        "M": _plain_int(row.get("M"), "M", minimum=1),
        "task_n": _plain_int(row.get("task_n"), "task_n", minimum=1),
        "base_priority_hash": row.get("base_priority_hash"),
        "base_power_hash": row.get("base_power_hash"),
        "base_service_curve_identity": row.get("base_service_curve_identity"),
        "base_tasks": base_tasks,
        "analysis_tasks": analysis_tasks,
        "exact_e0": row.get("exact_e0"),
        "service_curve_declaration_id": row.get("service_curve_declaration_id"),
        "service_curve_identity": row.get("service_curve_identity"),
        "service_curve_values": service_values,
        "power_scale": row.get("power_scale"),
        "analysis_power_hash": row.get("analysis_power_hash"),
        "variant": row.get("variant"),
        "numerical_mode": row.get("numerical_mode"),
        "availability": row.get("availability"),
    }
    return domain_hash(CORE4_ANALYSIS_INPUT_DOMAIN, preimage)


def _constant(rows: Sequence[Mapping[str, str]], fields: Iterable[str], label: str) -> None:
    for field in fields:
        values = {str(row.get(field, "")) for row in rows}
        if len(values) != 1:
            raise Core4ContractError(f"{label} changed non-target field {field}")


def _validate_effective_tasks(row: Mapping[str, str]) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    base = _task_payload(row["base_task_input_json"], "base_task_input_json")
    effective = _task_payload(
        row["analysis_task_input_json"], "analysis_task_input_json"
    )
    if len(base) != _plain_int(row["task_n"], "task_n", minimum=1):
        raise Core4ContractError("base task payload count does not match task_n")
    if len(effective) != len(base):
        raise Core4ContractError("analysis task payload count changed")
    scale = Fraction(row["power_scale"])
    if scale <= 0 or fraction_text(scale) != row["power_scale"]:
        raise Core4ContractError("power_scale is not a canonical positive Fraction")
    for original, transformed in zip(base, effective):
        for field in ("task_id", "priority_rank", "C", "D", "T"):
            if str(original.get(field)) != str(transformed.get(field)):
                raise Core4ContractError(f"analysis changed non-power task field {field}")
        expected_power = Fraction(str(original["P"])) * scale
        if str(transformed["P"]) != fraction_text(expected_power):
            raise Core4ContractError("power vector is not uniformly scaled by power_scale")
    if row["base_priority_hash"] != _priority_hash(base):
        raise Core4ContractError("base priority hash mismatch")
    if row["base_power_hash"] != _power_hash(base, sensitivity=False):
        raise Core4ContractError("base power hash mismatch")
    if row["analysis_power_hash"] != _power_hash(effective, sensitivity=True):
        raise Core4ContractError("analysis power hash mismatch")
    return base, effective


def _validate_axis_group(rows: Sequence[Mapping[str, str]], levels: Sequence[str]) -> None:
    parameter = rows[0]["parameter_name"]
    _constant(
        rows,
        (
            "experiment_id",
            "sweep_id",
            "base_taskset_id",
            "base_taskset_hash",
            "taskset_index",
            "M",
            "task_n",
            "base_priority_hash",
            "base_power_hash",
            "base_service_curve_identity",
            "base_task_input_json",
            "numerical_mode",
            "ordered_parameter_levels",
            "paired_analysis_ids",
        ),
        parameter,
    )
    decoded_levels = [json.loads(level) for level in levels]
    for row, level in zip(rows, decoded_levels):
        declared_availability = (
            str(level.get("availability", "AVAILABLE"))
            if isinstance(level, dict) else "AVAILABLE"
        )
        declared_reason = (
            str(level.get("reason", "")) if isinstance(level, dict) else ""
        )
        if row["availability"] != declared_availability:
            raise Core4ContractError(
                "request availability does not match the ordered level declaration"
            )
        if row["availability_reason"] != declared_reason:
            raise Core4ContractError(
                "request availability reason does not match the level declaration"
            )
        if parameter != "service_curve" and (
            row["availability"] != "AVAILABLE"
            or row["service_curve_relation_to_previous"] != "NOT_APPLICABLE"
        ):
            raise Core4ContractError(
                "only the declared service_curve axis may be unavailable"
            )
    if parameter == "initial_energy":
        _constant(
            rows,
            (
                "variant",
                "service_curve_declaration_id",
                "service_curve_identity",
                "service_curve_values_json",
                "power_scale",
                "analysis_power_hash",
                "analysis_task_input_json",
            ),
            parameter,
        )
        for row, level in zip(rows, decoded_levels):
            if row["power_scale"] != "1":
                raise Core4ContractError("initial_energy axis changed power_scale")
            if row["exact_e0"] != fraction_text(Fraction(str(level))):
                raise Core4ContractError("initial_energy level does not match exact_e0")
            if row["service_curve_identity"] != row["base_service_curve_identity"]:
                raise Core4ContractError("initial_energy axis changed service identity")
    elif parameter == "service_curve":
        _constant(
            rows,
            (
                "variant",
                "exact_e0",
                "power_scale",
                "analysis_power_hash",
                "analysis_task_input_json",
            ),
            parameter,
        )
        for row, level in zip(rows, decoded_levels):
            if row["power_scale"] != "1":
                raise Core4ContractError("service_curve axis changed power_scale")
            if not isinstance(level, dict) or row["service_curve_declaration_id"] != level.get("id"):
                raise Core4ContractError("service level does not match its declaration")
            if row["availability"] == "UNAVAILABLE":
                if row["service_curve_identity"] or row["service_curve_values_json"]:
                    raise Core4ContractError("unavailable service level has solver material")
                if row["service_curve_relation_to_previous"] != "DEPENDENCY_UNAVAILABLE":
                    raise Core4ContractError("unavailable service relation is not explicit")
            elif not row["service_curve_identity"] or not row["service_curve_values_json"]:
                raise Core4ContractError("available service level is missing exact material")
        previous_available: Mapping[str, str] | None = None
        for row in rows:
            if row["availability"] == "UNAVAILABLE":
                previous_available = None
                continue
            if previous_available is None:
                if row["service_curve_relation_to_previous"] != "FIRST_LEVEL":
                    raise Core4ContractError("first available service level is not marked FIRST_LEVEL")
            previous_available = row
        for left, right in zip(rows, rows[1:]):
            if "UNAVAILABLE" in {left["availability"], right["availability"]}:
                continue
            left_curve = _json_value(
                left["service_curve_values_json"], "service_curve_values_json", list
            )
            right_curve = _json_value(
                right["service_curve_values_json"], "service_curve_values_json", list
            )
            relation = service_curve_relation(left_curve, right_curve)
            if relation not in {"RIGHT_STRONGER", "EQUAL"}:
                raise Core4ContractError("service levels are not exact weak-to-strong prefixes")
            if right["service_curve_relation_to_previous"] != relation:
                raise Core4ContractError("persisted service relation does not match exact prefixes")
    elif parameter == "power_scale":
        _constant(
            rows,
            (
                "variant",
                "exact_e0",
                "service_curve_declaration_id",
                "service_curve_identity",
                "service_curve_values_json",
            ),
            parameter,
        )
        for row, level in zip(rows, decoded_levels):
            if row["power_scale"] != fraction_text(Fraction(str(level))):
                raise Core4ContractError("power level does not match power_scale")
            if row["service_curve_identity"] != row["base_service_curve_identity"]:
                raise Core4ContractError("power_scale axis changed service identity")
    elif parameter == "method":
        if decoded_levels != ["CW_THETA_CW", "LOC_THETA_LOC"]:
            raise Core4ContractError("method axis is not CW_THETA_CW -> LOC_THETA_LOC")
        _constant(
            rows,
            (
                "exact_e0",
                "service_curve_declaration_id",
                "service_curve_identity",
                "service_curve_values_json",
                "power_scale",
                "analysis_power_hash",
                "analysis_task_input_json",
            ),
            parameter,
        )
        if [row["variant"] for row in rows] != decoded_levels:
            raise Core4ContractError("method level and solver variant differ")
        if any(row["power_scale"] != "1" for row in rows):
            raise Core4ContractError("method axis changed power_scale")
        if any(
            row["service_curve_identity"] != row["base_service_curve_identity"]
            for row in rows
        ):
            raise Core4ContractError("method axis changed service identity")
    else:
        raise Core4ContractError(f"unknown CORE-4 parameter axis: {parameter}")


def validate_core4_pairing(root: Path | str) -> ValidatedCore4Rows:
    root = Path(root)
    requests = _read_table(
        root, "sensitivity_requests.csv", SENSITIVITY_REQUEST_COLUMNS
    )
    generated = _read_table(root, "generated_tasksets.csv", GENERATED_COLUMNS)
    results = _read_table(root, "per_taskset_results.csv", TASKSET_RESULT_COLUMNS)
    tasks = _read_table(root, "per_task_results.csv", TASK_RESULT_COLUMNS)
    failures = _read_table(root, "failures.csv", FAILURE_COLUMNS)
    if not requests:
        raise Core4ContractError("CORE-4 sensitivity request plan is empty")
    if not generated:
        raise Core4ContractError("CORE-4 frozen taskset table is empty")
    run_config = root / "run_config.yaml"
    if run_config.is_file():
        configured = load_config(run_config, expected_core="CORE-4")
        axes = configured["sensitivity"]["axes"]
        configured_levels = {
            "initial_energy": list(axes["initial_energy"]["values"]),
            "service_curve": list(axes["service_curve"]["variants"]),
            "power_scale": list(axes["power_scale"]["values"]),
            "method": list(axes["method"]["variants"]),
        }
        expected_orders = {
            parameter: canonical_json(
                make_sweep(configured["experiment_id"], parameter, levels).level_encodings
            )
            for parameter, levels in configured_levels.items()
        }
        for row in requests:
            parameter = row.get("parameter_name", "")
            if parameter not in expected_orders:
                raise Core4ContractError("request parameter axis is absent from run_config")
            if row.get("experiment_id") != configured["experiment_id"]:
                raise Core4ContractError("request experiment_id differs from run_config")
            if row.get("numerical_mode") != configured["analysis"]["numerical_mode"]:
                raise Core4ContractError("request numerical_mode differs from run_config")
            if row.get("ordered_parameter_levels") != expected_orders[parameter]:
                raise Core4ContractError("request ordered levels differ from run_config")
    _unique(requests, ("analysis_id",), "sensitivity analysis_id")
    _unique(results, ("analysis_id",), "terminal analysis_id")
    _unique(tasks, ("analysis_id", "task_id"), "task result key")
    _unique(generated, ("taskset_id",), "generated taskset_id")

    request_by_id = {row["analysis_id"]: row for row in requests}
    result_by_id = {row["analysis_id"]: row for row in results}
    available_ids = {
        row["analysis_id"] for row in requests if row["availability"] == "AVAILABLE"
    }
    unavailable_ids = {
        row["analysis_id"] for row in requests if row["availability"] == "UNAVAILABLE"
    }
    if len(available_ids) + len(unavailable_ids) != len(requests):
        raise Core4ContractError("CORE-4 request availability is not frozen")
    result_ids = set(result_by_id)
    missing = available_ids - result_ids
    if missing:
        raise Core4ContractError(
            f"MISSING_AVAILABLE_TERMINAL: {sorted(missing)}"
        )
    unexpected_unavailable = unavailable_ids & result_ids
    if unexpected_unavailable:
        raise Core4ContractError(
            f"UNAVAILABLE_REQUEST_HAS_TERMINAL: {sorted(unexpected_unavailable)}"
        )
    extra = result_ids - available_ids
    if extra:
        raise Core4ContractError(f"EXTRA_TERMINAL: {sorted(extra)}")

    generated_by_id = {row["taskset_id"]: row for row in generated}
    payloads: Dict[str, list[Dict[str, Any]]] = {}
    for row in requests:
        if not row["experiment_id"] or not row["sweep_id"]:
            raise Core4ContractError("CORE-4 request identity is empty")
        level_index = _plain_int(row["level_index"], "level_index")
        levels = _json_value(
            row["ordered_parameter_levels"], "ordered_parameter_levels", list
        )
        if not levels or any(not isinstance(level, str) for level in levels):
            raise Core4ContractError("ordered_parameter_levels must contain encodings")
        if level_index >= len(levels) or row["level_encoding"] != levels[level_index]:
            raise Core4ContractError("level_index does not select level_encoding")
        decoded = [json.loads(level) for level in levels]
        sweep = make_sweep(row["experiment_id"], row["parameter_name"], decoded)
        if sweep.sweep_id != row["sweep_id"] or tuple(levels) != sweep.level_encodings:
            raise Core4ContractError("sweep_id or ordered levels cannot be recomputed")
        base, effective = _validate_effective_tasks(row)
        payloads[row["analysis_id"]] = effective
        if core4_analysis_input_hash(row) != row["analysis_input_hash"]:
            raise Core4ContractError("analysis_input_hash mismatch")
        frozen = generated_by_id.get(row["base_taskset_id"])
        if frozen is None:
            raise Core4ContractError("base_taskset_id is absent from generated_tasksets")
        expected_generated = {
            "taskset_hash": row["base_taskset_hash"],
            "taskset_index": row["taskset_index"],
            "M": row["M"],
            "task_n": row["task_n"],
            "priority_hash": row["base_priority_hash"],
            "power_hash": row["base_power_hash"],
            "service_curve_reference": row["base_service_curve_identity"],
            "task_input_json": row["base_task_input_json"],
        }
        for field, expected in expected_generated.items():
            if frozen.get(field, "") != expected:
                raise Core4ContractError(f"frozen taskset mismatch: {field}")
        if len(base) != len(effective):
            raise Core4ContractError("base/analysis task counts differ")

    groups: Dict[tuple[str, str, str], list[Mapping[str, str]]] = {}
    for row in requests:
        key_variant = "METHOD_PAIR" if row["parameter_name"] == "method" else row["variant"]
        groups.setdefault((row["sweep_id"], row["base_taskset_hash"], key_variant), []).append(row)
    for (sweep_id, base_hash, key_variant), members in groups.items():
        members.sort(key=lambda row: int(row["level_index"]))
        levels = _json_value(
            members[0]["ordered_parameter_levels"], "ordered_parameter_levels", list
        )
        if [int(row["level_index"]) for row in members] != list(range(len(levels))):
            raise Core4ContractError("sweep group does not contain each ordered level exactly once")
        pair_ids = []
        for left, right in zip(members, members[1:]):
            pair_variant = (
                f"{left['variant']}->{right['variant']}"
                if left["parameter_name"] == "method"
                else key_variant
            )
            pair_ids.append(
                paired_analysis_id(
                    sweep_id,
                    base_hash,
                    pair_variant,
                    left["level_encoding"],
                    right["level_encoding"],
                )
            )
        encoded_pairs = canonical_json(pair_ids)
        if any(row["paired_analysis_ids"] != encoded_pairs for row in members):
            raise Core4ContractError("paired_analysis_ids cannot be recomputed")
        _validate_axis_group(members, levels)

    for analysis_id_value, result in result_by_id.items():
        request = request_by_id[analysis_id_value]
        if terminal_status_class(
            result.get("solver_status"), outer_timeout=result.get("outer_timeout")
        ) == "DEPENDENCY_UNAVAILABLE":
            raise Core4ContractError(
                "AVAILABLE request cannot have a DEPENDENCY_UNAVAILABLE terminal"
            )
        expected = {
            "taskset_id": request["base_taskset_id"],
            "taskset_hash": request["base_taskset_hash"],
            "M": request["M"],
            "task_n": request["task_n"],
            "exact_e0": request["exact_e0"],
            "analysis_variant": request["variant"],
        }
        for field, value in expected.items():
            if result.get(field, "") != value:
                raise Core4ContractError(f"terminal/request mismatch: {field}")

    for task in tasks:
        analysis_id_value = task["analysis_id"]
        if analysis_id_value not in result_by_id:
            raise Core4ContractError("task row has no terminal result")
        expected_tasks = {
            str(row["task_id"]): row for row in payloads[analysis_id_value]
        }
        expected = expected_tasks.get(task["task_id"])
        if expected is None:
            raise Core4ContractError("task row has an unknown task_id")
        for field in ("priority_rank", "C", "D", "T", "P"):
            if task.get(field, "") != str(expected[field]):
                raise Core4ContractError(f"task/request mismatch: {field}")

    return ValidatedCore4Rows(
        tuple(requests), tuple(generated), tuple(results), tuple(tasks), tuple(failures)
    )


def _core4_closure_files(root: Path) -> Dict[str, Path]:
    files: Dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in CORE4_MANIFEST_EXCLUDED_PATHS:
            continue
        if path.is_symlink():
            raise Core4ContractError(
                f"CORE-4 artifact closure contains a symbolic link: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise Core4ContractError(
                f"CORE-4 artifact closure contains a non-regular file: {relative}"
            )
        files[relative] = path
    return files


def write_core4_hash_manifest(root: Path | str) -> None:
    """Atomically inventory every immutable regular file in a CORE-4 root."""

    root = Path(root)
    rows = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}"
        for relative, path in _core4_closure_files(root).items()
    ]
    atomic_write_text(root / "file_hashes.sha256", "\n".join(rows) + "\n")


def validate_core4_hash_manifest(
    root: Path | str, *, require_completed_files: bool
) -> None:
    """Require and validate the exact non-self-referential CORE-4 inventory."""

    root = Path(root)
    manifest = root / "file_hashes.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise Core4ContractError("required CORE-4 file_hashes.sha256 is missing")
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Core4ContractError("CORE-4 file_hashes.sha256 is unreadable") from exc
    if not lines:
        raise Core4ContractError("CORE-4 file_hashes.sha256 is empty")
    expected: Dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if match is None:
            raise Core4ContractError("file_hashes.sha256 has an invalid row")
        digest, relative = match.groups()
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.as_posix() != relative
            or relative in CORE4_MANIFEST_EXCLUDED_PATHS
        ):
            raise Core4ContractError("file_hashes.sha256 has an unsafe path")
        if relative in expected:
            raise Core4ContractError("file_hashes.sha256 has a duplicate path")
        target = root / relative_path
        if target.is_symlink() or not target.is_file():
            raise Core4ContractError(
                f"file_hashes.sha256 declares a missing or non-regular file: {relative}"
            )
        expected[relative] = digest
    actual = _core4_closure_files(root)
    if require_completed_files and not CORE4_COMPLETED_REQUIRED_FILES.issubset(expected):
        missing = sorted(CORE4_COMPLETED_REQUIRED_FILES - set(expected))
        raise Core4ContractError(
            f"file_hashes.sha256 omits required completed files: {missing}"
        )
    if set(expected) != set(actual):
        raise Core4ContractError("file_hashes.sha256 file set mismatch")
    for relative, digest in expected.items():
        observed = hashlib.sha256(actual[relative].read_bytes()).hexdigest()
        if observed != digest:
            raise Core4ContractError(f"file hash mismatch: {relative}")


def _expected_count_fields(rows: ValidatedCore4Rows) -> Dict[str, int]:
    planned = len(rows.requests)
    available = sum(row["availability"] == "AVAILABLE" for row in rows.requests)
    unavailable = planned - available
    technical_ids = {
        row["analysis_id"]
        for row in rows.results
        if terminal_status_class(
            row.get("solver_status"), outer_timeout=row.get("outer_timeout")
        )
        == "TECHNICAL_FAILURE"
    }
    technical_ids.update(
        row["analysis_id"]
        for row in rows.failures
        if row.get("severity") == "P0" and row.get("analysis_id")
    )
    return {
        "planned_sensitivity_row_count": planned,
        "available_solver_request_count": available,
        "expected_terminal_count": available,
        "actual_terminal_count": len(rows.results),
        "dependency_unavailable_row_count": unavailable,
        "technical_failure_count": len(technical_ids),
    }


def configured_core4_counts(config: Mapping[str, Any]) -> Dict[str, int]:
    base_cells = {
        (cell.processors, cell.task_count, fraction_text(cell.utilization))
        for cell in expand_cells(config)
    }
    base_tasksets = len(base_cells) * int(config["grid"]["tasksets_per_cell"])
    planned_per_base = 0
    available_per_base = 0
    for parameter, spec in config["sensitivity"]["axes"].items():
        levels = spec["variants"] if parameter in {"service_curve", "method"} else spec["values"]
        for level in levels:
            variants = 1 if parameter == "method" else len(config["analysis"]["variants"])
            planned_per_base += variants
            availability = level.get("availability", "AVAILABLE") if isinstance(level, dict) else "AVAILABLE"
            if availability == "AVAILABLE":
                available_per_base += variants
    planned = planned_per_base * base_tasksets
    available = available_per_base * base_tasksets
    return {
        "planned_sensitivity_row_count": planned,
        "available_solver_request_count": available,
        "expected_terminal_count": available,
        "dependency_unavailable_row_count": planned - available,
    }


def _validate_run_mode_metadata(
    metadata: Mapping[str, Any], config: Mapping[str, Any]
) -> None:
    expected_formal = (
        config["sensitivity"].get("profile")
        == "formal-sustainability-v1"
    )
    if metadata.get("formal_large_scale_run") is not expected_formal:
        raise Core4ContractError(
            "CORE-4 formal_large_scale_run metadata/profile mismatch"
        )
    if metadata.get("finite_sample_consistency_check_only") is not True:
        raise Core4ContractError(
            "CORE-4 metadata must retain finite-sample-only semantics"
        )


def validate_core4_artifact_contract(
    root: Path | str, *, require_completed: bool = True
) -> ValidatedCore4Rows:
    root = Path(root)
    metadata = _load_json(root / "run_metadata.json", "run metadata")
    checkpoint = _load_json(root / "checkpoint.json", "checkpoint")
    if metadata.get("schema") != CORE4_RUN_SCHEMA:
        raise Core4ContractError("CORE-4 run metadata schema mismatch")
    if checkpoint.get("schema") != CORE4_CHECKPOINT_SCHEMA:
        raise Core4ContractError("CORE-4 checkpoint schema mismatch")
    if metadata.get("core") != "CORE-4" or checkpoint.get("core") != "CORE-4":
        raise Core4ContractError("CORE-4 artifact core mismatch")
    if not isinstance(metadata.get("experiment_id"), str) or not metadata["experiment_id"]:
        raise Core4ContractError("CORE-4 metadata experiment_id is invalid")
    if checkpoint.get("config_hash") != metadata.get("config_hash"):
        raise Core4ContractError("CORE-4 metadata/checkpoint config hash mismatch")
    run_config = root / "run_config.yaml"
    if not run_config.is_file():
        raise Core4ContractError("CORE-4 run_config.yaml is missing")
    loaded = load_config(run_config, expected_core="CORE-4")
    if config_hash(loaded) != metadata.get("config_hash"):
        raise Core4ContractError("CORE-4 persisted config hash mismatch")
    _validate_run_mode_metadata(metadata, loaded)
    configured_counts = configured_core4_counts(loaded)
    for field, value in configured_counts.items():
        if metadata.get(field) != value:
            raise Core4ContractError(f"CORE-4 metadata/config count mismatch: {field}")
    phase = checkpoint.get("phase")
    if phase not in {"INITIALIZED", "FINALIZING", "COMPLETED", "STOPPED"}:
        raise Core4ContractError("CORE-4 checkpoint phase is invalid")
    stop_requested = checkpoint.get("stop_requested")
    if not isinstance(stop_requested, bool):
        raise Core4ContractError("CORE-4 checkpoint stop_requested is not boolean")
    if (phase == "COMPLETED" and stop_requested) or (
        phase == "STOPPED" and not stop_requested
    ):
        raise Core4ContractError("CORE-4 checkpoint phase/stop state mismatch")
    if require_completed and (
        phase != "COMPLETED" or stop_requested
    ):
        raise Core4ContractError("CORE-4 analyzer requires a completed non-stopped run")
    rows = validate_core4_pairing(root)
    if any(
        row["experiment_id"] != metadata["experiment_id"] for row in rows.requests
    ):
        raise Core4ContractError("sensitivity request experiment_id mismatch")
    analysis_requests = _read_table(root, "analysis_requests.csv", REQUEST_COLUMNS)
    attempts = _read_table(root, "analysis_attempts.csv", ATTEMPT_COLUMNS)
    _unique(analysis_requests, ("analysis_id",), "solver request analysis_id")
    _unique(attempts, ("attempt_id",), "analysis attempt_id")
    available_ids = {
        row["analysis_id"]
        for row in rows.requests
        if row["availability"] == "AVAILABLE"
    }
    actual_request_ids = {row["analysis_id"] for row in analysis_requests}
    if actual_request_ids != available_ids:
        raise Core4ContractError("solver request set does not equal available sensitivity set")
    sensitivity_by_id = {row["analysis_id"]: row for row in rows.requests}
    terminal_by_id = {row["analysis_id"]: row for row in rows.results}
    for solver_request in analysis_requests:
        analysis_id_value = solver_request["analysis_id"]
        sensitivity = sensitivity_by_id[analysis_id_value]
        expected = {
            "taskset_id": sensitivity["base_taskset_id"],
            "taskset_hash": sensitivity["base_taskset_hash"],
            "exact_e0": sensitivity["exact_e0"],
            "variant": sensitivity["variant"],
            "numerical_mode": sensitivity["numerical_mode"],
            "request_status": "TERMINAL",
            "request_id": terminal_by_id[analysis_id_value]["request_id"],
        }
        for field, value in expected.items():
            if solver_request.get(field, "") != value:
                raise Core4ContractError(f"solver request/sensitivity mismatch: {field}")
    if any(row["analysis_id"] not in actual_request_ids for row in attempts):
        raise Core4ContractError("analysis attempt has no solver request")
    counts = _expected_count_fields(rows)
    for field, value in counts.items():
        if checkpoint.get(field) != value:
            raise Core4ContractError(f"CORE-4 checkpoint count mismatch: {field}")
        if metadata.get(field) is not None and field in {
            "planned_sensitivity_row_count",
            "available_solver_request_count",
            "expected_terminal_count",
            "dependency_unavailable_row_count",
        } and metadata.get(field) != value:
            raise Core4ContractError(f"CORE-4 metadata count mismatch: {field}")
    completed_ids = checkpoint.get("completed_analysis_ids")
    if (
        not isinstance(completed_ids, list)
        or any(not isinstance(value, str) or not value for value in completed_ids)
        or len(completed_ids) != len(set(completed_ids))
        or set(completed_ids) != {row["analysis_id"] for row in rows.results}
    ):
        raise Core4ContractError("CORE-4 checkpoint completed analysis set mismatch")
    if phase == "COMPLETED" and counts["technical_failure_count"]:
        raise Core4ContractError("completed CORE-4 run contains technical failures")
    if phase == "COMPLETED":
        validate_core4_hash_manifest(root, require_completed_files=True)
    elif phase == "STOPPED":
        validate_core4_hash_manifest(root, require_completed_files=False)
    return rows


def validate_core4_resume_envelope(
    root: Path | str,
    *,
    expected_config_hash: str,
    expected_experiment_id: str,
    expected_counts: Mapping[str, int],
) -> str:
    root = Path(root)
    metadata = _load_json(root / "run_metadata.json", "run metadata")
    checkpoint = _load_json(root / "checkpoint.json", "checkpoint")
    if metadata.get("schema") != CORE4_RUN_SCHEMA:
        raise Core4ContractError("CORE-4 run metadata schema mismatch")
    if checkpoint.get("schema") != CORE4_CHECKPOINT_SCHEMA:
        raise Core4ContractError("CORE-4 checkpoint schema mismatch")
    for document in (metadata, checkpoint):
        if document.get("core") != "CORE-4":
            raise Core4ContractError("CORE-4 resume core mismatch")
        if document.get("config_hash") != expected_config_hash:
            raise Core4ContractError("CORE-4 configuration hash mismatch")
    if metadata.get("experiment_id") != expected_experiment_id:
        raise Core4ContractError("CORE-4 resume experiment_id mismatch")
    run_config = root / "run_config.yaml"
    if not run_config.is_file():
        raise Core4ContractError("CORE-4 persisted run_config.yaml is missing")
    persisted_config = load_config(run_config, expected_core="CORE-4")
    if config_hash(persisted_config) != expected_config_hash:
        raise Core4ContractError("CORE-4 persisted configuration hash mismatch")
    _validate_run_mode_metadata(metadata, persisted_config)
    for field, value in expected_counts.items():
        if metadata.get(field) != value:
            raise Core4ContractError(f"CORE-4 resume metadata count mismatch: {field}")
        if checkpoint.get(field) != value:
            raise Core4ContractError(f"CORE-4 resume checkpoint count mismatch: {field}")
    phase = checkpoint.get("phase")
    if phase == "INITIALIZED":
        if (
            checkpoint.get("actual_terminal_count") != 0
            or checkpoint.get("technical_failure_count") != 0
            or checkpoint.get("completed_analysis_ids") != []
            or checkpoint.get("stop_requested") is not False
        ):
            raise Core4ContractError(
                "initialized CORE-4 checkpoint contains execution progress"
            )
        partial = [name for name in CORE4_PRIMARY_TABLES if (root / name).exists()]
        if partial:
            raise Core4ContractError(
                f"partial top-level CORE-4 artifacts in initialized run: {partial}"
            )
        return phase
    if phase == "STOPPED":
        validate_core4_hash_manifest(root, require_completed_files=False)
        raise Core4ContractError("refusing to resume a stopped CORE-4 run")
    if phase == "FINALIZING":
        if checkpoint.get("stop_requested") is not False:
            raise Core4ContractError("FINALIZING CORE-4 checkpoint requests stop")
        if (root / "file_hashes.sha256").exists():
            validate_core4_hash_manifest(root, require_completed_files=True)
        return phase
    if phase == "COMPLETED":
        validate_core4_artifact_contract(root)
        return phase
    raise Core4ContractError("CORE-4 checkpoint phase is invalid")
