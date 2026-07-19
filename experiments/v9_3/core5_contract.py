"""Fail-closed identity, pairing, resource, and artifact contract for CORE-5."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import canonical_json, config_hash, load_config
from .core5_terminal import Core5TerminalClass, classify_core5_terminal, truth
from .resource_measurement import RESOURCE_OBSERVATION_COLUMNS, RESOURCE_USAGE_COLUMNS
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


CORE5_RUN_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_RUN_V2"
CORE5_CHECKPOINT_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_CHECKPOINT_V2"

SCALABILITY_CELL_COLUMNS = (
    "scalability_cell_id", "scaling_axis", "level_index", "level_id",
    "level_value", "M", "task_n", "period_min", "period_max",
    "utilization", "worker_count", "variants", "tasksets_requested",
    "analysis_ids_json", "cell_wall_seconds", "terminal_analysis_count",
    "throughput_analyses_per_second",
)

CHILD_OUTCOME_COLUMNS = (
    "scalability_cell_id", "requested_count", "terminal_count", "stopped",
    "status_counts_json", "request_set_status", "terminal_status",
    "resource_status", "p0_failure_count", "contract_status",
)

RUNTIME_COLUMNS = (
    "scalability_cell_id", "scaling_axis", "level_id", "worker_count",
    "analysis_id", "analysis_variant", "terminal_class", "event_observed",
    "censoring_status", "observed_time_seconds", "timeout_budget_seconds",
    "right_censored_lower_bound_seconds",
)

SUMMARY_COLUMNS = (
    "scaling_axis", "level_id", "level_value", "worker_count", "variant",
    "planned_analysis_count", "terminal_analysis_count",
    "runtime_evaluable_count", "completed_count", "completed_mean_seconds",
    "completed_median_seconds", "completed_p95_seconds", "completed_max_seconds",
    "timeout_count", "technical_failure_count", "censored_count",
    "timeout_rate_evaluable_denominator", "timeout_rate",
    "timeout_budget_seconds", "restricted_mean_runtime_seconds",
    "restriction_tau_seconds", "final_attempt_resource_count",
    "peak_rss_available_observation_count", "peak_rss_observation_status",
    "peak_rss_final_attempt_mean_kib", "peak_rss_final_attempt_max_kib",
    "search_counter_available_analysis_count", "search_counter_observation_status",
    "checked_w_final_attempt_total", "checked_h_final_attempt_total",
    "checked_q_final_attempt_total", "envelope_call_final_attempt_total",
    "candidate_found_task_final_attempt_total", "cell_wall_seconds",
    "throughput_analyses_per_second",
)

WORKER_CHECK_COLUMNS = (
    "taskset_index", "analysis_variant", "left_worker_count",
    "right_worker_count", "left_analysis_id", "right_analysis_id", "status",
    "detail",
)

CORE5_RAW_TABLES = {
    "scalability_cells.csv": SCALABILITY_CELL_COLUMNS,
    "child_outcomes.csv": CHILD_OUTCOME_COLUMNS,
    "generated_tasksets.csv": GENERATED_COLUMNS,
    "analysis_requests.csv": REQUEST_COLUMNS,
    "analysis_attempts.csv": ATTEMPT_COLUMNS,
    "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
    "per_task_results.csv": TASK_RESULT_COLUMNS,
    "attempt_resource_observations.csv": RESOURCE_OBSERVATION_COLUMNS,
    "failures.csv": FAILURE_COLUMNS,
}

CORE5_DERIVED_TABLES = {
    "resource_usage.csv": RESOURCE_USAGE_COLUMNS,
    "runtime_censoring.csv": RUNTIME_COLUMNS,
    "scalability_summary.csv": SUMMARY_COLUMNS,
    "worker_semantic_checks.csv": WORKER_CHECK_COLUMNS,
    "core5_plot_data.csv": (
        "plot", "scaling_axis", "level_id", "level_value", "worker_count",
        "variant", "metric", "value",
    ),
}

CORE5_MANIFEST_EXCLUDED_PATHS = frozenset({"checkpoint.json", "file_hashes.sha256"})
CORE5_COMPLETED_REQUIRED_FILES = frozenset({
    "run_metadata.json", "run_config.yaml", *CORE5_RAW_TABLES,
    *CORE5_DERIVED_TABLES, "scalability_summary.json",
})


class Core5ContractError(RuntimeError):
    """Raised when CORE-5 evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class ValidatedCore5Rows:
    cells: tuple[Mapping[str, str], ...]
    generated: tuple[Mapping[str, str], ...]
    requests: tuple[Mapping[str, str], ...]
    attempts: tuple[Mapping[str, str], ...]
    results: tuple[Mapping[str, str], ...]
    tasks: tuple[Mapping[str, str], ...]
    observations: tuple[Mapping[str, str], ...]
    failures: tuple[Mapping[str, str], ...]


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Core5ContractError(f"missing CORE-5 {label}: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Core5ContractError(f"unreadable CORE-5 {label}: {path.name}") from exc
    if not isinstance(value, dict):
        raise Core5ContractError(f"CORE-5 {label} must be a JSON object")
    return value


def _plain_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise Core5ContractError(f"{label} is not a plain integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise Core5ContractError(f"{label} is not a plain integer") from exc
    if str(parsed) != str(value) or parsed < minimum:
        raise Core5ContractError(f"{label} is not a canonical integer")
    return parsed


def _json_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, str) or not value:
        raise Core5ContractError(f"{label} must be non-empty canonical JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise Core5ContractError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, list) or canonical_json(parsed) != value:
        raise Core5ContractError(f"{label} is not a canonical JSON list")
    return parsed


def _unique(rows: Sequence[Mapping[str, str]], fields: Sequence[str], label: str) -> None:
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in fields)
        if any(not value for value in key):
            raise Core5ContractError(f"{label} has an empty identity field: {fields}")
        if key in seen:
            raise Core5ContractError(f"duplicate {label}: {key}")
        seen.add(key)


def _read_table(root: Path, name: str, columns: Sequence[str]) -> list[Dict[str, str]]:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise Core5ContractError(f"missing required CORE-5 table: {name}")
    try:
        validate_csv_header(path, columns)
    except ResultWriterError as exc:
        raise Core5ContractError(str(exc)) from exc
    return read_csv(path)


def configured_core5_counts(config: Mapping[str, Any]) -> Dict[str, int]:
    scale = config["scalability"]
    cell_count = (
        len(scale["task_counts"]) + len(scale["core_counts"])
        + len(scale["period_ranges"]) + len(scale["worker_counts"])
        + (len(scale["utilization_points"]) if len(scale["utilization_points"]) > 1 else 0)
    )
    planned = cell_count * int(config["grid"]["tasksets_per_cell"]) * len(config["analysis"]["variants"])
    return {
        "planned_scalability_cell_count": cell_count,
        "planned_analysis_count": planned,
        "hard_analysis_limit": int(scale["max_analyses"]),
    }


def validate_core5_raw_tables(
    root: Path | str, *, require_complete: bool,
) -> ValidatedCore5Rows:
    root = Path(root)
    tables = {name: _read_table(root, name, columns) for name, columns in CORE5_RAW_TABLES.items()}
    cells = tables["scalability_cells.csv"]
    generated = tables["generated_tasksets.csv"]
    requests = tables["analysis_requests.csv"]
    attempts = tables["analysis_attempts.csv"]
    results = tables["per_taskset_results.csv"]
    tasks = tables["per_task_results.csv"]
    observations = tables["attempt_resource_observations.csv"]
    failures = tables["failures.csv"]

    _unique(cells, ("scalability_cell_id",), "scalability cell")
    _unique(tables["child_outcomes.csv"], ("scalability_cell_id",), "child outcome")
    _unique(generated, ("taskset_id",), "generated taskset")
    _unique(requests, ("analysis_id",), "analysis request analysis_id")
    _unique(requests, ("request_id",), "analysis request request_id")
    _unique(attempts, ("attempt_id",), "analysis attempt")
    _unique(results, ("analysis_id",), "terminal analysis result")
    _unique(tasks, ("analysis_id", "task_id"), "task result")
    _unique(observations, ("attempt_id",), "resource observation")
    _unique(failures, ("analysis_id", "code"), "failure witness")

    cell_analysis_ids: list[str] = []
    for cell in cells:
        ids = _json_list(cell["analysis_ids_json"], "scalability cell analysis_ids_json")
        if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
            raise Core5ContractError("scalability cell has duplicate or empty analysis IDs")
        if _plain_int(cell["terminal_analysis_count"], "terminal_analysis_count") > len(ids):
            raise Core5ContractError("cell terminal count exceeds its analysis set")
        cell_analysis_ids.extend(ids)
    if len(cell_analysis_ids) != len(set(cell_analysis_ids)):
        raise Core5ContractError("analysis ID is assigned to multiple scalability cells")

    request_ids = {row["analysis_id"] for row in requests}
    terminal_ids = {row["analysis_id"] for row in results}
    if set(cell_analysis_ids) != request_ids:
        raise Core5ContractError("scalability cell/request analysis set mismatch")
    if require_complete and request_ids != terminal_ids:
        raise Core5ContractError("CORE-5 request/terminal set mismatch")
    if not terminal_ids.issubset(request_ids):
        raise Core5ContractError("CORE-5 has an extra terminal")

    generated_by_id = {row["taskset_id"]: row for row in generated}
    request_by_id = {row["analysis_id"]: row for row in requests}
    attempt_by_id = {row["attempt_id"]: row for row in attempts}
    result_by_id = {row["analysis_id"]: row for row in results}
    for request in requests:
        generated_row = generated_by_id.get(request["taskset_id"])
        if generated_row is None:
            raise Core5ContractError("analysis request references a missing generated taskset")
        if request["taskset_hash"] != generated_row["taskset_hash"]:
            raise Core5ContractError("analysis request/generated taskset hash mismatch")
        if request["request_status"] not in {"PLANNED", "TERMINAL"}:
            raise Core5ContractError("analysis request status is invalid")
        if require_complete and request["request_status"] != "TERMINAL":
            raise Core5ContractError("completed CORE-5 request is not terminal")
    for attempt in attempts:
        if attempt["analysis_id"] not in request_by_id:
            raise Core5ContractError("analysis attempt has no request")
    for result in results:
        request = request_by_id[result["analysis_id"]]
        if result["request_id"] != request["request_id"]:
            raise Core5ContractError("terminal/request request_id mismatch")
        for field, request_field in (
            ("taskset_id", "taskset_id"), ("taskset_hash", "taskset_hash"),
            ("exact_e0", "exact_e0"), ("analysis_variant", "variant"),
        ):
            if result[field] != request[request_field]:
                raise Core5ContractError(f"terminal/request mismatch: {field}")
        final_attempt = attempt_by_id.get(result["final_attempt_id"])
        if final_attempt is None or final_attempt["analysis_id"] != result["analysis_id"]:
            raise Core5ContractError("terminal final_attempt_id is invalid")
        if final_attempt["solver_status"] != result["solver_status"]:
            raise Core5ContractError("terminal/final attempt status mismatch")
    for task in tasks:
        result = result_by_id.get(task["analysis_id"])
        if result is None or task["taskset_id"] != result["taskset_id"]:
            raise Core5ContractError("task result has no matching terminal")

    observed_by_attempt = {row["attempt_id"]: row for row in observations}
    for observation in observations:
        attempt = attempt_by_id.get(observation["attempt_id"])
        if attempt is None:
            raise Core5ContractError("resource observation references a missing attempt")
        if observation["analysis_id"] != attempt["analysis_id"]:
            raise Core5ContractError("resource observation/attempt analysis mismatch")
        status = observation["observation_status"]
        if status not in {"AVAILABLE", "EXPECTED_UNAVAILABLE", "TECHNICAL_UNAVAILABLE"}:
            raise Core5ContractError("resource observation status is invalid")
        if status == "AVAILABLE":
            try:
                if int(observation["peak_rss_kib"]) < 0:
                    raise ValueError
            except ValueError as exc:
                raise Core5ContractError("available peak RSS is not a nonnegative KiB integer") from exc
            if observation["peak_rss_scope"] != "CHILD_PROCESS" or observation["peak_rss_unit"] != "KiB":
                raise Core5ContractError("available peak RSS scope/unit mismatch")
            if observation["unavailability_reason"]:
                raise Core5ContractError("available resource observation has an unavailable reason")
        else:
            if observation["peak_rss_kib"] != "UNAVAILABLE" or not observation["unavailability_reason"]:
                raise Core5ContractError("unavailable resource observation is not explicit")
    for attempt in attempts:
        observation = observed_by_attempt.get(attempt["attempt_id"])
        if require_complete and truth(attempt["payload_received"]):
            if observation is None or observation["observation_status"] != "AVAILABLE":
                raise Core5ContractError("payload-bearing attempt is missing its RSS sample")
        elif require_complete and observation is None:
            raise Core5ContractError("attempt is missing its explicit resource observation")
        elif observation is not None and (
            observation["observation_status"] == "EXPECTED_UNAVAILABLE"
            and classify_core5_terminal(
                attempt["solver_status"], outer_timeout=attempt["outer_timeout"]
            ) != Core5TerminalClass.RIGHT_CENSORED
        ):
            raise Core5ContractError("only a no-payload timeout may have expected-unavailable RSS")

    return ValidatedCore5Rows(
        tuple(cells), tuple(generated), tuple(requests), tuple(attempts),
        tuple(results), tuple(tasks), tuple(observations), tuple(failures),
    )


def validate_core5_child_evidence(
    root: Path | str, outcome: Any,
) -> Dict[str, Any]:
    """Validate one child immediately, before any later cell may start."""

    root = Path(root)
    required = {
        "analysis_requests.csv": REQUEST_COLUMNS,
        "analysis_attempts.csv": ATTEMPT_COLUMNS,
        "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
        "per_task_results.csv": TASK_RESULT_COLUMNS,
        "attempt_resource_observations.csv": RESOURCE_OBSERVATION_COLUMNS,
        "failures.csv": FAILURE_COLUMNS,
    }
    rows = {name: _read_table(root, name, columns) for name, columns in required.items()}
    _unique(rows["analysis_requests.csv"], ("analysis_id",), "child request")
    _unique(rows["analysis_attempts.csv"], ("attempt_id",), "child attempt")
    _unique(rows["per_taskset_results.csv"], ("analysis_id",), "child terminal")
    _unique(rows["per_task_results.csv"], ("analysis_id", "task_id"), "child task result")
    _unique(rows["attempt_resource_observations.csv"], ("attempt_id",), "child resource observation")
    request_ids = {row["analysis_id"] for row in rows["analysis_requests.csv"]}
    terminal_ids = {row["analysis_id"] for row in rows["per_taskset_results.csv"]}
    if int(outcome.requested) != len(request_ids) or int(outcome.terminal) != len(terminal_ids):
        raise Core5ContractError("child outcome counts disagree with persisted tables")
    if request_ids != terminal_ids:
        raise Core5ContractError("child request/terminal set mismatch")
    if bool(outcome.stopped):
        raise Core5ContractError("child outcome is stopped")
    observed_counts: Dict[str, int] = {}
    for result in rows["per_taskset_results.csv"]:
        status = result["solver_status"]
        observed_counts[status] = observed_counts.get(status, 0) + 1
        if classify_core5_terminal(status, outer_timeout=result["outer_timeout"]) == Core5TerminalClass.TECHNICAL_FAILURE:
            raise Core5ContractError(f"child technical or unknown terminal: {status}")
    if dict(outcome.status_counts) != observed_counts:
        raise Core5ContractError("child status_counts disagree with terminals")
    p0 = [row for row in rows["failures.csv"] if row["severity"] == "P0"]
    if p0:
        raise Core5ContractError("child failures.csv contains P0")

    attempts = {row["attempt_id"]: row for row in rows["analysis_attempts.csv"]}
    observations = rows["attempt_resource_observations.csv"]
    observed_attempts = {row["attempt_id"] for row in observations}
    if observed_attempts != set(attempts):
        raise Core5ContractError("child attempt/resource observation set mismatch")
    for observation in observations:
        attempt = attempts[observation["attempt_id"]]
        if observation["analysis_id"] != attempt["analysis_id"]:
            raise Core5ContractError("child resource observation/attempt mismatch")
        if truth(attempt["payload_received"]) and observation["observation_status"] != "AVAILABLE":
            raise Core5ContractError("payload-bearing child attempt lacks RSS")
    return {
        "request_ids": sorted(request_ids),
        "terminal_ids": sorted(terminal_ids),
        "status_counts": observed_counts,
        "p0_failure_count": 0,
    }


def _closure_files(root: Path) -> Dict[str, Path]:
    files: Dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in CORE5_MANIFEST_EXCLUDED_PATHS:
            continue
        if path.is_symlink():
            raise Core5ContractError(f"CORE-5 artifact closure contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise Core5ContractError(f"CORE-5 artifact closure contains a non-regular file: {relative}")
        files[relative] = path
    return files


def write_core5_hash_manifest(root: Path | str) -> None:
    root = Path(root)
    rows = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}"
        for relative, path in _closure_files(root).items()
    ]
    atomic_write_text(root / "file_hashes.sha256", "\n".join(rows) + "\n")


