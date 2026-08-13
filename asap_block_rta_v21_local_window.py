#!/usr/bin/env python3
"""Experimental ASAP-BLOCK v21 local-window sufficient RTA.

This module intentionally leaves :mod:`asap_block_rta` unchanged.  It reuses
the frozen v20.4 input model, processor-reference calculation, harvesting
service curve, and exact integral flow primitive, while implementing the v21
local-window Omega set and inner closure search here.
"""

import argparse
import inspect
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import asap_block_rta as v20


RTA_VERSION = "v21-local-window"
THEORY_METADATA = {
    "theory_family": "local_window_closure",
    "closure_method": "delta_closure",
    "empty_set_guard": True,
    "fallback_guard": True,
    "consistency_guard": True,
    "certified_carry_in_source": "v21_recursive_certification",
    "uses_local_window": True,
    "uses_delta_closure": True,
    "uses_parallel_u_compression": False,
}


@dataclass(frozen=True)
class LocalOmegaExtrema:
    feasible: bool
    max_u: int = 0
    max_energy: Fraction = Fraction(0)


@dataclass(frozen=True)
class LocalGResult:
    feasible: bool
    value: Optional[int] = None
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class LocalClosureResult:
    closed: bool
    delta: Optional[int] = None
    g_value: Optional[int] = None
    failure_reason: Optional[str] = None


@dataclass
class V21ClosureProfile:
    task_id: str
    delta_iterations: int = 0
    g_loc_calls: int = 0
    omega_feasibility_calls: int = 0
    empty_omega_count: int = 0
    no_closure_count: int = 0
    closed_prefix_count: int = 0
    delta_cap_exceeded_count: int = 0
    max_delta_cap: int = 0
    max_delta_seen: int = 0
    delta_jump_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class V21TasksetAnalysis:
    system_file: str
    tasks_file: str
    horizon_ms: int
    assume_no_overflow: bool
    e0: float
    tasks: List[v20.TaskAnalysisResult]

    @property
    def proven(self) -> bool:
        return bool(self.tasks) and all(result.proven for result in self.tasks)

    @property
    def assumptions(self) -> List[str]:
        return [
            "battery does not overflow during the analyzed interval",
            "harvesting service curve is valid only within the {} ms horizon".format(
                self.horizon_ms
            ),
            "E0 is {} J at each analyzed job release".format(self.e0),
            "scheduler tick duration is 1 ms",
            "tasks use the restricted periodic single-segment fixed model",
            "local-window taskset proof uses first-counterexample semantics",
        ]

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "rta_version": RTA_VERSION,
            "system_file": self.system_file,
            "tasks_file": self.tasks_file,
            "horizon_ms": self.horizon_ms,
            "assume_no_overflow": self.assume_no_overflow,
            "E0": self.e0,
            "proven": self.proven,
            "conditional": True,
            "assumptions": self.assumptions,
            "proven_under_assumptions": self.proven,
            "absolute_schedulability_claim": False,
            "tasks": [result.to_dict() for result in self.tasks],
        }
        result.update(THEORY_METADATA)
        return result


def local_workload_bound(
    task: v20.RTATask, length: int, theta: Optional[int] = None
) -> int:
    """Return the frozen generic workload formula at local length ``L``."""

    return v20.workload_bound(task, length, theta)


def processor_reference_length(
    target: v20.RTATask,
    w: int,
    processors: int,
    tasks: Sequence[v20.RTATask],
    certified_bounds: Mapping[str, int],
) -> Tuple[int, int]:
    """Return frozen-v20.4 ``(A_k^Theta(w), D_k^P(w))``."""

    delay = v20.processor_delay(
        target,
        w,
        processors,
        tasks,
        certified_bounds=certified_bounds,
    )
    return target.wcet + delay, delay


def _cached_workload(
    task: v20.RTATask,
    length: int,
    theta: Optional[int],
    cache: Dict[Tuple[str, int, Optional[int]], int],
) -> int:
    key = (task.name, int(length), None if theta is None else int(theta))
    if key not in cache:
        cache[key] = local_workload_bound(task, length, theta)
    return cache[key]


