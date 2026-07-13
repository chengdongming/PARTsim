#!/usr/bin/env python3
"""Run ASAP-BLOCK v9.3 Pilot-2 timeout and tightness diagnostics."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asap_block_rta as legacy_rta
import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner
from scripts import run_v9_3_pilot as pilot1


VARIANTS = production_runner.VARIANT_ORDER
TIMEOUT_REQUEST_COLUMNS = (
    "request_id", "analysis_id", "taskset_id", "generation_seed", "U_norm", "E0",
    "analysis_variant", "original_timeout_seconds", "original_solver_status",
    "original_certification_status", "original_first_failed_priority",
    "original_candidate_vector", "original_task_status_vector", "checked_w_count",
    "checked_h_count", "checked_q_count", "envelope_calls", "task_input_hash",
)
RERUN_COLUMNS = (
    "purpose", "request_id", "analysis_id", "taskset_id", "generation_seed", "U_norm",
    "E0", "analysis_variant", "budget_seconds", "solver_status", "certification_status",
    "taskset_proven", "first_failed_priority", "candidate_vector", "task_status_vector",
    "checked_w_count", "checked_h_count", "checked_q_count", "envelope_calls",
    "worker_startup_seconds", "solver_wall_seconds", "solver_cpu_seconds",
    "serialization_seconds", "deserialization_seconds", "transport_and_exit_seconds",
    "total_wall_seconds", "outer_timeout", "outcome_matches_original",
    "candidate_matches_original", "certification_matches_original", "error_code",
)
BASELINE_DIAGNOSTIC_COLUMNS = (
    "record_type", "relation", "taskset_id", "U_norm", "E0", "task_id",
    "priority_rank", "carry_in_vector_hash", "w", "h", "q", "q_plus_h_equals_w",
    "complete_envelope", "local_envelope", "envelope_relation", "complete_closure",
    "local_closure", "local_only_closure", "complete_solver_status",
    "local_solver_status", "complete_candidate", "local_candidate",
    "response_relation", "original_complete_candidate", "original_local_candidate",
    "diagnostic_timeout_seconds",
)
SCREENING_TASKSET_COLUMNS = (
    "phase", "structure", "structure_alias_of", "deadline_mode", "power_mode",
    "cell_id", "U_norm", "taskset_index", "generation_seed", "taskset_id",
    "actual_total_utilization", "task_count", "distinct_power_count",
    "taskset_semantic_hash", "priority_rank_hash", "power_vector_hash",
    "generation_runtime_seconds", "task_input_json",
)
SCREENING_RESULT_COLUMNS = (
    "phase", "structure", "structure_alias_of", "deadline_mode", "power_mode",
    "cell_id", "U_norm", "taskset_index", "generation_seed", "taskset_id",
    "request_id", "analysis_id", "analysis_variant", "solver_status",
    "certification_status", "taskset_proven", "first_failed_priority",
    "candidate_vector", "task_status_vector", "candidate_found_task_count",
    "certified_task_count", "checked_w_count", "checked_h_count", "checked_q_count",
    "envelope_calls", "worker_startup_seconds", "solver_wall_seconds",
    "solver_cpu_seconds", "serialization_seconds", "deserialization_seconds",
    "transport_and_exit_seconds", "total_wall_seconds", "timeout", "numeric_error",
    "internal_error", "not_applicable", "dependency_status", "dominance_status",
    "source_vector_hash", "target_carry_in_vector_hash",
)
SCREENING_TIGHTNESS_COLUMNS = (
    "phase", "structure", "cell_id", "U_norm", "taskset_id", "relation", "task_id",
    "priority_rank", "complete_candidate", "local_candidate", "improvement", "status",
    "envelope_common_count", "envelope_strict_count", "envelope_equal_count",
    "envelope_violation_count", "local_only_closure_count", "q_plus_h_equals_w_count",
)
FAILURE_COLUMNS = (
    "severity", "stage", "taskset_id", "analysis_variant", "code", "detail",
    "input_file",
)


class Pilot2Error(RuntimeError):
    """Fail-closed Pilot-2 error."""


@dataclass(frozen=True)
class TimedExecution:
    result: Optional[taskset.TasksetAnalysisResult]
    worker_startup_seconds: float
    solver_wall_seconds: float
    solver_cpu_seconds: float
    serialization_seconds: float
    deserialization_seconds: float
    transport_and_exit_seconds: float
    total_wall_seconds: float
    outer_timeout: bool
    error_code: Optional[str] = None
    error_detail: Optional[str] = None


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _fraction(value: str) -> Fraction:
    return Fraction(str(value))


def _fraction_text(value: Optional[Fraction]) -> Optional[str]:
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _runtime(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        raise Pilot2Error("runtime must be finite and non-negative")
    return format(value, ".9f")


def _bool(value: str) -> bool:
    return str(value).strip().lower() == "true"


def load_config(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict) or config.get("pilot_version") != 2:
        raise Pilot2Error("Pilot-2 config must be a version-2 mapping")
    if config.get("rta_version") != "v9.3" or str(config.get("contract_version")) != "1.3.12":
        raise Pilot2Error("Pilot-2 requires v9.3 and v1.3.12")
    if config["analysis"]["variants"] != [variant.name for variant in VARIANTS]:
        raise Pilot2Error("five variants or their order changed")
    if config["analysis"]["max_workers"] != 1 or config["timeout_sensitivity"]["max_workers"] != 1:
        raise Pilot2Error("Pilot-2 must remain single-worker")
    screening = config["screening"]
    frozen = {
        "task_n": 10, "M": 4, "E0": 1, "normalized_utilizations": [0.4, 0.6],
        "tasksets_per_cell": 5, "base_seed": 930112,
        "confirmation_base_seed": 930212, "confirmation_tasksets_per_cell": 20,
    }
    for key, expected in frozen.items():
        if screening.get(key) != expected:
            raise Pilot2Error(f"screening.{key} must be {expected!r}")
    structures = screening.get("structures")
    if [entry.get("id") for entry in structures] != ["S1", "S2", "S3", "S4"]:
        raise Pilot2Error("screening structures must be ordered S1..S4")
    if [entry.get("deadline_mode") for entry in structures] != [
        "implicit", "constrained", "implicit", "constrained"
    ]:
        raise Pilot2Error("screening deadline structures changed")
    if config["timeout_sensitivity"]["budgets_seconds"] != [30, 60]:
        raise Pilot2Error("timeout budgets must be 30 then 60 seconds")
    generator_text = (PROJECT_ROOT / "global_task_generator.py").read_text(encoding="utf-8")
    if "--constrained-deadlines" not in generator_text:
        raise Pilot2Error("generator constrained-deadline mode is unavailable")
    return config


def _timed_worker(connection: Any, request: production_runner.V93DispatchRequest) -> None:
    try:
        connection.send(("started", time.perf_counter()))
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        try:
            result = production_runner.dispatch_rta_version(
                production_runner.V93_DISPATCH_VERSION, v93_request=request
            )
            payload = ("ok", result, None, None)
        except BaseException as exc:
            payload = ("error", None, type(exc).__name__, str(exc))
        solver_cpu = time.process_time() - cpu_started
        solver_wall = time.perf_counter() - wall_started
        serialization_started = time.perf_counter()
        blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        serialization_wall = time.perf_counter() - serialization_started
        connection.send(("finished", blob, solver_wall, solver_cpu, serialization_wall))
    finally:
        connection.close()


def execute_analysis(
    request: production_runner.V93DispatchRequest, budget_seconds: float,
) -> TimedExecution:
    """Execute one production request and expose operational timing components."""

    context = multiprocessing.get_context("fork")
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(target=_timed_worker, args=(sending, request))
    parent_started = time.perf_counter()
    process.start()
    sending.close()
    deadline = parent_started + float(budget_seconds) + 5.0
    marker_time = None
    finished = None
    try:
        remaining = max(0.0, deadline - time.perf_counter())
        if receiving.poll(remaining):
            marker = receiving.recv()
            if marker[0] == "started":
                marker_time = float(marker[1])
        remaining = max(0.0, deadline - time.perf_counter())
        if marker_time is not None and receiving.poll(remaining):
            finished = receiving.recv()
    finally:
        receiving.close()
    if finished is None:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        total = time.perf_counter() - parent_started
        return TimedExecution(
            None, max(0.0, (marker_time or parent_started) - parent_started), 0.0, 0.0,
            0.0, 0.0, max(0.0, total), total, True, "OUTER_TIMEOUT",
            "worker exceeded the hard parent timeout",
        )
    _, blob, solver_wall, solver_cpu, serialization_wall = finished
    deserialization_started = time.perf_counter()
    payload = pickle.loads(blob)
    deserialization_wall = time.perf_counter() - deserialization_started
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
    total = time.perf_counter() - parent_started
    startup = max(0.0, float(marker_time) - parent_started)
    known = startup + solver_wall + serialization_wall + deserialization_wall
    transport = max(0.0, total - known)
    if payload[0] == "ok":
        return TimedExecution(
            payload[1], startup, solver_wall, solver_cpu, serialization_wall,
            deserialization_wall, transport, total, False,
        )
    return TimedExecution(
        None, startup, solver_wall, solver_cpu, serialization_wall,
        deserialization_wall, transport, total, False, payload[2], payload[3],
    )


def _reconstruct_generated(row: Mapping[str, str]) -> pilot1.GeneratedTaskset:
    payload = tuple(json.loads(row["task_input_json"]))
    tasks = tuple(
        core.V93Task(
            str(item["task_id"]), int(item["C"]), int(item["D"]), int(item["T"]),
            Fraction(str(item["P"])),
        )
        for item in payload
    )
    return pilot1.GeneratedTaskset(
        int(row["generation_seed"]), row["taskset_id"], row["U_norm"],
        int(row["U_norm_index"]), int(row["E0"]), int(row["E0_index"]),
        int(row["taskset_index"]), _fraction(row["target_total_utilization"]),
        _fraction(row["actual_total_utilization"]), row["taskset_semantic_hash"],
        row["priority_rank_hash"], row["power_vector_hash"], tasks, payload,
        float(row["generation_runtime_seconds"]),
    )


def _baseline_context(config: Mapping[str, Any]) -> Dict[str, Any]:
    root = PROJECT_ROOT / config["baseline"]["artifact_root"]
    generated_rows = _read_csv(root / "generated_tasksets.csv")
    taskset_rows = _read_csv(root / "per_taskset_results.csv")
    task_rows = _read_csv(root / "per_task_results.csv")
    canonical_rows = _read_csv(root / "canonical_v1_3_12" / "per_taskset_results.csv")
    expected_tasksets = int(config["baseline"]["expected_tasksets"])
    expected_analyses = int(config["baseline"]["expected_analyses"])
    if len(generated_rows) != expected_tasksets or len(taskset_rows) != expected_analyses:
        raise Pilot2Error("first-pilot input/result cardinality mismatch")
    counts = defaultdict(int)
    for row in taskset_rows:
        counts[(row["taskset_id"], row["analysis_variant"])] += 1
    if len(counts) != expected_analyses or any(value != 1 for value in counts.values()):
        raise Pilot2Error("first-pilot variants are missing or duplicated")
    timeouts = [row for row in taskset_rows if _bool(row["timeout"])]
    if len(timeouts) != int(config["baseline"]["expected_timeouts"]):
        raise Pilot2Error("first-pilot timeout count is not exactly 27")
    generated = {row["taskset_id"]: _reconstruct_generated(row) for row in generated_rows}
    tasksets = {(row["taskset_id"], row["analysis_variant"]): row for row in taskset_rows}
    tasks: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for row in task_rows:
        tasks[(row["taskset_id"], row["analysis_variant"])].append(row)
    for rows in tasks.values():
        rows.sort(key=lambda item: int(item["priority_rank"]))
    requests = {row["analysis_run_id"]: row["request_id"] for row in canonical_rows}
    pilot_config = pilot1.load_pilot_config(root / "pilot_config.yaml")
    beta, beta_hash, _ = pilot1._build_exact_service_curve(
        root / "pilot_system_config.yaml", pilot_config
    )
    config_hash = pilot1._sha256_file(root / "pilot_config.yaml")
    return {
        "root": root, "generated_rows": generated_rows, "generated": generated,
        "taskset_rows": taskset_rows, "tasksets": tasksets, "tasks": tasks,
        "request_ids": requests, "beta": beta, "beta_hash": beta_hash,
        "config_hash": config_hash, "pilot_config": pilot_config,
    }


def _task_vectors(rows: Sequence[Mapping[str, str]]) -> Tuple[str, str]:
    candidates = [
        [row["task_id"], int(row["candidate_response_time"]) if row["candidate_response_time"] else None]
        for row in rows
    ]
    statuses = [
        [row["task_id"], row["task_solver_status"], row["task_certification_status"]]
        for row in rows
    ]
    return (
        json.dumps(candidates, separators=(",", ":")),
        json.dumps(statuses, separators=(",", ":")),
    )


def _result_vectors(result: taskset.TasksetAnalysisResult) -> Tuple[str, str]:
    candidates = [[record.task_id, record.candidate_response_time] for record in result.task_records]
    statuses = [
        [record.task_id, record.solver_status.value, record.certification_status.value]
        for record in result.task_records
    ]
    return (
        json.dumps(candidates, separators=(",", ":")),
        json.dumps(statuses, separators=(",", ":")),
    )


def _counter_totals(result: taskset.TasksetAnalysisResult) -> Tuple[int, int, int, int]:
    return tuple(
        sum(getattr(record, attribute) for record in result.task_records)
        for attribute in (
            "checked_w_count", "checked_h_count", "checked_q_count", "envelope_call_count"
        )
    )  # type: ignore[return-value]


def _baseline_counter_totals(rows: Sequence[Mapping[str, str]]) -> Tuple[int, int, int, int]:
    return tuple(
        sum(int(row[column] or 0) for row in rows)
        for column in ("checked_w_count", "checked_h_count", "checked_q_count", "envelope_calls")
    )  # type: ignore[return-value]


def _run_request(
    generated: pilot1.GeneratedTaskset, variant: taskset.AnalysisVariant,
    budget: float, context: Mapping[str, Any], analysis_id: str,
    source: Optional[taskset.TasksetAnalysisResult] = None,
) -> TimedExecution:
    inp = pilot1._analysis_input(
        generated, context["beta"], context["beta_hash"], context["config_hash"], budget
    )
    dependency = taskset.DependencyVectorCheckStatus.NOT_CHECKED
    if variant is taskset.AnalysisVariant.LOC_THETA_CW:
        dependency = (
            taskset.DependencyVectorCheckStatus.VALID
            if source is not None and production_runner._source_is_jointly_certified(source)
            else taskset.DependencyVectorCheckStatus.INVALID
        )
    request = production_runner.V93DispatchRequest(
        analysis_id, variant, inp, source=source,
        dependency_check_status=dependency, configuration_timeout_seconds=budget,
    )
    execution = execute_analysis(request, budget)
    if execution.outer_timeout:
        raise Pilot2Error("hard outer timeout exceeded the solver budget plus grace")
    if execution.result is None:
        raise Pilot2Error(f"analysis worker error: {execution.error_code}")
    pilot1.validate_analysis_result(execution.result, generated, source)
    return execution


def _select_controls(context: Mapping[str, Any], per_variant: int) -> List[Dict[str, str]]:
    selected = []
    for variant in VARIANTS:
        candidates = [
            row for row in context["taskset_rows"]
            if row["analysis_variant"] == variant.name
            and not _bool(row["timeout"])
            and row["solver_status"] != "NOT_APPLICABLE_DEPENDENCY"
        ]
        candidates.sort(
            key=lambda row: (
                row["solver_status"] != "COMPLETED", float(row["U_norm"]), int(row["E0"]),
                row["taskset_id"],
            )
        )
        if len(candidates) < per_variant:
            raise Pilot2Error(f"not enough non-timeout controls for {variant.name}")
        if per_variant == 1:
            chosen = [candidates[len(candidates) // 2]]
        else:
            chosen = [candidates[round(index * (len(candidates) - 1) / (per_variant - 1))]
                      for index in range(per_variant)]
        selected.extend(chosen)
    return selected


def _request_row(row: Mapping[str, str], context: Mapping[str, Any]) -> Dict[str, Any]:
    task_rows = context["tasks"][(row["taskset_id"], row["analysis_variant"])]
    candidates, statuses = _task_vectors(task_rows)
    counters = _baseline_counter_totals(task_rows)
    generated = context["generated"][row["taskset_id"]]
    return {
        "request_id": context["request_ids"][row["analysis_id"]],
        "analysis_id": row["analysis_id"], "taskset_id": row["taskset_id"],
        "generation_seed": row["generation_seed"], "U_norm": row["U_norm"], "E0": row["E0"],
        "analysis_variant": row["analysis_variant"],
        "original_timeout_seconds": 15, "original_solver_status": row["solver_status"],
        "original_certification_status": row["certification_status"],
        "original_first_failed_priority": row["first_failed_priority"],
        "original_candidate_vector": candidates, "original_task_status_vector": statuses,
        "checked_w_count": counters[0], "checked_h_count": counters[1],
        "checked_q_count": counters[2], "envelope_calls": counters[3],
        "task_input_hash": pilot1._domain_hash("ASAP_BLOCK:PILOT2:SAVED_INPUT:v9.3", generated.task_payload),
    }


def _attempt_row(
    purpose: str, baseline: Mapping[str, str], execution: TimedExecution,
    context: Mapping[str, Any], budget: int,
) -> Dict[str, Any]:
    result = execution.result
    if result is None:
        raise Pilot2Error("attempt row requires a result")
    task_rows = context["tasks"][(baseline["taskset_id"], baseline["analysis_variant"])]
    original_candidates, original_statuses = _task_vectors(task_rows)
    candidates, statuses = _result_vectors(result)
    counters = _counter_totals(result)
    certification_match = result.certification_status.value == baseline["certification_status"]
    candidate_match = candidates == original_candidates
    status_match = statuses == original_statuses
    return {
        "purpose": purpose, "request_id": context["request_ids"][baseline["analysis_id"]],
        "analysis_id": baseline["analysis_id"], "taskset_id": baseline["taskset_id"],
        "generation_seed": baseline["generation_seed"], "U_norm": baseline["U_norm"],
        "E0": baseline["E0"], "analysis_variant": baseline["analysis_variant"],
        "budget_seconds": budget, "solver_status": result.solver_status.value,
        "certification_status": result.certification_status.value,
        "taskset_proven": result.taskset_proven, "first_failed_priority": result.first_failed_priority,
        "candidate_vector": candidates, "task_status_vector": statuses,
        "checked_w_count": counters[0], "checked_h_count": counters[1],
        "checked_q_count": counters[2], "envelope_calls": counters[3],
        "worker_startup_seconds": _runtime(execution.worker_startup_seconds),
        "solver_wall_seconds": _runtime(execution.solver_wall_seconds),
        "solver_cpu_seconds": _runtime(execution.solver_cpu_seconds),
        "serialization_seconds": _runtime(execution.serialization_seconds),
        "deserialization_seconds": _runtime(execution.deserialization_seconds),
        "transport_and_exit_seconds": _runtime(execution.transport_and_exit_seconds),
        "total_wall_seconds": _runtime(execution.total_wall_seconds),
        "outer_timeout": execution.outer_timeout,
        "outcome_matches_original": candidate_match and certification_match and status_match,
        "candidate_matches_original": candidate_match,
        "certification_matches_original": certification_match,
        "error_code": execution.error_code,
    }


def run_timeout_sensitivity(
    config: Mapping[str, Any], output_root: Path, context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    context = context or _baseline_context(config)
    timeout_rows = [row for row in context["taskset_rows"] if _bool(row["timeout"])]
    timeout_rows.sort(key=lambda row: (float(row["U_norm"]), int(row["E0"]), row["taskset_id"], row["analysis_variant"]))
    request_rows = [_request_row(row, context) for row in timeout_rows]
    _write_csv(output_root / "timeout_requests.csv", TIMEOUT_REQUEST_COLUMNS, request_rows)
    controls = _select_controls(
        context, int(config["timeout_sensitivity"]["non_timeout_samples_per_variant"])
    )
    attempts: List[Dict[str, Any]] = []
    cache: Dict[Tuple[str, int, str], taskset.TasksetAnalysisResult] = {}

    def run_one(baseline: Mapping[str, str], budget: int, purpose: str) -> TimedExecution:
        generated = context["generated"][baseline["taskset_id"]]
        variant = taskset.AnalysisVariant[baseline["analysis_variant"]]
        source = None
        if variant is taskset.AnalysisVariant.LOC_THETA_CW:
            source_key = (generated.taskset_id, budget, taskset.AnalysisVariant.CW_THETA_CW.name)
            source = cache.get(source_key)
            if source is None:
                source_baseline = context["tasksets"][(generated.taskset_id, "CW_THETA_CW")]
                source_execution = _run_request(
                    generated, taskset.AnalysisVariant.CW_THETA_CW, budget, context,
                    source_baseline["analysis_id"],
                )
                source = source_execution.result
                cache[source_key] = source
        execution = _run_request(
            generated, variant, budget, context, baseline["analysis_id"], source=source
        )
        cache[(generated.taskset_id, budget, variant.name)] = execution.result
        attempts.append(_attempt_row(purpose, baseline, execution, context, budget))
        _write_csv(output_root / "timeout_reruns.csv", RERUN_COLUMNS, attempts)
        return execution

    for baseline in timeout_rows:
        execution = run_one(baseline, 30, "TIMEOUT_REQUEST")
        if execution.result.solver_status is taskset.AnalysisSolverStatus.TIMEOUT:
            run_one(baseline, 60, "TIMEOUT_REQUEST")
    for baseline in controls:
        run_one(baseline, 30, "NON_TIMEOUT_CONTROL")

    from scripts.analyze_v9_3_pilot2 import summarize_timeout
    summary = summarize_timeout(request_rows, attempts, config)
    _write_json(output_root / "timeout_summary.json", summary)
    return summary


def rerun_non_timeout_controls(
    config: Mapping[str, Any], output_root: Path,
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Replace only the control sample while preserving all 27 timeout attempts."""

    context = context or _baseline_context(config)
    attempts = [
        row for row in _read_csv(output_root / "timeout_reruns.csv")
        if row["purpose"] == "TIMEOUT_REQUEST"
    ]
    controls = _select_controls(
        context, int(config["timeout_sensitivity"]["non_timeout_samples_per_variant"])
    )
    cache: Dict[Tuple[str, int, str], taskset.TasksetAnalysisResult] = {}
    for baseline in controls:
        generated = context["generated"][baseline["taskset_id"]]
        variant = taskset.AnalysisVariant[baseline["analysis_variant"]]
        source = None
        if variant is taskset.AnalysisVariant.LOC_THETA_CW:
            source_baseline = context["tasksets"][(generated.taskset_id, "CW_THETA_CW")]
            source_execution = _run_request(
                generated, taskset.AnalysisVariant.CW_THETA_CW, 30, context,
                source_baseline["analysis_id"],
            )
            source = source_execution.result
            if source is None or not production_runner._source_is_jointly_certified(source):
                raise Pilot2Error("evaluated LOC_THETA_CW control source did not remain certified")
        execution = _run_request(
            generated, variant, 30, context, baseline["analysis_id"], source=source
        )
        attempts.append(_attempt_row(
            "NON_TIMEOUT_CONTROL", baseline, execution, context, 30
        ))
        _write_csv(output_root / "timeout_reruns.csv", RERUN_COLUMNS, attempts)
    requests = _read_csv(output_root / "timeout_requests.csv")
    from scripts.analyze_v9_3_pilot2 import summarize_timeout
    summary = summarize_timeout(requests, attempts, config)
    _write_json(output_root / "timeout_summary.json", summary)
    return summary


