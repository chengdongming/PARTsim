"""Independent exhaustive-sequence oracle for exact SEQ-PH-LOC."""

from fractions import Fraction
from itertools import combinations_with_replacement
import random

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph
import asap_block_rta_v9_3_seq as seq
from v9_3_ph_bruteforce_oracle import brute_force_ph


ORACLE_SEED = 930401
CONTROLLED_CASES = 500
RANDOM_CASES = 500
POWER_DOMAIN = (
    Fraction(1, 3),
    Fraction(1, 2),
    Fraction(1),
    Fraction(3, 2),
    Fraction(2),
    Fraction(3),
)


def strict_instance():
    return (
        core.V93Task("strict-k", 3, 5, 8, Fraction(3, 2)),
        (core.V93Task("strict-h0", 2, 4, 5, Fraction(1, 4)),),
        (
            core.V93Task("strict-l0", 1, 4, 4, Fraction(4)),
            core.V93Task("strict-l1", 1, 1, 4, Fraction(1, 3)),
        ),
        3,
        {"strict-h0": 2},
        Fraction(9, 2),
        tuple(map(Fraction, (0, 6, 6, 9, 11))),
    )


def impossible_prefix_instance():
    target = core.V93Task("impossible-k", 1, 5, 6, 1)
    hp_tasks = (
        core.V93Task("impossible-h0", 1, 3, 5, 2),
        core.V93Task("impossible-h1", 1, 3, 3, 3),
        core.V93Task("impossible-h2", 2, 2, 3, 2),
    )
    return (
        target,
        hp_tasks,
        (),
        3,
        {
            "impossible-h0": 3,
            "impossible-h1": 1,
            "impossible-h2": 2,
        },
        Fraction(10_000),
        tuple(Fraction(0) for _ in range(target.deadline)),
    )


