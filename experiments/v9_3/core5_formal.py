"""CORE-5 formal plan identities, exact transforms, and profile isolation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import random
import resource
import statistics
import time
from typing import Any, Dict, Mapping, Optional, Sequence

from . import exact_energy
from .config import (
    ConfigError, canonical_json, config_hash, domain_hash, dump_config,
    fraction_text, load_config,
)
from .cell_model import expand_cells, taskset_id
from .core5_contract import (
    Core5ContractError,
    validate_core5_child_evidence,
    validate_core5_hash_manifest,
    write_core5_hash_manifest,
)
from .core5_scalability import ResourceExecutionEngine
from .core5_terminal import (
    Core5TerminalClass, classify_core5_terminal, truth,
)
from .formal_authorization import (
    FormalAuthorizationError,
    revalidate_authorization_seal,
    verify_authorization,
)
from .resource_measurement import RESOURCE_OBSERVATION_COLUMNS
from .result_writer import (
    ATTEMPT_COLUMNS, FAILURE_COLUMNS, GENERATED_COLUMNS, REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, ResultWriterError,
    atomic_write_json, read_csv, validate_csv_header, write_csv,
)
from .taskset_store import (
    ServiceCurveMaterial, StoredTaskset, TasksetStore, prepare_service_curve,
)

import asap_block_rta_v9_3 as rta_core


CORE5_FORMAL_PLAN_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_FORMAL_PLAN_V1"
CORE5_FORMAL_RUN_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_FORMAL_RUN_V1"
CORE5_FORMAL_CHECKPOINT_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_FORMAL_CHECKPOINT_V1"
CORE5A_PROFILE = "formal-algorithmic-v1"
CORE5B_PROFILE = "formal-workers-v1"
CORE5A_METRICS_SCHEMA = "ASAP_BLOCK_V9_3_CORE5A_PERSISTED_METRICS_V1"
CORE5B_WORKER_CHECK_SCHEMA = "ASAP_BLOCK_V9_3_CORE5B_WORKER_CHECK_V1"
GENERIC_CHILD_RUN_SCHEMA = "ASAP_BLOCK_V9_3_FORMAL_RUN_V1"
CORE5B_WORKER_CHECK_COLUMNS = (
    "schema", "mathematical_request_id", "input_hash",
    "execution_count", "worker_execution_counts_json",
    "repetitions_by_worker_json", "terminal_class", "response_bound",
    "fixed_point_iterations", "fixed_point_iterations_status",
    "search_states", "inverse_service_queries", "candidate_count",
    "status", "detail",
)


class Core5FormalContractError(RuntimeError):
    """Raised when formal CORE-5 profiles or artifacts are mixed."""


@dataclass(frozen=True)
class FormalChildEvidence:
    state: str
    requested: int
    terminal: int
    status_counts: Mapping[str, int]
    stopped: bool = False


def _json_object(path: Path, label: str) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Core5FormalContractError(f"missing {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Core5FormalContractError(f"unreadable {label}: {path}") from exc
    if not isinstance(value, dict):
        raise Core5FormalContractError(f"{label} is not a JSON object: {path}")
    return value


def _validate_present_child_tables(root: Path) -> None:
    expected = {
        "generated_tasksets.csv": GENERATED_COLUMNS,
        "analysis_requests.csv": REQUEST_COLUMNS,
        "analysis_attempts.csv": ATTEMPT_COLUMNS,
        "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
        "per_task_results.csv": TASK_RESULT_COLUMNS,
        "attempt_resource_observations.csv": RESOURCE_OBSERVATION_COLUMNS,
        "failures.csv": FAILURE_COLUMNS,
    }
    for name, columns in expected.items():
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise Core5FormalContractError(
                f"invalid child artifact path: {path}"
            )
        try:
            validate_csv_header(path, columns)
        except ResultWriterError as exc:
            raise Core5FormalContractError(
                f"invalid child table header: {path}"
            ) from exc


def inspect_formal_child(
    child: Mapping[str, Any], *, expected_request_count: int,
) -> FormalChildEvidence:
    """Classify one child as fresh, resumable, or durably complete.

    Configuration and persisted identities are checked before a parent may
    decide to resume or skip the child. A mismatched or ambiguous child fails
    closed instead of being silently replaced.
    """

    root = Path(child["execution"]["output_root"])
    if not root.exists():
        return FormalChildEvidence("FRESH", 0, 0, {})
    if root.is_symlink() or not root.is_dir():
        raise Core5FormalContractError(f"invalid child output root: {root}")
    metadata_path = root / "run_metadata.json"
    if not metadata_path.exists():
        # CORE-5A prepares the deterministic service material immediately
        # before constructing the child engine. That pre-initialization
        # directory is not yet a child run and remains a valid fresh start.
        existing = {path.name for path in root.iterdir()}
        if not existing or existing == {"service_material"}:
            return FormalChildEvidence("FRESH", 0, 0, {})
        raise Core5FormalContractError(
            f"child artifacts exist without run metadata: {root}"
        )

    expected_hash = config_hash(child)
    metadata = _json_object(metadata_path, "child run metadata")
    if (
        metadata.get("schema") != GENERIC_CHILD_RUN_SCHEMA
        or metadata.get("config_hash") != expected_hash
    ):
        raise Core5FormalContractError(
            f"child run metadata/config hash mismatch: {root}"
        )
    persisted = load_config(root / "run_config.yaml", expected_core="CORE-5")
    if config_hash(persisted) != expected_hash:
        raise Core5FormalContractError(
            f"persisted child configuration hash mismatch: {root}"
        )
    seal = _json_object(
        root / "formal_authorization_seal.json", "child authorization seal"
    )
    if (
        metadata.get("formal_large_scale_run")
        != seal.get("formal_large_scale_run")
        or metadata.get("formal_authorization_id")
        != seal.get("authorization_id")
    ):
        raise Core5FormalContractError(
            f"child metadata/authorization seal mismatch: {root}"
        )
    checkpoint_path = root / "checkpoint.json"
    if checkpoint_path.exists():
        checkpoint = _json_object(checkpoint_path, "child checkpoint")
        if checkpoint.get("config_hash") != expected_hash:
            raise Core5FormalContractError(
                f"child checkpoint/config hash mismatch: {root}"
            )
    else:
        checkpoint = {}

    _validate_present_child_tables(root)
    requests = read_csv(root / "analysis_requests.csv")
    results = read_csv(root / "per_taskset_results.csv")
    request_ids = [row.get("analysis_id", "") for row in requests]
    result_ids = [row.get("analysis_id", "") for row in results]
    if (
        any(not value for value in request_ids + result_ids)
        or len(request_ids) != len(set(request_ids))
        or len(result_ids) != len(set(result_ids))
        or not set(result_ids).issubset(request_ids)
    ):
        raise Core5FormalContractError(
            f"invalid child request/terminal identity closure: {root}"
        )
    if requests and len(requests) != expected_request_count:
        raise Core5FormalContractError(
            f"child request plan count mismatch: {root}"
        )
    completed_ids = checkpoint.get("completed_analysis_ids", [])
    if (
        not isinstance(completed_ids, list)
        or any(not isinstance(value, str) or not value for value in completed_ids)
        or not set(completed_ids).issubset(request_ids)
        or not set(completed_ids).issubset(result_ids)
    ):
        raise Core5FormalContractError(
            f"child checkpoint completion set mismatch: {root}"
        )

    if (
        len(requests) == expected_request_count
        and set(request_ids) == set(result_ids)
        and all(row.get("request_status") == "TERMINAL" for row in requests)
    ):
        counts = dict(Counter(row["solver_status"] for row in results))
        evidence = FormalChildEvidence(
            "COMPLETED", len(requests), len(results), counts
        )
        try:
            validate_core5_child_evidence(root, evidence)
        except Core5ContractError as exc:
            raise Core5FormalContractError(
                f"completed child evidence is invalid: {root}"
            ) from exc
        generated = {
            row["taskset_id"]: row
            for row in read_csv(root / "generated_tasksets.csv")
        }
        if len(generated) != len(read_csv(root / "generated_tasksets.csv")):
            raise Core5FormalContractError(
                f"duplicate generated child taskset identity: {root}"
            )
        request_by_analysis = {
            row["analysis_id"]: row for row in requests
        }
        attempt_rows = read_csv(root / "analysis_attempts.csv")
        attempt_by_id = {
            row["attempt_id"]: row for row in attempt_rows
        }
        if len(attempt_by_id) != len(attempt_rows):
            raise Core5FormalContractError(
                f"duplicate child attempt identity: {root}"
            )
        result_by_analysis = {
            row["analysis_id"]: row for row in results
        }
        for result in results:
            request = request_by_analysis[result["analysis_id"]]
            generated_row = generated.get(request["taskset_id"])
            final_attempt = attempt_by_id.get(result["final_attempt_id"])
            if (
                generated_row is None
                or generated_row.get("taskset_hash") != request["taskset_hash"]
                or result["request_id"] != request["request_id"]
                or result["taskset_id"] != request["taskset_id"]
                or result["taskset_hash"] != request["taskset_hash"]
                or result["exact_e0"] != request["exact_e0"]
                or result["analysis_variant"] != request["variant"]
                or final_attempt is None
                or final_attempt["analysis_id"] != result["analysis_id"]
                or final_attempt["solver_status"] != result["solver_status"]
            ):
                raise Core5FormalContractError(
                    f"child request/input/terminal closure mismatch: {root}"
                )
        for task in read_csv(root / "per_task_results.csv"):
            result = result_by_analysis.get(task["analysis_id"])
            if result is None or task["taskset_id"] != result["taskset_id"]:
                raise Core5FormalContractError(
                    f"child task/terminal closure mismatch: {root}"
                )
        return evidence
    return FormalChildEvidence(
        "RESUME", len(requests), len(results),
        dict(Counter(row["solver_status"] for row in results)),
    )


@dataclass(frozen=True)
class Core5AFormalCell:
    scaling_axis: str
    level_id: str
    processors: int
    task_count: int
    period_min: int
    period_max: int
    exact_time_scale: Fraction
    utilization: Fraction
    source_family_id: str
    cell_id: str

    def mathematical_input(self) -> Dict[str, Any]:
        return {
            "M": self.processors,
            "task_n": self.task_count,
            "period_min": self.period_min,
            "period_max": self.period_max,
            "utilization": fraction_text(self.utilization),
        }

    def row(self) -> Dict[str, Any]:
        return {
            "formal_cell_id": self.cell_id,
            "scaling_axis": self.scaling_axis,
            "level_id": self.level_id,
            **self.mathematical_input(),
            "exact_time_scale": fraction_text(self.exact_time_scale),
            "source_taskset_family_id": self.source_family_id,
        }


def _source_family(
    config: Mapping[str, Any], *, processors: int, task_count: int,
    utilization: Fraction,
) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:CORE5A:SOURCE_TASKSET_FAMILY:v1",
        {
            "M": processors,
            "task_n": task_count,
            "period_min": 40,
            "period_max": 200,
            "utilization": fraction_text(utilization),
            "base_seed": config["grid"]["base_seed"],
            "generation": config["generation"],
        },
    )


def expand_core5a_cells(config: Mapping[str, Any]) -> tuple[Core5AFormalCell, ...]:
    if config["scalability"].get("profile") != CORE5A_PROFILE:
        raise ConfigError("CORE-5A expansion requires formal-algorithmic-v1")
    scale = config["scalability"]
    cells = []
    for utilization_text in scale["utilization_points"]:
        utilization = Fraction(utilization_text)
        specs = []
        specs.extend(
            ("task_count", f"n-{n}", 4, n, 40, 200, Fraction(1))
            for n in scale["task_counts"]
        )
        specs.extend(
            ("core_count", f"m-{m}", m, 20, 40, 200, Fraction(1))
            for m in scale["core_counts"]
        )
        specs.extend(
            (
                "time_scale", f"time-{factor}x", 4, 10,
                40 * int(Fraction(factor)), 200 * int(Fraction(factor)),
                Fraction(factor),
            )
            for factor in scale["time_scales"]
        )
        seen: set[tuple[int, int, int, int]] = set()
        for axis, level_id, processors, task_count, pmin, pmax, factor in specs:
            key = (processors, task_count, pmin, pmax)
            if key in seen:
                continue
            seen.add(key)
            source_processors = 4 if axis == "time_scale" else processors
            source_task_count = 10 if axis == "time_scale" else task_count
            source_family_id = _source_family(
                config, processors=source_processors,
                task_count=source_task_count, utilization=utilization,
            )
            identity = {
                "profile": CORE5A_PROFILE,
                "axis": axis,
                "level_id": level_id,
                "M": processors,
                "task_n": task_count,
                "period_min": pmin,
                "period_max": pmax,
                "exact_time_scale": fraction_text(factor),
                "utilization": fraction_text(utilization),
                "source_taskset_family_id": source_family_id,
            }
            cells.append(Core5AFormalCell(
                axis, level_id, processors, task_count, pmin, pmax, factor,
                utilization, source_family_id,
                domain_hash("ASAP_BLOCK:V9.3:CORE5A:FORMAL_CELL:v1", identity),
            ))
    return tuple(cells)


def exact_time_scale_payload(
    task_payload: Sequence[Mapping[str, Any]], exact_scale: Fraction,
) -> tuple[Dict[str, Any], ...]:
    """Scale C/D/T exactly while preserving utilization, D/T, P, and identity."""

    factor = Fraction(exact_scale)
    if factor.denominator != 1 or factor <= 0:
        raise Core5FormalContractError("CORE-5A time scale must be a positive integer")
    multiplier = factor.numerator
    transformed = []
    for source in task_payload:
        row = dict(source)
        c_value = int(source["C"]) * multiplier
        d_value = int(source["D"]) * multiplier
        t_value = int(source["T"]) * multiplier
        if not 0 < c_value <= d_value <= t_value:
            raise Core5FormalContractError("scaled task violates C <= D <= T")
        row.update({
            "C": c_value, "D": d_value, "T": t_value,
            "D_over_T": fraction_text(Fraction(d_value, t_value)),
            "source_task_id": str(source["task_id"]),
            "exact_time_scale": fraction_text(factor),
        })
        transformed.append(row)
    return tuple(transformed)


def core5b_math_request_rows(config: Mapping[str, Any]) -> tuple[Dict[str, Any], ...]:
    if config["scalability"].get("profile") != CORE5B_PROFILE:
        raise ConfigError("CORE-5B expansion requires formal-workers-v1")
    rows = []
    start = int(config["grid"].get("taskset_index_start", 0))
    count = int(config["grid"]["tasksets_per_cell"])
    for utilization in config["scalability"]["utilization_points"]:
        for taskset_index in range(start, start + count):
            taskset_input = {
                "M": 4, "task_n": 10, "period_min": 40,
                "period_max": 200, "utilization": utilization,
                "taskset_index": taskset_index,
                "base_seed": config["grid"]["base_seed"],
            }
            taskset_input_hash = domain_hash(
                "ASAP_BLOCK:V9.3:CORE5B:TASKSET_INPUT:v1", taskset_input
            )
            for variant in config["analysis"]["variants"]:
                mathematical_input = {
                    **taskset_input,
                    "taskset_input_hash": taskset_input_hash,
                    "E0": config["energy"]["initial_energy_values"][0],
                    "battery_capacity": config["energy"]["battery_capacity"],
                    "service_curve": config["energy"]["service_curve"]["id"],
                    "variant": variant,
                    "numerical_mode": config["analysis"]["numerical_mode"],
                }
                rows.append({
                    **mathematical_input,
                    "mathematical_request_id": domain_hash(
                        "ASAP_BLOCK:V9.3:CORE5B:MATHEMATICAL_REQUEST:v1",
                        mathematical_input,
                    ),
                    "input_hash": domain_hash(
                        "ASAP_BLOCK:V9.3:CORE5B:INPUT:v1", mathematical_input
                    ),
                })
    return tuple(rows)


def core5b_execution_schedule(config: Mapping[str, Any]) -> tuple[Dict[str, int], ...]:
    scale = config["scalability"]
    schedule = [
        {"worker_count": worker, "repetition": repetition}
        for worker in scale["worker_counts"]
        for repetition in range(scale["repetitions_per_worker"])
    ]
    random.Random(scale["schedule_seed"]).shuffle(schedule)
    return tuple({"run_order": index, **row} for index, row in enumerate(schedule))


def assert_worker_semantic_identity(rows: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed if repeated worker executions disagree mathematically."""

    grouped: Dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        request_id = str(row.get("mathematical_request_id", ""))
        if not request_id:
            raise Core5FormalContractError("worker result lacks mathematical request ID")
        grouped.setdefault(request_id, []).append(row)
    required = {
        "input_hash", "terminal_class", "response_bound",
        "fixed_point_iterations", "search_states",
        "inverse_service_queries", "candidate_count",
    }
    for request_id, members in grouped.items():
        if any(not required.issubset(member) for member in members):
            raise Core5FormalContractError(
                f"worker result lacks semantic fields: {request_id}"
            )
        signatures = {
            canonical_json({key: member[key] for key in sorted(required)})
            for member in members
        }
        if len(signatures) != 1:
            raise Core5FormalContractError(
                f"P0 worker semantic mismatch: {request_id}"
            )


