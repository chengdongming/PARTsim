"""Exact PH-LOC core for the v9.3 ASAP-BLOCK response-time analysis.

The module is intentionally separate from the CW/LOC core.  A fixed PH
branch is solved as a finite, integer, lower-bounded maximum-cost
circulation.  Feasibility is established by an integral max-flow reduction;
optimality is established by cancelling residual negative-cost cycles until
none remains.  Energy values never enter binary floating-point arithmetic.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import asap_block_rta_v9_3 as core


class PHEnvelopeStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    IMPOSSIBLE_PREFIX = "IMPOSSIBLE_PREFIX"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_INTERNAL = "UNPROVEN_INTERNAL"


class PHSafetyStatus(str, Enum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_INTERNAL = "UNPROVEN_INTERNAL"


class PHClosureStatus(str, Enum):
    CLOSED = "CLOSED"
    NOT_CLOSED = "NOT_CLOSED"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_INTERNAL = "UNPROVEN_INTERNAL"


class PHSearchStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    NO_CANDIDATE = "NO_CANDIDATE"
    UNPROVEN_TIMEOUT = "UNPROVEN_TIMEOUT"
    UNPROVEN_NUMERIC = "UNPROVEN_NUMERIC"
    UNPROVEN_INTERNAL = "UNPROVEN_INTERNAL"


@dataclass(frozen=True)
class PHFlowStatistics:
    feasibility_augmentations: int = 0
    optimality_cycle_cancellations: int = 0
    optimality_units_augmented: int = 0
    solved_z_branches: int = 0
    infeasible_z_branches: int = 0

    def plus(self, other: "PHFlowStatistics") -> "PHFlowStatistics":
        return PHFlowStatistics(
            self.feasibility_augmentations + other.feasibility_augmentations,
            self.optimality_cycle_cancellations
            + other.optimality_cycle_cancellations,
            self.optimality_units_augmented
            + other.optimality_units_augmented,
            self.solved_z_branches + other.solved_z_branches,
            self.infeasible_z_branches + other.infeasible_z_branches,
        )


@dataclass(frozen=True)
class PHHighPriorityAllocation:
    task_name: str
    g: int
    s: int
    e: int


@dataclass(frozen=True)
class PHLowPriorityAllocation:
    task_name: str
    ell: int


@dataclass(frozen=True)
class PHFlowWitnessEdge:
    key: str
    source: str
    sink: str
    lower: int
    upper: int
    cost: int
    flow: int


@dataclass(frozen=True)
class PHStageWitness:
    target_exec_z: int
    hp_allocations: Tuple[PHHighPriorityAllocation, ...]
    lp_allocations: Tuple[PHLowPriorityAllocation, ...]
    edge_flows: Tuple[PHFlowWitnessEdge, ...]
    integer_cost_scale: int
    scaled_variable_cost: int
    energy: Fraction


@dataclass(frozen=True)
class PHEnvelopeResult:
    status: PHEnvelopeStatus
    energy: Optional[Fraction]
    maximizing_target_exec_z: Optional[int]
    optimal_target_exec_z: Tuple[int, ...]
    checked_z_branches: int
    feasible_z_branches: int
    witness: Optional[PHStageWitness]
    solver_statistics: PHFlowStatistics
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class PHSafetyResult:
    status: PHSafetyStatus
    safe: Optional[bool]
    reason: str
    service_index: int
    available_energy: Optional[Fraction]
    envelope: PHEnvelopeResult


@dataclass(frozen=True)
class PHClosureResult:
    status: PHClosureStatus
    witness_h: Optional[int]
    processor_progress_a: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    impossible_prefix_count: int
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class PHSearchResult:
    solver_status: PHSearchStatus
    candidate_response_time: Optional[int]
    witness_h: Optional[int]
    closing_w: Optional[int]
    checked_w_count: int
    checked_h_count: int
    checked_q_count: int
    envelope_call_count: int
    impossible_prefix_count: int
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class _EdgeSpec:
    key: str
    source: str
    sink: str
    lower: int
    upper: int
    cost: int


class _FlowStatus(Enum):
    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True)
class _FlowResult:
    status: _FlowStatus
    flows: Tuple[int, ...] = ()
    minimum_cost: Optional[int] = None
    statistics: PHFlowStatistics = PHFlowStatistics()
    reason: Optional[str] = None


class _Deadline:
    def __init__(
        self,
        timeout_seconds: Optional[float],
        clock: Callable[[], float],
    ) -> None:
        if not callable(clock):
            raise core.V93InputError("clock must be callable")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise core.V93InputError(
                "timeout_seconds must be finite and non-negative"
            )
        self._clock = clock
        self._limit = timeout_seconds
        self._started = self._read() if timeout_seconds is not None else None

    def _read(self) -> float:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise core.V93NumericError("clock must return a finite number")
        return float(value)

    def expired(self) -> bool:
        if self._limit is None:
            return False
        elapsed = self._read() - self._started
        if elapsed < 0:
            raise core.V93NumericError("clock must be monotonic")
        return elapsed >= self._limit


class _PHDeadlineExpired(TimeoutError):
    """Internal signal that preserves timeout across every PH layer."""


def _raise_if_deadline_expired(
    deadline: Optional[_Deadline], checkpoint: str
) -> None:
    if deadline is not None and deadline.expired():
        raise _PHDeadlineExpired(
            "PH deadline expired at {}".format(checkpoint)
        )


def _plain_int(value: object, label: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise core.V93InputError(
            "{} must be an integer at least {}".format(label, minimum)
        )
    return value


def _flow_terminal_result(
    deadline: _Deadline,
    status: _FlowStatus,
    flows: Tuple[int, ...] = (),
    minimum_cost: Optional[int] = None,
    statistics: PHFlowStatistics = PHFlowStatistics(),
    reason: Optional[str] = None,
) -> _FlowResult:
    """Give expiration priority over every non-timeout flow terminal."""

    if status is not _FlowStatus.TIMEOUT and deadline.expired():
        return _FlowResult(
            _FlowStatus.TIMEOUT,
            statistics=statistics,
            reason="PH flow deadline expired at a terminal boundary",
        )
    return _FlowResult(status, flows, minimum_cost, statistics, reason)


def _validate_point(
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
) -> None:
    processors = _plain_int(processors, "M", 1)
    w = _plain_int(w, "w", target.wcet)
    q = _plain_int(q, "q", 1)
    h = _plain_int(h, "h", 0)
    if w > target.deadline:
        raise core.V93InputError("w must satisfy C_k <= w <= D_k")
    if q + h > w:
        raise core.V93InputError("PH requires q + h <= w")
    names = [target.name]
    names.extend(task.name for task in hp_tasks)
    names.extend(task.name for task in lp_tasks)
    if len(names) != len(set(names)):
        raise core.V93InputError("target, hp, and lp names must be disjoint")
    for task in hp_tasks:
        if task.name not in theta_by_name:
            raise core.V93InputError(
                "missing carry-in theta for {}".format(task.name)
            )
        theta = _plain_int(
            theta_by_name[task.name], "theta[{}]".format(task.name), task.wcet
        )
        if theta > task.deadline:
            raise core.V93InputError("theta must satisfy C_i <= theta_i <= D_i")


def _lcm(left: int, right: int) -> int:
    return left // math.gcd(left, right) * right


def _power_scale(tasks: Iterable[core.V93Task]) -> int:
    scale = 1
    for task in tasks:
        if not isinstance(task.power, Fraction):
            raise core.V93NumericError("task power must be an exact Fraction")
        scale = _lcm(scale, task.power.denominator)
    return scale


def _scaled_power(task: core.V93Task, scale: int) -> int:
    power = task.power
    if scale % power.denominator:
        raise core.V93NumericError("power denominator does not divide scale")
    return power.numerator * (scale // power.denominator)


def _node_hp(task: core.V93Task) -> str:
    return "HP:" + task.name


def _node_lp(task: core.V93Task) -> str:
    return "LP:" + task.name


def _build_branch(
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
    target_exec_z: int,
    scale: int,
) -> Tuple[Tuple[str, ...], Tuple[_EdgeSpec, ...]]:
    source, sink, lp_agg = "SOURCE", "SINK", "LP_AGG"
    g_node, s_node, e_node = "STAGE_G", "STAGE_S", "STAGE_E"
    length = q + h
    full_hp_ticks = q - target_exec_z
    edges = []
    nodes = [source, sink, lp_agg, g_node, s_node, e_node]
    for task in hp_tasks:
        node = _node_hp(task)
        nodes.append(node)
        workload = core.workload_bound_v9_3(
            task, length, theta_by_name[task.name]
        )
        edges.extend(
            (
                _EdgeSpec(
                    "source->hp:" + task.name,
                    source,
                    node,
                    0,
                    workload,
                    -_scaled_power(task, scale),
                ),
                _EdgeSpec("hp->G:" + task.name, node, g_node, 0, full_hp_ticks, 0),
                _EdgeSpec("hp->S:" + task.name, node, s_node, 0, target_exec_z, 0),
                _EdgeSpec("hp->E:" + task.name, node, e_node, 0, h, 0),
            )
        )
    previous_length = max(0, length - 1)
    for task in lp_tasks:
        node = _node_lp(task)
        nodes.append(node)
        upper = min(
            core.deadline_workload_bound_v9_3(task, previous_length),
            previous_length,
            target_exec_z,
        )
        edges.extend(
            (
                _EdgeSpec(
                    "source->lp:" + task.name,
                    source,
                    node,
                    0,
                    upper,
                    -_scaled_power(task, scale),
                ),
                _EdgeSpec("lp->agg:" + task.name, node, lp_agg, 0, upper, 0),
            )
        )
    edges.extend(
        (
            _EdgeSpec(
                "agg->S",
                lp_agg,
                s_node,
                0,
                (processors - 1) * min(target_exec_z, previous_length),
                0,
            ),
            _EdgeSpec(
                "G->sink",
                g_node,
                sink,
                processors * full_hp_ticks,
                processors * full_hp_ticks,
                0,
            ),
            _EdgeSpec(
                "S->sink",
                s_node,
                sink,
                0,
                (processors - 1) * target_exec_z,
                0,
            ),
            _EdgeSpec(
                "E->sink", e_node, sink, 0, (processors - 1) * h, 0
            ),
            _EdgeSpec(
                "return",
                sink,
                source,
                0,
                processors * full_hp_ticks
                + (processors - 1) * target_exec_z
                + (processors - 1) * h,
                0,
            ),
        )
    )
    return tuple(nodes), tuple(edges)


def _solve_min_cost_circulation(
    nodes: Sequence[str],
    edges: Sequence[_EdgeSpec],
    deadline: _Deadline,
) -> _FlowResult:
    """Return an integral optimum or a proved infeasibility certificate."""

    try:
        if len(nodes) != len(set(nodes)) or len({edge.key for edge in edges}) != len(edges):
            return _flow_terminal_result(
                deadline, _FlowStatus.INTERNAL, reason="duplicate flow node/edge"
            )
        node_set = set(nodes)
        balance = {node: 0 for node in nodes}
        for edge in edges:
            if edge.source not in node_set or edge.sink not in node_set:
                return _flow_terminal_result(
                    deadline, _FlowStatus.INTERNAL, reason="unknown edge endpoint"
                )
            if (
                type(edge.lower) is not int
                or type(edge.upper) is not int
                or type(edge.cost) is not int
                or edge.lower < 0
                or edge.upper < edge.lower
            ):
                return _flow_terminal_result(
                    deadline, _FlowStatus.INTERNAL, reason="invalid integer edge"
                )
            balance[edge.source] -= edge.lower
            balance[edge.sink] += edge.lower

        # Integral max-flow on the lower-bound residual network.
        super_source, super_sink = "__SUPER_SOURCE__", "__SUPER_SINK__"
        adjacency: Dict[str, list] = {
            node: [] for node in tuple(nodes) + (super_source, super_sink)
        }

        def add_arc(source: str, sink: str, capacity: int):
            forward = [sink, len(adjacency[sink]), capacity, capacity]
            reverse = [source, len(adjacency[source]), 0, 0]
            adjacency[source].append(forward)
            adjacency[sink].append(reverse)
            return source, len(adjacency[source]) - 1

        original_arcs = []
        for edge in edges:
            original_arcs.append(
                add_arc(edge.source, edge.sink, edge.upper - edge.lower)
            )
        required = 0
        for node in nodes:
            if balance[node] > 0:
                add_arc(super_source, node, balance[node])
                required += balance[node]
            elif balance[node] < 0:
                add_arc(node, super_sink, -balance[node])

        delivered = 0
        augmentations = 0
        while delivered < required:
            if deadline.expired():
                return _flow_terminal_result(
                    deadline,
                    _FlowStatus.TIMEOUT,
                    reason="PH flow feasibility timed out",
                )
            predecessor = {super_source: None}
            queue = deque([super_source])
            while queue and super_sink not in predecessor:
                if deadline.expired():
                    return _flow_terminal_result(
                        deadline,
                        _FlowStatus.TIMEOUT,
                        reason="PH flow feasibility search timed out",
                    )
                source = queue.popleft()
                for arc_index, arc in enumerate(adjacency[source]):
                    if arc[2] > 0 and arc[0] not in predecessor:
                        predecessor[arc[0]] = (source, arc_index)
                        queue.append(arc[0])
            if super_sink not in predecessor:
                stats = PHFlowStatistics(
                    feasibility_augmentations=augmentations,
                    infeasible_z_branches=1,
                )
                return _flow_terminal_result(
                    deadline, _FlowStatus.INFEASIBLE, statistics=stats
                )
            amount = required - delivered
            cursor = super_sink
            while cursor != super_source:
                source, arc_index = predecessor[cursor]
                amount = min(amount, adjacency[source][arc_index][2])
                cursor = source
            cursor = super_sink
            while cursor != super_source:
                source, arc_index = predecessor[cursor]
                arc = adjacency[source][arc_index]
                reverse = adjacency[arc[0]][arc[1]]
                arc[2] -= amount
                reverse[2] += amount
                cursor = source
            delivered += amount
            augmentations += 1

        flows = []
        for edge, (source, arc_index) in zip(edges, original_arcs):
            arc = adjacency[source][arc_index]
            used = arc[3] - arc[2]
            flows.append(edge.lower + used)

        # Exact cycle cancellation.  With finite integral capacities/costs,
        # absence of a negative residual cycle is a global min-cost proof.
        cancellations = 0
        augmented_units = 0
        node_count = len(nodes)
        while True:
            if deadline.expired():
                return _flow_terminal_result(
                    deadline,
                    _FlowStatus.TIMEOUT,
                    reason="PH flow optimization timed out",
                )
            residual = []
            for index, (edge, flow) in enumerate(zip(edges, flows)):
                if flow < edge.upper:
                    residual.append(
                        (edge.source, edge.sink, edge.cost, edge.upper - flow, index, 1)
                    )
                if flow > edge.lower:
                    residual.append(
                        (edge.sink, edge.source, -edge.cost, flow - edge.lower, index, -1)
                    )
            residual.sort(key=lambda item: (item[0], item[1], item[4], item[5]))
            distances = {node: 0 for node in nodes}
            predecessor = {}
            updated = None
            for _iteration in range(node_count):
                if deadline.expired():
                    return _flow_terminal_result(
                        deadline,
                        _FlowStatus.TIMEOUT,
                        reason="PH negative-cycle search timed out",
                    )
                updated = None
                for source, sink, cost, capacity, index, direction in residual:
                    if capacity and distances[sink] > distances[source] + cost:
                        distances[sink] = distances[source] + cost
                        predecessor[sink] = (
                            source,
                            sink,
                            cost,
                            capacity,
                            index,
                            direction,
                        )
                        updated = sink
                if updated is None:
                    break
            if updated is None:
                break
            cursor = updated
            for _ in range(node_count):
                if cursor not in predecessor:
                    return _flow_terminal_result(
                        deadline,
                        _FlowStatus.INTERNAL,
                        reason="negative-cycle predecessor chain is incomplete",
                    )
                cursor = predecessor[cursor][0]
            cycle_start = cursor
            cycle = []
            while True:
                if cursor not in predecessor:
                    return _flow_terminal_result(
                        deadline,
                        _FlowStatus.INTERNAL,
                        reason="negative-cycle reconstruction failed",
                    )
                arc = predecessor[cursor]
                cycle.append(arc)
                cursor = arc[0]
                if cursor == cycle_start:
                    break
                if len(cycle) > node_count:
                    return _flow_terminal_result(
                        deadline,
                        _FlowStatus.INTERNAL,
                        reason="negative cycle is not simple",
                    )
            if sum(arc[2] for arc in cycle) >= 0:
                return _flow_terminal_result(
                    deadline,
                    _FlowStatus.INTERNAL,
                    reason="cycle-cancellation optimality witness is not negative",
                )
            amount = min(arc[3] for arc in cycle)
            if amount <= 0:
                return _flow_terminal_result(
                    deadline, _FlowStatus.INTERNAL, reason="empty residual cycle"
                )
            for arc in cycle:
                flows[arc[4]] += arc[5] * amount
            cancellations += 1
            augmented_units += amount

        for node in nodes:
            incoming = sum(
                flow for edge, flow in zip(edges, flows) if edge.sink == node
            )
            outgoing = sum(
                flow for edge, flow in zip(edges, flows) if edge.source == node
            )
            if incoming != outgoing:
                return _flow_terminal_result(
                    deadline,
                    _FlowStatus.INTERNAL,
                    reason="flow conservation failed",
                )
        if any(
            not edge.lower <= flow <= edge.upper
            for edge, flow in zip(edges, flows)
        ):
            return _flow_terminal_result(
                deadline, _FlowStatus.INTERNAL, reason="flow bound failed"
            )
        minimum_cost = sum(edge.cost * flow for edge, flow in zip(edges, flows))
        stats = PHFlowStatistics(
            feasibility_augmentations=augmentations,
            optimality_cycle_cancellations=cancellations,
            optimality_units_augmented=augmented_units,
            solved_z_branches=1,
        )
        return _flow_terminal_result(
            deadline, _FlowStatus.OPTIMAL, tuple(flows), minimum_cost, stats
        )
    except (core.V93NumericError, ArithmeticError):
        raise
    except Exception as exc:  # fail closed at the solver boundary
        return _flow_terminal_result(
            deadline, _FlowStatus.INTERNAL, reason=str(exc)
        )


def _witness_from_flow(
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    target_exec_z: int,
    scale: int,
    edges: Sequence[_EdgeSpec],
    flow_result: _FlowResult,
) -> PHStageWitness:
    by_key = {edge.key: flow for edge, flow in zip(edges, flow_result.flows)}
    hp = tuple(
        PHHighPriorityAllocation(
            task.name,
            by_key["hp->G:" + task.name],
            by_key["hp->S:" + task.name],
            by_key["hp->E:" + task.name],
        )
        for task in hp_tasks
    )
    lp = tuple(
        PHLowPriorityAllocation(task.name, by_key["lp->agg:" + task.name])
        for task in lp_tasks
    )
    energy = target_exec_z * target.power
    energy += sum(
        (allocation.g + allocation.s + allocation.e) * task.power
        for allocation, task in zip(hp, hp_tasks)
    )
    energy += sum(
        allocation.ell * task.power
        for allocation, task in zip(lp, lp_tasks)
    )
    return PHStageWitness(
        target_exec_z=target_exec_z,
        hp_allocations=hp,
        lp_allocations=lp,
        edge_flows=tuple(
            PHFlowWitnessEdge(
                edge.key,
                edge.source,
                edge.sink,
                edge.lower,
                edge.upper,
                edge.cost,
                flow,
            )
            for edge, flow in zip(edges, flow_result.flows)
        ),
        integer_cost_scale=scale,
        scaled_variable_cost=flow_result.minimum_cost,
        energy=energy,
    )


def validate_phase_witness_v9_3(
    witness: PHStageWitness,
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
    _deadline: Optional[_Deadline] = None,
) -> bool:
    """Independently reconstruct every bound and validate a solver witness."""

    _raise_if_deadline_expired(_deadline, "witness_entry")
    _validate_point(
        target, hp_tasks, lp_tasks, w, q, h, processors, theta_by_name
    )
    if type(witness.target_exec_z) is not int or not 0 <= witness.target_exec_z <= min(target.wcet, q):
        return False
    scale = _power_scale((target,) + tuple(hp_tasks) + tuple(lp_tasks))
    if witness.integer_cost_scale != scale:
        return False
    _nodes, expected_edges = _build_branch(
        target,
        hp_tasks,
        lp_tasks,
        q,
        h,
        processors,
        theta_by_name,
        witness.target_exec_z,
        scale,
    )
    if len(witness.edge_flows) != len(expected_edges):
        return False
    for observed, expected in zip(witness.edge_flows, expected_edges):
        if (
            observed.key,
            observed.source,
            observed.sink,
            observed.lower,
            observed.upper,
            observed.cost,
        ) != (
            expected.key,
            expected.source,
            expected.sink,
            expected.lower,
            expected.upper,
            expected.cost,
        ):
            return False
        if type(observed.flow) is not int or not observed.lower <= observed.flow <= observed.upper:
            return False
        _raise_if_deadline_expired(_deadline, "witness_edge_complete")
    flows = {edge.key: edge.flow for edge in witness.edge_flows}
    for node in _nodes:
        incoming = sum(edge.flow for edge in witness.edge_flows if edge.sink == node)
        outgoing = sum(edge.flow for edge in witness.edge_flows if edge.source == node)
        if incoming != outgoing:
            return False
        _raise_if_deadline_expired(_deadline, "witness_node_complete")
    if tuple(item.task_name for item in witness.hp_allocations) != tuple(
        task.name for task in hp_tasks
    ):
        return False
    if tuple(item.task_name for item in witness.lp_allocations) != tuple(
        task.name for task in lp_tasks
    ):
        return False
    z = witness.target_exec_z
    length = q + h
    for task, allocation in zip(hp_tasks, witness.hp_allocations):
        if any(type(value) is not int or value < 0 for value in (allocation.g, allocation.s, allocation.e)):
            return False
        if allocation.g > q - z or allocation.s > z or allocation.e > h:
            return False
        workload = core.workload_bound_v9_3(
            task, length, theta_by_name[task.name]
        )
        if allocation.g + allocation.s + allocation.e > workload:
            return False
        if (
            allocation.g != flows["hp->G:" + task.name]
            or allocation.s != flows["hp->S:" + task.name]
            or allocation.e != flows["hp->E:" + task.name]
            or allocation.g + allocation.s + allocation.e
            != flows["source->hp:" + task.name]
        ):
            return False
        _raise_if_deadline_expired(_deadline, "witness_hp_complete")
    if sum(item.g for item in witness.hp_allocations) != processors * (q - z):
        return False
    if sum(item.e for item in witness.hp_allocations) > (processors - 1) * h:
        return False
    previous_length = max(0, length - 1)
    for task, allocation in zip(lp_tasks, witness.lp_allocations):
        upper = min(
            core.deadline_workload_bound_v9_3(task, previous_length),
            previous_length,
            z,
        )
        if type(allocation.ell) is not int or not 0 <= allocation.ell <= upper:
            return False
        if (
            allocation.ell != flows["lp->agg:" + task.name]
            or allocation.ell != flows["source->lp:" + task.name]
        ):
            return False
        _raise_if_deadline_expired(_deadline, "witness_lp_complete")
    lp_sum = sum(item.ell for item in witness.lp_allocations)
    if lp_sum > (processors - 1) * min(z, previous_length):
        return False
    if sum(item.s for item in witness.hp_allocations) + lp_sum > (processors - 1) * z:
        return False
    scaled_cost = sum(edge.cost * edge.flow for edge in witness.edge_flows)
    if scaled_cost != witness.scaled_variable_cost:
        return False
    _raise_if_deadline_expired(_deadline, "witness_energy_start")
    expected_energy = z * target.power
    expected_energy += sum(
        (allocation.g + allocation.s + allocation.e) * task.power
        for task, allocation in zip(hp_tasks, witness.hp_allocations)
    )
    expected_energy += sum(
        allocation.ell * task.power
        for task, allocation in zip(lp_tasks, witness.lp_allocations)
    )
    _raise_if_deadline_expired(_deadline, "witness_energy_reconstructed")
    if witness.energy != expected_energy:
        return False
    if Fraction(-scaled_cost, scale) + z * target.power != expected_energy:
        return False
    _raise_if_deadline_expired(_deadline, "witness_success")
    return True


def phase_energy_envelope_v9_3(
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    _deadline: Optional[_Deadline] = None,
    _flow_solver: Callable[
        [Sequence[str], Sequence[_EdgeSpec], _Deadline], _FlowResult
    ] = _solve_min_cost_circulation,
) -> PHEnvelopeResult:
    """Compute the exact PH envelope across every target-execution branch."""

    checked = 0
    feasible = 0
    stats = PHFlowStatistics()
    deadline = _deadline
    try:
        deadline = deadline or _Deadline(timeout_seconds, clock)
        _raise_if_deadline_expired(deadline, "envelope_entry")
        _validate_point(
            target, hp_tasks, lp_tasks, w, q, h, processors, theta_by_name
        )
        _raise_if_deadline_expired(deadline, "envelope_point_validated")
        scale = _power_scale((target,) + tuple(hp_tasks) + tuple(lp_tasks))
        optima = []
        for target_exec_z in range(0, min(target.wcet, q) + 1):
            _raise_if_deadline_expired(deadline, "envelope_z_entry")
            checked += 1
            nodes, edges = _build_branch(
                target,
                hp_tasks,
                lp_tasks,
                q,
                h,
                processors,
                theta_by_name,
                target_exec_z,
                scale,
            )
            result = _flow_solver(nodes, edges, deadline)
            _raise_if_deadline_expired(deadline, "envelope_solver_returned")
            stats = stats.plus(result.statistics)
            if result.status is _FlowStatus.INFEASIBLE:
                continue
            if result.status is _FlowStatus.TIMEOUT:
                return PHEnvelopeResult(
                    PHEnvelopeStatus.UNPROVEN_TIMEOUT,
                    None,
                    None,
                    (),
                    checked,
                    feasible,
                    None,
                    stats,
                    result.reason,
                )
            if result.status is not _FlowStatus.OPTIMAL:
                return PHEnvelopeResult(
                    PHEnvelopeStatus.UNPROVEN_INTERNAL,
                    None,
                    None,
                    (),
                    checked,
                    feasible,
                    None,
                    stats,
                    result.reason or "PH flow optimum was not proved",
                )
            feasible += 1
            _raise_if_deadline_expired(deadline, "envelope_before_witness")
            witness = _witness_from_flow(
                target,
                hp_tasks,
                lp_tasks,
                target_exec_z,
                scale,
                edges,
                result,
            )
            witness_valid = validate_phase_witness_v9_3(
                witness,
                target=target,
                hp_tasks=hp_tasks,
                lp_tasks=lp_tasks,
                w=w,
                q=q,
                h=h,
                processors=processors,
                theta_by_name=theta_by_name,
                _deadline=deadline,
            )
            _raise_if_deadline_expired(deadline, "envelope_witness_validated")
            if not witness_valid:
                return PHEnvelopeResult(
                    PHEnvelopeStatus.UNPROVEN_INTERNAL,
                    None,
                    None,
                    (),
                    checked,
                    feasible,
                    None,
                    stats,
                    "independent PH witness validation failed",
                )
            _raise_if_deadline_expired(deadline, "envelope_branch_optimal")
            optima.append((witness.energy, target_exec_z, witness))
        _raise_if_deadline_expired(deadline, "envelope_z_aggregation")
        if not optima:
            _raise_if_deadline_expired(deadline, "envelope_before_impossible_prefix")
            return PHEnvelopeResult(
                PHEnvelopeStatus.IMPOSSIBLE_PREFIX,
                None,
                None,
                (),
                checked,
                0,
                None,
                stats,
                "all target-execution branches are infeasible",
            )
        best_energy = max(item[0] for item in optima)
        optimal_z = tuple(item[1] for item in optima if item[0] == best_energy)
        chosen = next(item[2] for item in optima if item[0] == best_energy)
        _raise_if_deadline_expired(deadline, "envelope_before_optimal")
        return PHEnvelopeResult(
            PHEnvelopeStatus.OPTIMAL,
            best_energy,
            chosen.target_exec_z,
            optimal_z,
            checked,
            feasible,
            chosen,
            stats,
        )
    except TimeoutError as exc:
        status = PHEnvelopeStatus.UNPROVEN_TIMEOUT
        reason = str(exc)
    except (core.V93NumericError, OverflowError, ArithmeticError) as exc:
        try:
            _raise_if_deadline_expired(deadline, "envelope_numeric_failure")
        except _PHDeadlineExpired as timeout_exc:
            status = PHEnvelopeStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = PHEnvelopeStatus.UNPROVEN_NUMERIC
            reason = str(exc)
    except core.V93InputError:
        raise
    except Exception as exc:
        try:
            _raise_if_deadline_expired(deadline, "envelope_internal_failure")
        except _PHDeadlineExpired as timeout_exc:
            status = PHEnvelopeStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = PHEnvelopeStatus.UNPROVEN_INTERNAL
            reason = str(exc)
    return PHEnvelopeResult(
        status, None, None, (), checked, feasible, None, stats, reason
    )


def phase_safe_v9_3(
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    w: int,
    q: int,
    h: int,
    processors: int,
    theta_by_name: Mapping[str, int],
    e0: core.ExactInput,
    beta: core.ServiceCurve,
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    _deadline: Optional[_Deadline] = None,
    _flow_solver: Callable[
        [Sequence[str], Sequence[_EdgeSpec], _Deadline], _FlowResult
    ] = _solve_min_cost_circulation,
) -> PHSafetyResult:
    service_index = h + q - 1
    deadline = _deadline or _Deadline(timeout_seconds, clock)
    envelope = None
    try:
        _raise_if_deadline_expired(deadline, "safety_entry")
        envelope = phase_energy_envelope_v9_3(
            target=target,
            hp_tasks=hp_tasks,
            lp_tasks=lp_tasks,
            w=w,
            q=q,
            h=h,
            processors=processors,
            theta_by_name=theta_by_name,
            clock=clock,
            _deadline=deadline,
            _flow_solver=_flow_solver,
        )
        _raise_if_deadline_expired(deadline, "safety_envelope_returned")
        exact_e0 = core.exact_fraction_v9_3(e0, "E0")
        if exact_e0 < 0:
            raise core.V93NumericError("E0 must be non-negative")
        _raise_if_deadline_expired(deadline, "safety_e0_validated")
        validated_beta = core.validate_service_curve_v9_3(beta, service_index)
        _raise_if_deadline_expired(deadline, "safety_service_validated")
        available = exact_e0 + validated_beta[service_index]
        _raise_if_deadline_expired(deadline, "safety_before_envelope_terminal")
    except TimeoutError as exc:
        if envelope is None or envelope.status is not PHEnvelopeStatus.UNPROVEN_TIMEOUT:
            envelope = PHEnvelopeResult(
                PHEnvelopeStatus.UNPROVEN_TIMEOUT,
                None,
                None,
                (),
                0,
                0,
                None,
                PHFlowStatistics(),
                str(exc),
            )
        return PHSafetyResult(
            PHSafetyStatus.UNPROVEN_TIMEOUT,
            None,
            str(exc),
            service_index,
            None,
            envelope,
        )
    except (core.V93NumericError, ArithmeticError) as exc:
        try:
            _raise_if_deadline_expired(deadline, "safety_numeric_failure")
        except _PHDeadlineExpired as timeout_exc:
            if envelope is None:
                envelope = PHEnvelopeResult(
                    PHEnvelopeStatus.UNPROVEN_TIMEOUT,
                    None,
                    None,
                    (),
                    0,
                    0,
                    None,
                    PHFlowStatistics(),
                    str(timeout_exc),
                )
            return PHSafetyResult(
                PHSafetyStatus.UNPROVEN_TIMEOUT,
                None,
                str(timeout_exc),
                service_index,
                None,
                envelope,
            )
        return PHSafetyResult(
            PHSafetyStatus.UNPROVEN_NUMERIC,
            None,
            str(exc),
            service_index,
            None,
            envelope,
        )
    if envelope.status is PHEnvelopeStatus.IMPOSSIBLE_PREFIX:
        try:
            _raise_if_deadline_expired(deadline, "safety_before_impossible_prefix")
        except _PHDeadlineExpired as exc:
            return PHSafetyResult(
                PHSafetyStatus.UNPROVEN_TIMEOUT,
                None,
                str(exc),
                service_index,
                None,
                envelope,
            )
        return PHSafetyResult(
            PHSafetyStatus.SAFE,
            True,
            PHEnvelopeStatus.IMPOSSIBLE_PREFIX.value,
            service_index,
            None,
            envelope,
        )
    mapping = {
        PHEnvelopeStatus.UNPROVEN_TIMEOUT: PHSafetyStatus.UNPROVEN_TIMEOUT,
        PHEnvelopeStatus.UNPROVEN_NUMERIC: PHSafetyStatus.UNPROVEN_NUMERIC,
        PHEnvelopeStatus.UNPROVEN_INTERNAL: PHSafetyStatus.UNPROVEN_INTERNAL,
    }
    if envelope.status in mapping:
        return PHSafetyResult(
            mapping[envelope.status],
            None,
            envelope.failure_reason or envelope.status.value,
            service_index,
            None,
            envelope,
        )
    try:
        _raise_if_deadline_expired(deadline, "safety_before_energy_decision")
    except _PHDeadlineExpired as exc:
        return PHSafetyResult(
            PHSafetyStatus.UNPROVEN_TIMEOUT,
            None,
            str(exc),
            service_index,
            None,
            envelope,
        )
    safe = envelope.energy <= available
    try:
        _raise_if_deadline_expired(deadline, "safety_before_terminal")
    except _PHDeadlineExpired as exc:
        return PHSafetyResult(
            PHSafetyStatus.UNPROVEN_TIMEOUT,
            None,
            str(exc),
            service_index,
            None,
            envelope,
        )
    return PHSafetyResult(
        PHSafetyStatus.SAFE if safe else PHSafetyStatus.UNSAFE,
        safe,
        "ENERGY_BOUND" if safe else "ENERGY_EXCEEDS_SERVICE",
        service_index,
        available,
        envelope,
    )


def close_ph_v9_3(
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    w: int,
    processors: int,
    theta_by_name: Mapping[str, int],
    e0: core.ExactInput,
    beta: core.ServiceCurve,
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    _deadline: Optional[_Deadline] = None,
    _flow_solver: Callable[
        [Sequence[str], Sequence[_EdgeSpec], _Deadline], _FlowResult
    ] = _solve_min_cost_circulation,
) -> PHClosureResult:
    deadline = _deadline or _Deadline(timeout_seconds, clock)
    a_value = target.wcet
    checked_h = checked_q = envelope_calls = impossible = 0
    try:
        _raise_if_deadline_expired(deadline, "closure_before_progress")
        a_value = core.processor_progress_v9_3(
            target, hp_tasks, w, processors, theta_by_name
        )
        _raise_if_deadline_expired(deadline, "closure_after_progress")
        if a_value > w:
            _raise_if_deadline_expired(deadline, "closure_before_a_gt_w")
            return PHClosureResult(
                PHClosureStatus.NOT_CLOSED, None, a_value, 0, 0, 0, 0
            )
        for h in range(0, w - a_value + 1):
            _raise_if_deadline_expired(deadline, "closure_h_entry")
            checked_h += 1
            h_safe = True
            for q in range(1, a_value + 1):
                _raise_if_deadline_expired(deadline, "closure_q_entry")
                checked_q += 1
                envelope_calls += 1
                result = phase_safe_v9_3(
                    target=target,
                    hp_tasks=hp_tasks,
                    lp_tasks=lp_tasks,
                    w=w,
                    q=q,
                    h=h,
                    processors=processors,
                    theta_by_name=theta_by_name,
                    e0=e0,
                    beta=beta,
                    clock=clock,
                    _deadline=deadline,
                    _flow_solver=_flow_solver,
                )
                _raise_if_deadline_expired(deadline, "closure_prefix_returned")
                if result.reason == PHEnvelopeStatus.IMPOSSIBLE_PREFIX.value:
                    impossible += 1
                if result.status is PHSafetyStatus.UNSAFE:
                    _raise_if_deadline_expired(deadline, "closure_after_unsafe")
                    h_safe = False
                    break
                if result.status is not PHSafetyStatus.SAFE:
                    status = {
                        PHSafetyStatus.UNPROVEN_TIMEOUT: PHClosureStatus.UNPROVEN_TIMEOUT,
                        PHSafetyStatus.UNPROVEN_NUMERIC: PHClosureStatus.UNPROVEN_NUMERIC,
                        PHSafetyStatus.UNPROVEN_INTERNAL: PHClosureStatus.UNPROVEN_INTERNAL,
                    }[result.status]
                    return PHClosureResult(
                        status,
                        None,
                        a_value,
                        checked_h,
                        checked_q,
                        envelope_calls,
                        impossible,
                        result.reason,
                    )
            if h_safe:
                _raise_if_deadline_expired(deadline, "closure_before_closed")
                return PHClosureResult(
                    PHClosureStatus.CLOSED,
                    h,
                    a_value,
                    checked_h,
                    checked_q,
                    envelope_calls,
                    impossible,
                )
        _raise_if_deadline_expired(deadline, "closure_before_not_closed")
        return PHClosureResult(
            PHClosureStatus.NOT_CLOSED,
            None,
            a_value,
            checked_h,
            checked_q,
            envelope_calls,
            impossible,
        )
    except _PHDeadlineExpired as exc:
        return PHClosureResult(
            PHClosureStatus.UNPROVEN_TIMEOUT,
            None,
            a_value,
            checked_h,
            checked_q,
            envelope_calls,
            impossible,
            str(exc),
        )


def ph_response_time_v9_3(
    *,
    target: core.V93Task,
    hp_tasks: Sequence[core.V93Task],
    lp_tasks: Sequence[core.V93Task],
    processors: int,
    theta_by_name: Mapping[str, int],
    e0: core.ExactInput,
    beta: core.ServiceCurve,
    timeout_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    _flow_solver: Callable[
        [Sequence[str], Sequence[_EdgeSpec], _Deadline], _FlowResult
    ] = _solve_min_cost_circulation,
) -> PHSearchResult:
    checked_w = checked_h = checked_q = envelope_calls = impossible = 0
    deadline = None
    try:
        deadline = _Deadline(timeout_seconds, clock)
        _raise_if_deadline_expired(deadline, "response_entry")
        validated_beta = core.validate_service_curve_v9_3(
            beta, target.deadline - 1
        )
        _raise_if_deadline_expired(deadline, "response_service_validated")
        exact_e0 = core.exact_fraction_v9_3(e0, "E0")
        if exact_e0 < 0:
            raise core.V93NumericError("E0 must be non-negative")
        _raise_if_deadline_expired(deadline, "response_e0_validated")
        for w in range(target.wcet, target.deadline + 1):
            _raise_if_deadline_expired(deadline, "response_w_entry")
            checked_w += 1
            closure = close_ph_v9_3(
                target=target,
                hp_tasks=hp_tasks,
                lp_tasks=lp_tasks,
                w=w,
                processors=processors,
                theta_by_name=theta_by_name,
                e0=exact_e0,
                beta=validated_beta,
                clock=clock,
                _deadline=deadline,
                _flow_solver=_flow_solver,
            )
            _raise_if_deadline_expired(deadline, "response_closure_returned")
            checked_h += closure.checked_h_count
            checked_q += closure.checked_q_count
            envelope_calls += closure.envelope_call_count
            impossible += closure.impossible_prefix_count
            if closure.status is PHClosureStatus.CLOSED:
                _raise_if_deadline_expired(deadline, "response_before_candidate")
                return PHSearchResult(
                    PHSearchStatus.CANDIDATE,
                    w,
                    closure.witness_h,
                    w,
                    checked_w,
                    checked_h,
                    checked_q,
                    envelope_calls,
                    impossible,
                )
            if closure.status is not PHClosureStatus.NOT_CLOSED:
                status = {
                    PHClosureStatus.UNPROVEN_TIMEOUT: PHSearchStatus.UNPROVEN_TIMEOUT,
                    PHClosureStatus.UNPROVEN_NUMERIC: PHSearchStatus.UNPROVEN_NUMERIC,
                    PHClosureStatus.UNPROVEN_INTERNAL: PHSearchStatus.UNPROVEN_INTERNAL,
                }[closure.status]
                return PHSearchResult(
                    status,
                    None,
                    None,
                    None,
                    checked_w,
                    checked_h,
                    checked_q,
                    envelope_calls,
                    impossible,
                    closure.failure_reason,
                )
            _raise_if_deadline_expired(deadline, "response_w_not_closed")
    except TimeoutError as exc:
        status = PHSearchStatus.UNPROVEN_TIMEOUT
        reason = str(exc)
    except (core.V93NumericError, OverflowError, ArithmeticError) as exc:
        try:
            _raise_if_deadline_expired(deadline, "response_numeric_failure")
        except _PHDeadlineExpired as timeout_exc:
            status = PHSearchStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = PHSearchStatus.UNPROVEN_NUMERIC
            reason = str(exc)
    except core.V93InputError:
        raise
    except Exception as exc:
        try:
            _raise_if_deadline_expired(deadline, "response_internal_failure")
        except _PHDeadlineExpired as timeout_exc:
            status = PHSearchStatus.UNPROVEN_TIMEOUT
            reason = str(timeout_exc)
        else:
            status = PHSearchStatus.UNPROVEN_INTERNAL
            reason = str(exc)
    else:
        try:
            _raise_if_deadline_expired(deadline, "response_before_no_candidate")
        except _PHDeadlineExpired as exc:
            status = PHSearchStatus.UNPROVEN_TIMEOUT
            reason = str(exc)
        else:
            return PHSearchResult(
                PHSearchStatus.NO_CANDIDATE,
                None,
                None,
                None,
                checked_w,
                checked_h,
                checked_q,
                envelope_calls,
                impossible,
                "no PH closure candidate by the task deadline",
            )
    return PHSearchResult(
        status,
        None,
        None,
        None,
        checked_w,
        checked_h,
        checked_q,
        envelope_calls,
        impossible,
        reason,
    )
