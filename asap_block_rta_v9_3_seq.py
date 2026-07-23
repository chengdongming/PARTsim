"""Exact SEQ-PH-LOC closure for the v9.3 ASAP-BLOCK analysis.

SEQ changes only the PH closure witness: each processor-progress index may use
its own nondecreasing blocking value.  Every safety decision is delegated to
``asap_block_rta_v9_3_ph.phase_safe_v9_3`` with one shared deadline.  This
module deliberately contains no energy-envelope or flow implementation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Optional, Sequence, Tuple

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph


class SEQClosureStatus(str, Enum):
    CLOSED = "CLOSED"
    NOT_CLOSED = "NOT_CLOSED"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_INTERNAL = "UNPROVEN_INTERNAL"


class SEQSearchStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    NO_CANDIDATE = "NO_CANDIDATE"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_INTERNAL = "UNPROVEN_INTERNAL"


def _plain_int(value: object, label: str, minimum: Optional[int] = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        suffix = "" if minimum is None else " at least {}".format(minimum)
        raise core.V93InputError("{} must be a plain integer{}".format(label, suffix))
    return value


def _validate_seq_certificate(
    *,
    w: int,
    processor_progress_a: int,
    maximum_blocking_h: int,
    witness_sequence: Sequence[int],
    witness_h: Optional[int],
) -> None:
    """Validate one complete SEQ certificate without trusting its producer."""

    w_value = _plain_int(w, "certificate w", 1)
    a_value = _plain_int(
        processor_progress_a, "certificate processor_progress_a", 1
    )
    h_max = _plain_int(
        maximum_blocking_h, "certificate maximum_blocking_h", 0
    )
    if a_value + h_max != w_value:
        raise core.V93InputError("SEQ certificate must satisfy A + H == w")
    try:
        sequence_length = len(witness_sequence)
    except (TypeError, AttributeError) as exc:
        raise core.V93InputError(
            "SEQ certificate witness_sequence must be a sequence"
        ) from exc
    if sequence_length != a_value:
        raise core.V93InputError(
            "SEQ certificate must carry exactly A sequence entries"
        )
    previous = 0
    for index, value in enumerate(witness_sequence):
        _plain_int(value, "witness_sequence[{}]".format(index), 0)
        if value < previous or value > h_max:
            raise core.V93InputError(
                "SEQ witness must be nondecreasing and bounded by H"
            )
        previous = value
    _plain_int(witness_h, "witness_h", 0)
    if witness_h != witness_sequence[-1]:
        raise core.V93InputError("witness_h must equal the final sequence entry")


@dataclass(frozen=True)
class SEQClosureResult:
    status: SEQClosureStatus
    witness_sequence: Tuple[int, ...]
    witness_h: Optional[int]
    processor_progress_a: int
    maximum_blocking_h: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    impossible_prefix_count: int
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SEQClosureStatus):
            raise core.V93InputError("status must be a SEQClosureStatus")
        a_value = _plain_int(self.processor_progress_a, "processor_progress_a", 1)
        h_max = _plain_int(self.maximum_blocking_h, "maximum_blocking_h")
        for name in (
            "checked_h_count",
            "checked_q_count",
            "envelope_call_count",
            "impossible_prefix_count",
        ):
            _plain_int(getattr(self, name), name, 0)
        closed = self.status is SEQClosureStatus.CLOSED
        if closed != (len(self.witness_sequence) == a_value):
            raise core.V93InputError(
                "CLOSED must carry exactly A sequence entries"
            )
        if not closed:
            if self.witness_sequence or self.witness_h is not None:
                raise core.V93InputError(
                    "non-closed SEQ results may not carry a witness"
                )
            return
        if self.failure_reason is not None:
            raise core.V93InputError("CLOSED may not carry a failure reason")
        _validate_seq_certificate(
            w=a_value + h_max,
            processor_progress_a=a_value,
            maximum_blocking_h=h_max,
            witness_sequence=self.witness_sequence,
            witness_h=self.witness_h,
        )


@dataclass(frozen=True)
class SEQSearchResult:
    solver_status: SEQSearchStatus
    candidate_response_time: Optional[int]
    closing_w: Optional[int]
    processor_progress_a: Optional[int]
    maximum_blocking_h: Optional[int]
    witness_sequence: Tuple[int, ...]
    witness_h: Optional[int]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    impossible_prefix_count: int
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.solver_status, SEQSearchStatus):
            raise core.V93InputError("solver_status must be a SEQSearchStatus")
        for name in (
            "checked_w_count",
            "checked_h_count",
            "checked_q_count",
            "envelope_call_count",
            "impossible_prefix_count",
        ):
            _plain_int(getattr(self, name), name, 0)
        candidate = self.solver_status is SEQSearchStatus.CANDIDATE
        if candidate != (self.candidate_response_time is not None):
            raise core.V93InputError(
                "candidate presence must match SEQ search status"
            )
        if not candidate:
            if (
                self.closing_w is not None
                or self.processor_progress_a is not None
                or self.maximum_blocking_h is not None
                or self.witness_sequence
                or self.witness_h is not None
            ):
                raise core.V93InputError(
                    "non-candidate SEQ searches may not carry a witness"
                )
            return
        candidate_value = _plain_int(
            self.candidate_response_time, "candidate_response_time", 1
        )
        closing_w = _plain_int(self.closing_w, "closing_w", 1)
        if closing_w != candidate_value:
            raise core.V93InputError(
                "closing_w must equal candidate_response_time"
            )
        if self.failure_reason is not None:
            raise core.V93InputError("CANDIDATE may not carry a failure reason")
        _validate_seq_certificate(
            w=closing_w,
            processor_progress_a=self.processor_progress_a,
            maximum_blocking_h=self.maximum_blocking_h,
            witness_sequence=self.witness_sequence,
            witness_h=self.witness_h,
        )


SafetyChecker = Callable[..., ph.PHSafetyResult]


def _closure_terminal(
    status: SEQClosureStatus,
    *,
    a_value: int,
    h_max: int,
    checked_h: int,
    checked_q: int,
    envelope_calls: int,
    impossible: int,
    sequence: Tuple[int, ...] = (),
    reason: Optional[str] = None,
) -> SEQClosureResult:
    return SEQClosureResult(
        status=status,
        witness_sequence=sequence if status is SEQClosureStatus.CLOSED else (),
        witness_h=(
            sequence[-1]
            if status is SEQClosureStatus.CLOSED and sequence
            else None
        ),
        processor_progress_a=a_value,
        maximum_blocking_h=h_max,
        checked_h_count=checked_h,
        checked_q_count=checked_q,
        envelope_call_count=envelope_calls,
        impossible_prefix_count=impossible,
        failure_reason=reason,
    )


def close_seq_v9_3(
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    w: int,
    processors: int,
    theta_by_name: Mapping[str, int],
    e0: core.ExactInput,
    beta: core.ServiceCurve,
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    _deadline: Optional[ph._Deadline] = None,
    _safety_checker: SafetyChecker = ph.phase_safe_v9_3,
    _flow_solver: Callable[..., object] = ph._solve_min_cost_circulation,
) -> SEQClosureResult:
    """Exactly decide one SEQ candidate by prefix-preserving greedy search."""

    deadline = _deadline or ph._Deadline(timeout_seconds, clock)
    a_value = target.wcet
    h_max = w - a_value
    checked_h = checked_q = envelope_calls = impossible = 0
    sequence = []
    try:
        ph._raise_if_deadline_expired(deadline, "seq_closure_entry")
        exact_e0 = core.exact_fraction_v9_3(e0, "E0")
        if exact_e0 < 0:
            raise core.V93NumericError("E0 must be non-negative")
        validated_beta = core.validate_service_curve_v9_3(beta, w - 1)
        ph._raise_if_deadline_expired(
            deadline, "seq_closure_energy_inputs_validated"
        )
        ph._raise_if_deadline_expired(deadline, "seq_closure_before_progress")
        a_value = core.processor_progress_v9_3(
            target, hp_tasks, w, processors, theta_by_name
        )
        h_max = w - a_value
        ph._raise_if_deadline_expired(deadline, "seq_closure_after_progress")
        if a_value > w:
            ph._raise_if_deadline_expired(deadline, "seq_closure_before_a_gt_w")
            return _closure_terminal(
                SEQClosureStatus.NOT_CLOSED,
                a_value=a_value,
                h_max=h_max,
                checked_h=checked_h,
                checked_q=checked_q,
                envelope_calls=envelope_calls,
                impossible=impossible,
            )

        predecessor = 0
        for q_value in range(1, a_value + 1):
            ph._raise_if_deadline_expired(deadline, "seq_closure_q_entry")
            checked_q += 1
            selected = None
            for h_value in range(predecessor, h_max + 1):
                ph._raise_if_deadline_expired(deadline, "seq_closure_h_entry")
                checked_h += 1
                envelope_calls += 1
                result = _safety_checker(
                    target=target,
                    hp_tasks=hp_tasks,
                    lp_tasks=lp_tasks,
                    w=w,
                    q=q_value,
                    h=h_value,
                    processors=processors,
                    theta_by_name=theta_by_name,
                    e0=exact_e0,
                    beta=validated_beta,
                    clock=clock,
                    _deadline=deadline,
                    _flow_solver=_flow_solver,
                )
                ph._raise_if_deadline_expired(
                    deadline, "seq_closure_safety_returned"
                )
                if result.reason == ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX.value:
                    impossible += 1
                if result.status is ph.PHSafetyStatus.UNSAFE:
                    continue
                if result.status is not ph.PHSafetyStatus.SAFE:
                    mapped = {
                        ph.PHSafetyStatus.UNPROVEN_TIMEOUT: (
                            SEQClosureStatus.UNPROVEN_TIMEOUT
                        ),
                        ph.PHSafetyStatus.UNPROVEN_NUMERIC: (
                            SEQClosureStatus.UNPROVEN_NUMERIC
                        ),
                        ph.PHSafetyStatus.UNPROVEN_INTERNAL: (
                            SEQClosureStatus.UNPROVEN_INTERNAL
                        ),
                    }[result.status]
                    return _closure_terminal(
                        mapped,
                        a_value=a_value,
                        h_max=h_max,
                        checked_h=checked_h,
                        checked_q=checked_q,
                        envelope_calls=envelope_calls,
                        impossible=impossible,
                        reason=result.reason,
                    )
                ph._raise_if_deadline_expired(
                    deadline, "seq_closure_before_select"
                )
                selected = h_value
                sequence.append(h_value)
                predecessor = h_value
                ph._raise_if_deadline_expired(
                    deadline, "seq_closure_after_predecessor_update"
                )
                break
            if selected is None:
                ph._raise_if_deadline_expired(
                    deadline, "seq_closure_before_no_safe_h"
                )
                return _closure_terminal(
                    SEQClosureStatus.NOT_CLOSED,
                    a_value=a_value,
                    h_max=h_max,
                    checked_h=checked_h,
                    checked_q=checked_q,
                    envelope_calls=envelope_calls,
                    impossible=impossible,
                )

        ph._raise_if_deadline_expired(deadline, "seq_closure_before_closed")
        return _closure_terminal(
            SEQClosureStatus.CLOSED,
            a_value=a_value,
            h_max=h_max,
            checked_h=checked_h,
            checked_q=checked_q,
            envelope_calls=envelope_calls,
            impossible=impossible,
            sequence=tuple(sequence),
        )
    except ph._PHDeadlineExpired as exc:
        return _closure_terminal(
            SEQClosureStatus.UNPROVEN_TIMEOUT,
            a_value=a_value,
            h_max=h_max,
            checked_h=checked_h,
            checked_q=checked_q,
            envelope_calls=envelope_calls,
            impossible=impossible,
            reason=str(exc),
        )
    except (core.V93NumericError, OverflowError, ArithmeticError) as exc:
        try:
            ph._raise_if_deadline_expired(
                deadline, "seq_closure_numeric_failure"
            )
        except ph._PHDeadlineExpired as timeout_exc:
            status = SEQClosureStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = SEQClosureStatus.UNPROVEN_NUMERIC
            reason = str(exc)
        return _closure_terminal(
            status,
            a_value=a_value,
            h_max=h_max,
            checked_h=checked_h,
            checked_q=checked_q,
            envelope_calls=envelope_calls,
            impossible=impossible,
            reason=reason,
        )
    except core.V93InputError:
        raise
    except Exception as exc:
        try:
            ph._raise_if_deadline_expired(
                deadline, "seq_closure_internal_failure"
            )
        except ph._PHDeadlineExpired as timeout_exc:
            status = SEQClosureStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = SEQClosureStatus.UNPROVEN_INTERNAL
            reason = str(exc)
        return _closure_terminal(
            status,
            a_value=a_value,
            h_max=h_max,
            checked_h=checked_h,
            checked_q=checked_q,
            envelope_calls=envelope_calls,
            impossible=impossible,
            reason=reason,
        )


def _search_terminal(
    status: SEQSearchStatus,
    *,
    checked_w: int,
    checked_h: int,
    checked_q: int,
    envelope_calls: int,
    impossible: int,
    candidate: Optional[int] = None,
    a_value: Optional[int] = None,
    h_max: Optional[int] = None,
    sequence: Tuple[int, ...] = (),
    reason: Optional[str] = None,
) -> SEQSearchResult:
    success = status is SEQSearchStatus.CANDIDATE
    return SEQSearchResult(
        solver_status=status,
        candidate_response_time=candidate if success else None,
        closing_w=candidate if success else None,
        processor_progress_a=a_value if success else None,
        maximum_blocking_h=h_max if success else None,
        witness_sequence=sequence if success else (),
        witness_h=sequence[-1] if success and sequence else None,
        checked_w_count=checked_w,
        checked_h_count=checked_h,
        checked_q_count=checked_q,
        envelope_call_count=envelope_calls,
        impossible_prefix_count=impossible,
        failure_reason=reason,
    )


def seq_response_time_v9_3(
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    processors: int,
    theta_by_name: Mapping[str, int],
    e0: core.ExactInput,
    beta: core.ServiceCurve,
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    _safety_checker: SafetyChecker = ph.phase_safe_v9_3,
    _flow_solver: Callable[..., object] = ph._solve_min_cost_circulation,
    _closure_checker: Callable[..., SEQClosureResult] = close_seq_v9_3,
) -> SEQSearchResult:
    """Scan every integer ``w`` and return the first exact SEQ closure."""

    checked_w = checked_h = checked_q = envelope_calls = impossible = 0
    deadline = None
    try:
        deadline = ph._Deadline(timeout_seconds, clock)
        ph._raise_if_deadline_expired(deadline, "seq_response_entry")
        validated_beta = core.validate_service_curve_v9_3(
            beta, target.deadline - 1
        )
        exact_e0 = core.exact_fraction_v9_3(e0, "E0")
        if exact_e0 < 0:
            raise core.V93NumericError("E0 must be non-negative")
        ph._raise_if_deadline_expired(
            deadline, "seq_response_energy_inputs_validated"
        )
        for w_value in range(target.wcet, target.deadline + 1):
            ph._raise_if_deadline_expired(deadline, "seq_response_w_entry")
            checked_w += 1
            closure = _closure_checker(
                target=target,
                hp_tasks=hp_tasks,
                lp_tasks=lp_tasks,
                w=w_value,
                processors=processors,
                theta_by_name=theta_by_name,
                e0=exact_e0,
                beta=validated_beta,
                clock=clock,
                _deadline=deadline,
                _safety_checker=_safety_checker,
                _flow_solver=_flow_solver,
            )
            ph._raise_if_deadline_expired(
                deadline, "seq_response_closure_returned"
            )
            checked_h += closure.checked_h_count
            checked_q += closure.checked_q_count
            envelope_calls += closure.envelope_call_count
            impossible += closure.impossible_prefix_count
            if closure.status is SEQClosureStatus.CLOSED:
                ph._raise_if_deadline_expired(
                    deadline, "seq_response_before_rebinding_progress"
                )
                expected_a = core.processor_progress_v9_3(
                    target,
                    hp_tasks,
                    w_value,
                    processors,
                    theta_by_name,
                )
                ph._raise_if_deadline_expired(
                    deadline, "seq_response_after_rebinding_progress"
                )
                expected_h = w_value - expected_a
                ph._raise_if_deadline_expired(
                    deadline, "seq_response_before_certificate_compare"
                )
                try:
                    if closure.failure_reason is not None:
                        raise core.V93InputError(
                            "CLOSED closure carries a failure reason"
                        )
                    if closure.processor_progress_a != expected_a:
                        raise core.V93InputError(
                            "closure A does not match recomputed A"
                        )
                    if closure.maximum_blocking_h != expected_h:
                        raise core.V93InputError(
                            "closure H does not match w - recomputed A"
                        )
                    _validate_seq_certificate(
                        w=w_value,
                        processor_progress_a=expected_a,
                        maximum_blocking_h=expected_h,
                        witness_sequence=closure.witness_sequence,
                        witness_h=closure.witness_h,
                    )
                except ph._PHDeadlineExpired:
                    raise
                except Exception as exc:
                    ph._raise_if_deadline_expired(
                        deadline, "seq_response_certificate_mismatch"
                    )
                    return _search_terminal(
                        SEQSearchStatus.UNPROVEN_INTERNAL,
                        checked_w=checked_w,
                        checked_h=checked_h,
                        checked_q=checked_q,
                        envelope_calls=envelope_calls,
                        impossible=impossible,
                        reason="closure certificate mismatch: {}".format(exc),
                    )
                ph._raise_if_deadline_expired(
                    deadline, "seq_response_before_candidate"
                )
                candidate_result = _search_terminal(
                    SEQSearchStatus.CANDIDATE,
                    checked_w=checked_w,
                    checked_h=checked_h,
                    checked_q=checked_q,
                    envelope_calls=envelope_calls,
                    impossible=impossible,
                    candidate=w_value,
                    a_value=expected_a,
                    h_max=expected_h,
                    sequence=closure.witness_sequence,
                )
                ph._raise_if_deadline_expired(
                    deadline, "seq_response_candidate_constructed"
                )
                return candidate_result
            if closure.status is not SEQClosureStatus.NOT_CLOSED:
                mapped = {
                    SEQClosureStatus.UNPROVEN_TIMEOUT: (
                        SEQSearchStatus.UNPROVEN_TIMEOUT
                    ),
                    SEQClosureStatus.UNPROVEN_NUMERIC: (
                        SEQSearchStatus.UNPROVEN_NUMERIC
                    ),
                    SEQClosureStatus.UNPROVEN_INTERNAL: (
                        SEQSearchStatus.UNPROVEN_INTERNAL
                    ),
                }[closure.status]
                return _search_terminal(
                    mapped,
                    checked_w=checked_w,
                    checked_h=checked_h,
                    checked_q=checked_q,
                    envelope_calls=envelope_calls,
                    impossible=impossible,
                    reason=closure.failure_reason,
                )
            ph._raise_if_deadline_expired(
                deadline, "seq_response_w_not_closed"
            )
        ph._raise_if_deadline_expired(
            deadline, "seq_response_before_no_candidate"
        )
        return _search_terminal(
            SEQSearchStatus.NO_CANDIDATE,
            checked_w=checked_w,
            checked_h=checked_h,
            checked_q=checked_q,
            envelope_calls=envelope_calls,
            impossible=impossible,
            reason="no SEQ closure candidate by the task deadline",
        )
    except ph._PHDeadlineExpired as exc:
        status = SEQSearchStatus.UNPROVEN_TIMEOUT
        reason = str(exc)
    except (core.V93NumericError, OverflowError, ArithmeticError) as exc:
        try:
            ph._raise_if_deadline_expired(
                deadline, "seq_response_numeric_failure"
            )
        except ph._PHDeadlineExpired as timeout_exc:
            status = SEQSearchStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = SEQSearchStatus.UNPROVEN_NUMERIC
            reason = str(exc)
    except core.V93InputError:
        raise
    except Exception as exc:
        try:
            ph._raise_if_deadline_expired(
                deadline, "seq_response_internal_failure"
            )
        except ph._PHDeadlineExpired as timeout_exc:
            status = SEQSearchStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = SEQSearchStatus.UNPROVEN_INTERNAL
            reason = str(exc)
    return _search_terminal(
        status,
        checked_w=checked_w,
        checked_h=checked_h,
        checked_q=checked_q,
        envelope_calls=envelope_calls,
        impossible=impossible,
        reason=reason,
    )
