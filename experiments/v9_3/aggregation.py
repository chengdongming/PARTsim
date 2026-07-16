"""Formal CORE-1/CORE-2 summaries with explicit statistical domains."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import pickle
from typing import Any, Dict, Iterable, Mapping, Sequence

import asap_block_rta_v9_3 as rta_core
import asap_block_rta_v9_3_taskset as taskset

from .cell_model import analysis_id as planned_analysis_id, expand_cells
from .config import canonical_json, config_hash, domain_hash, load_config
from .derived_outputs import (
    DerivedOutputBundle, build_core1_derived_outputs,
    build_core2_derived_outputs, validate_persisted_derived_bundle,
    variant_summary,
)
from .formal_authorization import (
    AUTHORIZATION_SCHEMA, FORMAL_CONFIRMATION_TOKEN, authorization_id,
    taskset_store_identity, verify_authorization,
)
from .result_writer import (
    CELL_COLUMNS, DEPENDENCY_COLUMNS, DOMINANCE_COLUMNS, TASK_RESULT_COLUMNS,
    atomic_write_json, read_csv, write_csv,
)
from .output_inventory import (
    OutputInventory, inventory_for_closure, validate_aggregation_inventory,
    validate_hash_manifest, validate_inventory_paths, write_inventory_hashes,
)
from .taskset_store import StoredTaskset
from .validation import (
    ConformanceFailure, validate_terminal_result_contract,
)


class AggregationError(RuntimeError):
    """Raised before any summary/plot consumes an incomplete run."""


@dataclass(frozen=True)
class ValidatedRunClosure:
    """Immutable handles to the exact row sets accepted by run closure."""

    metadata: Mapping[str, Any]
    cells: tuple[Mapping[str, str], ...]
    requests: tuple[Mapping[str, str], ...]
    attempts: tuple[Mapping[str, str], ...]
    tasksets: tuple[Mapping[str, str], ...]
    tasks: tuple[Mapping[str, str], ...]
    dependencies: tuple[Mapping[str, str], ...]
    dominance: tuple[Mapping[str, str], ...]
    failures: tuple[Mapping[str, str], ...]


def _unique_index(
    rows: Sequence[Mapping[str, str]], *keys: str, label: str
) -> Dict[Any, Mapping[str, str]]:
    result: Dict[Any, Mapping[str, str]] = {}
    for row in rows:
        values = tuple(row.get(name, "") for name in keys)
        key = values[0] if len(values) == 1 else values
        if key in result:
            raise AggregationError(f"duplicate {label}: {key}")
        result[key] = row
    return result


def _json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AggregationError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise AggregationError(f"invalid {label}")
    return value


def _csv_value(value: Any) -> str:
    return "" if value is None else str(value)


def _vector_hash(entries: Iterable[tuple[str, int]]) -> str:
    frozen = tuple(sorted((str(key), int(value)) for key, value in entries))
    return (
        domain_hash("ASAP_BLOCK:V9.3:CARRY_IN_VECTOR:v1", frozen)
        if frozen else ""
    )


def _stored_from_generated(row: Mapping[str, str]) -> StoredTaskset:
    payload = tuple(json.loads(row["task_input_json"]))
    tasks = tuple(
        rta_core.V93Task(
            str(item["task_id"]),
            int(item["C"]),
            int(item["D"]),
            int(item["T"]),
            Fraction(str(item["P"])),
        )
        for item in payload
    )
    return StoredTaskset(
        row["taskset_id"],
        row["generation_id"],
        int(row["taskset_index"]),
        int(row["generation_seed"]),
        row["taskset_hash"],
        row["priority_hash"],
        row["power_hash"],
        Fraction(row["target_total_utilization"]),
        Fraction(row["actual_total_utilization"]),
        int(row["M"]),
        int(row["task_n"]),
        row["deadline_mode"],
        tasks,
        payload,
        float(row["generation_seconds"]),
        row["service_curve_reference"],
        Path(row["canonical_taskset_json"]),
    )


def _expected_dependency_context(
    request: Mapping[str, str], generated: Mapping[str, str]
) -> taskset.DependencyContext:
    return taskset.DependencyContext(
        taskset_identity=request["taskset_hash"],
        task_definitions_identity=domain_hash(
            "ASAP_BLOCK:V9.3:TASK_DEFINITIONS:v1",
            json.loads(generated["task_input_json"]),
        ),
        priority_order_identity=generated["priority_hash"],
        e0_canonical_identity=domain_hash(
            "ASAP_BLOCK:V9.3:E0:v1", request["exact_e0"]
        ),
        service_curve_identity=generated["service_curve_reference"],
        power_vector_identity=generated["power_hash"],
        numerical_mode=request["numerical_mode"],
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=(
            taskset.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity=None,
    )


def validate_run_closure_read_only(
    root: Path | str, expected_core: str
) -> ValidatedRunClosure:
    """Validate full persisted closure without writing or executing analyses."""

    root = Path(root)
    if expected_core not in {"CORE-1", "CORE-2"}:
        raise AggregationError(f"unsupported closure core: {expected_core}")

    config = load_config(root / "run_config.yaml", expected_core=expected_core)
    metadata = _json(root / "run_metadata.json", "run metadata")
    seal = _json(root / "formal_authorization_seal.json", "formal authorization seal")
    checkpoint = _json(root / "checkpoint.json", "checkpoint")
    if metadata.get("core") != expected_core:
        raise AggregationError("run metadata core mismatch")
    if (
        metadata.get("formal_large_scale_run")
        != seal.get("formal_large_scale_run")
        or metadata.get("formal_authorization_id") != seal.get("authorization_id")
        or seal.get("binding", {}).get("config_semantic_hash") != config_hash(config)
        or seal.get("binding", {}).get("core") != expected_core
        or seal.get("binding", {}).get("output_root") != str(root.resolve())
    ):
        raise AggregationError("run metadata/authorization seal mismatch")
    if seal.get("formal_large_scale_run"):
        binding = seal.get("binding")
        authorization_path = Path(str(seal.get("authorization_file", "")))
        if (
            not isinstance(binding, dict)
            or not binding.get("repository_clean")
            or seal.get("authorization_id") != authorization_id(binding)
            or binding.get("taskset_store_identity")
            != taskset_store_identity(binding.get("taskset_store", ""))
            or not authorization_path.is_file()
            or hashlib.sha256(authorization_path.read_bytes()).hexdigest()
            != seal.get("authorization_file_sha256")
        ):
            raise AggregationError("formal authorization seal is not verifiable")
        authorization = _json(authorization_path, "formal authorization")
        if authorization != {
            "schema": AUTHORIZATION_SCHEMA,
            "formal_confirmation_token": FORMAL_CONFIRMATION_TOKEN,
            "authorization_id": seal["authorization_id"],
            "binding": binding,
        }:
            raise AggregationError("formal authorization document mismatch")
        try:
            current_seal = verify_authorization(
                config,
                authorization_path=authorization_path,
                source_freeze_config=Path(binding["source_freeze_config"]),
                prepared_config=Path(binding["prepared_config"]),
                project_root=Path(__file__).resolve().parents[2],
            )
        except Exception as exc:
            raise AggregationError(
                "formal authorization is no longer valid"
            ) from exc
        if canonical_json(current_seal) != canonical_json(seal):
            raise AggregationError("formal authorization changed before aggregation")
    elif any(
        seal.get(key) is not None
        for key in (
            "authorization_id", "authorization_file",
            "authorization_file_sha256",
        )
    ):
        raise AggregationError("nonformal seal claims formal authorization")
    if metadata.get("config_hash") != config_hash(config):
        raise AggregationError("run metadata/config hash mismatch")
    if checkpoint.get("config_hash") != config_hash(config):
        raise AggregationError("checkpoint/config hash mismatch")
    if checkpoint.get("stop_requested") is not False:
        raise AggregationError("stopped run cannot be aggregated")

    expected_variants = (
        ("CW_THETA_CW", "LOC_THETA_LOC")
        if expected_core == "CORE-1" else
        ("CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC")
    )
    expected_cells = expand_cells(config)
    expected_group_count = (
        len(expected_cells) * int(config["grid"]["tasksets_per_cell"])
    )
    expected_analysis_count = expected_group_count * len(expected_variants)

    requests = read_csv(root / "analysis_requests.csv")
    results = read_csv(root / "per_taskset_results.csv")
    tasks = read_csv(root / "per_task_results.csv")
    attempts = read_csv(root / "analysis_attempts.csv")
    generated = read_csv(root / "generated_tasksets.csv")
    cells = read_csv(root / "cells.csv")
    dependencies = read_csv(root / "dependency_records.csv")
    dominance = read_csv(root / "dominance_checks.csv")
    failures = read_csv(root / "failures.csv")
    if len(requests) != expected_analysis_count:
        raise AggregationError("partial run: request count does not match frozen config")
    if any(row.get("request_status") != "TERMINAL" for row in requests):
        raise AggregationError("non-terminal request cannot be aggregated")
    if any(row.get("severity") == "P0" for row in failures):
        raise AggregationError("P0 failure run cannot be aggregated")

    request_by_id = _unique_index(requests, "analysis_id", label="request")
    result_by_id = _unique_index(results, "analysis_id", label="result")
    if set(result_by_id) != set(request_by_id):
        raise AggregationError("result set does not equal request set")
    _unique_index(results, "cell_id", "taskset_id", "analysis_variant", label="result variant")
    generated_by_id = _unique_index(generated, "taskset_id", label="generated taskset")
    cell_by_id = _unique_index(cells, "cell_id", label="cell")
    expected_cell_by_id = {cell.cell_id: cell for cell in expected_cells}
    if set(cell_by_id) != set(expected_cell_by_id):
        raise AggregationError("cell set does not match frozen config")
    for cell_id_value, cell in expected_cell_by_id.items():
        expected_row = cell.row()
        observed_row = cell_by_id[cell_id_value]
        if any(
            observed_row[column] != _csv_value(expected_row[column])
            for column in CELL_COLUMNS
        ):
            raise AggregationError("cell row does not match frozen config")
    task_by_key = _unique_index(tasks, "analysis_id", "task_id", label="task result")
    _unique_index(attempts, "attempt_id", label="attempt")
    if any(row.get("analysis_id") not in request_by_id for row in attempts):
        raise AggregationError("attempt belongs to an extra analysis")

    pairing_path = root / "taskset_pairing_manifest.json"
    if metadata.get("pairing_manifest_id") is not None or seal.get(
        "formal_large_scale_run"
    ):
        pairing = _json(pairing_path, "taskset pairing manifest")
        if pairing.get("pairing_id") != metadata.get("pairing_manifest_id"):
            raise AggregationError("pairing manifest/run metadata mismatch")
        entries = pairing.get("entries")
        if not isinstance(entries, list):
            raise AggregationError("pairing manifest entries are invalid")
        entry_by_key = _unique_index(
            [
                {**entry, "taskset_index": str(entry.get("taskset_index", ""))}
                for entry in entries
            ],
            "generation_id", "taskset_index", label="pairing taskset"
        )
        generated_by_key = _unique_index(
            generated, "generation_id", "taskset_index", label="generated pairing taskset"
        )
        if set(entry_by_key) != set(generated_by_key):
            raise AggregationError("pairing manifest/generated taskset set mismatch")
        for key, generated_row in generated_by_key.items():
            entry = entry_by_key[key]
            expected = {
                "generation_id": generated_row["generation_id"],
                "taskset_index": generated_row["taskset_index"],
                "generation_seed": int(generated_row["generation_seed"]),
                "taskset_id": generated_row["taskset_id"],
                "taskset_semantic_hash": generated_row["taskset_hash"],
                "priority_hash": generated_row["priority_hash"],
                "power_hash": generated_row["power_hash"],
                "task_payload": json.loads(generated_row["task_input_json"]),
                "service_curve_identity": generated_row["service_curve_reference"],
            }
            if entry != expected:
                raise AggregationError("pairing manifest taskset payload mismatch")

    grouped_requests: Dict[tuple[str, str], Dict[str, Mapping[str, str]]] = {}
    for row in requests:
        key = (row["cell_id"], row["taskset_id"])
        members = grouped_requests.setdefault(key, {})
        if row["variant"] in members:
            raise AggregationError(f"duplicate request variant: {key + (row['variant'],)}")
        members[row["variant"]] = row
    if len(grouped_requests) != expected_group_count:
        raise AggregationError("partial run: taskset/cell group count mismatch")
    groups_per_cell = Counter(cell_id_value for cell_id_value, _ in grouped_requests)
    if any(
        groups_per_cell[cell.cell_id]
        != int(config["grid"]["tasksets_per_cell"])
        for cell in expected_cells
    ):
        raise AggregationError("partial run: per-cell taskset count mismatch")
    for key, members in grouped_requests.items():
        if set(members) != set(expected_variants):
            raise AggregationError(f"variant closure mismatch for {key}")

    for analysis_id_value, request in request_by_id.items():
        result = result_by_id[analysis_id_value]
        for request_key, result_key in (
            ("cell_id", "cell_id"),
            ("taskset_id", "taskset_id"),
            ("taskset_hash", "taskset_hash"),
            ("exact_e0", "exact_e0"),
            ("variant", "analysis_variant"),
        ):
            if request[request_key] != result[result_key]:
                raise AggregationError(
                    f"request/result mismatch for {analysis_id_value}: {request_key}"
                )
        if request["cell_id"] not in cell_by_id:
            raise AggregationError("request refers to missing cell")
        cell = cell_by_id[request["cell_id"]]
        if (
            cell["exact_e0"] != request["exact_e0"]
            or cell["numerical_mode"] != request["numerical_mode"]
        ):
            raise AggregationError("request/cell E0 or numerical mode mismatch")
        generated_row = generated_by_id.get(request["taskset_id"])
        if generated_row is None or generated_row["taskset_hash"] != request["taskset_hash"]:
            raise AggregationError("request/generated taskset mismatch")
        if (
            generated_row["generation_id"] != cell["generation_id"]
            or generated_row["M"] != cell["M"]
            or generated_row["task_n"] != cell["task_n"]
            or Fraction(generated_row["target_total_utilization"])
            != Fraction(cell["utilization"]) * int(cell["M"])
            or generated_row["deadline_mode"] != cell["deadline_mode"]
        ):
            raise AggregationError("request cell/taskset generation mismatch")
        cell_object = expected_cell_by_id[request["cell_id"]]
        expected_analysis_id = planned_analysis_id(
            cell_object, request["taskset_hash"], request["variant"]
        )
        expected_request_id = domain_hash(
            "ASAP_BLOCK:V9.3:ANALYSIS_REQUEST:v1", expected_analysis_id
        )
        if (
            analysis_id_value != expected_analysis_id
            or request["request_id"] != expected_request_id
            or result["request_id"] != expected_request_id
            or request["timeout_seconds"]
            != _csv_value(config["analysis"]["timeout_seconds"])
            or request["retry_timeout_seconds"]
            != _csv_value(config["analysis"].get("retry_timeout_seconds"))
        ):
            raise AggregationError("request identity does not match frozen plan")
        if request["variant"] != "LOC_THETA_CW":
            if request["source_analysis_id"] or result["source_analysis_id"]:
                raise AggregationError("non-dependency variant carried a source")
            if result["source_vector_hash"] or result["target_carry_in_vector_hash"]:
                raise AggregationError("non-dependency variant carried a source vector")
            if result["dependency_check_status"] != "NOT_CHECKED":
                raise AggregationError("non-dependency variant was dependency-checked")
        else:
            source = grouped_requests[(request["cell_id"], request["taskset_id"])]["CW_THETA_CW"]
            if (
                not request["source_analysis_id"]
                or request["source_analysis_id"] != source["analysis_id"]
                or result["source_analysis_id"] != source["analysis_id"]
            ):
                raise AggregationError("LOC_THETA_CW source ID mismatch")
            if result["dependency_check_status"] not in {"VALID", "INVALID"}:
                raise AggregationError("LOC_THETA_CW dependency status mismatch")
            if result["source_vector_hash"] != result["target_carry_in_vector_hash"]:
                raise AggregationError("LOC_THETA_CW source/target vector mismatch")
            if result["dependency_check_status"] == "VALID" and not result["source_vector_hash"]:
                raise AggregationError("VALID LOC_THETA_CW has no source vector")
            if result["dependency_check_status"] == "INVALID" and (
                result["solver_status"] != "NOT_APPLICABLE_DEPENDENCY"
                or result["certification_status"] != "NOT_APPLICABLE"
            ):
                raise AggregationError("INVALID LOC_THETA_CW is not dependency N/A")

        members = [row for row in tasks if row["analysis_id"] == analysis_id_value]
        if result["terminal_origin"] == "PRODUCTION_ANALYZER":
            if len(members) != int(result["n_tasks_total"]):
                raise AggregationError("task result closure mismatch")
        elif members:
            raise AggregationError("outer-worker result unexpectedly has task rows")
        for member in members:
            if (
                member["cell_id"] != result["cell_id"]
                or member["taskset_id"] != result["taskset_id"]
                or member["exact_e0"] != result["exact_e0"]
                or member["analysis_variant"] != result["analysis_variant"]
            ):
                raise AggregationError("task/result identity mismatch")

    completed_ids = set(checkpoint.get("completed_analysis_ids", []))
    if (
        completed_ids != set(request_by_id)
        or checkpoint.get("completed_count") != len(request_by_id)
        or checkpoint.get("requested_count") != len(request_by_id)
    ):
        raise AggregationError("checkpoint/request closure mismatch")

    terminal_paths = sorted((root / "terminal_results").glob("*.json"))
    terminal_by_id: Dict[str, Mapping[str, Any]] = {}
    for path in terminal_paths:
        payload = _json(path, "terminal result")
        if set(payload) != {"taskset_row", "task_rows"}:
            raise AggregationError("terminal result shape mismatch")
        terminal_row = payload["taskset_row"]
        if not isinstance(terminal_row, dict) or path.stem != terminal_row.get("analysis_id"):
            raise AggregationError("terminal filename/analysis ID mismatch")
        if path.stem in terminal_by_id:
            raise AggregationError("duplicate terminal analysis ID")
        terminal_by_id[path.stem] = payload
    if set(terminal_by_id) != set(request_by_id):
        raise AggregationError("terminal set does not equal request set")
    for analysis_id_value, payload in terminal_by_id.items():
        terminal_tasks = payload["task_rows"]
        if not isinstance(terminal_tasks, list):
            raise AggregationError("terminal task rows are invalid")
        terminal_task_by_id: Dict[str, Mapping[str, Any]] = {}
        for terminal_task in terminal_tasks:
            task_id_value = _csv_value(terminal_task.get("task_id"))
            if task_id_value in terminal_task_by_id:
                raise AggregationError("duplicate terminal task row")
            terminal_task_by_id[task_id_value] = terminal_task
        persisted_task_ids = {
            row["task_id"] for row in tasks
            if row["analysis_id"] == analysis_id_value
        }
        if set(terminal_task_by_id) != persisted_task_ids:
            raise AggregationError("terminal/CSV task set mismatch")
        for task_id_value, terminal_task in terminal_task_by_id.items():
            key = (analysis_id_value, task_id_value)
            persisted = task_by_key.get(key)
            if persisted is None:
                raise AggregationError("terminal task row is missing from CSV")
            for column in TASK_RESULT_COLUMNS:
                if persisted[column] != _csv_value(terminal_task.get(column)):
                    raise AggregationError(f"terminal task/CSV mismatch: {column}")

    expected_state_ids = {
        analysis_id_value for analysis_id_value, row in result_by_id.items()
        if row["terminal_origin"] == "PRODUCTION_ANALYZER"
    }
    state_paths = sorted((root / "result_state").glob("*.pickle"))
    if {path.stem for path in state_paths} != expected_state_ids:
        raise AggregationError("result-state set does not match analyzer terminals")
    states_by_id: Dict[str, taskset.TasksetAnalysisResult] = {}
    for path in state_paths:
        try:
            with path.open("rb") as handle:
                state = pickle.load(handle)
        except Exception as exc:
            raise AggregationError("unreadable result state") from exc
        request = request_by_id[path.stem]
        if not isinstance(state, taskset.TasksetAnalysisResult) or state.analysis_id != path.stem:
            raise AggregationError("invalid result state identity")
        states_by_id[path.stem] = state
        generated_row = generated_by_id[request["taskset_id"]]
        context = state.dependency_context
        expected_definitions = domain_hash(
            "ASAP_BLOCK:V9.3:TASK_DEFINITIONS:v1",
            json.loads(generated_row["task_input_json"]),
        )
        expected_e0 = domain_hash("ASAP_BLOCK:V9.3:E0:v1", request["exact_e0"])
        if (
            context.taskset_identity != request["taskset_hash"]
            or context.task_definitions_identity != expected_definitions
            or context.priority_order_identity != generated_row["priority_hash"]
            or context.power_vector_identity != generated_row["power_hash"]
            or context.service_curve_identity != generated_row["service_curve_reference"]
            or context.e0_canonical_identity != expected_e0
            or context.numerical_mode != request["numerical_mode"]
        ):
            raise AggregationError("result state dependency context mismatch")
        for record in state.task_records:
            persisted = task_by_key.get((path.stem, record.task_id))
            if persisted is None:
                raise AggregationError("result state task is missing from CSV")
            record_fields = {
                "priority_rank": str(record.priority_rank),
                "task_solver_status": record.solver_status.value,
                "task_certification_status": record.certification_status.value,
                "candidate_response_time": _csv_value(
                    record.candidate_response_time
                ),
                "closing_w": _csv_value(record.closing_w),
                "witness_h": _csv_value(record.witness_h),
                "checked_w_count": str(record.checked_w_count),
                "checked_h_count": str(record.checked_h_count),
                "checked_q_count": str(record.checked_q_count),
                "envelope_call_count": str(record.envelope_call_count),
                "failure_reason": record.failure_reason or "",
                "carry_in_vector_hash": _vector_hash(
                    record.carry_in_values_used
                ),
            }
            for field, value in record_fields.items():
                if persisted[field] != value:
                    raise AggregationError(f"result state task/CSV mismatch: {field}")

    stored_by_id = {
        taskset_id_value: _stored_from_generated(row)
        for taskset_id_value, row in generated_by_id.items()
    }
    for analysis_id_value, request in request_by_id.items():
        generated_row = generated_by_id[request["taskset_id"]]
        state = states_by_id.get(analysis_id_value)
        source = (
            states_by_id.get(request["source_analysis_id"])
            if request["variant"] == "LOC_THETA_CW"
            else None
        )
        analysis_attempts = sorted(
            (
                row for row in attempts
                if row["analysis_id"] == analysis_id_value
            ),
            key=lambda row: (int(row["attempt_number"]), row["attempt_id"]),
        )
        try:
            cell = cell_by_id[request["cell_id"]]
            expected_identity = {
                "analysis_id": analysis_id_value,
                "request_id": request["request_id"],
                "cell_id": request["cell_id"],
                "taskset_id": request["taskset_id"],
                "taskset_hash": request["taskset_hash"],
                "generation_seed": int(generated_row["generation_seed"]),
                "M": int(cell["M"]),
                "task_n": int(cell["task_n"]),
                "utilization": cell["utilization"],
                "exact_e0": request["exact_e0"],
                "deadline_mode": cell["deadline_mode"],
                "analysis_variant": request["variant"],
                "method_role": taskset.ROLE_BY_VARIANT[
                    taskset.AnalysisVariant[request["variant"]]
                ].value,
            }
            terminal_payload = terminal_by_id[analysis_id_value]
            validate_terminal_result_contract(
                analysis_attempts,
                expected_analysis_id=analysis_id_value,
                expected_variant=taskset.AnalysisVariant[request["variant"]],
                retry_policy=config["analysis"]["retry_policy"],
                initial_timeout_seconds=config["analysis"]["timeout_seconds"],
                retry_timeout_seconds=config["analysis"].get(
                    "retry_timeout_seconds"
                ),
                stored=stored_by_id[request["taskset_id"]],
                expected_context=_expected_dependency_context(
                    request, generated_row
                ),
                expected_source_analysis_id=(
                    request["source_analysis_id"] or None
                ),
                source=source,
                state=state,
                expected_identity=expected_identity,
                terminal_row=terminal_payload["taskset_row"],
                terminal_task_rows=terminal_payload["task_rows"],
                materialized_row=result_by_id[analysis_id_value],
            )
        except (ConformanceFailure, taskset.CertificationError) as exc:
            raise AggregationError(
                f"attempt artifact conformance mismatch: {exc}"
            ) from exc

    if expected_core == "CORE-1":
        if dependencies:
            raise AggregationError("CORE-1 unexpectedly has dependency records")
    else:
        dependency_by_id = _unique_index(dependencies, "analysis_id", label="dependency")
        target_ids = {
            row["analysis_id"] for row in requests
            if row["variant"] == "LOC_THETA_CW"
        }
        if set(dependency_by_id) != target_ids:
            raise AggregationError("dependency record set mismatch")
        for target_id, dependency in dependency_by_id.items():
            target = result_by_id[target_id]
            request = request_by_id[target_id]
            source_id = request["source_analysis_id"]
            source = result_by_id[source_id]
            source_request = request_by_id[source_id]
            source_state = states_by_id.get(source_id)
            target_state = states_by_id.get(target_id)
            source_certified = bool(
                source_state is not None
                and source_state.analysis_variant
                is taskset.AnalysisVariant.CW_THETA_CW
                and source_state.analysis_id == source_id
                and source_state.solver_status
                is taskset.AnalysisSolverStatus.COMPLETED
                and source_state.certification_status
                is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
                and source_state.taskset_proven
                and source_state.n_tasks_certified
                == source_state.n_tasks_total
                == len(source_state.task_records)
                and all(
                    record.solver_status
                    is taskset.TaskSolverStatus.CANDIDATE_FOUND
                    and record.certification_status
                    is taskset.TaskCertificationStatus.CERTIFIED
                    and record.candidate_response_time is not None
                    for record in source_state.task_records
                )
            )
            valid = target["dependency_check_status"] == "VALID"
            if valid:
                if not source_certified or target_state is None:
                    raise AggregationError(
                        "VALID dependency lacks a certified source/target state"
                    )
                source_vector = tuple(
                    (record.task_id, record.candidate_response_time)
                    for record in source_state.task_records
                )
                if (
                    target_state.source_candidate_vector
                    != tuple(sorted(source_vector))
                ):
                    raise AggregationError(
                        "dependency target vector does not equal real source"
                    )
                source_vector_hash = _vector_hash(source_vector)
                target_vector_hash = _vector_hash(
                    target_state.source_candidate_vector
                )
            else:
                if source_certified:
                    raise AggregationError(
                        "INVALID dependency has a certified source"
                    )
                if (
                    target_state is None
                    or target_state.solver_status
                    is not taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
                    or target_state.certification_status
                    is not taskset.AnalysisCertificationStatus.NOT_APPLICABLE
                    or target_state.source_candidate_vector
                ):
                    raise AggregationError(
                        "INVALID dependency target is not strict N/A"
                    )
                source_vector_hash = ""
                target_vector_hash = ""

            expected_dependency = {
                "analysis_id": target_id,
                "cell_id": request["cell_id"],
                "taskset_id": request["taskset_id"],
                "target_variant": "LOC_THETA_CW",
                "source_analysis_id": source_id,
                "source_variant": "CW_THETA_CW",
                "source_certified": str(source_certified),
                "dependency_check_status": target["dependency_check_status"],
                "source_taskset_hash": source_request["taskset_hash"],
                "target_taskset_hash": request["taskset_hash"],
                "source_e0": source_request["exact_e0"],
                "target_e0": request["exact_e0"],
                "source_numerical_mode": source_request["numerical_mode"],
                "target_numerical_mode": request["numerical_mode"],
                "source_vector_hash": source_vector_hash,
                "target_vector_hash": target_vector_hash,
                "applicable": str(valid),
                "fallback_used": "False",
            }
            if any(
                dependency.get(column, "") != expected_dependency[column]
                for column in DEPENDENCY_COLUMNS
            ):
                raise AggregationError("dependency provenance mismatch")

    relation_specs = (
        (("LOCAL_RECURSIVE_VS_COMPLETE_RECURSIVE", "CW_THETA_CW", "LOC_THETA_LOC"),)
        if expected_core == "CORE-1"
        else (
            ("LOCAL_VS_COMPLETE_DEADLINE", "CW_D", "LOC_D"),
            ("LOCAL_VS_COMPLETE_FIXED_CW", "CW_THETA_CW", "LOC_THETA_CW"),
            ("LOCAL_RECURSIVE_VS_COMPLETE_RECURSIVE", "CW_THETA_CW", "LOC_THETA_LOC"),
        )
    )
    expected_dominance: list[Dict[str, str]] = []
    for (cell_id_value, taskset_id_value), members in grouped_requests.items():
        exact_e0 = members[expected_variants[0]]["exact_e0"]
        for relation, left_variant, right_variant in relation_specs:
            left_id = members[left_variant]["analysis_id"]
            right_id = members[right_variant]["analysis_id"]
            left = {
                row["task_id"]: row for row in tasks
                if row["analysis_id"] == left_id
                and row["task_solver_status"] == "CANDIDATE_FOUND"
            }
            right = {
                row["task_id"]: row for row in tasks
                if row["analysis_id"] == right_id
                and row["task_solver_status"] == "CANDIDATE_FOUND"
            }
            common = sorted(set(left) & set(right), key=int)
            if not common:
                expected_dominance.append({
                    "cell_id": cell_id_value,
                    "taskset_id": taskset_id_value,
                    "exact_e0": exact_e0,
                    "relation": relation,
                    "left_variant": left_variant,
                    "right_variant": right_variant,
                    "task_id": "",
                    "priority_rank": "",
                    "left_candidate": "",
                    "right_candidate": "",
                    "reduction": "",
                    "status": "NOT_APPLICABLE",
                })
                continue
            for task_id_value in common:
                left_candidate = int(left[task_id_value]["candidate_response_time"])
                right_candidate = int(right[task_id_value]["candidate_response_time"])
                reduction = left_candidate - right_candidate
                expected_dominance.append({
                    "cell_id": cell_id_value,
                    "taskset_id": taskset_id_value,
                    "exact_e0": exact_e0,
                    "relation": relation,
                    "left_variant": left_variant,
                    "right_variant": right_variant,
                    "task_id": task_id_value,
                    "priority_rank": left[task_id_value]["priority_rank"],
                    "left_candidate": str(left_candidate),
                    "right_candidate": str(right_candidate),
                    "reduction": str(reduction),
                    "status": (
                        "TIGHTER" if reduction > 0
                        else "EQUAL" if reduction == 0
                        else "VIOLATION"
                    ),
                })
    dominance_by_key = _unique_index(
        dominance, "cell_id", "taskset_id", "relation", "task_id",
        label="dominance check",
    )
    expected_dominance_by_key = _unique_index(
        expected_dominance,
        "cell_id", "taskset_id", "relation", "task_id",
        label="expected dominance check",
    )
    if dominance_by_key != expected_dominance_by_key:
        raise AggregationError("dominance check closure mismatch")
    if any(row["status"] == "VIOLATION" for row in dominance):
        raise AggregationError("P0 dominance violation cannot be aggregated")
    return ValidatedRunClosure(
        metadata=metadata,
        cells=tuple(cells),
        requests=tuple(requests),
        attempts=tuple(attempts),
        tasksets=tuple(results),
        tasks=tuple(tasks),
        dependencies=tuple(dependencies),
        dominance=tuple(dominance),
        failures=tuple(failures),
    )


def _write_derived_bundle(
    root: Path,
    expected_core: str,
    bundle: DerivedOutputBundle,
    inventory: OutputInventory,
) -> Dict[str, Any]:
    """Materialize only the canonical artifacts supplied by one pure builder."""

    for table in bundle.tables:
        write_csv(root / table.filename, table.columns, table.rows)
    summary = dict(bundle.summary)
    atomic_write_json(root / "summary.json", summary)
    validate_persisted_derived_bundle(root, expected_core, bundle)
    write_inventory_hashes(root, inventory)
    actual = validate_inventory_paths(root, inventory)
    validate_hash_manifest(root, inventory, actual)
    return summary


def _aggregate(root: Path, expected_core: str, builder: Any) -> Dict[str, Any]:
    closure = validate_run_closure_read_only(root, expected_core)
    bundle = builder(closure)
    inventory = inventory_for_closure(
        expected_core, closure, bundle.required_paths
    )
    state = validate_aggregation_inventory(root, inventory)
    if state == "complete":
        validate_persisted_derived_bundle(root, expected_core, bundle)
        actual = validate_inventory_paths(root, inventory)
        validate_hash_manifest(root, inventory, actual)
    return _write_derived_bundle(root, expected_core, bundle, inventory)


def aggregate_core1(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    return _aggregate(root, "CORE-1", build_core1_derived_outputs)


def aggregate_core2(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    return _aggregate(root, "CORE-2", build_core2_derived_outputs)
