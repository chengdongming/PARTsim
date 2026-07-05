#!/usr/bin/env python3
"""Isolated E3 A0-A2 RTA ablation variants.

This module intentionally does not modify the official v20.4 analyzer.  It
shares parsing, task modeling, harvesting service-curve, and basic workload
helpers with ``asap_block_rta.py``, but keeps the A0-A2 component-level policy
switches isolated for E3 experiments.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import asap_block_rta as v20


@dataclass(frozen=True)
class VariantPolicy:
    variant_name: str
    variant_label: str
    variant_group: str
    variant_safety_label: str
    proof_claim_eligible: bool
    diagnostic_only: bool
    uses_certified_carry_in: bool
    uses_processor_capacity_coupling: bool
    uses_window_level_task_capacity: bool
    uses_window_level_u_capacity: bool
    uses_local_window: bool
    certificate_policy: str
    expected_rta_version: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


VARIANT_POLICIES: Dict[str, VariantPolicy] = {
    "baseline_safe": VariantPolicy(
        variant_name="baseline_safe",
        variant_label="A0 baseline safe",
        variant_group="safe_chain",
        variant_safety_label="safe_under_v20p4_assumptions",
        proof_claim_eligible=True,
        diagnostic_only=False,
        uses_certified_carry_in=False,
        uses_processor_capacity_coupling=False,
        uses_window_level_task_capacity=False,
        uses_window_level_u_capacity=False,
        uses_local_window=False,
        certificate_policy="none_deadline_workload",
        expected_rta_version="e3-a0-baseline-safe",
    ),
    "carry_in_certified": VariantPolicy(
        variant_name="carry_in_certified",
        variant_label="A1 certified carry-in",
        variant_group="safe_chain",
        variant_safety_label="safe_under_v20p4_assumptions",
        proof_claim_eligible=True,
        diagnostic_only=False,
        uses_certified_carry_in=True,
        uses_processor_capacity_coupling=False,
        uses_window_level_task_capacity=False,
        uses_window_level_u_capacity=False,
        uses_local_window=False,
        certificate_policy="strict_variant_specific",
        expected_rta_version="e3-a1-carry-in-certified",
    ),
    "capacity_coupled": VariantPolicy(
        variant_name="capacity_coupled",
        variant_label="A2 capacity coupled",
        variant_group="safe_chain",
        variant_safety_label="safe_under_v20p4_assumptions",
        proof_claim_eligible=True,
        diagnostic_only=False,
        uses_certified_carry_in=True,
        uses_processor_capacity_coupling=True,
        uses_window_level_task_capacity=False,
        uses_window_level_u_capacity=False,
        uses_local_window=False,
        certificate_policy="strict_variant_specific",
        expected_rta_version="e3-a2-capacity-coupled",
    ),
}

RUNNABLE_VARIANTS = tuple(VARIANT_POLICIES)


@dataclass
class AblationProfile:
    task_id: str
    total_time_sec: float = 0.0
    fixed_point_iterations: int = 0
    processor_delay_time_sec: float = 0.0
    energy_blocking_time_sec: float = 0.0
    energy_state_time_sec: float = 0.0
    beta_inverse_time_sec: float = 0.0
    x_values_checked: int = 0
    z_values_checked: int = 0
    energy_state_calls: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AblationTaskResult:
    task_name: str
    period: int
    wcet: int
    deadline: int
    workload: str
    energy_per_tick: float
    schedulable: bool
    response_time_bound: Optional[int]
    rta_status: str
    certificate_status: str
    proof_claim_allowed: bool
    proof_claim_succeeded: bool
    failure_reason: Optional[str] = None
    iterations: int = 0
    rta_profile: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "task_name": self.task_name,
            "period": self.period,
            "wcet": self.wcet,
            "deadline": self.deadline,
            "workload": self.workload,
            "energy_per_tick": self.energy_per_tick,
            "schedulable": self.schedulable,
            "proven_under_assumptions": self.schedulable,
            "response_time_bound": self.response_time_bound,
            "rta_status": self.rta_status,
            "certificate_status": self.certificate_status,
            "proof_claim_allowed": self.proof_claim_allowed,
            "proof_claim_succeeded": self.proof_claim_succeeded,
            "failure_reason": self.failure_reason,
            "iterations": self.iterations,
        }
        if self.rta_profile is not None:
            result["rta_profile"] = self.rta_profile
        return result


@dataclass
class AblationTasksetResult:
    system_file: str
    tasks_file: str
    horizon_ms: int
    assume_no_overflow: bool
    e0: float
    policy: VariantPolicy
    tasks: List[AblationTaskResult]

    @property
    def certificate_status(self) -> str:
        if not self.tasks:
            return "no_tasks"
        statuses = {task.certificate_status for task in self.tasks}
        if "certificate_missing" in statuses:
            return "certificate_missing"
        if "invalid_certificate" in statuses:
            return "invalid_certificate"
        if statuses == {"not_required"}:
            return "not_required"
        return "available"

    @property
    def proof_claim_allowed(self) -> bool:
        return bool(self.tasks) and all(task.proof_claim_allowed for task in self.tasks)

    @property
    def proof_claim_succeeded(self) -> bool:
        return bool(self.tasks) and all(
            task.proof_claim_succeeded for task in self.tasks
        )

    @property
    def schedulable(self) -> bool:
        return self.proof_claim_succeeded

    @property
    def response_time_bound(self) -> Optional[int]:
        bounds = [
            task.response_time_bound
            for task in self.tasks
            if task.response_time_bound is not None
        ]
        return max(bounds) if bounds else None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "rta_version": self.policy.expected_rta_version,
            "schedulable": self.schedulable,
            "proven_under_assumptions": self.proof_claim_succeeded,
            "conditional": True,
            "absolute_schedulability_claim": False,
            "response_time_bound": self.response_time_bound,
            "system_file": self.system_file,
            "tasks_file": self.tasks_file,
            "horizon_ms": self.horizon_ms,
            "assume_no_overflow": self.assume_no_overflow,
            "E0": self.e0,
            "proof_claim_allowed": self.proof_claim_allowed,
            "proof_claim_succeeded": self.proof_claim_succeeded,
            "certificate_status": self.certificate_status,
            "tasks": [task.to_dict() for task in self.tasks],
        }
        result.update(self.policy.to_dict())
        return result


def get_policy(variant_name: str) -> VariantPolicy:
    try:
        return VARIANT_POLICIES[variant_name]
    except KeyError:
        raise ValueError("unknown ablation variant: {}".format(variant_name))


def _theta_for_task(
    policy: VariantPolicy,
    task: v20.RTATask,
    certified_bounds: Mapping[str, int],
) -> Optional[int]:
    if not policy.uses_certified_carry_in:
        return None
    return int(certified_bounds[task.name])


def _high_workload_bound(
    policy: VariantPolicy,
    task: v20.RTATask,
    w: int,
    certified_bounds: Mapping[str, int],
) -> int:
    return v20.workload_bound(
        task,
        w,
        _theta_for_task(policy, task, certified_bounds),
    )


def _processor_workloads(
    policy: VariantPolicy,
    target: v20.RTATask,
    w: int,
    taskset: Sequence[v20.RTATask],
    certified_bounds: Mapping[str, int],
) -> Dict[str, int]:
    interference_cap = max(w - target.wcet + 1, 0)
    return {
        task.name: min(
            _high_workload_bound(policy, task, w, certified_bounds),
            interference_cap,
        )
        for task in v20.hp(taskset, target)
    }


def processor_delay_variant(
    policy: VariantPolicy,
    target: v20.RTATask,
    w: int,
    M: int,
    taskset: Sequence[v20.RTATask],
    certified_bounds: Optional[Mapping[str, int]] = None,
) -> int:
    if M <= 0:
        raise ValueError("M must be positive")
    certified = dict(certified_bounds or {})
    bars = list(_processor_workloads(policy, target, w, taskset, certified).values())
    if not bars:
        return 0
    maximum_candidate = sum(bars) // M
    maximum_delay = 0
    for delay in range(maximum_candidate + 1):
        if sum(min(value, delay) for value in bars) >= M * delay:
            maximum_delay = delay
    return maximum_delay


def _uncoupled_energy_states_for_z(
    policy: VariantPolicy,
    target: v20.RTATask,
    taskset: Sequence[v20.RTATask],
    w: int,
    x: int,
    z: int,
    M: int,
    certified_bounds: Mapping[str, int],
) -> List[Tuple[int, Fraction]]:
    high = v20.hp(taskset, target)
    low = v20._lp(taskset, target)
    high_workloads = {
        task.name: _high_workload_bound(policy, task, w, certified_bounds)
        for task in high
    }
    processor_workloads = _processor_workloads(
        policy, target, w, taskset, certified_bounds
    )
    a_capacity = M * (x - z)
    if sum(
        min(high_workloads[task.name], processor_workloads[task.name])
        for task in high
    ) < a_capacity:
        return []
    low_workloads = {
        task.name: v20.workload_bound(task, w)
        for task in low
    }
    # Without b/u capacity coupling, every HP unit not forced into a can be u.
    u_total = sum(high_workloads.values()) - a_capacity
    energy = Fraction(z) * v20._energy_fraction(target)
    energy += sum(
        high_workloads[task.name] * v20._energy_fraction(task)
        for task in high
    )
    energy += sum(
        low_workloads[task.name] * v20._energy_fraction(task)
        for task in low
    )
    return [(u_total, energy)]


def _coupled_energy_states_for_z(
    policy: VariantPolicy,
    target: v20.RTATask,
    taskset: Sequence[v20.RTATask],
    w: int,
    x: int,
    z: int,
    M: int,
    certified_bounds: Mapping[str, int],
) -> List[Tuple[int, Fraction]]:
    if x == 0:
        return [(0, Fraction(0))] if z == 0 else []

    high = v20.hp(taskset, target)
    low = v20._lp(taskset, target)
    a_capacity = M * (x - z)
    high_workloads = {
        task.name: _high_workload_bound(policy, task, w, certified_bounds)
        for task in high
    }
    processor_workloads = _processor_workloads(
        policy, target, w, taskset, certified_bounds
    )
    a_caps = {
        task.name: min(
            high_workloads[task.name],
            processor_workloads[task.name],
            x - z,
        )
        for task in high
    }
    if sum(a_caps.values()) < a_capacity:
        return []

    b_capacity = (M - 1) * z
    low_caps = {
        task.name: min(v20.workload_bound(task, w), z)
        for task in low
    }

    physical_energy_limit = z * v20._energy_fraction(target)
    physical_energy_limit += sum(
        high_workloads[task.name] * v20._energy_fraction(task)
        for task in high
    )
    physical_energy_limit += sum(
        low_caps[task.name] * v20._energy_fraction(task)
        for task in low
    )
    mandatory_bonus = physical_energy_limit + 1

    high_count = len(high)
    low_count = len(low)
    source = 0
    first_high = 1
    first_low = first_high + high_count
    a_bin = first_low + low_count
    u_bin = a_bin + 1
    b_bin = u_bin + 1
    sink = b_bin + 1
    graph: List[List[v20._FlowEdge]] = [[] for _ in range(sink + 1)]

    for index, task in enumerate(high):
        node = first_high + index
        capacity = high_workloads[task.name]
        energy = v20._energy_fraction(task)
        v20._add_flow_edge(graph, source, node, capacity, Fraction(0))
        v20._add_flow_edge(
            graph, node, a_bin, a_caps[task.name], -(energy + mandatory_bonus)
        )
        # A2 deliberately has no window-level u capacity, so the sink capacity
        # is bounded only by available high-priority workload.
        v20._add_flow_edge(graph, node, u_bin, capacity, -energy)
        v20._add_flow_edge(graph, node, b_bin, min(z, capacity), -energy)
    for index, task in enumerate(low):
        node = first_low + index
        capacity = low_caps[task.name]
        energy = v20._energy_fraction(task)
        v20._add_flow_edge(graph, source, node, capacity, Fraction(0))
        v20._add_flow_edge(graph, node, b_bin, capacity, -energy)

    a_edge_index = v20._add_flow_edge(graph, a_bin, sink, a_capacity, Fraction(0))
    u_capacity = sum(high_workloads.values())
    v20._add_flow_edge(graph, u_bin, sink, u_capacity, Fraction(0))
    v20._add_flow_edge(graph, b_bin, sink, b_capacity, Fraction(0))
    _flow, flow_cost, _augmentations = v20._min_cost_max_flow(graph, source, sink)
    a_flow = a_capacity - graph[a_bin][a_edge_index].capacity
    if a_flow != a_capacity:
        return []

    u_total = min(u_capacity, sum(high_workloads.values()) - a_capacity)
    energy_total = (
        z * v20._energy_fraction(target)
        - flow_cost
        - a_capacity * mandatory_bonus
    )
    return [(u_total, energy_total)]


def energy_states_for_z(
    policy: VariantPolicy,
    target: v20.RTATask,
    taskset: Sequence[v20.RTATask],
    w: int,
    x: int,
    z: int,
    M: int,
    certified_bounds: Optional[Mapping[str, int]] = None,
) -> List[Tuple[int, Fraction]]:
    certified = dict(certified_bounds or {})
    if not policy.uses_processor_capacity_coupling:
        return _uncoupled_energy_states_for_z(
            policy, target, taskset, w, x, z, M, certified
        )
    return _coupled_energy_states_for_z(
        policy, target, taskset, w, x, z, M, certified
    )


def _energy_blocking_bound_result(
    policy: VariantPolicy,
    target: v20.RTATask,
    w: int,
    beta: Sequence[float],
    E0: float,
    taskset: Sequence[v20.RTATask],
    M: int,
    certified_bounds: Mapping[str, int],
    profile: Optional[AblationProfile] = None,
) -> v20._EnergyBlockingResult:
    delay = processor_delay_variant(policy, target, w, M, taskset, certified_bounds)
    reference_length = target.wcet + delay
    if reference_length <= 0:
        return v20._EnergyBlockingResult(0)

    blocking = 0
    saw_any_state = False
    exact_e0 = v20._exact_fraction(E0)
    for x in range(1, reference_length + 1):
        if profile is not None:
            profile.x_values_checked += 1
        z_min = max(0, x - delay)
        z_max = min(target.wcet, x)
        saw_state_for_x = False
        for z in range(z_min, z_max + 1):
            if profile is not None:
                profile.z_values_checked += 1
                profile.energy_state_calls += 1
            states_started = time.perf_counter()
            states = energy_states_for_z(
                policy, target, taskset, w, x, z, M, certified_bounds
            )
            if profile is not None:
                profile.energy_state_time_sec += time.perf_counter() - states_started
            if states:
                saw_state_for_x = True
                saw_any_state = True
            for u_total, energy in states:
                demand = max(energy - exact_e0, Fraction(0))
                beta_started = time.perf_counter()
                delta = v20.beta_inverse(beta, demand)
                if profile is not None:
                    profile.beta_inverse_time_sec += time.perf_counter() - beta_started
                if delta is None:
                    return v20._EnergyBlockingResult(
                        None, "finite energy service is insufficient"
                    )
                blocking = max(blocking, max(u_total, max(delta - x, 0)))
        # Unreachable reference prefixes do not participate in the maximum.
        if not saw_state_for_x:
            continue
    if not saw_any_state:
        return v20._EnergyBlockingResult(
            None, "energy-state set is empty for every reference prefix"
        )
    return v20._EnergyBlockingResult(blocking)


def _check_certificates(
    policy: VariantPolicy,
    target: v20.RTATask,
    taskset: Sequence[v20.RTATask],
    certified_bounds: Mapping[str, int],
) -> Optional[str]:
    if not policy.uses_certified_carry_in:
        return None
    missing = [
        task.name for task in v20.hp(taskset, target)
        if task.name not in certified_bounds
    ]
    if missing:
        return "missing higher-priority certificates: {}".format(
            ", ".join(missing)
        )
    for task in v20.hp(taskset, target):
        theta = int(certified_bounds[task.name])
        if theta < task.wcet or theta > task.deadline:
            return "invalid certificate for {}: {}".format(task.name, theta)
    return None


def response_time_bound_variant(
    policy: VariantPolicy,
    target: v20.RTATask,
    tasks: Sequence[v20.RTATask],
    M: int,
    beta: Sequence[float],
    E0: float = 0,
    max_iterations: int = 1000,
    assume_no_overflow: bool = False,
    profile: bool = False,
    certified_bounds: Optional[Mapping[str, int]] = None,
) -> AblationTaskResult:
    analysis_started = time.perf_counter()
    certified = dict(certified_bounds or {})
    rta_profile = AblationProfile(target.name) if profile else None

    def make_result(
        bound: Optional[int],
        schedulable: bool,
        status: str,
        certificate_status: str,
        proof_allowed: bool,
        reason: Optional[str],
        iterations: int,
    ) -> AblationTaskResult:
        if rta_profile is not None:
            rta_profile.fixed_point_iterations = iterations
            rta_profile.total_time_sec = time.perf_counter() - analysis_started
        proof_succeeded = bool(
            schedulable and proof_allowed and policy.proof_claim_eligible
        )
        return AblationTaskResult(
            target.name,
            target.period,
            target.wcet,
            target.deadline,
            target.workload,
            target.energy_per_tick,
            bool(schedulable),
            bound,
            status,
            certificate_status,
            bool(proof_allowed),
            proof_succeeded,
            reason,
            iterations,
            rta_profile.to_dict() if rta_profile is not None else None,
        )

    if M <= 0:
        raise ValueError("M must be positive")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    certificate_error = _check_certificates(policy, target, tasks, certified)
    if certificate_error:
        certificate_status = (
            "invalid_certificate"
            if certificate_error.startswith("invalid")
            else "certificate_missing"
        )
        return make_result(
            None,
            False,
            certificate_status,
            certificate_status,
            False,
            certificate_error,
            0,
        )
    certificate_status = (
        "available" if policy.uses_certified_carry_in else "not_required"
    )

    harvesting_horizon = len(beta) - 1
    if target.wcet > harvesting_horizon:
        return make_result(
            target.wcet,
            False,
            "rta_unproven",
            certificate_status,
            True,
            "response bound exceeds harvesting horizon",
            0,
        )

    current = target.wcet
    for iteration in range(1, max_iterations + 1):
        processor_started = time.perf_counter()
        cpu_delay = processor_delay_variant(
            policy, target, current, M, tasks, certified
        )
        if rta_profile is not None:
            rta_profile.processor_delay_time_sec += (
                time.perf_counter() - processor_started
            )
        energy_started = time.perf_counter()
        energy_result = _energy_blocking_bound_result(
            policy, target, current, beta, E0, tasks, M, certified, rta_profile
        )
        if rta_profile is not None:
            rta_profile.energy_blocking_time_sec += (
                time.perf_counter() - energy_started
            )
        if energy_result.blocking is None:
            return make_result(
                None,
                False,
                "rta_unproven",
                certificate_status,
                True,
                energy_result.failure_reason,
                iteration,
            )
        next_value = target.wcet + cpu_delay + energy_result.blocking
        if next_value > harvesting_horizon:
            return make_result(
                next_value,
                False,
                "rta_unproven",
                certificate_status,
                True,
                "response bound exceeds harvesting horizon",
                iteration,
            )
        if next_value > target.deadline:
            return make_result(
                next_value,
                False,
                "rta_unproven",
                certificate_status,
                True,
                "response-time bound exceeds the task deadline",
                iteration,
            )
        if next_value == current:
            if not assume_no_overflow:
                return make_result(
                    next_value,
                    False,
                    "rta_unproven",
                    certificate_status,
                    True,
                    "no-overflow assumption was not acknowledged",
                    iteration,
                )
            return make_result(
                next_value,
                True,
                "proven_under_assumptions",
                certificate_status,
                True,
                None,
                iteration,
            )
        if next_value < current:
            return make_result(
                next_value,
                False,
                "rta_unproven",
                certificate_status,
                True,
                "fixed-point iteration became non-monotonic",
                iteration,
            )
        current = next_value

    return make_result(
        current,
        False,
        "rta_unproven",
        certificate_status,
        True,
        "fixed-point iteration limit {} exceeded".format(max_iterations),
        max_iterations,
    )


def analyze_taskset_variant(
    system_yml: str,
    tasks_yml: str,
    variant_name: str,
    horizon_ms: int,
    initial_energy: float = 0.0,
    assume_no_overflow: bool = False,
    harvest_trace: Optional[Sequence[float]] = None,
    max_iterations: int = 1000,
    profile: bool = False,
) -> AblationTasksetResult:
    if horizon_ms <= 0:
        raise v20.InputValidationError("--horizon-ms must be positive")
    if not math.isfinite(initial_energy) or initial_energy < 0:
        raise v20.InputValidationError(
            "--initial-energy must be finite and non-negative"
        )
    policy = get_policy(variant_name)
    config = v20.load_system_config(system_yml)
    if initial_energy > config.max_energy:
        raise v20.InputValidationError(
            "--initial-energy cannot exceed max_energy={} J".format(
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

    results: List[AblationTaskResult] = []
    certified_bounds: Optional[Dict[str, int]] = (
        {} if policy.uses_certified_carry_in else None
    )
    for task in v20.rm_order(tasks):
        result = response_time_bound_variant(
            policy,
            task,
            tasks,
            config.num_cores,
            beta,
            E0=initial_energy,
            max_iterations=max_iterations,
            assume_no_overflow=assume_no_overflow,
            profile=profile,
            certified_bounds=certified_bounds,
        )
        results.append(result)
        if (
            certified_bounds is not None
            and result.proof_claim_succeeded
            and result.response_time_bound is not None
            and result.response_time_bound <= task.deadline
        ):
            certified_bounds[task.name] = result.response_time_bound

    return AblationTasksetResult(
        os.path.abspath(system_yml),
        os.path.abspath(tasks_yml),
        horizon_ms,
        assume_no_overflow,
        float(initial_energy),
        policy,
        results,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated E3 A0-A2 safe-chain RTA ablation variants"
    )
    parser.add_argument("--tasks", required=True, help="taskset YAML file")
    parser.add_argument(
        "--config",
        "--system",
        dest="config",
        required=True,
        help="system YAML file",
    )
    parser.add_argument("--variant", required=True, choices=RUNNABLE_VARIANTS)
    parser.add_argument("--horizon-ms", required=True, type=int)
    parser.add_argument(
        "--initial-energy",
        "--rta-initial-energy",
        dest="initial_energy",
        type=float,
        default=0.0,
    )
    parser.add_argument("--assume-no-overflow", action="store_true")
    parser.add_argument("--profile", "--profile-rta", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = analyze_taskset_variant(
            args.config,
            args.tasks,
            args.variant,
            args.horizon_ms,
            initial_energy=args.initial_energy,
            assume_no_overflow=args.assume_no_overflow,
            profile=args.profile,
        )
    except (v20.RTAError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
