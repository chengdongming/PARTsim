"""Scheduler LOAD-CROSS adapter using the canonical PERF-G taskset path."""

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from . import perf_g
from .cell_model import expand_cells
from .config import canonical_json, fraction_text
from .simulation_engine import (
    construct_paired_harvest_trace,
    normalize_scheduler_priority_policy,
)
from .taskset_store import TasksetStore, prepare_service_curve
from .parallel_prepare import run_prepare_jobs


V3_DOMAIN = "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v3"
V4_DOMAIN = "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v4"
V5_DOMAIN = "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v5"
DOMAIN = V5_DOMAIN
V4_EXPERIMENT = "scheduler-load-cross-v4"
V3_EXPERIMENT = "scheduler-load-cross-v3"
V5_EXPERIMENT = "scheduler-load-cross-v5"
DEADLINE_MODES = ("constrained", "implicit")
FORMAL_NORMALIZATION_HORIZON = perf_g.FORMAL_HORIZON_MS
ORDINARY_SYSTEM_TEMPLATE = "system_config_unified_template.yml"
DEFAULT_KAPPA = Fraction(10)
HARVEST_MODEL = "linear_ramp_v1"
HARVEST_MODEL_IDENTITY = {
    "harvest_model": HARVEST_MODEL,
    "ramp_half_span": "1/40",
    "ramp_horizon_ms": 60000,
    "post_horizon_policy": "hold_last",
}
FORMAL_UC_SCAN = tuple(Fraction(value) for value in (
    "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5", "9/10", "1",
))
FORMAL_UE_SCAN = FORMAL_UC_SCAN
FORMAL_CELLS = tuple(
    (uc, fixed_ue) for fixed_ue in (Fraction("3/10"), Fraction("3/5"), Fraction("9/10"))
    for uc in FORMAL_UC_SCAN
) + tuple(
    (fixed_uc, ue)
    for fixed_uc in (Fraction("3/10"), Fraction("3/5"), Fraction("9/10"))
    for ue in FORMAL_UE_SCAN
    if (fixed_uc, ue) not in tuple(
        (uc, fixed_ue)
        for fixed_ue in (Fraction("3/10"), Fraction("3/5"), Fraction("9/10"))
        for uc in FORMAL_UC_SCAN
    )
)
PANEL_GROUPS = {
    "BLOCK": ("ASAP-BLOCK", "ALAP-BLOCK", "ST-BLOCK"),
    "NONBLOCK": ("ASAP-NONBLOCK", "ALAP-NONBLOCK", "ST-NONBLOCK"),
    "SYNC": ("ASAP-SYNC", "ALAP-SYNC", "ST-SYNC"),
}
SCHEDULER_STYLES = {
    "ASAP": {"marker": "o", "linestyle": "-"},
    "ALAP": {"marker": "s", "linestyle": "--"},
    "ST": {"marker": "^", "linestyle": ":"},
}
FROZEN_MAIN_FIGURE = {
    "cells": FORMAL_CELLS,
    "uc_fixed_ues": (Fraction("3/10"), Fraction("3/5"), Fraction("9/10")),
    "ue_fixed_ucs": (Fraction("3/10"), Fraction("3/5"), Fraction("9/10")),
    "horizon_ms": perf_g.FORMAL_HORIZON_MS,
    "schedulers": tuple(perf_g.FORMAL_SCHEDULERS),
}
LEGACY_PILOT_CELLS = tuple(
    (Fraction(uc), Fraction(ue)) for uc, ue in (
        ("1/10", "2/5"), ("2/10", "2/5"), ("3/10", "2/5"),
        ("4/10", "2/5"), ("5/10", "2/5"), ("6/10", "2/5"),
        ("7/10", "2/5"), ("8/10", "2/5"), ("5/10", "1/5"),
        ("5/10", "3/10"), ("5/10", "1/2"), ("5/10", "3/5"),
    )
)
DEFAULT_CELLS = LEGACY_PILOT_CELLS
DEFAULT_SCHEDULERS = tuple(perf_g.CAL_SCHEDULERS)
ALL_SCHEDULERS = tuple(perf_g.FORMAL_SCHEDULERS)
_PREPARE_RAW_TRACE: tuple[Fraction, ...] | None = None

