"""Independent complete-variable oracle for the exact PH envelope."""

from fractions import Fraction
from itertools import product
import random

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph


ORACLE_SEED = 930314
CONTROLLED_INSTANCE_COUNT = 500
RANDOM_INSTANCE_COUNT = 500
POWER_DOMAIN = (Fraction(1), Fraction(2), Fraction(3), Fraction(1, 2), Fraction(3, 2))


def brute_force_ph(instance):
    """Enumerate z, every g/s/e, and every ell without production helpers."""

    target, hp_tasks, lp_tasks, w, q, h, processors, theta = instance
    length = q + h
    best = None
    best_z = set()
    feasible_witnesses = []
    for z in range(min(target.wcet, q) + 1):
        hp_domains = []
        for task in hp_tasks:
            workload = core.workload_bound_v9_3(
                task, length, theta[task.name]
            )
            domain = tuple(
                (g, s, e)
                for g in range(q - z + 1)
                for s in range(z + 1)
                for e in range(h + 1)
                if g + s + e <= workload
            )
            hp_domains.append(domain)
        lp_domains = []
        for task in lp_tasks:
            upper = min(
                core.deadline_workload_bound_v9_3(task, max(0, length - 1)),
                max(0, length - 1),
                z,
            )
            lp_domains.append(tuple(range(upper + 1)))
        hp_vectors = product(*hp_domains) if hp_domains else ((),)
        for hp_vector in hp_vectors:
            if sum(item[0] for item in hp_vector) != processors * (q - z):
                continue
            if sum(item[2] for item in hp_vector) > (processors - 1) * h:
                continue
            lp_vectors = product(*lp_domains) if lp_domains else ((),)
            for lp_vector in lp_vectors:
                if sum(lp_vector) > (processors - 1) * min(z, max(0, length - 1)):
                    continue
                if sum(item[1] for item in hp_vector) + sum(lp_vector) > (processors - 1) * z:
                    continue
                energy = z * target.power
                energy += sum(
                    (g + s + e) * task.power
                    for task, (g, s, e) in zip(hp_tasks, hp_vector)
                )
                energy += sum(
                    ell * task.power
                    for task, ell in zip(lp_tasks, lp_vector)
                )
                feasible_witnesses.append((z, hp_vector, lp_vector, energy))
                if best is None or energy > best:
                    best = energy
                    best_z = {z}
                elif energy == best:
                    best_z.add(z)
    return best, tuple(sorted(best_z)), tuple(feasible_witnesses)


def describe(instance):
    target, hp_tasks, lp_tasks, w, q, h, processors, theta = instance
    return {
        "target": target,
        "hp": hp_tasks,
        "lp": lp_tasks,
        "w": w,
        "q": q,
        "h": h,
        "M": processors,
        "theta": theta,
    }


def random_instance(rng, index):
    processors = rng.randint(1, 3)
    q = rng.randint(1, 3)
    h = rng.randint(0, 2)
    target_c = rng.randint(1, min(3, q + 1))
    target = core.V93Task(
        "k{}".format(index), target_c, q + h + 2, q + h + 3, Fraction(rng.randint(1, 9), rng.randint(1, 4))
    )
    hp = []
    for task_index in range(rng.randint(0, 2)):
        c_value = rng.randint(1, 2)
        deadline = rng.randint(c_value, c_value + 3)
        hp.append(
            core.V93Task(
                "h{}_{}".format(index, task_index),
                c_value,
                deadline,
                deadline + rng.randint(0, 2),
                Fraction(rng.randint(1, 9), rng.randint(1, 4)),
            )
        )
    lp = []
    for task_index in range(rng.randint(0, 2)):
        c_value = rng.randint(1, 2)
        deadline = rng.randint(c_value, c_value + 3)
        lp.append(
            core.V93Task(
                "l{}_{}".format(index, task_index),
                c_value,
                deadline,
                deadline + rng.randint(0, 2),
                Fraction(rng.randint(1, 9), rng.randint(1, 4)),
            )
        )
    theta = {task.name: rng.randint(task.wcet, task.deadline) for task in hp}
    return (
        target,
        tuple(hp),
        tuple(lp),
        max(target.wcet, q + h),
        q,
        h,
        processors,
        theta,
    )


