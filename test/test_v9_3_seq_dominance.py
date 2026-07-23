"""Deterministic SEQ <= PH <= LOC <= CW dominance evidence."""

from fractions import Fraction

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph
import asap_block_rta_v9_3_seq as seq
import asap_block_rta_v9_3_taskset as taskset
from test_v9_3_ph_dominance import _context
from experiments.v9_3 import exact_energy


CHECKPOINT_COMPARISONS = 240
FIXED_THETA_COMPARISONS = 100
RECURSIVE_TASKSET_COMPARISONS = 30


def fixed_theta_system(index):
    prefix = "seq-f{}-".format(index)
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


def test_every_one_of_240_ph_closing_checkpoints_also_closes_seq():
    compared = violations = excluded = 0
    for index in range(CHECKPOINT_COMPARISONS):
        target, hp_tasks, lp_tasks, theta = fixed_theta_system(index)
        phase_response = ph.ph_response_time_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            processors=3,
            theta_by_name=theta,
            e0=10_000,
            beta=tuple(Fraction(0) for _ in range(target.deadline)),
        )
        assert phase_response.solver_status is ph.PHSearchStatus.CANDIDATE
        w_value = phase_response.candidate_response_time
        phase_closure = ph.close_ph_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=w_value,
            processors=3,
            theta_by_name=theta,
            e0=10_000,
            beta=tuple(Fraction(0) for _ in range(target.deadline)),
        )
        seq_closure = seq.close_seq_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=w_value,
            processors=3,
            theta_by_name=theta,
            e0=10_000,
            beta=tuple(Fraction(0) for _ in range(target.deadline)),
        )
        assert phase_closure.status is ph.PHClosureStatus.CLOSED
        if seq_closure.status is not seq.SEQClosureStatus.CLOSED:
            violations += 1
        assert seq_closure.status is seq.SEQClosureStatus.CLOSED
        assert len(seq_closure.witness_sequence) == seq_closure.processor_progress_a
        compared += 1
    assert compared == CHECKPOINT_COMPARISONS
    assert violations == 0
    assert excluded == 0


def test_fixed_theta_seq_response_dominates_all_100_ph_responses():
    compared = violations = excluded = 0
    for index in range(FIXED_THETA_COMPARISONS):
        target, hp_tasks, lp_tasks, theta = fixed_theta_system(index)
        beta = tuple(Fraction(0) for _ in range(target.deadline))
        phase = ph.ph_response_time_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            processors=3,
            theta_by_name=theta,
            e0=10_000,
            beta=beta,
        )
        sequence = seq.seq_response_time_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            processors=3,
            theta_by_name=theta,
            e0=10_000,
            beta=beta,
        )
        assert phase.solver_status is ph.PHSearchStatus.CANDIDATE
        assert sequence.solver_status is seq.SEQSearchStatus.CANDIDATE
        if sequence.candidate_response_time > phase.candidate_response_time:
            violations += 1
        assert sequence.candidate_response_time <= phase.candidate_response_time
        compared += 1
    assert compared == FIXED_THETA_COMPARISONS
    assert violations == 0
    assert excluded == 0


def recursive_input(index):
    prefix = "seq-r{}-".format(index)
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
        dependency_context=_context("seq-{}".format(index), identity),
    )


def test_thirty_recursive_vectors_obey_full_four_version_chain():
    compared = seq_ph_violations = full_chain_violations = excluded = 0
    for index in range(RECURSIVE_TASKSET_COMPARISONS):
        analysis_input = recursive_input(index)
        results = {
            "cw": taskset.analyze_taskset_v9_3(
                "seq-cw-{}".format(index),
                taskset.AnalysisVariant.CW_THETA_CW,
                analysis_input,
            ),
            "loc": taskset.analyze_taskset_v9_3(
                "seq-loc-{}".format(index),
                taskset.AnalysisVariant.LOC_THETA_LOC,
                analysis_input,
            ),
            "ph": taskset.analyze_taskset_v9_3(
                "seq-ph-{}".format(index),
                taskset.AnalysisVariant.PH_THETA_PH,
                analysis_input,
            ),
            "seq": taskset.analyze_taskset_v9_3(
                "seq-seq-{}".format(index),
                taskset.AnalysisVariant.SEQ_THETA_SEQ,
                analysis_input,
            ),
        }
        assert all(result.taskset_proven for result in results.values())
        vectors = {
            name: tuple(
                record.candidate_response_time for record in result.task_records
            )
            for name, result in results.items()
        }
        for values in zip(
            vectors["seq"], vectors["ph"], vectors["loc"], vectors["cw"]
        ):
            if values[0] > values[1]:
                seq_ph_violations += 1
            if not values[0] <= values[1] <= values[2] <= values[3]:
                full_chain_violations += 1
            assert values[0] <= values[1] <= values[2] <= values[3]
        compared += 1
    assert compared == RECURSIVE_TASKSET_COMPARISONS
    assert seq_ph_violations == 0
    assert full_chain_violations == 0
    assert excluded == 0