DEFAULT_SCAN_PROFILE = {
    "uc_scan_values": [
        "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5", "9/10", "1",
    ],
    "ue_scan_values": [
        "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5", "9/10", "1",
    ],
    "uc_figure_fixed_ues": ["3/10", "3/5", "9/10"],
    "uc_figure_labels": ["low", "medium", "high"],
    "ue_figure_fixed_ucs": ["3/10", "3/5", "9/10"],
    "ue_figure_labels": ["low", "medium", "high"],
    "axis_display_min": "0",
    "axis_display_max": "1",
    "axis_tick_step": "1/10",
}
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MAX_AXIS_TICKS = 10001


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
        return FORMAL_CELLS
    cells: list[tuple[Fraction, Fraction]] = []
    for item in text.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"invalid cell {item!r}; expected U_C:U_E")
        cell = (parse_fraction(parts[0], "U_C"), parse_fraction(parts[1], "U_E"))
        if not (0 < cell[0] <= 1 and 0 < cell[1] <= 1):
            raise ValueError("U_C and U_E must be in (0, 1]")
        # Generic/custom grids retain the historical deduplication behavior;
        # the frozen campaign validator below still requires the exact 51-cell
        # ordered contract before it is treated as a paper run.
        if cell not in cells:
            cells.append(cell)
    if not cells:
        raise ValueError("at least one cell is required")
    return tuple(cells)


def parse_fraction_list(value: Any, label: str) -> tuple[Fraction, ...]:
    """Parse a comma-separated or JSON-like sequence without float conversion."""
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = list(value)
    else:
        raise ValueError(f"{label} must be a non-empty exact fraction list")
    if not values or any(value == "" for value in values):
        raise ValueError(f"{label} must be a non-empty exact fraction list")
    try:
        return tuple(parse_fraction(value, f"{label}[{index}]") for index, value in enumerate(values))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def fraction_token(value: Any) -> str:
    parsed = parse_fraction(value, "fraction token")
    return str(parsed.numerator) if parsed.denominator == 1 else f"{parsed.numerator}of{parsed.denominator}"


def decimal_text(value: Any) -> str:
    parsed = parse_fraction(value, "decimal value")
    return format(float(parsed), ".15g")


def axis_ticks(axis_min: Fraction, axis_max: Fraction, step: Fraction) -> tuple[Fraction, ...]:
    span = axis_max - axis_min
    count = span / step
    if count.denominator != 1:
        raise ValueError("axis display range must be divisible by axis tick step")
    if count.numerator > MAX_AXIS_TICKS - 1:
        raise ValueError("axis display tick count exceeds configured limit")
    return tuple(axis_min + step * index for index in range(count.numerator + 1))


