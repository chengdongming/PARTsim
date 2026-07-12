"""Independent direct-vector brute-force oracles for v9.3 tests only.

This module intentionally does not import or call the specialized exact
envelope implementation.  It constructs every integer execution vector and
checks every mathematical constraint directly.
"""

import itertools
from fractions import Fraction

from asap_block_rta_v9_3 import EnvelopeKind


def _workload(task, length, theta):
    shifted = length + theta - task.wcet
    jobs = shifted // task.period
    return jobs * task.wcet + min(
        task.wcet, shifted - jobs * task.period
    )


def brute_force_envelope(
    kind,
    target,
    hp_tasks,
    lp_tasks,
    w,
    q,
    h,
    processors,
    theta_by_name,
):
    """Enumerate all ``(y_k, y_hp..., y_lp...)`` vectors directly."""

    coverage = w if kind is EnvelopeKind.COMPLETE else q + h
    hp_caps = [
        min(_workload(task, coverage, theta_by_name[task.name]), q + h)
        for task in hp_tasks
    ]
    best = Fraction(0)
    for y_k in range(min(target.wcet, q) + 1):
        lp_caps = [
            min(_workload(task, coverage, task.deadline), y_k)
            for task in lp_tasks
        ]
        hp_vectors = itertools.product(
            *(range(capacity + 1) for capacity in hp_caps)
        )
        for hp_vector in hp_vectors:
            lp_vectors = itertools.product(
                *(range(capacity + 1) for capacity in lp_caps)
            )
            for lp_vector in lp_vectors:
                if sum(lp_vector) > (processors - 1) * y_k:
                    continue
                if (
                    y_k + sum(hp_vector) + sum(lp_vector)
                    > processors * (q + h)
                ):
                    continue
                energy = y_k * target.power
                energy += sum(
                    amount * task.power
                    for amount, task in zip(hp_vector, hp_tasks)
                )
                energy += sum(
                    amount * task.power
                    for amount, task in zip(lp_vector, lp_tasks)
                )
                if energy > best:
                    best = energy
    return best


def brute_force_complete_envelope(*args, **kwargs):
    """Direct-vector oracle for the complete-window definition."""

    return brute_force_envelope(EnvelopeKind.COMPLETE, *args, **kwargs)


def brute_force_local_envelope(*args, **kwargs):
    """Direct-vector oracle for the local-window definition."""

    return brute_force_envelope(EnvelopeKind.LOCAL, *args, **kwargs)
