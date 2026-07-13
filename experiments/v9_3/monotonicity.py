"""Empirical, paired monotonicity checks for v9.3 sensitivity runs.

These checks are implementation-consistency diagnostics over finite samples;
they are deliberately not presented as mathematical proofs.
"""

from __future__ import annotations

from enum import Enum
from fractions import Fraction
from typing import Any, Dict, Mapping, Sequence


class MonotonicityStatus(str, Enum):
    HOLDS = "MONOTONICITY_HOLDS"
    EQUAL = "EQUAL"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    TIMEOUT_CENSORED = "TIMEOUT_CENSORED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VIOLATION = "MONOTONICITY_VIOLATION"


def _truth(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def service_curve_relation(
    left: Sequence[Any], right: Sequence[Any]
) -> str:
    """Return the exact pointwise relation of two validated curve prefixes."""

    if len(left) != len(right) or not left:
        return "NOT_COMPARABLE"
    lhs = tuple(Fraction(value) for value in left)
    rhs = tuple(Fraction(value) for value in right)
    if lhs == rhs:
        return "EQUAL"
    if all(r >= l for l, r in zip(lhs, rhs)):
        return "RIGHT_STRONGER"
    if all(l >= r for l, r in zip(lhs, rhs)):
        return "LEFT_STRONGER"
    return "NOT_COMPARABLE"


def _candidate_map(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    result = {}
    for row in rows:
        if str(row.get("task_solver_status")) != "CANDIDATE_FOUND":
            continue
        value = row.get("candidate_response_time")
        if value not in (None, ""):
            result[str(row["task_id"])] = int(value)
    return result


def compare_paired_analyses(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    left_tasks: Sequence[Mapping[str, Any]],
    right_tasks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
) -> Dict[str, Any]:
    """Compare one ordered pair.

    ``RESOURCE_INCREASE`` covers higher E0 and a pointwise stronger service.
    ``COST_INCREASE`` covers a higher exact power scale. ``LOC_DOMINANCE``
    checks the declared local-vs-complete direction on common candidates.
    """

    if direction not in {"RESOURCE_INCREASE", "COST_INCREASE", "LOC_DOMINANCE"}:
        raise ValueError(f"unknown monotonicity direction: {direction}")
    statuses = {str(left.get("solver_status")), str(right.get("solver_status"))}
    if "DEPENDENCY_UNAVAILABLE" in statuses:
        return _result(MonotonicityStatus.DEPENDENCY_UNAVAILABLE)
    if "TIMEOUT" in statuses or _truth(left.get("outer_timeout")) or _truth(right.get("outer_timeout")):
        return _result(MonotonicityStatus.TIMEOUT_CENSORED)
    if str(left.get("taskset_hash")) != str(right.get("taskset_hash")):
        return _result(MonotonicityStatus.NOT_COMPARABLE, reason="base taskset hash mismatch")

    lhs, rhs = _candidate_map(left_tasks), _candidate_map(right_tasks)
    left_proven, right_proven = _truth(left.get("taskset_proven")), _truth(right.get("taskset_proven"))
    violations = []
    strict = []
    if direction in {"RESOURCE_INCREASE", "LOC_DOMINANCE"}:
        if left_proven and not right_proven:
            violations.append("certified_to_not_certified")
        elif not left_proven and right_proven:
            strict.append("certification_gain")
        for task_id, candidate in lhs.items():
            if task_id not in rhs:
                violations.append(f"candidate_disappeared:{task_id}")
            elif rhs[task_id] > candidate:
                violations.append(f"candidate_increased:{task_id}")
            elif rhs[task_id] < candidate:
                strict.append(f"candidate_decreased:{task_id}")
        strict.extend(f"candidate_appeared:{task_id}" for task_id in set(rhs) - set(lhs))
    else:
        if not left_proven and right_proven:
            violations.append("not_certified_to_certified")
        elif left_proven and not right_proven:
            strict.append("certification_loss")
        for task_id, candidate in rhs.items():
            if task_id not in lhs:
                violations.append(f"candidate_appeared:{task_id}")
            elif candidate < lhs[task_id]:
                violations.append(f"candidate_decreased:{task_id}")
            elif candidate > lhs[task_id]:
                strict.append(f"candidate_increased:{task_id}")
        strict.extend(f"candidate_disappeared:{task_id}" for task_id in set(lhs) - set(rhs))

    common = sorted(
        set(lhs) & set(rhs),
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    )
    tighter = sum(rhs[key] < lhs[key] for key in common)
    equal = sum(rhs[key] == lhs[key] for key in common)
    looser = sum(rhs[key] > lhs[key] for key in common)
    if violations:
        status = MonotonicityStatus.VIOLATION
    elif strict:
        status = MonotonicityStatus.HOLDS
    elif lhs == rhs and left_proven == right_proven:
        status = MonotonicityStatus.EQUAL
    else:
        status = MonotonicityStatus.NOT_COMPARABLE
    return _result(
        status,
        common_candidate_count=len(common),
        tighter_count=tighter,
        equal_count=equal,
        looser_count=looser,
        certification_gain=int(not left_proven and right_proven),
        certification_loss=int(left_proven and not right_proven),
        violation_reasons=";".join(violations),
        strict_reasons=";".join(strict),
    )


def _result(status: MonotonicityStatus, **values: Any) -> Dict[str, Any]:
    return {
        "monotonicity_status": status.value,
        "common_candidate_count": 0,
        "tighter_count": 0,
        "equal_count": 0,
        "looser_count": 0,
        "certification_gain": 0,
        "certification_loss": 0,
        "violation_reasons": "",
        "strict_reasons": "",
        **values,
    }
