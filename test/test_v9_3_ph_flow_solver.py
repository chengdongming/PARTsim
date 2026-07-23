"""Directed adversarial tests for the PH lower-bounded circulation solver."""

from fractions import Fraction
import time

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_ph as ph


def edge(key, source, sink, lower=0, upper=1, cost=0):
    return ph._EdgeSpec(key, source, sink, lower, upper, cost)


def solve(nodes, edges, deadline=None):
    return ph._solve_min_cost_circulation(
        tuple(nodes), tuple(edges), deadline or ph._Deadline(None, time.monotonic)
    )


def test_manual_lower_bound_conversion_and_restoration():
    # lower(A->B)=1 gives balance[A]=-1 and balance[B]=+1.  Therefore the
    # auxiliary path is SS->B->A->TT; restoring the lower yields one unit on
    # each original edge and exact conservation.
    result = solve(
        ("A", "B"),
        (
            edge("lower", "A", "B", lower=1, upper=3),
            edge("repair", "B", "A", upper=1),
        ),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (1, 1)
    assert result.statistics.feasibility_augmentations == 1


def test_negative_cycle_in_component_disconnected_from_source():
    result = solve(
        ("SOURCE", "isolated", "X", "Y"),
        (edge("xy", "X", "Y", upper=2, cost=-3), edge("yx", "Y", "X", upper=2)),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (2, 2)
    assert result.minimum_cost == -6


def test_negative_cycle_that_requires_a_reverse_residual_edge():
    # Feasibility deterministically uses the first A->B edge.  Its reverse
    # residual edge then forms the improving cycle with the cheaper parallel
    # edge, moving the unit without changing the fixed B->A return flow.
    result = solve(
        ("A", "B"),
        (
            edge("expensive", "A", "B", upper=1, cost=5),
            edge("cheap", "A", "B", upper=1, cost=0),
            edge("fixed-return", "B", "A", lower=1, upper=1),
        ),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (0, 1, 1)
    assert result.minimum_cost == 0
    assert result.statistics.optimality_cycle_cancellations == 1


def test_two_independent_negative_cycles_are_both_cancelled():
    result = solve(
        ("A", "B", "C", "D"),
        (
            edge("ab", "A", "B", upper=1, cost=-2),
            edge("ba", "B", "A", upper=1),
            edge("cd", "C", "D", upper=1, cost=-3),
            edge("dc", "D", "C", upper=1),
        ),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (1, 1, 1, 1)
    assert result.minimum_cost == -5
    assert result.statistics.optimality_cycle_cancellations == 2


def test_cycle_search_restarts_after_first_cancellation_and_finds_second():
    edges = (
        edge("0", "A", "B", cost=-3),
        edge("1", "C", "A", cost=3),
        edge("2", "A", "D", cost=4),
        edge("3", "C", "B", cost=-4),
        edge("4", "D", "B", cost=1),
        edge("5", "A", "C", cost=-4),
        edge("6", "C", "D", cost=-1),
        edge("7", "B", "D", cost=-2),
        edge("8", "D", "C", cost=5),
        edge("9", "B", "A", cost=-2),
    )
    result = solve(("A", "B", "C", "D"), edges)
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.statistics.optimality_cycle_cancellations == 2
    assert result.minimum_cost == -11


def test_parallel_edges_retain_independent_capacity_and_cost():
    result = solve(
        ("A", "B"),
        (
            edge("best", "A", "B", upper=1, cost=-4),
            edge("other", "A", "B", upper=1, cost=-1),
            edge("return", "B", "A", upper=2),
        ),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (1, 1, 2)
    assert result.minimum_cost == -5


def test_zero_cost_cycle_is_not_falsely_cancelled():
    result = solve(
        ("A", "B"),
        (edge("ab", "A", "B", cost=0), edge("ba", "B", "A", cost=0)),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (0, 0)
    assert result.statistics.optimality_cycle_cancellations == 0


def test_negative_self_loop_is_seen_from_all_nodes_initialization():
    result = solve(("isolated",), (edge("loop", "isolated", "isolated", upper=2, cost=-1),))
    assert result.status is ph._FlowStatus.OPTIMAL
    assert result.flows == (2,)
    assert result.minimum_cost == -2


def test_multiple_equivalent_optimal_flows_have_exact_objective():
    result = solve(
        ("A", "B"),
        (
            edge("parallel-1", "A", "B"),
            edge("parallel-2", "A", "B"),
            edge("fixed-return", "B", "A", lower=1, upper=1),
        ),
    )
    assert result.status is ph._FlowStatus.OPTIMAL
    assert sum(result.flows[:2]) == 1
    assert result.minimum_cost == 0


def test_different_and_large_power_denominators_scale_exactly():
    target = core.V93Task("k", 1, 2, 3, Fraction(1, 97))
    hp_task = core.V93Task("h", 1, 2, 3, Fraction(1, 89))
    lp_task = core.V93Task("l", 1, 2, 3, Fraction(1, 83))
    result = ph.phase_energy_envelope_v9_3(
        target=target,
        hp_tasks=(hp_task,),
        lp_tasks=(lp_task,),
        w=1,
        q=1,
        h=0,
        processors=1,
        theta_by_name={"h": 2},
    )
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    assert result.witness.integer_cost_scale == 97 * 89 * 83
    assert result.energy == Fraction(1, 89)


def _g_case(hp_count):
    target = core.V93Task("k", 1, 3, 4, 1)
    hp_tasks = tuple(
        core.V93Task("h{}".format(index), 1, 1, 3, 2 + index)
        for index in range(hp_count)
    )
    return ph.phase_energy_envelope_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=(),
        w=2,
        q=2,
        h=0,
        processors=2,
        theta_by_name={item.name: item.deadline for item in hp_tasks},
    )


def test_g_lower_bound_exactly_feasible():
    result = _g_case(2)
    assert result.status is ph.PHEnvelopeStatus.OPTIMAL
    assert result.feasible_z_branches == 1
    assert sum(item.g for item in result.witness.hp_allocations) == 2


def test_g_lower_bound_one_unit_short_is_impossible():
    result = _g_case(1)
    assert result.status is ph.PHEnvelopeStatus.IMPOSSIBLE_PREFIX
    assert result.energy is None


def test_return_edge_can_reach_its_finite_upper_bound():
    target = core.V93Task("k", 1, 3, 4, 1)
    hp_tasks = (
        core.V93Task("h1", 1, 3, 4, 10),
        core.V93Task("h2", 1, 3, 4, 9),
    )
    result = ph.phase_energy_envelope_v9_3(
        target=target,
        hp_tasks=hp_tasks,
        lp_tasks=(),
        w=1,
        q=1,
        h=0,
        processors=2,
        theta_by_name={item.name: item.deadline for item in hp_tasks},
    )
    return_edge = next(item for item in result.witness.edge_flows if item.key == "return")
    assert return_edge.flow == return_edge.upper == 2


def test_timeout_during_bellman_ford_returns_timeout_not_an_optimum():
    readings = iter((0.0, 0.0, 1.0))
    deadline = ph._Deadline(0.5, lambda: next(readings))
    result = solve(
        ("A", "B"),
        (edge("ab", "A", "B", cost=-1), edge("ba", "B", "A")),
        deadline,
    )
    assert result.status is ph._FlowStatus.TIMEOUT
    assert result.flows == ()
    assert result.minimum_cost is None
    assert "negative-cycle search" in result.reason


def test_tampered_production_witness_returns_unproven_internal(monkeypatch):
    monkeypatch.setattr(ph, "validate_phase_witness_v9_3", lambda *args, **kwargs: False)
    target = core.V93Task("k", 1, 2, 3, 1)
    result = ph.phase_energy_envelope_v9_3(
        target=target,
        hp_tasks=(),
        lp_tasks=(),
        w=1,
        q=1,
        h=0,
        processors=1,
        theta_by_name={},
    )
    assert result.status is ph.PHEnvelopeStatus.UNPROVEN_INTERNAL
    assert result.energy is None