def _availability(available: int, total: int) -> str:
    if available == 0:
        return "UNAVAILABLE"
    if available == total:
        return "AVAILABLE"
    return "PARTIAL"


def _counter_metric(
    rows: Sequence[Mapping[str, str]], field: str,
) -> Dict[str, Any]:
    values = [
        int(row[field]) for row in rows
        if row.get(field) not in (None, "", "UNAVAILABLE")
    ]
    return {
        "observation_status": _availability(len(values), len(rows)),
        "available_observation_count": len(values),
        "total": sum(values) if values else None,
    }


def _nearest_rank(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)]


def _aggregate_core5a_rows(
    child_roots: Sequence[Path], *, run_id: str,
) -> Dict[str, Any]:
    results = [
        row for root in child_roots
        for row in read_csv(root / "per_taskset_results.csv")
    ]
    tasks = [
        row for root in child_roots
        for row in read_csv(root / "per_task_results.csv")
    ]
    attempts = [
        row for root in child_roots
        for row in read_csv(root / "analysis_attempts.csv")
    ]
    observations = [
        row for root in child_roots
        for row in read_csv(root / "attempt_resource_observations.csv")
    ]

    terminal_classes = [
        classify_core5_terminal(
            row["solver_status"], outer_timeout=row["outer_timeout"]
        )
        for row in results
    ]
    completed_runtime = [
        float(row["runtime_wall_seconds"])
        for row, terminal_class in zip(results, terminal_classes)
        if (
            terminal_class == Core5TerminalClass.SCIENTIFIC_COMPLETION
            and row.get("runtime_wall_seconds") not in
            (None, "", "UNAVAILABLE")
        )
    ]
    peak_values = [
        int(row["peak_rss_kib"])
        for row in observations
        if (
            row.get("observation_status") == "AVAILABLE"
            and row.get("peak_rss_kib") not in
            (None, "", "UNAVAILABLE")
        )
    ]
    candidate_values = [
        int(row["n_tasks_candidate_found"])
        for row in results
        if row.get("n_tasks_candidate_found") not in
        (None, "", "UNAVAILABLE")
    ]
    attempt_timeout_count = sum(
        classify_core5_terminal(
            row["solver_status"], outer_timeout=row["outer_timeout"]
        ) == Core5TerminalClass.RIGHT_CENSORED
        for row in attempts
    )
    retried_analyses = {
        row["analysis_id"] for row in attempts
        if int(row["attempt_number"]) > 1
    }
    censoring = Counter(
        (
            "RIGHT_CENSORED_TIMEOUT"
            if terminal_class == Core5TerminalClass.RIGHT_CENSORED
            else "SCIENTIFIC_COMPLETION"
            if terminal_class == Core5TerminalClass.SCIENTIFIC_COMPLETION
            else "TECHNICAL_FAILURE"
        )
        for terminal_class in terminal_classes
    )
    return {
        "run_id": run_id,
        "execution_count": len(results),
        "terminal_status_counts": dict(sorted(Counter(
            row["solver_status"] for row in results
        ).items())),
        "runtime_seconds": {
            "scope": "SCIENTIFIC_COMPLETION_ONLY",
            "observation_status": _availability(
                len(completed_runtime), len(results)
            ),
            "available_observation_count": len(completed_runtime),
            "median": (
                statistics.median(completed_runtime)
                if completed_runtime else None
            ),
            "p95": _nearest_rank(completed_runtime, 0.95),
            "max": max(completed_runtime) if completed_runtime else None,
        },
        "peak_rss_kib": {
            "scope": "ALL_CHILD_ATTEMPTS",
            "observation_status": _availability(
                len(peak_values), len(observations)
            ),
            "available_observation_count": len(peak_values),
            "max": max(peak_values) if peak_values else None,
        },
        "fixed_point_iterations": {
            "observation_status": "UNAVAILABLE",
            "available_observation_count": 0,
            "total": None,
        },
        "search_counters": {
            "checked_w_count": _counter_metric(tasks, "checked_w_count"),
            "checked_h_count": _counter_metric(tasks, "checked_h_count"),
            "checked_q_count": _counter_metric(tasks, "checked_q_count"),
        },
        "inverse_service_queries": _counter_metric(
            tasks, "envelope_call_count"
        ),
        "candidate_counts": {
            "observation_status": _availability(
                len(candidate_values), len(results)
            ),
            "available_observation_count": len(candidate_values),
            "total": sum(candidate_values) if candidate_values else None,
        },
        "timeout_retry_counts": {
            "terminal_timeout_count": sum(
                terminal_class == Core5TerminalClass.RIGHT_CENSORED
                for terminal_class in terminal_classes
            ),
            "attempt_timeout_count": attempt_timeout_count,
            "analysis_retry_count": len(retried_analyses),
            "retry_attempt_count": sum(
                int(row["attempt_number"]) > 1 for row in attempts
            ),
            "total_attempt_count": len(attempts),
        },
        "censoring_state_counts": {
            "SCIENTIFIC_COMPLETION": censoring["SCIENTIFIC_COMPLETION"],
            "RIGHT_CENSORED_TIMEOUT": censoring["RIGHT_CENSORED_TIMEOUT"],
            "TECHNICAL_FAILURE": censoring["TECHNICAL_FAILURE"],
        },
        "unavailable_values_are_null": True,
    }


