"""Common production execution engine for v9.3 CORE-1 and CORE-2."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
import multiprocessing
import os
from pathlib import Path
import pickle
import signal
import subprocess
import threading
import time
import traceback
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner

from .cell_model import Cell, analysis_id, expand_cells
from .config import config_hash, domain_hash, dump_config, fraction_text
from .result_writer import (
    ATTEMPT_COLUMNS,
    ResultWriter,
    atomic_write_json,
    read_csv,
)
from .taskset_store import ServiceCurveMaterial, StoredTaskset, TasksetStore, prepare_service_curve
from .validation import ConformanceFailure, assert_unique, validate_analysis_result


class ExecutionError(RuntimeError):
    """Fail-closed experiment execution error."""


@dataclass(frozen=True)
class ExecutionPlanItem:
    cell: Cell
    taskset_index: int
    stored: StoredTaskset
    variant: taskset.AnalysisVariant
    analysis_id: str
    request_id: str
    source_analysis_id: Optional[str]


@dataclass(frozen=True)
class AttemptExecution:
    result: Optional[taskset.TasksetAnalysisResult]
    solver_status: str
    outer_timeout: bool
    solver_wall_seconds: float
    solver_cpu_seconds: float
    worker_startup_seconds: float
    ipc_seconds: float
    total_wall_seconds: float
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    traceback_text: Optional[str] = None


@dataclass(frozen=True)
class RunOutcome:
    output_root: Path
    requested: int
    terminal: int
    status_counts: Mapping[str, int]
    stopped: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _analysis_worker(
    sending: Any,
    started_event: Any,
    request: production_runner.V93DispatchRequest,
) -> None:
    started_event.set()
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    try:
        result = production_runner.dispatch_rta_version(
            production_runner.V93_DISPATCH_VERSION, v93_request=request
        )
        payload = (
            "ok", result, time.perf_counter() - wall_started,
            time.process_time() - cpu_started,
        )
    except BaseException as exc:
        payload = (
            "error", type(exc).__name__, str(exc), traceback.format_exc(),
            time.perf_counter() - wall_started, time.process_time() - cpu_started,
        )
    try:
        sending.send(payload)
    finally:
        sending.close()


def execute_isolated(
    request: production_runner.V93DispatchRequest,
    timeout_seconds: float,
    *,
    start_method: str = "spawn",
) -> AttemptExecution:
    """Execute one production request with a hard wall boundary."""

    context = multiprocessing.get_context(start_method)
    receiving, sending = context.Pipe(duplex=False)
    started_event = context.Event()
    process = context.Process(
        target=_analysis_worker, args=(sending, started_event, request), daemon=False
    )
    total_started = time.perf_counter()
    process.start()
    sending.close()
    if not started_event.wait(min(10.0, max(1.0, timeout_seconds))):
        process.terminate()
        process.join(5)
        return AttemptExecution(
            None, "TIMEOUT", True, 0.0, 0.0,
            time.perf_counter() - total_started, 0.0,
            time.perf_counter() - total_started,
            "WorkerStartupTimeout", "analysis worker did not start", None,
        )
    startup = time.perf_counter() - total_started
    # The analyzer owns the exact configured budget. A small transport grace
    # prevents classifying a returned inner TIMEOUT as an IPC failure.
    transport_grace = min(1.0, max(0.1, timeout_seconds * 0.05))
    try:
        if not receiving.poll(timeout_seconds + transport_grace):
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            total = time.perf_counter() - total_started
            return AttemptExecution(
                None, "TIMEOUT", True, timeout_seconds, 0.0, startup,
                max(0.0, total - startup - timeout_seconds), total,
                "ConfigurationTimeout", "hard per-configuration timeout", None,
            )
        payload = receiving.recv()
    except EOFError as exc:
        total = time.perf_counter() - total_started
        return AttemptExecution(
            None, "INTERNAL_CONFORMANCE_FAILURE", False, 0.0, 0.0, startup,
            max(0.0, total - startup), total,
            type(exc).__name__, str(exc), traceback.format_exc(),
        )
    finally:
        receiving.close()
    process.join(5)
    total = time.perf_counter() - total_started
    if process.is_alive():
        process.terminate()
        process.join(5)
        return AttemptExecution(
            None, "INTERNAL_CONFORMANCE_FAILURE", False, 0.0, 0.0,
            startup, max(0.0, total - startup), total,
            "WorkerDidNotExit", "worker returned a payload but did not exit", None,
        )
    if payload[0] == "ok":
        result = payload[1]
        solver_wall = float(payload[2])
        return AttemptExecution(
            result, result.solver_status.value, False, solver_wall,
            float(payload[3]), startup,
            max(0.0, total - startup - solver_wall), total,
        )
    solver_wall = float(payload[4])
    return AttemptExecution(
        None, "INTERNAL_CONFORMANCE_FAILURE", False, solver_wall,
        float(payload[5]), startup, max(0.0, total - startup - solver_wall), total,
        payload[1], payload[2], payload[3],
    )


def _vector_hash(entries: Iterable[Tuple[str, int]]) -> Optional[str]:
    frozen = tuple(sorted((str(key), int(value)) for key, value in entries))
    return (
        domain_hash("ASAP_BLOCK:V9.3:CARRY_IN_VECTOR:v1", frozen)
        if frozen else None
    )


def _dependency_context(
    stored: StoredTaskset,
    cell: Cell,
    service: ServiceCurveMaterial,
) -> taskset.DependencyContext:
    return taskset.DependencyContext(
        taskset_identity=stored.semantic_hash,
        task_definitions_identity=domain_hash(
            "ASAP_BLOCK:V9.3:TASK_DEFINITIONS:v1", stored.task_payload
        ),
        priority_order_identity=stored.priority_hash,
        e0_canonical_identity=domain_hash(
            "ASAP_BLOCK:V9.3:E0:v1", fraction_text(cell.exact_e0)
        ),
        service_curve_identity=service.identity,
        power_vector_identity=stored.power_hash,
        numerical_mode=cell.numerical_mode,
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        formal_contract_identity=None,
    )


def _analysis_input(
    stored: StoredTaskset,
    cell: Cell,
    service: ServiceCurveMaterial,
    timeout: float,
) -> taskset.TasksetAnalysisInput:
    return taskset.TasksetAnalysisInput(
        stored.tasks, stored.processors, cell.exact_e0, service.values,
        _dependency_context(stored, cell, service), timeout_seconds=timeout,
    )


def _variant(value: str) -> taskset.AnalysisVariant:
    return taskset.AnalysisVariant[value]


def _task_rows(
    item: ExecutionPlanItem,
    result: taskset.TasksetAnalysisResult,
) -> list[Dict[str, Any]]:
    definitions = {str(row["task_id"]): row for row in item.stored.task_payload}
    rows = []
    for record in result.task_records:
        definition = definitions[record.task_id]
        rows.append({
            "analysis_id": result.analysis_id,
            "cell_id": item.cell.cell_id,
            "taskset_id": item.stored.taskset_id,
            "exact_e0": fraction_text(item.cell.exact_e0),
            "analysis_variant": result.analysis_variant.name,
            "task_id": record.task_id,
            "priority_rank": record.priority_rank,
            "C": definition["C"], "D": definition["D"], "T": definition["T"],
            "P": definition["P"], "D_over_T": definition["D_over_T"],
            "task_solver_status": record.solver_status.value,
            "task_certification_status": record.certification_status.value,
            "candidate_response_time": record.candidate_response_time,
            "closing_w": record.closing_w,
            "witness_h": record.witness_h,
            "checked_w_count": record.checked_w_count,
            "checked_h_count": record.checked_h_count,
            "checked_q_count": record.checked_q_count,
            "envelope_call_count": record.envelope_call_count,
            "failure_reason": record.failure_reason,
            "carry_in_vector_hash": _vector_hash(record.carry_in_values_used),
        })
    return rows


def _terminal_payload(
    item: ExecutionPlanItem,
    execution: AttemptExecution,
    attempts: Sequence[Mapping[str, Any]],
    *,
    result: Optional[taskset.TasksetAnalysisResult],
    serialization_seconds: float,
) -> Dict[str, Any]:
    attempt_count = len(attempts)
    total_wall = sum(float(row["total_wall_seconds"]) for row in attempts)
    total_cpu = sum(float(row["solver_cpu_seconds"]) for row in attempts)
    source_hash = _vector_hash(result.source_candidate_vector) if result else None
    target_hash = source_hash if item.variant is taskset.AnalysisVariant.LOC_THETA_CW else None
    if result is None:
        solver_status = execution.solver_status
        certification = "NOT_CERTIFIED"
        method_role = taskset.ROLE_BY_VARIANT[item.variant].value
        row = {
            "analysis_variant": item.variant.name,
            "method_role": method_role,
            "solver_status": solver_status,
            "certification_status": certification,
            "taskset_proven": False,
            "first_failed_priority": None,
            "n_tasks_total": len(item.stored.tasks),
            "n_tasks_evaluated": 0,
            "n_tasks_candidate_found": 0,
            "n_tasks_certified": 0,
            "source_analysis_id": item.source_analysis_id,
            "dependency_check_status": (
                "INVALID" if item.variant is taskset.AnalysisVariant.LOC_THETA_CW else "NOT_CHECKED"
            ),
            "fixed_carry_in_interface_status": (
                "ACTIVE" if item.variant not in {
                    taskset.AnalysisVariant.CW_THETA_CW,
                    taskset.AnalysisVariant.LOC_THETA_LOC,
                } else "NOT_APPLICABLE"
            ),
            "dominance_invariant_status": "NOT_CHECKED",
            "dominance_violation_count": 0,
            "diagnostic_mode": False,
            "terminal_origin": "OUTER_WORKER",
        }
        task_rows: list[Dict[str, Any]] = []
    else:
        row = {
            "analysis_variant": result.analysis_variant.name,
            "method_role": result.method_role.value,
            "solver_status": result.solver_status.value,
            "certification_status": result.certification_status.value,
            "taskset_proven": result.taskset_proven,
            "first_failed_priority": result.first_failed_priority,
            "n_tasks_total": result.n_tasks_total,
            "n_tasks_evaluated": result.n_tasks_evaluated,
            "n_tasks_candidate_found": result.n_tasks_candidate_found,
            "n_tasks_certified": result.n_tasks_certified,
            "source_analysis_id": result.source_analysis_id,
            "dependency_check_status": result.dependency_check_status.value,
            "fixed_carry_in_interface_status": result.fixed_carry_in_interface_status.value,
            "dominance_invariant_status": result.dominance_invariant_status.value,
            "dominance_violation_count": 1 if result.dominance_counterexample else 0,
            "diagnostic_mode": result.diagnostic_mode,
            "terminal_origin": "PRODUCTION_ANALYZER",
        }
        task_rows = _task_rows(item, result)
    final_attempt = attempts[-1]
    taskset_row = {
        "analysis_id": item.analysis_id,
        "request_id": item.request_id,
        "cell_id": item.cell.cell_id,
        "taskset_id": item.stored.taskset_id,
        "taskset_hash": item.stored.semantic_hash,
        "generation_seed": item.stored.seed,
        "M": item.cell.processors,
        "task_n": item.cell.task_count,
        "utilization": fraction_text(item.cell.utilization),
        "exact_e0": fraction_text(item.cell.exact_e0),
        "deadline_mode": item.cell.deadline_mode,
        **row,
        "source_vector_hash": source_hash,
        "target_carry_in_vector_hash": target_hash,
        "final_attempt_id": final_attempt["attempt_id"],
        "attempt_count": attempt_count,
        "timeout_budget_seconds": final_attempt["timeout_budget_seconds"],
        "runtime_wall_seconds": f"{total_wall:.9f}",
        "runtime_cpu_seconds": f"{total_cpu:.9f}",
        "worker_startup_seconds": final_attempt["worker_startup_seconds"],
        "serialization_seconds": f"{serialization_seconds:.9f}",
        "ipc_seconds": final_attempt["ipc_seconds"],
        "outer_timeout": final_attempt["outer_timeout"],
    }
    return {"taskset_row": taskset_row, "task_rows": task_rows}


def _attempt_id(analysis_id_value: str, attempt_number: int) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:ANALYSIS_ATTEMPT:v1",
        {"analysis_id": analysis_id_value, "attempt_number": attempt_number},
    )


def _pickle_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class ExecutionEngine:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.config_identity = config_hash(config)
        self.root = Path(config["execution"]["output_root"])
        self.writer: Optional[ResultWriter] = None
        self.service: Optional[ServiceCurveMaterial] = None
        self.store: Optional[TasksetStore] = None
        self.stop_requested = threading.Event()
        self._write_lock = threading.RLock()
        self._checkpoint_counter = 0
        self._cells: list[Cell] = []
        self._stored_by_key: Dict[tuple[str, int], StoredTaskset] = {}
        self._requests: list[Dict[str, Any]] = []
        self._dependencies: list[Dict[str, Any]] = []
        self._dominance: list[Dict[str, Any]] = []
        self._failures: list[Dict[str, Any]] = []

    def describe(self, *, max_cells: Optional[int] = None) -> Dict[str, Any]:
        cells = list(expand_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        return {
            "experiment_id": self.config["experiment_id"],
            "core": self.config["core"],
            "cell_count": len(cells),
            "tasksets_per_cell": self.config["grid"]["tasksets_per_cell"],
            "variants": self.config["analysis"]["variants"],
            "request_count": len(cells) * self.config["grid"]["tasksets_per_cell"] * len(self.config["analysis"]["variants"]),
            "cells": [cell.row() for cell in cells],
        }

    def _initialize(self, *, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        metadata_path = self.root / "run_metadata.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("config_hash") != self.config_identity:
                raise ExecutionError("configuration hash mismatch; refusing to resume")
            if not resume:
                raise ExecutionError("run directory already exists; use --resume")
        else:
            metadata = {
                "schema": "ASAP_BLOCK_V9_3_FORMAL_RUN_V1",
                "experiment_id": self.config["experiment_id"],
                "core": self.config["core"],
                "config_hash": self.config_identity,
                "created_at_utc": _utc_now(),
                "git_head": self._git_head(),
                "production_entry": "asap_block_rta_v9_3_taskset.analyze_taskset_v9_3",
                "formal_large_scale_run": False,
            }
            atomic_write_json(metadata_path, metadata)
            dump_config(self.config, self.root / "run_config.yaml")
        self.writer = ResultWriter(self.root)
        # A resumed P0 run must never erase the already-recorded failure audit.
        self._failures = list(read_csv(self.root / "failures.csv"))
        self.service = prepare_service_curve(self.config, self.root)
        self.store = TasksetStore(
            Path(self.config["execution"]["taskset_store"]), self.config, self.service
        )

    @staticmethod
    def _git_head() -> str:
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parents[2]),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "UNKNOWN"

    def _prepare_plan(
        self, *, max_cells: Optional[int], max_tasksets: Optional[int]
    ) -> list[list[ExecutionPlanItem]]:
        assert self.store is not None
        cells = list(expand_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        self._cells = cells
        per_cell = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            per_cell = min(per_cell, max_tasksets)
        chains: list[list[ExecutionPlanItem]] = []
        generated_rows: Dict[str, Dict[str, Any]] = {}
        for cell in cells:
            for index in range(per_cell):
                key = (cell.generation_id, index)
                stored = self._stored_by_key.get(key)
                if stored is None:
                    stored = self.store.get_or_create(cell, index)
                    self._stored_by_key[key] = stored
                generated_rows[stored.taskset_id] = stored.generated_row()
                ids = {
                    variant_name: analysis_id(cell, stored.semantic_hash, variant_name)
                    for variant_name in self.config["analysis"]["variants"]
                }
                chain = []
                for variant_name in self.config["analysis"]["variants"]:
                    variant = _variant(variant_name)
                    aid = ids[variant_name]
                    source_id = (
                        ids["CW_THETA_CW"]
                        if variant is taskset.AnalysisVariant.LOC_THETA_CW else None
                    )
                    request_id = domain_hash(
                        "ASAP_BLOCK:V9.3:ANALYSIS_REQUEST:v1", aid
                    )
                    chain.append(ExecutionPlanItem(
                        cell, index, stored, variant, aid, request_id, source_id
                    ))
                    self._requests.append({
                        "request_id": request_id, "analysis_id": aid,
                        "cell_id": cell.cell_id, "taskset_id": stored.taskset_id,
                        "taskset_hash": stored.semantic_hash,
                        "exact_e0": fraction_text(cell.exact_e0),
                        "variant": variant.name,
                        "numerical_mode": cell.numerical_mode,
                        "timeout_seconds": self.config["analysis"]["timeout_seconds"],
                        "retry_timeout_seconds": self.config["analysis"].get("retry_timeout_seconds"),
                        "source_analysis_id": source_id,
                        "request_status": "PLANNED",
                    })
                chains.append(chain)
        assert_unique(self._requests, "analysis_id")
        self._generated_rows = list(generated_rows.values())
        return chains

    def _attempts_for(self, analysis_id_value: str) -> list[Dict[str, str]]:
        assert self.writer is not None
        return [
            row for row in read_csv(self.root / "analysis_attempts.csv")
            if row["analysis_id"] == analysis_id_value
        ]

    def _result_state_path(self, analysis_id_value: str) -> Path:
        assert self.writer is not None
        return self.writer.states / f"{analysis_id_value}.pickle"

    def _load_result_state(self, analysis_id_value: str) -> Optional[taskset.TasksetAnalysisResult]:
        path = self._result_state_path(analysis_id_value)
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            result = pickle.load(handle)
        if not isinstance(result, taskset.TasksetAnalysisResult) or result.analysis_id != analysis_id_value:
            raise ExecutionError("invalid resumed analyzer state")
        return result

    def _record_failure(
        self, item: ExecutionPlanItem, code: str, detail: str,
        traceback_text: Optional[str], severity: str = "P0",
    ) -> None:
        assert self.writer is not None
        failure_path = self.writer.fail_inputs / f"{item.analysis_id}.json"
        atomic_write_json(failure_path, {
            "analysis_id": item.analysis_id,
            "cell": item.cell.row(),
            "taskset": item.stored.generated_row(),
            "variant": item.variant.name,
        })
        row = {
            "severity": severity, "stage": "ANALYSIS",
            "analysis_id": item.analysis_id, "cell_id": item.cell.cell_id,
            "taskset_id": item.stored.taskset_id, "variant": item.variant.name,
            "code": code, "detail": detail, "traceback": traceback_text,
            "failure_input": str(failure_path),
        }
        with self._write_lock:
            self._failures.append(row)

    def _dependency_status(
        self, item: ExecutionPlanItem, source: Optional[taskset.TasksetAnalysisResult]
    ) -> taskset.DependencyVectorCheckStatus:
        if item.variant is not taskset.AnalysisVariant.LOC_THETA_CW:
            return taskset.DependencyVectorCheckStatus.NOT_CHECKED
        if source is None:
            return taskset.DependencyVectorCheckStatus.INVALID
        return (
            taskset.DependencyVectorCheckStatus.VALID
            if production_runner._source_is_jointly_certified(source)
            else taskset.DependencyVectorCheckStatus.INVALID
        )

    def _finish_terminal(
        self,
        item: ExecutionPlanItem,
        execution: AttemptExecution,
        attempts: Sequence[Mapping[str, Any]],
        result: Optional[taskset.TasksetAnalysisResult],
    ) -> None:
        assert self.writer is not None
        serialization_started = time.perf_counter()
        payload = _terminal_payload(
            item, execution, attempts, result=result, serialization_seconds=0.0
        )
        serialization = time.perf_counter() - serialization_started
        payload["taskset_row"]["serialization_seconds"] = f"{serialization:.9f}"
        self.writer.write_terminal(item.analysis_id, payload)

    def _run_item(
        self,
        item: ExecutionPlanItem,
        source: Optional[taskset.TasksetAnalysisResult],
    ) -> Optional[taskset.TasksetAnalysisResult]:
        assert self.writer is not None and self.service is not None
        terminal = self.writer.terminal(item.analysis_id)
        if terminal is not None:
            return self._load_result_state(item.analysis_id)
        prior = self._attempts_for(item.analysis_id)
        state = self._load_result_state(item.analysis_id)
        max_attempts = 2 if self.config["analysis"]["retry_policy"] == "timeout_once" else 1
        if prior and prior[-1]["solver_status"] != "TIMEOUT" and state is not None:
            resumed = AttemptExecution(
                state, state.solver_status.value, False, 0, 0, 0, 0, 0
            )
            self._finish_terminal(item, resumed, prior, state)
            return state
        if len(prior) >= max_attempts:
            final_status = prior[-1]["solver_status"]
            resumed = AttemptExecution(
                state, final_status, prior[-1]["outer_timeout"] == "True",
                0, 0, 0, 0, 0,
            )
            self._finish_terminal(item, resumed, prior, state)
            return state

        final_execution: Optional[AttemptExecution] = None
        for attempt_number in range(len(prior) + 1, max_attempts + 1):
            if self.stop_requested.is_set():
                return state
            budget = float(
                self.config["analysis"]["timeout_seconds"]
                if attempt_number == 1 else
                self.config["analysis"]["retry_timeout_seconds"]
            )
            dependency = self._dependency_status(item, source)
            request = production_runner.V93DispatchRequest(
                item.analysis_id, item.variant,
                _analysis_input(item.stored, item.cell, self.service, budget),
                source=source, dependency_check_status=dependency,
                configuration_timeout_seconds=budget,
            )
            started_at = _utc_now()
            execution = execute_isolated(request, budget)
            final_execution = execution
            result = execution.result
            attempt_serialization = 0.0
            if result is not None:
                try:
                    validate_analysis_result(
                        result, item.stored, expected_analysis_id=item.analysis_id,
                        expected_variant=item.variant, source=source,
                    )
                    if result.solver_status is taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE:
                        raise ConformanceFailure("production analyzer returned INTERNAL_CONFORMANCE_FAILURE")
                except Exception as exc:
                    self._record_failure(
                        item, type(exc).__name__, str(exc), traceback.format_exc()
                    )
                    if self.config["execution"]["fail_fast_on_p0"]:
                        self.stop_requested.set()
                    execution = AttemptExecution(
                        None, "INTERNAL_CONFORMANCE_FAILURE", False,
                        execution.solver_wall_seconds, execution.solver_cpu_seconds,
                        execution.worker_startup_seconds, execution.ipc_seconds,
                        execution.total_wall_seconds, type(exc).__name__, str(exc),
                        traceback.format_exc(),
                    )
                    result = None
                else:
                    serialization_started = time.perf_counter()
                    _pickle_atomic(self._result_state_path(item.analysis_id), result)
                    attempt_serialization = time.perf_counter() - serialization_started
                    state = result
            elif execution.solver_status == "INTERNAL_CONFORMANCE_FAILURE":
                self._record_failure(
                    item, execution.exception_type or "WorkerFailure",
                    execution.exception_message or "isolated worker failed",
                    execution.traceback_text,
                )
                if self.config["execution"]["fail_fast_on_p0"]:
                    self.stop_requested.set()
            attempt_id = _attempt_id(item.analysis_id, attempt_number)
            attempt_row = {
                "attempt_id": attempt_id,
                "analysis_id": item.analysis_id,
                "attempt_number": attempt_number,
                "parent_attempt_id": prior[-1]["attempt_id"] if prior else None,
                "timeout_budget_seconds": budget,
                "solver_status": execution.solver_status,
                "outer_timeout": execution.outer_timeout,
                "solver_wall_seconds": f"{execution.solver_wall_seconds:.9f}",
                "solver_cpu_seconds": f"{execution.solver_cpu_seconds:.9f}",
                "worker_startup_seconds": f"{execution.worker_startup_seconds:.9f}",
                "serialization_seconds": f"{attempt_serialization:.9f}",
                "ipc_seconds": f"{execution.ipc_seconds:.9f}",
                "total_wall_seconds": f"{execution.total_wall_seconds + attempt_serialization:.9f}",
                "exception_type": execution.exception_type,
                "exception_message": execution.exception_message,
                "traceback": execution.traceback_text,
                "started_at_utc": started_at,
            }
            with self._write_lock:
                self.writer.append_attempt(attempt_row)
            prior.append({key: str(value) if value is not None else "" for key, value in attempt_row.items()})
            if execution.solver_status != "TIMEOUT":
                break
        assert final_execution is not None
        self._finish_terminal(item, final_execution, prior, state)
        return state

    def _dependency_row(
        self,
        item: ExecutionPlanItem,
        source: Optional[taskset.TasksetAnalysisResult],
        target: Optional[taskset.TasksetAnalysisResult],
    ) -> Dict[str, Any]:
        source_vector = _vector_hash(
            (record.task_id, record.candidate_response_time)
            for record in source.task_records
        ) if source and source.taskset_proven else None
        target_vector = _vector_hash(target.source_candidate_vector) if target else None
        applicable = bool(
            source and target
            and production_runner._source_is_jointly_certified(source)
            and target.dependency_check_status is taskset.DependencyVectorCheckStatus.VALID
        )
        return {
            "analysis_id": item.analysis_id,
            "cell_id": item.cell.cell_id,
            "taskset_id": item.stored.taskset_id,
            "target_variant": item.variant.name,
            "source_analysis_id": item.source_analysis_id,
            "source_variant": "CW_THETA_CW",
            "source_certified": bool(source and source.taskset_proven),
            "dependency_check_status": (
                target.dependency_check_status.value if target else "INVALID"
            ),
            "source_taskset_hash": item.stored.semantic_hash,
            "target_taskset_hash": item.stored.semantic_hash,
            "source_e0": fraction_text(item.cell.exact_e0),
            "target_e0": fraction_text(item.cell.exact_e0),
            "source_numerical_mode": item.cell.numerical_mode,
            "target_numerical_mode": item.cell.numerical_mode,
            "source_vector_hash": source_vector,
            "target_vector_hash": target_vector,
            "applicable": applicable,
            "fallback_used": False,
        }

    @staticmethod
    def _candidate_map(result: Optional[taskset.TasksetAnalysisResult]) -> Dict[str, Any]:
        if result is None:
            return {}
        return {
            row.task_id: row for row in result.task_records
            if row.solver_status is taskset.TaskSolverStatus.CANDIDATE_FOUND
        }

    def _dominance_rows(
        self,
        chain: Sequence[ExecutionPlanItem],
        results: Mapping[taskset.AnalysisVariant, Optional[taskset.TasksetAnalysisResult]],
    ) -> list[Dict[str, Any]]:
        item = chain[0]
        relations = []
        present = {entry.variant for entry in chain}
        candidates = (
            ("LOCAL_VS_COMPLETE_DEADLINE", taskset.AnalysisVariant.CW_D, taskset.AnalysisVariant.LOC_D),
            ("LOCAL_VS_COMPLETE_FIXED_CW", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_CW),
            ("LOCAL_RECURSIVE_VS_COMPLETE_RECURSIVE", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_LOC),
        )
        for relation, left_variant, right_variant in candidates:
            if left_variant not in present or right_variant not in present:
                continue
            left = self._candidate_map(results.get(left_variant))
            right = self._candidate_map(results.get(right_variant))
            common = sorted(set(left) & set(right), key=int)
            if not common:
                relations.append({
                    "cell_id": item.cell.cell_id, "taskset_id": item.stored.taskset_id,
                    "exact_e0": fraction_text(item.cell.exact_e0), "relation": relation,
                    "left_variant": left_variant.name, "right_variant": right_variant.name,
                    "task_id": None, "priority_rank": None, "left_candidate": None,
                    "right_candidate": None, "reduction": None,
                    "status": "NOT_APPLICABLE",
                })
                continue
            for task_id_value in common:
                lrow, rrow = left[task_id_value], right[task_id_value]
                reduction = lrow.candidate_response_time - rrow.candidate_response_time
                status = "TIGHTER" if reduction > 0 else "EQUAL" if reduction == 0 else "VIOLATION"
                relations.append({
                    "cell_id": item.cell.cell_id, "taskset_id": item.stored.taskset_id,
                    "exact_e0": fraction_text(item.cell.exact_e0), "relation": relation,
                    "left_variant": left_variant.name, "right_variant": right_variant.name,
                    "task_id": task_id_value, "priority_rank": lrow.priority_rank,
                    "left_candidate": lrow.candidate_response_time,
                    "right_candidate": rrow.candidate_response_time,
                    "reduction": reduction, "status": status,
                })
                if status == "VIOLATION":
                    raise ConformanceFailure(
                        f"P0 dominance violation: {relation} {item.stored.taskset_id} task {task_id_value}"
                    )
        return relations

    def _run_chain(self, chain: Sequence[ExecutionPlanItem]) -> None:
        results: Dict[taskset.AnalysisVariant, Optional[taskset.TasksetAnalysisResult]] = {}
        for item in chain:
            if self.stop_requested.is_set():
                return
            source = results.get(taskset.AnalysisVariant.CW_THETA_CW)
            if item.variant is taskset.AnalysisVariant.LOC_THETA_CW and source is None:
                source = self._load_result_state(item.source_analysis_id or "")
            result = self._run_item(item, source)
            results[item.variant] = result
            if item.variant is taskset.AnalysisVariant.LOC_THETA_CW:
                with self._write_lock:
                    self._dependencies.append(self._dependency_row(item, source, result))
        try:
            rows = self._dominance_rows(chain, results)
        except ConformanceFailure as exc:
            self._record_failure(chain[0], type(exc).__name__, str(exc), traceback.format_exc())
            if self.config["execution"]["fail_fast_on_p0"]:
                self.stop_requested.set()
            return
        with self._write_lock:
            self._dominance.extend(rows)
            self._checkpoint_counter += 1
            if self._checkpoint_counter % self.config["execution"]["checkpoint_every"] == 0:
                self._checkpoint()

    def _checkpoint(self) -> None:
        assert self.writer is not None
        completed = self.writer.terminal_payloads()
        completed_ids = {payload["taskset_row"]["analysis_id"] for payload in completed}
        for row in self._requests:
            row["request_status"] = "TERMINAL" if row["analysis_id"] in completed_ids else "PLANNED"
        self.writer.materialize(
            [cell.row() for cell in self._cells], self._generated_rows,
            self._requests, self._dependencies, self._dominance, self._failures,
        )
        atomic_write_json(self.root / "checkpoint.json", {
            "config_hash": self.config_identity,
            "completed_analysis_ids": sorted(completed_ids),
            "completed_count": len(completed_ids),
            "requested_count": len(self._requests),
            "stop_requested": self.stop_requested.is_set(),
            "updated_at_utc": _utc_now(),
        })

    def run(
        self,
        *,
        resume: Optional[bool] = None,
        max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> RunOutcome:
        if max_cells is not None and max_cells <= 0:
            raise ExecutionError("max_cells must be positive")
        if max_tasksets is not None and max_tasksets <= 0:
            raise ExecutionError("max_tasksets must be positive")
        resume_value = self.config["execution"]["resume"] if resume is None else resume
        self._initialize(resume=resume_value)
        chains = self._prepare_plan(max_cells=max_cells, max_tasksets=max_tasksets)
        prior_handlers: Dict[int, Any] = {}

        def stop_handler(signum: int, frame: Any) -> None:
            self.stop_requested.set()

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                prior_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, stop_handler)
        try:
            workers = min(int(self.config["analysis"]["worker_count"]), len(chains) or 1)
            if workers == 1:
                for chain in chains:
                    self._run_chain(chain)
                    if self.stop_requested.is_set():
                        break
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(self._run_chain, chain) for chain in chains]
                    for future in as_completed(futures):
                        future.result()
                        if self.stop_requested.is_set():
                            break
        finally:
            with self._write_lock:
                self._checkpoint()
            for signum, handler in prior_handlers.items():
                signal.signal(signum, handler)
        assert self.writer is not None
        payloads = self.writer.terminal_payloads()
        counts: Dict[str, int] = {}
        for payload in payloads:
            status = payload["taskset_row"]["solver_status"]
            counts[status] = counts.get(status, 0) + 1
        return RunOutcome(
            self.root, len(self._requests), len(payloads), counts,
            self.stop_requested.is_set(),
        )
