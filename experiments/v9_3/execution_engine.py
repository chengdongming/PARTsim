"""Common production execution engine for v9.3 CORE-1 and CORE-2."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
import multiprocessing
from multiprocessing.connection import (
    wait as wait_for_multiprocessing_objects,
)
import os
from pathlib import Path
import pickle
import resource
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner

from .cell_model import Cell, analysis_id, expand_cells
from .config import canonical_json, config_hash, domain_hash, dump_config, fraction_text
from .formal_authorization import (
    FormalAuthorizationError, verify_authorization,
)
from .result_writer import (
    ATTEMPT_COLUMNS, REQUEST_COLUMNS,
    ResultWriter,
    atomic_write_json,
    read_csv,
)
from .taskset_store import ServiceCurveMaterial, StoredTaskset, TasksetStore, prepare_service_curve
from .validation import (
    ConformanceFailure, assert_unique, expected_attempt_id,
    validate_analysis_result, validate_attempt_artifact_contract,
    validate_terminal_result_contract,
)


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
    peak_rss_kib: Optional[int] = None
    peak_rss_scope: str = "UNAVAILABLE"
    payload_received: bool = False
    worker_cleanup_status: str = "NOT_RECORDED"
    worker_exitcode: Optional[int] = None
    worker_cleanup_seconds: float = 0.0
    failure_origin: str = "NOT_RECORDED"


@dataclass(frozen=True)
class WorkerCleanup:
    status: str
    exitcode: Optional[int]
    seconds: float


@dataclass(frozen=True)
class RunOutcome:
    output_root: Path
    requested: int
    terminal: int
    status_counts: Mapping[str, int]
    stopped: bool


_PROCESS_LIFECYCLE_LOCK = threading.Lock()
_EXITCODE_REFRESH_INTERVAL_SECONDS = 0.001


def _phase_deadline(timeout_seconds: float) -> float:
    return time.monotonic() + max(0.0, float(timeout_seconds))


def _acquire_process_lifecycle(deadline: Optional[float]) -> bool:
    """Acquire the parent bookkeeping lock within an optional deadline."""

    if deadline is None:
        _PROCESS_LIFECYCLE_LOCK.acquire()
        return True

    remaining = deadline - time.monotonic()

    if remaining <= 0.0:
        return _PROCESS_LIFECYCLE_LOCK.acquire(blocking=False)

    return _PROCESS_LIFECYCLE_LOCK.acquire(timeout=remaining)


def _start_worker_process(process: Any) -> None:
    """Serialize ``Process.start`` and its global child-registry cleanup."""

    _PROCESS_LIFECYCLE_LOCK.acquire()

    try:
        process.start()
    finally:
        _PROCESS_LIFECYCLE_LOCK.release()


def _signal_worker(
    process: Any,
    method_name: str,
    deadline: float,
) -> bool:
    """Invoke a Process signal method without exceeding the phase deadline."""

    if not _acquire_process_lifecycle(deadline):
        return False

    try:
        getattr(process, method_name)()
        return True
    finally:
        _PROCESS_LIFECYCLE_LOCK.release()


def _worker_sentinel(
    process: Any,
    deadline: float,
) -> Tuple[bool, Any]:
    """Read ``Process.sentinel`` under the lifecycle protocol."""

    if not _acquire_process_lifecycle(deadline):
        return False, None

    try:
        return True, process.sentinel
    finally:
        _PROCESS_LIFECYCLE_LOCK.release()


def _worker_exit_confirmed(
    process: Any,
    deadline: Optional[float],
) -> Tuple[bool, Optional[int]]:
    """Atomically refresh exit state and close only a confirmed Process."""

    if not _acquire_process_lifecycle(deadline):
        return False, None

    try:
        process.join(0.0)
        exitcode = process.exitcode

        if exitcode is None:
            return False, None

        process.close()
        return True, exitcode
    finally:
        _PROCESS_LIFECYCLE_LOCK.release()


def _confirm_worker_exit(
    process: Any,
    deadline: float,
) -> Tuple[bool, Optional[int]]:
    """Confirm exit by deadline without holding the lock during waiting."""

    confirmed, exitcode = _worker_exit_confirmed(
        process,
        deadline,
    )

    if confirmed:
        return True, exitcode

    acquired, sentinel = _worker_sentinel(
        process,
        deadline,
    )

    if not acquired:
        return False, None

    while True:
        remaining = deadline - time.monotonic()

        if remaining <= 0.0:
            return _worker_exit_confirmed(
                process,
                deadline,
            )

        try:
            ready = wait_for_multiprocessing_objects(
                [sentinel],
                timeout=remaining,
            )
        except (OSError, TypeError, ValueError):
            return _worker_exit_confirmed(
                process,
                deadline,
            )

        confirmed, exitcode = _worker_exit_confirmed(
            process,
            deadline,
        )

        if confirmed:
            return True, exitcode

        if not ready:
            return False, None

        remaining = deadline - time.monotonic()

        if remaining <= 0.0:
            return False, None

        # A ready sentinel means the kernel observed exit, but CPython's
        # shared Popen.returncode may still be awaiting another thread's
        # serialized poll. Yield outside the lock and retry within the same
        # deadline rather than converting that transient state into P0.
        time.sleep(min(
            _EXITCODE_REFRESH_INTERVAL_SECONDS,
            remaining,
        ))


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
        peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            peak_rss //= 1024
        payload = (
            "ok", result, time.perf_counter() - wall_started,
            time.process_time() - cpu_started, peak_rss,
        )
    except BaseException as exc:
        peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            peak_rss //= 1024
        payload = (
            "error", type(exc).__name__, str(exc), traceback.format_exc(),
            time.perf_counter() - wall_started, time.process_time() - cpu_started,
            peak_rss,
        )
    try:
        sending.send(payload)
    finally:
        sending.close()


def _reap_worker(
    process: Any,
    *,
    initial_join_seconds: float,
    terminate_join_seconds: float,
    kill_join_seconds: float,
    kill_confirmation_seconds: float,
) -> WorkerCleanup:
    """Boundedly reap a worker without interpreting its analysis payload."""

    cleanup_started = time.perf_counter()
    initial_deadline = _phase_deadline(initial_join_seconds)
    confirmed, exitcode = _confirm_worker_exit(
        process,
        initial_deadline,
    )

    if confirmed:
        status = "EXITED_NORMALLY"
    else:
        terminate_deadline = _phase_deadline(
            terminate_join_seconds
        )
        _signal_worker(
            process,
            "terminate",
            terminate_deadline,
        )
        confirmed, exitcode = _confirm_worker_exit(
            process,
            terminate_deadline,
        )

        if confirmed:
            status = "REAPED_AFTER_TERMINATE"
        else:
            kill_deadline = _phase_deadline(
                kill_join_seconds
            )
            kill_sent = _signal_worker(
                process,
                "kill",
                kill_deadline,
            )
            confirmed, exitcode = _confirm_worker_exit(
                process,
                kill_deadline,
            )

            if confirmed:
                status = "REAPED_AFTER_KILL"
            else:
                confirmation_deadline = _phase_deadline(
                    kill_confirmation_seconds
                )

                if not kill_sent:
                    _signal_worker(
                        process,
                        "kill",
                        confirmation_deadline,
                    )

                confirmed, exitcode = _confirm_worker_exit(
                    process,
                    confirmation_deadline,
                )

                if confirmed:
                    status = "REAPED_AFTER_KILL"
                else:
                    status = "UNREAPED_AFTER_KILL"

    elapsed = time.perf_counter() - cleanup_started

    return WorkerCleanup(
        status=status,
        exitcode=exitcode,
        seconds=elapsed,
    )


def execute_isolated(
    request: production_runner.V93DispatchRequest,
    timeout_seconds: float,
    *,
    start_method: str = "spawn",
    worker_target: Optional[Any] = None,
    post_payload_join_seconds: float = 5.0,
    terminate_join_seconds: float = 5.0,
    kill_join_seconds: float = 5.0,
    kill_confirmation_seconds: float = 5.0,
) -> AttemptExecution:
    """Execute one production request with a hard pre-payload wall boundary.

    Once a complete payload has been received, that payload remains
    authoritative. Subsequent worker cleanup cannot rewrite solver status.
    """

    context = multiprocessing.get_context(start_method)
    receiving, sending = context.Pipe(duplex=False)
    started_event = context.Event()

    target = (
        _analysis_worker
        if worker_target is None
        else worker_target
    )

    process = context.Process(
        target=target,
        args=(sending, started_event, request),
        daemon=False,
    )

    total_started = time.perf_counter()
    _start_worker_process(process)
    sending.close()

    startup_wait = min(
        10.0,
        max(1.0, timeout_seconds),
    )

    if not started_event.wait(startup_wait):
        startup = time.perf_counter() - total_started
        receiving.close()

        cleanup = _reap_worker(
            process,
            initial_join_seconds=0.0,
            terminate_join_seconds=terminate_join_seconds,
            kill_join_seconds=kill_join_seconds,
            kill_confirmation_seconds=kill_confirmation_seconds,
        )

        total = time.perf_counter() - total_started

        return AttemptExecution(
            result=None,
            solver_status="TIMEOUT",
            outer_timeout=True,
            solver_wall_seconds=0.0,
            solver_cpu_seconds=0.0,
            worker_startup_seconds=startup,
            ipc_seconds=max(
                0.0,
                total - startup - cleanup.seconds,
            ),
            total_wall_seconds=total,
            exception_type="WorkerStartupTimeout",
            exception_message=(
                "analysis worker did not start"
            ),
            traceback_text=None,
            payload_received=False,
            worker_cleanup_status=cleanup.status,
            worker_exitcode=cleanup.exitcode,
            worker_cleanup_seconds=cleanup.seconds,
            failure_origin="OUTER_TIMEOUT_STARTUP",
        )

    startup = time.perf_counter() - total_started

    # The analyzer owns the exact configured budget. A small transport grace
    # prevents classifying a returned inner TIMEOUT as an IPC failure.
    transport_grace = min(
        1.0,
        max(0.1, timeout_seconds * 0.05),
    )

    payload: Any = None
    receive_error: Optional[
        tuple[str, str, str]
    ] = None

    try:
        if not receiving.poll(
            timeout_seconds + transport_grace
        ):
            cleanup = _reap_worker(
                process,
                initial_join_seconds=0.0,
                terminate_join_seconds=(
                    terminate_join_seconds
                ),
                kill_join_seconds=kill_join_seconds,
                kill_confirmation_seconds=(
                    kill_confirmation_seconds
                ),
            )

            total = time.perf_counter() - total_started

            return AttemptExecution(
                result=None,
                solver_status="TIMEOUT",
                outer_timeout=True,
                solver_wall_seconds=timeout_seconds,
                solver_cpu_seconds=0.0,
                worker_startup_seconds=startup,
                ipc_seconds=max(
                    0.0,
                    total
                    - startup
                    - timeout_seconds
                    - cleanup.seconds,
                ),
                total_wall_seconds=total,
                exception_type="ConfigurationTimeout",
                exception_message=(
                    "hard per-configuration timeout"
                ),
                traceback_text=None,
                payload_received=False,
                worker_cleanup_status=cleanup.status,
                worker_exitcode=cleanup.exitcode,
                worker_cleanup_seconds=cleanup.seconds,
                failure_origin="OUTER_TIMEOUT_CONFIGURATION",
            )

        payload = receiving.recv()

    except Exception as exc:
        receive_error = (
            type(exc).__name__,
            str(exc),
            traceback.format_exc(),
        )

    finally:
        receiving.close()

    if receive_error is not None:
        cleanup = _reap_worker(
            process,
            initial_join_seconds=0.0,
            terminate_join_seconds=terminate_join_seconds,
            kill_join_seconds=kill_join_seconds,
            kill_confirmation_seconds=kill_confirmation_seconds,
        )

        total = time.perf_counter() - total_started

        return AttemptExecution(
            result=None,
            solver_status=(
                "INTERNAL_CONFORMANCE_FAILURE"
            ),
            outer_timeout=False,
            solver_wall_seconds=0.0,
            solver_cpu_seconds=0.0,
            worker_startup_seconds=startup,
            ipc_seconds=max(
                0.0,
                total - startup - cleanup.seconds,
            ),
            total_wall_seconds=total,
            exception_type=receive_error[0],
            exception_message=receive_error[1],
            traceback_text=receive_error[2],
            payload_received=False,
            worker_cleanup_status=cleanup.status,
            worker_exitcode=cleanup.exitcode,
            worker_cleanup_seconds=cleanup.seconds,
            failure_origin="IPC_RECEIVE_FAILURE",
        )

    # A complete payload has crossed the IPC boundary. From this point on,
    # cleanup state is recorded independently and cannot replace the result.
    cleanup = _reap_worker(
        process,
        initial_join_seconds=post_payload_join_seconds,
        terminate_join_seconds=terminate_join_seconds,
        kill_join_seconds=kill_join_seconds,
        kill_confirmation_seconds=kill_confirmation_seconds,
    )

    total = time.perf_counter() - total_started

    if not isinstance(payload, tuple) or not payload:
        return AttemptExecution(
            result=None,
            solver_status=(
                "INTERNAL_CONFORMANCE_FAILURE"
            ),
            outer_timeout=False,
            solver_wall_seconds=0.0,
            solver_cpu_seconds=0.0,
            worker_startup_seconds=startup,
            ipc_seconds=max(
                0.0,
                total - startup - cleanup.seconds,
            ),
            total_wall_seconds=total,
            exception_type="InvalidWorkerPayload",
            exception_message=(
                "worker payload is not a non-empty tuple"
            ),
            traceback_text=None,
            payload_received=True,
            worker_cleanup_status=cleanup.status,
            worker_exitcode=cleanup.exitcode,
            worker_cleanup_seconds=cleanup.seconds,
            failure_origin="INVALID_WORKER_PAYLOAD_SHAPE",
        )

    try:
        payload_kind = payload[0]

        if payload_kind == "ok":
            result = payload[1]
            solver_wall = float(payload[2])
            solver_cpu = float(payload[3])
            peak_rss = int(payload[4])

            return AttemptExecution(
                result=result,
                solver_status=result.solver_status.value,
                outer_timeout=False,
                solver_wall_seconds=solver_wall,
                solver_cpu_seconds=solver_cpu,
                worker_startup_seconds=startup,
                ipc_seconds=max(
                    0.0,
                    total
                    - startup
                    - solver_wall
                    - cleanup.seconds,
                ),
                total_wall_seconds=total,
                peak_rss_kib=peak_rss,
                peak_rss_scope=(
                    "CHILD_PROCESS_MAX_RSS_"
                    "INCLUDES_SHARED_LIBRARIES"
                ),
                payload_received=True,
                worker_cleanup_status=cleanup.status,
                worker_exitcode=cleanup.exitcode,
                worker_cleanup_seconds=cleanup.seconds,
                failure_origin="ANALYZER_RESULT",
            )

        if payload_kind == "error":
            solver_wall = float(payload[4])
            solver_cpu = float(payload[5])
            peak_rss = int(payload[6])

            return AttemptExecution(
                result=None,
                solver_status=(
                    "INTERNAL_CONFORMANCE_FAILURE"
                ),
                outer_timeout=False,
                solver_wall_seconds=solver_wall,
                solver_cpu_seconds=solver_cpu,
                worker_startup_seconds=startup,
                ipc_seconds=max(
                    0.0,
                    total
                    - startup
                    - solver_wall
                    - cleanup.seconds,
                ),
                total_wall_seconds=total,
                exception_type=str(payload[1]),
                exception_message=str(payload[2]),
                traceback_text=str(payload[3]),
                peak_rss_kib=peak_rss,
                peak_rss_scope=(
                    "CHILD_PROCESS_MAX_RSS_"
                    "INCLUDES_SHARED_LIBRARIES"
                ),
                payload_received=True,
                worker_cleanup_status=cleanup.status,
                worker_exitcode=cleanup.exitcode,
                worker_cleanup_seconds=cleanup.seconds,
                failure_origin="WORKER_ERROR_PAYLOAD",
            )

        raise ValueError(
            f"unknown worker payload kind: {payload_kind!r}"
        )

    except Exception as exc:
        return AttemptExecution(
            result=None,
            solver_status=(
                "INTERNAL_CONFORMANCE_FAILURE"
            ),
            outer_timeout=False,
            solver_wall_seconds=0.0,
            solver_cpu_seconds=0.0,
            worker_startup_seconds=startup,
            ipc_seconds=max(
                0.0,
                total - startup - cleanup.seconds,
            ),
            total_wall_seconds=total,
            exception_type="InvalidWorkerPayload",
            exception_message=str(exc),
            traceback_text=traceback.format_exc(),
            payload_received=True,
            worker_cleanup_status=cleanup.status,
            worker_exitcode=cleanup.exitcode,
            worker_cleanup_seconds=cleanup.seconds,
            failure_origin="INVALID_WORKER_PAYLOAD_CONTENT",
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
        # Provenance is copied from the final persisted attempt.  It is not
        # inferred from solver status, timeout flags, or exception fields.
        "failure_origin": final_attempt["failure_origin"],
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
    return expected_attempt_id(analysis_id_value, attempt_number)


def _pickle_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class ExecutionEngine:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        service_override: Optional[ServiceCurveMaterial] = None,
        store_override: Optional[TasksetStore] = None,
        authorization_path: Optional[Path] = None,
        source_config_path: Optional[Path] = None,
        prepared_config_path: Optional[Path] = None,
    ) -> None:
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
        self._service_override = service_override
        self._store_override = store_override
        self._authorization_path = authorization_path
        self._source_config_path = source_config_path
        self._prepared_config_path = prepared_config_path
        self._authorization_seal: Optional[Dict[str, Any]] = None

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
        seal_path = self.root / "formal_authorization_seal.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("config_hash") != self.config_identity:
                raise ExecutionError("configuration hash mismatch; refusing to resume")
            if not resume:
                raise ExecutionError("run directory already exists; use --resume")
            if not seal_path.is_file():
                raise ExecutionError("run is missing its formal authorization seal")
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
            if bool(seal.get("formal_large_scale_run")):
                current = verify_authorization(
                    self.config,
                    authorization_path=self._authorization_path,
                    source_freeze_config=self._source_config_path,
                    prepared_config=self._prepared_config_path,
                    project_root=Path(__file__).resolve().parents[2],
                )
                if canonical_json(current) != canonical_json(seal):
                    raise ExecutionError("formal authorization changed during resume")
            elif self._authorization_path is not None:
                raise ExecutionError("cannot promote a nonformal run during resume")
            if (
                metadata.get("formal_large_scale_run")
                != seal.get("formal_large_scale_run")
                or metadata.get("formal_authorization_id")
                != seal.get("authorization_id")
            ):
                raise ExecutionError("run metadata/authorization seal mismatch")
            self._authorization_seal = dict(seal)
        else:
            seal = verify_authorization(
                self.config,
                authorization_path=self._authorization_path,
                source_freeze_config=self._source_config_path,
                prepared_config=self._prepared_config_path,
                project_root=Path(__file__).resolve().parents[2],
            )
            self._authorization_seal = dict(seal)
            atomic_write_json(seal_path, seal)
            metadata = {
                "schema": "ASAP_BLOCK_V9_3_FORMAL_RUN_V1",
                "experiment_id": self.config["experiment_id"],
                "core": self.config["core"],
                "config_hash": self.config_identity,
                "created_at_utc": _utc_now(),
                "git_head": self._git_head(),
                "production_entry": "asap_block_rta_v9_3_taskset.analyze_taskset_v9_3",
                "formal_large_scale_run": seal["formal_large_scale_run"],
                "formal_authorization_id": seal["authorization_id"],
            }
            atomic_write_json(metadata_path, metadata)
            dump_config(self.config, self.root / "run_config.yaml")
        self.writer = ResultWriter(self.root)
        # A resumed P0 run must never erase the already-recorded failure audit.
        self._failures = list(read_csv(self.root / "failures.csv"))
        self.service = self._service_override or prepare_service_curve(self.config, self.root)
        self.store = self._store_override or TasksetStore(
            Path(self.config["execution"]["taskset_store"]), self.config, self.service
        )
        if hasattr(self.store, "verify_pairing_manifest"):
            self.store.verify_pairing_manifest(
                require_complete=bool(
                    self._authorization_seal
                    and self._authorization_seal["formal_large_scale_run"]
                )
            )
            metadata["pairing_manifest_id"] = self.store.manifest_document()[
                "pairing_id"
            ]
            atomic_write_json(metadata_path, metadata)

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
        taskset_index_start = self.config["grid"].get("taskset_index_start", 0)
        generated_rows: Dict[str, Dict[str, Any]] = {}
        for cell in cells:
            for index in range(taskset_index_start, taskset_index_start + per_cell):
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

    @staticmethod
    def _csv_text(value: Any) -> str:
        return "" if value is None else str(value)

    def _validate_persisted_request_plan(self, *, resume: bool) -> None:
        """Make the persisted request journal authoritative before execution."""

        assert self.writer is not None
        persisted = read_csv(self.root / "analysis_requests.csv")
        if not resume:
            if persisted:
                raise ExecutionError("new run directory contains persisted requests")
            self._checkpoint()
            return
        try:
            assert_unique(persisted, "analysis_id")
        except ConformanceFailure as exc:
            raise ExecutionError(str(exc)) from exc
        expected = {row["analysis_id"]: row for row in self._requests}
        actual = {row["analysis_id"]: row for row in persisted}
        if set(actual) != set(expected):
            raise ExecutionError("persisted request set does not match the active plan")
        for analysis_id_value, planned in expected.items():
            row = actual[analysis_id_value]
            for column in REQUEST_COLUMNS:
                if column == "request_status":
                    continue
                if row.get(column, "") != self._csv_text(planned.get(column)):
                    raise ExecutionError(
                        f"persisted request mismatch for {analysis_id_value}: {column}"
                    )
            terminal_exists = (
                self.writer.finals / f"{analysis_id_value}.json"
            ).is_file()
            expected_status = "TERMINAL" if terminal_exists else "PLANNED"
            if row.get("request_status") != expected_status:
                raise ExecutionError(
                    f"persisted request/terminal status mismatch for {analysis_id_value}"
                )

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
        try:
            with path.open("rb") as handle:
                result = pickle.load(handle)
        except Exception as exc:
            raise ExecutionError("unreadable resumed analyzer state") from exc
        if not isinstance(result, taskset.TasksetAnalysisResult) or result.analysis_id != analysis_id_value:
            raise ExecutionError("invalid resumed analyzer state")
        return result

    def _validate_attempt_artifacts(
        self,
        item: ExecutionPlanItem,
        source: Optional[taskset.TasksetAnalysisResult],
        state: Optional[taskset.TasksetAnalysisResult],
        attempts: Sequence[Mapping[str, str]],
        *,
        terminal_row: Optional[Mapping[str, object]] = None,
    ) -> None:
        assert self.service is not None
        try:
            validate_attempt_artifact_contract(
                attempts,
                expected_analysis_id=item.analysis_id,
                expected_variant=item.variant,
                retry_policy=self.config["analysis"]["retry_policy"],
                initial_timeout_seconds=self.config["analysis"]["timeout_seconds"],
                retry_timeout_seconds=self.config["analysis"].get(
                    "retry_timeout_seconds"
                ),
                stored=item.stored,
                expected_context=_dependency_context(
                    item.stored, item.cell, self.service
                ),
                expected_source_analysis_id=item.source_analysis_id,
                source=source,
                terminal_row=terminal_row,
                state=state,
            )
        except (ConformanceFailure, taskset.CertificationError) as exc:
            if state is not None:
                detail = f"resumed analyzer state failed conformance: {exc}"
            elif "requires analyzer state" in str(exc):
                detail = (
                    "persisted analyzer attempt is missing its analyzer state: "
                    f"{exc}"
                )
            else:
                detail = str(exc)
            raise ExecutionError(detail) from exc

    def _validate_terminal_payload(
        self,
        item: ExecutionPlanItem,
        terminal: Mapping[str, Any],
        source: Optional[taskset.TasksetAnalysisResult],
        state: Optional[taskset.TasksetAnalysisResult],
        attempts: Sequence[Mapping[str, str]],
    ) -> None:
        if set(terminal) != {"taskset_row", "task_rows"}:
            raise ExecutionError("terminal payload shape mismatch")
        row = terminal.get("taskset_row")
        task_rows = terminal.get("task_rows")
        if not isinstance(row, dict) or not isinstance(task_rows, list):
            raise ExecutionError("terminal payload types are invalid")
        expected_identity = {
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
            "analysis_variant": item.variant.name,
            "method_role": taskset.ROLE_BY_VARIANT[item.variant].value,
        }
        assert self.service is not None
        try:
            validate_terminal_result_contract(
                attempts,
                expected_analysis_id=item.analysis_id,
                expected_variant=item.variant,
                retry_policy=self.config["analysis"]["retry_policy"],
                initial_timeout_seconds=(
                    self.config["analysis"]["timeout_seconds"]
                ),
                retry_timeout_seconds=self.config["analysis"].get(
                    "retry_timeout_seconds"
                ),
                stored=item.stored,
                expected_context=_dependency_context(
                    item.stored, item.cell, self.service
                ),
                expected_identity=expected_identity,
                terminal_row=row,
                terminal_task_rows=task_rows,
                expected_source_analysis_id=item.source_analysis_id,
                source=source,
                state=state,
                expected_task_rows=(
                    _task_rows(item, state) if state is not None else None
                ),
            )
        except (ConformanceFailure, taskset.CertificationError) as exc:
            if state is not None:
                detail = f"resumed analyzer state failed conformance: {exc}"
            else:
                detail = str(exc)
            raise ExecutionError(detail) from exc

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
        source: Optional[taskset.TasksetAnalysisResult],
    ) -> None:
        assert self.writer is not None
        draft = _terminal_payload(
            item, execution, attempts, result=result, serialization_seconds=0.0
        )
        self._validate_terminal_payload(
            item, draft, source, result, attempts
        )
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
        prior = self._attempts_for(item.analysis_id)
        state = self._load_result_state(item.analysis_id)
        if terminal is not None:
            self._validate_terminal_payload(
                item, terminal, source, state, prior
            )
            return state
        if prior or state is not None:
            self._validate_attempt_artifacts(
                item, source, state, prior
            )
        max_attempts = 2 if self.config["analysis"]["retry_policy"] == "timeout_once" else 1
        if prior and prior[-1]["solver_status"] != "TIMEOUT":
            resumed = AttemptExecution(
                state, prior[-1]["solver_status"], False, 0, 0, 0, 0, 0
            )
            self._finish_terminal(item, resumed, prior, state, source)
            return state
        if len(prior) >= max_attempts:
            final_status = prior[-1]["solver_status"]
            resumed = AttemptExecution(
                state, final_status, prior[-1]["outer_timeout"] == "True",
                0, 0, 0, 0, 0,
            )
            self._finish_terminal(item, resumed, prior, state, source)
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
                source=source, source_analysis_id=item.source_analysis_id,
                dependency_check_status=dependency,
                configuration_timeout_seconds=budget,
            )
            started_at = _utc_now()
            execution = execute_isolated(request, budget)
            result = execution.result

            if (
                result is not None
                and execution.payload_received
                and execution.worker_cleanup_status
                == "UNREAPED_AFTER_KILL"
            ):
                self._record_failure(
                    item,
                    "WorkerUnreapedAfterPayload",
                    (
                        "worker returned a complete payload "
                        "but remained alive after terminate/kill"
                    ),
                    None,
                )

                if self.config["execution"]["fail_fast_on_p0"]:
                    self.stop_requested.set()

            attempt_serialization = 0.0
            if result is not None:
                try:
                    validate_analysis_result(
                        result, item.stored, expected_analysis_id=item.analysis_id,
                        expected_variant=item.variant,
                        expected_context=_dependency_context(
                            item.stored, item.cell, self.service
                        ),
                        expected_source_analysis_id=item.source_analysis_id,
                        source=source,
                    )
                    if result.solver_status is taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE:
                        raise ConformanceFailure("production analyzer returned INTERNAL_CONFORMANCE_FAILURE")
                except Exception as exc:
                    self._record_failure(
                        item, type(exc).__name__, str(exc), traceback.format_exc()
                    )
                    if self.config["execution"]["fail_fast_on_p0"]:
                        self.stop_requested.set()
                    execution = replace(
                        execution,
                        result=None,
                        solver_status="INTERNAL_CONFORMANCE_FAILURE",
                        outer_timeout=False,
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                        traceback_text=traceback.format_exc(),
                        failure_origin="RESULT_VALIDATION_FAILURE",
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
            if result is None and state is not None:
                state_path = self._result_state_path(item.analysis_id)
                if state_path.exists():
                    state_path.unlink()
                state = None
            # Capture the post-validation execution object. Validation may
            # rewrite a payload-bearing success into a fail-closed terminal
            # execution, which must also govern terminal materialization.
            final_execution = execution

            attempt_id = _attempt_id(item.analysis_id, attempt_number)
            attempt_row = {
                "attempt_id": attempt_id,
                "analysis_id": item.analysis_id,
                "attempt_number": attempt_number,
                "parent_attempt_id": prior[-1]["attempt_id"] if prior else None,
                "timeout_budget_seconds": budget,
                "solver_status": execution.solver_status,
                "failure_origin": execution.failure_origin,
                "outer_timeout": execution.outer_timeout,
                "solver_wall_seconds": f"{execution.solver_wall_seconds:.9f}",
                "solver_cpu_seconds": f"{execution.solver_cpu_seconds:.9f}",
                "worker_startup_seconds": f"{execution.worker_startup_seconds:.9f}",
                "serialization_seconds": f"{attempt_serialization:.9f}",
                "ipc_seconds": f"{execution.ipc_seconds:.9f}",
                "payload_received": execution.payload_received,
                "worker_cleanup_status": execution.worker_cleanup_status,
                "worker_exitcode": execution.worker_exitcode,
                "worker_cleanup_seconds": f"{execution.worker_cleanup_seconds:.9f}",
                "total_wall_seconds": f"{execution.total_wall_seconds + attempt_serialization:.9f}",
                "exception_type": execution.exception_type,
                "exception_message": execution.exception_message,
                "traceback": execution.traceback_text,
                "started_at_utc": started_at,
            }
            with self._write_lock:
                self.writer.append_attempt(attempt_row)
                self._observe_attempt(item, attempt_row, execution)
            prior.append({key: str(value) if value is not None else "" for key, value in attempt_row.items()})
            if execution.solver_status != "TIMEOUT":
                break
        assert final_execution is not None
        self._finish_terminal(item, final_execution, prior, state, source)
        return state

    def _observe_attempt(
        self,
        item: ExecutionPlanItem,
        attempt_row: Mapping[str, Any],
        execution: AttemptExecution,
    ) -> None:
        """Optional additive resource hook for CORE-5; formal tables are unchanged."""


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
            source = None
            if item.variant is taskset.AnalysisVariant.LOC_THETA_CW:
                source = results.get(taskset.AnalysisVariant.CW_THETA_CW)
                if source is None:
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
        requested_ids = {row["analysis_id"] for row in self._requests}
        unexpected = completed_ids - requested_ids
        if unexpected:
            raise ExecutionError(
                f"terminal results do not belong to the active plan: {sorted(unexpected)}"
            )
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
        if hasattr(self.store, "manifest_document"):
            atomic_write_json(
                self.root / "taskset_pairing_manifest.json",
                self.store.manifest_document(),
            )

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
        self._validate_persisted_request_plan(resume=resume_value)
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
        if not self.stop_requested.is_set() and len(payloads) != len(self._requests):
            raise ExecutionError(
                "run finished without one terminal result per request"
            )
        counts: Dict[str, int] = {}
        for payload in payloads:
            status = payload["taskset_row"]["solver_status"]
            counts[status] = counts.get(status, 0) + 1
        return RunOutcome(
            self.root, len(self._requests), len(payloads), counts,
            self.stop_requested.is_set(),
        )