def normalize_scan_profile(
    *, uc_scan_values: Any = None, ue_scan_values: Any = None,
    uc_figure_fixed_ues: Any = None, uc_figure_labels: Any = None,
    ue_figure_fixed_ucs: Any = None, ue_figure_labels: Any = None,
    ue_figure_fixed_uc: Any = None, axis_display_min: Any = None,
    axis_display_max: Any = None, axis_tick_step: Any = None,
) -> dict[str, Any]:
    """Validate and canonicalize one v4 grid and figure contract."""
    source = DEFAULT_SCAN_PROFILE
    uc_scan = parse_fraction_list(
        source["uc_scan_values"] if uc_scan_values is None else uc_scan_values,
        "uc_scan_values",
    )
    ue_scan = parse_fraction_list(
        source["ue_scan_values"] if ue_scan_values is None else ue_scan_values,
        "ue_scan_values",
    )
    fixed_ues = parse_fraction_list(
        source["uc_figure_fixed_ues"] if uc_figure_fixed_ues is None else uc_figure_fixed_ues,
        "uc_figure_fixed_ues",
    )
    labels_value = source["uc_figure_labels"] if uc_figure_labels is None else uc_figure_labels
    if isinstance(labels_value, str):
        labels = tuple(part.strip() for part in labels_value.split(","))
    elif isinstance(labels_value, Sequence) and not isinstance(labels_value, (bytes, bytearray)):
        labels = tuple(str(part) for part in labels_value)
    else:
        raise ValueError("uc_figure_labels must be a non-empty label list")
    if not labels or any(not label or not _SAFE_LABEL.fullmatch(label) for label in labels):
        raise ValueError("uc_figure_labels contain an empty or unsafe label")
    if len(labels) != len(fixed_ues) or len(set(labels)) != len(labels):
        raise ValueError("uc_figure_labels must match fixed U_E count and be unique")
    for name, values in (("uc_scan_values", uc_scan), ("ue_scan_values", ue_scan)):
        if any(value > 1 for value in values):
            raise ValueError(f"{name} must be in (0,1]")
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError(f"{name} must be strictly increasing and unique")
    if any(value not in ue_scan for value in fixed_ues) or len(set(fixed_ues)) != len(fixed_ues):
        raise ValueError("uc_figure_fixed_ues must be unique values in ue_scan_values")
    if ue_figure_fixed_ucs is not None and ue_figure_fixed_uc is not None:
        raise ValueError("ue_figure_fixed_uc conflicts with ue_figure_fixed_ucs")
    fixed_ucs = parse_fraction_list(
        source["ue_figure_fixed_ucs"] if ue_figure_fixed_ucs is None else ue_figure_fixed_ucs,
        "ue_figure_fixed_ucs",
    )
    if any(value not in uc_scan for value in fixed_ucs) or len(set(fixed_ucs)) != len(fixed_ucs):
        raise ValueError("ue_figure_fixed_ucs must be unique values in uc_scan_values")
    ue_labels_value = source["ue_figure_labels"] if ue_figure_labels is None else ue_figure_labels
    if isinstance(ue_labels_value, str):
        ue_labels = tuple(part.strip() for part in ue_labels_value.split(","))
    elif isinstance(ue_labels_value, Sequence) and not isinstance(ue_labels_value, (bytes, bytearray)):
        ue_labels = tuple(str(part) for part in ue_labels_value)
    else:
        raise ValueError("ue_figure_labels must be a non-empty label list")
    if not ue_labels or any(not label or not _SAFE_LABEL.fullmatch(label) for label in ue_labels):
        raise ValueError("ue_figure_labels contain an empty or unsafe label")
    if len(ue_labels) != len(fixed_ucs) or len(set(ue_labels)) != len(ue_labels):
        raise ValueError("ue_figure_labels must match fixed U_C count and be unique")
    minimum = parse_fraction(
        source["axis_display_min"] if axis_display_min is None else axis_display_min,
        "axis_display_min", positive=False,
    )
    maximum = parse_fraction(
        source["axis_display_max"] if axis_display_max is None else axis_display_max,
        "axis_display_max", positive=False,
    )
    step = parse_fraction(
        source["axis_tick_step"] if axis_tick_step is None else axis_tick_step,
        "axis_tick_step",
    )
    if not 0 <= minimum < maximum <= 1:
        raise ValueError("axis display range must satisfy 0 <= min < max <= 1")
    if step <= 0:
        raise ValueError("axis_tick_step must be positive")
    if any(value < minimum or value > maximum for value in (*uc_scan, *ue_scan)):
        raise ValueError("axis display range must cover every scan value")
    ticks = axis_ticks(minimum, maximum, step)
    return {
        "uc_scan_values": [fraction_text(value) for value in uc_scan],
        "ue_scan_values": [fraction_text(value) for value in ue_scan],
        "uc_figure_fixed_ues": [fraction_text(value) for value in fixed_ues],
        "uc_figure_labels": list(labels),
        "ue_figure_fixed_ucs": [fraction_text(value) for value in fixed_ucs],
        "ue_figure_labels": list(ue_labels),
        "axis_display_min": fraction_text(minimum),
        "axis_display_max": fraction_text(maximum),
        "axis_tick_step": fraction_text(step),
        "axis_ticks": [fraction_text(value) for value in ticks],
        "zero_point_policy": "display_tick_only_outside_experiment_domain",
    }