def local_omega_extrema_for_z(
    target: v20.RTATask,
    tasks: Sequence[v20.RTATask],
    w: int,
    x: int,
    delta: int,
    z: int,
    processors: int,
    certified_bounds: Mapping[str, int],
    workload_cache: Optional[
        Dict[Tuple[str, int, Optional[int]], int]
    ] = None,
    closure_profile: Optional[V21ClosureProfile] = None,
) -> LocalOmegaExtrema:
    """Return exact independent ``max U`` and ``max E`` for local Omega.

    The integral flow enforces exact ``sum(a_i)=M(x-z)``, per-task local
    workload capacity, shared ``b/c`` concurrency capacity, and
    ``sum(u_i)<=M*delta``.  The independent extrema are exact for
    ``max_omega max(U(omega), charge(E(omega)))`` because the outer maximum
    distributes over the two monotone objectives.
    """

    if closure_profile is not None:
        closure_profile.omega_feasibility_calls += 1
    del w  # w affects z through D^P outside this local Omega subproblem.
    if processors <= 0 or x < 0 or delta < 0 or z < 0:
        raise ValueError("invalid local Omega dimensions")
    if x == 0:
        return LocalOmegaExtrema(z == 0, 0, Fraction(0))

    cache = workload_cache if workload_cache is not None else {}
    local_length = x + delta
    high = v20.hp(tasks, target)
    low = v20._lp(tasks, target)
    high_limits = {
        task.name: min(
            _cached_workload(
                task,
                local_length,
                certified_bounds[task.name],
                cache,
            ),
            local_length,
        )
        for task in high
    }
    a_capacity = processors * (x - z)
    if a_capacity < 0:
        return LocalOmegaExtrema(False)
    a_caps = {
        task.name: min(high_limits[task.name], x - z)
        for task in high
    }
    if sum(a_caps.values()) < a_capacity:
        return LocalOmegaExtrema(False)

    u_capacity = processors * delta
    max_u = min(
        u_capacity,
        sum(high_limits.values()) - a_capacity,
    )
    b_capacity = (processors - 1) * z
    low_limits = {
        task.name: min(
            _cached_workload(task, local_length, None, cache),
            z,
        )
        for task in low
    }

    physical_limit = z * v20._energy_fraction(target)
    physical_limit += sum(
        high_limits[task.name] * v20._energy_fraction(task)
        for task in high
    )
    physical_limit += sum(
        low_limits[task.name] * v20._energy_fraction(task)
        for task in low
    )
    mandatory_bonus = physical_limit + 1

    source = 0
    first_high = 1
    first_low = first_high + len(high)
    a_bin = first_low + len(low)
    u_bin = a_bin + 1
    b_bin = u_bin + 1
    sink = b_bin + 1
    graph: List[List[v20._FlowEdge]] = [
        [] for _ in range(sink + 1)
    ]

    for index, task in enumerate(high):
        node = first_high + index
        capacity = high_limits[task.name]
        energy = v20._energy_fraction(task)
        v20._add_flow_edge(graph, source, node, capacity, Fraction(0))
        v20._add_flow_edge(
            graph,
            node,
            a_bin,
            a_caps[task.name],
            -(energy + mandatory_bonus),
        )
        v20._add_flow_edge(graph, node, u_bin, capacity, -energy)
        v20._add_flow_edge(
            graph, node, b_bin, min(z, capacity), -energy
        )
    for index, task in enumerate(low):
        node = first_low + index
        capacity = low_limits[task.name]
        energy = v20._energy_fraction(task)
        v20._add_flow_edge(graph, source, node, capacity, Fraction(0))
        v20._add_flow_edge(graph, node, b_bin, capacity, -energy)

    a_edge_index = v20._add_flow_edge(
        graph, a_bin, sink, a_capacity, Fraction(0)
    )
    v20._add_flow_edge(graph, u_bin, sink, u_capacity, Fraction(0))
    v20._add_flow_edge(graph, b_bin, sink, b_capacity, Fraction(0))
    _flow, flow_cost, _augmentations = v20._min_cost_max_flow(
        graph, source, sink
    )
    a_flow = a_capacity - graph[a_bin][a_edge_index].capacity
    if a_flow != a_capacity:
        return LocalOmegaExtrema(False)

    max_energy = (
        z * v20._energy_fraction(target)
        - flow_cost
        - a_capacity * mandatory_bonus
    )
    return LocalOmegaExtrema(True, max_u, max_energy)