def reconstruct_core5a_metrics(
    root: Path | str, run_ids: Sequence[str],
) -> Dict[str, Any]:
    root = Path(root)
    ordered = sorted(run_ids)
    groups = [
        _aggregate_core5a_rows(
            [root / "child_runs" / run_id], run_id=run_id
        )
        for run_id in ordered
    ]
    overall = _aggregate_core5a_rows(
        [root / "child_runs" / run_id for run_id in ordered],
        run_id="ALL",
    )
    return {
        "schema": CORE5A_METRICS_SCHEMA,
        "profile": CORE5A_PROFILE,
        "child_run_count": len(ordered),
        "groups": groups,
        "overall": overall,
    }


def build_worker_semantic_checks(
    rows: Sequence[Mapping[str, Any]], *,
    expected_request_ids: Sequence[str],
    worker_counts: Sequence[int], repetitions_per_worker: int,
) -> list[Dict[str, Any]]:
    assert_worker_semantic_identity(rows)
    grouped: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["mathematical_request_id"])].append(row)
    if set(grouped) != set(expected_request_ids):
        raise Core5FormalContractError(
            "CORE-5B mathematical request set mismatch"
        )
    expected_workers = {int(value) for value in worker_counts}
    expected_repetitions = set(range(repetitions_per_worker))
    expected_execution_count = len(expected_workers) * repetitions_per_worker
    checks = []
    signature_fields = (
        "input_hash", "terminal_class", "response_bound",
        "fixed_point_iterations", "search_states",
        "inverse_service_queries", "candidate_count",
    )
    for request_id in sorted(grouped):
        members = grouped[request_id]
        pairs = [
            (int(row["worker_count"]), int(row["repetition"]))
            for row in members
        ]
        worker_counter = Counter(worker for worker, _ in pairs)
        repetitions = {
            worker: sorted(repetition for member_worker, repetition in pairs
                           if member_worker == worker)
            for worker in sorted(expected_workers)
        }
        if (
            len(members) != expected_execution_count
            or set(worker_counter) != expected_workers
            or any(
                worker_counter[worker] != repetitions_per_worker
                or set(repetitions[worker]) != expected_repetitions
                or len(repetitions[worker]) != repetitions_per_worker
                for worker in expected_workers
            )
        ):
            raise Core5FormalContractError(
                f"CORE-5B worker/repetition multiplicity mismatch: {request_id}"
            )
        first = members[0]
        checks.append({
            "schema": CORE5B_WORKER_CHECK_SCHEMA,
            "mathematical_request_id": request_id,
            "input_hash": first["input_hash"],
            "execution_count": len(members),
            "worker_execution_counts_json": canonical_json({
                str(worker): worker_counter[worker]
                for worker in sorted(expected_workers)
            }),
            "repetitions_by_worker_json": canonical_json({
                str(worker): repetitions[worker]
                for worker in sorted(expected_workers)
            }),
            "terminal_class": first["terminal_class"],
            "response_bound": first["response_bound"],
            "fixed_point_iterations": (
                "UNAVAILABLE"
                if first["fixed_point_iterations"] is None
                else first["fixed_point_iterations"]
            ),
            "fixed_point_iterations_status": (
                "UNAVAILABLE"
                if first["fixed_point_iterations"] is None
                else "AVAILABLE"
            ),
            "search_states": first["search_states"],
            "inverse_service_queries": first["inverse_service_queries"],
            "candidate_count": first["candidate_count"],
            "status": "SEMANTICALLY_IDENTICAL",
            "detail": "",
        })
        if any(
            canonical_json({field: member[field] for field in signature_fields})
            != canonical_json({field: first[field] for field in signature_fields})
            for member in members[1:]
        ):
            raise Core5FormalContractError(
                f"P0 worker semantic mismatch: {request_id}"
            )
    return checks


