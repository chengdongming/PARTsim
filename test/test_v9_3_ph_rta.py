from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph


def task(name, c=1, d=5, t=7, p=1):
    return core.V93Task(name, c, d, t, p)


def envelope(target, hp=(), lp=(), *, w=1, q=1, h=0, m=1, theta=None, **kwargs):
    return ph.phase_energy_envelope_v9_3(
        target=target,
        hp_tasks=tuple(hp),
        lp_tasks=tuple(lp),
        w=w,
        q=q,
        h=h,
        processors=m,
        theta_by_name=theta or {item.name: item.deadline for item in hp},
        **kwargs,
    )


class ArmableClock:
    def __init__(self):
        self.armed = False

    def __call__(self):
        return 1.0 if self.armed else 0.0


def single_task_flow_result(nodes, edges, _deadline):
    """Deterministically prove z=0 infeasible and z=1 optimal."""

    g_edge = next(item for item in edges if item.key == "G->sink")
    if g_edge.lower:
        return ph._FlowResult(ph._FlowStatus.INFEASIBLE)
    return ph._FlowResult(
        ph._FlowStatus.OPTIMAL,
        tuple(item.lower for item in edges),
        0,
    )


def single_task_timeout_search(*, clock, flow_solver=single_task_flow_result):
    return ph.ph_response_time_v9_3(
        target=task("deadline-k", 1, 1, 1, Fraction(1)),
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=Fraction(1),
        beta=(Fraction(0),),
        timeout_seconds=0.5,
        clock=clock,
        _flow_solver=flow_solver,
    )


def strict_microcase():
    tasks = (
        task("t1", 1, 7, 7, 9),
        task("t2", 1, 1, 8, 5),
        task("t3", 3, 11, 12, 3),
        task("t4", 2, 11, 13, 6),
        task("t5", 2, 15, 17, 4),
        task("t6", 2, 17, 19, 7),
    )
    target = tasks[3]
    hp_tasks = tasks[:3]
    lp_tasks = tasks[4:]
    theta = {item.name: item.deadline for item in hp_tasks}
    return target, hp_tasks, lp_tasks, theta


def test_empty_hp_lp_m1_h0_z_equals_q_and_q_plus_h_one():
    target = task("k", 1, 2, 3, Fraction(3, 2))
    result = envelope(target)
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    assert result.energy == Fraction(3, 2)
    assert result.maximizing_target_exec_z == 1
    assert result.witness.hp_allocations == ()
    assert result.witness.lp_allocations == ()
    assert result.witness.target_exec_z == 1


def test_z_zero_and_multiple_feasible_z_branches():
    target = task("k", 1, 3, 4, 1)
    hp_tasks = (task("h1", p=10), task("h2", p=9))
    result = envelope(target, hp_tasks, m=2)
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    assert result.feasible_z_branches == 2
    assert result.maximizing_target_exec_z == 0
    assert sum(item.g for item in result.witness.hp_allocations) == 2


def test_g_exact_fill_and_one_unit_short_proves_impossible_prefix():
    target = task("k", 1, 4, 5, 1)
    hp_tasks = (task("h", 1, 2, 3, 3),)
    result = envelope(target, hp_tasks, w=2, q=2, m=2)
    assert result.status is ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX
    assert result.energy is None
    assert result.witness is None
    assert result.feasible_z_branches == 0


def test_s_and_e_zero_capacities_and_shared_hp_workload():
    target = task("k", 1, 3, 4, 1)
    hp_tasks = (task("h", 1, 1, 3, 4),)
    result = envelope(target, hp_tasks, w=2, q=1, h=1, m=1)
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    witness = result.witness
    assert all(item.s == 0 for item in witness.hp_allocations if witness.target_exec_z == 0)
    assert all(item.e == 0 for item in witness.hp_allocations)
    for allocation in witness.hp_allocations:
        workload = core.workload_bound_v9_3(hp_tasks[0], 2, 1)
        assert allocation.g + allocation.s + allocation.e <= workload


def test_low_priority_individual_and_aggregate_bounds_are_in_witness():
    target = task("k", 2, 4, 5, 20)
    hp_tasks = (task("h", 1, 3, 4, 1),)
    lp_tasks = (task("l1", 1, 3, 5, 9), task("l2", 1, 3, 5, 8))
    result = envelope(target, hp_tasks, lp_tasks, w=2, q=2, h=0, m=2)
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    z = result.witness.target_exec_z
    lp_sum = sum(item.ell for item in result.witness.lp_allocations)
    assert all(item.ell <= min(1, z) for item in result.witness.lp_allocations)
    assert lp_sum <= min(z, 1)
    assert sum(item.s for item in result.witness.hp_allocations) + lp_sum <= z