def local_g(
    target: v20.RTATask,
    w: int,
    x: int,
    delta: int,
    beta: Sequence[float],
    e0: float,
    tasks: Sequence[v20.RTATask],
    processors: int,
    certified_bounds: Mapping[str, int],
    processor_delay_value: Optional[int] = None,
    workload_cache: Optional[
        Dict[Tuple[str, int, Optional[int]], int]
    ] = None,
    closure_profile: Optional[V21ClosureProfile] = None,
) -> LocalGResult:
    """Compute ``G_k^{Theta,loc}(w,x,delta)`` exactly."""

    if closure_profile is not None:
        closure_profile.g_loc_calls += 1
    delay = (
        processor_delay_value
        if processor_delay_value is not None
        else v20.processor_delay(
            target,
            w,
            processors,
            tasks,
            certified_bounds=certified_bounds,
        )
    )
    z_min = max(0, x - delay)
    z_max = min(target.wcet, x)
    if z_min > z_max:
        if closure_profile is not None:
            closure_profile.empty_omega_count += 1
        return LocalGResult(False, failure_reason="empty local Omega")

    feasible = False
    max_u = 0
    max_energy = Fraction(0)
    cache = workload_cache if workload_cache is not None else {}
    for z in range(z_min, z_max + 1):
        extrema = local_omega_extrema_for_z(
            target,
            tasks,
            w,
            x,
            delta,
            z,
            processors,
            certified_bounds,
            workload_cache=cache,
            closure_profile=closure_profile,
        )
        if not extrema.feasible:
            continue
        feasible = True
        max_u = max(max_u, extrema.max_u)
        max_energy = max(max_energy, extrema.max_energy)
    if not feasible:
        if closure_profile is not None:
            closure_profile.empty_omega_count += 1
        return LocalGResult(False, failure_reason="empty local Omega")

    demand = max(max_energy - v20._exact_fraction(e0), Fraction(0))
    charge_time = v20.beta_inverse(beta, demand)
    if charge_time is None:
        return LocalGResult(
            True,
            None,
            "finite energy service is insufficient for local window",
        )
    value = max(max_u, max(charge_time - x, 0))
    return LocalGResult(True, value)


GFunction = Callable[..., LocalGResult]


def _accepts_closure_profile(function: GFunction) -> bool:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return False
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return True
    parameter = signature.parameters.get("closure_profile")
    return (
        parameter is not None
        and parameter.kind != inspect.Parameter.POSITIONAL_ONLY
    )


def close_delta(
    target: v20.RTATask,
    w: int,
    x: int,
    delta_cap: int,
    beta: Sequence[float],
    e0: float,
    tasks: Sequence[v20.RTATask],
    processors: int,
    certified_bounds: Mapping[str, int],
    processor_delay_value: Optional[int] = None,
    workload_cache: Optional[
        Dict[Tuple[str, int, Optional[int]], int]
    ] = None,
    g_function: GFunction = local_g,
    closure_profile: Optional[V21ClosureProfile] = None,
) -> LocalClosureResult:
    """Find the minimum certified local offset within ``delta_cap``."""

    if delta_cap < 0:
        if closure_profile is not None:
            closure_profile.no_closure_count += 1
            closure_profile.delta_cap_exceeded_count += 1
        return LocalClosureResult(False, failure_reason="A_k^Theta(w) > w")
    if closure_profile is not None:
        closure_profile.max_delta_cap = max(
            closure_profile.max_delta_cap, delta_cap
        )
    pass_closure_profile = (
        closure_profile is not None
        and _accepts_closure_profile(g_function)
    )
    delta = 0
    while delta <= delta_cap:
        if closure_profile is not None:
            closure_profile.delta_iterations += 1
            closure_profile.max_delta_seen = max(
                closure_profile.max_delta_seen, delta
            )
        g_kwargs = {
            "processor_delay_value": processor_delay_value,
            "workload_cache": workload_cache,
        }
        if pass_closure_profile:
            g_kwargs["closure_profile"] = closure_profile
        result = g_function(
            target,
            w,
            x,
            delta,
            beta,
            e0,
            tasks,
            processors,
            certified_bounds,
            **g_kwargs,
        )
        if not result.feasible:
            delta += 1
            continue
        if result.value is None:
            if closure_profile is not None:
                closure_profile.no_closure_count += 1
            return LocalClosureResult(
                False, failure_reason=result.failure_reason
            )
        if result.value <= delta:
            if closure_profile is not None:
                closure_profile.closed_prefix_count += 1
            return LocalClosureResult(True, delta, result.value)
        next_delta = max(delta + 1, result.value)
        if closure_profile is not None and next_delta > delta + 1:
            closure_profile.delta_jump_count += 1
        delta = next_delta
    if closure_profile is not None:
        closure_profile.no_closure_count += 1
        closure_profile.delta_cap_exceeded_count += 1
    return LocalClosureResult(
        False,
        failure_reason="no local closure within candidate window",
    )


