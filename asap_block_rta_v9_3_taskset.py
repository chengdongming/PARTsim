"""Task-set orchestration and joint certification for the v9.3 RTA core.

This module deliberately separates a single-task solver candidate from a
theorem-backed task-set certificate.  It does not integrate with an experiment
runner and it never writes result CSV files.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Mapping, Optional, Protocol, Sequence, Tuple

import asap_block_rta_v9_3 as core
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
