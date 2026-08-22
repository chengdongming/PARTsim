"""Small, reproducible RTA-LOAD-CROSS experiment runner.

This module is intentionally an experiment adapter.  The RTA decisions remain
in the existing v9.3 exact kernels and task-set dispatcher.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_methods as methods
import asap_block_rta_v9_3_taskset as taskset_api
from experiments.v9_3 import exact_energy
from experiments.v9_3.rta4_physical_core_slots_v3 import (
    PhysicalCoreSlotPoolV3,
    SlotCompletionV3,
    SlotStartedV3,
    SlotTaskV3,
    SlotTimeoutV3,
    SlotWorkerExitV3,
    discover_cpu_topology_v3,
)
from global_task_generator import EnergyAwareTaskGenerator


FROZEN_UC = tuple(Fraction(str(value)) for value in ("0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"))
FROZEN_UE_FIRST = tuple(Fraction(str(value)) for value in ("0.5", "0.8", "1.0"))
FROZEN_UE_SECOND = tuple(Fraction(str(value)) for value in ("0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "1.0"))
FROZEN_UC_SECOND = tuple(Fraction(str(value)) for value in ("0.3", "0.5", "0.7"))
FROZEN_WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")
METHOD_DISPLAY_TO_ID = {
    "CW": methods.V93MethodId.CW_THETA_CW,
    "LOC": methods.V93MethodId.LOC_THETA_LOC,
    "PH": methods.V93MethodId.PH_THETA_PH,
    "SEQ": methods.V93MethodId.SEQ_THETA_SEQ,
}
METHOD_ID_TO_DISPLAY = {value: key for key, value in METHOD_DISPLAY_TO_ID.items()}


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def parse_fraction(value: str | int | Fraction, label: str) -> Fraction:
    try:
        result = value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} must be an exact rational") from exc
    if result < 0:
        raise ValueError(f"{label} must be non-negative")
    return result


def frozen_cells() -> tuple[tuple[Fraction, Fraction], ...]:
    result: list[tuple[Fraction, Fraction]] = []
    for uc in FROZEN_UC:
        for ue in FROZEN_UE_FIRST:
            if (uc, ue) not in result:
                result.append((uc, ue))
    for uc in FROZEN_UC_SECOND:
        for ue in FROZEN_UE_SECOND:
            if (uc, ue) not in result:
                result.append((uc, ue))
    return tuple(result)


def parse_cells(text: str | None) -> tuple[tuple[Fraction, Fraction], ...]:
    if not text:
        return frozen_cells()
    cells: list[tuple[Fraction, Fraction]] = []
    for item in text.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"invalid cell {item!r}; expected U_C:U_E")
        cell = (parse_fraction(parts[0], "U_C"), parse_fraction(parts[1], "U_E"))
        if not (0 < cell[0] <= 1 and 0 < cell[1] <= 1):
            raise ValueError("U_C and U_E must be in the open/closed range (0, 1]")
        if cell not in cells:
            cells.append(cell)
    if not cells:
        raise ValueError("at least one cell is required")
    return tuple(cells)


def stable_seed(base_seed: int, processors: int, tasks: int, target_uc: Fraction, generation_index: int) -> int:
    material = f"{base_seed}|{processors}|{tasks}|{fraction_text(target_uc)}|{generation_index}"
    return int.from_bytes(hashlib.sha256(material.encode("ascii")).digest()[:8], "big")


def taskset_id(target_uc: Fraction, generation_index: int, target_ue: Fraction) -> str:
    def id_number(value: Fraction) -> str:
        return format(float(value), ".15g")
    return f"uc{id_number(target_uc)}-i{generation_index:04d}-ue{id_number(target_ue)}"


def _fraction_id(value: Fraction) -> str:
    return fraction_text(value).replace("-", "m").replace("/", "_")


def fixed_scale_taskset_id(
    target_uc: Fraction, generation_index: int, energy_scale: Fraction,
) -> str:
    return (
        f"fixed-scale-uc{_fraction_id(target_uc)}-i{generation_index:04d}"
        f"-k{_fraction_id(energy_scale)}"
    )


def request_id(taskset_identifier: str, e0: Fraction, method: str) -> str:
    return f"{taskset_identifier}-e0-{fraction_text(e0)}-{method}"


def static_counts(*, samples_per_uc: int = 500, e0_count: int = 2, method_count: int = 4, cells: int = 42, uc_count: int | None = None) -> dict[str, int]:
    unique_uc_count = len(FROZEN_UC) if uc_count is None else uc_count
    return {
        "cells": cells,
        "skeletons": unique_uc_count * samples_per_uc,
        "scaled_tasksets": cells * samples_per_uc,
        "requests": cells * samples_per_uc * e0_count * method_count,
    }


def _load_exact_energy_model(config_path: Path) -> dict[str, Fraction]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = config["energy_management"]["scheduler_energy_model"]
        base_power = Fraction(str(model["base_power"]))
        ratio = Fraction(str(model["frequency_power_ratios"][8100]))
        coefficients = model["workload_coefficients"]
        return {
            workload: base_power * Fraction(str(coefficients[workload])) * ratio
            for workload in FROZEN_WORKLOADS
        }
    except (KeyError, OSError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"cannot read exact scheduler energy model from {config_path}") from exc


def _task_json(task: Mapping[str, Any], base_energy: Fraction, energy: Fraction, priority: int) -> dict[str, Any]:
    c = task.get("C", task.get("runtime"))
    d = task.get("D", task.get("deadline"))
    t = task.get("T", task.get("iat"))
    return {
        "name": str(task["name"]),
        "priority": priority,
        "C": int(c),
        "D": int(d),
        "T": int(t),
        "workload": str(task["workload"]),
        "base_energy_per_tick": fraction_text(base_energy),
        "energy_per_tick": fraction_text(energy),
    }


def generate_cpu_skeleton(
    *, seed: int, target_uc: Fraction, processors: int, tasks: int,
    period_min: int, period_max: int, min_task_util: Fraction,
    max_task_util: Fraction, tolerance_total: Fraction, system_config: Path,
) -> tuple[dict[str, Any], ...]:
    generator = EnergyAwareTaskGenerator(
        seed=seed,
        system_config_path=str(system_config),
        task_workload_candidates=FROZEN_WORKLOADS,
    )
    generated, _resources, _dag, _energy = generator.generate_taskset(
        n=tasks,
        total_utilization=float(processors * target_uc),
        min_period=period_min,
        max_period=period_max,
        num_cpus=processors,
        implicit_deadline=False,
        dag_enabled=False,
        energy_aware=False,
        arrival_offset=False,
        min_task_util=float(min_task_util),
        max_task_util=float(max_task_util),
        wcet_rounding="compensated",
        actual_utilization_tolerance_total=float(tolerance_total),
    )
    ordered = sorted(generated, key=lambda row: (int(row["iat"]), int(row["name"].rsplit("_", 1)[1])))
    total = sum(Fraction(int(row["runtime"]), int(row["iat"])) for row in ordered)
    target_total = processors * target_uc
    if abs(total - target_total) > tolerance_total:
        raise ValueError(f"actual total utilization {total} exceeds target tolerance for U_C={target_uc}")
    result = []
    for priority, row in enumerate(ordered):
        c, d, t = int(row["runtime"]), int(row["deadline"]), int(row["iat"])
        if not (c <= d <= t):
            raise ValueError(f"invalid deadline relation for {row['name']}: {c}, {d}, {t}")
        if row["workload"] not in FROZEN_WORKLOADS:
            raise ValueError(f"unknown workload {row['workload']}")
        result.append({
            "name": str(row["name"]), "priority": priority, "C": c,
            "D": d, "T": t, "workload": str(row["workload"]),
        })
    return tuple(result)


def scale_skeleton(
    skeleton: Sequence[Mapping[str, Any]], *, target_uc: Fraction,
    target_ue: Fraction, generation_index: int, seed: int, processors: int = 1,
    rho: Fraction, base_energies: Mapping[str, Fraction],
) -> dict[str, Any]:
    base_rate = sum(
        Fraction(int(row["C"]), int(row["T"])) * base_energies[str(row["workload"])]
        for row in skeleton
    )
    if base_rate <= 0:
        raise ValueError("base energy rate must be positive")
    scale = target_ue * rho / base_rate
    tasks_json = tuple(
        _task_json(row, base_energies[str(row["workload"])], scale * base_energies[str(row["workload"])] , int(row["priority"]))
        for row in skeleton
    )
    actual_uc = sum(Fraction(int(row["C"]), int(row["T"])) for row in skeleton) / processors
    actual_ue = sum(
        Fraction(int(row["C"]), int(row["T"])) * parse_fraction(item["energy_per_tick"], "energy")
        for row, item in zip(skeleton, tasks_json)
    ) / rho
    if actual_ue != target_ue:
        raise ValueError("U_E exact scaling failed")
    return {
        "taskset_id": taskset_id(target_uc, generation_index, target_ue),
        "target_uc": fraction_text(target_uc), "actual_uc": fraction_text(actual_uc),
        "target_ue": fraction_text(target_ue), "actual_ue": fraction_text(actual_ue),
        "generation_index": generation_index, "seed": seed,
        "energy_scale": fraction_text(scale), "tasks": list(tasks_json),
    }


def scale_skeleton_fixed_energy_scale(
    skeleton: Sequence[Mapping[str, Any]], *, target_uc: Fraction,
    generation_index: int, seed: int, processors: int = 1,
    rho: Fraction, base_energies: Mapping[str, Fraction],
    energy_scale: Fraction,
) -> dict[str, Any]:
    """Scale task powers by one fixed exact kappa without targeting U_E."""
    target_uc = parse_fraction(target_uc, "target_uc")
    rho = parse_fraction(rho, "rho")
    energy_scale = parse_fraction(energy_scale, "energy_scale")
    if target_uc <= 0 or target_uc > 1:
        raise ValueError("target_uc must be in the open/closed range (0, 1]")
    if processors < 1:
        raise ValueError("processors must be positive")
    if rho <= 0:
        raise ValueError("rho must be positive")
    tasks_json = tuple(
        _task_json(
            row, base_energies[str(row["workload"])],
            energy_scale * base_energies[str(row["workload"])],
            int(row["priority"]),
        )
        for row in skeleton
    )
    actual_uc = sum(
        Fraction(int(row["C"]), int(row["T"])) for row in skeleton
    ) / processors
    actual_ue = sum(
        Fraction(int(row["C"]), int(row["T"]))
        * parse_fraction(item["energy_per_tick"], "energy")
        for row, item in zip(skeleton, tasks_json)
    ) / rho
    return {
        "taskset_id": fixed_scale_taskset_id(
            target_uc, generation_index, energy_scale,
        ),
        "energy_mode": "fixed_scale",
        "energy_scale": fraction_text(energy_scale),
        "target_uc": fraction_text(target_uc),
        "actual_uc": fraction_text(actual_uc),
        "target_ue": None,
        "actual_ue": fraction_text(actual_ue),
        "generation_index": generation_index,
        "seed": seed,
        "tasks": list(tasks_json),
    }


def prepare_fixed_scale_taskset(job: Mapping[str, Any]) -> dict[str, Any]:
    """Pure fixed-scale preparation unit used by the runner process pool."""
    uc = Fraction(job["target_uc"])
    index = int(job["generation_index"])
    seed = stable_seed(int(job["seed"]), int(job["processors"]), int(job["tasks"]), uc, index)
    skeleton = generate_cpu_skeleton(
        seed=seed, target_uc=uc, processors=int(job["processors"]),
        tasks=int(job["tasks"]), period_min=int(job["period_min"]),
        period_max=int(job["period_max"]),
        min_task_util=Fraction(job["min_task_util"]),
        max_task_util=Fraction(job["max_task_util"]),
        tolerance_total=Fraction(job["tolerance"]),
        system_config=Path(job["system_config"]),
    )
    taskset = scale_skeleton_fixed_energy_scale(
        skeleton, target_uc=uc, generation_index=index, seed=seed,
        processors=int(job["processors"]), rho=Fraction(job["rho"]),
        base_energies=job["base_energies"], energy_scale=Fraction(job["energy_scale"]),
    )
    return {"target_uc": fraction_text(uc), "generation_index": index, "taskset": taskset}


def prepare_load_cross_group(job: Mapping[str, Any]) -> dict[str, Any]:
    """Generate one skeleton and all its U_E-scaled immutable tasksets."""
    uc = Fraction(job["target_uc"])
    index = int(job["generation_index"])
    seed = stable_seed(int(job["seed"]), int(job["processors"]), int(job["tasks"]), uc, index)
    skeleton = generate_cpu_skeleton(
        seed=seed, target_uc=uc, processors=int(job["processors"]),
        tasks=int(job["tasks"]), period_min=int(job["period_min"]),
        period_max=int(job["period_max"]),
        min_task_util=Fraction(job["min_task_util"]),
        max_task_util=Fraction(job["max_task_util"]),
        tolerance_total=Fraction(job["tolerance"]),
        system_config=Path(job["system_config"]),
    )
    tasksets = [
        scale_skeleton(
            skeleton, target_uc=uc, target_ue=Fraction(ue),
            generation_index=index, seed=seed, processors=int(job["processors"]),
            rho=Fraction(job["rho"]), base_energies=job["base_energies"],
        )
        for ue in job["target_ues"]
    ]
    return {
        "target_uc": fraction_text(uc), "generation_index": index,
        "tasksets": tasksets,
    }


def _beta_values(tasks: Sequence[Mapping[str, Any]], rho: Fraction, latency: Fraction) -> tuple[Fraction, ...]:
    horizon = max(int(row["D"]) for row in tasks) - 1
    return tuple(rho * max(Fraction(delta) - latency, Fraction(0)) for delta in range(horizon + 1))


def _analysis_context(tasks: Sequence[core.V93Task], e0: Fraction, beta: Sequence[Fraction], taskset_identifier: str) -> taskset_api.DependencyContext:
    identity = exact_energy.exact_input_identity(
        task_powers=((task.name, task.power) for task in tasks),
        e0=e0,
        service_prefix=beta,
    )
    return taskset_api.DependencyContext(
        taskset_identity=taskset_identifier,
        task_definitions_identity=taskset_identifier,
        priority_order_identity=taskset_identifier,
        e0_canonical_identity=fraction_text(e0),
        service_curve_identity=hashlib.sha256(repr(tuple(beta)).encode("ascii")).hexdigest(),
        power_vector_identity=identity,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=taskset_api.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=taskset_api.FIXED_CARRY_IN_INTERFACE_SHA256,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=identity,
        float_decision_path=False,
    )


def _request_payload(taskset: Mapping[str, Any], e0: Fraction, method_name: str, processors: int, rho: Fraction, latency: Fraction, timeout: float) -> dict[str, Any]:
    tasks = tuple(core.V93Task(
        str(row["name"]), int(row["C"]), int(row["D"]), int(row["T"]), parse_fraction(row["energy_per_tick"], "energy_per_tick")
    ) for row in taskset["tasks"])
    beta = _beta_values(taskset["tasks"], rho, latency)
    result = {
        "taskset_id": str(taskset["taskset_id"]), "target_uc": taskset["target_uc"],
        "actual_uc": taskset["actual_uc"], "target_ue": taskset["target_ue"],
        "actual_ue": taskset["actual_ue"], "e0": fraction_text(e0),
        "method": method_name, "processors": processors, "tasks": tasks,
        "beta": beta, "timeout": timeout,
    }
    if "energy_mode" in taskset:
        result["energy_mode"] = taskset["energy_mode"]
        result["energy_scale"] = taskset["energy_scale"]
    return result


def _analyze_worker(_state: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    tasks = tuple(payload["tasks"])
    beta = tuple(payload["beta"])
    e0 = parse_fraction(str(payload["e0"]), "E0")
    timeout = float(payload["timeout"])
    context = _analysis_context(tasks, e0, beta, str(payload["taskset_id"]))
    analysis_input = taskset_api.TasksetAnalysisInput(
        tasks=tasks, processors=int(payload["processors"]), e0=e0, beta=beta,
        dependency_context=context, timeout_seconds=timeout,
    )
    method_id = METHOD_DISPLAY_TO_ID[str(payload["method"])]
    result = taskset_api.analyze_method_taskset_v9_3(
        analysis_id=str(payload["taskset_id"]) + "-" + str(payload["method"]),
        method_spec=method_id, analysis_input=analysis_input,
    )
    raw_status = result.solver_status.value
    if result.taskset_proven:
        final_status = "PROVEN"
    elif raw_status == "TIMEOUT":
        final_status = "UNPROVEN_TIMEOUT"
    elif raw_status == "NO_CANDIDATE":
        final_status = "NOT_PROVEN"
    elif raw_status == "NUMERIC_ERROR":
        final_status = "NUMERIC_ERROR"
    else:
        final_status = "INTERNAL_ERROR"
    result = {
        "taskset_id": payload["taskset_id"], "target_uc": payload["target_uc"],
        "actual_uc": payload["actual_uc"], "target_ue": payload["target_ue"],
        "actual_ue": payload["actual_ue"], "e0": payload["e0"],
        "method": payload["method"], "solver_status": raw_status,
        "final_status": final_status, "taskset_proven": bool(result.taskset_proven),
        "response_time_vector": [row.candidate_response_time for row in result.task_results],
        "failure_reason": result.failure_reason,
        "task_records": [
            {
                "task_id": row.task_id, "priority_rank": row.priority_rank,
                "solver_status": row.solver_status.value,
                "candidate_response_time": row.candidate_response_time,
                "closing_w": row.closing_w, "witness_h": row.witness_h,
                "checked_w_count": row.checked_w_count,
                "checked_h_count": row.checked_h_count,
                "checked_q_count": row.checked_q_count,
                "envelope_call_count": row.envelope_call_count,
                "runtime_wall": row.runtime_wall, "runtime_cpu": row.runtime_cpu,
                "failure_reason": row.failure_reason,
            } for row in result.task_results
        ],
        "retryable_timeout": final_status == "UNPROVEN_TIMEOUT",
    }
    if "energy_mode" in payload:
        result["energy_mode"] = payload["energy_mode"]
        result["energy_scale"] = payload["energy_scale"]
    return result


@dataclass
class _Pending:
    request: dict[str, Any]
    attempt_index: int
    timeout: float


def execute_requests(
    requests: Sequence[dict[str, Any]], *, workers: int, timeout_first: float,
    timeout_retry: float, on_result: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    topology = discover_cpu_topology_v3()
    selected = topology.select(workers)
    pool = PhysicalCoreSlotPoolV3(selected, worker_callable=_analyze_worker)
    pending = deque(_Pending(request, 0, timeout_first) for request in requests)
    active: dict[str, _Pending] = {}
    attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pool.start()
    try:
        while pending or pool.active_slot_count:
            for slot_id in pool.idle_slot_ids:
                if not pending:
                    break
                item = pending.popleft()
                task_id = item.request["request_id"]
                active[task_id] = item
                pool.submit(slot_id, task_id, item.request["payload"], item.timeout)
            event = pool.poll()
            if event is None or isinstance(event, SlotStartedV3):
                continue
            item = active.pop(event.task_id)
            if isinstance(event, SlotCompletionV3):
                result = dict(event.result) if event.result is not None else {
                    "final_status": "INTERNAL_ERROR", "failure_reason": event.error_classification,
                }
                wall = max(0.0, (event.finished_monotonic_ns - event.started_monotonic_ns) / 1_000_000_000)
                attempts[item.request["request_id"]].append({
                    "attempt_index": item.attempt_index,
                    "timeout_budget_seconds": item.timeout,
                    "solver_status": result.get("solver_status", "INTERNAL_ERROR"),
                    "wall_seconds": wall, "cpu_seconds": event.runtime_cpu_seconds,
                })
                if result.get("retryable_timeout") and item.attempt_index == 0:
                    pending.append(_Pending(item.request, 1, timeout_retry))
                    continue
                result["request_id"] = item.request["request_id"]
                result["attempts"] = attempts[item.request["request_id"]]
                result["final_attempt_wall_seconds"] = attempts[item.request["request_id"]][-1]["wall_seconds"]
                result["final_attempt_cpu_seconds"] = attempts[item.request["request_id"]][-1]["cpu_seconds"]
                result["total_wall_seconds"] = sum(row["wall_seconds"] for row in attempts[item.request["request_id"]])
                result["total_cpu_seconds"] = sum(row["cpu_seconds"] or 0.0 for row in attempts[item.request["request_id"]])
                on_result(result)
            elif isinstance(event, SlotTimeoutV3):
                attempts[item.request["request_id"]].append({
                    "attempt_index": item.attempt_index,
                    "timeout_budget_seconds": item.timeout,
                    "solver_status": "TIMEOUT",
                    "wall_seconds": item.timeout, "cpu_seconds": None,
                })
                pool.replace(event.slot_id, timeout_kill=True)
                if item.attempt_index == 0:
                    pending.append(_Pending(item.request, 1, timeout_retry))
                else:
                    result = dict(item.request["metadata"])
                    result.update({
                        "request_id": item.request["request_id"],
                        "solver_status": "TIMEOUT", "final_status": "UNPROVEN_TIMEOUT",
                        "taskset_proven": False, "response_time_vector": None,
                        "failure_reason": "hard timeout after retry",
                        "task_records": [], "attempts": attempts[item.request["request_id"]],
                        "final_attempt_wall_seconds": item.timeout,
                        "final_attempt_cpu_seconds": None,
                        "total_wall_seconds": sum(row["wall_seconds"] for row in attempts[item.request["request_id"]]),
                        "total_cpu_seconds": None,
                    })
                    on_result(result)
            elif isinstance(event, SlotWorkerExitV3):
                pool.replace(event.slot_id)
                result = dict(item.request["metadata"])
                result.update({
                    "request_id": item.request["request_id"], "solver_status": "INTERNAL_ERROR",
                    "final_status": "INTERNAL_ERROR", "taskset_proven": False,
                    "response_time_vector": None, "failure_reason": f"worker exited with code {event.exitcode}",
                    "task_records": [], "attempts": attempts[item.request["request_id"]],
                    "final_attempt_wall_seconds": None, "final_attempt_cpu_seconds": None,
                    "total_wall_seconds": None, "total_cpu_seconds": None,
                })
                on_result(result)
        return {
            "topology": topology.as_dict(),
            "worker_affinity_bindings": list(pool.worker_affinity_bindings),
            "worker_intervals": list(pool.worker_intervals),
            "slot_replacement_count": pool.slot_replacement_count,
            "timeout_kill_count": pool.timeout_kill_count,
        }
    finally:
        pool.shutdown()


def make_requests(tasksets: Iterable[Mapping[str, Any]], e0_values: Sequence[Fraction], method_names: Sequence[str], processors: int, rho: Fraction, latency: Fraction, timeout_first: float) -> list[dict[str, Any]]:
    requests = []
    for taskset in tasksets:
        for e0 in e0_values:
            for method_name in method_names:
                identifier = request_id(str(taskset["taskset_id"]), e0, method_name)
                metadata = {key: taskset[key] for key in ("taskset_id", "target_uc", "actual_uc", "target_ue", "actual_ue")}
                if "energy_mode" in taskset:
                    metadata["energy_mode"] = taskset["energy_mode"]
                    metadata["energy_scale"] = taskset["energy_scale"]
                metadata.update({"e0": fraction_text(e0), "method": method_name})
                requests.append({
                    "request_id": identifier, "metadata": metadata,
                    "payload": _request_payload(taskset, e0, method_name, processors, rho, latency, timeout_first),
                })
    return requests


def export_core3_tasksets(tasksets: Sequence[Mapping[str, Any]], output_path: Path) -> int:
    selected = [row for row in tasksets if row["target_ue"] == "4/5" and Fraction(row["target_uc"]) in FROZEN_UC and int(row["generation_index"]) < 100]
    with output_path.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return len(selected)


__all__ = [
    "FROZEN_UC", "FROZEN_WORKLOADS", "METHOD_DISPLAY_TO_ID", "execute_requests",
    "export_core3_tasksets", "fraction_text", "frozen_cells", "generate_cpu_skeleton",
    "fixed_scale_taskset_id", "make_requests", "parse_cells", "parse_fraction",
    "prepare_fixed_scale_taskset", "prepare_load_cross_group", "request_id",
    "scale_skeleton", "scale_skeleton_fixed_energy_scale",
    "stable_seed", "static_counts", "taskset_id",
]
