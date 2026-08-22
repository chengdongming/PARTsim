"""Scheduler LOAD-CROSS adapter using the canonical PERF-G taskset path."""

from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import perf_g
from .cell_model import expand_cells
from .config import canonical_json, fraction_text
from .simulation_engine import construct_paired_harvest_trace
from .taskset_store import TasksetStore, prepare_service_curve


DOMAIN = "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v2"
FORMAL_NORMALIZATION_HORIZON = perf_g.FORMAL_HORIZON_MS
DEFAULT_KAPPA = Fraction(10)
DEFAULT_CELLS = tuple(
    (Fraction(uc), Fraction(ue)) for uc, ue in (
        ("1/10", "2/5"), ("2/10", "2/5"), ("3/10", "2/5"),
        ("4/10", "2/5"), ("5/10", "2/5"), ("6/10", "2/5"),
        ("7/10", "2/5"), ("8/10", "2/5"), ("5/10", "1/5"),
        ("5/10", "3/10"), ("5/10", "1/2"), ("5/10", "3/5"),
    )
)
DEFAULT_SCHEDULERS = tuple(perf_g.CAL_SCHEDULERS)
ALL_SCHEDULERS = tuple(perf_g.FORMAL_SCHEDULERS)


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


def parse_cells(text: str | None) -> tuple[tuple[Fraction, Fraction], ...]:
    if not text:
        return DEFAULT_CELLS
    cells: list[tuple[Fraction, Fraction]] = []
    for item in text.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"invalid cell {item!r}; expected U_C:U_E")
        cell = (parse_fraction(parts[0], "U_C"), parse_fraction(parts[1], "U_E"))
        if not (0 < cell[0] <= 1 and 0 < cell[1] <= 1):
            raise ValueError("U_C and U_E must be in (0, 1]")
        if cell not in cells:
            cells.append(cell)
    if not cells:
        raise ValueError("at least one cell is required")
    return tuple(cells)


def resolve_figure_slices(
    cells: Sequence[tuple[Fraction, Fraction]],
    *, fixed_ue: Fraction | None = None,
    fixed_uc: Fraction | None = None,
) -> dict[str, dict[str, str]]:
    ue_to_ucs: dict[Fraction, set[Fraction]] = {}
    uc_to_ues: dict[Fraction, set[Fraction]] = {}
    for uc, ue in cells:
        ue_to_ucs.setdefault(ue, set()).add(uc)
        uc_to_ues.setdefault(uc, set()).add(ue)

    def resolve(
        explicit: Fraction | None, groups: dict[Fraction, set[Fraction]], label: str,
    ) -> Fraction:
        if explicit is None:
            maximum = max((len(values) for values in groups.values()), default=0)
            candidates = [key for key, values in groups.items() if len(values) == maximum]
            if maximum == 0 or len(candidates) != 1:
                raise ValueError(
                    f"ambiguous {label}; provide an explicit figure slice"
                )
            selected = candidates[0]
        else:
            selected = parse_fraction(explicit, label)
        if not 0 < selected <= 1:
            raise ValueError(f"{label} must be in (0,1]")
        if selected not in groups or not groups[selected]:
            raise ValueError(f"{label} {fraction_text(selected)} is absent from cells")
        return selected

    selected_ue = resolve(fixed_ue, ue_to_ucs, "U_E figure slice")
    selected_uc = resolve(fixed_uc, uc_to_ues, "U_C figure slice")
    return {
        "uc_scan": {
            "x_key": "target_uc", "fixed_key": "target_ue",
            "fixed_value": fraction_text(selected_ue),
        },
        "ue_scan": {
            "x_key": "target_ue", "fixed_key": "target_uc",
            "fixed_value": fraction_text(selected_uc),
        },
    }


def parse_schedulers(text: str | None) -> tuple[str, ...]:
    values = DEFAULT_SCHEDULERS if not text else tuple(x.strip() for x in text.split(","))
    if not values or any(not x for x in values) or len(set(values)) != len(values):
        raise ValueError("scheduler list must be non-empty and unique")
    unknown = sorted(set(values) - set(ALL_SCHEDULERS))
    if unknown:
        raise ValueError(f"unknown scheduler(s): {', '.join(unknown)}")
    return values