class ExactTimeScaleStoreView:
    """Expose exact C/D/T-scaled descendants of one frozen source family."""

    def __init__(
        self, base_store: TasksetStore, base_cell: Any,
        exact_scale: Fraction, root: Path,
    ) -> None:
        self.base_store = base_store
        self.base_cell = base_cell
        self.exact_scale = Fraction(exact_scale)
        self.root = Path(root)

    def get_or_create(self, cell: Any, taskset_index: int) -> StoredTaskset:
        source = self.base_store.get_or_create(self.base_cell, taskset_index)
        if self.exact_scale == 1:
            return source
        payload = exact_time_scale_payload(
            source.task_payload, self.exact_scale
        )
        tasks = tuple(
            rta_core.V93Task(
                str(row["task_id"]), int(row["C"]), int(row["D"]),
                int(row["T"]), exact_energy.parse_persisted_fraction(
                    row["P"], "CORE-5 source task P",
                ),
            )
            for row in payload
        )
        semantic_input = {
            "schema": "ASAP_BLOCK_V9_3_CORE5A_EXACT_TIME_SCALE_V1",
            "source_taskset_hash": source.semantic_hash,
            "source_taskset_id": source.taskset_id,
            "target_generation_id": cell.generation_id,
            "exact_time_scale": fraction_text(self.exact_scale),
            "tasks": payload,
        }
        semantic_hash = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5A:SCALED_TASKSET:v1", semantic_input
        )
        target_id = taskset_id(
            cell.generation_id, taskset_index, semantic_hash
        )
        path = (
            self.root / cell.generation_id
            / f"taskset_{taskset_index:05d}.json"
        )
        document = {
            **semantic_input,
            "taskset_id": target_id,
            "taskset_hash": semantic_hash,
            "taskset_index": taskset_index,
            "source_generation_id": source.generation_id,
            "priority_hash": source.priority_hash,
            "power_hash": source.power_hash,
            "target_total_utilization": fraction_text(source.target_utilization),
            "actual_total_utilization": fraction_text(source.actual_utilization),
            "service_curve_reference": source.service_curve_reference,
        }
        if path.is_file():
            observed = json.loads(path.read_text(encoding="utf-8"))
            if observed != document:
                raise Core5FormalContractError(
                    "scaled taskset artifact conflicts with exact transform"
                )
        else:
            atomic_write_json(path, document)
        return StoredTaskset(
            target_id, cell.generation_id, taskset_index, source.seed,
            semantic_hash, source.priority_hash, source.power_hash,
            source.target_utilization, source.actual_utilization,
            cell.processors, cell.task_count, source.deadline_mode,
            tasks, payload, source.generation_seconds,
            source.service_curve_reference, path,
        )