def validate_core5_hash_manifest(
    root: Path | str, *, require_completed_files: bool,
) -> None:
    root = Path(root)
    manifest = root / "file_hashes.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise Core5ContractError("required CORE-5 file_hashes.sha256 is missing")
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Core5ContractError("CORE-5 file_hashes.sha256 is unreadable") from exc
    if not lines:
        raise Core5ContractError("CORE-5 file_hashes.sha256 is empty")
    declared: Dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if match is None:
            raise Core5ContractError("CORE-5 file_hashes.sha256 has an invalid row")
        digest, relative = match.groups()
        relative_path = Path(relative)
        if (
            relative_path.is_absolute() or ".." in relative_path.parts
            or relative_path.as_posix() != relative
            or relative in CORE5_MANIFEST_EXCLUDED_PATHS
        ):
            raise Core5ContractError("CORE-5 file_hashes.sha256 has an unsafe path")
        if relative in declared:
            raise Core5ContractError("CORE-5 file_hashes.sha256 has a duplicate path")
        target = root / relative_path
        if target.is_symlink() or not target.is_file():
            raise Core5ContractError(f"manifest declares a missing/non-regular file: {relative}")
        declared[relative] = digest
    actual = _closure_files(root)
    if require_completed_files and not CORE5_COMPLETED_REQUIRED_FILES.issubset(declared):
        missing = sorted(CORE5_COMPLETED_REQUIRED_FILES - set(declared))
        raise Core5ContractError(f"manifest omits required completed files: {missing}")
    if set(declared) != set(actual):
        raise Core5ContractError("CORE-5 file_hashes.sha256 file set mismatch")
    for relative, digest in declared.items():
        if hashlib.sha256(actual[relative].read_bytes()).hexdigest() != digest:
            raise Core5ContractError(f"CORE-5 file hash mismatch: {relative}")


