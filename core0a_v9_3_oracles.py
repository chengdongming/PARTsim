"""Independent mathematical reference implementations for v9.3 CORE-0A.

The module intentionally does not import the production v9.3 core.  Callers
may pass its immutable task objects, but no production workload, truncation,
processor, envelope, cache, sorting, or prefix helper is reachable here.
"""

from __future__ import annotations

import itertools
from fractions import Fraction
from typing import Mapping, Sequence


def workload_reference(task, length: int, theta: int) -> int:
    shifted = length + theta - task.wcet
    jobs = shifted // task.period
    residual = shifted - jobs * task.period
    return jobs * task.wcet + min(task.wcet, residual)


def processor_reference(
    target,
    hp_tasks: Sequence,
    w: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> int:
    truncation = max(0, w - target.wcet + 1)
    bars = []
    for task in hp_tasks:
        raw = workload_reference(task, w, theta_by_name[task.name])
        bars.append(min(raw, truncation))
    upper = sum(bars) // processors
    valid = []
    for delay in range(upper + 1):
        lhs = sum(min(value, delay) for value in bars)
        if lhs >= processors * delay:
            valid.append(delay)
    return max(valid)


def envelope_reference(
    kind: str,
    target,
    hp_tasks: Sequence,
    lp_tasks: Sequence,
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> Fraction:
    if kind not in {"complete", "local"}:
        raise ValueError("kind must be complete or local")
    coverage = w if kind == "complete" else q + h
    hp_caps = [
        min(
            workload_reference(task, coverage, theta_by_name[task.name]),
            q + h,
        )
        for task in hp_tasks
    ]
    best = Fraction(0)
    for target_units in range(min(target.wcet, q) + 1):
        lp_caps = [
            min(
                workload_reference(task, coverage, task.deadline),
                target_units,
            )
            for task in lp_tasks
        ]
        for hp_vector in itertools.product(
            *(range(capacity + 1) for capacity in hp_caps)
        ):
            for lp_vector in itertools.product(
                *(range(capacity + 1) for capacity in lp_caps)
            ):
                if sum(lp_vector) > (processors - 1) * target_units:
                    continue
                if (
                    target_units + sum(hp_vector) + sum(lp_vector)
                    > processors * (q + h)
                ):
                    continue
                energy = target_units * target.power
                energy += sum(
                    units * task.power
                    for units, task in zip(hp_vector, hp_tasks)
                )
                energy += sum(
                    units * task.power
                    for units, task in zip(lp_vector, lp_tasks)
                )
                best = max(best, energy)
    return best
