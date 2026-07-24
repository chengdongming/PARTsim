"""Task-set orchestration and joint certification for the v9.3 RTA core.

This module deliberately separates a single-task solver candidate from a
theorem-backed task-set certificate.  It does not integrate with an experiment
runner and it never writes result CSV files.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Mapping, Optional, Protocol, Sequence, Tuple

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_methods as methods
from experiments.v9_3 import exact_energy


THEORY_DOCUMENT_PATH = exact_energy.THEORY_DOCUMENT_PATH
THEORY_DOCUMENT_SHA256 = exact_energy.THEORY_DOCUMENT_SHA256
FIXED_CARRY_IN_INTERFACE_SHA256 = THEORY_DOCUMENT_SHA256
LEGACY_THEORY_DOCUMENT_PATH = (
    "asap_block_rta_multicore_complete_and_local_paper_ready_v9_3_"
    "fixed_carry_in_interface(1).md"
)
LEGACY_THEORY_DOCUMENT_SHA256 = (
    "524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e"
)


class CertificationError(ValueError):
    """Raised when an object would violate the joint-certification contract."""


def require_plain_int(value: object, field: str) -> int:
    """Return a schema integer, rejecting bool and non-integral subclasses."""

    if type(value) is not int:
        raise CertificationError(f"{field} must be a plain integer")
    return value


def require_optional_plain_int(value: object, field: str) -> Optional[int]:
    if value is None:
        return None
    return require_plain_int(value, field)


def require_nonnegative_plain_int(value: object, field: str) -> int:
    integer = require_plain_int(value, field)
    if integer < 0:
        raise CertificationError(f"{field} must be a nonnegative plain integer")
    return integer


class AnalysisVariant(str, Enum):
    CW_D = "CW-D"
    LOC_D = "LOC-D"
    CW_THETA_CW = "CW-Theta^cw"
    LOC_THETA_CW = "LOC-Theta^cw"
    LOC_THETA_LOC = "LOC-Theta^loc"
    PH_THETA_PH = "PH-Theta^ph"
    SEQ_THETA_SEQ = "SEQ-Theta^seq"


class AnalysisMethodRole(str, Enum):
    MAIN_METHOD = "MAIN_METHOD"
    AUXILIARY_ABLATION = "AUXILIARY_ABLATION"
    DIAGNOSTIC = "DIAGNOSTIC"


class TaskSolverStatus(str, Enum):
    CANDIDATE_FOUND = "CANDIDATE_FOUND"
    NO_CANDIDATE = "NO_CANDIDATE"
    TIMEOUT = "TIMEOUT"
    NUMERIC_ERROR = "NUMERIC_ERROR"
    NOT_EVALUATED_AFTER_PREFIX_FAILURE = "NOT_EVALUATED_AFTER_PREFIX_FAILURE"
    NOT_APPLICABLE_DEPENDENCY = "NOT_APPLICABLE_DEPENDENCY"
    INTERNAL_CONFORMANCE_FAILURE = "INTERNAL_CONFORMANCE_FAILURE"


class TaskCertificationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    PROVISIONAL_NOT_CERTIFIED = "PROVISIONAL_NOT_CERTIFIED"
    DIAGNOSTIC_ONLY_NOT_CERTIFIED = "DIAGNOSTIC_ONLY_NOT_CERTIFIED"
    NOT_CERTIFIED = "NOT_CERTIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AnalysisSolverStatus(str, Enum):
    COMPLETED = "COMPLETED"
    NO_CANDIDATE = "NO_CANDIDATE"
    TIMEOUT = "TIMEOUT"
    NUMERIC_ERROR = "NUMERIC_ERROR"
    NOT_APPLICABLE_DEPENDENCY = "NOT_APPLICABLE_DEPENDENCY"
    INTERNAL_CONFORMANCE_FAILURE = "INTERNAL_CONFORMANCE_FAILURE"
    UNSUPPORTED_EXPERIMENT_VARIANT = "UNSUPPORTED_EXPERIMENT_VARIANT"


class AnalysisCertificationStatus(str, Enum):
    CERTIFIED_TASKSET = "CERTIFIED_TASKSET"
    DIAGNOSTIC_ONLY_NOT_CERTIFIED = "DIAGNOSTIC_ONLY_NOT_CERTIFIED"
    NOT_CERTIFIED = "NOT_CERTIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DependencyVectorCheckStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    NOT_CHECKED = "NOT_CHECKED"


class DominanceInvariantStatus(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    SATISFIED = "SATISFIED"
    DOMINANCE_INVARIANT_VIOLATION = "DOMINANCE_INVARIANT_VIOLATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FixedCarryInInterfaceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HASH_MISMATCH = "HASH_MISMATCH"
    NOT_APPLICABLE = "NOT_APPLICABLE"


ROLE_BY_VARIANT = {
    AnalysisVariant.CW_D: AnalysisMethodRole.AUXILIARY_ABLATION,
    AnalysisVariant.LOC_D: AnalysisMethodRole.AUXILIARY_ABLATION,
    AnalysisVariant.CW_THETA_CW: AnalysisMethodRole.MAIN_METHOD,
    AnalysisVariant.LOC_THETA_CW: AnalysisMethodRole.AUXILIARY_ABLATION,
    AnalysisVariant.LOC_THETA_LOC: AnalysisMethodRole.MAIN_METHOD,
    # PH is exposed only through the directed mathematical API in this round;
    # it is deliberately not a formal CORE-1 experiment method.
    AnalysisVariant.PH_THETA_PH: AnalysisMethodRole.DIAGNOSTIC,
    # SEQ likewise remains a directed mathematical/task-set diagnostic API.
    AnalysisVariant.SEQ_THETA_SEQ: AnalysisMethodRole.DIAGNOSTIC,
}
MAIN_METHOD_VARIANTS = frozenset(
    variant
    for variant, role in ROLE_BY_VARIANT.items()
    if role is AnalysisMethodRole.MAIN_METHOD
)


@dataclass(frozen=True)
class DependencyContext:
    taskset_identity: str
    task_definitions_identity: str
    priority_order_identity: str
    e0_canonical_identity: str
    service_curve_identity: str
    power_vector_identity: str
    numerical_mode: str
    numerical_scale: Optional[str]
    theory_document_sha256: str
    fixed_carry_in_interface_sha256: str
    formal_contract_identity: Optional[str] = None
    numeric_contract_sha256: str = ""
    source_numeric_model: str = ""
    demand_rounding_mode: str = ""
    supply_rounding_mode: str = ""
    e0_rounding_mode: str = ""
    exact_input_identity: str = ""
    float_decision_path: bool = True


@dataclass(frozen=True)
class TasksetAnalysisInput:
    tasks: Tuple[core.V93Task, ...]
    processors: int
    e0: core.ExactInput
    beta: core.ServiceCurve
    dependency_context: DependencyContext
    timeout_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.tasks:
            raise CertificationError("task set must be nonempty")
        names = tuple(task.name for task in self.tasks)
        if len(names) != len(set(names)):
            raise CertificationError("task IDs must be unique")
        if (
            isinstance(self.processors, bool)
            or not isinstance(self.processors, int)
            or self.processors < 1
        ):
            raise CertificationError("processors must be a positive integer")


@dataclass(frozen=True)
class SingleTaskSolverResult:
    solver_status: TaskSolverStatus
    candidate_response_time: Optional[int] = None
    closing_w: Optional[int] = None
    witness_h: Optional[int] = None
    checked_w_count: int = 0
    checked_h_count: int = 0
    checked_q_count: int = 0
    envelope_call_count: int = 0
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        require_optional_plain_int(
            self.candidate_response_time, "candidate_response_time"
        )
        require_optional_plain_int(self.closing_w, "closing_w")
        require_optional_plain_int(self.witness_h, "witness_h")
        for field in (
            "checked_w_count",
            "checked_h_count",
            "checked_q_count",
            "envelope_call_count",
        ):
            require_nonnegative_plain_int(getattr(self, field), field)
        found = self.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        if found != (self.candidate_response_time is not None):
            raise CertificationError("candidate presence must match solver status")
        if found and self.closing_w != self.candidate_response_time:
            raise CertificationError("closing_w must equal the returned candidate")


_FINALIZER_TOKEN = object()
_METHOD_FINALIZER_TOKEN = object()


def _require_optional_nonnegative_plain_int(
    value: object, field_name: str
) -> Optional[int]:
    value = require_optional_plain_int(value, field_name)
    if value is not None and value < 0:
        raise CertificationError(
            "{} must be nonnegative when available".format(field_name)
        )
    return value


def _validate_optional_counter_fields(
    value: object, field_name: str
) -> None:
    _require_optional_nonnegative_plain_int(value, field_name)


@dataclass(frozen=True)
class V93KernelTaskResult:
    """Lossless internal result returned by one existing mathematical kernel."""

    solver_status: TaskSolverStatus
    kernel_solver_status: str
    candidate_response_time: Optional[int]
    closing_w: Optional[int]
    witness_h: Optional[int]
    processor_progress_a: Optional[int]
    maximum_blocking_h: Optional[int]
    witness_sequence: Tuple[int, ...]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    impossible_prefix_count: Optional[int]
    phase_safe_calls: Optional[int]
    flow_solver_calls: Optional[int]
    flow_feasible_count: Optional[int]
    flow_infeasible_count: Optional[int]
    z_branch_count: Optional[int]
    flow_node_count: Optional[int]
    flow_edge_count: Optional[int]
    flow_feasibility_augmentations: Optional[int]
    flow_optimality_cycle_cancellations: Optional[int]
    flow_optimality_units_augmented: Optional[int]
    failure_reason: Optional[str]
    unavailable_metrics: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.solver_status, TaskSolverStatus):
            raise CertificationError(
                "kernel result solver_status must be a TaskSolverStatus"
            )
        if (
            not isinstance(self.kernel_solver_status, str)
            or not self.kernel_solver_status
        ):
            raise CertificationError(
                "kernel_solver_status must be a non-empty string"
            )
        for field_name in (
            "candidate_response_time",
            "closing_w",
            "witness_h",
            "processor_progress_a",
            "maximum_blocking_h",
        ):
            _require_optional_nonnegative_plain_int(
                getattr(self, field_name), field_name
            )
        for field_name in (
            "checked_w_count",
            "checked_h_count",
            "checked_q_count",
            "envelope_call_count",
        ):
            require_nonnegative_plain_int(
                getattr(self, field_name), field_name
            )
        for field_name in (
            "impossible_prefix_count",
            "phase_safe_calls",
            "flow_solver_calls",
            "flow_feasible_count",
            "flow_infeasible_count",
            "z_branch_count",
            "flow_node_count",
            "flow_edge_count",
            "flow_feasibility_augmentations",
            "flow_optimality_cycle_cancellations",
            "flow_optimality_units_augmented",
        ):
            _validate_optional_counter_fields(
                getattr(self, field_name), field_name
            )
        try:
            sequence = tuple(self.witness_sequence)
        except TypeError as exc:
            raise CertificationError(
                "witness_sequence must be a tuple of integers"
            ) from exc
        if sequence != self.witness_sequence:
            raise CertificationError("witness_sequence must be a tuple")
        for index, value in enumerate(sequence):
            _require_optional_nonnegative_plain_int(
                value, "witness_sequence[{}]".format(index)
            )
        if not isinstance(self.unavailable_metrics, tuple):
            raise CertificationError("unavailable_metrics must be a tuple")
        if len(self.unavailable_metrics) != len(
            set(self.unavailable_metrics)
        ) or any(
            not isinstance(item, str) or not item
            for item in self.unavailable_metrics
        ):
            raise CertificationError(
                "unavailable_metrics must contain unique non-empty names"
            )

        found = self.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        if found != (self.candidate_response_time is not None):
            raise CertificationError(
                "kernel candidate presence must match solver status"
            )
        if found:
            if self.closing_w != self.candidate_response_time:
                raise CertificationError(
                    "kernel closing_w must equal its candidate"
                )
            if self.failure_reason is not None:
                raise CertificationError(
                    "kernel candidate may not carry a failure reason"
                )
        elif (
            self.closing_w is not None
            or self.witness_h is not None
            or self.processor_progress_a is not None
            or self.maximum_blocking_h is not None
            or self.witness_sequence
        ):
            raise CertificationError(
                "non-candidate kernel result may not carry a certificate"
            )


@dataclass(frozen=True)
class V93CarryTraceEntry:
    task_id: str
    priority_rank: int
    theta_by_task: Tuple[Tuple[str, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise CertificationError("carry trace task_id must be non-empty")
        require_nonnegative_plain_int(self.priority_rank, "priority_rank")
        if not isinstance(self.theta_by_task, tuple):
            raise CertificationError("theta_by_task must be a tuple")
        names = []
        for task_id, value in self.theta_by_task:
            if not isinstance(task_id, str) or not task_id:
                raise CertificationError(
                    "carry trace task names must be non-empty"
                )
            require_plain_int(value, "carry trace theta")
            names.append(task_id)
        if len(names) != len(set(names)):
            raise CertificationError(
                "carry trace may not contain duplicate task IDs"
            )


@dataclass(frozen=True)
class V93MethodTaskResult:
    """Task result used by the eight-method adapter, not by disk schemas."""

    method_id: methods.V93MethodId
    kernel: methods.V93Kernel
    carry_policy: methods.V93CarryPolicy
    task_id: str
    priority_rank: int
    solver_status: TaskSolverStatus
    kernel_solver_status: str
    certification_status: TaskCertificationStatus
    candidate_response_time: Optional[int]
    carry_in_values_used: Tuple[Tuple[str, int], ...]
    closing_w: Optional[int]
    witness_h: Optional[int]
    processor_progress_a: Optional[int]
    maximum_blocking_h: Optional[int]
    witness_sequence: Tuple[int, ...]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    solver_call_count: int
    impossible_prefix_count: Optional[int]
    phase_safe_calls: Optional[int]
    flow_solver_calls: Optional[int]
    flow_feasible_count: Optional[int]
    flow_infeasible_count: Optional[int]
    z_branch_count: Optional[int]
    flow_node_count: Optional[int]
    flow_edge_count: Optional[int]
    flow_feasibility_augmentations: Optional[int]
    flow_optimality_cycle_cancellations: Optional[int]
    flow_optimality_units_augmented: Optional[int]
    runtime_wall: float
    runtime_cpu: Optional[float]
    failure_reason: Optional[str]
    unavailable_metrics: Tuple[str, ...]
    _certification_token: object = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        spec = methods.method_spec_v9_3(self.method_id)
        if self.kernel is not spec.kernel:
            raise CertificationError("task result kernel/method mismatch")
        if self.carry_policy is not spec.carry_policy:
            raise CertificationError(
                "task result carry policy/method mismatch"
            )
        if not isinstance(self.task_id, str) or not self.task_id:
            raise CertificationError("task_id must be non-empty")
        require_nonnegative_plain_int(self.priority_rank, "priority_rank")
        if not isinstance(self.certification_status, TaskCertificationStatus):
            raise CertificationError(
                "certification_status must be a TaskCertificationStatus"
            )
        if type(self.solver_call_count) is not int:
            raise CertificationError("solver_call_count must be a plain integer")
        if self.solver_call_count not in {0, 1}:
            raise CertificationError("solver_call_count must be zero or one")
        if (
            isinstance(self.runtime_wall, bool)
            or not isinstance(self.runtime_wall, (int, float))
            or not math.isfinite(self.runtime_wall)
            or self.runtime_wall < 0
        ):
            raise CertificationError(
                "runtime_wall must be finite and nonnegative"
            )
        if self.runtime_cpu is not None and (
            isinstance(self.runtime_cpu, bool)
            or not isinstance(self.runtime_cpu, (int, float))
            or not math.isfinite(self.runtime_cpu)
            or self.runtime_cpu < 0
        ):
            raise CertificationError(
                "runtime_cpu must be finite and nonnegative when available"
            )
        kernel_result = V93KernelTaskResult(
            solver_status=self.solver_status,
            kernel_solver_status=self.kernel_solver_status,
            candidate_response_time=self.candidate_response_time,
            closing_w=self.closing_w,
            witness_h=self.witness_h,
            processor_progress_a=self.processor_progress_a,
            maximum_blocking_h=self.maximum_blocking_h,
            witness_sequence=self.witness_sequence,
            checked_w_count=self.checked_w_count,
            checked_h_count=self.checked_h_count,
            checked_q_count=self.checked_q_count,
            envelope_call_count=self.envelope_call_count,
            impossible_prefix_count=self.impossible_prefix_count,
            phase_safe_calls=self.phase_safe_calls,
            flow_solver_calls=self.flow_solver_calls,
            flow_feasible_count=self.flow_feasible_count,
            flow_infeasible_count=self.flow_infeasible_count,
            z_branch_count=self.z_branch_count,
            flow_node_count=self.flow_node_count,
            flow_edge_count=self.flow_edge_count,
            flow_feasibility_augmentations=(
                self.flow_feasibility_augmentations
            ),
            flow_optimality_cycle_cancellations=(
                self.flow_optimality_cycle_cancellations
            ),
            flow_optimality_units_augmented=(
                self.flow_optimality_units_augmented
            ),
            failure_reason=self.failure_reason,
            unavailable_metrics=self.unavailable_metrics,
        )
        del kernel_result
        V93CarryTraceEntry(
            self.task_id,
            self.priority_rank,
            self.carry_in_values_used,
        )
        found = self.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        certified = (
            self.certification_status is TaskCertificationStatus.CERTIFIED
        )
        if certified and (
            self._certification_token is not _METHOD_FINALIZER_TOKEN
            or not found
        ):
            raise CertificationError(
                "unified CERTIFIED task requires atomic finalization"
            )
        if not found and self.certification_status in {
            TaskCertificationStatus.CERTIFIED,
            TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED,
        }:
            raise CertificationError(
                "non-candidate task cannot be certified or provisional"
            )


@dataclass(frozen=True)
class V93MethodTasksetAnalysisResult:
    """In-memory result for one canonical method registry entry."""

    analysis_id: str
    method_id: methods.V93MethodId
    kernel: methods.V93Kernel
    carry_policy: methods.V93CarryPolicy
    solver_status: AnalysisSolverStatus
    analysis_certification_status: AnalysisCertificationStatus
    task_results: Tuple[V93MethodTaskResult, ...]
    taskset_proven: bool
    first_failed_task: Optional[str]
    failure_reason: Optional[str]
    carry_trace: Tuple[V93CarryTraceEntry, ...]
    exact_input_identity: str
    _finalizer_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._finalizer_token is not _METHOD_FINALIZER_TOKEN:
            raise CertificationError(
                "unified task-set results require the internal finalizer"
            )
        spec = methods.method_spec_v9_3(self.method_id)
        if self.kernel is not spec.kernel:
            raise CertificationError("task-set kernel/method mismatch")
        if self.carry_policy is not spec.carry_policy:
            raise CertificationError("task-set carry policy/method mismatch")
        if not isinstance(self.solver_status, AnalysisSolverStatus):
            raise CertificationError(
                "solver_status must be an AnalysisSolverStatus"
            )
        if not isinstance(
            self.analysis_certification_status,
            AnalysisCertificationStatus,
        ):
            raise CertificationError(
                "analysis_certification_status must be an "
                "AnalysisCertificationStatus"
            )
        if not isinstance(self.analysis_id, str) or not self.analysis_id:
            raise CertificationError("analysis_id must be non-empty")
        if not isinstance(self.task_results, tuple) or not self.task_results:
            raise CertificationError("task_results must be a non-empty tuple")
        if not isinstance(self.carry_trace, tuple):
            raise CertificationError("carry_trace must be a tuple")
        if len(self.carry_trace) != len(self.task_results):
            raise CertificationError(
                "carry_trace must contain one entry per task"
            )
        expected_ranks = tuple(range(len(self.task_results)))
        if tuple(
            result.priority_rank for result in self.task_results
        ) != expected_ranks:
            raise CertificationError(
                "unified priority ranks must be contiguous"
            )
        if tuple(
            (entry.task_id, entry.priority_rank, entry.theta_by_task)
            for entry in self.carry_trace
        ) != tuple(
            (
                result.task_id,
                result.priority_rank,
                result.carry_in_values_used,
            )
            for result in self.task_results
        ):
            raise CertificationError(
                "carry_trace does not match task result carry values"
            )
        if any(
            result.method_id is not self.method_id
            or result.kernel is not self.kernel
            or result.carry_policy is not self.carry_policy
            for result in self.task_results
        ):
            raise CertificationError(
                "task results do not match task-set method"
            )
        certified = (
            self.analysis_certification_status
            is AnalysisCertificationStatus.CERTIFIED_TASKSET
        )
        if self.taskset_proven is not certified:
            raise CertificationError(
                "taskset_proven must match analysis certification"
            )
        certified_tasks = tuple(
            result.certification_status
            is TaskCertificationStatus.CERTIFIED
            for result in self.task_results
        )
        if certified:
            if (
                self.solver_status is not AnalysisSolverStatus.COMPLETED
                or not all(certified_tasks)
                or not all(
                    result.solver_status
                    is TaskSolverStatus.CANDIDATE_FOUND
                    for result in self.task_results
                )
                or self.first_failed_task is not None
                or self.failure_reason is not None
            ):
                raise CertificationError(
                    "certified unified task set is internally inconsistent"
                )
        elif any(certified_tasks):
            raise CertificationError(
                "failed task set may not carry partial certification"
            )
        if self.first_failed_task is not None and self.first_failed_task not in {
            result.task_id for result in self.task_results
        }:
            raise CertificationError("first_failed_task is not in task set")
        if not isinstance(self.exact_input_identity, str):
            raise CertificationError("exact_input_identity must be preserved")


@dataclass(frozen=True)
class TaskAnalysisRecord:
    task_id: str
    priority_rank: int
    solver_status: TaskSolverStatus
    certification_status: TaskCertificationStatus
    candidate_response_time: Optional[int]
    carry_in_values_used: Tuple[Tuple[str, int], ...]
    closing_w: Optional[int]
    witness_h: Optional[int]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    failure_reason: Optional[str] = None
    _certification_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_task_record_plain_integers(self)
        found = self.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        if found != (self.candidate_response_time is not None):
            raise CertificationError("task candidate presence/status mismatch")
        if self.certification_status is TaskCertificationStatus.CERTIFIED:
            if self._certification_token is not _FINALIZER_TOKEN:
                raise CertificationError("CERTIFIED may only be produced by finalizer")
            if not found:
                raise CertificationError("certified task must have a candidate")


def validate_task_record_plain_integers(record: TaskAnalysisRecord) -> None:
    require_nonnegative_plain_int(record.priority_rank, "priority_rank")
    require_optional_plain_int(
        record.candidate_response_time, "candidate_response_time"
    )
    require_optional_plain_int(record.closing_w, "closing_w")
    require_optional_plain_int(record.witness_h, "witness_h")
    for field in (
        "checked_w_count",
        "checked_h_count",
        "checked_q_count",
        "envelope_call_count",
    ):
        require_nonnegative_plain_int(getattr(record, field), field)
    try:
        carry_entries = tuple(record.carry_in_values_used)
        for _task_id, value in carry_entries:
            require_plain_int(value, "carry_in_values_used value")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CertificationError):
            raise
        raise CertificationError(
            "carry_in_values_used must contain task/integer pairs"
        ) from exc
    found = record.solver_status is TaskSolverStatus.CANDIDATE_FOUND
    if found != (record.candidate_response_time is not None):
        raise CertificationError("task candidate presence/status mismatch")
    if found and record.closing_w != record.candidate_response_time:
        raise CertificationError(
            "closing_w must equal candidate_response_time"
        )


@dataclass(frozen=True)
class DominanceCounterexample:
    task_id: str
    priority_rank: int
    source_candidate: Optional[int]
    local_candidate: Optional[int]
    carry_in_vector: Tuple[Tuple[str, int], ...]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int

    def __post_init__(self) -> None:
        validate_dominance_counterexample_plain_integers(self)


def validate_dominance_counterexample_plain_integers(
    counterexample: DominanceCounterexample,
) -> None:
    require_nonnegative_plain_int(
        counterexample.priority_rank, "priority_rank"
    )
    require_optional_plain_int(
        counterexample.source_candidate, "source_candidate"
    )
    require_optional_plain_int(
        counterexample.local_candidate, "local_candidate"
    )
    for field in (
        "checked_w_count",
        "checked_h_count",
        "checked_q_count",
        "envelope_call_count",
    ):
        require_nonnegative_plain_int(getattr(counterexample, field), field)
    try:
        for _task_id, value in counterexample.carry_in_vector:
            require_plain_int(value, "dominance carry_in_vector value")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CertificationError):
            raise
        raise CertificationError(
            "dominance carry_in_vector must contain task/integer pairs"
        ) from exc


@dataclass(frozen=True)
class TasksetAnalysisResult:
    analysis_id: str
    analysis_variant: AnalysisVariant
    method_role: AnalysisMethodRole
    solver_status: AnalysisSolverStatus
    certification_status: AnalysisCertificationStatus
    task_records: Tuple[TaskAnalysisRecord, ...]
    first_failed_priority: Optional[int]
    n_tasks_total: int
    n_tasks_evaluated: int
    n_tasks_candidate_found: int
    n_tasks_certified: int
    taskset_proven: bool
    source_analysis_id: Optional[str]
    source_candidate_vector: Tuple[Tuple[str, int], ...]
    dependency_check_status: DependencyVectorCheckStatus
    fixed_carry_in_interface_status: FixedCarryInInterfaceStatus
    dominance_invariant_status: DominanceInvariantStatus
    diagnostic_mode: bool
    dependency_context: DependencyContext
    dominance_counterexample: Optional[DominanceCounterexample]
    _finalizer_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_taskset_result_plain_integers(self)
        if self._finalizer_token is not _FINALIZER_TOKEN:
            raise CertificationError("task-set results may only be produced by finalizer")
        if self.method_role is not ROLE_BY_VARIANT[self.analysis_variant]:
            raise CertificationError("analysis method role does not match variant")
        if self.taskset_proven != (
            self.certification_status
            is AnalysisCertificationStatus.CERTIFIED_TASKSET
        ):
            raise CertificationError("taskset_proven must be derived from certification")
        if self.n_tasks_total != len(self.task_records):
            raise CertificationError("task count does not match records")
        if self.n_tasks_candidate_found != sum(
            record.solver_status is TaskSolverStatus.CANDIDATE_FOUND
            for record in self.task_records
        ):
            raise CertificationError("candidate count mismatch")
        if self.n_tasks_certified != sum(
            record.certification_status is TaskCertificationStatus.CERTIFIED
            for record in self.task_records
        ):
            raise CertificationError("certified count mismatch")
        if self.analysis_variant is AnalysisVariant.LOC_THETA_CW:
            if not self.source_analysis_id:
                raise CertificationError(
                    "LOC-Theta^cw requires source_analysis_id"
                )
            if self.dependency_check_status not in {
                DependencyVectorCheckStatus.VALID,
                DependencyVectorCheckStatus.INVALID,
            }:
                raise CertificationError(
                    "LOC-Theta^cw dependency status must be VALID or INVALID"
                )
            source_ids = tuple(item[0] for item in self.source_candidate_vector)
            if len(source_ids) != len(set(source_ids)):
                raise CertificationError("source candidate vector contains duplicate task IDs")
            if self.dependency_check_status is DependencyVectorCheckStatus.VALID:
                expected_ids = tuple(record.task_id for record in self.task_records)
                if source_ids != tuple(sorted(expected_ids)):
                    raise CertificationError(
                        "VALID LOC-Theta^cw source vector must cover the task set"
                    )
                if self.fixed_carry_in_interface_status is not FixedCarryInInterfaceStatus.ACTIVE:
                    raise CertificationError(
                        "VALID LOC-Theta^cw requires ACTIVE fixed carry-in interface"
                    )
            elif (
                not self.diagnostic_mode
                and self.solver_status
                is not AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY
            ):
                raise CertificationError(
                    "INVALID LOC-Theta^cw must be dependency N/A"
                )
        else:
            if self.source_analysis_id is not None:
                raise CertificationError(
                    "non-LOC-Theta^cw result may not carry source_analysis_id"
                )
            if self.source_candidate_vector:
                raise CertificationError(
                    "non-LOC-Theta^cw result may not carry an external source vector"
                )
            if self.dependency_check_status is not DependencyVectorCheckStatus.NOT_CHECKED:
                raise CertificationError(
                    "non-LOC-Theta^cw dependency status must be NOT_CHECKED"
                )
        if self.certification_status is AnalysisCertificationStatus.CERTIFIED_TASKSET:
            if self.solver_status is not AnalysisSolverStatus.COMPLETED:
                raise CertificationError("certified task set must be completed")
            if self.n_tasks_candidate_found != self.n_tasks_total:
                raise CertificationError("certified task set must have every candidate")
            if self.n_tasks_certified != self.n_tasks_total:
                raise CertificationError("certified task set must certify every task")
            if self.diagnostic_mode:
                raise CertificationError("diagnostic result cannot be certified")


def validate_taskset_result_plain_integers(
    result: TasksetAnalysisResult,
) -> None:
    for field in (
        "n_tasks_total",
        "n_tasks_evaluated",
        "n_tasks_candidate_found",
        "n_tasks_certified",
    ):
        require_nonnegative_plain_int(getattr(result, field), field)
    failed = result.first_failed_priority
    if failed is not None:
        failed = require_nonnegative_plain_int(
            failed, "first_failed_priority"
        )
        if failed >= result.n_tasks_total:
            raise CertificationError(
                "first_failed_priority is outside the task set"
            )
    if result.n_tasks_total != len(result.task_records):
        raise CertificationError("n_tasks_total does not match task records")
    expected_ranks = tuple(range(result.n_tasks_total))
    observed_ranks = tuple(
        record.priority_rank for record in result.task_records
    )
    if observed_ranks != expected_ranks:
        raise CertificationError(
            "priority_rank must form a contiguous zero-based sequence"
        )
    for record in result.task_records:
        validate_task_record_plain_integers(record)
    evaluated = sum(
        record.solver_status not in {
            TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
            TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
        }
        for record in result.task_records
    )
    if result.n_tasks_evaluated != evaluated:
        raise CertificationError("n_tasks_evaluated mismatch")
    if result.n_tasks_candidate_found != sum(
        record.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        for record in result.task_records
    ):
        raise CertificationError("n_tasks_candidate_found mismatch")
    if result.n_tasks_certified != sum(
        record.certification_status is TaskCertificationStatus.CERTIFIED
        for record in result.task_records
    ):
        raise CertificationError("n_tasks_certified mismatch")
    try:
        for _task_id, value in result.source_candidate_vector:
            require_plain_int(value, "source_candidate_vector value")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, CertificationError):
            raise
        raise CertificationError(
            "source_candidate_vector must contain task/integer pairs"
        ) from exc
    if result.dominance_counterexample is not None:
        validate_dominance_counterexample_plain_integers(
            result.dominance_counterexample
        )


class SingleTaskSolver(Protocol):
    def __call__(
        self,
        *,
        task: core.V93Task,
        hp_tasks: Sequence[core.V93Task],
        lp_tasks: Sequence[core.V93Task],
        carry_in_vector: Mapping[str, int],
        window_mode: core.EnvelopeKind,
        energy_input: TasksetAnalysisInput,
        timeout_seconds: Optional[float],
    ) -> SingleTaskSolverResult:
        ...


def solve_single_task_v9_3(
    *,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    window_mode: core.EnvelopeKind,
    energy_input: TasksetAnalysisInput,
    timeout_seconds: Optional[float],
) -> SingleTaskSolverResult:
    """Adapt one exact core result without assigning certification semantics."""

    result = core.canonical_closure_search_v9_3(
        window_mode,
        task,
        hp_tasks,
        lp_tasks,
        energy_input.processors,
        carry_in_vector,
        energy_input.e0,
        energy_input.beta,
        timeout_seconds=timeout_seconds,
    )
    status = result.solver_status
    if status is core.V93SolverStatus.CANDIDATE:
        mapped = TaskSolverStatus.CANDIDATE_FOUND
    elif status is core.V93SolverStatus.NO_CANDIDATE:
        mapped = TaskSolverStatus.NO_CANDIDATE
    elif status is core.V93SolverStatus.UNPROVEN_TIMEOUT:
        mapped = TaskSolverStatus.TIMEOUT
    elif status in {
        core.V93SolverStatus.UNPROVEN_NUMERIC,
        core.V93SolverStatus.UNPROVEN_OVERFLOW,
    }:
        mapped = TaskSolverStatus.NUMERIC_ERROR
    else:
        mapped = TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    candidate_value = (
        result.candidate_response_time
        if mapped is TaskSolverStatus.CANDIDATE_FOUND
        else None
    )
    return SingleTaskSolverResult(
        solver_status=mapped,
        candidate_response_time=candidate_value,
        closing_w=result.closing_w if candidate_value is not None else None,
        witness_h=result.witness_h if candidate_value is not None else None,
        checked_w_count=result.checked_w_count,
        checked_h_count=result.checked_h_count,
        checked_q_count=result.checked_q_count,
        envelope_call_count=result.envelope_call_count,
        failure_reason=result.failure_reason,
    )


def solve_single_task_ph_v9_3(
    *,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    window_mode: core.EnvelopeKind,
    energy_input: TasksetAnalysisInput,
    timeout_seconds: Optional[float],
) -> SingleTaskSolverResult:
    """Adapt the independent exact PH core without adding experiment wiring."""

    # Keep the frozen CW/LOC evidence and mutation harness independent of the
    # optional directed PH module unless PH is actually requested.
    import asap_block_rta_v9_3_ph as ph_core

    if window_mode is not core.EnvelopeKind.LOCAL:
        return SingleTaskSolverResult(
            TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            failure_reason="PH requires local-window coverage",
        )
    result = ph_core.ph_response_time_v9_3(
        target=task,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=energy_input.processors,
        theta_by_name=carry_in_vector,
        e0=energy_input.e0,
        beta=energy_input.beta,
        timeout_seconds=timeout_seconds,
    )
    status = result.solver_status
    if status is ph_core.PHSearchStatus.CANDIDATE:
        mapped = TaskSolverStatus.CANDIDATE_FOUND
    elif status is ph_core.PHSearchStatus.NO_CANDIDATE:
        mapped = TaskSolverStatus.NO_CANDIDATE
    elif status is ph_core.PHSearchStatus.UNPROVEN_TIMEOUT:
        mapped = TaskSolverStatus.TIMEOUT
    elif status is ph_core.PHSearchStatus.UNPROVEN_NUMERIC:
        mapped = TaskSolverStatus.NUMERIC_ERROR
    else:
        mapped = TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    candidate = (
        result.candidate_response_time
        if mapped is TaskSolverStatus.CANDIDATE_FOUND
        else None
    )
    return SingleTaskSolverResult(
        solver_status=mapped,
        candidate_response_time=candidate,
        closing_w=result.closing_w if candidate is not None else None,
        witness_h=result.witness_h if candidate is not None else None,
        checked_w_count=result.checked_w_count,
        checked_h_count=result.checked_h_count,
        checked_q_count=result.checked_q_count,
        envelope_call_count=result.envelope_call_count,
        failure_reason=result.failure_reason,
    )


def solve_single_task_seq_v9_3(
    *,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    window_mode: core.EnvelopeKind,
    energy_input: TasksetAnalysisInput,
    timeout_seconds: Optional[float],
) -> SingleTaskSolverResult:
    """Adapt the independent exact SEQ core without experiment wiring."""

    import asap_block_rta_v9_3_seq as seq_core

    if window_mode is not core.EnvelopeKind.LOCAL:
        return SingleTaskSolverResult(
            TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            failure_reason="SEQ requires local-window PH safety",
        )
    result = seq_core.seq_response_time_v9_3(
        target=task,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=energy_input.processors,
        theta_by_name=carry_in_vector,
        e0=energy_input.e0,
        beta=energy_input.beta,
        timeout_seconds=timeout_seconds,
    )
    try:
        result = seq_core.SEQSearchResult(
            solver_status=result.solver_status,
            candidate_response_time=result.candidate_response_time,
            closing_w=result.closing_w,
            processor_progress_a=result.processor_progress_a,
            maximum_blocking_h=result.maximum_blocking_h,
            witness_sequence=result.witness_sequence,
            witness_h=result.witness_h,
            checked_w_count=result.checked_w_count,
            checked_h_count=result.checked_h_count,
            checked_q_count=result.checked_q_count,
            envelope_call_count=result.envelope_call_count,
            impossible_prefix_count=result.impossible_prefix_count,
            failure_reason=result.failure_reason,
        )
    except Exception as exc:
        return SingleTaskSolverResult(
            TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            failure_reason="malformed SEQ search certificate: {}".format(exc),
        )
    status = result.solver_status
    if status is seq_core.SEQSearchStatus.CANDIDATE:
        mapped = TaskSolverStatus.CANDIDATE_FOUND
    elif status is seq_core.SEQSearchStatus.NO_CANDIDATE:
        mapped = TaskSolverStatus.NO_CANDIDATE
    elif status is seq_core.SEQSearchStatus.UNPROVEN_TIMEOUT:
        mapped = TaskSolverStatus.TIMEOUT
    elif status is seq_core.SEQSearchStatus.UNPROVEN_NUMERIC:
        mapped = TaskSolverStatus.NUMERIC_ERROR
    else:
        mapped = TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
    candidate = (
        result.candidate_response_time
        if mapped is TaskSolverStatus.CANDIDATE_FOUND
        else None
    )
    return SingleTaskSolverResult(
        solver_status=mapped,
        candidate_response_time=candidate,
        closing_w=result.closing_w if candidate is not None else None,
        witness_h=result.witness_h if candidate is not None else None,
        checked_w_count=result.checked_w_count,
        checked_h_count=result.checked_h_count,
        checked_q_count=result.checked_q_count,
        envelope_call_count=result.envelope_call_count,
        failure_reason=result.failure_reason,
    )


def _record(
    task: core.V93Task,
    rank: int,
    result: SingleTaskSolverResult,
    carry: Mapping[str, int],
    certification: TaskCertificationStatus,
) -> TaskAnalysisRecord:
    return TaskAnalysisRecord(
        task_id=task.name,
        priority_rank=rank,
        solver_status=result.solver_status,
        certification_status=certification,
        candidate_response_time=result.candidate_response_time,
        carry_in_values_used=tuple(sorted(carry.items())),
        closing_w=result.closing_w,
        witness_h=result.witness_h,
        checked_w_count=result.checked_w_count,
        checked_h_count=result.checked_h_count,
        checked_q_count=result.checked_q_count,
        envelope_call_count=result.envelope_call_count,
        failure_reason=result.failure_reason,
    )


def _not_evaluated(task: core.V93Task, rank: int) -> TaskAnalysisRecord:
    return _record(
        task,
        rank,
        SingleTaskSolverResult(
            TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
            failure_reason="not evaluated after prefix failure",
        ),
        {},
        TaskCertificationStatus.NOT_APPLICABLE,
    )


def _not_applicable(task: core.V93Task, rank: int) -> TaskAnalysisRecord:
    return _record(
        task,
        rank,
        SingleTaskSolverResult(
            TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
            failure_reason="fixed carry-in dependency is not applicable",
        ),
        {},
        TaskCertificationStatus.NOT_APPLICABLE,
    )


def _numeric_failure_result(
    *,
    analysis_id: str,
    variant: AnalysisVariant,
    tasks: Sequence[core.V93Task],
    context: DependencyContext,
    reason: str,
    dependency_status: DependencyVectorCheckStatus,
    planned_source_analysis_id: Optional[str],
) -> TasksetAnalysisResult:
    first = _record(
        tasks[0],
        0,
        SingleTaskSolverResult(
            TaskSolverStatus.NUMERIC_ERROR,
            failure_reason=reason,
        ),
        {},
        TaskCertificationStatus.NOT_CERTIFIED,
    )
    records = (first,) + tuple(
        _not_evaluated(task, rank)
        for rank, task in enumerate(tasks[1:], start=1)
    )
    recursive = variant in {
        AnalysisVariant.CW_THETA_CW,
        AnalysisVariant.LOC_THETA_LOC,
        AnalysisVariant.PH_THETA_PH,
        AnalysisVariant.SEQ_THETA_SEQ,
    }
    return _make_result(
        analysis_id=analysis_id,
        variant=variant,
        records=records,
        solver_status=AnalysisSolverStatus.NUMERIC_ERROR,
        certification_status=AnalysisCertificationStatus.NOT_CERTIFIED,
        first_failed_priority=0,
        source=None,
        source_vector=(),
        dependency_status=dependency_status,
        interface_status=(
            FixedCarryInInterfaceStatus.NOT_APPLICABLE
            if recursive else FixedCarryInInterfaceStatus.HASH_MISMATCH
        ),
        dominance_status=DominanceInvariantStatus.NOT_CHECKED,
        diagnostic_mode=False,
        context=context,
        planned_source_analysis_id=planned_source_analysis_id,
    )


def _validate_record_vector(
    tasks: Sequence[core.V93Task], records: Sequence[TaskAnalysisRecord]
) -> None:
    if len(tasks) != len(records):
        raise CertificationError("finalizer requires one record per task")
    expected = tuple((task.name, rank) for rank, task in enumerate(tasks))
    actual = tuple((record.task_id, record.priority_rank) for record in records)
    if actual != expected or len({record.task_id for record in records}) != len(records):
        raise CertificationError("task IDs and priority ranks must be complete and unique")
    for record in records:
        validate_task_record_plain_integers(record)
    if any(
        record.certification_status is TaskCertificationStatus.CERTIFIED
        for record in records
    ):
        raise CertificationError("pre-finalization records may not be CERTIFIED")


def validate_carry_in_trace(
    *,
    variant: AnalysisVariant,
    tasks: Sequence[core.V93Task],
    records: Sequence[TaskAnalysisRecord],
    compatibility_vector: Optional[Mapping[str, int]] = None,
) -> None:
    """Validate the exact per-task carry-in trace without mutating it."""

    if len(tasks) != len(records):
        raise CertificationError("carry-in trace requires one record per task")
    expected_order = tuple(
        (task.name, rank) for rank, task in enumerate(tasks)
    )
    observed_order = tuple(
        (record.task_id, record.priority_rank) for record in records
    )
    if observed_order != expected_order:
        raise CertificationError("carry-in trace task order mismatch")
    for record in records:
        validate_task_record_plain_integers(record)

    recursive = variant in {
        AnalysisVariant.CW_THETA_CW,
        AnalysisVariant.LOC_THETA_LOC,
        AnalysisVariant.PH_THETA_PH,
        AnalysisVariant.SEQ_THETA_SEQ,
    }
    fixed = not recursive
    frozen: Optional[Tuple[Tuple[str, int], ...]] = None
    if fixed:
        if compatibility_vector is None:
            if variant is not AnalysisVariant.LOC_THETA_CW or any(
                record.solver_status
                is not TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY
                for record in records
            ):
                raise CertificationError(
                    "fixed carry-in trace requires a compatibility vector"
                )
        else:
            expected_ids = {task.name for task in tasks}
            if set(compatibility_vector) != expected_ids:
                raise CertificationError(
                    "compatibility vector must cover the task set"
                )
            for value in compatibility_vector.values():
                require_plain_int(value, "compatibility vector value")
            frozen = tuple(sorted(compatibility_vector.items()))

    prefix: dict[str, int] = {}
    for record in records:
        inactive = record.solver_status in {
            TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
            TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
        }
        expected = () if inactive else (
            tuple(sorted(prefix.items())) if recursive else frozen
        )
        if record.carry_in_values_used != expected:
            raise CertificationError(
                f"carry-in trace mismatch at task {record.task_id}"
            )
        if recursive and record.solver_status is TaskSolverStatus.CANDIDATE_FOUND:
            if record.candidate_response_time is None:
                raise CertificationError(
                    "recursive carry-in prefix has a missing candidate"
                )
            prefix[record.task_id] = record.candidate_response_time


def _make_result(
    *,
    analysis_id: str,
    variant: AnalysisVariant,
    records: Tuple[TaskAnalysisRecord, ...],
    solver_status: AnalysisSolverStatus,
    certification_status: AnalysisCertificationStatus,
    first_failed_priority: Optional[int],
    source: Optional[TasksetAnalysisResult],
    source_vector: Tuple[Tuple[str, int], ...],
    dependency_status: DependencyVectorCheckStatus,
    interface_status: FixedCarryInInterfaceStatus,
    dominance_status: DominanceInvariantStatus,
    diagnostic_mode: bool,
    context: DependencyContext,
    counterexample: Optional[DominanceCounterexample] = None,
    planned_source_analysis_id: Optional[str] = None,
) -> TasksetAnalysisResult:
    evaluated = sum(
        record.solver_status
        not in {
            TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
            TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
        }
        for record in records
    )
    return TasksetAnalysisResult(
        analysis_id=analysis_id,
        analysis_variant=variant,
        method_role=ROLE_BY_VARIANT[variant],
        solver_status=solver_status,
        certification_status=certification_status,
        task_records=records,
        first_failed_priority=first_failed_priority,
        n_tasks_total=len(records),
        n_tasks_evaluated=evaluated,
        n_tasks_candidate_found=sum(
            record.solver_status is TaskSolverStatus.CANDIDATE_FOUND
            for record in records
        ),
        n_tasks_certified=sum(
            record.certification_status is TaskCertificationStatus.CERTIFIED
            for record in records
        ),
        taskset_proven=(
            certification_status is AnalysisCertificationStatus.CERTIFIED_TASKSET
        ),
        source_analysis_id=(
            planned_source_analysis_id
            or (
                source.analysis_id
                if variant is AnalysisVariant.LOC_THETA_CW and source is not None
                else None
            )
        ),
        source_candidate_vector=(
            source_vector if variant is AnalysisVariant.LOC_THETA_CW else ()
        ),
        dependency_check_status=dependency_status,
        fixed_carry_in_interface_status=interface_status,
        dominance_invariant_status=dominance_status,
        diagnostic_mode=diagnostic_mode,
        dependency_context=context,
        dominance_counterexample=counterexample,
        _finalizer_token=_FINALIZER_TOKEN,
    )


def finalize_joint_certification(
    *,
    analysis_id: str,
    variant: AnalysisVariant,
    tasks: Sequence[core.V93Task],
    records: Sequence[TaskAnalysisRecord],
    context: DependencyContext,
    interface_status: FixedCarryInInterfaceStatus,
    dependency_status: DependencyVectorCheckStatus,
    compatibility_vector: Optional[Mapping[str, int]] = None,
    source: Optional[TasksetAnalysisResult] = None,
    observer: Optional[Callable[[str, Tuple[TaskAnalysisRecord, ...]], None]] = None,
) -> TasksetAnalysisResult:
    """Validate the complete vector, then atomically create certified copies."""

    _validate_record_vector(tasks, records)
    before = tuple(records)
    if any(record.solver_status is not TaskSolverStatus.CANDIDATE_FOUND for record in before):
        raise CertificationError("joint certification requires candidates for all tasks")
    for task, record in zip(tasks, before):
        if not task.wcet <= record.candidate_response_time <= task.deadline:
            raise CertificationError("candidate must satisfy C_i <= R_i <= D_i")
    recursive = variant in {
        AnalysisVariant.CW_THETA_CW,
        AnalysisVariant.LOC_THETA_LOC,
        AnalysisVariant.PH_THETA_PH,
        AnalysisVariant.SEQ_THETA_SEQ,
    }
    if recursive:
        if compatibility_vector is not None:
            raise CertificationError("recursive analysis may not use a fixed compatibility vector")
        if interface_status is not FixedCarryInInterfaceStatus.NOT_APPLICABLE:
            raise CertificationError("recursive analysis does not use fixed carry-in interface")
    else:
        if interface_status is not FixedCarryInInterfaceStatus.ACTIVE:
            raise CertificationError("fixed carry-in certification requires ACTIVE interface")
        if not _interface_active(context):
            raise CertificationError("fixed carry-in certification hash mismatch")
        if compatibility_vector is None:
            raise CertificationError("fixed carry-in certification requires compatibility vector")
    if variant in {AnalysisVariant.CW_D, AnalysisVariant.LOC_D}:
        deadlines = {task.name: task.deadline for task in tasks}
        if dict(compatibility_vector) != deadlines:
            raise CertificationError("deadline carry-in vector must equal D")
    if variant is AnalysisVariant.LOC_THETA_CW:
        certified_vector = _certified_source_vector(source, tasks)
        if certified_vector is None:
            raise CertificationError("LOC-Theta^cw requires a jointly certified CW source")
        if source.dependency_context != context:
            raise CertificationError("LOC-Theta^cw source/target identity mismatch")
        if dependency_status is not DependencyVectorCheckStatus.VALID:
            raise CertificationError("LOC-Theta^cw dependency vector must be VALID")
        if dict(compatibility_vector) != certified_vector:
            raise CertificationError("LOC-Theta^cw vector must equal frozen CW source")
    if compatibility_vector is not None:
        expected_ids = {task.name for task in tasks}
        if set(compatibility_vector) != expected_ids:
            raise CertificationError("compatibility vector must cover the task set")
        for task, record in zip(tasks, before):
            gamma = compatibility_vector[record.task_id]
            if isinstance(gamma, bool) or not isinstance(gamma, int):
                raise CertificationError("fixed carry-in values must be integers")
            if not task.wcet <= gamma <= task.deadline:
                raise CertificationError("fixed carry-in must satisfy C_i <= Gamma_i <= D_i")
            if record.candidate_response_time > compatibility_vector[record.task_id]:
                raise CertificationError("candidate exceeds fixed carry-in vector")
    validate_carry_in_trace(
        variant=variant,
        tasks=tasks,
        records=before,
        compatibility_vector=compatibility_vector,
    )
    if observer:
        observer("before", before)
    upgraded = tuple(
        replace(
            record,
            certification_status=TaskCertificationStatus.CERTIFIED,
            _certification_token=_FINALIZER_TOKEN,
        )
        for record in before
    )
    if observer:
        observer("after", upgraded)
    source_vector = (
        tuple(sorted(compatibility_vector.items()))
        if compatibility_vector is not None
        else ()
    )
    dominance = (
        DominanceInvariantStatus.SATISFIED
        if variant is AnalysisVariant.LOC_THETA_CW
        else DominanceInvariantStatus.NOT_APPLICABLE
    )
    return _make_result(
        analysis_id=analysis_id,
        variant=variant,
        records=upgraded,
        solver_status=AnalysisSolverStatus.COMPLETED,
        certification_status=AnalysisCertificationStatus.CERTIFIED_TASKSET,
        first_failed_priority=None,
        source=source,
        source_vector=source_vector,
        dependency_status=dependency_status,
        interface_status=interface_status,
        dominance_status=dominance,
        diagnostic_mode=False,
        context=context,
    )


def _interface_active(context: DependencyContext) -> bool:
    return (
        context.theory_document_sha256 == THEORY_DOCUMENT_SHA256
        and context.fixed_carry_in_interface_sha256
        == FIXED_CARRY_IN_INTERFACE_SHA256
        and context.numeric_contract_sha256
        == exact_energy.NUMERIC_CONTRACT_SHA256
        and context.source_numeric_model == exact_energy.SOURCE_NUMERIC_MODEL
        and context.demand_rounding_mode == exact_energy.DEMAND_ROUNDING_MODE
        and context.supply_rounding_mode == exact_energy.SUPPLY_ROUNDING_MODE
        and context.e0_rounding_mode == exact_energy.E0_ROUNDING_MODE
        and bool(context.exact_input_identity)
        and context.float_decision_path is False
    )


def _certified_source_vector(
    source: Optional[TasksetAnalysisResult],
    tasks: Sequence[core.V93Task],
) -> Optional[dict]:
    if source is None:
        return None
    # A caller can bypass frozen-dataclass construction (for example while
    # loading an untrusted artifact).  Revalidate every integer-bearing field
    # before deriving a compatibility vector or invoking a task solver.
    validate_taskset_result_plain_integers(source)
    if source.analysis_variant is not AnalysisVariant.CW_THETA_CW:
        return None
    if source.solver_status is not AnalysisSolverStatus.COMPLETED:
        return None
    if source.certification_status is not AnalysisCertificationStatus.CERTIFIED_TASKSET:
        return None
    if (
        source.method_role is not ROLE_BY_VARIANT[AnalysisVariant.CW_THETA_CW]
        or not isinstance(source.taskset_proven, bool)
        or not source.taskset_proven
        or not isinstance(source.diagnostic_mode, bool)
        or source.diagnostic_mode
        or source.first_failed_priority is not None
        or source.n_tasks_total != len(tasks)
        or source.n_tasks_evaluated != len(tasks)
        or source.n_tasks_candidate_found != len(tasks)
        or source.n_tasks_certified != len(tasks)
        or source.source_analysis_id is not None
        or source.source_candidate_vector
        or source.dependency_check_status
        is not DependencyVectorCheckStatus.NOT_CHECKED
        or source.fixed_carry_in_interface_status
        is not FixedCarryInInterfaceStatus.NOT_APPLICABLE
        or source.dominance_invariant_status
        is not DominanceInvariantStatus.NOT_APPLICABLE
        or source.dominance_counterexample is not None
    ):
        raise CertificationError(
            "certified CW source violates the joint-certification result matrix"
        )
    expected_records = tuple(
        (task.name, rank) for rank, task in enumerate(tasks)
    )
    if tuple(
        (record.task_id, record.priority_rank)
        for record in source.task_records
    ) != expected_records:
        raise CertificationError(
            "certified CW source task order does not match the target task set"
        )
    for task, record in zip(tasks, source.task_records):
        if (
            record.solver_status is not TaskSolverStatus.CANDIDATE_FOUND
            or record.certification_status is not TaskCertificationStatus.CERTIFIED
            or record.candidate_response_time is None
            or not task.wcet <= record.candidate_response_time <= task.deadline
            or record.closing_w != record.candidate_response_time
        ):
            raise CertificationError(
                "certified CW source task record violates C <= R = closing_w <= D"
            )
    validate_carry_in_trace(
        variant=AnalysisVariant.CW_THETA_CW,
        tasks=tasks,
        records=source.task_records,
    )
    return {
        record.task_id: record.candidate_response_time
        for record in source.task_records
    }


def analyze_taskset_v9_3(
    analysis_id: str,
    variant: AnalysisVariant,
    analysis_input: TasksetAnalysisInput,
    *,
    source: Optional[TasksetAnalysisResult] = None,
    source_analysis_id: Optional[str] = None,
    dependency_check_status: DependencyVectorCheckStatus = DependencyVectorCheckStatus.NOT_CHECKED,
    fixed_carry_in_interface_status: Optional[FixedCarryInInterfaceStatus] = None,
    diagnostic_mode: bool = False,
    diagnostic_carry_in_vector: Optional[Mapping[str, int]] = None,
    single_task_solver: SingleTaskSolver = solve_single_task_v9_3,
    finalization_observer: Optional[Callable[[str, Tuple[TaskAnalysisRecord, ...]], None]] = None,
) -> TasksetAnalysisResult:
    """Run a registered v9.3 task-set analysis.

    PH and SEQ are directed mathematical APIs only; formal runners retain
    their frozen five-configuration order and never call them implicitly.
    This compatibility entry preserves its historical per-task timeout
    forwarding; the eight-method adapter below owns request-wide budgets.
    """

    if not isinstance(variant, AnalysisVariant):
        raise CertificationError("variant must be an AnalysisVariant")
    if not isinstance(dependency_check_status, DependencyVectorCheckStatus):
        raise CertificationError(
            "dependency_check_status must be a DependencyVectorCheckStatus"
        )
    if variant is AnalysisVariant.LOC_THETA_CW:
        resolved_source_analysis_id = (
            source.analysis_id if source is not None else source_analysis_id
        )
        if not resolved_source_analysis_id:
            raise CertificationError(
                "LOC-Theta^cw requires a source result or planned source ID"
            )
        if (
            source is not None
            and source_analysis_id is not None
            and source.analysis_id != source_analysis_id
        ):
            raise CertificationError("LOC-Theta^cw planned source ID mismatch")
        if source is None and (
            dependency_check_status is not DependencyVectorCheckStatus.INVALID
        ):
            raise CertificationError(
                "missing LOC-Theta^cw source must have INVALID dependency"
            )
        if dependency_check_status not in {
            DependencyVectorCheckStatus.VALID,
            DependencyVectorCheckStatus.INVALID,
        }:
            raise CertificationError(
                "LOC-Theta^cw dependency status must be VALID or INVALID"
            )
        if diagnostic_carry_in_vector is not None:
            raise CertificationError(
                "LOC-Theta^cw may not fall back to a diagnostic carry-in vector"
            )
    else:
        resolved_source_analysis_id = None
        if source is not None:
            raise CertificationError(
                "only LOC-Theta^cw may receive a source result"
            )
        if source_analysis_id is not None:
            raise CertificationError(
                "only LOC-Theta^cw may receive a planned source ID"
            )
        if dependency_check_status is not DependencyVectorCheckStatus.NOT_CHECKED:
            raise CertificationError(
                "non-LOC-Theta^cw dependency status must be NOT_CHECKED"
            )
    tasks = analysis_input.tasks
    exact_phase_variant = variant in {
        AnalysisVariant.PH_THETA_PH,
        AnalysisVariant.SEQ_THETA_SEQ,
    }
    if exact_phase_variant and any(
        earlier.period > later.period
        for earlier, later in zip(tasks, tasks[1:])
    ):
        raise CertificationError(
            "PH/SEQ recursive tasks must be supplied in nondecreasing RM period order"
        )
    recursive = variant in {
        AnalysisVariant.CW_THETA_CW,
        AnalysisVariant.LOC_THETA_LOC,
        AnalysisVariant.PH_THETA_PH,
        AnalysisVariant.SEQ_THETA_SEQ,
    }
    if (
        variant is AnalysisVariant.PH_THETA_PH
        and single_task_solver is solve_single_task_v9_3
    ):
        single_task_solver = solve_single_task_ph_v9_3
    if (
        variant is AnalysisVariant.SEQ_THETA_SEQ
        and single_task_solver is solve_single_task_v9_3
    ):
        single_task_solver = solve_single_task_seq_v9_3
    if not _interface_active(analysis_input.dependency_context):
        return _numeric_failure_result(
            analysis_id=analysis_id,
            variant=variant,
            tasks=tasks,
            context=analysis_input.dependency_context,
            reason="numeric/theory contract mismatch",
            dependency_status=dependency_check_status,
            planned_source_analysis_id=resolved_source_analysis_id,
        )
    try:
        validated_beta = core.validate_service_curve_v9_3(
            analysis_input.beta,
            max(task.deadline for task in analysis_input.tasks) - 1,
        )
    except core.V93NumericError as exc:
        return _numeric_failure_result(
            analysis_id=analysis_id,
            variant=variant,
            tasks=tasks,
            context=analysis_input.dependency_context,
            reason="invalid theorem-backed service curve: {}".format(exc),
            dependency_status=dependency_check_status,
            planned_source_analysis_id=resolved_source_analysis_id,
        )
    exact_e0 = analysis_input.e0
    if exact_phase_variant:
        try:
            exact_e0 = core.exact_fraction_v9_3(analysis_input.e0, "E0")
            if exact_e0 < 0:
                raise core.V93NumericError("E0 must be non-negative")
            if callable(analysis_input.beta):
                identity_service_prefix = validated_beta
            else:
                identity_service_prefix = core.validate_service_curve_v9_3(
                    analysis_input.beta, len(analysis_input.beta) - 1
                )
            expected_exact_input_identity = exact_energy.exact_input_identity(
                task_powers=(
                    (task.name, task.power) for task in analysis_input.tasks
                ),
                e0=exact_e0,
                service_prefix=identity_service_prefix,
            )
        except (
            core.V93NumericError,
            exact_energy.ExactEnergyError,
            ArithmeticError,
            TypeError,
            ValueError,
        ) as exc:
            return _numeric_failure_result(
                analysis_id=analysis_id,
                variant=variant,
                tasks=tasks,
                context=analysis_input.dependency_context,
                reason="invalid exact PH/SEQ input identity material: {}".format(exc),
                dependency_status=dependency_check_status,
                planned_source_analysis_id=resolved_source_analysis_id,
            )
        if (
            analysis_input.dependency_context.exact_input_identity
            != expected_exact_input_identity
        ):
            return _numeric_failure_result(
                analysis_id=analysis_id,
                variant=variant,
                tasks=tasks,
                context=analysis_input.dependency_context,
                reason="exact PH/SEQ input identity mismatch",
                dependency_status=dependency_check_status,
                planned_source_analysis_id=resolved_source_analysis_id,
            )
    analysis_input = replace(
        analysis_input, e0=exact_e0, beta=validated_beta
    )
    if fixed_carry_in_interface_status is None:
        fixed_carry_in_interface_status = (
            FixedCarryInInterfaceStatus.NOT_APPLICABLE
            if recursive
            else FixedCarryInInterfaceStatus.ACTIVE
        )
    if (
        not recursive
        and fixed_carry_in_interface_status is FixedCarryInInterfaceStatus.ACTIVE
        and not _interface_active(analysis_input.dependency_context)
    ):
        fixed_carry_in_interface_status = FixedCarryInInterfaceStatus.HASH_MISMATCH
    window = (
        core.EnvelopeKind.COMPLETE
        if variant in {AnalysisVariant.CW_D, AnalysisVariant.CW_THETA_CW}
        else core.EnvelopeKind.LOCAL
    )

    fixed_vector = None
    interface_valid = (
        fixed_carry_in_interface_status is FixedCarryInInterfaceStatus.ACTIVE
        and _interface_active(analysis_input.dependency_context)
    )
    if variant in {AnalysisVariant.CW_D, AnalysisVariant.LOC_D}:
        fixed_vector = {task.name: task.deadline for task in tasks}
        applicable = interface_valid
    elif variant is AnalysisVariant.LOC_THETA_CW:
        certified_vector = _certified_source_vector(source, tasks)
        identities_match = bool(
            source
            and source.dependency_context == analysis_input.dependency_context
        )
        source_dependency_valid = bool(certified_vector and identities_match)
        if (
            not source_dependency_valid
            or not interface_valid
            or dependency_check_status is DependencyVectorCheckStatus.INVALID
        ):
            dependency_check_status = DependencyVectorCheckStatus.INVALID
        applicable = bool(
            certified_vector
            and identities_match
            and interface_valid
            and dependency_check_status is DependencyVectorCheckStatus.VALID
        )
        fixed_vector = certified_vector
    else:
        applicable = True

    if not applicable and (
        variant is AnalysisVariant.LOC_THETA_CW or not diagnostic_mode
    ):
        records = tuple(_not_applicable(task, rank) for rank, task in enumerate(tasks))
        return _make_result(
            analysis_id=analysis_id,
            variant=variant,
            records=records,
            solver_status=AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY,
            certification_status=AnalysisCertificationStatus.NOT_APPLICABLE,
            first_failed_priority=None,
            source=source,
            source_vector=tuple(sorted((fixed_vector or {}).items())),
            dependency_status=dependency_check_status,
            interface_status=fixed_carry_in_interface_status,
            dominance_status=DominanceInvariantStatus.NOT_APPLICABLE,
            diagnostic_mode=False,
            context=analysis_input.dependency_context,
            planned_source_analysis_id=resolved_source_analysis_id,
        )

    records = []
    recursive_candidates = {}
    counterexample = None
    terminal = None
    failed_rank = None
    for rank, task in enumerate(tasks):
        hp_tasks = tasks[:rank]
        lp_tasks = tasks[rank + 1 :]
        carry = recursive_candidates if recursive else fixed_vector
        solver_result = single_task_solver(
            task=task,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            carry_in_vector=dict(carry),
            window_mode=window,
            energy_input=analysis_input,
            timeout_seconds=analysis_input.timeout_seconds,
        )
        status = solver_result.solver_status
        certification = (
            TaskCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
            if diagnostic_mode and status is TaskSolverStatus.CANDIDATE_FOUND
            else (
                TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
                if status is TaskSolverStatus.CANDIDATE_FOUND
                else TaskCertificationStatus.NOT_CERTIFIED
            )
        )
        record = _record(task, rank, solver_result, carry, certification)

        compatibility_limit = fixed_vector.get(task.name) if fixed_vector else task.deadline
        dominance_failure = (
            status is TaskSolverStatus.CANDIDATE_FOUND
            and solver_result.candidate_response_time > compatibility_limit
        )
        if (
            variant is AnalysisVariant.LOC_THETA_CW
            and applicable
            and status is TaskSolverStatus.NO_CANDIDATE
        ):
            dominance_failure = True
        if dominance_failure and not diagnostic_mode:
            record = replace(record, certification_status=TaskCertificationStatus.NOT_CERTIFIED)
            counterexample = DominanceCounterexample(
                task_id=task.name,
                priority_rank=rank,
                source_candidate=compatibility_limit,
                local_candidate=solver_result.candidate_response_time,
                carry_in_vector=tuple(sorted(carry.items())),
                checked_w_count=solver_result.checked_w_count,
                checked_h_count=solver_result.checked_h_count,
                checked_q_count=solver_result.checked_q_count,
                envelope_call_count=solver_result.envelope_call_count,
            )
            terminal = AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        elif status is TaskSolverStatus.NO_CANDIDATE:
            terminal = AnalysisSolverStatus.NO_CANDIDATE
        elif status is TaskSolverStatus.TIMEOUT:
            terminal = AnalysisSolverStatus.TIMEOUT
        elif status is TaskSolverStatus.NUMERIC_ERROR:
            terminal = AnalysisSolverStatus.NUMERIC_ERROR
        elif status is TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE:
            terminal = AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        elif status is not TaskSolverStatus.CANDIDATE_FOUND:
            terminal = AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE

        records.append(record)
        if terminal is not None:
            failed_rank = rank
            records.extend(
                _not_evaluated(later, later_rank)
                for later_rank, later in enumerate(tasks[rank + 1 :], rank + 1)
            )
            break
        if recursive:
            recursive_candidates[task.name] = solver_result.candidate_response_time

    frozen_records = tuple(records)
    _validate_record_vector(tasks, frozen_records)
    source_vector = tuple(sorted((fixed_vector or {}).items()))
    if terminal is not None:
        return _make_result(
            analysis_id=analysis_id,
            variant=variant,
            records=frozen_records,
            solver_status=terminal,
            certification_status=(
                AnalysisCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED
                if diagnostic_mode
                else AnalysisCertificationStatus.NOT_CERTIFIED
            ),
            first_failed_priority=failed_rank,
            source=source,
            source_vector=source_vector,
            dependency_status=dependency_check_status,
            interface_status=fixed_carry_in_interface_status,
            dominance_status=(
                DominanceInvariantStatus.DOMINANCE_INVARIANT_VIOLATION
                if counterexample
                else DominanceInvariantStatus.NOT_CHECKED
            ),
            diagnostic_mode=diagnostic_mode,
            context=analysis_input.dependency_context,
            counterexample=counterexample,
            planned_source_analysis_id=resolved_source_analysis_id,
        )
    if diagnostic_mode:
        return _make_result(
            analysis_id=analysis_id,
            variant=variant,
            records=frozen_records,
            solver_status=AnalysisSolverStatus.COMPLETED,
            certification_status=AnalysisCertificationStatus.DIAGNOSTIC_ONLY_NOT_CERTIFIED,
            first_failed_priority=None,
            source=source,
            source_vector=source_vector,
            dependency_status=dependency_check_status,
            interface_status=fixed_carry_in_interface_status,
            dominance_status=DominanceInvariantStatus.NOT_CHECKED,
            diagnostic_mode=True,
            context=analysis_input.dependency_context,
            planned_source_analysis_id=resolved_source_analysis_id,
        )
    return finalize_joint_certification(
        analysis_id=analysis_id,
        variant=variant,
        tasks=tasks,
        records=frozen_records,
        context=analysis_input.dependency_context,
        interface_status=fixed_carry_in_interface_status,
        dependency_status=dependency_check_status,
        compatibility_vector=fixed_vector if not recursive else None,
        source=source,
        observer=finalization_observer,
    )


class MethodKernelDispatcher(Protocol):
    def __call__(
        self,
        *,
        method_spec: methods.V93MethodSpec,
        task: core.V93Task,
        hp_tasks: Sequence[core.V93Task],
        lp_tasks: Sequence[core.V93Task],
        carry_in_vector: Mapping[str, int],
        energy_input: TasksetAnalysisInput,
        timeout_seconds: Optional[float],
    ) -> V93KernelTaskResult:
        ...


class _AnalysisBudgetExpired(TimeoutError):
    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(
            "v9.3 unified request timeout at {}".format(stage)
        )


class _V93AnalysisBudget:
    """One monotonic wall-clock budget for a taskset x method request."""

    def __init__(
        self,
        timeout_seconds: Optional[float],
        clock: Callable[[], float],
        checkpoint_observer: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not callable(clock):
            raise CertificationError("analysis budget clock must be callable")
        if checkpoint_observer is not None and not callable(
            checkpoint_observer
        ):
            raise CertificationError(
                "analysis budget checkpoint observer must be callable"
            )
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise CertificationError(
                "timeout_seconds must be finite and non-negative"
            )
        self._clock = clock
        self._checkpoint_observer = checkpoint_observer
        self._last_read = self._read_clock()
        self.start_time = self._last_read
        self.absolute_deadline = (
            None
            if timeout_seconds is None
            else self.start_time + float(timeout_seconds)
        )
        if (
            self.absolute_deadline is not None
            and not math.isfinite(self.absolute_deadline)
        ):
            raise CertificationError(
                "analysis budget absolute deadline must be finite"
            )

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    def _read_clock(self) -> float:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise CertificationError(
                "analysis budget clock must return a finite number"
            )
        value = float(value)
        if hasattr(self, "_last_read") and value < self._last_read:
            raise CertificationError(
                "analysis budget clock must be monotonic"
            )
        self._last_read = value
        return value

    def checkpoint(self, stage: str) -> None:
        if not isinstance(stage, str) or not stage:
            raise CertificationError(
                "analysis budget stage must be non-empty"
            )
        if self._checkpoint_observer is not None:
            self._checkpoint_observer(stage)
        if self.absolute_deadline is None:
            return
        if self._read_clock() >= self.absolute_deadline:
            raise _AnalysisBudgetExpired(stage)

    def remaining_seconds(self) -> Optional[float]:
        if self.absolute_deadline is None:
            return None
        return max(0.0, self.absolute_deadline - self._read_clock())

    def elapsed_seconds(self) -> float:
        return max(0.0, self._read_clock() - self.start_time)


_VALIDATED_DISPATCH_ISSUER = object()


@dataclass(frozen=True)
class _ValidatedDispatchCapability:
    """Module-private proof binding one kernel call to validated input."""

    issuer: object
    analysis_nonce: object
    method_id: methods.V93MethodId
    validated_input: TasksetAnalysisInput
    task: core.V93Task
    priority_rank: int
    hp_tasks: Tuple[core.V93Task, ...]
    lp_tasks: Tuple[core.V93Task, ...]
    carry_items: Tuple[Tuple[str, int], ...]
    exact_input_identity: str


def _issue_dispatch_capability(
    *,
    analysis_nonce: object,
    spec: methods.V93MethodSpec,
    validated_input: TasksetAnalysisInput,
    task: core.V93Task,
    priority_rank: int,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
) -> _ValidatedDispatchCapability:
    return _ValidatedDispatchCapability(
        issuer=_VALIDATED_DISPATCH_ISSUER,
        analysis_nonce=analysis_nonce,
        method_id=spec.method_id,
        validated_input=validated_input,
        task=task,
        priority_rank=priority_rank,
        hp_tasks=tuple(hp_tasks),
        lp_tasks=tuple(lp_tasks),
        carry_items=tuple(carry_in_vector.items()),
        exact_input_identity=(
            validated_input.dependency_context.exact_input_identity
        ),
    )


def _validate_dispatch_capability(
    capability: object,
    *,
    spec: methods.V93MethodSpec,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    energy_input: TasksetAnalysisInput,
) -> None:
    if (
        not isinstance(capability, _ValidatedDispatchCapability)
        or capability.issuer is not _VALIDATED_DISPATCH_ISSUER
        or capability.method_id is not spec.method_id
        or capability.validated_input is not energy_input
        or capability.task is not task
        or capability.hp_tasks != tuple(hp_tasks)
        or capability.lp_tasks != tuple(lp_tasks)
        or capability.carry_items != tuple(carry_in_vector.items())
        or capability.exact_input_identity
        != energy_input.dependency_context.exact_input_identity
    ):
        raise CertificationError(
            "kernel dispatch requires a matching validated-input capability"
        )
    if capability.analysis_nonce is None:
        raise CertificationError(
            "validated-input capability is missing its analysis nonce"
        )
    ordered_tasks = energy_input.tasks
    rank = capability.priority_rank
    if (
        rank < 0
        or rank >= len(ordered_tasks)
        or ordered_tasks[rank] is not task
    ):
        raise CertificationError(
            "validated-input capability task binding mismatch"
        )


class _FlowTelemetry:
    """Observe the existing PH flow hook without changing its decisions."""

    def __init__(self, ph_core: object) -> None:
        self._ph_core = ph_core
        self.flow_solver_calls = 0
        self.flow_feasible_count = 0
        self.flow_infeasible_count = 0
        self.flow_node_count = 0
        self.flow_edge_count = 0
        self.feasibility_augmentations = 0
        self.optimality_cycle_cancellations = 0
        self.optimality_units_augmented = 0

    def __call__(self, nodes: Sequence[str], edges: Sequence[object], deadline: object):
        self.flow_solver_calls += 1
        self.flow_node_count += len(nodes)
        self.flow_edge_count += len(edges)
        result = self._ph_core._solve_min_cost_circulation(
            nodes, edges, deadline
        )
        if result.status is self._ph_core._FlowStatus.OPTIMAL:
            self.flow_feasible_count += 1
        elif result.status is self._ph_core._FlowStatus.INFEASIBLE:
            self.flow_infeasible_count += 1
        statistics = result.statistics
        if isinstance(statistics, self._ph_core.PHFlowStatistics):
            self.feasibility_augmentations += (
                statistics.feasibility_augmentations
            )
            self.optimality_cycle_cancellations += (
                statistics.optimality_cycle_cancellations
            )
            self.optimality_units_augmented += (
                statistics.optimality_units_augmented
            )
        return result

    def fields(self) -> Mapping[str, int]:
        return {
            "flow_solver_calls": self.flow_solver_calls,
            "flow_feasible_count": self.flow_feasible_count,
            "flow_infeasible_count": self.flow_infeasible_count,
            "z_branch_count": self.flow_solver_calls,
            # Node/edge counts are totals over every invoked z-branch graph.
            "flow_node_count": self.flow_node_count,
            "flow_edge_count": self.flow_edge_count,
            "flow_feasibility_augmentations": (
                self.feasibility_augmentations
            ),
            "flow_optimality_cycle_cancellations": (
                self.optimality_cycle_cancellations
            ),
            "flow_optimality_units_augmented": (
                self.optimality_units_augmented
            ),
        }


def _status_value(value: object) -> str:
    raw = getattr(value, "value", None)
    return raw if isinstance(raw, str) and raw else repr(value)


def _internal_kernel_result(
    reason: str,
    *,
    raw_status: str = "ADAPTER_INTERNAL_CONFORMANCE_FAILURE",
    telemetry: Optional[_FlowTelemetry] = None,
) -> V93KernelTaskResult:
    flow = telemetry.fields() if telemetry is not None else {}
    return V93KernelTaskResult(
        solver_status=TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        kernel_solver_status=raw_status,
        candidate_response_time=None,
        closing_w=None,
        witness_h=None,
        processor_progress_a=None,
        maximum_blocking_h=None,
        witness_sequence=(),
        checked_w_count=0,
        checked_h_count=0,
        checked_q_count=0,
        envelope_call_count=0,
        impossible_prefix_count=None,
        phase_safe_calls=None,
        flow_solver_calls=flow.get("flow_solver_calls"),
        flow_feasible_count=flow.get("flow_feasible_count"),
        flow_infeasible_count=flow.get("flow_infeasible_count"),
        z_branch_count=flow.get("z_branch_count"),
        flow_node_count=flow.get("flow_node_count"),
        flow_edge_count=flow.get("flow_edge_count"),
        flow_feasibility_augmentations=flow.get(
            "flow_feasibility_augmentations"
        ),
        flow_optimality_cycle_cancellations=flow.get(
            "flow_optimality_cycle_cancellations"
        ),
        flow_optimality_units_augmented=flow.get(
            "flow_optimality_units_augmented"
        ),
        failure_reason=reason,
        unavailable_metrics=("cache_hit_rate",),
    )


def _numeric_kernel_result(reason: str) -> V93KernelTaskResult:
    return V93KernelTaskResult(
        solver_status=TaskSolverStatus.NUMERIC_ERROR,
        kernel_solver_status="ADAPTER_INPUT_VALIDATION",
        candidate_response_time=None,
        closing_w=None,
        witness_h=None,
        processor_progress_a=None,
        maximum_blocking_h=None,
        witness_sequence=(),
        checked_w_count=0,
        checked_h_count=0,
        checked_q_count=0,
        envelope_call_count=0,
        impossible_prefix_count=None,
        phase_safe_calls=None,
        flow_solver_calls=None,
        flow_feasible_count=None,
        flow_infeasible_count=None,
        z_branch_count=None,
        flow_node_count=None,
        flow_edge_count=None,
        flow_feasibility_augmentations=None,
        flow_optimality_cycle_cancellations=None,
        flow_optimality_units_augmented=None,
        failure_reason=reason,
        unavailable_metrics=("solver_not_called",),
    )


def _timeout_kernel_result(
    stage: str,
    *,
    telemetry: Optional[_FlowTelemetry] = None,
) -> V93KernelTaskResult:
    flow = telemetry.fields() if telemetry is not None else {}
    return V93KernelTaskResult(
        solver_status=TaskSolverStatus.TIMEOUT,
        kernel_solver_status="ADAPTER_REQUEST_TIMEOUT",
        candidate_response_time=None,
        closing_w=None,
        witness_h=None,
        processor_progress_a=None,
        maximum_blocking_h=None,
        witness_sequence=(),
        checked_w_count=0,
        checked_h_count=0,
        checked_q_count=0,
        envelope_call_count=0,
        impossible_prefix_count=None,
        phase_safe_calls=None,
        flow_solver_calls=flow.get("flow_solver_calls"),
        flow_feasible_count=flow.get("flow_feasible_count"),
        flow_infeasible_count=flow.get("flow_infeasible_count"),
        z_branch_count=flow.get("z_branch_count"),
        flow_node_count=flow.get("flow_node_count"),
        flow_edge_count=flow.get("flow_edge_count"),
        flow_feasibility_augmentations=flow.get(
            "flow_feasibility_augmentations"
        ),
        flow_optimality_cycle_cancellations=flow.get(
            "flow_optimality_cycle_cancellations"
        ),
        flow_optimality_units_augmented=flow.get(
            "flow_optimality_units_augmented"
        ),
        failure_reason="v9.3 unified request timeout at {}".format(stage),
        unavailable_metrics=("timeout_discarded_candidate",),
    )


def _common_raw_fields(raw: object) -> Mapping[str, object]:
    return {
        "candidate_response_time": getattr(
            raw, "candidate_response_time"
        ),
        "closing_w": getattr(raw, "closing_w"),
        "witness_h": getattr(raw, "witness_h"),
        "checked_w_count": getattr(raw, "checked_w_count"),
        "checked_h_count": getattr(raw, "checked_h_count"),
        "checked_q_count": getattr(raw, "checked_q_count"),
        "envelope_call_count": getattr(raw, "envelope_call_count"),
        "failure_reason": getattr(raw, "failure_reason"),
    }


def _validate_raw_certificate_shape(
    *,
    found: bool,
    fields: Mapping[str, object],
) -> None:
    if found:
        if fields["candidate_response_time"] is None:
            raise CertificationError(
                "candidate kernel status is missing its candidate"
            )
        if fields["closing_w"] != fields["candidate_response_time"]:
            raise CertificationError(
                "candidate kernel status has inconsistent closing_w"
            )
        if fields["witness_h"] is None:
            raise CertificationError(
                "candidate kernel status is missing witness_h"
            )
        if fields["failure_reason"] is not None:
            raise CertificationError(
                "candidate kernel status carries a failure reason"
            )
    elif any(
        fields[field_name] is not None
        for field_name in (
            "candidate_response_time",
            "closing_w",
            "witness_h",
        )
    ):
        raise CertificationError(
            "non-candidate kernel status carries a certificate"
        )


def _validate_candidate_for_task(
    task: core.V93Task, result: V93KernelTaskResult
) -> V93KernelTaskResult:
    if result.solver_status is TaskSolverStatus.CANDIDATE_FOUND:
        candidate = result.candidate_response_time
        if not task.wcet <= candidate <= task.deadline:
            raise CertificationError(
                "kernel candidate must satisfy C_i <= R_i <= D_i"
            )
    return result


def _validate_method_kernel_certificate(
    *,
    spec: methods.V93MethodSpec,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    energy_input: TasksetAnalysisInput,
    result: V93KernelTaskResult,
) -> V93KernelTaskResult:
    if result.solver_status is not TaskSolverStatus.CANDIDATE_FOUND:
        return result
    if result.witness_h is None:
        raise CertificationError("candidate is missing witness_h")
    if spec.kernel in {methods.V93Kernel.CW, methods.V93Kernel.LOC}:
        if (
            result.processor_progress_a is not None
            or result.maximum_blocking_h is not None
            or result.witness_sequence
        ):
            raise CertificationError(
                "CW/LOC candidate carries a foreign certificate"
            )
        return result

    expected_a = core.processor_progress_v9_3(
        task,
        hp_tasks,
        result.candidate_response_time,
        energy_input.processors,
        carry_in_vector,
    )
    expected_h = result.candidate_response_time - expected_a
    if (
        result.processor_progress_a != expected_a
        or result.maximum_blocking_h != expected_h
    ):
        raise CertificationError(
            "phase candidate A/H does not match the existing core"
        )
    if spec.kernel is methods.V93Kernel.PH:
        if result.witness_sequence:
            raise CertificationError("PH candidate carries a SEQ witness")
        if not 0 <= result.witness_h <= expected_h:
            raise CertificationError("PH witness_h is outside A/H")
        return result

    import asap_block_rta_v9_3_seq as seq_core

    seq_core._validate_seq_certificate(
        w=result.candidate_response_time,
        processor_progress_a=expected_a,
        maximum_blocking_h=expected_h,
        witness_sequence=result.witness_sequence,
        witness_h=result.witness_h,
    )
    return result


def _dispatch_validated_single_task_method_v9_3(
    *,
    capability: _ValidatedDispatchCapability,
    method_spec: methods.V93MethodSpec,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    energy_input: TasksetAnalysisInput,
    timeout_seconds: Optional[float],
    budget: _V93AnalysisBudget,
) -> V93KernelTaskResult:
    """Private capability-gated path to the four existing kernels."""

    spec = methods.method_spec_v9_3(method_spec)
    try:
        _validate_dispatch_capability(
            capability,
            spec=spec,
            task=task,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            carry_in_vector=carry_in_vector,
            energy_input=energy_input,
        )
        budget.checkpoint("before_validated_kernel_entry")
        if spec.kernel in {methods.V93Kernel.CW, methods.V93Kernel.LOC}:
            window = (
                core.EnvelopeKind.COMPLETE
                if spec.kernel is methods.V93Kernel.CW
                else core.EnvelopeKind.LOCAL
            )
            raw = core.canonical_closure_search_v9_3(
                window,
                task,
                hp_tasks,
                lp_tasks,
                energy_input.processors,
                carry_in_vector,
                energy_input.e0,
                energy_input.beta,
                timeout_seconds=timeout_seconds,
                clock=budget.clock,
            )
            budget.checkpoint("after_cw_loc_kernel")
            mapped = {
                core.V93SolverStatus.CANDIDATE: (
                    TaskSolverStatus.CANDIDATE_FOUND
                ),
                core.V93SolverStatus.NO_CANDIDATE: (
                    TaskSolverStatus.NO_CANDIDATE
                ),
                core.V93SolverStatus.UNPROVEN_TIMEOUT: (
                    TaskSolverStatus.TIMEOUT
                ),
                core.V93SolverStatus.UNPROVEN_NUMERIC: (
                    TaskSolverStatus.NUMERIC_ERROR
                ),
                core.V93SolverStatus.UNPROVEN_OVERFLOW: (
                    TaskSolverStatus.NUMERIC_ERROR
                ),
            }.get(
                raw.solver_status,
                TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            )
            fields = _common_raw_fields(raw)
            found = mapped is TaskSolverStatus.CANDIDATE_FOUND
            _validate_raw_certificate_shape(found=found, fields=fields)
            result = V93KernelTaskResult(
                solver_status=mapped,
                kernel_solver_status=_status_value(raw.solver_status),
                candidate_response_time=(
                    fields["candidate_response_time"] if found else None
                ),
                closing_w=fields["closing_w"] if found else None,
                witness_h=fields["witness_h"] if found else None,
                processor_progress_a=None,
                maximum_blocking_h=None,
                witness_sequence=(),
                checked_w_count=fields["checked_w_count"],
                checked_h_count=fields["checked_h_count"],
                checked_q_count=fields["checked_q_count"],
                envelope_call_count=fields["envelope_call_count"],
                impossible_prefix_count=None,
                phase_safe_calls=None,
                flow_solver_calls=None,
                flow_feasible_count=None,
                flow_infeasible_count=None,
                z_branch_count=None,
                flow_node_count=None,
                flow_edge_count=None,
                flow_feasibility_augmentations=None,
                flow_optimality_cycle_cancellations=None,
                flow_optimality_units_augmented=None,
                failure_reason=(
                    None if found else fields["failure_reason"]
                ),
                unavailable_metrics=("cache_hit_rate",),
            )
            return _validate_candidate_for_task(task, result)

        import asap_block_rta_v9_3_ph as ph_core

        telemetry = _FlowTelemetry(ph_core)
        if spec.kernel is methods.V93Kernel.PH:
            raw = ph_core.ph_response_time_v9_3(
                target=task,
                hp_tasks=hp_tasks,
                lp_tasks=lp_tasks,
                processors=energy_input.processors,
                theta_by_name=carry_in_vector,
                e0=energy_input.e0,
                beta=energy_input.beta,
                timeout_seconds=timeout_seconds,
                clock=budget.clock,
                _flow_solver=telemetry,
            )
            budget.checkpoint("after_ph_kernel")
            mapped = {
                ph_core.PHSearchStatus.CANDIDATE: (
                    TaskSolverStatus.CANDIDATE_FOUND
                ),
                ph_core.PHSearchStatus.NO_CANDIDATE: (
                    TaskSolverStatus.NO_CANDIDATE
                ),
                ph_core.PHSearchStatus.UNPROVEN_TIMEOUT: (
                    TaskSolverStatus.TIMEOUT
                ),
                ph_core.PHSearchStatus.UNPROVEN_NUMERIC: (
                    TaskSolverStatus.NUMERIC_ERROR
                ),
                ph_core.PHSearchStatus.UNPROVEN_INTERNAL: (
                    TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
                ),
            }.get(
                raw.solver_status,
                TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
            )
            fields = _common_raw_fields(raw)
            found = mapped is TaskSolverStatus.CANDIDATE_FOUND
            _validate_raw_certificate_shape(found=found, fields=fields)
            a_value = h_max = None
            if found:
                budget.checkpoint("before_ph_certificate_material")
                a_value = core.processor_progress_v9_3(
                    task,
                    hp_tasks,
                    fields["candidate_response_time"],
                    energy_input.processors,
                    carry_in_vector,
                )
                h_max = fields["candidate_response_time"] - a_value
                if (
                    type(fields["witness_h"]) is not int
                    or not 0 <= fields["witness_h"] <= h_max
                ):
                    raise CertificationError(
                        "PH witness_h is outside its recomputed A/H bound"
                    )
                budget.checkpoint("after_ph_certificate_material")
            flow = telemetry.fields()
            result = V93KernelTaskResult(
                solver_status=mapped,
                kernel_solver_status=_status_value(raw.solver_status),
                candidate_response_time=(
                    fields["candidate_response_time"] if found else None
                ),
                closing_w=fields["closing_w"] if found else None,
                witness_h=fields["witness_h"] if found else None,
                processor_progress_a=a_value,
                maximum_blocking_h=h_max,
                witness_sequence=(),
                checked_w_count=fields["checked_w_count"],
                checked_h_count=fields["checked_h_count"],
                checked_q_count=fields["checked_q_count"],
                envelope_call_count=fields["envelope_call_count"],
                impossible_prefix_count=getattr(
                    raw, "impossible_prefix_count"
                ),
                phase_safe_calls=fields["envelope_call_count"],
                flow_solver_calls=flow["flow_solver_calls"],
                flow_feasible_count=flow["flow_feasible_count"],
                flow_infeasible_count=flow["flow_infeasible_count"],
                z_branch_count=flow["z_branch_count"],
                flow_node_count=flow["flow_node_count"],
                flow_edge_count=flow["flow_edge_count"],
                flow_feasibility_augmentations=flow[
                    "flow_feasibility_augmentations"
                ],
                flow_optimality_cycle_cancellations=flow[
                    "flow_optimality_cycle_cancellations"
                ],
                flow_optimality_units_augmented=flow[
                    "flow_optimality_units_augmented"
                ],
                failure_reason=(
                    None if found else fields["failure_reason"]
                ),
                unavailable_metrics=(
                    "ph_stage_witness",
                    "cache_hit_rate",
                ),
            )
            return _validate_candidate_for_task(task, result)

        if spec.kernel is not methods.V93Kernel.SEQ:
            raise CertificationError("unregistered mathematical kernel")
        import asap_block_rta_v9_3_seq as seq_core

        raw = seq_core.seq_response_time_v9_3(
            target=task,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            processors=energy_input.processors,
            theta_by_name=carry_in_vector,
            e0=energy_input.e0,
            beta=energy_input.beta,
            timeout_seconds=timeout_seconds,
            clock=budget.clock,
            _flow_solver=telemetry,
        )
        budget.checkpoint("after_seq_kernel")
        # Reconstruct to invoke the core's independent certificate validator
        # even if an injected or deserialized object bypassed its dataclass.
        raw = seq_core.SEQSearchResult(
            solver_status=raw.solver_status,
            candidate_response_time=raw.candidate_response_time,
            closing_w=raw.closing_w,
            processor_progress_a=raw.processor_progress_a,
            maximum_blocking_h=raw.maximum_blocking_h,
            witness_sequence=raw.witness_sequence,
            witness_h=raw.witness_h,
            checked_w_count=raw.checked_w_count,
            checked_h_count=raw.checked_h_count,
            checked_q_count=raw.checked_q_count,
            envelope_call_count=raw.envelope_call_count,
            impossible_prefix_count=raw.impossible_prefix_count,
            failure_reason=raw.failure_reason,
        )
        budget.checkpoint("after_seq_result_revalidation")
        mapped = {
            seq_core.SEQSearchStatus.CANDIDATE: (
                TaskSolverStatus.CANDIDATE_FOUND
            ),
            seq_core.SEQSearchStatus.NO_CANDIDATE: (
                TaskSolverStatus.NO_CANDIDATE
            ),
            seq_core.SEQSearchStatus.UNPROVEN_TIMEOUT: (
                TaskSolverStatus.TIMEOUT
            ),
            seq_core.SEQSearchStatus.UNPROVEN_NUMERIC: (
                TaskSolverStatus.NUMERIC_ERROR
            ),
            seq_core.SEQSearchStatus.UNPROVEN_INTERNAL: (
                TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE
            ),
        }.get(
            raw.solver_status,
            TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        )
        found = mapped is TaskSolverStatus.CANDIDATE_FOUND
        flow = telemetry.fields()
        result = V93KernelTaskResult(
            solver_status=mapped,
            kernel_solver_status=_status_value(raw.solver_status),
            candidate_response_time=(
                raw.candidate_response_time if found else None
            ),
            closing_w=raw.closing_w if found else None,
            witness_h=raw.witness_h if found else None,
            processor_progress_a=(
                raw.processor_progress_a if found else None
            ),
            maximum_blocking_h=(
                raw.maximum_blocking_h if found else None
            ),
            witness_sequence=raw.witness_sequence if found else (),
            checked_w_count=raw.checked_w_count,
            checked_h_count=raw.checked_h_count,
            checked_q_count=raw.checked_q_count,
            envelope_call_count=raw.envelope_call_count,
            impossible_prefix_count=raw.impossible_prefix_count,
            phase_safe_calls=raw.envelope_call_count,
            flow_solver_calls=flow["flow_solver_calls"],
            flow_feasible_count=flow["flow_feasible_count"],
            flow_infeasible_count=flow["flow_infeasible_count"],
            z_branch_count=flow["z_branch_count"],
            flow_node_count=flow["flow_node_count"],
            flow_edge_count=flow["flow_edge_count"],
            flow_feasibility_augmentations=flow[
                "flow_feasibility_augmentations"
            ],
            flow_optimality_cycle_cancellations=flow[
                "flow_optimality_cycle_cancellations"
            ],
            flow_optimality_units_augmented=flow[
                "flow_optimality_units_augmented"
            ],
            failure_reason=None if found else raw.failure_reason,
            unavailable_metrics=("cache_hit_rate",),
        )
        return _validate_candidate_for_task(task, result)
    except _AnalysisBudgetExpired as exc:
        return _timeout_kernel_result(
            exc.stage,
            telemetry=locals().get("telemetry"),
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return _internal_kernel_result(
            "unified kernel adapter rejected result: {}: {}".format(
                type(exc).__name__, exc
            ),
            telemetry=locals().get("telemetry"),
        )


def _revalidate_kernel_result(result: object) -> V93KernelTaskResult:
    """Reject hook objects that bypassed the frozen dataclass constructor."""

    try:
        return V93KernelTaskResult(
            solver_status=result.solver_status,
            kernel_solver_status=result.kernel_solver_status,
            candidate_response_time=result.candidate_response_time,
            closing_w=result.closing_w,
            witness_h=result.witness_h,
            processor_progress_a=result.processor_progress_a,
            maximum_blocking_h=result.maximum_blocking_h,
            witness_sequence=result.witness_sequence,
            checked_w_count=result.checked_w_count,
            checked_h_count=result.checked_h_count,
            checked_q_count=result.checked_q_count,
            envelope_call_count=result.envelope_call_count,
            impossible_prefix_count=result.impossible_prefix_count,
            phase_safe_calls=result.phase_safe_calls,
            flow_solver_calls=result.flow_solver_calls,
            flow_feasible_count=result.flow_feasible_count,
            flow_infeasible_count=result.flow_infeasible_count,
            z_branch_count=result.z_branch_count,
            flow_node_count=result.flow_node_count,
            flow_edge_count=result.flow_edge_count,
            flow_feasibility_augmentations=(
                result.flow_feasibility_augmentations
            ),
            flow_optimality_cycle_cancellations=(
                result.flow_optimality_cycle_cancellations
            ),
            flow_optimality_units_augmented=(
                result.flow_optimality_units_augmented
            ),
            failure_reason=result.failure_reason,
            unavailable_metrics=result.unavailable_metrics,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return _internal_kernel_result(
            "malformed unified kernel result: {}: {}".format(
                type(exc).__name__, exc
            )
        )


def _stable_rm_order(
    task_items: Sequence[core.V93Task],
) -> Tuple[core.V93Task, ...]:
    """Sort by period while retaining caller order for equal periods."""

    return tuple(
        task
        for _index, task in sorted(
            enumerate(task_items),
            key=lambda indexed: indexed[1].period,
        )
    )


def _validated_unified_input(
    analysis_input: TasksetAnalysisInput,
    ordered_tasks: Tuple[core.V93Task, ...],
) -> Tuple[Optional[TasksetAnalysisInput], Optional[str]]:
    """Validate every global exact input before any solver dispatch."""

    if not _interface_active(analysis_input.dependency_context):
        return None, "numeric/theory contract mismatch"
    try:
        required_horizon = max(task.deadline for task in ordered_tasks) - 1
        validated_beta = core.validate_service_curve_v9_3(
            analysis_input.beta, required_horizon
        )
        exact_e0 = core.exact_fraction_v9_3(analysis_input.e0, "E0")
        if exact_e0 < 0:
            raise core.V93NumericError("E0 must be non-negative")
        if callable(analysis_input.beta):
            identity_service_prefix = validated_beta
        else:
            identity_service_prefix = core.validate_service_curve_v9_3(
                analysis_input.beta, len(analysis_input.beta) - 1
            )
        expected_identity = exact_energy.exact_input_identity(
            task_powers=(
                (task.name, task.power) for task in ordered_tasks
            ),
            e0=exact_e0,
            service_prefix=identity_service_prefix,
        )
    except (
        core.V93NumericError,
        exact_energy.ExactEnergyError,
        ArithmeticError,
        TypeError,
        ValueError,
    ) as exc:
        return None, "invalid exact method input material: {}".format(exc)
    if (
        analysis_input.dependency_context.exact_input_identity
        != expected_identity
    ):
        return None, "exact method input identity mismatch"
    return (
        replace(
            analysis_input,
            tasks=ordered_tasks,
            e0=exact_e0,
            beta=validated_beta,
        ),
        None,
    )


def dispatch_single_task_method_v9_3(
    *,
    method_spec: methods.MethodReference,
    task: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    carry_in_vector: Mapping[str, int],
    energy_input: TasksetAnalysisInput,
    timeout_seconds: Optional[float],
    _clock: Callable[[], float] = time.monotonic,
) -> V93KernelTaskResult:
    """Validate exact identity before entering one existing kernel."""

    spec = methods.method_spec_v9_3(method_spec)
    if not isinstance(energy_input, TasksetAnalysisInput):
        return _numeric_kernel_result(
            "public dispatcher requires TasksetAnalysisInput"
        )
    try:
        budget = _V93AnalysisBudget(timeout_seconds, _clock)
    except CertificationError as exc:
        return _numeric_kernel_result(str(exc))
    try:
        budget.checkpoint("single_task_analysis_start")
        ordered_tasks = _stable_rm_order(energy_input.tasks)
        validated_input, input_failure = _validated_unified_input(
            energy_input, ordered_tasks
        )
        budget.checkpoint("single_task_identity_validated")
        if validated_input is None:
            return _numeric_kernel_result(input_failure)
        matching_ranks = tuple(
            rank
            for rank, registered_task in enumerate(ordered_tasks)
            if registered_task is task
        )
        if len(matching_ranks) != 1:
            return _numeric_kernel_result(
                "public dispatcher task is not the validated task object"
            )
        rank = matching_ranks[0]
        analysis_nonce = object()
        capability = _issue_dispatch_capability(
            analysis_nonce=analysis_nonce,
            spec=spec,
            validated_input=validated_input,
            task=task,
            priority_rank=rank,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            carry_in_vector=carry_in_vector,
        )
        budget.checkpoint("before_task_dispatch")
        remaining = budget.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise _AnalysisBudgetExpired("before_task_dispatch")
        result = _dispatch_validated_single_task_method_v9_3(
            capability=capability,
            method_spec=spec,
            task=task,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            carry_in_vector=carry_in_vector,
            energy_input=validated_input,
            timeout_seconds=remaining,
            budget=budget,
        )
        budget.checkpoint("after_task_dispatch")
        budget.checkpoint("before_candidate_publication")
        return result
    except _AnalysisBudgetExpired as exc:
        return _timeout_kernel_result(exc.stage)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return _internal_kernel_result(
            "public dispatcher rejected input: {}: {}".format(
                type(exc).__name__, exc
            )
        )


def _method_task_result(
    *,
    spec: methods.V93MethodSpec,
    task: core.V93Task,
    rank: int,
    carry: Mapping[str, int],
    result: V93KernelTaskResult,
    runtime_wall: float,
    runtime_cpu: Optional[float],
    solver_call_count: int = 1,
) -> V93MethodTaskResult:
    certification = (
        TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED
        if result.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        else TaskCertificationStatus.NOT_CERTIFIED
    )
    return V93MethodTaskResult(
        method_id=spec.method_id,
        kernel=spec.kernel,
        carry_policy=spec.carry_policy,
        task_id=task.name,
        priority_rank=rank,
        solver_status=result.solver_status,
        kernel_solver_status=result.kernel_solver_status,
        certification_status=certification,
        candidate_response_time=result.candidate_response_time,
        carry_in_values_used=tuple(carry.items()),
        closing_w=result.closing_w,
        witness_h=result.witness_h,
        processor_progress_a=result.processor_progress_a,
        maximum_blocking_h=result.maximum_blocking_h,
        witness_sequence=result.witness_sequence,
        checked_w_count=result.checked_w_count,
        checked_h_count=result.checked_h_count,
        checked_q_count=result.checked_q_count,
        envelope_call_count=result.envelope_call_count,
        solver_call_count=solver_call_count,
        impossible_prefix_count=result.impossible_prefix_count,
        phase_safe_calls=result.phase_safe_calls,
        flow_solver_calls=result.flow_solver_calls,
        flow_feasible_count=result.flow_feasible_count,
        flow_infeasible_count=result.flow_infeasible_count,
        z_branch_count=result.z_branch_count,
        flow_node_count=result.flow_node_count,
        flow_edge_count=result.flow_edge_count,
        flow_feasibility_augmentations=(
            result.flow_feasibility_augmentations
        ),
        flow_optimality_cycle_cancellations=(
            result.flow_optimality_cycle_cancellations
        ),
        flow_optimality_units_augmented=(
            result.flow_optimality_units_augmented
        ),
        runtime_wall=runtime_wall,
        runtime_cpu=runtime_cpu,
        failure_reason=result.failure_reason,
        unavailable_metrics=result.unavailable_metrics,
    )


def _not_evaluated_method_task(
    spec: methods.V93MethodSpec,
    task: core.V93Task,
    rank: int,
) -> V93MethodTaskResult:
    result = V93KernelTaskResult(
        solver_status=TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
        kernel_solver_status=(
            TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE.value
        ),
        candidate_response_time=None,
        closing_w=None,
        witness_h=None,
        processor_progress_a=None,
        maximum_blocking_h=None,
        witness_sequence=(),
        checked_w_count=0,
        checked_h_count=0,
        checked_q_count=0,
        envelope_call_count=0,
        impossible_prefix_count=None,
        phase_safe_calls=None,
        flow_solver_calls=None,
        flow_feasible_count=None,
        flow_infeasible_count=None,
        z_branch_count=None,
        flow_node_count=None,
        flow_edge_count=None,
        flow_feasibility_augmentations=None,
        flow_optimality_cycle_cancellations=None,
        flow_optimality_units_augmented=None,
        failure_reason="not evaluated after prefix failure",
        unavailable_metrics=("not_evaluated",),
    )
    return _method_task_result(
        spec=spec,
        task=task,
        rank=rank,
        carry={},
        result=result,
        runtime_wall=0.0,
        runtime_cpu=0.0,
        solver_call_count=0,
    )


def _make_unified_taskset_result(
    *,
    analysis_id: str,
    spec: methods.V93MethodSpec,
    task_results: Tuple[V93MethodTaskResult, ...],
    solver_status: AnalysisSolverStatus,
    certification_status: AnalysisCertificationStatus,
    first_failed_task: Optional[str],
    failure_reason: Optional[str],
    exact_input_identity: object,
) -> V93MethodTasksetAnalysisResult:
    identity = (
        exact_input_identity
        if isinstance(exact_input_identity, str)
        else repr(exact_input_identity)
    )
    return V93MethodTasksetAnalysisResult(
        analysis_id=analysis_id,
        method_id=spec.method_id,
        kernel=spec.kernel,
        carry_policy=spec.carry_policy,
        solver_status=solver_status,
        analysis_certification_status=certification_status,
        task_results=task_results,
        taskset_proven=(
            certification_status
            is AnalysisCertificationStatus.CERTIFIED_TASKSET
        ),
        first_failed_task=first_failed_task,
        failure_reason=failure_reason,
        carry_trace=tuple(
            V93CarryTraceEntry(
                task_result.task_id,
                task_result.priority_rank,
                task_result.carry_in_values_used,
            )
            for task_result in task_results
        ),
        exact_input_identity=identity,
        _finalizer_token=_METHOD_FINALIZER_TOKEN,
    )


def _global_unified_numeric_failure(
    *,
    analysis_id: str,
    spec: methods.V93MethodSpec,
    tasks: Tuple[core.V93Task, ...],
    exact_input_identity: object,
    reason: str,
) -> V93MethodTasksetAnalysisResult:
    first_kernel = V93KernelTaskResult(
        solver_status=TaskSolverStatus.NUMERIC_ERROR,
        kernel_solver_status="ADAPTER_INPUT_VALIDATION",
        candidate_response_time=None,
        closing_w=None,
        witness_h=None,
        processor_progress_a=None,
        maximum_blocking_h=None,
        witness_sequence=(),
        checked_w_count=0,
        checked_h_count=0,
        checked_q_count=0,
        envelope_call_count=0,
        impossible_prefix_count=None,
        phase_safe_calls=None,
        flow_solver_calls=None,
        flow_feasible_count=None,
        flow_infeasible_count=None,
        z_branch_count=None,
        flow_node_count=None,
        flow_edge_count=None,
        flow_feasibility_augmentations=None,
        flow_optimality_cycle_cancellations=None,
        flow_optimality_units_augmented=None,
        failure_reason=reason,
        unavailable_metrics=("solver_not_called",),
    )
    task_results = (
        _method_task_result(
            spec=spec,
            task=tasks[0],
            rank=0,
            carry={},
            result=first_kernel,
            runtime_wall=0.0,
            runtime_cpu=0.0,
            solver_call_count=0,
        ),
    ) + tuple(
        _not_evaluated_method_task(spec, task, rank)
        for rank, task in enumerate(tasks[1:], start=1)
    )
    return _make_unified_taskset_result(
        analysis_id=analysis_id,
        spec=spec,
        task_results=task_results,
        solver_status=AnalysisSolverStatus.NUMERIC_ERROR,
        certification_status=AnalysisCertificationStatus.NOT_CERTIFIED,
        first_failed_task=tasks[0].name,
        failure_reason=reason,
        exact_input_identity=exact_input_identity,
    )


def _global_unified_timeout_failure(
    *,
    analysis_id: str,
    spec: methods.V93MethodSpec,
    tasks: Tuple[core.V93Task, ...],
    exact_input_identity: object,
    stage: str,
    runtime_wall: float,
) -> V93MethodTasksetAnalysisResult:
    timeout_result = _timeout_kernel_result(stage)
    task_results = (
        _method_task_result(
            spec=spec,
            task=tasks[0],
            rank=0,
            carry={},
            result=timeout_result,
            runtime_wall=runtime_wall,
            runtime_cpu=0.0,
            solver_call_count=0,
        ),
    ) + tuple(
        _not_evaluated_method_task(spec, task, rank)
        for rank, task in enumerate(tasks[1:], start=1)
    )
    return _make_unified_taskset_result(
        analysis_id=analysis_id,
        spec=spec,
        task_results=task_results,
        solver_status=AnalysisSolverStatus.TIMEOUT,
        certification_status=AnalysisCertificationStatus.NOT_CERTIFIED,
        first_failed_task=tasks[0].name,
        failure_reason=timeout_result.failure_reason,
        exact_input_identity=exact_input_identity,
    )


def _replace_last_candidate_with_timeout(
    *,
    task_results: Tuple[V93MethodTaskResult, ...],
    stage: str,
) -> Tuple[V93MethodTaskResult, ...]:
    if not task_results:
        raise CertificationError(
            "timeout replacement requires at least one task result"
        )
    previous = task_results[-1]
    timeout_result = _timeout_kernel_result(stage)
    cleared = replace(
        previous,
        solver_status=timeout_result.solver_status,
        kernel_solver_status=timeout_result.kernel_solver_status,
        certification_status=TaskCertificationStatus.NOT_CERTIFIED,
        candidate_response_time=None,
        closing_w=None,
        witness_h=None,
        processor_progress_a=None,
        maximum_blocking_h=None,
        witness_sequence=(),
        checked_w_count=timeout_result.checked_w_count,
        checked_h_count=timeout_result.checked_h_count,
        checked_q_count=timeout_result.checked_q_count,
        envelope_call_count=timeout_result.envelope_call_count,
        impossible_prefix_count=timeout_result.impossible_prefix_count,
        phase_safe_calls=timeout_result.phase_safe_calls,
        flow_solver_calls=timeout_result.flow_solver_calls,
        flow_feasible_count=timeout_result.flow_feasible_count,
        flow_infeasible_count=timeout_result.flow_infeasible_count,
        z_branch_count=timeout_result.z_branch_count,
        flow_node_count=timeout_result.flow_node_count,
        flow_edge_count=timeout_result.flow_edge_count,
        flow_feasibility_augmentations=(
            timeout_result.flow_feasibility_augmentations
        ),
        flow_optimality_cycle_cancellations=(
            timeout_result.flow_optimality_cycle_cancellations
        ),
        flow_optimality_units_augmented=(
            timeout_result.flow_optimality_units_augmented
        ),
        failure_reason=timeout_result.failure_reason,
        unavailable_metrics=timeout_result.unavailable_metrics,
        _certification_token=None,
    )
    return task_results[:-1] + (cleared,)


def _finalize_unified_method_result(
    *,
    analysis_id: str,
    spec: methods.V93MethodSpec,
    tasks: Tuple[core.V93Task, ...],
    task_results: Tuple[V93MethodTaskResult, ...],
    exact_input_identity: str,
    observer: Optional[
        Callable[[str, Tuple[V93MethodTaskResult, ...]], None]
    ] = None,
) -> V93MethodTasksetAnalysisResult:
    if len(tasks) != len(task_results) or any(
        result.solver_status is not TaskSolverStatus.CANDIDATE_FOUND
        for result in task_results
    ):
        raise CertificationError(
            "unified finalizer requires one candidate per task"
        )
    recursive_candidates = {}
    for rank, (task, result) in enumerate(zip(tasks, task_results)):
        if (
            result.task_id != task.name
            or result.priority_rank != rank
            or not task.wcet
            <= result.candidate_response_time
            <= task.deadline
        ):
            raise CertificationError(
                "unified candidate vector failed task/rank/deadline validation"
            )
        hp_tasks = tasks[:rank]
        expected_carry = (
            {
                hp_task.name: hp_task.deadline
                for hp_task in hp_tasks
            }
            if spec.carry_policy is methods.V93CarryPolicy.FIXED_D
            else {
                hp_task.name: recursive_candidates[hp_task.name]
                for hp_task in hp_tasks
            }
        )
        if result.carry_in_values_used != tuple(expected_carry.items()):
            raise CertificationError(
                "unified carry trace does not match method policy"
            )
        recursive_candidates[task.name] = result.candidate_response_time
    if observer is not None:
        observer("before", task_results)
    certified = tuple(
        replace(
            result,
            certification_status=TaskCertificationStatus.CERTIFIED,
            _certification_token=_METHOD_FINALIZER_TOKEN,
        )
        for result in task_results
    )
    if observer is not None:
        observer("after", certified)
    return _make_unified_taskset_result(
        analysis_id=analysis_id,
        spec=spec,
        task_results=certified,
        solver_status=AnalysisSolverStatus.COMPLETED,
        certification_status=AnalysisCertificationStatus.CERTIFIED_TASKSET,
        first_failed_task=None,
        failure_reason=None,
        exact_input_identity=exact_input_identity,
    )


def analyze_method_taskset_v9_3(
    *,
    analysis_id: str,
    method_spec: methods.MethodReference,
    analysis_input: TasksetAnalysisInput,
    kernel_dispatcher: MethodKernelDispatcher = (
        dispatch_single_task_method_v9_3
    ),
    finalization_observer: Optional[
        Callable[[str, Tuple[V93MethodTaskResult, ...]], None]
    ] = None,
    _clock: Callable[[], float] = time.monotonic,
    _budget_checkpoint_observer: Optional[
        Callable[[str], None]
    ] = None,
) -> V93MethodTasksetAnalysisResult:
    """Analyze one task set through any of the eight canonical methods.

    The carry policy changes only the source of ``Theta``.  Every mathematical
    decision is delegated to one of the four existing kernels.  The input
    timeout is one request-wide wall-clock budget, not a per-task allowance.
    """

    if not isinstance(analysis_id, str) or not analysis_id:
        raise CertificationError("analysis_id must be non-empty")
    if not isinstance(analysis_input, TasksetAnalysisInput):
        raise CertificationError(
            "analysis_input must be a TasksetAnalysisInput"
        )
    if not callable(kernel_dispatcher):
        raise CertificationError("kernel_dispatcher must be callable")
    spec = methods.method_spec_v9_3(method_spec)
    try:
        budget = _V93AnalysisBudget(
            analysis_input.timeout_seconds,
            _clock,
            _budget_checkpoint_observer,
        )
    except CertificationError as exc:
        ordered_tasks = _stable_rm_order(analysis_input.tasks)
        return _global_unified_numeric_failure(
            analysis_id=analysis_id,
            spec=spec,
            tasks=ordered_tasks,
            exact_input_identity=(
                analysis_input.dependency_context.exact_input_identity
            ),
            reason="invalid request timeout budget: {}".format(exc),
        )
    try:
        budget.checkpoint("taskset_analysis_start")
    except _AnalysisBudgetExpired as exc:
        ordered_tasks = _stable_rm_order(analysis_input.tasks)
        return _global_unified_timeout_failure(
            analysis_id=analysis_id,
            spec=spec,
            tasks=ordered_tasks,
            exact_input_identity=(
                analysis_input.dependency_context.exact_input_identity
            ),
            stage=exc.stage,
            runtime_wall=budget.elapsed_seconds(),
        )
    ordered_tasks = _stable_rm_order(analysis_input.tasks)
    validated_input, input_failure = _validated_unified_input(
        analysis_input, ordered_tasks
    )
    try:
        budget.checkpoint("after_identity_validation")
    except _AnalysisBudgetExpired as exc:
        return _global_unified_timeout_failure(
            analysis_id=analysis_id,
            spec=spec,
            tasks=ordered_tasks,
            exact_input_identity=(
                analysis_input.dependency_context.exact_input_identity
            ),
            stage=exc.stage,
            runtime_wall=budget.elapsed_seconds(),
        )
    if validated_input is None:
        return _global_unified_numeric_failure(
            analysis_id=analysis_id,
            spec=spec,
            tasks=ordered_tasks,
            exact_input_identity=(
                analysis_input.dependency_context.exact_input_identity
            ),
            reason=input_failure,
        )

    analysis_nonce = object()
    task_results = []
    recursive_candidates = {}
    first_failed_task = None
    first_failure_reason = None
    operational_terminal = None
    stop_prefix = False
    for rank, task in enumerate(ordered_tasks):
        if stop_prefix:
            task_results.append(
                _not_evaluated_method_task(spec, task, rank)
            )
            continue
        hp_tasks = ordered_tasks[:rank]
        lp_tasks = ordered_tasks[rank + 1 :]
        carry = (
            {
                hp_task.name: hp_task.deadline
                for hp_task in hp_tasks
            }
            if spec.carry_policy is methods.V93CarryPolicy.FIXED_D
            else {
                hp_task.name: recursive_candidates[hp_task.name]
                for hp_task in hp_tasks
            }
        )
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        solver_called = False
        try:
            budget.checkpoint("before_task_dispatch")
            remaining = budget.remaining_seconds()
            if remaining is not None and remaining <= 0:
                raise _AnalysisBudgetExpired("before_task_dispatch")
            solver_called = True
            if kernel_dispatcher is dispatch_single_task_method_v9_3:
                capability = _issue_dispatch_capability(
                    analysis_nonce=analysis_nonce,
                    spec=spec,
                    validated_input=validated_input,
                    task=task,
                    priority_rank=rank,
                    hp_tasks=hp_tasks,
                    lp_tasks=lp_tasks,
                    carry_in_vector=carry,
                )
                raw_result = _dispatch_validated_single_task_method_v9_3(
                    capability=capability,
                    method_spec=spec,
                    task=task,
                    hp_tasks=hp_tasks,
                    lp_tasks=lp_tasks,
                    carry_in_vector=carry,
                    energy_input=validated_input,
                    timeout_seconds=remaining,
                    budget=budget,
                )
            else:
                raw_result = kernel_dispatcher(
                    method_spec=spec,
                    task=task,
                    hp_tasks=hp_tasks,
                    lp_tasks=lp_tasks,
                    carry_in_vector=carry,
                    energy_input=validated_input,
                    timeout_seconds=remaining,
                )
            budget.checkpoint("after_task_dispatch")
            budget.checkpoint("before_certificate_revalidation")
            kernel_result = _revalidate_kernel_result(raw_result)
            kernel_result = _validate_candidate_for_task(
                task, kernel_result
            )
            kernel_result = _validate_method_kernel_certificate(
                spec=spec,
                task=task,
                hp_tasks=hp_tasks,
                carry_in_vector=carry,
                energy_input=validated_input,
                result=kernel_result,
            )
            budget.checkpoint("after_certificate_revalidation")
            budget.checkpoint("before_candidate_publication")
        except _AnalysisBudgetExpired as exc:
            kernel_result = _timeout_kernel_result(exc.stage)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            kernel_result = _internal_kernel_result(
                "kernel dispatcher raised {}: {}".format(
                    type(exc).__name__, exc
                )
            )
        runtime_cpu = time.process_time() - cpu_started
        runtime_wall = time.perf_counter() - wall_started
        task_result = _method_task_result(
            spec=spec,
            task=task,
            rank=rank,
            carry=carry,
            result=kernel_result,
            runtime_wall=runtime_wall,
            runtime_cpu=runtime_cpu,
            solver_call_count=1 if solver_called else 0,
        )
        try:
            budget.checkpoint("after_candidate_publication")
        except _AnalysisBudgetExpired as exc:
            timeout_result = _timeout_kernel_result(exc.stage)
            runtime_cpu = time.process_time() - cpu_started
            runtime_wall = time.perf_counter() - wall_started
            task_result = _method_task_result(
                spec=spec,
                task=task,
                rank=rank,
                carry=carry,
                result=timeout_result,
                runtime_wall=runtime_wall,
                runtime_cpu=runtime_cpu,
                solver_call_count=1 if solver_called else 0,
            )
        task_results.append(task_result)
        status = task_result.solver_status
        if status is TaskSolverStatus.CANDIDATE_FOUND:
            if spec.carry_policy is methods.V93CarryPolicy.SELF_RECURSIVE:
                recursive_candidates[task.name] = (
                    task_result.candidate_response_time
                )
            continue

        if first_failed_task is None:
            first_failed_task = task.name
            first_failure_reason = task_result.failure_reason
        if status is TaskSolverStatus.NO_CANDIDATE:
            if spec.carry_policy is methods.V93CarryPolicy.SELF_RECURSIVE:
                stop_prefix = True
            continue
        operational_terminal = {
            TaskSolverStatus.TIMEOUT: AnalysisSolverStatus.TIMEOUT,
            TaskSolverStatus.NUMERIC_ERROR: AnalysisSolverStatus.NUMERIC_ERROR,
            TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE: (
                AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
            ),
        }.get(status, AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE)
        stop_prefix = True

    frozen_results = tuple(task_results)
    if all(
        result.solver_status is TaskSolverStatus.CANDIDATE_FOUND
        for result in frozen_results
    ):
        try:
            budget.checkpoint("before_taskset_certification")
            finalized = _finalize_unified_method_result(
                analysis_id=analysis_id,
                spec=spec,
                tasks=ordered_tasks,
                task_results=frozen_results,
                exact_input_identity=(
                    validated_input.dependency_context.exact_input_identity
                ),
                observer=finalization_observer,
            )
            budget.checkpoint("after_taskset_certification")
            return finalized
        except _AnalysisBudgetExpired as exc:
            timeout_results = _replace_last_candidate_with_timeout(
                task_results=frozen_results,
                stage=exc.stage,
            )
            return _make_unified_taskset_result(
                analysis_id=analysis_id,
                spec=spec,
                task_results=timeout_results,
                solver_status=AnalysisSolverStatus.TIMEOUT,
                certification_status=(
                    AnalysisCertificationStatus.NOT_CERTIFIED
                ),
                first_failed_task=timeout_results[-1].task_id,
                failure_reason=timeout_results[-1].failure_reason,
                exact_input_identity=(
                    validated_input.dependency_context.exact_input_identity
                ),
            )

    try:
        budget.checkpoint("before_taskset_result_return")
    except _AnalysisBudgetExpired as exc:
        frozen_results = _replace_last_candidate_with_timeout(
            task_results=frozen_results,
            stage=exc.stage,
        )
        operational_terminal = AnalysisSolverStatus.TIMEOUT
        if first_failed_task is None:
            first_failed_task = frozen_results[-1].task_id
            first_failure_reason = frozen_results[-1].failure_reason
    solver_status = operational_terminal or AnalysisSolverStatus.NO_CANDIDATE
    return _make_unified_taskset_result(
        analysis_id=analysis_id,
        spec=spec,
        task_results=frozen_results,
        solver_status=solver_status,
        certification_status=AnalysisCertificationStatus.NOT_CERTIFIED,
        first_failed_task=first_failed_task,
        failure_reason=first_failure_reason,
        exact_input_identity=(
            validated_input.dependency_context.exact_input_identity
        ),
    )