def _validate_cell_plan(config: Mapping[str, Any], rows: Sequence[Mapping[str, str]]) -> None:
    # Lazy import avoids coupling the contract constants back into the runner.
    from .core5_scalability import Core5ScalabilityRunner, expand_scalability_cells

    expected = [Core5ScalabilityRunner._cell_row(cell) for cell in expand_scalability_cells(config)]
    if len(rows) != len(expected):
        raise Core5ContractError("CORE-5 scalability cell plan count mismatch")
    for actual, planned in zip(rows, expected):
        for field, value in planned.items():
            if actual.get(field, "") != str(value):
                raise Core5ContractError(f"CORE-5 scalability cell plan mismatch: {field}")
        if _json_list(actual["variants"], "cell variants") != config["analysis"]["variants"]:
            raise Core5ContractError("CORE-5 cell method scope mismatch")


def validate_core5_artifact_contract(
    root: Path | str, *, require_completed: bool = True,
) -> ValidatedCore5Rows:
    root = Path(root)
    metadata = _load_json(root / "run_metadata.json", "run metadata")
    checkpoint = _load_json(root / "checkpoint.json", "checkpoint")
    if metadata.get("schema") != CORE5_RUN_SCHEMA:
        raise Core5ContractError("CORE-5 run metadata schema mismatch")
    if checkpoint.get("schema") != CORE5_CHECKPOINT_SCHEMA:
        raise Core5ContractError("CORE-5 checkpoint schema mismatch")
    if metadata.get("core") != "CORE-5" or checkpoint.get("core") != "CORE-5":
        raise Core5ContractError("CORE-5 artifact core mismatch")
    if checkpoint.get("config_hash") != metadata.get("config_hash"):
        raise Core5ContractError("CORE-5 metadata/checkpoint config hash mismatch")
    run_config = root / "run_config.yaml"
    if run_config.is_symlink() or not run_config.is_file():
        raise Core5ContractError("CORE-5 run_config.yaml is missing")
    config = load_config(run_config, expected_core="CORE-5")
    if config_hash(config) != metadata.get("config_hash"):
        raise Core5ContractError("CORE-5 persisted config hash mismatch")
    counts = configured_core5_counts(config)
    if counts != {
        "planned_scalability_cell_count": 8,
        "planned_analysis_count": 16,
        "hard_analysis_limit": 20,
    }:
        raise Core5ContractError("CORE-5 V2 requires the exact 8/16/20 plan")
    for field, value in counts.items():
        if metadata.get(field) != value or checkpoint.get(field) != value:
            raise Core5ContractError(f"CORE-5 configured count mismatch: {field}")
    phase = checkpoint.get("phase")
    if phase not in {"INITIALIZED", "RUNNING", "FINALIZING", "COMPLETED", "STOPPED"}:
        raise Core5ContractError("CORE-5 checkpoint phase is invalid")
    if not isinstance(checkpoint.get("stop_requested"), bool):
        raise Core5ContractError("CORE-5 checkpoint stop_requested is not boolean")
    if phase == "COMPLETED" and checkpoint["stop_requested"]:
        raise Core5ContractError("completed CORE-5 checkpoint is stopped")
    if phase == "STOPPED" and not checkpoint["stop_requested"]:
        raise Core5ContractError("stopped CORE-5 checkpoint lacks stop_requested")
    if require_completed and phase != "COMPLETED":
        raise Core5ContractError("CORE-5 analyzer requires a completed run")

    rows = validate_core5_raw_tables(root, require_complete=phase in {"FINALIZING", "COMPLETED"})
    if phase in {"FINALIZING", "COMPLETED"}:
        _validate_cell_plan(config, rows.cells)
    actual_terminal = len(rows.results)
    technical = sum(
        classify_core5_terminal(row["solver_status"], outer_timeout=row["outer_timeout"])
        == Core5TerminalClass.TECHNICAL_FAILURE
        for row in rows.results
    )
    p0 = sum(row["severity"] == "P0" for row in rows.failures)
    expected_fields = {
        "completed_scalability_cell_count": len(rows.cells),
        "actual_terminal_count": actual_terminal,
        "technical_failure_count": technical,
        "p0_failure_count": p0,
    }
    for field, value in expected_fields.items():
        if checkpoint.get(field) != value:
            raise Core5ContractError(f"CORE-5 checkpoint count mismatch: {field}")
    completed_ids = checkpoint.get("completed_analysis_ids")
    if (
        not isinstance(completed_ids, list) or len(completed_ids) != len(set(completed_ids))
        or set(completed_ids) != {row["analysis_id"] for row in rows.results}
    ):
        raise Core5ContractError("CORE-5 checkpoint completed analysis set mismatch")
    if phase in {"FINALIZING", "COMPLETED"}:
        if actual_terminal != counts["planned_analysis_count"] or technical or p0:
            raise Core5ContractError("final CORE-5 evidence is incomplete or technical")
        for name, columns in CORE5_DERIVED_TABLES.items():
            _read_table(root, name, columns)
        summary = _load_json(root / "scalability_summary.json", "summary")
        if summary.get("stopped") is not False or summary.get("technical_failure_count") != 0:
            raise Core5ContractError("final CORE-5 summary state mismatch")
        checks = read_csv(root / "worker_semantic_checks.csv")
        expected_checks = int(config["grid"]["tasksets_per_cell"]) * len(config["analysis"]["variants"])
        if len(checks) != expected_checks or any(
            row["status"] not in {"SEMANTICALLY_EQUAL", "TIMEOUT_CENSORED"}
            for row in checks
        ):
            raise Core5ContractError("final CORE-5 worker pairing is not closed")
        child_outcomes = _read_table(
            root, "child_outcomes.csv", CHILD_OUTCOME_COLUMNS
        )
        if (
            len(child_outcomes) != counts["planned_scalability_cell_count"]
            or {row["scalability_cell_id"] for row in child_outcomes}
            != {row["scalability_cell_id"] for row in rows.cells}
            or any(
                row["contract_status"] != "VALID" or truth(row["stopped"])
                for row in child_outcomes
            )
        ):
            raise Core5ContractError("final CORE-5 child outcome set is not closed")
    if phase == "COMPLETED":
        validate_core5_hash_manifest(root, require_completed_files=True)
    elif phase == "STOPPED":
        validate_core5_hash_manifest(root, require_completed_files=False)
    return rows