def controlled_instance(index):
    """Map each index to one distinct point in the frozen small domain."""

    processors = 1 + index % 3
    hp_count = (index // 3) % 3
    lp_count = (index // 9) % 2
    q = 1 + (index // 18) % 3
    h = (index // 54) % 3
    target_c = 1 + (index // 162) % 3
    target_power = POWER_DOMAIN[(index // 7) % len(POWER_DOMAIN)]
    w = max(target_c, q + h)
    target = core.V93Task(
        "controlled-k{}".format(index), target_c, w + 2, w + 3, target_power
    )
    hp = []
    for task_index in range(hp_count):
        c_value = 1 + ((index + task_index) % 2)
        deadline = c_value + ((index // (task_index + 2)) % 3)
        hp.append(
            core.V93Task(
                "controlled-h{}_{}".format(index, task_index),
                c_value,
                deadline,
                deadline + 1 + ((index + task_index) % 2),
                POWER_DOMAIN[(index + task_index + 1) % len(POWER_DOMAIN)],
            )
        )
    lp = []
    for task_index in range(lp_count):
        c_value = 1 + ((index + task_index + 1) % 2)
        deadline = c_value + ((index // 5 + task_index) % 3)
        lp.append(
            core.V93Task(
                "controlled-l{}_{}".format(index, task_index),
                c_value,
                deadline,
                deadline + 1,
                POWER_DOMAIN[(index + task_index + 3) % len(POWER_DOMAIN)],
            )
        )
    theta = {
        item.name: item.wcet
        + ((index + task_index) % (item.deadline - item.wcet + 1))
        for task_index, item in enumerate(hp)
    }
    return target, tuple(hp), tuple(lp), w, q, h, processors, theta


def compare_instances(instances):
    optimal_count = impossible_count = energy_count = witness_count = 0
    for instance in instances:
        expected_energy, expected_z, witnesses = brute_force_ph(instance)
        target, hp_tasks, lp_tasks, w, q, h, processors, theta = instance
        actual = ph.phase_energy_envelope_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=w,
            q=q,
            h=h,
            processors=processors,
            theta_by_name=theta,
        )
        message = "reproducible instance: {!r}".format(describe(instance))
        if expected_energy is None:
            impossible_count += 1
            assert actual.status is ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX, message
            assert actual.energy is None, message
            assert actual.witness is None, message
        else:
            optimal_count += 1
            assert actual.status is ph.PHEnvelopeStatus.OPTIMAL, message
            assert actual.energy == expected_energy, message
            energy_count += 1
            assert actual.optimal_target_exec_z == expected_z, message
            assert actual.witness is not None, message
            assert any(
                witness[0] == actual.witness.target_exec_z
                and witness[3] == actual.witness.energy
                for witness in witnesses
            ), message
            assert ph.validate_phase_witness_v9_3(
                actual.witness,
                target=target,
                hp_tasks=hp_tasks,
                lp_tasks=lp_tasks,
                w=w,
                q=q,
                h=h,
                processors=processors,
                theta_by_name=theta,
            ), message
            witness_count += 1
    assert optimal_count > 0
    assert impossible_count > 0
    assert energy_count == optimal_count
    assert witness_count == optimal_count
    return {
        "total": len(instances),
        "optimal": optimal_count,
        "impossible": impossible_count,
        "energy": energy_count,
        "witness": witness_count,
        "mismatch": 0,
    }


def test_controlled_small_domain_oracle_matches_production_exactly():
    instances = [controlled_instance(index) for index in range(CONTROLLED_INSTANCE_COUNT)]
    assert len({repr(describe(instance)) for instance in instances}) == len(instances)
    summary = compare_instances(instances)
    assert summary["total"] == CONTROLLED_INSTANCE_COUNT


def test_fixed_seed_oracle_matches_production_exactly():
    rng = random.Random(ORACLE_SEED)
    instances = [random_instance(rng, index) for index in range(RANDOM_INSTANCE_COUNT)]
    summary = compare_instances(instances)
    assert summary["total"] == RANDOM_INSTANCE_COUNT