def test_one_feasible_z_and_multiple_optimal_z_values():
    alone = envelope(task("alone", 1, 2, 3, 1))
    assert alone.feasible_z_branches == 1
    target = task("k", 1, 3, 4, 1)
    hp_tasks = (task("h", 1, 3, 4, 1),)
    tied = envelope(target, hp_tasks)
    assert tied.status is ph.PHEnvelopeStatus.OPTIMAL
    assert tied.optimal_target_exec_z == (0, 1)


def test_fraction_and_equal_power_cost_scaling_is_exact():
    target = task("k", 1, 3, 4, Fraction(2, 3))
    hp_tasks = (
        task("h1", 1, 3, 4, Fraction(2, 3)),
        task("h2", 1, 3, 4, Fraction(2, 3)),
    )
    result = envelope(target, hp_tasks, m=2)
    assert result.energy == Fraction(4, 3)
    assert result.witness.integer_cost_scale == 3
    assert isinstance(result.energy, Fraction)


def test_timeout_numeric_failure_and_internal_failure_fail_closed(monkeypatch):
    target = task("k", 1, 2, 3, 1)
    timed = envelope(target, timeout_seconds=0)
    assert timed.status is ph.PHEnvelopeStatus.UNPROVEN_TIMEOUT
    assert timed.energy is None

    def numeric_failure(_tasks):
        raise core.V93NumericError("bad exact scale")

    monkeypatch.setattr(ph, "_power_scale", numeric_failure)
    numeric = envelope(target)
    assert numeric.status is ph.PHEnvelopeStatus.UNPROVEN_NUMERIC
    assert numeric.energy is None


def test_last_z_witness_overrun_is_timeout_not_candidate_without_sleep():
    class SixthReadExpires:
        def __init__(self):
            self.reads = 0

        def __call__(self):
            self.reads += 1
            return 0.0 if self.reads <= 5 else 1.0

    clock = SixthReadExpires()
    result = single_task_timeout_search(clock=clock)
    assert result.solver_status is ph.PHSearchStatus.UNPROVEN_TIMEOUT
    assert result.candidate_response_time is None


@pytest.mark.parametrize(
    "checkpoint",
    [
        "witness_entry",
        "witness_edge_complete",
        "witness_node_complete",
        "witness_energy_reconstructed",
        "witness_success",
        "envelope_witness_validated",
    ],
)
def test_witness_deadline_checkpoints_fail_closed(monkeypatch, checkpoint):
    clock = ArmableClock()
    original = ph._raise_if_deadline_expired
    hits = []

    def arm_at_checkpoint(deadline, observed):
        if observed == checkpoint and observed not in hits:
            hits.append(observed)
            clock.armed = True
        return original(deadline, observed)

    monkeypatch.setattr(ph, "_raise_if_deadline_expired", arm_at_checkpoint)
    result = single_task_timeout_search(clock=clock)
    assert hits == [checkpoint]
    assert result.solver_status is ph.PHSearchStatus.UNPROVEN_TIMEOUT
    assert result.candidate_response_time is None


@pytest.mark.parametrize(
    "checkpoint",
    [
        "closure_before_progress",
        "closure_after_progress",
        "closure_before_a_gt_w",
        "response_closure_returned",
        "response_before_no_candidate",
    ],
)
def test_progress_terminal_deadline_checkpoints_fail_closed(
    monkeypatch, checkpoint
):
    clock = ArmableClock()
    original_check = ph._raise_if_deadline_expired
    original_progress = core.processor_progress_v9_3
    hits = []

    def progress(*args, **kwargs):
        return 2

    def arm_at_checkpoint(deadline, observed):
        if observed == checkpoint and observed not in hits:
            hits.append(observed)
            clock.armed = True
        return original_check(deadline, observed)

    monkeypatch.setattr(core, "processor_progress_v9_3", progress)
    monkeypatch.setattr(ph, "_raise_if_deadline_expired", arm_at_checkpoint)
    try:
        result = single_task_timeout_search(clock=clock)
    finally:
        monkeypatch.setattr(core, "processor_progress_v9_3", original_progress)
    assert hits == [checkpoint]
    assert result.solver_status is ph.PHSearchStatus.UNPROVEN_TIMEOUT
    assert result.candidate_response_time is None