def validate_core5_resume_envelope(
    root: Path | str, *, expected_config_hash: str,
) -> str:
    root = Path(root)
    metadata = _load_json(root / "run_metadata.json", "run metadata")
    checkpoint = _load_json(root / "checkpoint.json", "checkpoint")
    if metadata.get("schema") != CORE5_RUN_SCHEMA or checkpoint.get("schema") != CORE5_CHECKPOINT_SCHEMA:
        raise Core5ContractError("CORE-5 resume schema mismatch")
    if metadata.get("config_hash") != expected_config_hash or checkpoint.get("config_hash") != expected_config_hash:
        raise Core5ContractError("CORE-5 configuration hash mismatch")
    config = load_config(root / "run_config.yaml", expected_core="CORE-5")
    if config_hash(config) != expected_config_hash:
        raise Core5ContractError("CORE-5 persisted configuration hash mismatch")
    phase = checkpoint.get("phase")
    if phase == "COMPLETED":
        validate_core5_artifact_contract(root)
        return phase
    if phase == "STOPPED":
        validate_core5_artifact_contract(root, require_completed=False)
        raise Core5ContractError("refusing to resume a stopped CORE-5 run")
    if phase == "FINALIZING" and (root / "file_hashes.sha256").exists():
        validate_core5_hash_manifest(root, require_completed_files=True)
    if phase not in {"INITIALIZED", "RUNNING", "FINALIZING"}:
        raise Core5ContractError("CORE-5 resume checkpoint phase is invalid")
    return str(phase)