def controlled_instance(index):
    if index == 0:
        return strict_instance()
    if index == 1:
        return impossible_prefix_instance()
    prefix = "c{}-".format(index)
    target_c = 1 + index % 2
    target_d = target_c + (index // 2) % 3
    target = core.V93Task(
        prefix + "k",
        target_c,
        target_d,
        target_d + 1 + index % 2,
        POWER_DOMAIN[index % len(POWER_DOMAIN)],
    )
    hp_tasks = ()
    if (index // 6) % 2:
        hp_c = 1 + (index // 12) % 2
        hp_d = hp_c + (index // 24) % 2
        hp_tasks = (
            core.V93Task(
                prefix + "h",
                hp_c,
                hp_d,
                hp_d + 1 + (index // 48) % 2,
                POWER_DOMAIN[(index + 1) % len(POWER_DOMAIN)],
            ),
        )
    lp_tasks = ()
    if (index // 9) % 2:
        lp_c = 1 + (index // 18) % 2
        lp_d = lp_c + (index // 36) % 2
        lp_tasks = (
            core.V93Task(
                prefix + "l",
                lp_c,
                lp_d,
                lp_d + 1,
                POWER_DOMAIN[(index + 3) % len(POWER_DOMAIN)],
            ),
        )
    processors = 1 + (index // 5) % 2
    theta = {
        task.name: task.wcet
        + ((index // 7) % (task.deadline - task.wcet + 1))
        for task in hp_tasks
    }
    e0 = Fraction(index % 9, 1 + (index // 9) % 3)
    beta = [Fraction(0)]
    for position in range(1, target.deadline):
        beta.append(
            beta[-1]
            + Fraction((index + position) % 5, 1 + (index + position) % 3)
        )
    return target, hp_tasks, lp_tasks, processors, theta, e0, tuple(beta)


def random_instance(rng, index):
    prefix = "r{}-".format(index)
    target_c = rng.randint(1, 2)
    target_d = rng.randint(target_c, 4)
    target = core.V93Task(
        prefix + "k",
        target_c,
        target_d,
        target_d + rng.randint(0, 2),
        Fraction(rng.randint(1, 7), rng.randint(1, 3)),
    )
    hp_tasks = ()
    if rng.randint(0, 1):
        hp_c = rng.randint(1, 2)
        hp_d = rng.randint(hp_c, min(4, hp_c + 2))
        hp_tasks = (
            core.V93Task(
                prefix + "h",
                hp_c,
                hp_d,
                hp_d + rng.randint(0, 2),
                Fraction(rng.randint(1, 7), rng.randint(1, 3)),
            ),
        )
    lp_tasks = ()
    if rng.randint(0, 1):
        lp_c = rng.randint(1, 2)
        lp_d = rng.randint(lp_c, min(4, lp_c + 2))
        lp_tasks = (
            core.V93Task(
                prefix + "l",
                lp_c,
                lp_d,
                lp_d + rng.randint(0, 2),
                Fraction(rng.randint(1, 7), rng.randint(1, 3)),
            ),
        )
    processors = rng.randint(1, 2)
    theta = {
        task.name: rng.randint(task.wcet, task.deadline) for task in hp_tasks
    }
    e0 = Fraction(rng.randint(0, 8), rng.randint(1, 3))
    beta = [Fraction(0)]
    for _position in range(1, target.deadline):
        beta.append(beta[-1] + Fraction(rng.randint(0, 4), rng.randint(1, 3)))
    return target, hp_tasks, lp_tasks, processors, theta, e0, tuple(beta)


def independent_matrix(instance, w_value):
    target, hp_tasks, lp_tasks, processors, theta, e0, beta = instance
    a_value = core.processor_progress_v9_3(
        target, hp_tasks, w_value, processors, theta
    )
    h_max = w_value - a_value
    if a_value > w_value:
        return {
            "a": a_value,
            "h_max": h_max,
            "safe": {},
            "impossible": set(),
            "sequences": (),
            "greedy": (),
            "visited": (),
            "checked_q": 0,
        }
    safe = {}
    impossible = set()
    for q_value in range(1, a_value + 1):
        for h_value in range(h_max + 1):
            energy, _best_z, _witnesses = brute_force_ph(
                (
                    target,
                    hp_tasks,
                    lp_tasks,
                    w_value,
                    q_value,
                    h_value,
                    processors,
                    theta,
                )
            )
            point = (q_value, h_value)
            if energy is None:
                impossible.add(point)
                safe[point] = True
            else:
                safe[point] = energy <= e0 + beta[h_value + q_value - 1]

    feasible = tuple(
        values
        for values in combinations_with_replacement(range(h_max + 1), a_value)
        if all(safe[(q_value, values[q_value - 1])] for q_value in range(1, a_value + 1))
    )
    predecessor = 0
    greedy = []
    visited = []
    checked_q = 0
    for q_value in range(1, a_value + 1):
        checked_q += 1
        selected = None
        for h_value in range(predecessor, h_max + 1):
            visited.append((q_value, h_value))
            if safe[(q_value, h_value)]:
                selected = h_value
                greedy.append(h_value)
                predecessor = h_value
                break
        if selected is None:
            greedy = []
            break
    if feasible:
        assert greedy
        assert greedy[-1] == min(values[-1] for values in feasible)
    else:
        assert not greedy
    return {
        "a": a_value,
        "h_max": h_max,
        "safe": safe,
        "impossible": impossible,
        "sequences": feasible,
        "greedy": tuple(greedy),
        "visited": tuple(visited),
        "checked_q": checked_q,
    }


def compare_case(instance):
    target, hp_tasks, lp_tasks, processors, theta, e0, beta = instance
    expected_candidate = None
    expected_sequence = ()
    impossible_seen = 0
    checkpoint_count = 0
    for w_value in range(target.wcet, target.deadline + 1):
        expected = independent_matrix(instance, w_value)
        observed_points = []

        def logged_safety(**kwargs):
            observed_points.append((kwargs["q"], kwargs["h"]))
            return ph.phase_safe_v9_3(**kwargs)

        actual = seq.close_seq_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=w_value,
            processors=processors,
            theta_by_name=theta,
            e0=e0,
            beta=beta,
            _safety_checker=logged_safety,
        )
        expected_closed = bool(expected["sequences"])
        assert (actual.status is seq.SEQClosureStatus.CLOSED) == expected_closed
        assert actual.processor_progress_a == expected["a"]
        assert actual.maximum_blocking_h == expected["h_max"]
        assert actual.witness_sequence == expected["greedy"]
        assert actual.witness_h == (
            expected["greedy"][-1] if expected["greedy"] else None
        )
        assert tuple(observed_points) == expected["visited"]
        assert actual.checked_h_count == len(expected["visited"])
        assert actual.checked_q_count == expected["checked_q"]
        assert actual.envelope_call_count == len(expected["visited"])
        expected_impossible = sum(
            point in expected["impossible"] for point in expected["visited"]
        )
        assert actual.impossible_prefix_count == expected_impossible
        impossible_seen += expected_impossible
        checkpoint_count += len(expected["visited"])
        if expected_closed and expected_candidate is None:
            expected_candidate = w_value
            expected_sequence = expected["greedy"]

    response = seq.seq_response_time_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=processors,
        theta_by_name=theta,
        e0=e0,
        beta=beta,
    )
    if expected_candidate is None:
        assert response.solver_status is seq.SEQSearchStatus.NO_CANDIDATE
        assert response.candidate_response_time is None
        assert response.witness_sequence == ()
    else:
        assert response.solver_status is seq.SEQSearchStatus.CANDIDATE
        assert response.candidate_response_time == expected_candidate
        assert response.closing_w == expected_candidate
        assert response.witness_sequence == expected_sequence
        assert response.witness_h == expected_sequence[-1]
    return checkpoint_count, impossible_seen


def compare_cases(instances):
    checkpoints = impossible = 0
    for instance in instances:
        case_checkpoints, case_impossible = compare_case(instance)
        checkpoints += case_checkpoints
        impossible += case_impossible
    assert checkpoints > 0
    return {
        "cases": len(instances),
        "checkpoints": checkpoints,
        "impossible": impossible,
        "closure_mismatch": 0,
        "sequence_mismatch": 0,
        "candidate_mismatch": 0,
    }


def test_five_hundred_controlled_cases_match_independent_oracle():
    instances = [controlled_instance(index) for index in range(CONTROLLED_CASES)]
    assert len({repr(instance) for instance in instances}) == CONTROLLED_CASES
    summary = compare_cases(instances)
    assert summary["cases"] == CONTROLLED_CASES
    assert summary["impossible"] > 0


def test_five_hundred_fixed_seed_random_cases_match_independent_oracle():
    rng = random.Random(ORACLE_SEED)
    instances = [random_instance(rng, index) for index in range(RANDOM_CASES)]
    assert len(instances) == RANDOM_CASES
    summary = compare_cases(instances)
    assert summary["cases"] == RANDOM_CASES