def _hash(value: Any) -> str:
    return hashlib.sha256(
        DOMAIN.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def eta_for_ue(target_ue: Fraction) -> Fraction:
    target = parse_fraction(target_ue, "target U_E")
    return Fraction(1, 1) / target


def _config(seed: int, *, utilizations: Sequence[Fraction], count: int,
            processors: int, tasks: int, period_min: int, period_max: int,
            min_task_util: Fraction, max_task_util: Fraction,
            tolerance: Fraction) -> dict[str, Any]:
    config = perf_g._task_generation_config("FORMAL", utilizations, count)
    config["grid"]["base_seed"] = int(seed)
    config["platform"] = {"cores": [processors], "task_count": [tasks]}
    config["generation"].update({
        "period_min": period_min, "period_max": period_max,
        "min_task_util": fraction_text(min_task_util),
        "max_task_util": fraction_text(max_task_util),
        "utilization_tolerance": fraction_text(tolerance),
    })
    return config


def materialize_tasksets(root: Path, *, seed: int, utilizations: Sequence[Fraction],
                         count: int, processors: int, tasks: int,
                         period_min: int, period_max: int,
                         min_task_util: Fraction, max_task_util: Fraction,
                         tolerance: Fraction) -> tuple[list[Any], Any]:
    config = _config(
        seed, utilizations=utilizations, count=count, processors=processors,
        tasks=tasks, period_min=period_min, period_max=period_max,
        min_task_util=min_task_util, max_task_util=max_task_util,
        tolerance=tolerance,
    )
    service = prepare_service_curve(config, root / "service")
    store = TasksetStore(root / "tasksets", config, service)
    cells = expand_cells(config)
    tasksets = []
    for cell in cells:
        for index in range(count):
            taskset = store.get_or_create(cell, index)
            if taskset.processors != processors or taskset.task_count != tasks:
                raise ValueError("canonical taskset dimensions mismatch")
            if any(int(row.get("arrival_offset", 0)) != 0 for row in taskset.task_payload):
                raise ValueError("scheduler LOAD-CROSS requires synchronous release")
            tasksets.append(taskset)
    store.verify_pairing_manifest(require_complete=True)
    return tasksets, service


def taskset_row(taskset: Any, processors: int) -> dict[str, Any]:
    row = taskset.generated_row()
    row.update({
        "target_uc": fraction_text(taskset.target_utilization / processors),
        "actual_uc": fraction_text(taskset.actual_utilization / processors),
        "canonical_task_power": True,
    })
    return row


def request_rows(tasksets: Sequence[Any], cells: Sequence[tuple[Fraction, Fraction]],
                 schedulers: Sequence[str], horizon: int) -> list[dict[str, Any]]:
    by_uc_index = {
        (Fraction(taskset.target_utilization, taskset.processors), taskset.taskset_index): taskset
        for taskset in tasksets
    }
    rows = []
    for uc, ue in cells:
        eta = eta_for_ue(ue)
        for index in sorted(index for key_uc, index in by_uc_index if key_uc == uc):
            taskset = by_uc_index[(uc, index)]
            for scheduler in schedulers:
                identity = {
                    "taskset_id": taskset.taskset_id, "target_ue": fraction_text(ue),
                    "scheduler": scheduler,
                }
                rows.append({
                    "request_id": "scheduler-load-cross-" + _hash(identity)[:32],
                    "taskset_id": taskset.taskset_id,
                    "taskset_hash": taskset.semantic_hash,
                    "target_uc": fraction_text(uc), "actual_uc": fraction_text(taskset.actual_utilization / taskset.processors),
                    "target_ue": fraction_text(ue), "eta": fraction_text(eta),
                    "generation_index": taskset.taskset_index, "seed": taskset.seed,
                    "scheduler": scheduler, "scheduler_cli": perf_g.SCHEDULER_CLI[scheduler],
                    "horizon_ms": horizon,
                })
    return rows


def energy_material(taskset: Any, target_ue: Fraction, raw_trace: Sequence[Fraction], *, kappa: Fraction,
                    normalization_horizon: int = FORMAL_NORMALIZATION_HORIZON) -> dict[str, str]:
    """Reuse PERF-G's exact demand/burst arithmetic with eta=1/U_E."""
    if len(raw_trace) != normalization_horizon:
        raise ValueError("raw trace must cover the normalization horizon")
    ue = parse_fraction(target_ue, "target U_E")
    eta = eta_for_ue(ue)
    if normalization_horizon == perf_g.FORMAL_HORIZON_MS:
        material = perf_g.energy_material(
            taskset, {"kappa": fraction_text(kappa), "eta": fraction_text(eta)}, raw_trace,
        )
    else:
        payload = taskset.task_payload
        demand = sum(Fraction(row["C"], row["T"]) * Fraction(row["P"]) for row in payload)
        powers = sorted((Fraction(row["P"]) for row in payload), reverse=True)
        burst = sum(powers[: min(taskset.processors, taskset.task_count)], Fraction(0))
        raw_mean = sum(raw_trace, Fraction(0)) / normalization_horizon
        material = {
            "kappa": fraction_text(kappa), "eta": fraction_text(eta),
            "P_dem_j_per_tick": fraction_text(demand), "E_burst_j": fraction_text(burst),
            "battery_capacity_j": fraction_text(kappa * burst),
            "initial_energy_j": fraction_text(kappa * burst / 2),
            "raw_reference_mean_j_per_tick": fraction_text(raw_mean),
            "solar_scale": fraction_text(eta * demand / raw_mean),
            "normalization_horizon_ms": str(normalization_horizon),
        }
    demand = Fraction(material["P_dem_j_per_tick"])
    raw_mean = Fraction(material["raw_reference_mean_j_per_tick"])
    target_supply = demand / ue
    solar_scale = Fraction(material["solar_scale"])
    if (
        Fraction(material["eta"]) != eta
        or solar_scale * raw_mean != target_supply
        or demand / target_supply != ue
    ):
        raise ValueError("U_E service-only exact identity failed")
    return {
        **material,
        "target_ue": fraction_text(ue), "eta": fraction_text(eta),
        "target_supply_mean_j_per_tick": fraction_text(target_supply),
        "raw_reference_mean_j_per_tick": fraction_text(raw_mean),
        "solar_scale": fraction_text(solar_scale),
        "normalization_horizon_ms": str(normalization_horizon),
        "energy_control": "SERVICE_ONLY_SCALING",
    }
