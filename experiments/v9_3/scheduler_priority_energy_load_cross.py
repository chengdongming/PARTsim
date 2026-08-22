"""Priority-energy correlated Scheduler LOAD-CROSS material and pairing.

This module is deliberately separate from the ordinary Scheduler LOAD-CROSS
adapter.  It reuses only the frozen timing generator/store and applies the
priority-energy projection as an immutable experiment-level materialization.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import asap_block_rta as legacy_rta

from . import perf_g
from . import scheduler_load_cross as ordinary
from .config import canonical_json, fraction_text
from .rta4_core3_contracts_v7 import canonical_binary64_decimal_v7
from .simulation_engine import construct_paired_harvest_trace
from .taskset_store import task_demand_for_wcet


DOMAIN = "ASAP_BLOCK:V9.3:SCHEDULER_PRIORITY_ENERGY_LOAD_CROSS:v1"
DEFAULT_RATIOS = (Fraction(1), Fraction(2))
REFERENCE_RATIO = Fraction(2)
DEFAULT_KAPPA = Fraction(10)
PROCESSORS = 4
TASK_COUNT = 10
NORMALIZATION_HORIZON = perf_g.FORMAL_HORIZON_MS
DEFAULT_CELLS = ordinary.DEFAULT_CELLS
DEFAULT_SCHEDULERS = tuple(perf_g.CAL_SCHEDULERS)
ALL_SCHEDULERS = ordinary.ALL_SCHEDULERS
SCHEDULER_CLI = perf_g.SCHEDULER_CLI


@dataclass(frozen=True)
class PriorityTaskset:
    base: Any
    task_payload: tuple[Mapping[str, Any], ...]
    base_hash: str  # projected all-hash taskset hash; retained as a compatibility alias
    priority_hash: str
    source_taskset_hash: str = ""

    @property
    def projected_taskset_hash(self) -> str:
        return self.base_hash

    @property
    def source_taskset_id(self) -> str:
        return str(self.base.taskset_id)


def _hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        (DOMAIN + ":" + domain).encode("ascii")
        + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def parse_fraction(value: Any, label: str, *, positive: bool = True) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be exact")
    try:
        result = value if isinstance(value, Fraction) else Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} must be exact") from exc
    if (positive and result <= 0) or (not positive and result < 0):
        raise ValueError(f"{label} has invalid sign")
    return result


def parse_ratios(text: str | None) -> tuple[Fraction, ...]:
    if not text:
        return DEFAULT_RATIOS
    values = tuple(parse_fraction(item.strip(), "priority-energy ratio") for item in text.split(","))
    if not values or len(set(values)) != len(values):
        raise ValueError("priority-energy ratios must be non-empty and unique")
    if any(value < 1 for value in values):
        raise ValueError("priority-energy ratios must be at least one")
    if REFERENCE_RATIO not in values:
        raise ValueError("priority-energy ratios must include reference ratio 2")
    return values


def parse_cells(text: str | None) -> tuple[tuple[Fraction, Fraction], ...]:
    return ordinary.parse_cells(text)


def resolve_figure_slices(
    cells: Sequence[tuple[Fraction, Fraction]], *,
    fixed_ue: Fraction | None = None, fixed_uc: Fraction | None = None,
) -> dict[str, dict[str, str]]:
    return ordinary.resolve_figure_slices(
        cells, fixed_ue=fixed_ue, fixed_uc=fixed_uc,
    )


def parse_schedulers(text: str | None) -> tuple[str, ...]:
    if not text:
        return DEFAULT_SCHEDULERS
    values = tuple(item.strip() for item in text.split(","))
    if not values or len(set(values)) != len(values) or any(not item for item in values):
        raise ValueError("scheduler list must be non-empty and unique")
    unknown = sorted(set(values) - set(ALL_SCHEDULERS))
    if unknown:
        raise ValueError(f"unknown scheduler(s): {', '.join(unknown)}")
    return values


def eta_for_ue(target_ue: Fraction) -> Fraction:
    return ordinary.eta_for_ue(target_ue)


def hash_task_payload(taskset: Any, system_path: Path) -> tuple[Mapping[str, Any], ...]:
    """Project the frozen timing taskset onto the single hash workload."""
    system = legacy_rta.load_system_config(str(system_path))
    rows = []
    for row in taskset.task_payload:
        task = dict(row)
        task["workload"] = "hash"
        task["P"] = fraction_text(task_demand_for_wcet(
            system, "hash", int(task["C"]),
            label=f"priority-energy task {task['task_id']} hash demand",
        ))
        rows.append(task)
    return tuple(rows)


def materialize_tasksets(root: Path, *, seed: int, utilizations: Sequence[Fraction],
                         count: int, processors: int, tasks: int,
                         period_min: int, period_max: int,
                         min_task_util: Fraction, max_task_util: Fraction,
                         tolerance: Fraction, prepare_workers: int = 1) -> tuple[list[PriorityTaskset], Any]:
    """Create one paired hash-workload projection for every base timing set."""
    base_root = root / "base_timing"
    base_sets, service = ordinary.materialize_tasksets(
        base_root, seed=seed, utilizations=utilizations, count=count,
        processors=processors, tasks=tasks, period_min=period_min,
        period_max=period_max, min_task_util=min_task_util,
        max_task_util=max_task_util, tolerance=tolerance,
        prepare_workers=prepare_workers,
    )
    result = []
    for base in base_sets:
        payload = hash_task_payload(base, service.system_path)
        if len(payload) != tasks or any(row["workload"] != "hash" for row in payload):
            raise ValueError("priority-energy base taskset is not all-hash")
        projected_hash = _hash("PROJECTED_TASKSET", {
            "source_taskset_id": base.taskset_id,
            "source_taskset_hash": base.semantic_hash,
            "tasks": payload,
        })
        priority_hash = _hash(
            "PRIORITY_VECTOR",
            [{"task_id": row["task_id"], "priority_rank": row["priority_rank"]}
             for row in payload],
        )
        result.append(PriorityTaskset(
            base, payload, projected_hash, priority_hash, base.semantic_hash,
        ))
    return result, service


def _ranked(taskset: PriorityTaskset) -> tuple[Mapping[str, Any], ...]:
    rows = tuple(taskset.task_payload)
    ranks = [int(row["priority_rank"]) for row in rows]
    if ranks != list(range(len(rows))):
        raise ValueError("priority ranks must be contiguous and canonical")
    if len({str(row["task_id"]) for row in rows}) != len(rows):
        raise ValueError("priority-energy task IDs must be unique")
    return rows


def _group_demands(taskset: PriorityTaskset) -> tuple[Fraction, Fraction]:
    rows = _ranked(taskset)
    high = rows[:taskset.base.processors]
    low = rows[taskset.base.processors:]
    def demand(group: Sequence[Mapping[str, Any]]) -> Fraction:
        return sum(
            (Fraction(row["C"], row["T"]) * Fraction(row["P"])
             for row in group), Fraction(0),
        )
    return demand(high), demand(low)


def factors_for_ratio(high: Fraction, low: Fraction, ratio: Fraction) -> tuple[Fraction, Fraction]:
    if high <= 0 or low <= 0 or ratio < 1:
        raise ValueError("priority-energy groups and ratio must be positive")
    low_factor = (high + low) / (ratio * high + low)
    return ratio * low_factor, low_factor


def priority_energy_material(taskset: PriorityTaskset, ratio: Fraction,
                             *, reference_ratio: Fraction = REFERENCE_RATIO) -> dict[str, Any]:
    ratio = parse_fraction(ratio, "priority-energy ratio")
    reference_ratio = parse_fraction(reference_ratio, "reference ratio")
    high_demand, low_demand = _group_demands(taskset)
    high_factor, low_factor = factors_for_ratio(high_demand, low_demand, ratio)
    ref_high, ref_low = factors_for_ratio(high_demand, low_demand, reference_ratio)
    rows = _ranked(taskset)
    task_rows = []
    factors: dict[str, str] = {}
    ref_powers: list[Fraction] = []
    transformed_powers: list[Fraction] = []
    for row in rows:
        is_high = int(row["priority_rank"]) < taskset.base.processors
        exact_factor = high_factor if is_high else low_factor
        exact_reference_factor = ref_high if is_high else ref_low
        emitted = canonical_binary64_decimal_v7(exact_factor)
        factors[str(row["task_id"])] = emitted
        base_power = Fraction(row["P"])
        transformed = base_power * exact_factor
        ref_powers.append(base_power * exact_reference_factor)
        transformed_powers.append(transformed)
        task_rows.append({
            "task_id": str(row["task_id"]),
            "priority_rank": int(row["priority_rank"]),
            "group": "HP" if is_high else "LP",
            "base_P": fraction_text(base_power),
            "transformed_P": fraction_text(transformed),
            "exact_factor": fraction_text(exact_factor),
            "emitted_factor": emitted,
            "reference_exact_factor": fraction_text(exact_reference_factor),
            "workload": "hash",
        })
    base_demand = sum(
        (Fraction(row["C"], row["T"]) * Fraction(row["P"])
         for row in rows), Fraction(0),
    )
    transformed_demand = sum(
        (Fraction(row["C"], row["T"]) * Fraction(item["transformed_P"])
         for row, item in zip(rows, task_rows)), Fraction(0),
    )
    if transformed_demand != base_demand:
        raise ValueError("priority-energy demand conservation failed")
    reference_burst = sum(sorted(ref_powers, reverse=True)[:taskset.base.processors], Fraction(0))
    material = {
        "schema": "SCHEDULER_PRIORITY_ENERGY_LOAD_CROSS_MATERIAL_V1",
        "profile": "priority-energy-correlated",
        "rho": fraction_text(ratio),
        "rho_reference": fraction_text(reference_ratio),
        "hp_count": taskset.base.processors,
        "lp_count": taskset.base.task_count - taskset.base.processors,
        "priority_hash": taskset.priority_hash,
        "projected_priority_hash": taskset.priority_hash,
        "base_taskset_hash": taskset.base_hash,
        "projected_taskset_hash": taskset.projected_taskset_hash,
        "source_taskset_id": taskset.source_taskset_id,
        "source_taskset_hash": taskset.base.semantic_hash,
        "H_base": fraction_text(high_demand),
        "L_base": fraction_text(low_demand),
        "P_dem_base": fraction_text(base_demand),
        "P_dem_transformed": fraction_text(transformed_demand),
        "high_factor": fraction_text(high_factor),
        "low_factor": fraction_text(low_factor),
        "reference_high_factor": fraction_text(ref_high),
        "reference_low_factor": fraction_text(ref_low),
        "E_burst_reference": fraction_text(reference_burst),
        "tasks": task_rows,
        "task_energy_factors": factors,
    }
    material["material_hash"] = _hash("MATERIAL", material)
    return material


def energy_material(priority_material: Mapping[str, Any], target_ue: Fraction,
                    raw_trace: Sequence[Fraction], *, kappa: Fraction) -> dict[str, Any]:
    ue = parse_fraction(target_ue, "target U_E")
    eta = eta_for_ue(ue)
    raw_mean = sum(raw_trace, Fraction(0)) / len(raw_trace)
    demand = Fraction(priority_material["P_dem_transformed"])
    burst = Fraction(priority_material["E_burst_reference"])
    battery = parse_fraction(kappa, "kappa") * burst
    target_supply = demand / ue
    runtime_demand = Fraction(priority_material.get("P_dem_runtime", demand))
    runtime_effective_ue = runtime_demand / target_supply
    solar_scale = target_supply / raw_mean
    result = {
        "kappa": fraction_text(kappa),
        "target_ue": fraction_text(ue),
        "eta": fraction_text(eta),
        "P_dem_j_per_tick": fraction_text(demand),
        "P_dem_conceptual_j_per_tick": fraction_text(demand),
        "P_dem_runtime_j_per_tick": fraction_text(runtime_demand),
        "runtime_effective_ue": fraction_text(runtime_effective_ue),
        "runtime_effective_ue_abs_error": fraction_text(abs(runtime_effective_ue - ue)),
        "runtime_effective_ue_rel_error": fraction_text(abs(runtime_effective_ue - ue) / ue),
        "runtime_rounding_bound": priority_material.get("runtime_rounding_bound", "0"),
        "runtime_conservation_abs_error": priority_material.get("runtime_conservation_abs_error", "0"),
        "runtime_conservation_rel_error": priority_material.get("runtime_conservation_rel_error", "0"),
        "target_supply_mean_j_per_tick": fraction_text(target_supply),
        "E_burst_reference_j": fraction_text(burst),
        "battery_capacity_j": fraction_text(battery),
        "initial_energy_j": fraction_text(battery / 2),
        "raw_reference_mean_j_per_tick": fraction_text(raw_mean),
        "solar_scale": fraction_text(solar_scale),
        "energy_control": "SERVICE_ONLY_SCALING",
        "rho": priority_material["rho"],
        "rho_reference": priority_material["rho_reference"],
        "material_hash": priority_material["material_hash"],
    }
    if Fraction(result["eta"]) * Fraction(result["target_ue"]) != 1:
        raise ValueError("eta != 1/U_E")
    if Fraction(result["solar_scale"]) * raw_mean != target_supply:
        raise ValueError("solar supply identity failed")
    return result


def request_rows(tasksets: Sequence[PriorityTaskset], cells: Sequence[tuple[Fraction, Fraction]],
                 ratios: Sequence[Fraction], schedulers: Sequence[str], horizon: int,
                 *, system_path: Path | None = None) -> list[dict[str, Any]]:
    by_uc_index = {(Fraction(item.base.target_utilization, item.base.processors), item.base.taskset_index): item for item in tasksets}
    rows = []
    for uc, ue in cells:
        for index in sorted(index for key_uc, index in by_uc_index if key_uc == uc):
            taskset = by_uc_index[(uc, index)]
            for ratio in ratios:
                material = priority_energy_material(taskset, ratio)
                if system_path is not None:
                    material = runtime_material(
                        material, taskset.task_payload, system_path,
                    )
                for scheduler in schedulers:
                    identity = {
                        "source_taskset_id": taskset.source_taskset_id,
                        "source_taskset_hash": taskset.source_taskset_hash or taskset.base.semantic_hash,
                        "projected_taskset_hash": taskset.projected_taskset_hash,
                        "target_ue": fraction_text(ue),
                        "rho": fraction_text(ratio),
                        "scheduler": scheduler,
                    }
                    rows.append({
                        "request_id": "scheduler-priority-energy-" + _hash("REQUEST", identity)[:32],
                        "domain": DOMAIN,
                        "taskset_id": taskset.source_taskset_id,
                        "taskset_hash": taskset.projected_taskset_hash,
                        "source_taskset_id": taskset.source_taskset_id,
                        "source_taskset_hash": taskset.source_taskset_hash or taskset.base.semantic_hash,
                        "projected_taskset_hash": taskset.projected_taskset_hash,
                        # Compatibility alias; the simulator-facing hash is projected.
                        "base_taskset_hash": taskset.projected_taskset_hash,
                        "material_hash": material["material_hash"],
                        "target_uc": fraction_text(uc),
                        "actual_uc": fraction_text(taskset.base.actual_utilization / taskset.base.processors),
                        "target_ue": fraction_text(ue),
                        "eta": fraction_text(eta_for_ue(ue)),
                        "rho": fraction_text(ratio),
                        "rho_reference": fraction_text(REFERENCE_RATIO),
                        "generation_index": taskset.base.taskset_index,
                        "seed": taskset.base.seed,
                        "scheduler": scheduler,
                        "scheduler_cli": SCHEDULER_CLI[scheduler],
                        "horizon_ms": horizon,
                    })
    return rows


def taskset_row(taskset: PriorityTaskset) -> dict[str, Any]:
    source_hash = taskset.source_taskset_hash or taskset.base.semantic_hash
    return {
        **taskset.base.generated_row(),
        "taskset_id": taskset.base.taskset_id,
        "taskset_hash": taskset.projected_taskset_hash,
        "source_taskset_id": taskset.source_taskset_id,
        "source_taskset_hash": source_hash,
        "projected_taskset_hash": taskset.projected_taskset_hash,
        "taskset_hash_semantics": "projected_taskset_hash",
        "projected_priority_hash": taskset.priority_hash,
        "projected_workload": "hash",
        "target_uc": fraction_text(taskset.base.target_utilization / taskset.base.processors),
        "actual_uc": fraction_text(taskset.base.actual_utilization / taskset.base.processors),
        "all_workloads_hash": True,
        "priority_rank_source": "canonical_task_payload.priority_rank",
        "tasks": list(taskset.task_payload),
        "projected_tasks": list(taskset.task_payload),
        "priority_hash": taskset.priority_hash,
    }


def raw_trace_for_service(service: Any) -> tuple[Fraction, ...]:
    return tuple(construct_paired_harvest_trace(service.system_path, NORMALIZATION_HORIZON))


def _ulp_fraction(value: float) -> Fraction:
    if value == 0.0:
        return Fraction.from_float(5e-324)
    _mantissa, exponent = math.frexp(abs(value))
    return Fraction.from_float(math.ldexp(1.0, exponent - 53))


def runtime_task_powers(task_payload: Sequence[Mapping[str, Any]],
                        factors: Mapping[str, str], system_path: Path) -> dict[str, dict[str, Any]]:
    """Reproduce C++ operation order and preserve the exact binary64 result."""
    system = legacy_rta.load_system_config(str(system_path))
    result: dict[str, dict[str, Any]] = {}
    for row in task_payload:
        task_id = str(row["task_id"])
        wcet = int(row["C"])
        power = system.base_power * system.workload_coefficient("hash") * system.frequency_ratio()
        total = power * (float(wcet) * 0.001)
        total *= float(factors[task_id])
        runtime_power = total / float(wcet)
        result[task_id] = {
            "task_id": task_id,
            "runtime_power_float": runtime_power,
            "runtime_power_binary64_hex": runtime_power.hex(),
            "runtime_power_exact_fraction": fraction_text(Fraction.from_float(runtime_power)),
            "runtime_factor_binary64_hex": float(factors[task_id]).hex(),
        }
    return result


def runtime_material(material: Mapping[str, Any],
                     task_payload: Sequence[Mapping[str, Any]],
                     system_path: Path) -> dict[str, Any]:
    """Attach runtime binary64 provenance without changing conceptual values."""
    result = {key: value for key, value in material.items() if key != "material_hash"}
    runtime = runtime_task_powers(
        task_payload, material["task_energy_factors"], system_path,
    )
    task_by_id = {str(row["task_id"]): row for row in task_payload}
    task_rows = []
    runtime_demand = Fraction(0)
    runtime_bound = Fraction(0)
    for row in material["tasks"]:
        task = dict(row)
        info = runtime[str(row["task_id"])]
        runtime_power = Fraction(info["runtime_power_exact_fraction"])
        task["runtime_transformed_P_binary64"] = info["runtime_power_float"]
        task["runtime_transformed_P_binary64_hex"] = info["runtime_power_binary64_hex"]
        task["runtime_transformed_P_exact_fraction"] = info["runtime_power_exact_fraction"]
        task["runtime_factor_binary64_hex"] = info["runtime_factor_binary64_hex"]
        task_spec = task_by_id[str(row["task_id"])]
        runtime_demand += Fraction(task_spec["C"], task_spec["T"]) * runtime_power
        factor_rounding = abs(
            Fraction(row["base_P"]) *
            (Fraction.from_float(float(row["emitted_factor"])) - Fraction(row["exact_factor"]))
        )
        runtime_bound += Fraction(task_spec["C"], task_spec["T"]) * (
            factor_rounding + 8 * _ulp_fraction(info["runtime_power_float"])
        )
        task_rows.append(task)
    runtime_bound += len(task_rows) * 2 * _ulp_fraction(float(runtime_demand))
    conceptual_demand = Fraction(material["P_dem_transformed"])
    runtime_error = abs(runtime_demand - conceptual_demand)
    if runtime_error > runtime_bound:
        raise ValueError("runtime binary64 demand exceeded the derived ULP bound")
    result.update({
        "tasks": task_rows,
        "P_dem_runtime": fraction_text(runtime_demand),
        "runtime_rounding_bound": fraction_text(runtime_bound),
        "runtime_conservation_abs_error": fraction_text(runtime_error),
        "runtime_conservation_rel_error": fraction_text(
            runtime_error / conceptual_demand if conceptual_demand else Fraction(0),
        ),
        "runtime_operation_order": "power*wcet_seconds; total*=factor; total/wcet",
    })
    result["material_hash"] = _hash("MATERIAL", result)
    return result