def build_scan_cells(
    uc_scan_values: Sequence[Any], ue_scan_values: Sequence[Any],
    uc_figure_fixed_ues: Sequence[Any], ue_figure_fixed_ucs: Sequence[Any] | Any,
) -> tuple[tuple[Fraction, Fraction], ...]:
    """Return canonical ordered cells for the two v4 figure scans."""
    uc_scan = tuple(parse_fraction(value, "uc_scan_value") for value in uc_scan_values)
    ue_scan = tuple(parse_fraction(value, "ue_scan_value") for value in ue_scan_values)
    fixed_ues = tuple(parse_fraction(value, "uc_figure_fixed_ue") for value in uc_figure_fixed_ues)
    if isinstance(ue_figure_fixed_ucs, (str, Fraction, int)):
        ue_figure_fixed_ucs = (ue_figure_fixed_ucs,)
    fixed_ucs = tuple(parse_fraction(value, "ue_figure_fixed_uc") for value in ue_figure_fixed_ucs)
    cells: list[tuple[Fraction, Fraction]] = []
    for ue in fixed_ues:
        for uc in uc_scan:
            if (uc, ue) not in cells:
                cells.append((uc, ue))
    for uc in fixed_ucs:
        for ue in ue_scan:
            if (uc, ue) not in cells:
                cells.append((uc, ue))
    return tuple(cells)