def local_energy_blocking_bound(
    target: v20.RTATask,
    w: int,
    beta: Sequence[float],
    e0: float,
    tasks: Sequence[v20.RTATask],
    processors: int,
    certified_bounds: Mapping[str, int],
    closure_profile: Optional[V21ClosureProfile] = None,
) -> LocalClosureResult:
    """Return ``B_k^{E,Theta,loc}(w)`` when every prefix closes."""

    reference_length, delay = processor_reference_length(
        target, w, processors, tasks, certified_bounds
    )
    if reference_length > w:
        if closure_profile is not None:
            closure_profile.no_closure_count += 1
            closure_profile.delta_cap_exceeded_count += 1
        return LocalClosureResult(False, failure_reason="A_k^Theta(w) > w")
    delta_cap = w - reference_length
    blocking = 0
    cache: Dict[Tuple[str, int, Optional[int]], int] = {}
    for x in range(1, reference_length + 1):
        closure = close_delta(
            target,
            w,
            x,
            delta_cap,
            beta,
            e0,
            tasks,
            processors,
            certified_bounds,
            processor_delay_value=delay,
            workload_cache=cache,
            closure_profile=closure_profile,
        )
        if not closure.closed:
            return closure
        blocking = max(blocking, int(closure.delta))
    return LocalClosureResult(True, blocking, blocking)


def response_time_bound_v21(
    target: v20.RTATask,
    tasks: Sequence[v20.RTATask],
    processors: int,
    beta: Sequence[float],
    e0: float = 0.0,
    assume_no_overflow: bool = False,
    certified_bounds: Optional[Mapping[str, int]] = None,
    profile_rta: bool = False,
) -> v20.TaskAnalysisResult:
    """Scan candidate windows and return the first v21 local closure."""

    certified = dict(certified_bounds or {})
    closure_profile = V21ClosureProfile(target.name) if profile_rta else None

    def make_result(
        bound: Optional[int],
        proven: bool,
        reason: Optional[str],
        iterations: int,
    ) -> v20.TaskAnalysisResult:
        return v20.TaskAnalysisResult(
            target.name,
            target.period,
            target.wcet,
            target.deadline,
            target.workload,
            target.energy_per_tick,
            bound,
            proven,
            reason,
            iterations,
            closure_profile.to_dict() if closure_profile is not None else None,
        )

    higher = v20.hp(tasks, target)
    missing = [task.name for task in higher if task.name not in certified]
    if missing:
        return make_result(
            None,
            False,
            "higher-priority tasks are not certified under {}: {}".format(
                RTA_VERSION, ", ".join(missing)
            ),
            0,
        )
    for task in higher:
        theta = int(certified[task.name])
        if theta < task.wcet or theta > task.deadline:
            return make_result(
                None,
                False,
                "invalid v21 certified carry-in for {}: {}".format(
                    task.name, theta
                ),
                0,
            )

    horizon = len(beta) - 1
    candidates_checked = 0
    last_reason = "no v21 local-window closure by the task deadline"
    for w in range(target.wcet, target.deadline + 1):
        if w > horizon:
            last_reason = "candidate response bound exceeds harvesting horizon"
            break
        candidates_checked += 1
        reference_length, _delay = processor_reference_length(
            target, w, processors, tasks, certified
        )
        if reference_length > w:
            if closure_profile is not None:
                closure_profile.no_closure_count += 1
                closure_profile.delta_cap_exceeded_count += 1
            last_reason = "A_k^Theta(w) > w"
            continue
        closure = local_energy_blocking_bound(
            target,
            w,
            beta,
            e0,
            tasks,
            processors,
            certified,
            closure_profile=closure_profile,
        )
        if not closure.closed:
            last_reason = closure.failure_reason or last_reason
            continue
        bound = reference_length + int(closure.delta)
        if bound > w:
            if closure_profile is not None:
                closure_profile.no_closure_count += 1
            last_reason = "local-window outer closure exceeds candidate w"
            continue
        if not assume_no_overflow:
            return make_result(
                w,
                False,
                "no-overflow assumption was not acknowledged",
                candidates_checked,
            )
        return make_result(
            w,
            True,
            None,
            candidates_checked,
        )

    return make_result(
        None,
        False,
        last_reason,
        candidates_checked,
    )