def test_progress_expiring_inside_call_is_timeout_before_a_gt_w(monkeypatch):
    clock = ArmableClock()

    def progress(*_args, **_kwargs):
        clock.armed = True
        return 2

    monkeypatch.setattr(core, "processor_progress_v9_3", progress)
    result = single_task_timeout_search(clock=clock)
    assert result.solver_status is ph.PHSearchStatus.UNPROVEN_TIMEOUT
    assert result.candidate_response_time is None


def test_large_hp_progress_crossing_deadline_is_timeout_without_sleep(
    monkeypatch,
):
    clock = ArmableClock()
    hp_tasks = tuple(
        task("stress-h{}".format(index), 1, 1, 1, 1)
        for index in range(20_000)
    )
    theta = {item.name: item.deadline for item in hp_tasks}
    original_workload = core.workload_bound_v9_3
    calls = 0

    def workload(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 10_000:
            clock.armed = True
        return original_workload(*args, **kwargs)

    monkeypatch.setattr(core, "workload_bound_v9_3", workload)
    result = ph.ph_response_time_v9_3(
        target=task("stress-k", 1, 1, 1, 1),
        hp_tasks=hp_tasks,
        lp_tasks=(),
        processors=1,
        theta_by_name=theta,
        e0=1,
        beta=(0,),
        timeout_seconds=0.5,
        clock=clock,
    )
    assert calls == 20_000
    assert result.solver_status is ph.PHSearchStatus.UNPROVEN_TIMEOUT
    assert result.candidate_response_time is None


def test_witness_tampering_is_detected_independently():
    target = task("k", 1, 3, 4, 1)
    hp_tasks = (task("h", 1, 3, 4, 2),)
    result = envelope(target, hp_tasks)
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    first = result.witness.edge_flows[0]
    corrupt_edge = replace(first, flow=first.upper + 1)
    corrupt = replace(
        result.witness,
        edge_flows=(corrupt_edge,) + result.witness.edge_flows[1:],
    )
    assert not ph.validate_phase_witness_v9_3(
        corrupt,
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=(),
        w=1,
        q=1,
        h=0,
        processors=1,
        theta_by_name={"h": 3},
    )


def test_production_witness_validation_failure_returns_unproven_internal(monkeypatch):
    target = task("k", 1, 2, 3, 1)
    monkeypatch.setattr(ph, "validate_phase_witness_v9_3", lambda *args, **kwargs: False)
    result = envelope(target)
    assert result.status is ph.PHEnvelopeStatus.UNPROVEN_INTERNAL
    assert result.energy is None
    assert result.failure_reason == "independent PH witness validation failed"


def test_unproven_checkpoint_aborts_search_without_trying_later_h():
    calls = []

    def unproved(nodes, edges, deadline):
        calls.append((nodes, edges))
        return ph._FlowResult(ph._FlowStatus.INTERNAL, reason="solver fault")

    target = task("k", 1, 3, 4, 1)
    result = ph.ph_response_time_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        processors=1,
        theta_by_name={},
        e0=100,
        beta=(0, 0, 0),
        _flow_solver=unproved,
    )
    assert result.solver_status is ph.PHSearchStatus.UNPROVEN_INTERNAL
    assert len(calls) == 1


def test_impossible_prefix_is_safe_without_fabricating_zero_energy():
    target = task("k", 1, 4, 5, 1)
    hp_tasks = (task("h", 1, 2, 3, 3),)
    result = ph.phase_safe_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=(),
        w=2,
        q=2,
        h=0,
        processors=2,
        theta_by_name={"h": 2},
        e0=0,
        beta=(0, 0),
    )
    assert result.status is ph.PHSafetyStatus.SAFE
    assert result.reason == "IMPOSSIBLE_PREFIX"
    assert result.envelope.energy is None
    assert result.available_energy is None


def test_invalid_numeric_input_cannot_hide_behind_impossible_prefix():
    target = task("k", 1, 4, 5, 1)
    hp_tasks = (task("h", 1, 2, 3, 3),)
    result = ph.phase_safe_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=(),
        w=2,
        q=2,
        h=0,
        processors=2,
        theta_by_name={"h": 2},
        e0=0,
        beta=(0, float("nan")),
    )
    assert result.status is ph.PHSafetyStatus.UNPROVEN_NUMERIC
    assert result.safe is None
    assert result.envelope.status is ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX


