"""Deterministic PH <= LOC dominance stress tests for the frozen v9.3 API."""

from fractions import Fraction

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph
import asap_block_rta_v9_3_taskset as taskset
from experiments.v9_3 import exact_energy


CHECKPOINT_COMPARISONS = 240
FIXED_THETA_COMPARISONS = 100
RECURSIVE_TASKSET_COMPARISONS = 30


def _point(index):
    prefix = "p{}-".format(index)
    target_c = 1 + index % 2
    target = core.V93Task(
        prefix + "k",
        target_c,
        8,
        10,
        Fraction(1 + index % 7, 1 + index % 3),
    )
    hp_tasks = (
        core.V93Task(prefix + "h0", 1, 3, 4, Fraction(2 + index % 3, 2)),
        core.V93Task(prefix + "h1", 1, 4, 5, Fraction(3 + index % 5, 3)),
    )
    lp_tasks = (
        core.V93Task(prefix + "l0", 1, 5, 7, Fraction(4 + index % 4, 3)),
    )
    processors = 1 + index % 3
    w = target_c + 2 + (index // 2) % 3
    q = target_c
    h = (index // 6) % (w - q + 1)
    theta = {item.name: item.deadline for item in hp_tasks}
    return target, hp_tasks, lp_tasks, processors, w, q, h, theta


def test_nonempty_checkpoint_dominance_uses_every_generated_checkpoint():
    compared = 0
    for index in range(CHECKPOINT_COMPARISONS):
        target, hp_tasks, lp_tasks, processors, w, q, h, theta = _point(index)
        phase = ph.phase_energy_envelope_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=w,
            q=q,
            h=h,
            processors=processors,
            theta_by_name=theta,
        )
        # z=q is feasible by construction, so no point is filtered out.
        assert phase.status is ph.PHEnvelopeStatus.OPTIMAL
        local = core.local_window_envelope_v9_3(
            target, hp_tasks, lp_tasks, w, q, h, processors, theta
        )
        assert phase.energy <= local
        compared += 1
    assert compared == CHECKPOINT_COMPARISONS


def _fixed_theta_system(index):
    prefix = "f{}-".format(index)
    target = core.V93Task(
        prefix + "k",
        1 + index % 2,
        6 + index % 3,
        10 + index % 3,
        Fraction(1 + index % 5, 1 + index % 3),
    )
    hp_tasks = (
        core.V93Task(prefix + "h0", 1, 3, 5, Fraction(2 + index % 3, 2)),
        core.V93Task(prefix + "h1", 1, 4, 7, Fraction(3 + index % 4, 3)),
    )
    lp_tasks = (
        core.V93Task(prefix + "l0", 1, 5, 8, Fraction(4 + index % 2, 3)),
    )
    return target, hp_tasks, lp_tasks, {
        item.name: item.deadline for item in hp_tasks
    }


def test_fixed_theta_candidate_dominance_on_one_hundred_systems():
    compared = 0
    for index in range(FIXED_THETA_COMPARISONS):
        target, hp_tasks, lp_tasks, theta = _fixed_theta_system(index)
        local = core.canonical_closure_search_v9_3(
            core.EnvelopeKind.LOCAL,
            target,
            hp_tasks,
            lp_tasks,
            3,
            theta,
            10_000,
            lambda _length: 0,
        )
        phase = ph.ph_response_time_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            processors=3,
            theta_by_name=theta,
            e0=10_000,
            beta=lambda _length: 0,
        )
        assert local.solver_status is core.V93SolverStatus.CANDIDATE
        assert phase.solver_status is ph.PHSearchStatus.CANDIDATE
        assert phase.candidate_response_time <= local.candidate_response_time
        compared += 1
    assert compared == FIXED_THETA_COMPARISONS


def _context(index, exact_input_identity):
    tag = str(index)
    return taskset.DependencyContext(
        taskset_identity="dominance-taskset-" + tag,
        task_definitions_identity="dominance-definitions-" + tag,
        priority_order_identity="dominance-priority-" + tag,
        e0_canonical_identity="dominance-e0-" + tag,
        service_curve_identity="dominance-service-" + tag,
        power_vector_identity="dominance-power-" + tag,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        formal_contract_identity="dominance-formal-" + tag,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=exact_input_identity,
        float_decision_path=False,
    )


def _recursive_input(index):
    prefix = "r{}-".format(index)
    tasks = (
        core.V93Task(prefix + "t0", 1, 3, 4, Fraction(1 + index % 3, 2)),
        core.V93Task(prefix + "t1", 1, 4, 5, Fraction(2 + index % 4, 3)),
        core.V93Task(prefix + "t2", 1, 5, 6, Fraction(3 + index % 5, 4)),
    )
    beta = tuple(Fraction(0) for _ in range(max(item.deadline for item in tasks)))
    identity = exact_energy.exact_input_identity(
        task_powers=((item.name, item.power) for item in tasks),
        e0=Fraction(10_000),
        service_prefix=beta,
    )
    return taskset.TasksetAnalysisInput(
        tasks=tasks,
        processors=3,
        e0=10_000,
        beta=beta,
        dependency_context=_context(index, identity),
    )


def test_recursive_ph_candidate_vectors_dominate_thirty_recursive_loc_vectors():
    compared = 0
    for index in range(RECURSIVE_TASKSET_COMPARISONS):
        analysis_input = _recursive_input(index)
        local = taskset.analyze_taskset_v9_3(
            "recursive-loc-{}".format(index),
            taskset.AnalysisVariant.LOC_THETA_LOC,
            analysis_input,
        )
        phase = taskset.analyze_taskset_v9_3(
            "recursive-ph-{}".format(index),
            taskset.AnalysisVariant.PH_THETA_PH,
            analysis_input,
        )
        assert local.taskset_proven and phase.taskset_proven
        local_vector = {
            record.task_id: record.candidate_response_time
            for record in local.task_records
        }
        phase_vector = {
            record.task_id: record.candidate_response_time
            for record in phase.task_records
        }
        assert phase_vector.keys() == local_vector.keys()
        assert all(
            phase_vector[task_id] <= local_vector[task_id]
            for task_id in phase_vector
        )
        compared += 1
    assert compared == RECURSIVE_TASKSET_COMPARISONS
