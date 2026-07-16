"""Fail-closed conformance checks around production analyzer results."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

import asap_block_rta_v9_3_taskset as taskset

from .config import domain_hash
from .result_writer import TASKSET_RESULT_COLUMNS
from .taskset_store import StoredTaskset


class ConformanceFailure(RuntimeError):
    pass


ALLOWED_ATTEMPT_SOLVER_STATUSES = frozenset({
    "COMPLETED",
    "NO_CANDIDATE",
    "TIMEOUT",
    "NUMERIC_ERROR",
    "NOT_APPLICABLE_DEPENDENCY",
    "INTERNAL_CONFORMANCE_FAILURE",
})
ALLOWED_TERMINAL_ORIGINS = frozenset({
    "PRODUCTION_ANALYZER", "OUTER_WORKER",
})
ALLOWED_WORKER_CLEANUP_STATUSES = frozenset({
    "EXITED_NORMALLY",
    "REAPED_AFTER_TERMINATE",
    "REAPED_AFTER_KILL",
    "UNREAPED_AFTER_KILL",
})
OUTER_TIMEOUT_EXCEPTION_TYPES = frozenset({
    "WorkerStartupTimeout", "ConfigurationTimeout",
})
ATTEMPT_FAILURE_ORIGINS = frozenset({
    "ANALYZER_RESULT",
    "OUTER_TIMEOUT_STARTUP",
    "OUTER_TIMEOUT_CONFIGURATION",
    "IPC_RECEIVE_FAILURE",
    "INVALID_WORKER_PAYLOAD_SHAPE",
    "INVALID_WORKER_PAYLOAD_CONTENT",
    "WORKER_ERROR_PAYLOAD",
    "RESULT_VALIDATION_FAILURE",
})
OUTER_TIMEOUT_ORIGINS = frozenset({
    "OUTER_TIMEOUT_STARTUP", "OUTER_TIMEOUT_CONFIGURATION",
})
INTERNAL_FAILURE_ORIGINS = frozenset({
    "IPC_RECEIVE_FAILURE",
    "INVALID_WORKER_PAYLOAD_SHAPE",
    "INVALID_WORKER_PAYLOAD_CONTENT",
    "WORKER_ERROR_PAYLOAD",
    "RESULT_VALIDATION_FAILURE",
})
_OUTER_TIMEOUT_DETAILS = {
    "OUTER_TIMEOUT_STARTUP": (
        "WorkerStartupTimeout", "analysis worker did not start",
    ),
    "OUTER_TIMEOUT_CONFIGURATION": (
        "ConfigurationTimeout", "hard per-configuration timeout",
    ),
}


def _canonical_csv_bool(row: Mapping[str, str], field: str) -> bool:
    value = row.get(field)
    if value not in {"True", "False"}:
        raise ConformanceFailure(
            f"attempt {field} must use canonical True/False text"
        )
    return value == "True"


def _validate_worker_cleanup(row: Mapping[str, str]) -> Optional[int]:
    status = row.get("worker_cleanup_status")
    if status not in ALLOWED_WORKER_CLEANUP_STATUSES:
        raise ConformanceFailure("attempt worker cleanup status is invalid")
    exitcode = row.get("worker_exitcode", "")
    if status == "UNREAPED_AFTER_KILL":
        if exitcode != "":
            raise ConformanceFailure(
                "unreaped worker cleanup must not claim an exitcode"
            )
        return None
    if not isinstance(exitcode, str) or exitcode == "":
        raise ConformanceFailure(
            "confirmed worker cleanup requires an exitcode"
        )
    try:
        parsed = int(exitcode)
    except ValueError as exc:
        raise ConformanceFailure("worker exitcode is not an integer") from exc
    if str(parsed) != exitcode:
        raise ConformanceFailure("worker exitcode is not canonical")
    return parsed


def _validate_attempt_payload_semantics(row: Mapping[str, str]) -> None:
    status = row.get("solver_status")
    if status not in ALLOWED_ATTEMPT_SOLVER_STATUSES:
        raise ConformanceFailure("attempt solver_status is not frozen")
    outer_timeout = _canonical_csv_bool(row, "outer_timeout")
    payload_received = _canonical_csv_bool(row, "payload_received")
    exitcode = _validate_worker_cleanup(row)
    failure_origin = row.get("failure_origin")
    if failure_origin not in ATTEMPT_FAILURE_ORIGINS:
        raise ConformanceFailure("attempt failure_origin is not frozen")
    exception_type = row.get("exception_type", "")
    exception_message = row.get("exception_message", "")
    traceback_text = row.get("traceback", "")

    if failure_origin == "ANALYZER_RESULT":
        if status == "INTERNAL_CONFORMANCE_FAILURE":
            raise ConformanceFailure(
                "analyzer result failure origin is incompatible with solver_status"
            )
        if outer_timeout:
            if status != "TIMEOUT":
                raise ConformanceFailure(
                    "outer timeout solver_status must be TIMEOUT"
                )
            if payload_received:
                raise ConformanceFailure(
                    "outer_timeout=True requires payload_received=False"
                )
            raise ConformanceFailure(
                "outer timeout requires a frozen outer-timeout failure origin"
            )
        if not payload_received:
            raise ConformanceFailure(
                "analyzer result requires payload_received=True"
            )
        if exception_type or exception_message or traceback_text:
            raise ConformanceFailure(
                "analyzer result must not carry worker exception fields"
            )
        return

    if failure_origin in OUTER_TIMEOUT_ORIGINS:
        if status != "TIMEOUT":
            raise ConformanceFailure(
                "outer timeout solver_status must be TIMEOUT"
            )
        if not outer_timeout:
            raise ConformanceFailure(
                "outer timeout failure origin requires outer_timeout=True"
            )
        if payload_received:
            raise ConformanceFailure(
                "outer_timeout=True requires payload_received=False"
            )
        expected_type, expected_message = _OUTER_TIMEOUT_DETAILS[failure_origin]
        if (
            exception_type != expected_type
            or exception_message != expected_message
            or traceback_text
        ):
            raise ConformanceFailure(
                "outer timeout exception payload is inconsistent with failure origin"
            )
        return

    if status != "INTERNAL_CONFORMANCE_FAILURE" or outer_timeout:
        raise ConformanceFailure(
            "internal failure origin requires INTERNAL_CONFORMANCE_FAILURE "
            "and outer_timeout=False"
        )

    if failure_origin == "IPC_RECEIVE_FAILURE":
        if payload_received or not exception_type or not traceback_text:
            raise ConformanceFailure(
                "IPC receive failure requires no payload, exception_type, and traceback"
            )
        if (
            row.get("worker_cleanup_status") == "EXITED_NORMALLY"
            and exitcode == 0
        ):
            raise ConformanceFailure(
                "IPC receive failure with normal cleanup requires a nonzero exitcode"
            )
        return

    if not payload_received:
        raise ConformanceFailure(
            "worker internal failure origin requires payload_received=True"
        )
    if failure_origin == "INVALID_WORKER_PAYLOAD_SHAPE":
        if (
            exception_type != "InvalidWorkerPayload"
            or exception_message != "worker payload is not a non-empty tuple"
            or traceback_text
        ):
            raise ConformanceFailure(
                "invalid worker payload shape exception matrix is inconsistent"
            )
        return
    if failure_origin == "INVALID_WORKER_PAYLOAD_CONTENT":
        if (
            exception_type != "InvalidWorkerPayload"
            or not exception_message
            or not traceback_text
        ):
            raise ConformanceFailure(
                "invalid worker payload content requires message and traceback"
            )
        return
    if failure_origin == "WORKER_ERROR_PAYLOAD":
        if not exception_type:
            raise ConformanceFailure(
                "worker error payload requires exception_type"
            )
        if not traceback_text:
            raise ConformanceFailure(
                "worker error payload requires traceback"
            )
        return
    if failure_origin == "RESULT_VALIDATION_FAILURE":
        if not exception_type or not exception_message or not traceback_text:
            raise ConformanceFailure(
                "result validation failure requires exception_type, message, and traceback"
            )
        return

    # The frozen origin set and branches above must stay exhaustive.
    raise ConformanceFailure("attempt failure_origin has no semantic matrix")


def _validate_legacy_status_implications(row: Mapping[str, str]) -> None:
    """Kept separate so error messages remain explicit at chain boundaries."""

    status = row.get("solver_status")
    outer_timeout = _canonical_csv_bool(row, "outer_timeout")
    payload_received = _canonical_csv_bool(row, "payload_received")
    if status == "TIMEOUT" and not payload_received and not outer_timeout:
        raise ConformanceFailure(
            "TIMEOUT without a payload must be an outer timeout"
        )


def expected_attempt_id(analysis_id: str, attempt_number: int) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:ANALYSIS_ATTEMPT:v1",
        {"analysis_id": analysis_id, "attempt_number": attempt_number},
    )


def validate_attempt_chain(
    attempts: Sequence[Mapping[str, str]],
    *,
    expected_analysis_id: str,
    retry_policy: str,
    initial_timeout_seconds: float,
    retry_timeout_seconds: Optional[float],
    expected_final_status: Optional[str] = None,
    expected_attempt_count: Optional[int] = None,
    expected_final_attempt_id: Optional[str] = None,
) -> None:
    """Validate an immutable attempt journal against its frozen retry policy."""

    if not attempts:
        raise ConformanceFailure("terminal/state has no persisted attempt chain")
    max_attempts = 2 if retry_policy == "timeout_once" else 1
    if len(attempts) > max_attempts:
        raise ConformanceFailure("attempt count exceeds retry policy")
    if expected_attempt_count is not None and len(attempts) != expected_attempt_count:
        raise ConformanceFailure("attempt count does not match result")

    expected_parent = ""
    for number, row in enumerate(attempts, start=1):
        if row.get("analysis_id") != expected_analysis_id:
            raise ConformanceFailure("attempt belongs to another analysis")
        if row.get("attempt_number") != str(number):
            raise ConformanceFailure("attempt numbers are not contiguous")
        attempt_id = expected_attempt_id(expected_analysis_id, number)
        if row.get("attempt_id") != attempt_id:
            raise ConformanceFailure("attempt ID mismatch")
        if row.get("parent_attempt_id", "") != expected_parent:
            raise ConformanceFailure("attempt parent chain mismatch")
        expected_parent = attempt_id
        expected_budget = (
            initial_timeout_seconds if number == 1 else retry_timeout_seconds
        )
        if expected_budget is None:
            raise ConformanceFailure("retry attempt has no frozen timeout budget")
        try:
            observed_budget = float(row.get("timeout_budget_seconds", ""))
        except (TypeError, ValueError) as exc:
            raise ConformanceFailure("attempt timeout budget is invalid") from exc
        if not math.isfinite(observed_budget) or observed_budget != float(expected_budget):
            label = "initial" if number == 1 else "retry"
            raise ConformanceFailure(f"{label} timeout budget mismatch")
        _validate_attempt_payload_semantics(row)
        _validate_legacy_status_implications(row)
        if number > 1 and attempts[number - 2].get("solver_status") != "TIMEOUT":
            raise ConformanceFailure("retry attempt must follow a timeout")

    final = attempts[-1]
    if (
        expected_final_status is not None
        and final.get("solver_status") != expected_final_status
    ):
        raise ConformanceFailure("final attempt/state status mismatch")
    if (
        expected_final_attempt_id is not None
        and final.get("attempt_id") != expected_final_attempt_id
    ):
        raise ConformanceFailure("final attempt ID does not match result")


def validate_attempt_artifact_contract(
    attempts: Sequence[Mapping[str, str]],
    *,
    expected_analysis_id: str,
    expected_variant: taskset.AnalysisVariant,
    retry_policy: str,
    initial_timeout_seconds: float,
    retry_timeout_seconds: Optional[float],
    stored: StoredTaskset,
    expected_context: taskset.DependencyContext,
    expected_source_analysis_id: Optional[str] = None,
    source: Optional[taskset.TasksetAnalysisResult] = None,
    terminal_row: Optional[Mapping[str, object]] = None,
    state: Optional[taskset.TasksetAnalysisResult] = None,
) -> None:
    """Validate the single frozen attempt/state/terminal semantic matrix."""

    terminal_status: Optional[str] = None
    terminal_count: Optional[int] = None
    terminal_attempt_id: Optional[str] = None
    terminal_origin: Optional[str] = None
    terminal_failure_origin: Optional[str] = None
    terminal_outer_timeout: Optional[bool] = None
    if terminal_row is not None:
        terminal_status_value = terminal_row.get("solver_status")
        if not isinstance(terminal_status_value, str):
            raise ConformanceFailure("terminal solver_status is not text")
        terminal_status = terminal_status_value
        if terminal_status not in ALLOWED_ATTEMPT_SOLVER_STATUSES:
            raise ConformanceFailure("terminal solver_status is not frozen")
        try:
            terminal_count = taskset.require_nonnegative_plain_int(
                terminal_row.get("attempt_count"), "terminal attempt_count"
            )
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
        final_id_value = terminal_row.get("final_attempt_id")
        if not isinstance(final_id_value, str) or not final_id_value:
            raise ConformanceFailure("terminal final_attempt_id is invalid")
        terminal_attempt_id = final_id_value
        terminal_origin_value = terminal_row.get("terminal_origin")
        if terminal_origin_value not in ALLOWED_TERMINAL_ORIGINS:
            raise ConformanceFailure("terminal origin is not frozen")
        terminal_origin = str(terminal_origin_value)
        failure_origin_value = terminal_row.get("failure_origin")
        if failure_origin_value not in ATTEMPT_FAILURE_ORIGINS:
            raise ConformanceFailure(
                "terminal failure_origin is not frozen"
            )
        terminal_failure_origin = str(failure_origin_value)
        outer_value = terminal_row.get("outer_timeout")
        if outer_value not in {"True", "False"}:
            raise ConformanceFailure(
                "terminal outer_timeout must use canonical True/False text"
            )
        terminal_outer_timeout = outer_value == "True"

    expected_final_status = terminal_status
    if expected_final_status is None and state is not None:
        expected_final_status = state.solver_status.value
    validate_attempt_chain(
        attempts,
        expected_analysis_id=expected_analysis_id,
        retry_policy=retry_policy,
        initial_timeout_seconds=initial_timeout_seconds,
        retry_timeout_seconds=retry_timeout_seconds,
        expected_final_status=expected_final_status,
        expected_attempt_count=terminal_count,
        expected_final_attempt_id=terminal_attempt_id,
    )
    final = attempts[-1]
    final_status = final["solver_status"]
    final_outer_timeout = final["outer_timeout"] == "True"
    final_payload_received = final["payload_received"] == "True"
    final_failure_origin = final["failure_origin"]
    if (
        terminal_failure_origin is not None
        and terminal_failure_origin != final_failure_origin
    ):
        raise ConformanceFailure(
            "terminal/final attempt failure_origin mismatch"
        )
    if (
        terminal_outer_timeout is not None
        and terminal_outer_timeout != final_outer_timeout
    ):
        raise ConformanceFailure("terminal/attempt outer_timeout mismatch")

    if state is not None:
        if final_status == "INTERNAL_CONFORMANCE_FAILURE":
            raise ConformanceFailure(
                "worker internal failure must not carry analyzer state"
            )
        if not final_payload_received:
            raise ConformanceFailure(
                "analyzer state requires payload_received=True"
            )
        if final_outer_timeout:
            raise ConformanceFailure(
                "analyzer state is incompatible with outer_timeout=True"
            )
        if final_status != state.solver_status.value:
            raise ConformanceFailure("attempt/state solver_status mismatch")
        if final_failure_origin != "ANALYZER_RESULT":
            raise ConformanceFailure(
                "analyzer state requires ANALYZER_RESULT failure origin"
            )
        if terminal_origin is not None and terminal_origin != "PRODUCTION_ANALYZER":
            raise ConformanceFailure(
                "terminal origin with analyzer state must be PRODUCTION_ANALYZER"
            )
        validate_analysis_result(
            state,
            stored,
            expected_analysis_id=expected_analysis_id,
            expected_variant=expected_variant,
            expected_context=expected_context,
            expected_source_analysis_id=expected_source_analysis_id,
            source=source,
        )
        return

    if final_status not in {"TIMEOUT", "INTERNAL_CONFORMANCE_FAILURE"}:
        raise ConformanceFailure(
            f"solver_status {final_status} requires analyzer state"
        )
    if final_status == "TIMEOUT" and not final_outer_timeout:
        raise ConformanceFailure(
            "state-less TIMEOUT must be an outer timeout"
        )
    if final_status == "INTERNAL_CONFORMANCE_FAILURE" and final_outer_timeout:
        raise ConformanceFailure(
            "worker internal failure cannot be an outer timeout"
        )
    expected_origins = (
        OUTER_TIMEOUT_ORIGINS
        if final_status == "TIMEOUT"
        else INTERNAL_FAILURE_ORIGINS
    )
    if final_failure_origin not in expected_origins:
        raise ConformanceFailure(
            "state-less terminal failure origin does not match solver_status"
        )
    if terminal_origin is not None and terminal_origin != "OUTER_WORKER":
        raise ConformanceFailure(
            "terminal origin without analyzer state must be OUTER_WORKER"
        )


_TERMINAL_PLAIN_INTEGER_FIELDS = frozenset({
    "generation_seed",
    "M",
    "task_n",
    "n_tasks_total",
    "n_tasks_evaluated",
    "n_tasks_candidate_found",
    "n_tasks_certified",
    "dominance_violation_count",
    "attempt_count",
})
_TERMINAL_BOOLEAN_FIELDS = frozenset({
    "taskset_proven", "diagnostic_mode",
})


def _terminal_vector_hash(entries: Sequence[tuple[str, int]]) -> Optional[str]:
    frozen = tuple(sorted(entries))
    return (
        domain_hash("ASAP_BLOCK:V9.3:CARRY_IN_VECTOR:v1", frozen)
        if frozen else None
    )


def _validate_terminal_native_types(row: Mapping[str, Any]) -> None:
    for field in _TERMINAL_PLAIN_INTEGER_FIELDS:
        try:
            taskset.require_nonnegative_plain_int(row.get(field), field)
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
    first_failed = row.get("first_failed_priority")
    if first_failed is not None:
        try:
            taskset.require_nonnegative_plain_int(
                first_failed, "first_failed_priority"
            )
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
    for field in _TERMINAL_BOOLEAN_FIELDS:
        if not isinstance(row.get(field), bool):
            raise ConformanceFailure(f"{field} must be boolean")


def _materialized_value(field: str, value: Any) -> str:
    if field in _TERMINAL_PLAIN_INTEGER_FIELDS:
        try:
            parsed = taskset.require_nonnegative_plain_int(value, field)
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
        return str(parsed)
    if field == "first_failed_priority":
        if value is None:
            return ""
        try:
            parsed = taskset.require_nonnegative_plain_int(value, field)
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
        return str(parsed)
    if field in _TERMINAL_BOOLEAN_FIELDS:
        if not isinstance(value, bool):
            raise ConformanceFailure(f"{field} must be boolean")
        return "True" if value else "False"
    return "" if value is None else str(value)


def validate_terminal_result_contract(
    attempts: Sequence[Mapping[str, str]],
    *,
    expected_analysis_id: str,
    expected_variant: taskset.AnalysisVariant,
    retry_policy: str,
    initial_timeout_seconds: float,
    retry_timeout_seconds: Optional[float],
    stored: StoredTaskset,
    expected_context: taskset.DependencyContext,
    expected_identity: Mapping[str, Any],
    terminal_row: Mapping[str, Any],
    terminal_task_rows: Sequence[Mapping[str, Any]],
    expected_source_analysis_id: Optional[str] = None,
    source: Optional[taskset.TasksetAnalysisResult] = None,
    state: Optional[taskset.TasksetAnalysisResult] = None,
    materialized_row: Optional[Mapping[str, str]] = None,
    expected_task_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> None:
    """Validate the sole frozen terminal/result matrix at every consumer."""

    if set(terminal_row) != set(TASKSET_RESULT_COLUMNS):
        raise ConformanceFailure("terminal result schema mismatch")
    if not isinstance(terminal_task_rows, (list, tuple)):
        raise ConformanceFailure("terminal task rows are invalid")
    _validate_terminal_native_types(terminal_row)

    validate_attempt_artifact_contract(
        attempts,
        expected_analysis_id=expected_analysis_id,
        expected_variant=expected_variant,
        retry_policy=retry_policy,
        initial_timeout_seconds=initial_timeout_seconds,
        retry_timeout_seconds=retry_timeout_seconds,
        stored=stored,
        expected_context=expected_context,
        expected_source_analysis_id=expected_source_analysis_id,
        source=source,
        terminal_row=terminal_row,
        state=state,
    )

    for field, expected in expected_identity.items():
        if terminal_row.get(field) != expected:
            raise ConformanceFailure(f"terminal identity mismatch: {field}")

    final = attempts[-1]
    expected_attempt_fields = {
        "final_attempt_id": final["attempt_id"],
        "attempt_count": len(attempts),
        "timeout_budget_seconds": final["timeout_budget_seconds"],
        "failure_origin": final["failure_origin"],
        "outer_timeout": final["outer_timeout"],
        "runtime_wall_seconds": (
            f"{sum(float(item['total_wall_seconds']) for item in attempts):.9f}"
        ),
        "runtime_cpu_seconds": (
            f"{sum(float(item['solver_cpu_seconds']) for item in attempts):.9f}"
        ),
        "worker_startup_seconds": final["worker_startup_seconds"],
        "ipc_seconds": final["ipc_seconds"],
    }
    for field, expected in expected_attempt_fields.items():
        if terminal_row.get(field) != expected:
            raise ConformanceFailure(f"terminal/attempt mismatch: {field}")

    if state is None:
        expected_result = {
            "solver_status": final["solver_status"],
            "certification_status": "NOT_CERTIFIED",
            "taskset_proven": False,
            "first_failed_priority": None,
            "n_tasks_total": len(stored.tasks),
            "n_tasks_evaluated": 0,
            "n_tasks_candidate_found": 0,
            "n_tasks_certified": 0,
            "source_analysis_id": expected_source_analysis_id,
            "source_vector_hash": None,
            "target_carry_in_vector_hash": None,
            "dependency_check_status": (
                "INVALID"
                if expected_variant is taskset.AnalysisVariant.LOC_THETA_CW
                else "NOT_CHECKED"
            ),
            "fixed_carry_in_interface_status": (
                "NOT_APPLICABLE"
                if expected_variant in {
                    taskset.AnalysisVariant.CW_THETA_CW,
                    taskset.AnalysisVariant.LOC_THETA_LOC,
                }
                else "ACTIVE"
            ),
            "dominance_invariant_status": "NOT_CHECKED",
            "dominance_violation_count": 0,
            "diagnostic_mode": False,
            "terminal_origin": "OUTER_WORKER",
        }
        if terminal_task_rows:
            raise ConformanceFailure(
                "state-less terminal result matrix unexpectedly has task rows"
            )
        matrix_label = "state-less terminal result matrix"
    else:
        vector_hash = _terminal_vector_hash(state.source_candidate_vector)
        expected_result = {
            "solver_status": state.solver_status.value,
            "certification_status": state.certification_status.value,
            "taskset_proven": state.taskset_proven,
            "first_failed_priority": state.first_failed_priority,
            "n_tasks_total": state.n_tasks_total,
            "n_tasks_evaluated": state.n_tasks_evaluated,
            "n_tasks_candidate_found": state.n_tasks_candidate_found,
            "n_tasks_certified": state.n_tasks_certified,
            "source_analysis_id": state.source_analysis_id,
            "source_vector_hash": vector_hash,
            "target_carry_in_vector_hash": (
                vector_hash
                if expected_variant is taskset.AnalysisVariant.LOC_THETA_CW
                else None
            ),
            "dependency_check_status": state.dependency_check_status.value,
            "fixed_carry_in_interface_status": (
                state.fixed_carry_in_interface_status.value
            ),
            "dominance_invariant_status": state.dominance_invariant_status.value,
            "dominance_violation_count": (
                1 if state.dominance_counterexample else 0
            ),
            "diagnostic_mode": state.diagnostic_mode,
            "terminal_origin": "PRODUCTION_ANALYZER",
        }
        matrix_label = "terminal/state"
        if (
            expected_task_rows is not None
            and list(terminal_task_rows) != list(expected_task_rows)
        ):
            raise ConformanceFailure(
                "terminal task rows do not match analyzer state"
            )

    for field, expected in expected_result.items():
        if terminal_row.get(field) != expected:
            raise ConformanceFailure(f"{matrix_label} mismatch: {field}")

    if materialized_row is not None:
        if set(materialized_row) != set(TASKSET_RESULT_COLUMNS):
            raise ConformanceFailure("materialized result schema mismatch")
        for field in TASKSET_RESULT_COLUMNS:
            expected_text = _materialized_value(field, terminal_row[field])
            if materialized_row.get(field) != expected_text:
                raise ConformanceFailure(f"terminal/CSV mismatch: {field}")


def _validate_inactive_record(
    record: taskset.TaskAnalysisRecord,
    *,
    expected_status: taskset.TaskSolverStatus,
    expected_certification: taskset.TaskCertificationStatus,
) -> None:
    if (
        record.solver_status is not expected_status
        or record.certification_status is not expected_certification
        or record.candidate_response_time is not None
        or record.closing_w is not None
        or record.witness_h is not None
        or record.carry_in_values_used
        or any(
            value != 0
            for value in (
                record.checked_w_count,
                record.checked_h_count,
                record.checked_q_count,
                record.envelope_call_count,
            )
        )
    ):
        raise ConformanceFailure("inactive task record violates its status contract")


def _validate_status_matrix(
    result: taskset.TasksetAnalysisResult,
) -> None:
    records = result.task_records
    if not isinstance(result.diagnostic_mode, bool):
        raise ConformanceFailure("diagnostic_mode must be boolean")
    if result.solver_status is taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY:
        if (
            result.certification_status
            is not taskset.AnalysisCertificationStatus.NOT_APPLICABLE
            or result.first_failed_priority is not None
            or result.n_tasks_evaluated != 0
            or result.n_tasks_candidate_found != 0
            or result.n_tasks_certified != 0
            or result.taskset_proven
            or result.diagnostic_mode
            or result.dominance_invariant_status
            is not taskset.DominanceInvariantStatus.NOT_APPLICABLE
        ):
            raise ConformanceFailure("dependency N/A result matrix is inconsistent")
        for record in records:
            _validate_inactive_record(
                record,
                expected_status=taskset.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
                expected_certification=taskset.TaskCertificationStatus.NOT_APPLICABLE,
            )
        return

    if result.solver_status is taskset.AnalysisSolverStatus.COMPLETED:
        expected_analysis_certification = (
            taskset.AnalysisCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
            if result.diagnostic_mode
            else taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
        )
        expected_task_certification = (
            taskset.TaskCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
            if result.diagnostic_mode
            else taskset.TaskCertificationStatus.CERTIFIED
        )
        if (
            result.certification_status is not expected_analysis_certification
            or result.first_failed_priority is not None
            or any(
                record.solver_status is not taskset.TaskSolverStatus.CANDIDATE_FOUND
                or record.certification_status is not expected_task_certification
                for record in records
            )
        ):
            raise ConformanceFailure("completed result matrix is inconsistent")
        return

    failure_status = {
        taskset.AnalysisSolverStatus.NO_CANDIDATE:
            taskset.TaskSolverStatus.NO_CANDIDATE,
        taskset.AnalysisSolverStatus.TIMEOUT:
            taskset.TaskSolverStatus.TIMEOUT,
        taskset.AnalysisSolverStatus.NUMERIC_ERROR:
            taskset.TaskSolverStatus.NUMERIC_ERROR,
    }.get(result.solver_status)
    if failure_status is None and result.solver_status is not taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE:
        raise ConformanceFailure("unsupported task-set solver status")
    failed = result.first_failed_priority
    if isinstance(failed, bool) or not isinstance(failed, int) or not 0 <= failed < len(records):
        raise ConformanceFailure("first_failed_priority is inconsistent")
    expected_analysis_certification = (
        taskset.AnalysisCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
        if result.diagnostic_mode
        else taskset.AnalysisCertificationStatus.NOT_CERTIFIED
    )
    if (
        result.certification_status is not expected_analysis_certification
        or result.taskset_proven
    ):
        raise ConformanceFailure("failed result certification matrix is inconsistent")
    prefix_certification = (
        taskset.TaskCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
        if result.diagnostic_mode
        else taskset.TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
    )
    for rank, record in enumerate(records):
        if rank < failed:
            if (
                record.solver_status is not taskset.TaskSolverStatus.CANDIDATE_FOUND
                or record.certification_status is not prefix_certification
            ):
                raise ConformanceFailure("executed success prefix is inconsistent")
        elif rank == failed:
            allowed_failure = (
                {failure_status}
                if failure_status is not None
                else {
                    taskset.TaskSolverStatus.CANDIDATE_FOUND,
                    taskset.TaskSolverStatus.NO_CANDIDATE,
                    taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
                }
            )
            if (
                record.solver_status not in allowed_failure
                or record.certification_status
                is not taskset.TaskCertificationStatus.NOT_CERTIFIED
            ):
                raise ConformanceFailure("failed task record is inconsistent")
        else:
            _validate_inactive_record(
                record,
                expected_status=(
                    taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE
                ),
                expected_certification=taskset.TaskCertificationStatus.NOT_APPLICABLE,
            )


def _validate_experiment_status_fields(
    result: taskset.TasksetAnalysisResult,
    expected_variant: taskset.AnalysisVariant,
) -> None:
    """Bind non-numerical state fields to the production experiment contract."""

    if result.diagnostic_mode:
        raise ConformanceFailure("production experiment state used diagnostic mode")
    recursive = expected_variant in {
        taskset.AnalysisVariant.CW_THETA_CW,
        taskset.AnalysisVariant.LOC_THETA_LOC,
    }
    expected_interface = (
        taskset.FixedCarryInInterfaceStatus.NOT_APPLICABLE
        if recursive
        else taskset.FixedCarryInInterfaceStatus.ACTIVE
    )
    if result.fixed_carry_in_interface_status is not expected_interface:
        raise ConformanceFailure("fixed carry-in interface status mismatch")
    if result.dominance_counterexample is not None:
        raise ConformanceFailure("P0 dominance counterexample in analyzer state")
    if (
        result.solver_status
        is taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
    ):
        if expected_variant is not taskset.AnalysisVariant.LOC_THETA_CW:
            raise ConformanceFailure("non-dependency variant is unexpectedly N/A")
        expected_dominance = taskset.DominanceInvariantStatus.NOT_APPLICABLE
    elif result.solver_status is taskset.AnalysisSolverStatus.COMPLETED:
        expected_dominance = (
            taskset.DominanceInvariantStatus.SATISFIED
            if expected_variant is taskset.AnalysisVariant.LOC_THETA_CW
            else taskset.DominanceInvariantStatus.NOT_APPLICABLE
        )
    else:
        expected_dominance = taskset.DominanceInvariantStatus.NOT_CHECKED
    if result.dominance_invariant_status is not expected_dominance:
        raise ConformanceFailure("dominance status matrix mismatch")


def validate_analysis_result(
    result: taskset.TasksetAnalysisResult,
    stored: StoredTaskset,
    *,
    expected_analysis_id: str,
    expected_variant: taskset.AnalysisVariant,
    expected_context: taskset.DependencyContext,
    expected_source_analysis_id: Optional[str] = None,
    source: Optional[taskset.TasksetAnalysisResult] = None,
) -> None:
    try:
        taskset.validate_taskset_result_plain_integers(result)
    except taskset.CertificationError as exc:
        raise ConformanceFailure(str(exc)) from exc
    if result.analysis_id != expected_analysis_id:
        raise ConformanceFailure("analysis ID mismatch")
    if result.analysis_variant is not expected_variant:
        raise ConformanceFailure("analysis variant mismatch")
    if result.method_role is not taskset.ROLE_BY_VARIANT[expected_variant]:
        raise ConformanceFailure("analysis method role mismatch")
    if result.dependency_context != expected_context:
        raise ConformanceFailure("analysis dependency context mismatch")
    if result.taskset_proven != (
        result.certification_status
        is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
    ):
        raise ConformanceFailure("taskset_proven/certification mismatch")
    if result.n_tasks_total != len(stored.tasks):
        raise ConformanceFailure("task count mismatch")
    record_order = tuple(
        (record.task_id, record.priority_rank)
        for record in result.task_records
    )
    expected_order = tuple(
        (item.name, rank) for rank, item in enumerate(stored.tasks)
    )
    expected_ids = tuple(item.name for item in stored.tasks)
    if record_order != expected_order or len(expected_ids) != len(set(expected_ids)):
        raise ConformanceFailure("missing, reordered, or duplicate task rows")
    evaluated = sum(
        record.solver_status not in {
            taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
            taskset.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
        }
        for record in result.task_records
    )
    candidates = sum(
        record.solver_status is taskset.TaskSolverStatus.CANDIDATE_FOUND
        for record in result.task_records
    )
    certified = sum(
        record.certification_status is taskset.TaskCertificationStatus.CERTIFIED
        for record in result.task_records
    )
    if (
        result.n_tasks_evaluated != evaluated
        or result.n_tasks_candidate_found != candidates
        or result.n_tasks_certified != certified
    ):
        raise ConformanceFailure("task result counters are inconsistent")
    definitions = {item.name: item for item in stored.tasks}
    for record in result.task_records:
        definition = definitions[record.task_id]
        found = record.solver_status is taskset.TaskSolverStatus.CANDIDATE_FOUND
        if found != (record.candidate_response_time is not None):
            raise ConformanceFailure("task candidate presence/status mismatch")
        if found and (
            record.candidate_response_time is None
            or not definition.wcet
            <= record.candidate_response_time
            <= definition.deadline
            or record.closing_w != record.candidate_response_time
        ):
            raise ConformanceFailure("task candidate violates C <= R <= D")
        for value in (
            record.checked_w_count,
            record.checked_h_count,
            record.checked_q_count,
            record.envelope_call_count,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConformanceFailure("task solver counter is invalid")
        if record.certification_status is taskset.TaskCertificationStatus.CERTIFIED:
            if not found:
                raise ConformanceFailure("certified task is not CANDIDATE_FOUND")
            candidate = record.candidate_response_time
            if candidate is None or not definition.wcet <= candidate <= definition.deadline:
                raise ConformanceFailure("certified candidate violates C <= R <= D")
    _validate_status_matrix(result)
    _validate_experiment_status_fields(result, expected_variant)
    if result.certification_status is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET:
        if (
            result.solver_status is not taskset.AnalysisSolverStatus.COMPLETED
            or candidates != result.n_tasks_total
            or certified != result.n_tasks_total
            or result.diagnostic_mode
        ):
            raise ConformanceFailure("certified task-set closure is inconsistent")
    if expected_variant is not taskset.AnalysisVariant.LOC_THETA_CW:
        if expected_source_analysis_id is not None or source is not None:
            raise ConformanceFailure("non-dependency variant received a planned source")
        if result.source_analysis_id is not None:
            raise ConformanceFailure("non-dependency variant carried source_analysis_id")
        if result.source_candidate_vector:
            raise ConformanceFailure("non-dependency variant carried a source vector")
        if (
            result.dependency_check_status
            is not taskset.DependencyVectorCheckStatus.NOT_CHECKED
        ):
            raise ConformanceFailure("non-dependency variant was dependency-checked")
        if expected_variant in {
            taskset.AnalysisVariant.CW_THETA_CW,
            taskset.AnalysisVariant.LOC_THETA_LOC,
        } and (
            result.fixed_carry_in_interface_status
            is not taskset.FixedCarryInInterfaceStatus.NOT_APPLICABLE
        ):
            raise ConformanceFailure("recursive variant claimed fixed carry-in interface")
        if expected_variant in {
            taskset.AnalysisVariant.CW_D,
            taskset.AnalysisVariant.LOC_D,
        }:
            compatibility_vector: Optional[Mapping[str, int]] = {
                item.name: item.deadline for item in stored.tasks
            }
        else:
            compatibility_vector = None
        try:
            taskset.validate_carry_in_trace(
                variant=expected_variant,
                tasks=stored.tasks,
                records=result.task_records,
                compatibility_vector=compatibility_vector,
            )
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
        return

    if not expected_source_analysis_id:
        raise ConformanceFailure("LOC_THETA_CW requires its planned source ID")
    if source is None:
        if result.source_analysis_id != expected_source_analysis_id:
            raise ConformanceFailure("missing-source target did not retain planned source ID")
        if result.source_candidate_vector:
            raise ConformanceFailure("missing-source target carried a source vector")
        if result.dependency_check_status is not taskset.DependencyVectorCheckStatus.INVALID:
            raise ConformanceFailure("missing-source dependency is not INVALID")
        if result.solver_status is not taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY:
            raise ConformanceFailure("missing-source target is not dependency N/A")
        if result.certification_status is not taskset.AnalysisCertificationStatus.NOT_APPLICABLE:
            raise ConformanceFailure("missing-source target certification is not N/A")
        try:
            taskset.validate_carry_in_trace(
                variant=expected_variant,
                tasks=stored.tasks,
                records=result.task_records,
                compatibility_vector=None,
            )
        except taskset.CertificationError as exc:
            raise ConformanceFailure(str(exc)) from exc
        return
    if source.analysis_id != expected_source_analysis_id:
        raise ConformanceFailure("source analysis ID does not match the plan")
    if result.source_analysis_id != expected_source_analysis_id:
        raise ConformanceFailure("target source analysis ID does not match the plan")
    if source.analysis_variant is not taskset.AnalysisVariant.CW_THETA_CW:
        raise ConformanceFailure("LOC_THETA_CW source variant is not CW_THETA_CW")
    validate_analysis_result(
        source,
        stored,
        expected_analysis_id=expected_source_analysis_id,
        expected_variant=taskset.AnalysisVariant.CW_THETA_CW,
        expected_context=expected_context,
    )
    if source.dependency_context != expected_context:
        raise ConformanceFailure("source dependency context mismatch")
    if source.dependency_context.taskset_identity != stored.semantic_hash:
        raise ConformanceFailure("source taskset identity mismatch")
    source_ids = tuple(record.task_id for record in source.task_records)
    if source_ids != expected_ids or len(source_ids) != len(set(source_ids)):
        raise ConformanceFailure("source task rows do not match the taskset")
    source_certified = bool(
        source.taskset_proven
        and source.certification_status
        is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
        and source.solver_status is taskset.AnalysisSolverStatus.COMPLETED
        and source.n_tasks_certified == source.n_tasks_total == len(stored.tasks)
        and all(
            record.solver_status is taskset.TaskSolverStatus.CANDIDATE_FOUND
            and record.certification_status is taskset.TaskCertificationStatus.CERTIFIED
            and record.candidate_response_time is not None
            for record in source.task_records
        )
    )
    source_vector = (
        tuple(sorted(
            (record.task_id, record.candidate_response_time)
            for record in source.task_records
        ))
        if source_certified else ()
    )
    if result.source_candidate_vector != source_vector:
        raise ConformanceFailure(
            "dependency source/target carry-in vector mismatch"
        )
    if source_certified:
        if result.dependency_check_status is not taskset.DependencyVectorCheckStatus.VALID:
            raise ConformanceFailure("certified source dependency is not VALID")
        if (
            result.fixed_carry_in_interface_status
            is not taskset.FixedCarryInInterfaceStatus.ACTIVE
        ):
            raise ConformanceFailure("valid dependency has inactive fixed carry-in interface")
    else:
        if result.dependency_check_status is not taskset.DependencyVectorCheckStatus.INVALID:
            raise ConformanceFailure("uncertified source dependency is not INVALID")
        if result.solver_status is not taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY:
            raise ConformanceFailure("uncertified source did not produce dependency N/A")
        if result.certification_status is not taskset.AnalysisCertificationStatus.NOT_APPLICABLE:
            raise ConformanceFailure("dependency failure has non-N/A certification")
        if result.diagnostic_mode:
            raise ConformanceFailure("dependency failure used a diagnostic fallback")
    try:
        taskset.validate_carry_in_trace(
            variant=expected_variant,
            tasks=stored.tasks,
            records=result.task_records,
            compatibility_vector=(dict(source_vector) if source_certified else None),
        )
    except taskset.CertificationError as exc:
        raise ConformanceFailure(str(exc)) from exc


def assert_unique(rows: list[Mapping[str, object]], *keys: str) -> None:
    seen = set()
    for row in rows:
        identity = tuple(row[key] for key in keys)
        if identity in seen:
            raise ConformanceFailure(f"duplicate row {identity}")
        seen.add(identity)