class Core5FormalRunner:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        authorization_path: Optional[Path] = None,
        source_config_path: Optional[Path] = None,
        prepared_config_path: Optional[Path] = None,
    ) -> None:
        self.config = dict(config)
        self.profile = self.config["scalability"].get("profile")
        if self.profile not in {CORE5A_PROFILE, CORE5B_PROFILE}:
            raise ConfigError("CORE-5 formal runner requires a formal profile")
        self.root = Path(self.config["execution"]["output_root"])
        self.identity = config_hash(self.config)
        self._authorization_path = authorization_path
        self._source_config_path = source_config_path
        self._prepared_config_path = prepared_config_path
        self._authorization_seal: Optional[Dict[str, Any]] = None

    def describe(self, *, max_cells: int | None = None) -> Dict[str, Any]:
        if self.profile == CORE5A_PROFILE:
            all_cells = list(expand_core5a_cells(self.config))
            cells = all_cells if max_cells is None else all_cells[:max_cells]
            requests = (
                len(cells) * self.config["grid"]["tasksets_per_cell"]
                * len(self.config["analysis"]["variants"])
            )
            result = {
                "schema": CORE5_FORMAL_PLAN_SCHEMA,
                "profile": self.profile,
                "experiment_id": self.config["experiment_id"],
                "core": "CORE-5",
                "cell_count": len(cells),
                "unique_scale_configurations_per_utilization": 8,
                "mathematical_request_count": requests,
                "solver_execution_count": requests,
                "hard_analysis_limit": self.config["scalability"]["max_analyses"],
                "cells": [cell.row() for cell in cells],
            }
        else:
            math_rows = core5b_math_request_rows(self.config)
            schedule = list(core5b_execution_schedule(self.config))
            cells = schedule if max_cells is None else schedule[:max_cells]
            executions = len(cells) * len(math_rows)
            result = {
                "schema": CORE5_FORMAL_PLAN_SCHEMA,
                "profile": self.profile,
                "experiment_id": self.config["experiment_id"],
                "core": "CORE-5",
                "cell_count": len(cells),
                "mathematical_request_count": len(math_rows),
                "input_hash_count": len({row["input_hash"] for row in math_rows}),
                "solver_execution_count": executions,
                "repetitions_per_worker": self.config["scalability"]["repetitions_per_worker"],
                "worker_counts": self.config["scalability"]["worker_counts"],
                "schedule_seed": self.config["scalability"]["schedule_seed"],
                "hard_analysis_limit": self.config["scalability"]["max_analyses"],
                "cells": cells,
            }
        result["plan_hash"] = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5:FORMAL_PLAN:v1", result
        )
        return result

    def _write_checkpoint(
        self, *, phase: str, completed_run_ids: Sequence[str],
        terminal_count: int, p0: bool,
    ) -> None:
        plan = self.describe()
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": CORE5_FORMAL_CHECKPOINT_SCHEMA,
            "profile": self.profile,
            "config_hash": self.identity,
            "plan_hash": plan["plan_hash"],
            "phase": phase,
            "completed_run_ids": sorted(completed_run_ids),
            "terminal_count": int(terminal_count),
            "p0": bool(p0),
        })

    def _initialize(self, *, resume: bool) -> tuple[list[str], int, str]:
        seal = verify_authorization(
            self.config,
            authorization_path=self._authorization_path,
            source_freeze_config=self._source_config_path,
            prepared_config=self._prepared_config_path,
            project_root=Path(__file__).resolve().parents[2],
        )
        self._authorization_seal = dict(seal)
        metadata_path = self.root / "run_metadata.json"
        if metadata_path.is_file():
            if not resume:
                raise Core5FormalContractError(
                    "formal CORE-5 output exists; use --resume"
                )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            checkpoint = json.loads(
                (self.root / "checkpoint.json").read_text(encoding="utf-8")
            )
            seal = _json_object(
                self.root / "formal_authorization_seal.json",
                "formal authorization seal",
            )
            persisted = load_config(
                self.root / "run_config.yaml", expected_core="CORE-5"
            )
            plan = self.describe()
            completed_ids = checkpoint.get("completed_run_ids")
            if (
                metadata.get("schema") != CORE5_FORMAL_RUN_SCHEMA
                or metadata.get("profile") != self.profile
                or metadata.get("config_hash") != self.identity
                or metadata.get("formal_large_scale_run") is not True
                or checkpoint.get("schema") != CORE5_FORMAL_CHECKPOINT_SCHEMA
                or checkpoint.get("phase") == "STOPPED"
                or checkpoint.get("phase") not in {
                    "INITIALIZED", "RUNNING", "INTERRUPTED",
                    "FINALIZING", "COMPLETED",
                }
                or checkpoint.get("profile") != self.profile
                or checkpoint.get("config_hash") != self.identity
                or checkpoint.get("plan_hash") != plan["plan_hash"]
                or config_hash(persisted) != self.identity
                or seal != self._authorization_seal
                or metadata.get("formal_authorization_id")
                != seal.get("authorization_id")
                or not isinstance(completed_ids, list)
                or any(
                    not isinstance(value, str) or not value
                    for value in (completed_ids or [])
                )
                or len(completed_ids or []) != len(set(completed_ids or []))
                or isinstance(checkpoint.get("terminal_count"), bool)
                or not isinstance(checkpoint.get("terminal_count"), int)
                or checkpoint.get("terminal_count", -1) < 0
                or checkpoint.get("p0") is not False
            ):
                raise Core5FormalContractError(
                    "formal CORE-5 resume envelope mismatch"
                )
            return (
                list(completed_ids),
                int(checkpoint.get("terminal_count", 0)),
                str(checkpoint["phase"]),
            )
        self.root.mkdir(parents=True, exist_ok=True)
        if any(self.root.iterdir()):
            raise Core5FormalContractError(
                "formal CORE-5 output has artifacts without metadata"
            )
        plan = self.describe()
        atomic_write_json(self.root / "formal_authorization_seal.json", seal)
        atomic_write_json(metadata_path, {
            "schema": CORE5_FORMAL_RUN_SCHEMA,
            "profile": self.profile,
            "config_hash": self.identity,
            "plan_hash": plan["plan_hash"],
            "formal_large_scale_run": True,
            "formal_authorization_id": seal["authorization_id"],
            "authorization_seal_schema": seal["schema"],
        })
        dump_config(self.config, self.root / "run_config.yaml")
        atomic_write_json(self.root / "formal_plan.json", plan)
        self._write_checkpoint(
            phase="INITIALIZED", completed_run_ids=[], terminal_count=0,
            p0=False,
        )
        return [], 0, "INITIALIZED"

    def _child_config(
        self, *, run_id: str, processors: int, task_count: int,
        period_min: int, period_max: int, utilizations: Sequence[str],
        tasksets: int, worker_count: int,
    ) -> Dict[str, Any]:
        from copy import deepcopy

        child = deepcopy(self.config)
        child.pop("parameter_status", None)
        child["scalability"]["profile"] = "bounded-smoke-v2"
        child["parent_formal_authorization_id"] = (
            self._authorization_seal["authorization_id"]
            if self._authorization_seal else None
        )
        # Worker/repetition are operational dimensions; this experiment ID is
        # deliberately stable so mathematical analysis IDs remain identical.
        child["experiment_id"] = self.config["experiment_id"]
        child["platform"] = {"cores": [processors], "task_count": [task_count]}
        child["generation"]["period_min"] = period_min
        child["generation"]["period_max"] = period_max
        child["grid"]["utilization_points"] = list(utilizations)
        child["grid"]["tasksets_per_cell"] = tasksets
        child["analysis"]["worker_count"] = worker_count
        child["execution"]["output_root"] = str(
            self.root / "child_runs" / run_id
        )
        child["execution"]["taskset_store"] = self.config["execution"][
            "taskset_store"
        ]
        child["execution"]["resume"] = False
        return child

    @staticmethod
    def _usage_cpu_seconds() -> float:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return float(usage.ru_utime + usage.ru_stime)

    def _run_child(
        self, child: Mapping[str, Any], *, resume: bool,
        service: ServiceCurveMaterial | None = None,
        store: Any = None,
    ) -> tuple[Any, Dict[str, Any]]:
        started = time.perf_counter()
        cpu_before = self._usage_cpu_seconds()
        outcome = ResourceExecutionEngine(
            child, service_override=service, store_override=store
        ).run(resume=resume)
        wall = time.perf_counter() - started
        cpu = max(self._usage_cpu_seconds() - cpu_before, 0.0)
        observations = read_csv(
            Path(child["execution"]["output_root"])
            / "attempt_resource_observations.csv"
        )
        rss = [
            int(row["peak_rss_kib"]) for row in observations
            if str(row.get("peak_rss_kib", "")).isdigit()
        ]
        return outcome, {
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "peak_rss_kib": max(rss, default=None),
            "analyses_per_second": outcome.terminal / wall if wall else None,
        }

    @staticmethod
    def _reconstruct_child_metrics(child_root: Path) -> Dict[str, Any]:
        attempts = read_csv(child_root / "analysis_attempts.csv")
        observations = read_csv(
            child_root / "attempt_resource_observations.csv"
        )
        results = read_csv(child_root / "per_taskset_results.csv")
        wall_values = [
            float(row["total_wall_seconds"]) for row in attempts
            if row.get("total_wall_seconds") not in
            (None, "", "UNAVAILABLE")
        ]
        cpu_values = [
            float(row["solver_cpu_seconds"]) for row in attempts
            if row.get("solver_cpu_seconds") not in
            (None, "", "UNAVAILABLE")
        ]
        rss_values = [
            int(row["peak_rss_kib"]) for row in observations
            if (
                row.get("observation_status") == "AVAILABLE"
                and row.get("peak_rss_kib") not in
                (None, "", "UNAVAILABLE")
            )
        ]
        wall = sum(wall_values) if wall_values else None
        return {
            "wall_seconds": wall,
            "cpu_seconds": sum(cpu_values) if cpu_values else None,
            "peak_rss_kib": max(rss_values) if rss_values else None,
            "analyses_per_second": (
                len(results) / wall if wall is not None and wall > 0 else None
            ),
            "measurement_source": "PERSISTED_CHILD_ATTEMPTS",
            "unavailable_values_are_null": True,
        }

    def _load_or_reconstruct_run_metric(
        self, *, run_id: str, child_root: Path,
        identity: Mapping[str, Any],
    ) -> Dict[str, Any]:
        metric_path = self.root / "run_metrics" / f"{run_id}.json"
        if metric_path.is_file():
            metric = _json_object(metric_path, "formal child run metric")
            if metric.get("run_id") != run_id:
                raise Core5FormalContractError(
                    f"run metric identity mismatch: {run_id}"
                )
            return metric
        metric = {
            "run_id": run_id,
            **identity,
            **self._reconstruct_child_metrics(child_root),
        }
        atomic_write_json(metric_path, metric)
        return metric

    def run(
        self, *, resume: bool = False, max_cells: int | None = None,
        max_tasksets: int | None = None,
    ) -> Mapping[str, Any]:
        if max_cells is not None or max_tasksets is not None:
            raise Core5FormalContractError(
                "formal CORE-5 execution cannot be truncated; use --dry-run for inspection"
            )
        completed, terminal_count, phase = self._initialize(resume=resume)
        if (
            phase == "COMPLETED"
            and (self.root / "formal_summary.json").is_file()
        ):
            return analyze_core5_formal_artifacts(self.root)
        if self.profile == CORE5A_PROFILE:
            summary = self._run_core5a(
                resume=resume, completed=set(completed),
                terminal_count=terminal_count,
            )
        else:
            summary = self._run_core5b(
                resume=resume, completed=set(completed),
                terminal_count=terminal_count,
            )
        atomic_write_json(self.root / "formal_summary.json", summary)
        self._write_checkpoint(
            phase="FINALIZING", completed_run_ids=summary["completed_run_ids"],
            terminal_count=summary["solver_execution_count"], p0=False,
        )
        write_core5_hash_manifest(self.root)
        try:
            validate_core5_hash_manifest(
                self.root, require_completed_files=False
            )
        except Core5ContractError as exc:
            raise Core5FormalContractError(
                "CORE-5 formal parent hash manifest validation failed"
            ) from exc
        self._write_checkpoint(
            phase="COMPLETED", completed_run_ids=summary["completed_run_ids"],
            terminal_count=summary["solver_execution_count"], p0=False,
        )
        return analyze_core5_formal_artifacts(self.root)

    def _run_core5a(
        self, *, resume: bool, completed: set[str], terminal_count: int,
    ) -> Dict[str, Any]:
        cells = expand_core5a_cells(self.config)
        completed_ids = set(completed)
        expected_ids = {cell.cell_id for cell in cells}
        expected_per_child = (
            self.config["grid"]["tasksets_per_cell"]
            * len(self.config["analysis"]["variants"])
        )
        if not completed_ids.issubset(expected_ids):
            raise Core5FormalContractError(
                "CORE-5A checkpoint contains an unknown child run"
            )
        if terminal_count != len(completed_ids) * expected_per_child:
            raise Core5FormalContractError(
                "CORE-5A checkpoint terminal count is inconsistent"
            )
        metric_by_run: Dict[str, Dict[str, Any]] = {}
        for cell in cells:
            run_id = cell.cell_id
            child = self._child_config(
                run_id=run_id, processors=cell.processors,
                task_count=cell.task_count, period_min=cell.period_min,
                period_max=cell.period_max,
                utilizations=[fraction_text(cell.utilization)],
                tasksets=self.config["grid"]["tasksets_per_cell"],
                worker_count=1,
            )
            child_root = Path(child["execution"]["output_root"])
            evidence = inspect_formal_child(
                child, expected_request_count=expected_per_child
            )
            already_accounted = run_id in completed_ids
            if already_accounted and evidence.state != "COMPLETED":
                raise Core5FormalContractError(
                    f"CORE-5A checkpoint child is not complete: {run_id}"
                )
            if evidence.state == "COMPLETED":
                metrics = self._load_or_reconstruct_run_metric(
                    run_id=run_id, child_root=child_root,
                    identity=cell.row(),
                )
                if not already_accounted:
                    terminal_count += evidence.terminal
                    completed_ids.add(run_id)
                    self._write_checkpoint(
                        phase="RUNNING", completed_run_ids=completed_ids,
                        terminal_count=terminal_count, p0=False,
                    )
                metric_by_run[run_id] = metrics
                continue

            service = prepare_service_curve(
                child, child_root / "service_material"
            )
            store = None
            if cell.scaling_axis == "time_scale":
                from copy import deepcopy

                base_config = deepcopy(child)
                base_config["generation"]["period_min"] = 40
                base_config["generation"]["period_max"] = 200
                base_cell = expand_cells(base_config)[0]
                base_store = TasksetStore(
                    Path(self.config["execution"]["taskset_store"])
                    / "time_scale_sources" / cell.source_family_id,
                    base_config, service,
                )
                store = ExactTimeScaleStoreView(
                    base_store, base_cell, cell.exact_time_scale,
                    Path(self.config["execution"]["taskset_store"])
                    / "time_scaled",
                )
            outcome, metrics = self._run_child(
                child, resume=evidence.state == "RESUME",
                service=service, store=store,
            )
            if outcome.stopped:
                self._write_checkpoint(
                    phase="INTERRUPTED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count, p0=False,
                )
                raise Core5FormalContractError(
                    f"CORE-5A child interrupted; resume required: {run_id}"
                )
            if outcome.terminal != outcome.requested:
                self._write_checkpoint(
                    phase="STOPPED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count, p0=True,
                )
                raise Core5FormalContractError(
                    f"P0 CORE-5A incomplete child: {run_id}"
                )
            try:
                validation = validate_core5_child_evidence(
                    child_root, outcome
                )
            except Core5ContractError as exc:
                self._write_checkpoint(
                    phase="STOPPED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count, p0=True,
                )
                raise Core5FormalContractError(
                    f"P0 CORE-5A invalid child evidence: {run_id}"
                ) from exc
            if len(validation["request_ids"]) != expected_per_child:
                raise Core5FormalContractError(
                    f"CORE-5A child request count mismatch: {run_id}"
                )
            terminal_count += outcome.terminal
            completed_ids.add(run_id)
            metric_row = {
                "run_id": run_id, **cell.row(), **metrics,
                "measurement_source": "PARENT_OBSERVED_CHILD_RUN",
                "unavailable_values_are_null": True,
            }
            metric_by_run[run_id] = metric_row
            atomic_write_json(
                self.root / "run_metrics" / f"{run_id}.json", metric_row
            )
            self._write_checkpoint(
                phase="RUNNING", completed_run_ids=completed_ids,
                terminal_count=terminal_count, p0=False,
            )
        plan = self.describe()
        if terminal_count != plan["solver_execution_count"]:
            raise Core5FormalContractError("CORE-5A terminal count mismatch")
        persisted_metrics = reconstruct_core5a_metrics(
            self.root, sorted(completed_ids)
        )
        atomic_write_json(
            self.root / "core5a_metrics.json", persisted_metrics
        )
        return {
            "schema": "ASAP_BLOCK_V9_3_CORE5A_FORMAL_SUMMARY_V1",
            "profile": self.profile,
            "mathematical_request_count": plan["mathematical_request_count"],
            "solver_execution_count": terminal_count,
            "completed_run_ids": sorted(completed_ids),
            "parallel_throughput_is_not_algorithmic_complexity": True,
            "persisted_metrics_file": "core5a_metrics.json",
            "unavailable_values_are_null": True,
            "run_metrics": [
                metric_by_run[cell.cell_id] for cell in cells
            ],
        }

    @staticmethod
    def _worker_semantic_rows(
        child_root: Path, scheduled: Mapping[str, int],
    ) -> list[Dict[str, Any]]:
        task_rows = read_csv(child_root / "per_task_results.csv")
        tasks_by_analysis: Dict[str, list[Mapping[str, str]]] = {}
        for row in task_rows:
            tasks_by_analysis.setdefault(row["analysis_id"], []).append(row)
        semantic_rows = []
        for result in read_csv(child_root / "per_taskset_results.csv"):
            task_signature = [
                {
                    key: row.get(key, "")
                    for key in (
                        "task_id", "task_solver_status",
                        "candidate_response_time", "checked_w_count",
                        "checked_h_count", "checked_q_count",
                        "envelope_call_count",
                    )
                }
                for row in sorted(
                    tasks_by_analysis[result["analysis_id"]],
                    key=lambda value: int(value["task_id"]),
                )
            ]
            signature = {
                "input_hash": domain_hash(
                    "ASAP_BLOCK:V9.3:CORE5B:OBSERVED_INPUT:v1",
                    {
                        "taskset_hash": result["taskset_hash"],
                        "variant": result["analysis_variant"],
                        "exact_e0": result["exact_e0"],
                    },
                ),
                "terminal_class": result["solver_status"],
                "response_bound": canonical_json([
                    row["candidate_response_time"] for row in task_signature
                ]),
                "fixed_point_iterations": None,
                "search_states": canonical_json([{
                    "checked_w_count": row["checked_w_count"],
                    "checked_h_count": row["checked_h_count"],
                    "checked_q_count": row["checked_q_count"],
                } for row in task_signature]),
                "inverse_service_queries": canonical_json([
                    row["envelope_call_count"] for row in task_signature
                ]),
                "candidate_count": (
                    int(result["n_tasks_candidate_found"])
                    if result.get("n_tasks_candidate_found") not in
                    (None, "", "UNAVAILABLE") else None
                ),
            }
            semantic_rows.append({
                "mathematical_request_id": result["analysis_id"],
                "worker_count": scheduled["worker_count"],
                "repetition": scheduled["repetition"],
                **signature,
            })
        return semantic_rows

    def _run_core5b(
        self, *, resume: bool, completed: set[str], terminal_count: int,
    ) -> Dict[str, Any]:
        schedule = core5b_execution_schedule(self.config)
        completed_ids = set(completed)
        expected_ids = {
            f"w{row['worker_count']}-r{row['repetition']}"
            for row in schedule
        }
        expected_per_child = self.describe()["mathematical_request_count"]
        if not completed_ids.issubset(expected_ids):
            raise Core5FormalContractError(
                "CORE-5B checkpoint contains an unknown child run"
            )
        if terminal_count != len(completed_ids) * expected_per_child:
            raise Core5FormalContractError(
                "CORE-5B checkpoint terminal count is inconsistent"
            )
        semantic_rows = []
        run_metrics = []
        baseline_throughput: Dict[int, list[float]] = {}
        for scheduled in schedule:
            run_id = f"w{scheduled['worker_count']}-r{scheduled['repetition']}"
            child = self._child_config(
                run_id=run_id, processors=4, task_count=10,
                period_min=40, period_max=200,
                utilizations=self.config["scalability"]["utilization_points"],
                tasksets=self.config["grid"]["tasksets_per_cell"],
                worker_count=scheduled["worker_count"],
            )
            child_root = Path(child["execution"]["output_root"])
            evidence = inspect_formal_child(
                child, expected_request_count=expected_per_child
            )
            already_accounted = run_id in completed_ids
            if already_accounted and evidence.state != "COMPLETED":
                raise Core5FormalContractError(
                    f"CORE-5B checkpoint child is not complete: {run_id}"
                )
            if evidence.state == "COMPLETED":
                metric_row = self._load_or_reconstruct_run_metric(
                    run_id=run_id, child_root=child_root,
                    identity=scheduled,
                )
                run_metrics.append(metric_row)
                throughput = metric_row.get("analyses_per_second")
                if throughput is not None:
                    baseline_throughput.setdefault(
                        scheduled["worker_count"], []
                    ).append(float(throughput))
                semantic_rows.extend(self._worker_semantic_rows(
                    child_root, scheduled
                ))
                if not already_accounted:
                    terminal_count += evidence.terminal
                    completed_ids.add(run_id)
                    self._write_checkpoint(
                        phase="RUNNING", completed_run_ids=completed_ids,
                        terminal_count=terminal_count, p0=False,
                    )
                continue
            outcome, metrics = self._run_child(
                child, resume=evidence.state == "RESUME"
            )
            if outcome.stopped:
                self._write_checkpoint(
                    phase="INTERRUPTED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count, p0=False,
                )
                raise Core5FormalContractError(
                    f"CORE-5B child interrupted; resume required: {run_id}"
                )
            if outcome.terminal != outcome.requested:
                self._write_checkpoint(
                    phase="STOPPED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count, p0=True,
                )
                raise Core5FormalContractError(
                    f"P0 CORE-5B incomplete child: {run_id}"
                )
            try:
                validation = validate_core5_child_evidence(
                    child_root, outcome
                )
            except Core5ContractError as exc:
                self._write_checkpoint(
                    phase="STOPPED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count, p0=True,
                )
                raise Core5FormalContractError(
                    f"P0 CORE-5B invalid child evidence: {run_id}"
                ) from exc
            if len(validation["request_ids"]) != expected_per_child:
                raise Core5FormalContractError(
                    f"CORE-5B child request count mismatch: {run_id}"
                )
            semantic_rows.extend(
                self._worker_semantic_rows(child_root, scheduled)
            )
            terminal_count += outcome.terminal
            completed_ids.add(run_id)
            throughput = metrics["analyses_per_second"]
            if throughput is not None:
                baseline_throughput.setdefault(
                    scheduled["worker_count"], []
                ).append(float(throughput))
            metric_row = {
                "run_id": run_id, **scheduled, **metrics,
                "measurement_source": "PARENT_OBSERVED_CHILD_RUN",
                "unavailable_values_are_null": True,
            }
            run_metrics.append(metric_row)
            atomic_write_json(
                self.root / "run_metrics" / f"{run_id}.json", metric_row
            )
            self._write_checkpoint(
                phase="RUNNING", completed_run_ids=completed_ids,
                terminal_count=terminal_count, p0=False,
            )
        plan = self.describe()
        if terminal_count != plan["solver_execution_count"]:
            raise Core5FormalContractError("CORE-5B execution count mismatch")
        first_child_root = Path(self._child_config(
            run_id=(
                f"w{schedule[0]['worker_count']}-r{schedule[0]['repetition']}"
            ),
            processors=4, task_count=10, period_min=40, period_max=200,
            utilizations=self.config["scalability"]["utilization_points"],
            tasksets=self.config["grid"]["tasksets_per_cell"],
            worker_count=schedule[0]["worker_count"],
        )["execution"]["output_root"])
        expected_request_ids = sorted(
            row["analysis_id"] for row in read_csv(
                first_child_root / "analysis_requests.csv"
            )
        )
        checks = build_worker_semantic_checks(
            semantic_rows, expected_request_ids=expected_request_ids,
            worker_counts=self.config["scalability"]["worker_counts"],
            repetitions_per_worker=self.config["scalability"][
                "repetitions_per_worker"
            ],
        )
        write_csv(
            self.root / "worker_semantic_checks.csv",
            CORE5B_WORKER_CHECK_COLUMNS, checks,
        )
        one_values = baseline_throughput.get(1, [])
        one = statistics.median(one_values) if one_values else None
        worker_summary = []
        for worker in self.config["scalability"]["worker_counts"]:
            values = baseline_throughput.get(worker, [])
            throughput = statistics.median(values) if values else None
            speedup = (
                throughput / one
                if throughput is not None and one is not None and one > 0
                else None
            )
            worker_summary.append({
                "worker_count": worker,
                "median_analyses_per_second": throughput,
                "speedup": speedup,
                "parallel_efficiency": (
                    speedup / worker if speedup is not None else None
                ),
            })
        return {
            "schema": "ASAP_BLOCK_V9_3_CORE5B_FORMAL_SUMMARY_V1",
            "profile": self.profile,
            "mathematical_request_count": plan["mathematical_request_count"],
            "solver_execution_count": terminal_count,
            "completed_run_ids": sorted(completed_ids),
            "worker_semantic_mismatch_count": 0,
            "worker_semantic_check_count": len(checks),
            "worker_semantic_checks_file": "worker_semantic_checks.csv",
            "unavailable_values_are_null": True,
            "single_request_runtime_excluded_from_algorithmic_regression": True,
            "run_metrics": run_metrics,
            "worker_summary": worker_summary,
        }


