"""V5 orchestration over the frozen V3 physical-core process slots.

This module owns only V5 attempt grouping and operational evidence.  CPU
topology discovery, SMT collapsing, affinity enforcement, process lifecycle,
hard-timeout replacement, and worker protocol execution remain owned by V3.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import pickle
import time
from typing import Any, Callable, Mapping, Sequence

from .rta4_formal_workers_v3 import (
    V3AttemptRequest,
    V3AttemptResponse,
    V3WorkerBootstrap,
    combine_attempt_results_v3,
    execute_worker_attempt_in_slot_v3,
    project_hard_timeout_result_v3,
)
from .rta4_physical_core_slots_v3 import (
    PHYSICAL_CORE_EXECUTION_BACKEND_V3,
    PhysicalCoreSlotPoolV3,
    PhysicalCoreSlotV3Error,
    PhysicalCoreV3,
    SlotCompletionV3,
    SlotStartedV3,
    SlotTimeoutV3,
    SlotWorkerExitV3,
    _mean_concurrency,
)
from .rta4_shared_energy import FrozenMapping


class RTA4PhysicalExecutionV5Error(RuntimeError):
    """Fail-closed V5 grouping, serialization, or slot protocol failure."""


@dataclass(frozen=True)
class PreparedPhysicalRecordV5:
    plan_record: Any
    worker_record: Any
    certificate: Any
    run_context: Any


def _attempt_budget(
    bootstrap: V3WorkerBootstrap, worker_record: Any, attempt_index: int,
) -> int:
    if worker_record.kind == "simulation":
        if attempt_index != 0:
            raise RTA4PhysicalExecutionV5Error(
                "simulation records permit exactly one attempt"
            )
        return int(bootstrap.simulation_timeout_seconds)
    try:
        contract = bootstrap.timeout_contract[
            str(worker_record.material["method"])
        ]
        budget = contract[
            "initial_timeout_seconds"
            if attempt_index == 0 else "retry_timeout_seconds"
        ]
    except (KeyError, TypeError) as exc:
        raise RTA4PhysicalExecutionV5Error(
            "V5 record has no matching timeout contract"
        ) from exc
    if type(budget) is not int or budget < 1:
        raise RTA4PhysicalExecutionV5Error(
            "V5 attempt timeout must be a positive integer"
        )
    return budget


def _request(
    bootstrap: V3WorkerBootstrap,
    prepared: PreparedPhysicalRecordV5,
    attempt_index: int,
) -> V3AttemptRequest:
    budget = _attempt_budget(
        bootstrap, prepared.worker_record, attempt_index,
    )
    request = V3AttemptRequest(
        prepared.worker_record,
        prepared.certificate,
        prepared.run_context,
        attempt_index,
        budget,
    )
    try:
        pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        raise RTA4PhysicalExecutionV5Error(
            "V5 physical attempt request is not serializable"
        ) from exc
    return request


def _attempt_diagnostic(
    *,
    request: V3AttemptRequest,
    worker: Any,
    started_ns: int,
    finished_ns: int,
    timed_out: bool,
    worker_exit: int | None = None,
    error_classification: str | None = None,
) -> dict[str, Any]:
    if finished_ns < started_ns:
        raise RTA4PhysicalExecutionV5Error(
            "V5 physical attempt interval is invalid"
        )
    return {
        "attempt_index": request.attempt_index,
        "worker_pid": worker.worker_pid,
        "slot_id": worker.slot_id,
        "worker_generation": worker.worker_generation,
        "logical_cpu_id": worker.logical_cpu_id,
        "physical_package_id": worker.physical_package_id,
        "physical_core_id": worker.physical_core_id,
        "affinity_mask": list(worker.affinity_mask),
        "started_monotonic_ns": started_ns,
        "finished_monotonic_ns": finished_ns,
        "timed_out": timed_out,
        "worker_exit": worker_exit,
        "error_classification": error_classification,
    }


def _infrastructure_result(
    prepared: PreparedPhysicalRecordV5,
    request: V3AttemptRequest,
    classification: str,
    *,
    malformed: bool = False,
) -> Mapping[str, Any]:
    detail = classification[:500]
    attempt = FrozenMapping({
        "attempt_index": request.attempt_index,
        "timeout_seconds": request.timeout_seconds,
        "status": "INTERNAL_ERROR",
        "runtime_wall_seconds": "0",
        "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0,
        "error_classification": detail,
    })
    common = {
        "solver_status": "INTERNAL_ERROR",
        "attempts": (attempt,),
        "failure_reason": detail,
        "failure_closed": True,
        "protocol_malformed_result": malformed,
    }
    if prepared.worker_record.kind == "simulation":
        return FrozenMapping({
            **common,
            "status": "INTERNAL_ERROR",
            "error_classification": detail,
            "runtime_wall_seconds": "0",
            "runtime_cpu_seconds": "0",
            "result": FrozenMapping({"failure_reason": detail}),
        })
    return FrozenMapping({
        **common,
        "taskset_proven": False,
        "timeout_seconds": request.timeout_seconds,
        "runtime_wall_seconds": "0",
        "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0,
    })


def _result_with_diagnostics(
    result: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    return FrozenMapping({
        **dict(result),
        "worker_backend": PHYSICAL_CORE_EXECUTION_BACKEND_V3,
        "physical_core_binding_required": True,
        "execution_attempt_diagnostics": tuple(
            FrozenMapping(dict(row)) for row in diagnostics
        ),
    })


def execute_physical_group_v5(
    *,
    worker_count: int,
    selected_cores: Sequence[PhysicalCoreV3],
    prepared_records: Sequence[PreparedPhysicalRecordV5],
    bootstrap: V3WorkerBootstrap,
    max_in_flight: int,
    terminal_callback: Callable[[Any, Mapping[str, Any]], None],
    pool_factory: Callable[..., Any] = PhysicalCoreSlotPoolV3,
) -> Mapping[str, Any]:
    """Run one V5 resource group through distinct pinned V3 slot processes."""

    if type(worker_count) is not int or worker_count < 1:
        raise RTA4PhysicalExecutionV5Error(
            "physical execution group worker_count must be positive"
        )
    if len(selected_cores) != worker_count:
        raise RTA4PhysicalExecutionV5Error(
            "physical execution group core selection size drift"
        )
    if type(max_in_flight) is not int or max_in_flight < worker_count:
        raise RTA4PhysicalExecutionV5Error(
            "max_in_flight must cover every selected physical slot"
        )
    if type(bootstrap) is not V3WorkerBootstrap:
        raise RTA4PhysicalExecutionV5Error("invalid V5 worker bootstrap")
    try:
        pickle.dumps(bootstrap, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        raise RTA4PhysicalExecutionV5Error(
            "V5 physical slot bootstrap is not serializable"
        ) from exc
    records = tuple(prepared_records)
    if len({
        row.worker_record.execution_id for row in records
    }) != len(records):
        raise RTA4PhysicalExecutionV5Error(
            "physical execution group contains duplicate executions"
        )
    for row in records:
        _request(bootstrap, row, 0)

    pool = pool_factory(
        tuple(selected_cores),
        worker_callable=execute_worker_attempt_in_slot_v3,
        worker_state=bootstrap,
        start_method="spawn",
    )
    pending = deque((row, 0) for row in records)
    active: dict[str, tuple[PreparedPhysicalRecordV5, V3AttemptRequest]] = {}
    histories: dict[str, dict[int, Mapping[str, Any]]] = {}
    diagnostics: dict[str, list[Mapping[str, Any]]] = {
        row.worker_record.execution_id: [] for row in records
    }
    started_by_task: dict[str, int] = {}
    terminal_results: dict[str, Mapping[str, Any]] = {}
    group_started = time.monotonic_ns()

    def finish(
        prepared: PreparedPhysicalRecordV5, result: Mapping[str, Any],
    ) -> None:
        execution = prepared.worker_record.execution_id
        if execution in terminal_results:
            raise RTA4PhysicalExecutionV5Error(
                "physical execution produced a duplicate terminal"
            )
        value = _result_with_diagnostics(result, diagnostics[execution])
        terminal_callback(prepared.plan_record, value)
        terminal_results[execution] = value

    def finish_failure(
        prepared: PreparedPhysicalRecordV5,
        request: V3AttemptRequest,
        classification: str,
        *,
        malformed: bool = False,
    ) -> None:
        result = _infrastructure_result(
            prepared, request, classification, malformed=malformed,
        )
        history = histories.get(prepared.worker_record.execution_id, {})
        if (
            prepared.worker_record.kind != "simulation"
            and request.attempt_index == 1
            and 0 in history
        ):
            try:
                result = combine_attempt_results_v3({
                    0: history[0],
                    1: result,
                })
            except Exception:
                result = FrozenMapping({
                    **dict(result),
                    "protocol_malformed_result": True,
                })
        finish(prepared, result)

    def finish_attempt(
        prepared: PreparedPhysicalRecordV5,
        request: V3AttemptRequest,
        result: Mapping[str, Any],
    ) -> None:
        record = prepared.worker_record
        if record.kind == "simulation":
            finish(prepared, result)
            return
        history = histories.setdefault(record.execution_id, {})
        history[request.attempt_index] = result
        if (
            result.get("solver_status") == "TIMEOUT"
            and request.attempt_index == 0
        ):
            pending.append((prepared, 1))
            return
        try:
            combined = combine_attempt_results_v3(history)
        except Exception as exc:
            finish_failure(
                prepared,
                request,
                f"MALFORMED_ATTEMPT_HISTORY:{type(exc).__name__}:{exc}",
                malformed=True,
            )
            return
        finish(prepared, combined)

    pool.start()
    try:
        while pending or active:
            for slot_id in pool.idle_slot_ids:
                if not pending or len(active) >= max_in_flight:
                    break
                prepared, attempt_index = pending.popleft()
                request = _request(bootstrap, prepared, attempt_index)
                task_id = (
                    f"{prepared.worker_record.execution_id}:{attempt_index}"
                )
                pool.submit(slot_id, task_id, request, request.timeout_seconds)
                active[task_id] = (prepared, request)
            event = pool.poll()
            if event is None:
                continue
            if isinstance(event, SlotStartedV3):
                if event.task_id not in active:
                    raise RTA4PhysicalExecutionV5Error(
                        "physical slot start lies outside active V5 attempts"
                    )
                started_by_task[event.task_id] = event.started_monotonic_ns
                continue
            if isinstance(event, SlotCompletionV3):
                try:
                    prepared, request = active.pop(event.task_id)
                except KeyError as exc:
                    raise RTA4PhysicalExecutionV5Error(
                        "physical slot completion lies outside active V5 attempts"
                    ) from exc
                diagnostics[prepared.worker_record.execution_id].append(
                    _attempt_diagnostic(
                        request=request,
                        worker=event.worker,
                        started_ns=event.started_monotonic_ns,
                        finished_ns=event.finished_monotonic_ns,
                        timed_out=False,
                        error_classification=event.error_classification,
                    )
                )
                if event.error_classification is not None:
                    finish_failure(
                        prepared,
                        request,
                        "PHYSICAL_SLOT_ATTEMPT_FAILURE:"
                        + event.error_classification,
                    )
                    continue
                response = event.result
                if (
                    type(response) is not V3AttemptResponse
                    or response.plan_record_identity
                    != prepared.worker_record.record_id
                    or response.execution_identity
                    != prepared.worker_record.execution_id
                    or response.attempt_index != request.attempt_index
                    or response.timeout_seconds != request.timeout_seconds
                    or not isinstance(response.result, Mapping)
                ):
                    finish_failure(
                        prepared,
                        request,
                        "MALFORMED_PHYSICAL_SLOT_RESPONSE",
                        malformed=True,
                    )
                    continue
                finish_attempt(prepared, request, response.result)
                continue
            if isinstance(event, SlotTimeoutV3):
                prepared, request = active.pop(event.task_id)
                pool.worker_intervals.append({
                    **event.worker.as_dict(),
                    "task_id": event.task_id,
                    "attempt_started_monotonic_ns": event.started_monotonic_ns,
                    "attempt_finished_monotonic_ns": event.timed_out_monotonic_ns,
                    "timed_out": True,
                })
                diagnostics[prepared.worker_record.execution_id].append(
                    _attempt_diagnostic(
                        request=request,
                        worker=event.worker,
                        started_ns=event.started_monotonic_ns,
                        finished_ns=event.timed_out_monotonic_ns,
                        timed_out=True,
                        error_classification="PHYSICAL_SLOT_HARD_TIMEOUT",
                    )
                )
                pool.replace(event.slot_id, timeout_kill=True)
                if prepared.worker_record.kind == "simulation":
                    finish_failure(
                        prepared,
                        request,
                        "SIMULATION_PHYSICAL_SLOT_TIMEOUT",
                    )
                    continue
                try:
                    projected = project_hard_timeout_result_v3(
                        bootstrap, request,
                    )
                except Exception as exc:
                    finish_failure(
                        prepared,
                        request,
                        f"HARD_TIMEOUT_PROJECTION_FAILURE:{type(exc).__name__}:{exc}",
                    )
                    continue
                finish_attempt(prepared, request, projected)
                continue
            if isinstance(event, SlotWorkerExitV3):
                pool.replace(event.slot_id)
                if event.task_id is None:
                    continue
                prepared, request = active.pop(event.task_id)
                now = time.monotonic_ns()
                started = started_by_task.get(event.task_id, now)
                diagnostics[prepared.worker_record.execution_id].append(
                    _attempt_diagnostic(
                        request=request,
                        worker=event.worker,
                        started_ns=started,
                        finished_ns=now,
                        timed_out=False,
                        worker_exit=event.exitcode,
                        error_classification="PHYSICAL_SLOT_WORKER_EXIT",
                    )
                )
                finish_failure(
                    prepared,
                    request,
                    f"PHYSICAL_SLOT_WORKER_EXIT:{event.exitcode}",
                )
                continue
            raise RTA4PhysicalExecutionV5Error(
                "unknown V3 physical slot event in V5 execution"
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except PhysicalCoreSlotV3Error:
        raise
    finally:
        pool.shutdown()
    group_finished = time.monotonic_ns()
    interval_pairs = [(
        int(row["attempt_started_monotonic_ns"]),
        int(row["attempt_finished_monotonic_ns"]),
    ) for row in pool.worker_intervals]
    maximum, mean = _mean_concurrency(interval_pairs)
    final_values = tuple(terminal_results.values())
    return FrozenMapping({
        "worker_count": worker_count,
        "requested_record_count": len(records),
        "completed_record_count": len(terminal_results),
        "group_started_monotonic_ns": group_started,
        "group_finished_monotonic_ns": group_finished,
        "elapsed_wall_seconds": (group_finished - group_started) / 1_000_000_000,
        "selected_physical_cores": tuple(
            FrozenMapping(row.as_dict()) for row in selected_cores
        ),
        "worker_process_ids": tuple(sorted({
            int(row["worker_pid"]) for row in pool.worker_affinity_bindings
        })),
        "worker_affinity_bindings": tuple(
            FrozenMapping(dict(row)) for row in pool.worker_affinity_bindings
        ),
        "max_concurrent_active_slots": maximum,
        "mean_concurrent_active_slots": mean,
        "slot_replacement_count": pool.slot_replacement_count,
        "timeout_kill_count": pool.timeout_kill_count,
        "terminal_timeout_count": sum(
            row.get("solver_status") == "TIMEOUT" for row in final_values
        ),
        "internal_error_count": sum(
            row.get("solver_status") == "INTERNAL_ERROR"
            or row.get("status") == "INTERNAL_ERROR"
            for row in final_values
        ),
    })


__all__ = [
    "PreparedPhysicalRecordV5",
    "RTA4PhysicalExecutionV5Error",
    "execute_physical_group_v5",
]