def test_beta_index_is_h_plus_q_minus_one():
    target = task("k", 1, 5, 6, 2)
    result = ph.phase_safe_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=3,
        q=1,
        h=2,
        processors=1,
        theta_by_name={},
        e0=1,
        beta=(0, 3, 100),
    )
    assert result.service_index == 2
    assert result.available_energy == 101


def test_strict_theory_microcase_a_loc_ph_and_common_h():
    target, hp_tasks, lp_tasks, theta = strict_microcase()
    assert core.processor_progress_v9_3(target, hp_tasks, 7, 3, theta) == 3
    loc = core.canonical_closure_search_v9_3(
        core.EnvelopeKind.LOCAL,
        target,
        hp_tasks,
        lp_tasks,
        3,
        theta,
        0,
        lambda length: 14 * length,
    )
    phase = ph.ph_response_time_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=3,
        theta_by_name=theta,
        e0=0,
        beta=lambda length: 14 * length,
    )
    assert loc.candidate_response_time == 8
    assert phase.solver_status is ph.PHSearchStatus.CANDIDATE
    assert phase.candidate_response_time == 7
    assert phase.closing_w == 7
    assert phase.witness_h == 4

    # Critical boundary checkpoints: PH closes at (w, h) = (7, 4), while
    # LOC still fails q=2 there and first closes at (w, h) = (8, 5).
    phase_boundary = tuple(
        ph.phase_safe_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=7,
            q=q,
            h=4,
            processors=3,
            theta_by_name=theta,
            e0=0,
            beta=lambda length: 14 * length,
        )
        for q in range(1, 4)
    )
    assert tuple(item.envelope.energy for item in phase_boundary) == (52, 69, 72)
    assert tuple(item.available_energy for item in phase_boundary) == (56, 70, 84)
    assert all(item.safe for item in phase_boundary)
    assert core.local_window_envelope_v9_3(
        target, hp_tasks, lp_tasks, 7, 2, 4, 3, theta
    ) == 72
    assert 72 > 14 * (4 + 2 - 1)
    loc_closing_boundary = tuple(
        core.local_window_envelope_v9_3(
            target, hp_tasks, lp_tasks, 8, q, 5, 3, theta
        )
        for q in range(1, 4)
    )
    assert loc_closing_boundary == (55, 75, 75)
    assert all(
        envelope_value <= service_value
        for envelope_value, service_value in zip(
            loc_closing_boundary, (70, 84, 98)
        )
    )


@pytest.mark.parametrize("q,h", [(1, 0), (1, 1), (2, 0), (2, 1), (3, 1)])
def test_nonempty_ph_envelope_is_dominated_by_loc(q, h):
    target, hp_tasks, lp_tasks, theta = strict_microcase()
    w = max(target.wcet, q + h)
    phase = envelope(
        target,
        hp_tasks,
        lp_tasks,
        w=w,
        q=q,
        h=h,
        m=3,
        theta=theta,
    )
    if phase.status is ph.PHEnvelopeStatus.OPTIMAL:
        local = core.local_window_envelope_v9_3(
            target, hp_tasks, lp_tasks, w, q, h, 3, theta
        )
        assert phase.energy <= local
    else:
        assert phase.status is ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX


def test_first_closing_w_and_fixed_theta_candidate_dominance():
    target, hp_tasks, lp_tasks, theta = strict_microcase()
    phase = ph.ph_response_time_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=lp_tasks,
        processors=3,
        theta_by_name=theta,
        e0=0,
        beta=lambda length: 14 * length,
    )
    loc = core.canonical_closure_search_v9_3(
        core.EnvelopeKind.LOCAL,
        target,
        hp_tasks,
        lp_tasks,
        3,
        theta,
        0,
        lambda length: 14 * length,
    )
    assert phase.candidate_response_time <= loc.candidate_response_time
    for earlier in range(target.wcet, phase.candidate_response_time):
        closed = ph.close_ph_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=earlier,
            processors=3,
            theta_by_name=theta,
            e0=0,
            beta=lambda length: 14 * length,
        )
        assert closed.status is ph.PHClosureStatus.NOT_CLOSED


def test_production_solver_is_flow_based_not_complete_variable_enumeration():
    source = Path(ph.__file__).read_text(encoding="utf-8")
    assert "networkx" not in source
    assert "itertools" not in source
    assert "product(" not in source
    assert "negative residual cycle" in source