def build_scan_contract(profile: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_scan_profile(**{
        key: profile[key] for key in (
            "uc_scan_values", "ue_scan_values", "uc_figure_fixed_ues",
            "uc_figure_labels", "axis_display_min", "axis_display_max",
            "axis_tick_step", "ue_figure_fixed_ucs", "ue_figure_labels",
        ) if key in profile
    })
    cells = build_scan_cells(
        normalized["uc_scan_values"], normalized["ue_scan_values"],
        normalized["uc_figure_fixed_ues"], normalized["ue_figure_fixed_ucs"],
    )
    normalized["ordered_cells"] = [[fraction_text(uc), fraction_text(ue)] for uc, ue in cells]
    normalized["unique_cell_count"] = len(cells)
    return normalized


def build_v4_figure_slices(profile: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_scan_profile(**{
        key: profile[key] for key in (
            "uc_scan_values", "ue_scan_values", "uc_figure_fixed_ues",
            "uc_figure_labels", "ue_figure_fixed_ucs", "ue_figure_labels",
            "axis_display_min", "axis_display_max", "axis_tick_step",
        ) if key in profile
    })
    uc_scans = []
    for fixed_ue, label in zip(normalized["uc_figure_fixed_ues"], normalized["uc_figure_labels"]):
        uc_scans.append({
            "x_key": "target_uc", "fixed_key": "target_ue",
            "fixed_value": fixed_ue, "label": label,
            "x_values": list(normalized["uc_scan_values"]),
        })
    ue_scans = []
    for fixed_uc, label in zip(normalized["ue_figure_fixed_ucs"], normalized["ue_figure_labels"]):
        ue_scans.append({
            "x_key": "target_ue", "fixed_key": "target_uc",
            "fixed_value": fixed_uc, "label": label,
            "x_values": list(normalized["ue_scan_values"]),
        })
    return {"uc_scans": uc_scans, "ue_scans": ue_scans}


DEFAULT_CELLS = build_scan_cells(
    DEFAULT_SCAN_PROFILE["uc_scan_values"], DEFAULT_SCAN_PROFILE["ue_scan_values"],
    DEFAULT_SCAN_PROFILE["uc_figure_fixed_ues"], DEFAULT_SCAN_PROFILE["ue_figure_fixed_ucs"],
)


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


def validate_frozen_main_figure(
    cells: Sequence[tuple[Fraction, Fraction]],
    schedulers: Sequence[str],
    *,
    horizon_ms: int,
    priority_policy: str = "RM",
) -> None:
    """Fail closed when the paper campaign is presented as the frozen one."""
    try:
        policy = normalize_scheduler_priority_policy(priority_policy)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    if policy != "RM":
        raise ValueError("frozen main-figure campaign requires priority policy RM")
    if tuple(cells) != tuple(FORMAL_CELLS):
        raise ValueError(
            "frozen main-figure campaign must contain exactly the 51 canonical cells"
        )
    if tuple(schedulers) != tuple(perf_g.FORMAL_SCHEDULERS):
        raise ValueError(
            "frozen main-figure campaign must contain all 9 schedulers in canonical order"
        )
    if int(horizon_ms) != perf_g.FORMAL_HORIZON_MS:
        raise ValueError(
            "frozen main-figure campaign requires simulation horizon 60000 ms"
        )


def _hash(value: Any, *, domain: str = DOMAIN) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def harvest_trace_identity(raw_trace: Sequence[Fraction]) -> str:
    """Stable identity for the one paired raw solar trace used by a run."""
    if not raw_trace:
        raise ValueError("raw solar trace must not be empty")
    digest = hashlib.sha256()
    for value in raw_trace:
        digest.update(fraction_text(value).encode("ascii"))
        digest.update(b"\0")
    return "raw-trace-" + digest.hexdigest()[:32]


def eta_for_ue(target_ue: Fraction) -> Fraction:
    target = parse_fraction(target_ue, "target U_E")
    return Fraction(1, 1) / target


def normalize_deadline_mode(value: Any) -> str:
    mode = str(value).strip() if value is not None else ""
    if mode not in DEADLINE_MODES:
        raise ValueError(
            "deadline_mode must be one of: " + ", ".join(DEADLINE_MODES)
        )
    return mode


def _config(seed: int, *, utilizations: Sequence[Fraction], count: int,
            processors: int, tasks: int, period_min: int, period_max: int,
            min_task_util: Fraction, max_task_util: Fraction,
            tolerance: Fraction,
            deadline_mode: str = "constrained",
            system_template: str = ORDINARY_SYSTEM_TEMPLATE) -> dict[str, Any]:
    deadline_mode = normalize_deadline_mode(deadline_mode)
    config = perf_g._task_generation_config(
        "FORMAL", utilizations, count, system_template=system_template,
    )
    config["grid"]["base_seed"] = int(seed)
    config["platform"] = {"cores": [processors], "task_count": [tasks]}
    config["generation"].update({
        "deadline_mode": deadline_mode,
        "period_min": period_min, "period_max": period_max,
        "min_task_util": fraction_text(min_task_util),
        "max_task_util": fraction_text(max_task_util),
        "utilization_tolerance": fraction_text(tolerance),
    })
    config["energy"]["service_curve"].update(HARVEST_MODEL_IDENTITY)
    config["energy"]["service_curve"].update({
        "use_real_solar_data": False,
        "require_real_solar_data": False,
    })
    return config


def prepare_taskset_candidate(job: Mapping[str, Any]) -> dict[str, Any]:
    """Generate one taskset in an isolated directory; never touch shared state."""
    config = job["config"]
    service = job["service"]
    generation_id = str(job["generation_id"])
    taskset_index = int(job["taskset_index"])
    with tempfile.TemporaryDirectory(prefix="scheduler_load_cross_candidate_") as directory:
        worker_store = TasksetStore(Path(directory) / "tasksets", config, service)
        cell = next(
            cell for cell in expand_cells(config)
            if cell.generation_id == generation_id
        )
        candidate_path = worker_store.path_for(generation_id, taskset_index)
        worker_store._generate(candidate_path, cell, taskset_index)
        return {
            "generation_id": generation_id,
            "taskset_index": taskset_index,
            "document": json.loads(candidate_path.read_text(encoding="utf-8")),
        }


def materialize_tasksets(root: Path, *, seed: int, utilizations: Sequence[Fraction],
                         count: int, processors: int, tasks: int,
                         period_min: int, period_max: int,
                         min_task_util: Fraction, max_task_util: Fraction,
                         tolerance: Fraction, prepare_workers: int = 1,
                         deadline_mode: str = "constrained",
                         system_template: str = ORDINARY_SYSTEM_TEMPLATE) -> tuple[list[Any], Any]:
    deadline_mode = normalize_deadline_mode(deadline_mode)
    config = _config(
        seed, utilizations=utilizations, count=count, processors=processors,
        tasks=tasks, period_min=period_min, period_max=period_max,
        min_task_util=min_task_util, max_task_util=max_task_util,
        tolerance=tolerance, deadline_mode=deadline_mode,
        system_template=system_template,
    )
    service = prepare_service_curve(config, root / "service")
    store = TasksetStore(root / "tasksets", config, service)
    cells = expand_cells(config)
    tasksets_by_key: dict[tuple[str, int], Any] = {}
    missing: list[dict[str, Any]] = []
    for cell in cells:
        for index in range(count):
            if prepare_workers == 1 or store.path_for(cell.generation_id, index).is_file():
                taskset = store.get_or_create(cell, index)
            else:
                missing.append({
                    "config": config, "service": service,
                    "generation_id": cell.generation_id,
                    "taskset_index": index,
                })
                continue
            if taskset.processors != processors or taskset.task_count != tasks:
                raise ValueError("canonical taskset dimensions mismatch")
            if any(int(row.get("arrival_offset", 0)) != 0 for row in taskset.task_payload):
                raise ValueError("scheduler LOAD-CROSS requires synchronous release")
            tasksets_by_key[(cell.generation_id, index)] = taskset
    if missing:
        candidates = run_prepare_jobs(
            missing, prepare_taskset_candidate, workers=prepare_workers,
            phase="scheduler-load-cross prepare-tasksets",
            key=lambda row: (row["generation_id"], row["taskset_index"]),
        )
        for cell in cells:
            for index in range(count):
                key = (cell.generation_id, index)
                if key in candidates:
                    candidate = candidates[key]
                    taskset = store.commit_candidate(cell, index, candidate["document"])
                elif store.path_for(*key).is_file():
                    taskset = store.get_or_create(cell, index)
                else:
                    raise RuntimeError(f"missing prepared taskset candidate {key!r}")
                tasksets_by_key[key] = taskset
    store.verify_pairing_manifest(require_complete=True)
    tasksets = [
        tasksets_by_key[(cell.generation_id, index)]
        for cell in cells for index in range(count)
    ]
    if any(normalize_deadline_mode(taskset.deadline_mode) != deadline_mode for taskset in tasksets):
        raise ValueError("materialized taskset deadline mode mismatch")
    for taskset in tasksets:
        for item in taskset.task_payload:
            c, d, t = int(item["C"]), int(item["D"]), int(item["T"])
            if not (0 < c <= d <= t):
                raise ValueError("materialized taskset violates C <= D <= T")
            if deadline_mode == "implicit" and d != t:
                raise ValueError("implicit taskset must satisfy D == T")
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
                 schedulers: Sequence[str], horizon: int,
                 priority_policy: str = "RM", *, experiment_name: str = V3_EXPERIMENT,
                 deadline_mode: str | None = None,
                 ) -> list[dict[str, Any]]:
    try:
        policy = normalize_scheduler_priority_policy(priority_policy)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    by_uc_index = {
        (Fraction(taskset.target_utilization, taskset.processors), taskset.taskset_index): taskset
        for taskset in tasksets
    }
    inferred_modes = {
        normalize_deadline_mode(getattr(taskset, "deadline_mode", "constrained"))
        for taskset in tasksets
    }
    if deadline_mode is None:
        if len(inferred_modes) != 1:
            raise ValueError("request tasksets contain mixed deadline modes")
        deadline_mode = next(iter(inferred_modes), "constrained")
    deadline_mode = normalize_deadline_mode(deadline_mode)
    if inferred_modes and inferred_modes != {deadline_mode}:
        raise ValueError("request deadline mode does not match tasksets")
    is_v5 = experiment_name == V5_EXPERIMENT
    rows = []
    for uc, ue in cells:
        eta = eta_for_ue(ue)
        for index in sorted(index for key_uc, index in by_uc_index if key_uc == uc):
            taskset = by_uc_index[(uc, index)]
            for scheduler in schedulers:
                identity = {
                    "taskset_id": taskset.taskset_id, "target_ue": fraction_text(ue),
                    "scheduler": scheduler,
                    **HARVEST_MODEL_IDENTITY,
                }
                if is_v5:
                    identity["deadline_mode"] = deadline_mode
                if policy == "DM":
                    identity["priority_policy"] = policy
                row = {
                    "experiment": experiment_name,
                    "taskset_id": taskset.taskset_id,
                    "taskset_hash": taskset.semantic_hash,
                    "target_uc": fraction_text(uc), "actual_uc": fraction_text(taskset.actual_utilization / taskset.processors),
                    "target_ue": fraction_text(ue), "eta": fraction_text(eta),
                    "generation_index": taskset.taskset_index, "seed": taskset.seed,
                    "scheduler": scheduler, "scheduler_cli": perf_g.SCHEDULER_CLI[scheduler],
                    "horizon_ms": horizon,
                    "priority_policy": policy,
                    **HARVEST_MODEL_IDENTITY,
                }
                if is_v5:
                    row["deadline_mode"] = deadline_mode
                    request_domain = V5_DOMAIN
                elif experiment_name == V4_EXPERIMENT:
                    request_domain = V4_DOMAIN
                else:
                    request_domain = V3_DOMAIN
                row["request_id"] = "scheduler-load-cross-" + _hash(
                    identity, domain=request_domain,
                )[:32]
                rows.append(row)
    return rows


def energy_material(taskset: Any, target_ue: Fraction, raw_trace: Sequence[Fraction], *, kappa: Fraction,
                    normalization_horizon: int = FORMAL_NORMALIZATION_HORIZON,
                    raw_trace_id: str | None = None) -> dict[str, str]:
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
        "runtime_configured_average_supply_j_per_tick": fraction_text(
            target_supply
        ),
        "runtime_average_supply_j_per_tick": fraction_text(target_supply),
        "actual_ue": fraction_text(demand / target_supply),
        "actual_ue_abs_error": fraction_text(abs(demand / target_supply - ue)),
        "actual_ue_minus_target_ue": fraction_text(demand / target_supply - ue),
        "actual_ue_rel_error": fraction_text(
            abs(demand / target_supply - ue) / ue
        ),
        "raw_reference_mean_j_per_tick": fraction_text(raw_mean),
        "solar_scale": fraction_text(solar_scale),
        "normalization_horizon_ms": str(normalization_horizon),
        "energy_control": "SERVICE_ONLY_SCALING",
        **HARVEST_MODEL_IDENTITY,
        "harvest_trace_id": raw_trace_id or harvest_trace_identity(raw_trace),
    }


def prepare_energy_material(job: Mapping[str, Any]) -> dict[str, Any]:
    """Compute one immutable energy material for one (taskset, U_E) pair."""
    class _TasksetView:
        task_payload = tuple(job["task_payload"])
        processors = int(job["processors"])
        task_count = int(job["task_count"])

    raw_trace = _PREPARE_RAW_TRACE
    if raw_trace is None:
        raw_trace = tuple(job["raw_trace"])
    if job.get("raw_trace_id") is None:
        material = energy_material(
            _TasksetView(), Fraction(job["target_ue"]), raw_trace,
            kappa=Fraction(job["kappa"]),
        )
    else:
        material = energy_material(
            _TasksetView(), Fraction(job["target_ue"]), raw_trace,
            kappa=Fraction(job["kappa"]),
            raw_trace_id=str(job["raw_trace_id"]),
        )
    return {
        "taskset_id": str(job["taskset_id"]),
        "target_ue": fraction_text(Fraction(job["target_ue"])),
        "material": material,
    }


def set_prepare_raw_trace(raw_trace: Sequence[Fraction]) -> None:
    """Publish one read-only trace for forked preparation workers."""
    global _PREPARE_RAW_TRACE
    _PREPARE_RAW_TRACE = tuple(raw_trace)