def _trace_search(
    generated: pilot1.GeneratedTaskset, rank: int, carry: Mapping[str, int],
    kind: core.EnvelopeKind, beta: Sequence[Fraction], timeout: float,
) -> Tuple[core.V93SearchResult, Dict[Tuple[int, int, int], Mapping[str, object]], set]:
    trace: Dict[Tuple[int, int, int], Mapping[str, object]] = {}
    closures = set()

    def observer(event: Mapping[str, object]) -> None:
        if event.get("event_type") != "Q_CHECK":
            return
        key = (int(event["w"]), int(event["h"]), int(event["q"]))
        trace[key] = dict(event)
        if event.get("h_result") == "CLOSED":
            closures.add((key[0], key[1]))

    tasks = generated.tasks
    result = core.canonical_closure_search_v9_3(
        kind, tasks[rank], tasks[:rank], tasks[rank + 1 :], 4, carry,
        Fraction(generated.e0), beta, timeout_seconds=timeout, trace_observer=observer,
    )
    return result, trace, closures


def _eligible_status(row: Mapping[str, str]) -> bool:
    return row["task_solver_status"] not in {
        "NOT_EVALUATED_AFTER_PREFIX_FAILURE", "NOT_APPLICABLE_DEPENDENCY"
    }


def _diagnose_pair_task(
    relation: str, generated: pilot1.GeneratedTaskset, rank: int,
    carry: Mapping[str, int], beta: Sequence[Fraction], timeout: float,
    original_complete: Mapping[str, str], original_local: Mapping[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    complete, complete_trace, complete_closures = _trace_search(
        generated, rank, carry, core.EnvelopeKind.COMPLETE, beta, timeout
    )
    local, local_trace, local_closures = _trace_search(
        generated, rank, carry, core.EnvelopeKind.LOCAL, beta, timeout
    )
    common = sorted(set(complete_trace) & set(local_trace))
    carry_hash = pilot1._hash_vector(carry.items())
    complete_candidate = complete.candidate_response_time
    local_candidate = local.candidate_response_time
    response_relation = (
        "TIGHTER" if complete_candidate is not None and local_candidate is not None and local_candidate < complete_candidate
        else "EQUAL" if complete_candidate is not None and local_candidate == complete_candidate
        else "VIOLATION" if complete_candidate is not None and local_candidate is not None and local_candidate > complete_candidate
        else "NOT_APPLICABLE"
    )
    rows: List[Dict[str, Any]] = [{
        "record_type": "TASK_SUMMARY", "relation": relation,
        "taskset_id": generated.taskset_id, "U_norm": generated.u_norm, "E0": generated.e0,
        "task_id": generated.tasks[rank].name, "priority_rank": rank,
        "carry_in_vector_hash": carry_hash, "w": None, "h": None, "q": None,
        "q_plus_h_equals_w": None, "complete_envelope": None, "local_envelope": None,
        "envelope_relation": None, "complete_closure": None, "local_closure": None,
        "local_only_closure": None, "complete_solver_status": complete.solver_status.value,
        "local_solver_status": local.solver_status.value, "complete_candidate": complete_candidate,
        "local_candidate": local_candidate, "response_relation": response_relation,
        "original_complete_candidate": original_complete["candidate_response_time"],
        "original_local_candidate": original_local["candidate_response_time"],
        "diagnostic_timeout_seconds": timeout,
    }]
    metrics = defaultdict(int)
    local_only_closure_keys = local_closures - complete_closures
    for w, h, q in common:
        complete_value = Fraction(complete_trace[(w, h, q)]["envelope_value"])
        local_value = Fraction(local_trace[(w, h, q)]["envelope_value"])
        relation_value = (
            "STRICT" if local_value < complete_value else "EQUAL"
            if local_value == complete_value else "VIOLATION"
        )
        metrics["common"] += 1
        metrics[relation_value.lower()] += 1
        if q + h == w:
            metrics["q_plus_h_equals_w"] += 1
        local_only = (w, h) in local_only_closure_keys
        rows.append({
            "record_type": "ACCESS_POINT", "relation": relation,
            "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
            "E0": generated.e0, "task_id": generated.tasks[rank].name,
            "priority_rank": rank, "carry_in_vector_hash": carry_hash,
            "w": w, "h": h, "q": q, "q_plus_h_equals_w": q + h == w,
            "complete_envelope": _fraction_text(complete_value),
            "local_envelope": _fraction_text(local_value),
            "envelope_relation": relation_value,
            "complete_closure": (w, h) in complete_closures,
            "local_closure": (w, h) in local_closures,
            "local_only_closure": local_only,
            "complete_solver_status": complete.solver_status.value,
            "local_solver_status": local.solver_status.value,
            "complete_candidate": complete_candidate, "local_candidate": local_candidate,
            "response_relation": response_relation,
            "original_complete_candidate": original_complete["candidate_response_time"],
            "original_local_candidate": original_local["candidate_response_time"],
            "diagnostic_timeout_seconds": timeout,
        })
    metrics["local_only_closure"] = len(local_only_closure_keys)
    if metrics["violation"]:
        raise Pilot2Error("pointwise local envelope exceeded complete envelope")
    return rows, dict(metrics)


def run_baseline_diagnostics(
    config: Mapping[str, Any], output_root: Path, timeout_summary: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    context = context or _baseline_context(config)
    recommendation = timeout_summary["temporary_recommendation"]
    diagnostic_timeout = 60 if recommendation in {"60_SECONDS", "FURTHER_EVALUATION"} else 30
    rows: List[Dict[str, Any]] = []
    aggregate: Dict[Tuple[str, str], Dict[str, int]] = {}
    pair_specs = (
        ("DEADLINE_CARRY_IN", "CW_D", "LOC_D"),
        ("FIXED_CW_CARRY_IN", "CW_THETA_CW", "LOC_THETA_CW"),
    )
    generated_values = sorted(context["generated"].values(), key=lambda item: item.taskset_id)
    for generated in generated_values:
        for relation, complete_variant, local_variant in pair_specs:
            complete_rows = context["tasks"][(generated.taskset_id, complete_variant)]
            local_rows = context["tasks"][(generated.taskset_id, local_variant)]
            if relation == "FIXED_CW_CARRY_IN":
                source_row = context["tasksets"][(generated.taskset_id, complete_variant)]
                if source_row["certification_status"] != "CERTIFIED_TASKSET":
                    continue
                carry = {
                    row["task_id"]: int(row["candidate_response_time"])
                    for row in complete_rows if row["candidate_response_time"]
                }
                if len(carry) != len(generated.tasks):
                    raise Pilot2Error("certified CW source has an incomplete candidate vector")
            else:
                carry = {task.name: task.deadline for task in generated.tasks}
            for rank, (complete_row, local_row) in enumerate(zip(complete_rows, local_rows)):
                if not (_eligible_status(complete_row) and _eligible_status(local_row)):
                    continue
                task_rows, metrics = _diagnose_pair_task(
                    relation, generated, rank, carry, context["beta"], diagnostic_timeout,
                    complete_row, local_row,
                )
                rows.extend(task_rows)
                aggregate[(generated.taskset_id, f"{relation}:{rank}")] = metrics
                _write_csv(
                    output_root / "baseline_tightness_diagnostics.csv",
                    BASELINE_DIAGNOSTIC_COLUMNS, rows,
                )
        complete_rows = context["tasks"][(generated.taskset_id, "CW_THETA_CW")]
        local_rows = context["tasks"][(generated.taskset_id, "LOC_THETA_LOC")]
        for rank, (complete_row, local_row) in enumerate(zip(complete_rows, local_rows)):
            if not (complete_row["candidate_response_time"] and local_row["candidate_response_time"]):
                continue
            complete_candidate = int(complete_row["candidate_response_time"])
            local_candidate = int(local_row["candidate_response_time"])
            relation_value = (
                "TIGHTER" if local_candidate < complete_candidate else
                "EQUAL" if local_candidate == complete_candidate else "VIOLATION"
            )
            if relation_value == "VIOLATION":
                raise Pilot2Error("recursive candidate dominance violation")
            rows.append({
                "record_type": "CANDIDATE_ONLY", "relation": "RECURSIVE_CARRY_IN",
                "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
                "E0": generated.e0, "task_id": generated.tasks[rank].name,
                "priority_rank": rank, "carry_in_vector_hash": None, "w": None,
                "h": None, "q": None, "q_plus_h_equals_w": None,
                "complete_envelope": None, "local_envelope": None,
                "envelope_relation": None, "complete_closure": None,
                "local_closure": None, "local_only_closure": None,
                "complete_solver_status": complete_row["task_solver_status"],
                "local_solver_status": local_row["task_solver_status"],
                "complete_candidate": complete_candidate, "local_candidate": local_candidate,
                "response_relation": relation_value,
                "original_complete_candidate": complete_candidate,
                "original_local_candidate": local_candidate,
                "diagnostic_timeout_seconds": diagnostic_timeout,
            })
        _write_csv(
            output_root / "baseline_tightness_diagnostics.csv",
            BASELINE_DIAGNOSTIC_COLUMNS, rows,
        )
    from scripts.analyze_v9_3_pilot2 import summarize_baseline
    summary = summarize_baseline(rows, context, diagnostic_timeout)
    _write_json(output_root / "baseline_tightness_summary.json", summary)
    return summary


def derive_screening_seed(
    base_seed: int, structure_index: int, utilization_index: int, taskset_index: int,
) -> int:
    material = f"{base_seed}|{structure_index}|{utilization_index}|{taskset_index}"
    digest = hashlib.sha256(material.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % 2147483647


def _screening_context(config: Mapping[str, Any], output_root: Path) -> Dict[str, Any]:
    adapter = {
        "task_generation": {
            "M": config["screening"]["M"],
            "task_p_max": config["screening"]["generation"]["task_p_max"],
        },
        "energy_model": dict(config["energy_model"]),
    }
    with tempfile.TemporaryDirectory(prefix="v9_3_pilot2_system_") as temp:
        temp_root = Path(temp)
        system_path = pilot1._prepare_system_config(adapter, temp_root)
        beta, beta_hash, beta_spec = pilot1._build_exact_service_curve(
            system_path, adapter
        )
        saved_system = output_root / "pilot2_system_config.yaml"
        shutil.copyfile(system_path, saved_system)
    return {
        "beta": beta, "beta_hash": beta_hash, "beta_spec": beta_spec,
        "config_hash": pilot1._sha256_file(output_root / "pilot2_config.yaml"),
        "system_path": saved_system,
    }


def _generate_screening_taskset(
    config: Mapping[str, Any], analysis_context: Mapping[str, Any],
    structure: Mapping[str, Any], structure_index: int, u_norm: float, u_index: int,
    taskset_index: int, base_seed: int, phase: str,
) -> pilot1.GeneratedTaskset:
    screening = config["screening"]
    generation = screening["generation"]
    seed = derive_screening_seed(base_seed, structure_index, u_index, taskset_index)
    target = Fraction(str(u_norm)) * int(screening["M"])
    with tempfile.TemporaryDirectory(prefix="v9_3_pilot2_generation_") as temp:
        task_path = Path(temp) / "tasks.yaml"
        command = [
            sys.executable, str(pilot1.TASK_GENERATOR), "-n", str(screening["task_n"]),
            "-u", format(float(target), ".15g"), "-p", str(generation["task_p_min"]),
            "-P", str(generation["task_p_max"]), "-c", str(screening["M"]),
            "--seed", str(seed), "-s", str(analysis_context["system_path"]),
            "-o", str(task_path), "--min-task-util", str(generation["min_task_util"]),
            "--max-task-util", str(generation["max_task_util"]),
            "--wcet-rounding", str(generation["wcet_rounding"]),
            "--actual-utilization-tolerance-total",
            str(generation["actual_utilization_tolerance_total"]),
        ]
        if structure["deadline_mode"] == "constrained":
            command.append("--constrained-deadlines")
        started = time.perf_counter()
        completed = subprocess.run(
            command, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=60, check=False,
        )
        elapsed = time.perf_counter() - started
        if completed.returncode:
            raise Pilot2Error(f"task generation failed with exit code {completed.returncode}")
        legacy_tasks = legacy_rta.rm_order(legacy_rta.load_tasks(str(task_path)))
        with task_path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    raw_by_name = {str(item["name"]): item for item in document["taskset"]}
    system = legacy_rta.load_system_config(str(analysis_context["system_path"]))
    tasks = []
    payload = []
    for rank, legacy_task in enumerate(legacy_tasks):
        raw = raw_by_name[legacy_task.name]
        power = Fraction(str(system.task_energy_per_tick(legacy_task.workload)))
        task_id = str(rank)
        tasks.append(core.V93Task(
            task_id, legacy_task.wcet, legacy_task.deadline, legacy_task.period, power
        ))
        payload.append({
            "task_id": task_id, "source_name": legacy_task.name,
            "priority_rank": rank, "C": legacy_task.wcet, "D": legacy_task.deadline,
            "T": legacy_task.period, "P": _fraction_text(power),
            "workload": pilot1._task_workload(raw),
            "arrival_offset": int(next((
                part.split("=", 1)[1] for part in str(raw.get("params", "")).split(",")
                if part.strip().startswith("arrival_offset=")
            ), "0")),
        })
    if structure["deadline_mode"] == "implicit" and any(
        task.deadline != task.period for task in tasks
    ):
        raise Pilot2Error("implicit-deadline structure generated D != T")
    if structure["deadline_mode"] == "constrained" and any(
        not task.wcet <= task.deadline <= task.period for task in tasks
    ):
        raise Pilot2Error("constrained-deadline structure generated illegal deadline")
    actual = sum(Fraction(task.wcet, task.period) for task in tasks)
    tolerance = Fraction(str(generation["actual_utilization_tolerance_total"]))
    if abs(actual - target) > tolerance:
        raise Pilot2Error("screening utilization is outside tolerance")
    semantic = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT2:TASKSET_SEMANTIC:v9.3",
        {"M": screening["M"], "tasks": payload, "service_curve_hash": analysis_context["beta_hash"]},
    )
    priority_hash = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT2:PRIORITY:v9.3",
        [{"task_id": item["task_id"], "priority_rank": item["priority_rank"]} for item in payload],
    )
    power_hash = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT2:POWER_VECTOR:v9.3",
        [{"task_id": item["task_id"], "P": item["P"]} for item in payload],
    )
    taskset_id = (
        f"v93-p2-{phase.lower()}-{structure['id'].lower()}-u{u_index}-"
        f"t{taskset_index:02d}-{semantic[:12]}"
    )
    return pilot1.GeneratedTaskset(
        seed, taskset_id, format(float(u_norm), ".15g"), u_index,
        int(screening["E0"]), 0, taskset_index, target, actual, semantic,
        priority_hash, power_hash, tuple(tasks), tuple(payload), elapsed,
    )


def _screening_taskset_row(
    phase: str, structure: Mapping[str, Any], cell_id: str,
    generated: pilot1.GeneratedTaskset,
) -> Dict[str, Any]:
    return {
        "phase": phase, "structure": structure["id"],
        "structure_alias_of": structure.get("alias_of"),
        "deadline_mode": structure["deadline_mode"], "power_mode": structure["power_mode"],
        "cell_id": cell_id, "U_norm": generated.u_norm,
        "taskset_index": generated.taskset_index, "generation_seed": generated.seed,
        "taskset_id": generated.taskset_id,
        "actual_total_utilization": _fraction_text(generated.actual_total_utilization),
        "task_count": len(generated.tasks),
        "distinct_power_count": len({task.power for task in generated.tasks}),
        "taskset_semantic_hash": generated.semantic_hash,
        "priority_rank_hash": generated.priority_hash,
        "power_vector_hash": generated.power_hash,
        "generation_runtime_seconds": _runtime(generated.generation_runtime_seconds),
        "task_input_json": json.dumps(
            list(generated.task_payload), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _screening_result_row(
    phase: str, structure: Mapping[str, Any], cell_id: str,
    generated: pilot1.GeneratedTaskset, variant: taskset.AnalysisVariant,
    execution: TimedExecution,
) -> Dict[str, Any]:
    result = execution.result
    if result is None:
        raise Pilot2Error("screening result row requires a result")
    candidates, statuses = _result_vectors(result)
    counters = _counter_totals(result)
    source_hash = pilot1._hash_vector(result.source_candidate_vector)
    target_hash = source_hash if variant is taskset.AnalysisVariant.LOC_THETA_CW else None
    return {
        "phase": phase, "structure": structure["id"],
        "structure_alias_of": structure.get("alias_of"),
        "deadline_mode": structure["deadline_mode"], "power_mode": structure["power_mode"],
        "cell_id": cell_id, "U_norm": generated.u_norm,
        "taskset_index": generated.taskset_index, "generation_seed": generated.seed,
        "taskset_id": generated.taskset_id,
        "request_id": pilot1._domain_hash(
            "ASAP_BLOCK:PILOT2:REQUEST:v9.3", {"analysis_id": result.analysis_id}
        ),
        "analysis_id": result.analysis_id, "analysis_variant": variant.name,
        "solver_status": result.solver_status.value,
        "certification_status": result.certification_status.value,
        "taskset_proven": result.taskset_proven,
        "first_failed_priority": result.first_failed_priority,
        "candidate_vector": candidates, "task_status_vector": statuses,
        "candidate_found_task_count": result.n_tasks_candidate_found,
        "certified_task_count": result.n_tasks_certified,
        "checked_w_count": counters[0], "checked_h_count": counters[1],
        "checked_q_count": counters[2], "envelope_calls": counters[3],
        "worker_startup_seconds": _runtime(execution.worker_startup_seconds),
        "solver_wall_seconds": _runtime(execution.solver_wall_seconds),
        "solver_cpu_seconds": _runtime(execution.solver_cpu_seconds),
        "serialization_seconds": _runtime(execution.serialization_seconds),
        "deserialization_seconds": _runtime(execution.deserialization_seconds),
        "transport_and_exit_seconds": _runtime(execution.transport_and_exit_seconds),
        "total_wall_seconds": _runtime(execution.total_wall_seconds),
        "timeout": result.solver_status is taskset.AnalysisSolverStatus.TIMEOUT,
        "numeric_error": result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR,
        "internal_error": result.solver_status is taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        "not_applicable": result.solver_status is taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY,
        "dependency_status": result.dependency_check_status.value,
        "dominance_status": result.dominance_invariant_status.value,
        "source_vector_hash": source_hash, "target_carry_in_vector_hash": target_hash,
    }


def _run_five(
    phase: str, structure: Mapping[str, Any], cell_id: str,
    generated: pilot1.GeneratedTaskset, timeout: int,
    analysis_context: Mapping[str, Any],
) -> Tuple[Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult], List[Dict[str, Any]]]:
    results: Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult] = {}
    rows = []
    inp_context = dict(analysis_context)
    for variant in VARIANTS:
        analysis_id = pilot1._domain_hash(
            "ASAP_BLOCK:PILOT2:ANALYSIS:v9.3",
            {"phase": phase, "taskset_id": generated.taskset_id, "variant": variant.name},
        )
        source = results.get(taskset.AnalysisVariant.CW_THETA_CW) if (
            variant is taskset.AnalysisVariant.LOC_THETA_CW
        ) else None
        execution = _run_request(
            generated, variant, timeout, inp_context, analysis_id, source=source
        )
        result = execution.result
        if result is None:
            raise Pilot2Error("missing screening result")
        if result.solver_status in {
            taskset.AnalysisSolverStatus.NUMERIC_ERROR,
            taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        }:
            raise Pilot2Error(f"P0 {result.solver_status.value} in {generated.taskset_id} {variant.name}")
        results[variant] = result
        rows.append(_screening_result_row(
            phase, structure, cell_id, generated, variant, execution
        ))
    if tuple(results) != VARIANTS:
        raise Pilot2Error("missing or reordered five-variant result")
    pilot1.dominance_rows(generated, results)
    return results, rows


def _record_is_evaluated(record: taskset.TaskAnalysisRecord) -> bool:
    return record.solver_status not in {
        taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
        taskset.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
    }


def _pair_tightness_rows(
    phase: str, structure: Mapping[str, Any], cell_id: str,
    generated: pilot1.GeneratedTaskset,
    results: Mapping[taskset.AnalysisVariant, taskset.TasksetAnalysisResult],
    beta: Sequence[Fraction], diagnostic_timeout: int, include_envelope: bool,
) -> List[Dict[str, Any]]:
    specs = (
        ("DEADLINE_CARRY_IN", taskset.AnalysisVariant.CW_D, taskset.AnalysisVariant.LOC_D),
        ("FIXED_CW_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_CW),
        ("RECURSIVE_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_LOC),
    )
    rows = []
    for relation, complete_variant, local_variant in specs:
        complete = results[complete_variant]
        local = results[local_variant]
        if relation == "DEADLINE_CARRY_IN":
            carry = {task.name: task.deadline for task in generated.tasks}
        elif relation == "FIXED_CW_CARRY_IN":
            if not production_runner._source_is_jointly_certified(complete):
                carry = None
            else:
                carry = {
                    record.task_id: record.candidate_response_time
                    for record in complete.task_records
                }
        else:
            carry = None
        for rank, (left, right) in enumerate(zip(complete.task_records, local.task_records)):
            both_candidates = (
                left.solver_status is taskset.TaskSolverStatus.CANDIDATE_FOUND
                and right.solver_status is taskset.TaskSolverStatus.CANDIDATE_FOUND
            )
            if both_candidates:
                improvement = left.candidate_response_time - right.candidate_response_time
                status = "TIGHTER" if improvement > 0 else "EQUAL" if improvement == 0 else "VIOLATION"
            else:
                improvement = None
                status = "NOT_APPLICABLE"
            metrics: Dict[str, int] = {}
            if (
                include_envelope and relation != "RECURSIVE_CARRY_IN" and carry is not None
                and _record_is_evaluated(left) and _record_is_evaluated(right)
            ):
                _discarded, metrics = _diagnose_pair_task(
                    relation, generated, rank, carry, beta, diagnostic_timeout,
                    {"candidate_response_time": left.candidate_response_time or ""},
                    {"candidate_response_time": right.candidate_response_time or ""},
                )
            rows.append({
                "phase": phase, "structure": structure["id"], "cell_id": cell_id,
                "U_norm": generated.u_norm, "taskset_id": generated.taskset_id,
                "relation": relation, "task_id": left.task_id, "priority_rank": rank,
                "complete_candidate": left.candidate_response_time,
                "local_candidate": right.candidate_response_time,
                "improvement": improvement, "status": status,
                "envelope_common_count": metrics.get("common", 0),
                "envelope_strict_count": metrics.get("strict", 0),
                "envelope_equal_count": metrics.get("equal", 0),
                "envelope_violation_count": metrics.get("violation", 0),
                "local_only_closure_count": metrics.get("local_only_closure", 0),
                "q_plus_h_equals_w_count": metrics.get("q_plus_h_equals_w", 0),
            })
            if status == "VIOLATION" or metrics.get("violation", 0):
                raise Pilot2Error("P0 dominance violation in screening tightness")
    return rows


def _save_failure_input(
    output_root: Path, stage: str, generated: Optional[pilot1.GeneratedTaskset]
) -> str:
    if generated is None:
        return ""
    failure_dir = output_root / "failure_inputs"
    failure_dir.mkdir(parents=True, exist_ok=True)
    path = failure_dir / f"{stage}_{generated.taskset_id}.json"
    _write_json(path, {
        "taskset_id": generated.taskset_id, "generation_seed": generated.seed,
        "U_norm": generated.u_norm, "E0": generated.e0,
        "tasks": list(generated.task_payload),
    })
    return str(path.relative_to(output_root))


def run_screening(
    config: Mapping[str, Any], output_root: Path, timeout_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    recommendation = timeout_summary["temporary_recommendation"]
    timeout = 60 if recommendation in {"60_SECONDS", "FURTHER_EVALUATION"} else 30
    analysis_context = _screening_context(config, output_root)
    taskset_rows: List[Dict[str, Any]] = []
    result_rows: List[Dict[str, Any]] = []
    tightness_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    active: Optional[pilot1.GeneratedTaskset] = None
    active_variant = None
    try:
        for structure_index, structure in enumerate(config["screening"]["structures"]):
            for u_index, u_norm in enumerate(config["screening"]["normalized_utilizations"]):
                cell_id = f"{structure['id']}-U{format(float(u_norm), '.1f')}"
                for taskset_index in range(config["screening"]["tasksets_per_cell"]):
                    active = _generate_screening_taskset(
                        config, analysis_context, structure, structure_index, u_norm, u_index,
                        taskset_index, config["screening"]["base_seed"], "SCREENING",
                    )
                    taskset_rows.append(_screening_taskset_row(
                        "SCREENING", structure, cell_id, active
                    ))
                    _write_csv(
                        output_root / "screening_tasksets.csv", SCREENING_TASKSET_COLUMNS,
                        taskset_rows,
                    )
                    results, new_results = _run_five(
                        "SCREENING", structure, cell_id, active, timeout, analysis_context
                    )
                    result_rows.extend(new_results)
                    _write_csv(
                        output_root / "screening_results.csv", SCREENING_RESULT_COLUMNS,
                        result_rows,
                    )
                    tightness_rows.extend(_pair_tightness_rows(
                        "SCREENING", structure, cell_id, active, results,
                        analysis_context["beta"], timeout, True,
                    ))
                    _write_csv(
                        output_root / "screening_tightness.csv", SCREENING_TIGHTNESS_COLUMNS,
                        tightness_rows,
                    )
    except BaseException as exc:
        failures.append({
            "severity": "P0", "stage": "screening", "taskset_id": active.taskset_id if active else None,
            "analysis_variant": active_variant, "code": type(exc).__name__,
            "detail": str(exc)[:500],
            "input_file": _save_failure_input(output_root, "screening", active),
        })
        _write_csv(output_root / "failures.csv", FAILURE_COLUMNS, failures)
        raise
    _write_csv(output_root / "failures.csv", FAILURE_COLUMNS, failures)
    from scripts.analyze_v9_3_pilot2 import summarize_screening
    summary = summarize_screening(taskset_rows, result_rows, tightness_rows, config, timeout)
    _write_json(output_root / "screening_summary.json", summary)
    return summary


def run_confirmation(
    config: Mapping[str, Any], output_root: Path, timeout_summary: Mapping[str, Any],
    screening_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    selected = list(screening_summary.get("selected_confirmation_cells", []))
    if not selected:
        empty: List[Dict[str, Any]] = []
        _write_csv(output_root / "confirmation_results.csv", SCREENING_RESULT_COLUMNS, empty)
        return {"executed": False, "selected_cells": [], "cells": {}}
    recommendation = timeout_summary["temporary_recommendation"]
    timeout = 60 if recommendation in {"60_SECONDS", "FURTHER_EVALUATION"} else 30
    analysis_context = _screening_context(config, output_root)
    result_rows: List[Dict[str, Any]] = []
    tightness_rows: List[Dict[str, Any]] = []
    structure_by_id = {entry["id"]: entry for entry in config["screening"]["structures"]}
    selected_set = set(selected)
    for structure_index, structure in enumerate(config["screening"]["structures"]):
        for u_index, u_norm in enumerate(config["screening"]["normalized_utilizations"]):
            cell_id = f"{structure['id']}-U{format(float(u_norm), '.1f')}"
            if cell_id not in selected_set:
                continue
            for taskset_index in range(config["screening"]["confirmation_tasksets_per_cell"]):
                generated = _generate_screening_taskset(
                    config, analysis_context, structure, structure_index, u_norm, u_index,
                    taskset_index, config["screening"]["confirmation_base_seed"], "CONFIRMATION",
                )
                results, new_results = _run_five(
                    "CONFIRMATION", structure, cell_id, generated, timeout, analysis_context
                )
                result_rows.extend(new_results)
                tightness_rows.extend(_pair_tightness_rows(
                    "CONFIRMATION", structure, cell_id, generated, results,
                    analysis_context["beta"], timeout, False,
                ))
                _write_csv(
                    output_root / "confirmation_results.csv", SCREENING_RESULT_COLUMNS,
                    result_rows,
                )
    from scripts.analyze_v9_3_pilot2 import summarize_confirmation
    summary = summarize_confirmation(result_rows, tightness_rows, screening_summary)
    return summary


def _hash_files(output_root: Path) -> None:
    target = output_root / "file_hashes.sha256"
    lines = []
    for path in sorted(output_root.rglob("*"), key=lambda item: item.relative_to(output_root).as_posix()):
        if not path.is_file() or path == target:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(output_root).as_posix()}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_output(config_path: Path, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / "pilot2_config.yaml"
    if not target.exists():
        shutil.copyfile(config_path, target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "v9_3_pilot2.yaml")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--stage", choices=("timeout", "controls", "baseline", "screening", "confirmation", "all"),
        default="all",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        prepare_output(args.config, args.output_root)
        context = None
        timeout_summary = None
        baseline_summary = None
        screening_summary = None
        if args.stage in {"timeout", "all"}:
            context = _baseline_context(config)
            timeout_summary = run_timeout_sensitivity(config, args.output_root, context)
        elif args.stage == "controls":
            context = _baseline_context(config)
            timeout_summary = rerun_non_timeout_controls(config, args.output_root, context)
        else:
            timeout_summary = json.loads((args.output_root / "timeout_summary.json").read_text(encoding="utf-8"))
        if args.stage in {"baseline", "all"}:
            context = context or _baseline_context(config)
            baseline_summary = run_baseline_diagnostics(
                config, args.output_root, timeout_summary, context
            )
        elif (args.output_root / "baseline_tightness_summary.json").exists():
            baseline_summary = json.loads((args.output_root / "baseline_tightness_summary.json").read_text(encoding="utf-8"))
        if args.stage in {"screening", "all"}:
            if timeout_summary.get("p0") or (baseline_summary and baseline_summary.get("p0")):
                raise Pilot2Error("P0 gate prevents screening")
            screening_summary = run_screening(config, args.output_root, timeout_summary)
        elif (args.output_root / "screening_summary.json").exists():
            screening_summary = json.loads((args.output_root / "screening_summary.json").read_text(encoding="utf-8"))
        confirmation_summary = None
        if args.stage in {"confirmation", "all"}:
            if screening_summary is None:
                raise Pilot2Error("confirmation requires screening_summary.json")
            confirmation_summary = run_confirmation(
                config, args.output_root, timeout_summary, screening_summary
            )
            screening_summary["confirmation"] = confirmation_summary
            _write_json(args.output_root / "screening_summary.json", screening_summary)
        if args.stage == "all":
            from scripts.analyze_v9_3_pilot2 import write_report
            write_report(args.output_root, timeout_summary, baseline_summary, screening_summary)
            _hash_files(args.output_root)
        print(json.dumps({
            "stage": args.stage,
            "temporary_timeout": timeout_summary.get("temporary_recommendation"),
            "differentiating_cells": (
                screening_summary.get("differentiating_cells", []) if screening_summary else None
            ),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"v9.3 Pilot-2 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