def analyze_core5_formal_artifacts(root: Path | str) -> Mapping[str, Any]:
    """Reconstruct and validate a completed formal run from child evidence."""

    root = Path(root)
    metadata = _json_object(root / "run_metadata.json", "formal run metadata")
    checkpoint = _json_object(root / "checkpoint.json", "formal checkpoint")
    summary = _json_object(root / "formal_summary.json", "formal summary")
    seal = _json_object(
        root / "formal_authorization_seal.json", "formal authorization seal"
    )
    if metadata.get("schema") != CORE5_FORMAL_RUN_SCHEMA:
        raise Core5FormalContractError("formal analyzer rejects non-formal run schema")
    if checkpoint.get("schema") != CORE5_FORMAL_CHECKPOINT_SCHEMA:
        raise Core5FormalContractError("formal analyzer rejects checkpoint schema")
    config = load_config(root / "run_config.yaml", expected_core="CORE-5")
    profile = config["scalability"].get("profile")
    if profile not in {CORE5A_PROFILE, CORE5B_PROFILE}:
        raise Core5FormalContractError("formal analyzer rejects bounded profile")
    if metadata.get("formal_large_scale_run") is not True:
        raise Core5FormalContractError(
            "formal analyzer requires formal_large_scale_run=true"
        )
    try:
        validate_core5_hash_manifest(
            root, require_completed_files=False
        )
    except Core5ContractError as exc:
        raise Core5FormalContractError(
            "formal analyzer rejects parent file hash manifest"
        ) from exc
    runner = Core5FormalRunner(config)
    plan = runner.describe()
    try:
        revalidate_authorization_seal(
            config, seal, project_root=Path(__file__).resolve().parents[2]
        )
    except FormalAuthorizationError as exc:
        raise Core5FormalContractError(
            "formal analyzer rejects authorization-seal mismatch"
        ) from exc
    runner._authorization_seal = dict(seal)
    if (
        metadata.get("profile") != profile
        or checkpoint.get("profile") != profile
        or summary.get("profile") != profile
        or metadata.get("config_hash") != config_hash(config)
        or checkpoint.get("config_hash") != config_hash(config)
        or metadata.get("plan_hash") != plan["plan_hash"]
        or checkpoint.get("plan_hash") != plan["plan_hash"]
        or metadata.get("formal_authorization_id")
        != seal.get("authorization_id")
    ):
        raise Core5FormalContractError("CORE-5 formal profile/config/plan mismatch")
    if checkpoint.get("phase") != "COMPLETED":
        raise Core5FormalContractError("formal analyzer requires a completed run")
    if checkpoint.get("p0") is not False:
        raise Core5FormalContractError("formal analyzer rejects a P0 checkpoint")
    for field in ("mathematical_request_count", "solver_execution_count"):
        if summary.get(field) != plan[field]:
            raise Core5FormalContractError(f"formal count mismatch: {field}")
    if checkpoint.get("terminal_count") != plan["solver_execution_count"]:
        raise Core5FormalContractError(
            "formal checkpoint terminal count mismatch"
        )
    if profile == CORE5A_PROFILE:
        cells = expand_core5a_cells(config)
        expected_runs = {}
        for cell in cells:
            expected_runs[cell.cell_id] = {
                "child": runner._child_config(
                    run_id=cell.cell_id, processors=cell.processors,
                    task_count=cell.task_count, period_min=cell.period_min,
                    period_max=cell.period_max,
                    utilizations=[fraction_text(cell.utilization)],
                    tasksets=config["grid"]["tasksets_per_cell"],
                    worker_count=1,
                ),
                "expected_request_count": (
                    config["grid"]["tasksets_per_cell"]
                    * len(config["analysis"]["variants"])
                ),
            }
    else:
        schedule = core5b_execution_schedule(config)
        expected_runs = {}
        for row in schedule:
            run_id = f"w{row['worker_count']}-r{row['repetition']}"
            expected_runs[run_id] = {
                "scheduled": row,
                "child": runner._child_config(
                    run_id=run_id, processors=4, task_count=10,
                    period_min=40, period_max=200,
                    utilizations=config["scalability"]["utilization_points"],
                    tasksets=config["grid"]["tasksets_per_cell"],
                    worker_count=row["worker_count"],
                ),
                "expected_request_count": plan["mathematical_request_count"],
            }
    completed = summary.get("completed_run_ids")
    if (
        not isinstance(completed, list)
        or set(completed) != set(expected_runs)
        or set(checkpoint.get("completed_run_ids", [])) != set(expected_runs)
    ):
        raise Core5FormalContractError(
            "formal analyzer rejects missing worker/cell/repetition child"
        )
    child_runs_root = root / "child_runs"
    actual_child_ids = {
        path.name for path in child_runs_root.iterdir() if path.is_dir()
    } if child_runs_root.is_dir() else set()
    if actual_child_ids != set(expected_runs):
        raise Core5FormalContractError(
            "formal analyzer rejects unexpected or missing child directories"
        )

    reconstructed_terminal_count = 0
    semantic_rows: list[Dict[str, Any]] = []
    request_sets: list[set[str]] = []
    for run_id, expected in expected_runs.items():
        child_root = root / "child_runs" / run_id
        evidence = inspect_formal_child(
            expected["child"],
            expected_request_count=expected["expected_request_count"],
        )
        if evidence.state != "COMPLETED":
            raise Core5FormalContractError(
                f"formal analyzer requires complete child evidence: {run_id}"
            )
        reconstructed_terminal_count += evidence.terminal
        child_request_ids = {
            row["analysis_id"] for row in read_csv(
                child_root / "analysis_requests.csv"
            )
        }
        request_sets.append(child_request_ids)
        if profile == CORE5B_PROFILE:
            semantic_rows.extend(runner._worker_semantic_rows(
                child_root, expected["scheduled"]
            ))
    if reconstructed_terminal_count != plan["solver_execution_count"]:
        raise Core5FormalContractError(
            "formal analyzer reconstructed execution count mismatch"
        )

    if profile == CORE5A_PROFILE:
        persisted_metrics = _json_object(
            root / "core5a_metrics.json", "CORE-5A persisted metrics"
        )
        reconstructed_metrics = reconstruct_core5a_metrics(
            root, sorted(expected_runs)
        )
        if persisted_metrics != reconstructed_metrics:
            raise Core5FormalContractError(
                "formal analyzer rejects CORE-5A persisted metric mismatch"
            )
        if (
            summary.get("persisted_metrics_file") != "core5a_metrics.json"
            or summary.get("unavailable_values_are_null") is not True
        ):
            raise Core5FormalContractError(
                "formal analyzer rejects CORE-5A metric declaration"
            )
    else:
        if (
            not request_sets
            or len(request_sets[0]) != plan["mathematical_request_count"]
            or any(values != request_sets[0] for values in request_sets[1:])
        ):
            raise Core5FormalContractError(
                "formal analyzer rejects CORE-5B child request-set mismatch"
            )
        reconstructed_checks = build_worker_semantic_checks(
            semantic_rows,
            expected_request_ids=sorted(request_sets[0]),
            worker_counts=config["scalability"]["worker_counts"],
            repetitions_per_worker=config["scalability"][
                "repetitions_per_worker"
            ],
        )
        checks_path = root / "worker_semantic_checks.csv"
        try:
            validate_csv_header(checks_path, CORE5B_WORKER_CHECK_COLUMNS)
        except ResultWriterError as exc:
            raise Core5FormalContractError(
                "formal analyzer rejects worker semantic check schema"
            ) from exc
        expected_csv_rows = [{
            column: "" if row.get(column) is None else str(row.get(column))
            for column in CORE5B_WORKER_CHECK_COLUMNS
        } for row in reconstructed_checks]
        if read_csv(checks_path) != expected_csv_rows:
            raise Core5FormalContractError(
                "formal analyzer rejects persisted worker semantic checks"
            )
        if (
            len(reconstructed_checks) != plan["mathematical_request_count"]
            or summary.get("worker_semantic_check_count")
            != plan["mathematical_request_count"]
            or summary.get("worker_semantic_mismatch_count") != 0
            or summary.get("worker_semantic_checks_file")
            != "worker_semantic_checks.csv"
        ):
            raise Core5FormalContractError(
                "formal analyzer rejects CORE-5B semantic summary"
            )
    return summary