def analyze_taskset_v21(
    system_yml: str,
    tasks_yml: str,
    horizon_ms: int,
    assume_no_overflow: bool = False,
    harvest_trace: Optional[Sequence[float]] = None,
    initial_energy: float = 0.0,
    profile_rta: bool = False,
) -> V21TasksetAnalysis:
    """Run strict priority-ordered v21 taskset certification."""

    if horizon_ms is None or horizon_ms <= 0:
        raise v20.InputValidationError("--horizon-ms must be positive")
    config = v20.load_system_config(system_yml)
    if not math.isfinite(initial_energy) or initial_energy < 0:
        raise v20.InputValidationError(
            "--rta-initial-energy must be finite and non-negative"
        )
    if initial_energy > config.max_energy:
        raise v20.InputValidationError(
            "--rta-initial-energy cannot exceed max_energy={} J".format(
                config.max_energy
            )
        )
    tasks = v20.load_tasks(tasks_yml)
    taskset = tuple(tasks)
    for task in tasks:
        task.energy_per_tick = config.task_energy_per_tick(task.workload)
        task._taskset = taskset
        task._num_cores = config.num_cores
    v20._check_single_tick_capacity(tasks, config)
    trace = (
        list(harvest_trace)
        if harvest_trace is not None
        else v20._harvest_trace_from_config(config, horizon_ms)
    )
    beta = v20.build_energy_service_curve(trace, horizon_ms)

    results: List[v20.TaskAnalysisResult] = []
    certified: Dict[str, int] = {}
    for task in v20.rm_order(tasks):
        result = response_time_bound_v21(
            task,
            tasks,
            config.num_cores,
            beta,
            e0=initial_energy,
            assume_no_overflow=assume_no_overflow,
            certified_bounds=certified,
            profile_rta=profile_rta,
        )
        results.append(result)
        if result.proven and result.response_time_bound is not None:
            certified[task.name] = result.response_time_bound

    return V21TasksetAnalysis(
        os.path.abspath(system_yml),
        os.path.abspath(tasks_yml),
        horizon_ms,
        assume_no_overflow,
        float(initial_energy),
        results,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experimental v21-local-window sufficient RTA for ASAP-BLOCK"
        )
    )
    parser.add_argument("--system", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--horizon-ms", required=True, type=int)
    parser.add_argument("--assume-no-overflow", action="store_true")
    parser.add_argument(
        "--rta-initial-energy",
        type=float,
        default=0.0,
        help=(
            "absolute E0 lower bound in joules at every analyzed job "
            "release; independent of simulator --initial-energy"
        ),
    )
    parser.add_argument("--profile", "--profile-rta", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        report = analyze_taskset_v21(
            args.system,
            args.tasks,
            args.horizon_ms,
            assume_no_overflow=args.assume_no_overflow,
            initial_energy=args.rta_initial_energy,
            profile_rta=args.profile,
        )
    except v20.RTAError as exc:
        if args.json:
            payload = {
                "rta_version": RTA_VERSION,
                "error": str(exc),
            }
            payload.update(THEORY_METADATA)
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("error: {}".format(exc), file=sys.stderr)
        return 2

    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("ASAP-BLOCK {} conditional analysis".format(RTA_VERSION))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
