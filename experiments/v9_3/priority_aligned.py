"""Optional, outcome-independent task structure; ordinary generation is unchanged.

This is an experimental population definition, not a schedulability condition.
All utilization limits below refer to sum(C/T), unless explicitly called a share.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import product
from typing import Any, Mapping, Sequence


PROFILE_VERSION = "PRIORITY_ALIGNED_TASKSETS_V2"
DEFAULT_PARAMETERS = {
    "high_group_multiplier": "2",
    "anchor_total_share": "3/10",
    "low_group_share_min": "1/5",
    "low_task_util_max": "7/20",
    "low_group_util_max": "1",
    "min_low_wcet": 2,
    "pair_total_util_tolerance": "1/10000",
    "max_attempts": 64,
}


class IneligibleTaskset(ValueError):
    """A candidate cannot meet the declared material constraints."""


def _text(value: Fraction) -> str:
    return str(value)


def profile_material(name: str = "ordinary", parameters: Mapping[str, Any] | None = None) -> dict | None:
    """Canonical profile, omitted for ordinary so old hashes/seeds stay identical."""
    if name == "ordinary":
        if parameters:
            raise ValueError("ordinary tasksets do not accept priority-aligned parameters")
        return None
    if name != "priority-aligned":
        raise ValueError("taskset profile must be ordinary or priority-aligned")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise ValueError("priority-aligned parameters must be an object")
    supplied = {} if parameters is None else dict(parameters)
    unknown = set(supplied) - set(DEFAULT_PARAMETERS)
    if unknown:
        raise ValueError("unknown priority-aligned parameters: " + ", ".join(sorted(unknown)))
    values = {**DEFAULT_PARAMETERS, **supplied}
    for key in DEFAULT_PARAMETERS:
        value = values[key]
        if key in {"min_low_wcet", "max_attempts"}:
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")
            continue
        if isinstance(value, (float, bool)):
            raise ValueError(f"{key} must be an exact rational string or integer")
        try:
            value = Fraction(value)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise ValueError(f"invalid priority-aligned parameter {key}") from exc
        if value <= 0:
            raise ValueError(f"{key} must be positive")
        if key in {"anchor_total_share", "low_group_share_min", "low_task_util_max"} and value >= 1:
            raise ValueError(f"{key} must be less than one")
        values[key] = _text(value)
    if values["min_low_wcet"] < 2:
        raise ValueError("min_low_wcet must be at least 2; one-tick low tasks are excluded")
    if Fraction(values["anchor_total_share"]) + Fraction(values["low_group_share_min"]) >= 1:
        raise ValueError("anchor and low-group shares must leave room for other high tasks")
    return {"name": name, "version": PROFILE_VERSION, "parameters": values}


def configured_profile(generation: Mapping[str, Any]) -> dict | None:
    value = generation.get("taskset_profile")
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"name", "version", "parameters"}:
        raise ValueError("taskset_profile must contain name, version and parameters")
    expected = profile_material(value["name"], value["parameters"])
    if expected != value:
        raise ValueError("taskset_profile is not canonical or has an unsupported version")
    if expected and (generation["deadline_mode"] != "implicit" or generation["priority_policy"] != "RM"):
        raise ValueError("priority-aligned tasksets currently support RM with D=T only")
    return expected


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _allocate(weights: list[Fraction], periods: list[int], lower: list[int], upper: list[int],
              target: Fraction) -> list[Fraction]:
    lo = [Fraction(c, t) for c, t in zip(lower, periods)]
    hi = [Fraction(c, t) for c, t in zip(upper, periods)]
    if not sum(lo) <= target <= sum(hi):
        raise IneligibleTaskset("group utilization does not fit WCET bounds")
    result = lo.copy()
    free = {i for i in range(len(weights)) if hi[i] > lo[i]}
    remainder = target - sum(lo)
    while remainder and free:
        weight_sum = sum((weights[i] for i in free), Fraction())
        increments = {i: remainder * weights[i] / weight_sum for i in free}
        saturated = [i for i in sorted(free) if increments[i] >= hi[i] - result[i]]
        if not saturated:
            for i in free:
                result[i] += increments[i]
            remainder = Fraction()
            break
        for i in saturated:
            remainder -= hi[i] - result[i]
            result[i] = hi[i]
            free.remove(i)
    if remainder or sum(result) != target:
        raise RuntimeError("bounded allocation failed to conserve utilization")
    return result


def transform(tasks: Sequence[Mapping[str, Any]], power_tiers: Sequence[Fraction], *,
              processors: int, min_task_util: Fraction, max_task_util: Fraction,
              target: Fraction, tolerance: Fraction, parameters: Mapping[str, Any],
              taskset_index: int) -> tuple[list[int], int]:
    """Redistribute WCETs, preserving periods/workloads and approximate total U."""
    if processors != 4 or len(tasks) != 10:
        raise ValueError("priority-aligned V2 currently requires 4 processors and 10 tasks")
    if any(t["D"] != t["T"] or int(t.get("arrival_offset", 0)) != 0 for t in tasks):
        raise ValueError("priority-aligned V2 requires synchronous D=T tasks")
    periods = [int(t["T"]) for t in tasks]
    if periods != sorted(periods) or len(power_tiers) != len(tasks):
        raise ValueError("task order/power tiers do not match canonical RM tasks")
    p = {k: Fraction(v) for k, v in parameters.items()}
    util = [Fraction(t["C"], t["T"]) for t in tasks]
    total = sum(util)
    candidates = [i for i in range(processors) if power_tiers[i] == max(power_tiers)]
    if not candidates:
        raise IneligibleTaskset("no highest-power-tier task among first four")
    anchor = max(candidates)
    if not any(power_tiers[i] < power_tiers[anchor] for i in range(processors, len(tasks))):
        raise IneligibleTaskset("no cheaper lower-priority task")
    lower = [_ceil(min_task_util * t) for t in periods]
    upper = [int(max_task_util * t) for t in periods]
    anchor_u = min(max_task_util, p["anchor_total_share"] * total)
    anchor_c = int(anchor_u * periods[anchor])
    if anchor_c < lower[anchor]:
        raise IneligibleTaskset("scaled anchor is below minimum task utilization")
    lower[anchor] = upper[anchor] = anchor_c
    for i in range(processors, len(tasks)):
        lower[i] = max(lower[i], int(p["min_low_wcet"]))
        upper[i] = min(upper[i], int(p["low_task_util_max"] * periods[i]), anchor_c - 1)
    if any(lo > hi for lo, hi in zip(lower, upper)):
        raise IneligibleTaskset("lower-priority WCET/laxity bounds are infeasible")
    high_min = sum(Fraction(lower[i], periods[i]) for i in range(processors))
    high_max = sum(Fraction(upper[i], periods[i]) for i in range(processors))
    low_min = max(p["low_group_share_min"] * total,
                  sum(Fraction(lower[i], periods[i]) for i in range(processors, len(tasks))))
    low_max = min(p["low_group_util_max"],
                  sum(Fraction(upper[i], periods[i]) for i in range(processors, len(tasks))))
    feasible_min = max(high_min, total - low_max)
    feasible_max = min(high_max, total - low_min)
    if feasible_min > feasible_max:
        raise IneligibleTaskset("group shares cannot conserve computation demand")
    high_target = min(max(p["high_group_multiplier"] * sum(util[:processors]), feasible_min), feasible_max)
    desired = [Fraction()] * len(tasks)
    desired[anchor] = Fraction(anchor_c, periods[anchor])
    for indices, group_target in (
        ([i for i in range(processors) if i != anchor], high_target - desired[anchor]),
        (list(range(processors, len(tasks))), total - high_target),
    ):
        allocation = _allocate([util[i] for i in indices], [periods[i] for i in indices],
                               [lower[i] for i in indices], [upper[i] for i in indices], group_target)
        for i, value in zip(indices, allocation):
            desired[i] = value
    options = []
    for i, value in enumerate(desired):
        ideal = value * periods[i]
        floor = int(ideal)
        options.append(sorted({max(lower[i], min(upper[i], floor)),
                               max(lower[i], min(upper[i], _ceil(ideal)))}))
    best = None
    for vector in product(*options):
        actual = sum(Fraction(c, t) for c, t in zip(vector, periods))
        low_actual = sum(Fraction(vector[i], periods[i]) for i in range(processors, len(tasks)))
        if low_actual < p["low_group_share_min"] * actual or low_actual > p["low_group_util_max"]:
            continue
        gap = actual - total
        if abs(gap) > p["pair_total_util_tolerance"] or abs(actual - target) > tolerance:
            continue
        distortion = sum(abs(Fraction(c) - u * t) for c, u, t in zip(vector, desired, periods))
        preferred_sign = int((gap >= 0) != (taskset_index % 2 == 0))
        score = (abs(gap), distortion, preferred_sign, vector)
        if best is None or score < best[0]:
            best = score, list(vector)
    if best is None:
        raise IneligibleTaskset("integer projection cannot meet utilization/share tolerances")
    return best[1], anchor


def audit(tasks: Sequence[Mapping[str, Any]], power_tiers: Sequence[Fraction], *,
          processors: int, min_task_util: Fraction, max_task_util: Fraction,
          target: Fraction, tolerance: Fraction, parameters: Mapping[str, Any],
          source_wcets: Sequence[int] | None = None) -> dict:
    """Check actual integer material, not only the continuous allocation target."""
    if processors != 4 or len(tasks) != 10 or len(power_tiers) != len(tasks):
        raise ValueError("priority-aligned V2 requires 4 cores / 10 tasks")
    periods = [t["T"] for t in tasks]
    if periods != sorted(periods) or any(t["D"] != t["T"] for t in tasks):
        raise ValueError("priority-aligned material is not RM/D=T")
    if any(type(t[k]) is not int for t in tasks for k in ("C", "D", "T")):
        raise ValueError("priority-aligned timing fields must be integers")
    if any(not 0 < t["C"] <= t["D"] or int(t.get("arrival_offset", 0)) != 0 for t in tasks):
        raise ValueError("invalid WCET/deadline/release offset")
    p = {k: Fraction(v) for k, v in parameters.items()}
    candidates = [i for i in range(processors) if power_tiers[i] == max(power_tiers)]
    if not candidates:
        raise ValueError("missing high-power anchor")
    anchor = max(candidates)
    util = [Fraction(t["C"], t["T"]) for t in tasks]
    total, low = sum(util), sum(util[processors:])
    if source_wcets is not None and (len(source_wcets) != len(tasks)
                                   or any(type(c) is not int or c < 1 for c in source_wcets)):
        raise ValueError("source WCET vector is invalid")
    source_total = total if source_wcets is None else sum(Fraction(c, t) for c, t in zip(source_wcets, periods))
    expected_c = int(min(max_task_util, p["anchor_total_share"] * source_total) * periods[anchor])
    if tasks[anchor]["C"] != expected_c:
        raise ValueError("anchor WCET does not match scaled policy")
    if any(not min_task_util <= u <= max_task_util for u in util):
        raise ValueError("task utilization outside generation bounds")
    laxity = tasks[anchor]["D"] - tasks[anchor]["C"]
    if any(t["C"] < p["min_low_wcet"] or t["C"] >= tasks[anchor]["C"]
           or t["D"] - t["C"] <= laxity or u > p["low_task_util_max"]
           for t, u in zip(tasks[processors:], util[processors:])):
        raise ValueError("lower-priority execution/laxity/utilization relation failed")
    if not p["low_group_share_min"] * total <= low <= p["low_group_util_max"]:
        raise ValueError("actual low-priority group outside declared share/budget")
    if not any(tier < power_tiers[anchor] for tier in power_tiers[processors:]):
        raise ValueError("no cheaper low-priority task")
    if abs(total - target) > tolerance or abs(total - source_total) > p["pair_total_util_tolerance"]:
        raise ValueError("actual target/pair utilization drift exceeds tolerance")
    return {"anchor_rank": anchor + 1, "anchor_wcet": tasks[anchor]["C"],
            "actual_uc": _text(total / processors), "low_group_share": _text(low / total),
            "min_low_wcet": min(t["C"] for t in tasks[processors:]),
            "source_total_utilization": _text(source_total), "actual_total_utilization": _text(total)}
