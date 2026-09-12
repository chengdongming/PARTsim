"""Exact v9.3 mathematical core for the ASAP-BLOCK response-time analysis.

This module is deliberately isolated from the frozen v20.4 and v21 analyzers.
It implements the mathematical objects and the finite closure scan from the
v9.3 theory document, but it is not wired into an experiment runner or schema.

All theorem-backed energy values are :class:`fractions.Fraction` instances.
Ordinary binary floating-point values are rejected rather than rounded.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from typing import (
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)


RTA_VERSION_V9_3 = "v9.3-exact-core"

ExactInput = Union[int, Fraction, Decimal, str]


class V93Error(Exception):
    """Base exception for the isolated v9.3 mathematical core."""


class V93InputError(V93Error, ValueError):
    """Raised when an input violates the v9.3 model preconditions."""


class V93NumericError(V93Error, ArithmeticError):
    """Raised when a value cannot enter an exact theorem-backed comparison."""


def exact_fraction_v9_3(value: ExactInput, label: str = "energy") -> Fraction:
    """Convert an exact input to ``Fraction`` without binary-float rounding.

    Integers, ``Fraction``, finite ``Decimal``, and rational/decimal strings
    are accepted.  ``float`` is intentionally rejected, including finite
    floats, so a theorem-backed caller must make its scaling choice explicit.
    """

    if isinstance(value, bool) or isinstance(value, float):
        raise V93NumericError(
            "{} must be exact; ordinary float/bool values are unsupported".format(
                label
            )
        )
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise V93NumericError("{} must be finite".format(label))
        return Fraction(value)
    if isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise V93NumericError(
                "{} is not an exact finite rational".format(label)
            ) from exc
    raise V93NumericError(
        "{} must be int, Fraction, Decimal, or an exact string".format(label)
    )


def _require_int(value: int, label: str, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise V93InputError("{} must be an integer".format(label))
    if minimum is not None and value < minimum:
        raise V93InputError("{} must be at least {}".format(label, minimum))
    return value


def _candidate_window(target: "V93Task", w: int) -> int:
    """Validate a canonical candidate window ``C_k <= w <= D_k``."""

    w = _require_int(w, "w", target.wcet)
    if w > target.deadline:
        raise V93InputError("w must satisfy C_k <= w <= D_k")
    return w


@dataclass(frozen=True)
class V93Task:
    """Restricted-deadline sequential sporadic task used by the v9.3 core."""

    name: str
    wcet: int
    deadline: int
    period: int
    power: ExactInput

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise V93InputError("task name must be a non-empty string")
        c_i = _require_int(self.wcet, "{}.wcet".format(self.name), 1)
        d_i = _require_int(self.deadline, "{}.deadline".format(self.name), 1)
        t_i = _require_int(self.period, "{}.period".format(self.name), 1)
        if not c_i <= d_i <= t_i:
            raise V93InputError(
                "{} must satisfy C <= D <= T".format(self.name)
            )
        try:
            exact_power = exact_fraction_v9_3(
                self.power, "{}.power".format(self.name)
            )
        except V93NumericError as exc:
            raise V93InputError(str(exc)) from exc
        if exact_power <= 0:
            raise V93InputError("{}.power must be positive".format(self.name))
        object.__setattr__(self, "power", exact_power)


class EnvelopeKind(Enum):
    """The two v9.3 energy-envelope workload-index variants."""

    COMPLETE = "complete"
    LOCAL = "local"


class V93SolverStatus(Enum):
    """Conservative outcomes of the canonical finite closure scan."""

    CANDIDATE = "CANDIDATE"
    NO_CANDIDATE = "NO_CANDIDATE"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_OVERFLOW = "UNPROVEN_OVERFLOW"


@dataclass(frozen=True)
class V93SearchResult:
    """Result and exact visit counters for a v9.3 ``w/h/q`` scan."""

    solver_status: V93SolverStatus
    candidate_response_time: Optional[int]
    witness_h: Optional[int]
    closing_w: Optional[int]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    failure_reason: Optional[str] = None


def workload_bound_v9_3(task: V93Task, length: int, theta: int) -> int:
    """Return the exact parameterized workload ``W_i^theta(L)``.

    The implementation is the v9.3 Section 3 formula verbatim in integer
    arithmetic.  Preconditions are ``L >= 0`` and ``C <= theta <= D <= T``.
    """

    length = _require_int(length, "L", 0)
    theta = _require_int(theta, "theta", task.wcet)
    if theta > task.deadline:
        raise V93InputError(
            "theta for {} must satisfy C <= theta <= D".format(task.name)
        )
    shifted = length + theta - task.wcet
    n_i = shifted // task.period
    edge = shifted - n_i * task.period
    return n_i * task.wcet + min(task.wcet, edge)


def deadline_workload_bound_v9_3(task: V93Task, length: int) -> int:
    """Return ``W_i^D(L)`` for a lower-priority task."""

    return workload_bound_v9_3(task, length, task.deadline)


def _validate_task_partition(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    lp_tasks: Sequence[V93Task],
) -> None:
    names = [target.name]
    names.extend(task.name for task in hp_tasks)
    names.extend(task.name for task in lp_tasks)
    if len(names) != len(set(names)):
        raise V93InputError(
            "target, hp, and lp task names must be unique and disjoint"
        )


def _validated_theta(
    hp_tasks: Sequence[V93Task], theta_by_name: Mapping[str, int]
) -> Tuple[int, ...]:
    values = []
    for task in hp_tasks:
        if task.name not in theta_by_name:
            raise V93InputError(
                "missing carry-in theta for {}".format(task.name)
            )
        theta = _require_int(
            theta_by_name[task.name],
            "theta[{}]".format(task.name),
            task.wcet,
        )
        if theta > task.deadline:
            raise V93InputError(
                "theta for {} must satisfy C <= theta <= D".format(task.name)
            )
        values.append(theta)
    return tuple(values)


def effective_hp_workloads_v9_3(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    w: int,
    theta_by_name: Mapping[str, int],
) -> Tuple[int, ...]:
    """Return every ``bar W_i,k^{P,Theta}(w)`` in hp input order."""

    w = _candidate_window(target, w)
    theta_values = _validated_theta(hp_tasks, theta_by_name)
    interference_cap = max(0, w - target.wcet + 1)
    return tuple(
        min(workload_bound_v9_3(task, w, theta), interference_cap)
        for task, theta in zip(hp_tasks, theta_values)
    )


def processor_delay_definition_scan_v9_3(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    w: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> int:
    """Compute ``D_k^{P,Theta}(w)`` by directly scanning its definition."""

    processors = _require_int(processors, "M", 1)
    bars = effective_hp_workloads_v9_3(
        target, hp_tasks, w, theta_by_name
    )
    upper = sum(bars) // processors
    maximum = 0
    for d_value in range(upper + 1):
        if (
            sum(min(value, d_value) for value in bars)
            >= processors * d_value
        ):
            maximum = d_value
    return maximum


def processor_delay_v9_3(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    w: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> int:
    """Compute the exact processor delay using a proven monotone predicate.

    ``sum_i min(bar_W_i, d) - M*d`` is a discrete concave function that is
    zero at ``d=0``.  Its non-negative integer superlevel set is therefore an
    initial interval, so binary search returns exactly the definition maximum.
    Tests compare this implementation with the direct definition scan.
    """

    processors = _require_int(processors, "M", 1)
    bars = effective_hp_workloads_v9_3(
        target, hp_tasks, w, theta_by_name
    )
    low = 0
    high = sum(bars) // processors
    while low < high:
        middle = (low + high + 1) // 2
        if (
            sum(min(value, middle) for value in bars)
            >= processors * middle
        ):
            low = middle
        else:
            high = middle - 1
    return low


def processor_progress_v9_3(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    w: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> int:
    """Return ``A_k^Theta(w) = C_k + D_k^{P,Theta}(w)``."""

    return target.wcet + processor_delay_v9_3(
        target, hp_tasks, w, processors, theta_by_name
    )


def _bounded_prefix_value(
    task_caps: Sequence[Tuple[V93Task, int]], count: int
) -> Fraction:
    """Return the value of the highest-power ``count`` bounded units."""

    count = _require_int(count, "prefix count", 0)
    available = sum(capacity for _task, capacity in task_caps)
    if count > available:
        raise V93InputError("prefix count exceeds bounded multiset size")
    remaining = count
    value = Fraction(0)
    for task, capacity in sorted(
        task_caps, key=lambda pair: (-pair[0].power, pair[0].name)
    ):
        selected = min(remaining, capacity)
        value += selected * task.power
        remaining -= selected
        if remaining == 0:
            break
    return value


def exact_energy_envelope_v9_3(
    kind: EnvelopeKind,
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    lp_tasks: Sequence[V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> Fraction:
    """Return the exact complete- or local-window v9.3 energy envelope.

    This is the specialized Section 12.1 algorithm.  It enumerates every
    legal target amount ``y_k`` and low-priority amount ``z``, while bounded
    power-sorted prefix sums exactly maximize the hp and lp unit multisets.
    """

    if not isinstance(kind, EnvelopeKind):
        raise V93InputError("kind must be an EnvelopeKind")
    _validate_task_partition(target, hp_tasks, lp_tasks)
    processors = _require_int(processors, "M", 1)
    w = _candidate_window(target, w)
    q = _require_int(q, "q", 1)
    h = _require_int(h, "h", 0)
    if q + h > w:
        raise V93InputError("the envelope requires q + h <= w")
    theta_values = _validated_theta(hp_tasks, theta_by_name)

    coverage = w if kind is EnvelopeKind.COMPLETE else q + h
    hp_caps = tuple(
        (
            task,
            min(workload_bound_v9_3(task, coverage, theta), q + h),
        )
        for task, theta in zip(hp_tasks, theta_values)
    )
    u_hp = sum(capacity for _task, capacity in hp_caps)

    best = Fraction(0)
    for y_k in range(0, min(target.wcet, q) + 1):
        lp_caps = tuple(
            (
                task,
                min(deadline_workload_bound_v9_3(task, coverage), y_k),
            )
            for task in lp_tasks
        )
        u_lp = sum(capacity for _task, capacity in lp_caps)
        c_rem = processors * (q + h) - y_k
        c_lp = (processors - 1) * y_k
        z_max = min(c_lp, c_rem, u_lp)
        for z_value in range(z_max + 1):
            hp_count = min(c_rem - z_value, u_hp)
            energy = y_k * target.power
            energy += _bounded_prefix_value(lp_caps, z_value)
            energy += _bounded_prefix_value(hp_caps, hp_count)
            if energy > best:
                best = energy
    return best


def complete_window_envelope_v9_3(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    lp_tasks: Sequence[V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> Fraction:
    """Return ``E_k^{Theta,cw}(w,q,h)`` exactly."""

    return exact_energy_envelope_v9_3(
        EnvelopeKind.COMPLETE,
        target,
        hp_tasks,
        lp_tasks,
        w,
        q,
        h,
        processors,
        theta_by_name,
    )


def local_window_envelope_v9_3(
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    lp_tasks: Sequence[V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> Fraction:
    """Return ``E_k^{Theta,loc}(w,q,h)`` exactly."""

    return exact_energy_envelope_v9_3(
        EnvelopeKind.LOCAL,
        target,
        hp_tasks,
        lp_tasks,
        w,
        q,
        h,
        processors,
        theta_by_name,
    )


EnvelopeFunction = Callable[..., ExactInput]
ServiceCurve = Union[Callable[[int], ExactInput], Sequence[ExactInput]]


def validate_service_curve_v9_3(
    beta: ServiceCurve, required_horizon: int
) -> Tuple[Fraction, ...]:
    """Validate and freeze a theorem-backed service curve prefix.

    ``required_horizon`` is the largest service index that the caller may
    inspect.  Every value is read twice, converted through the approved exact
    numeric domain, and compared across the two passes.  Returning the frozen
    tuple prevents a stateful callback from changing after validation.
    """

    required_horizon = _require_int(
        required_horizon, "service-curve required horizon", 0
    )
    is_callback = callable(beta)
    if not is_callback and (
        isinstance(beta, (str, bytes, bytearray))
        or not isinstance(beta, SequenceABC)
    ):
        raise V93NumericError(
            "service curve must be a callback or a finite exact sequence"
        )
    if not is_callback and len(beta) <= required_horizon:
        raise V93NumericError(
            "service curve is undefined through required index {}".format(
                required_horizon
            )
        )

    def read_pass() -> Tuple[Fraction, ...]:
        values = []
        for length in range(required_horizon + 1):
            try:
                raw = beta(length) if is_callback else beta[length]
            except Exception as exc:
                raise V93NumericError(
                    "service curve callback/access failed at index {}".format(
                        length
                    )
                ) from exc
            values.append(
                exact_fraction_v9_3(raw, "beta({})".format(length))
            )
        return tuple(values)

    first = read_pass()
    second = read_pass()
    if first != second:
        raise V93NumericError(
            "service curve callback is non-deterministic on the required prefix"
        )
    if first[0] != 0:
        raise V93NumericError("service curve must satisfy beta(0) == 0")
    previous = first[0]
    for length, value in enumerate(first):
        if value < 0:
            raise V93NumericError(
                "beta({}) must be non-negative".format(length)
            )
        if length and value < previous:
            raise V93NumericError(
                "service curve must be monotone non-decreasing"
            )
        previous = value
    return first


def _clock_at(clock: Callable[[], float]) -> float:
    """Return one finite operational clock reading or fail conservatively."""

    reading = clock()
    if (
        isinstance(reading, bool)
        or not isinstance(reading, (int, float))
        or not math.isfinite(reading)
    ):
        raise V93NumericError("clock must return a finite int or float")
    return float(reading)


def _result(
    status: V93SolverStatus,
    checked: Sequence[int],
    candidate: Optional[int] = None,
    witness_h: Optional[int] = None,
    reason: Optional[str] = None,
) -> V93SearchResult:
    return V93SearchResult(
        status,
        candidate,
        witness_h,
        candidate,
        checked[0],
        checked[1],
        checked[2],
        checked[3],
        reason,
    )


def canonical_closure_search_v9_3(
    kind: EnvelopeKind,
    target: V93Task,
    hp_tasks: Sequence[V93Task],
    lp_tasks: Sequence[V93Task],
    processors: int,
    theta_by_name: Mapping[str, int],
    e0: ExactInput,
    beta: ServiceCurve,
    envelope_function: EnvelopeFunction = exact_energy_envelope_v9_3,
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    trace_observer: Optional[Callable[[Mapping[str, object]], None]] = None,
) -> V93SearchResult:
    """Run the canonical v9.3 pointwise ``w``, ``h``, then ``q`` scan.

    The service index is exactly ``h + q - 1``; envelope coverage is delegated
    to the selected complete/local exact envelope and is exactly ``q + h`` for
    local windows.  Operational failures return an ``UNPROVEN_*`` status and
    never a candidate.
    """

    if not isinstance(kind, EnvelopeKind):
        raise V93InputError("kind must be an EnvelopeKind")
    if not callable(envelope_function):
        raise V93InputError("envelope_function must be callable")
    if not callable(clock):
        raise V93InputError("clock must be callable")
    if trace_observer is not None and not callable(trace_observer):
        raise V93InputError("trace_observer must be callable")
    _validate_task_partition(target, hp_tasks, lp_tasks)
    processors = _require_int(processors, "M", 1)
    _validated_theta(hp_tasks, theta_by_name)
    try:
        exact_e0 = exact_fraction_v9_3(e0, "E0")
    except V93NumericError as exc:
        raise V93InputError(str(exc)) from exc
    if exact_e0 < 0:
        raise V93InputError("E0 must be non-negative")
    if timeout_seconds is not None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise V93InputError(
                "timeout_seconds must be finite and non-negative"
            )

    checked = [0, 0, 0, 0]
    started: Optional[float] = None

    def timeout_result() -> V93SearchResult:
        return _result(
            V93SolverStatus.UNPROVEN_TIMEOUT,
            checked,
            reason="v9.3 closure search timed out",
        )

    def is_timed_out() -> bool:
        if timeout_seconds is None:
            return False
        if started is None:
            raise V93NumericError("timeout clock was not initialized")
        elapsed = _clock_at(clock) - started
        if elapsed < 0:
            raise V93NumericError("clock must be monotonic")
        return elapsed >= timeout_seconds

    try:
        validated_beta = validate_service_curve_v9_3(
            beta, target.deadline - 1
        )
        if timeout_seconds is not None:
            started = _clock_at(clock)
        for w in range(target.wcet, target.deadline + 1):
            if is_timed_out():
                return timeout_result()
            checked[0] += 1
            a_value = processor_progress_v9_3(
                target, hp_tasks, w, processors, theta_by_name
            )
            if a_value > w:
                if trace_observer is not None:
                    trace_observer(
                        {
                            "w": w,
                            "A": a_value,
                            "h": None,
                            "q": None,
                            "event_type": "W_SKIPPED_A_GT_W",
                            "envelope_value": None,
                            "service_value": None,
                            "service_index": None,
                            "coverage_index": None,
                            "q_result": "NOT_APPLICABLE",
                            "h_result": "NOT_APPLICABLE",
                            "w_result": "CONTINUE",
                        }
                    )
                continue
            for h in range(0, w - a_value + 1):
                if is_timed_out():
                    return timeout_result()
                checked[1] += 1
                h_is_valid = True
                for q in range(1, a_value + 1):
                    if is_timed_out():
                        return timeout_result()
                    checked[2] += 1
                    checked[3] += 1
                    envelope_raw = envelope_function(
                        kind=kind,
                        target=target,
                        hp_tasks=hp_tasks,
                        lp_tasks=lp_tasks,
                        w=w,
                        q=q,
                        h=h,
                        processors=processors,
                        theta_by_name=theta_by_name,
                    )
                    if is_timed_out():
                        return timeout_result()
                    envelope = exact_fraction_v9_3(
                        envelope_raw, "energy envelope"
                    )
                    if envelope < 0:
                        raise V93NumericError(
                            "energy envelope must be non-negative"
                        )
                    service = exact_e0 + validated_beta[h + q - 1]
                    if is_timed_out():
                        return timeout_result()
                    if envelope > service:
                        if trace_observer is not None:
                            trace_observer(
                                {
                                    "w": w,
                                    "A": a_value,
                                    "h": h,
                                    "q": q,
                                    "event_type": "Q_CHECK",
                                    "envelope_value": envelope,
                                    "service_value": service,
                                    "service_index": h + q - 1,
                                    "coverage_index": (
                                        w
                                        if kind is EnvelopeKind.COMPLETE
                                        else q + h
                                    ),
                                    "q_result": "FAIL",
                                    "h_result": "FAIL",
                                    "w_result": "CONTINUE",
                                }
                            )
                        h_is_valid = False
                        break
                    if trace_observer is not None:
                        closes_h = q == a_value
                        trace_observer(
                            {
                                "w": w,
                                "A": a_value,
                                "h": h,
                                "q": q,
                                "event_type": "Q_CHECK",
                                "envelope_value": envelope,
                                "service_value": service,
                                "service_index": h + q - 1,
                                "coverage_index": (
                                    w
                                    if kind is EnvelopeKind.COMPLETE
                                    else q + h
                                ),
                                "q_result": "PASS",
                                "h_result": (
                                    "CLOSED" if closes_h else "CONTINUE"
                                ),
                                "w_result": (
                                    "CANDIDATE" if closes_h else "CONTINUE"
                                ),
                            }
                        )
                if h_is_valid:
                    return _result(
                        V93SolverStatus.CANDIDATE,
                        checked,
                        candidate=w,
                        witness_h=h,
                    )
    except TimeoutError as exc:
        return _result(
            V93SolverStatus.UNPROVEN_TIMEOUT, checked, reason=str(exc)
        )
    except OverflowError as exc:
        return _result(
            V93SolverStatus.UNPROVEN_OVERFLOW, checked, reason=str(exc)
        )
    except (V93NumericError, ArithmeticError) as exc:
        return _result(
            V93SolverStatus.UNPROVEN_NUMERIC, checked, reason=str(exc)
        )

    return _result(
        V93SolverStatus.NO_CANDIDATE,
        checked,
        reason="no v9.3 closure candidate by the task deadline",
    )
