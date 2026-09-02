#!/usr/bin/env python3
"""Analyze paired scheduler LOAD-CROSS results."""

from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3.parallel_prepare import run_independent_jobs, validate_workers


DMR_BOOTSTRAP_REPLICATES = 10000
ACTUAL_UE_RELATIVE_TOLERANCE = Fraction(1, 10**12)

_PLOT_COLORS = {
    "ASAP": "tab:blue",
    "ALAP": "tab:orange",
    "ST": "tab:green",
}

_V5_TIMING_STYLES = {
    "ASAP": {"color": "#0072B2", "marker": "o"},
    "ALAP": {"color": "#D55E00", "marker": "s"},
    "ST": {"color": "#009E73", "marker": "^"},
}
_V5_BLOCKING_LINESTYLES = {
    "BLOCK": "-", "NONBLOCK": "--", "SYNC": "-.",
}


def _plot_style(prefix: str) -> dict[str, Any]:
    """Return the shared three-panel style for one scheduler family."""
    style = experiment.SCHEDULER_STYLES[prefix]
    is_asap = prefix == "ASAP"
    return {
        **style,
        "color": _PLOT_COLORS[prefix],
        "linewidth": 2.2 if is_asap else 1.5,
        "markersize": 6.0 if is_asap else 5.0,
        "zorder": 3 if is_asap else 2,
    }


def _v5_plot_style(scheduler: str) -> dict[str, Any]:
    """Return the deterministic color/marker/line style for one v5 curve."""
    try:
        timing, blocking = scheduler.split("-", 1)
        style = _V5_TIMING_STYLES[timing]
        linestyle = _V5_BLOCKING_LINESTYLES[blocking]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid v5 scheduler style: {scheduler}") from exc
    return {
        **style, "linestyle": linestyle, "linewidth": 1.1,
        "markersize": 3.5, "markerfacecolor": "none",
        "markeredgewidth": 0.8, "alpha": 0.95, "zorder": 3,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0]) if rows else ["target_uc", "target_ue", "scheduler", "acceptance_ratio"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)


def _validate_harvest_model(value: Any, label: str) -> None:
    if value != experiment.HARVEST_MODEL_IDENTITY:
        raise SystemExit(f"{label} does not identify linear_ramp_v1")


def wilson_ci(k: int, n: int, *, z: float = 1.959963984540054) -> tuple[float, float]:
    """Return the two-sided Wilson score interval for a binomial proportion."""
    if n <= 0 or k < 0 or k > n:
        raise ValueError("Wilson interval requires 0 <= k <= n and n > 0")
    p = k / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denominator
    radius = z / denominator * ((p * (1.0 - p) / n) + z2 / (4.0 * n * n)) ** 0.5
    low = max(0.0, centre - radius)
    high = min(1.0, centre + radius)
    return min(low, p), max(high, p)


# Short alias used by analysis-focused callers.
wilson_interval = wilson_ci


def _dmr_bootstrap_seed(
    campaign_seed: int, target_uc: str, target_ue: str, scheduler: str,
    deadline_mode: str | None = None,
) -> int:
    suffix = "" if deadline_mode is None else f"\0{deadline_mode}"
    material = f"DMR_CLUSTER_BOOTSTRAP\0{campaign_seed}\0{target_uc}\0{target_ue}\0{scheduler}{suffix}"
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def dmr_cluster_bootstrap_ci(
    taskset_counts: list[tuple[int, int]], *, seed: int,
    replicates: int = DMR_BOOTSTRAP_REPLICATES,
) -> tuple[float | None, float | None]:
    """Return a reproducible taskset-cluster bootstrap CI for DMR.

    Each pair is (adjudicable_jobs, deadline_miss_jobs).  Resampling is done
    at taskset level, and each replicate recomputes the job-weighted ratio.
    """
    if len(taskset_counts) < 2:
        return None, None
    if replicates <= 0:
        raise ValueError("DMR bootstrap replicates must be positive")
    rng = random.Random(seed)
    estimates: list[float] = []
    n_tasksets = len(taskset_counts)
    for _ in range(replicates):
        adjudicable = 0
        misses = 0
        for _ in range(n_tasksets):
            jobs, task_misses = taskset_counts[rng.randrange(n_tasksets)]
            adjudicable += jobs
            misses += task_misses
        estimates.append(1.0 - misses / adjudicable)
    estimates.sort()

    def percentile(probability: float) -> float:
        position = (len(estimates) - 1) * probability
        lower = int(position)
        upper = min(lower + 1, len(estimates) - 1)
        weight = position - lower
        return estimates[lower] * (1.0 - weight) + estimates[upper] * weight

    low = percentile(0.025)
    high = percentile(0.975)
    return max(0.0, low), min(1.0, high)


def summarize_dmr(
    rows: list[dict[str, Any]], *, target_uc: str, target_ue: str,
    scheduler: str, campaign_seed: int, priority_policy: str = "RM",
    bootstrap_replicates: int = DMR_BOOTSTRAP_REPLICATES,
    deadline_mode: str | None = None,
) -> dict[str, Any]:
    """Aggregate DMR from valid taskset outcomes using job-weighted counts."""
    taskset_counts: list[tuple[int, int]] = []
    for row in rows:
        outcome = row.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("outcome_status") != "AVAILABLE":
            raise ValueError("DMR outcome unavailable")
        try:
            adjudicable = int(outcome["adjudicable_jobs"])
            misses = int(outcome["deadline_miss_jobs"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DMR outcome job counts are missing or invalid") from exc
        if adjudicable <= 0:
            raise ValueError("DMR requires adjudicable_jobs > 0")
        if misses < 0 or misses > adjudicable:
            raise ValueError("DMR requires 0 <= deadline_miss_jobs <= adjudicable_jobs")
        wholepass = row.get("wholepass", outcome.get("wholepass"))
        if wholepass is True and misses != 0:
            raise ValueError("WholePass=True is inconsistent with deadline misses")
        if misses > 0 and wholepass is not False:
            raise ValueError("deadline misses require WholePass=False")
        taskset_counts.append((adjudicable, misses))

    total_adjudicable = sum(jobs for jobs, _misses in taskset_counts)
    total_misses = sum(misses for _jobs, misses in taskset_counts)
    total_on_time = total_adjudicable - total_misses
    dmr = total_on_time / total_adjudicable
    ci_low, ci_high = dmr_cluster_bootstrap_ci(
        taskset_counts,
        seed=_dmr_bootstrap_seed(
            campaign_seed, target_uc, target_ue, scheduler, deadline_mode,
        ),
        replicates=bootstrap_replicates,
    )
    result = {
        "priority_policy": priority_policy,
        "target_uc": target_uc,
        "target_ue": target_ue,
        "scheduler": scheduler,
        "n_tasksets": len(taskset_counts),
        "total_adjudicable_jobs": total_adjudicable,
        "total_deadline_miss_jobs": total_misses,
        "total_on_time_jobs": total_on_time,
        "dmr": dmr,
        "dmr_ci95_low": ci_low,
        "dmr_ci95_high": ci_high,
    }
    if deadline_mode is not None:
        result["deadline_mode"] = experiment.normalize_deadline_mode(deadline_mode)
    return result


def _configured_cells(config: dict[str, Any]) -> tuple[tuple[Fraction, Fraction], ...]:
    raw_cells = config.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise SystemExit("run_config cells are missing or invalid")
    cells = []
    for index, raw_cell in enumerate(raw_cells):
        if not isinstance(raw_cell, list) or len(raw_cell) != 2:
            raise SystemExit(f"run_config cell {index} is invalid")
        try:
            cell = (
                experiment.parse_fraction(raw_cell[0], f"cells[{index}].U_C"),
                experiment.parse_fraction(raw_cell[1], f"cells[{index}].U_E"),
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not all(0 < value <= 1 for value in cell):
            raise SystemExit(f"run_config cell {index} is outside (0,1]")
        if cell not in cells:
            cells.append(cell)
    if len(cells) != len(raw_cells):
        raise SystemExit("run_config cells contain duplicates")
    return tuple(cells)


def _figure_slices(config: dict[str, Any], cells: tuple[tuple[Fraction, Fraction], ...]) -> dict[str, dict[str, str]]:
    raw_slices = config.get("figure_slices")
    if raw_slices is None:
        try:
            return experiment.resolve_figure_slices(cells)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if not isinstance(raw_slices, dict):
        raise SystemExit("run_config figure_slices is invalid")
    expected = {
        "uc_scan": ("target_uc", "target_ue"),
        "ue_scan": ("target_ue", "target_uc"),
    }
    normalized: dict[str, dict[str, str]] = {}
    for name, (x_key, fixed_key) in expected.items():
        entry = raw_slices.get(name)
        if not isinstance(entry, dict):
            raise SystemExit(f"run_config figure_slices.{name} is missing")
        if entry.get("x_key") != x_key or entry.get("fixed_key") != fixed_key:
            raise SystemExit(f"run_config figure_slices.{name} has invalid axes")
        try:
            fixed_value = experiment.parse_fraction(
                entry.get("fixed_value"), f"figure_slices.{name}.fixed_value",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        canonical = experiment.fraction_text(fixed_value)
        if entry.get("fixed_value") != canonical:
            raise SystemExit(f"run_config figure_slices.{name}.fixed_value is not canonical")
        normalized[name] = {
            "x_key": x_key, "fixed_key": fixed_key, "fixed_value": canonical,
        }
    try:
        experiment.resolve_figure_slices(
            cells,
            fixed_ue=experiment.parse_fraction(normalized["uc_scan"]["fixed_value"], "fixed U_E"),
            fixed_uc=experiment.parse_fraction(normalized["ue_scan"]["fixed_value"], "fixed U_C"),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return normalized


def _v4_contract(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_contract = config.get("scan_contract")
    if not isinstance(raw_contract, dict):
        raise SystemExit("v4 run_config scan_contract is missing")
    try:
        expected_contract = experiment.build_scan_contract(raw_contract)
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"v4 run_config scan_contract is invalid: {exc}") from exc
    if raw_contract != expected_contract:
        raise SystemExit("v4 run_config scan_contract is not canonical")
    expected_cells = expected_contract["ordered_cells"]
    if config.get("cells") != expected_cells:
        raise SystemExit("v4 run_config cells do not match scan_contract")
    expected_slices = experiment.build_v4_figure_slices(expected_contract)
    if config.get("figure_slices") != expected_slices:
        raise SystemExit("v4 run_config figure_slices do not match scan_contract")
    return expected_contract, expected_slices


def _axis_plot_values(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "axis_min": contract["axis_display_min"],
        "axis_max": contract["axis_display_max"],
        "axis_ticks": list(contract["axis_ticks"]),
    }


def decimal_axis_labels(axis_ticks: Sequence[Any]) -> list[str]:
    """Format exact axis ticks for paper-facing decimal labels."""
    labels = []
    for value in axis_ticks:
        tick = Fraction(value)
        labels.append("0" if tick == 0 else experiment.decimal_text(tick))
    return labels


def select_scan_rows(
    summaries: list[dict[str, Any]], fixed_key: str, fixed_value: str,
) -> list[dict[str, Any]]:
    target = Fraction(fixed_value)
    return [row for row in summaries if Fraction(row[fixed_key]) == target]


def _validate_scan_rows(
    rows: list[dict[str, Any]], cells: tuple[tuple[Fraction, Fraction], ...],
    *, fixed_key: str, x_key: str, fixed_value: str, label: str,
) -> None:
    fixed_index = 1 if fixed_key == "target_ue" else 0
    x_index = 0 if x_key == "target_uc" else 1
    expected_x = {
        experiment.fraction_text(cell[x_index]) for cell in cells
        if experiment.fraction_text(cell[fixed_index]) == fixed_value
    }
    observed_x = {experiment.fraction_text(Fraction(row[x_key])) for row in rows}
    if not expected_x or observed_x != expected_x:
        raise SystemExit(f"{label} slice does not match configured cells")
    if not any(row["wholepass_ratio"] is not None for row in rows):
        raise SystemExit(f"{label} slice has no valid result rows")


def _validate_dmr_scan_rows(
    rows: list[dict[str, Any]], cells: tuple[tuple[Fraction, Fraction], ...],
    *, fixed_key: str, x_key: str, fixed_value: str, label: str,
) -> None:
    fixed_index = 1 if fixed_key == "target_ue" else 0
    x_index = 0 if x_key == "target_uc" else 1
    expected_x = {
        experiment.fraction_text(cell[x_index]) for cell in cells
        if experiment.fraction_text(cell[fixed_index]) == fixed_value
    }
    observed_x = {experiment.fraction_text(Fraction(row[x_key])) for row in rows}
    if not expected_x or observed_x != expected_x:
        raise SystemExit(f"{label} slice does not match configured cells")
    if not rows:
        raise SystemExit(f"{label} slice has no valid result rows")
    for row in rows:
        if not 0 <= row["dmr"] <= 1:
            raise SystemExit(f"{label} contains DMR outside [0,1]")
        low = row["dmr_ci95_low"]
        high = row["dmr_ci95_high"]
        if low is not None and high is not None and not 0 <= low <= row["dmr"] <= high <= 1:
            raise SystemExit(f"{label} contains invalid DMR bootstrap bounds")


def _validate_v4_slice(
    rows: list[dict[str, Any]], slice_config: dict[str, Any],
    schedulers: list[str], label: str,
) -> None:
    expected = list(slice_config["x_values"])
    for scheduler in schedulers:
        observed = sorted([
            experiment.fraction_text(Fraction(row[slice_config["x_key"]]))
            for row in rows if row["scheduler"] == scheduler
        ], key=Fraction)
        if observed != sorted(expected, key=Fraction):
            raise SystemExit(f"{label} has missing, duplicate, or extra scan points")


def _validate_v5_slice(
    rows: list[dict[str, Any]], slice_config: dict[str, Any],
    deadline_mode: str, schedulers: list[str], label: str,
) -> None:
    expected = sorted(slice_config["x_values"], key=Fraction)
    for scheduler in schedulers:
        observed = sorted(
            [
                experiment.fraction_text(Fraction(row[slice_config["x_key"]]))
                for row in rows if row["scheduler"] == scheduler
            ],
            key=Fraction,
        )
        if observed != expected:
            raise SystemExit(
                f"{label} {deadline_mode} has missing, duplicate, or extra scan points"
            )


def _validate_dmr_ymin(value: float, label: str = "DMR y-axis lower bound") -> float:
    try:
        lower_bound = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number in [0, 1)") from exc
    if not math.isfinite(lower_bound) or not 0.0 <= lower_bound < 1.0:
        raise ValueError(f"{label} must be a finite number in [0, 1)")
    return lower_bound


def _parse_dmr_ymin(value: str) -> float:
    try:
        return _validate_dmr_ymin(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _validate_dmr_ci_lower_bounds(
    rows: list[dict[str, Any]], lower_bound: float, label: str,
) -> None:
    observed = [
        row["dmr_ci95_low"] for row in rows
        if row.get("dmr_ci95_low") is not None
    ]
    if not observed:
        return
    minimum = min(observed)
    if minimum < lower_bound:
        raise ValueError(
            f"{label} DMR y-axis lower bound {lower_bound:g} would clip "
            f"the minimum observed DMR CI lower bound {minimum:g}"
        )


def plot_scan(
    rows: list[dict[str, Any]], output: Path, filename: str, xkey: str,
    schedulers: list[str], xlabel: str, title: str, *, axis_min: str | None = None,
    axis_max: str | None = None, axis_ticks: list[str] | None = None,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
    for axis, (panel, panel_schedulers) in zip(axes, experiment.PANEL_GROUPS.items()):
        for scheduler in panel_schedulers:
            if scheduler not in schedulers:
                continue
            values = [
                row for row in rows
                if row["scheduler"] == scheduler and row["wholepass_ratio"] is not None
            ]
            values.sort(key=lambda row: Fraction(row[xkey]))
            if not values:
                continue
            prefix = scheduler.split("-", 1)[0]
            style = _plot_style(prefix)
            axis.errorbar(
                [float(Fraction(row[xkey])) for row in values],
                [row["wholepass_ratio"] for row in values],
                yerr=[
                    [row["wholepass_ratio"] - row["ci95_low"] for row in values],
                    [row["ci95_high"] - row["wholepass_ratio"] for row in values],
                ],
                marker=style["marker"], linestyle=style["linestyle"],
                color=style["color"], linewidth=style["linewidth"],
                markersize=style["markersize"], zorder=style["zorder"],
                capsize=2, label=scheduler,
            )
        axis.set_xlabel(xlabel)
        axis.set_title(panel)
        axis.set_ylim(0, 1)
        if axis_min is not None and axis_max is not None and axis_ticks is not None:
            axis.set_xlim(float(Fraction(axis_min)), float(Fraction(axis_max)))
            axis.set_xticks([float(Fraction(value)) for value in axis_ticks])
            axis.set_xticklabels(decimal_axis_labels(axis_ticks))
        axis.grid(alpha=0.25)
        axis.legend(fontsize="small")
    axes[0].set_ylabel("Whole-taskset pass ratio")
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(output / filename)
    plt.close(figure)


def _plot_scan_job(job: dict[str, Any]) -> None:
    axis = {
        key: job[key] for key in ("axis_min", "axis_max", "axis_ticks")
        if key in job
    }
    plot_scan(
        job["rows"], Path(job["output"]), job["filename"], job["xkey"],
        job["schedulers"], job["xlabel"], job["title"],
        **axis,
    )


def plot_dmr_scan(
    rows: list[dict[str, Any]], output: Path, filename: str, xkey: str,
    schedulers: list[str], xlabel: str, title: str, ymin: float = 0.0, *,
    axis_min: str | None = None, axis_max: str | None = None,
    axis_ticks: list[str] | None = None,
) -> None:
    ymin = _validate_dmr_ymin(ymin)
    _validate_dmr_ci_lower_bounds(rows, ymin, filename)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
    for axis, (panel, panel_schedulers) in zip(axes, experiment.PANEL_GROUPS.items()):
        for scheduler in panel_schedulers:
            if scheduler not in schedulers:
                continue
            values = [
                row for row in rows
                if row["scheduler"] == scheduler and row["dmr"] is not None
            ]
            values.sort(key=lambda row: Fraction(row[xkey]))
            if not values:
                continue
            prefix = scheduler.split("-", 1)[0]
            style = _plot_style(prefix)
            lower_errors = [
                row["dmr"] - row["dmr_ci95_low"]
                if row["dmr_ci95_low"] is not None else 0.0
                for row in values
            ]
            upper_errors = [
                row["dmr_ci95_high"] - row["dmr"]
                if row["dmr_ci95_high"] is not None else 0.0
                for row in values
            ]
            axis.errorbar(
                [float(Fraction(row[xkey])) for row in values],
                [row["dmr"] for row in values],
                yerr=[lower_errors, upper_errors],
                marker=style["marker"], linestyle=style["linestyle"],
                color=style["color"], linewidth=style["linewidth"],
                markersize=style["markersize"], zorder=style["zorder"],
                capsize=2, label=scheduler,
            )
        axis.set_xlabel(xlabel)
        axis.set_title(panel)
        axis.set_ylim(ymin, 1.0)
        if axis_min is not None and axis_max is not None and axis_ticks is not None:
            axis.set_xlim(float(Fraction(axis_min)), float(Fraction(axis_max)))
            axis.set_xticks([float(Fraction(value)) for value in axis_ticks])
            axis.set_xticklabels(decimal_axis_labels(axis_ticks))
        axis.grid(alpha=0.25)
        axis.legend(fontsize="small")
    axes[0].set_ylabel("Deadline-meeting ratio (DMR)")
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(output / filename)
    plt.close(figure)


def _plot_dmr_scan_job(job: dict[str, Any]) -> None:
    axis = {
        key: job[key] for key in ("axis_min", "axis_max", "axis_ticks")
        if key in job
    }
    plot_dmr_scan(
        job["rows"], Path(job["output"]), job["filename"], job["xkey"],
        job["schedulers"], job["xlabel"], job["title"], job.get("ymin", 0.0),
        **axis,
    )


def _plot_any_scan_job(job: dict[str, Any]) -> None:
    if job.get("v5_composite"):
        if job.get("metric") == "dmr":
            plot_v5_composite_dmr(
                job["slice_rows"], Path(job["output"]), job["filename"],
                job["xkey"], job["schedulers"], job["xlabel"], job["title"],
                job["ymin"], axis_min=job["axis_min"], axis_max=job["axis_max"],
                axis_ticks=job["axis_ticks"],
            )
        else:
            plot_v5_composite_scan(
                job["slice_rows"], Path(job["output"]), job["filename"],
                job["xkey"], job["schedulers"], job["xlabel"], job["title"],
                axis_min=job["axis_min"], axis_max=job["axis_max"],
                axis_ticks=job["axis_ticks"],
            )
        return
    if job.get("composite"):
        if job.get("metric") == "dmr":
            _plot_composite_dmr_job(job)
        else:
            _plot_composite_scan_job(job)
        return
    if job.get("metric") == "dmr":
        _plot_dmr_scan_job(job)
    else:
        _plot_scan_job(job)


def _plot_composite_scan_job(job: dict[str, Any]) -> None:
    plot_composite_scan(
        job["slice_rows"], Path(job["output"]), job["filename"],
        job["xkey"], job["schedulers"], job["xlabel"], job["title"],
        axis_min=job["axis_min"], axis_max=job["axis_max"],
        axis_ticks=job["axis_ticks"],
    )


def _plot_composite_dmr_job(job: dict[str, Any]) -> None:
    plot_composite_dmr(
        job["slice_rows"], Path(job["output"]), job["filename"],
        job["xkey"], job["schedulers"], job["xlabel"], job["title"],
        job["ymin"], axis_min=job["axis_min"], axis_max=job["axis_max"],
        axis_ticks=job["axis_ticks"],
    )


def _axis_values(contract: dict[str, Any]) -> tuple[float, float, list[float], list[str]]:
    return (
        float(Fraction(contract["axis_display_min"])),
        float(Fraction(contract["axis_display_max"])),
        [float(Fraction(value)) for value in contract["axis_ticks"]],
        decimal_axis_labels(contract["axis_ticks"]),
    )


def _draw_composite_axes(
    figure: Any, axes: Any, slice_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    *, xkey: str, schedulers: list[str], xlabel: str, metric: str,
    axis_min: str, axis_max: str, axis_ticks: list[str], ymin: float = 0.0,
) -> None:
    low, high, ticks, ticklabels = _axis_values({
        "axis_display_min": axis_min, "axis_display_max": axis_max,
        "axis_ticks": axis_ticks,
    })
    handles: dict[str, Any] = {}
    for row_index, (slice_config, rows) in enumerate(slice_rows):
        axis = axes[row_index][0]
        for scheduler in schedulers:
            values = [
                row for row in rows if row["scheduler"] == scheduler
                and row[metric] is not None
            ]
            values.sort(key=lambda row: Fraction(row[xkey]))
            if not values:
                continue
            style = _v5_plot_style(scheduler)
            if metric == "wholepass_ratio":
                lower = [row[metric] - row["ci95_low"] for row in values]
                upper = [row["ci95_high"] - row[metric] for row in values]
            else:
                lower = [
                    row[metric] - row["dmr_ci95_low"]
                    if row["dmr_ci95_low"] is not None else 0.0 for row in values
                ]
                upper = [
                    row["dmr_ci95_high"] - row[metric]
                    if row["dmr_ci95_high"] is not None else 0.0 for row in values
                ]
            container = axis.errorbar(
                [float(Fraction(row[xkey])) for row in values],
                [row[metric] for row in values], yerr=[lower, upper],
                marker=style["marker"], linestyle=style["linestyle"],
                color=style["color"], linewidth=style["linewidth"],
                markersize=style["markersize"], zorder=style["zorder"],
                capsize=2, label=scheduler,
            )
            line = container.lines[0]
            handles.setdefault(scheduler, line)
        fixed_name = "U_E" if slice_config["fixed_key"] == "target_ue" else "U_C"
        axis.set_title(slice_config["label"])
        axis.set_xlabel(xlabel)
        axis.set_xlim(low, high)
        axis.set_xticks(ticks)
        axis.set_xticklabels(ticklabels)
        axis.set_ylim(ymin, 1.0)
        axis.grid(alpha=0.25)
        axis.set_ylabel(
            f"{fixed_name}={experiment.decimal_text(slice_config['fixed_value'])}\n"
            f"{('Whole-taskset pass ratio' if metric == 'wholepass_ratio' else 'DMR')}"
        )
    figure.legend(
        [handles[name] for name in experiment.ALL_SCHEDULERS if name in handles],
        [name for name in experiment.ALL_SCHEDULERS if name in handles],
        loc="lower center", ncol=3,
    )
    figure.subplots_adjust(bottom=0.20, hspace=0.35)


def plot_composite_scan(
    slice_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]], output: Path,
    filename: str, xkey: str, schedulers: list[str], xlabel: str, title: str,
    *, axis_min: str, axis_max: str, axis_ticks: list[str],
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(
        len(slice_rows), 1, squeeze=False, figsize=(10, 4 * len(slice_rows)), sharey=True,
    )
    _draw_composite_axes(
        figure, axes, slice_rows, xkey=xkey, schedulers=schedulers, xlabel=xlabel,
        metric="wholepass_ratio", axis_min=axis_min, axis_max=axis_max,
        axis_ticks=axis_ticks,
    )
    figure.suptitle(title)
    figure.savefig(output / filename)
    plt.close(figure)


def plot_composite_dmr(
    slice_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]], output: Path,
    filename: str, xkey: str, schedulers: list[str], xlabel: str, title: str,
    ymin: float, *, axis_min: str, axis_max: str, axis_ticks: list[str],
) -> None:
    ymin = _validate_dmr_ymin(ymin)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(
        len(slice_rows), 1, squeeze=False, figsize=(10, 4 * len(slice_rows)), sharey=True,
    )
    _draw_composite_axes(
        figure, axes, slice_rows, xkey=xkey, schedulers=schedulers, xlabel=xlabel,
        metric="dmr", axis_min=axis_min, axis_max=axis_max, axis_ticks=axis_ticks,
        ymin=ymin,
    )
    figure.suptitle(title)
    figure.savefig(output / filename)
    plt.close(figure)


def _draw_v5_axes(
    figure: Any, axes: Any,
    slice_rows: list[tuple[dict[str, Any], str, list[dict[str, Any]]]],
    *, xkey: str, schedulers: list[str], xlabel: str, metric: str,
    axis_min: str, axis_max: str, axis_ticks: list[str], ymin: float = 0.0,
) -> None:
    low, high, ticks, ticklabels = _axis_values({
        "axis_display_min": axis_min, "axis_display_max": axis_max,
        "axis_ticks": axis_ticks,
    })
    handles: dict[str, Any] = {}
    row_slice_configs: dict[int, dict[str, Any]] = {}
    for axis_row in axes:
        for axis in axis_row:
            axis.set_xlabel(xlabel)
            axis.set_xlim(low, high)
            axis.set_xticks(ticks)
            axis.set_xticklabels(ticklabels)
            axis.set_ylim(ymin, 1.0)
            axis.grid(alpha=0.25)
    for pair_index, (slice_config, deadline_mode, rows) in enumerate(slice_rows):
        row_index = pair_index // 2
        column_index = 0 if deadline_mode == "constrained" else 1
        axis = axes[row_index][column_index]
        row_slice_configs[row_index] = slice_config
        for scheduler in schedulers:
            values = [
                row for row in rows if row["scheduler"] == scheduler
                and row[metric] is not None
            ]
            values.sort(key=lambda row: Fraction(row[xkey]))
            if not values:
                continue
            style = _v5_plot_style(scheduler)
            if metric == "wholepass_ratio":
                lower = [row[metric] - row["ci95_low"] for row in values]
                upper = [row["ci95_high"] - row[metric] for row in values]
            else:
                lower = [
                    row[metric] - row["dmr_ci95_low"]
                    if row["dmr_ci95_low"] is not None else 0.0
                    for row in values
                ]
                upper = [
                    row["dmr_ci95_high"] - row[metric]
                    if row["dmr_ci95_high"] is not None else 0.0
                    for row in values
                ]
            container = axis.errorbar(
                [float(Fraction(row[xkey])) for row in values],
                [row[metric] for row in values], yerr=[lower, upper],
                marker=style["marker"], linestyle=style["linestyle"],
                color=style["color"], linewidth=style["linewidth"],
                markersize=style["markersize"],
                markerfacecolor=style["markerfacecolor"],
                markeredgewidth=style["markeredgewidth"], alpha=style["alpha"],
                zorder=style["zorder"], capsize=2, label=scheduler,
            )
            container.lines[0].set_label(scheduler)
            handles.setdefault(scheduler, container.lines[0])
        axis.set_xlabel(xlabel)
        if deadline_mode == "constrained":
            axis_title = "C ≤ D ≤ T"
        elif any(row.get("implicit_data_reused") for row in rows):
            axis_title = "Implicit deadlines (D=T; RM=DM; shared RM run)"
        else:
            axis_title = "Implicit deadlines (D=T; canonical RM run)"
        axis.set_title(axis_title)
        axis.set_xlim(low, high)
        axis.set_xticks(ticks)
        axis.set_xticklabels(ticklabels)
        axis.set_ylim(ymin, 1.0)
        axis.grid(alpha=0.25)
    for row_index, slice_config in row_slice_configs.items():
        fixed_name = "U_E" if slice_config["fixed_key"] == "target_ue" else "U_C"
        axes[row_index][0].set_ylabel(
            f"{slice_config['label']}: {fixed_name}="
            f"{experiment.decimal_text(slice_config['fixed_value'])}\n"
            f"{('Whole-taskset pass ratio' if metric == 'wholepass_ratio' else 'DMR')}"
        )
    legend_handles = [handles[name] for name in schedulers if name in handles]
    legend_labels = [name for name in schedulers if name in handles]
    figure.legend(
        legend_handles, legend_labels, loc="lower center",
        bbox_to_anchor=(0.5, 0.015), ncol=3,
    )
    figure.subplots_adjust(bottom=0.25, hspace=0.35, wspace=0.25)


def plot_v5_composite_scan(
    slice_rows: list[tuple[dict[str, Any], str, list[dict[str, Any]]]], output: Path,
    filename: str, xkey: str, schedulers: list[str], xlabel: str, title: str,
    *, axis_min: str, axis_max: str, axis_ticks: list[str],
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(
        3, 2, squeeze=False, figsize=(11, 12), sharey=True,
    )
    _draw_v5_axes(
        figure, axes, slice_rows, xkey=xkey, schedulers=schedulers, xlabel=xlabel,
        metric="wholepass_ratio", axis_min=axis_min, axis_max=axis_max,
        axis_ticks=axis_ticks,
    )
    figure.suptitle(title)
    figure.savefig(output / filename)
    plt.close(figure)


def plot_v5_composite_dmr(
    slice_rows: list[tuple[dict[str, Any], str, list[dict[str, Any]]]], output: Path,
    filename: str, xkey: str, schedulers: list[str], xlabel: str, title: str,
    ymin: float, *, axis_min: str, axis_max: str, axis_ticks: list[str],
) -> None:
    ymin = _validate_dmr_ymin(ymin)
    _validate_dmr_ci_lower_bounds(
        [row for _slice, _mode, rows in slice_rows for row in rows], ymin, filename,
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(
        3, 2, squeeze=False, figsize=(11, 12), sharey=True,
    )
    _draw_v5_axes(
        figure, axes, slice_rows, xkey=xkey, schedulers=schedulers, xlabel=xlabel,
        metric="dmr", axis_min=axis_min, axis_max=axis_max,
        axis_ticks=axis_ticks, ymin=ymin,
    )
    figure.suptitle(title)
    figure.savefig(output / filename)
    plt.close(figure)


def _slice_csv_rows(
    slice_config: dict[str, Any], rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fixed_name = "U_E" if slice_config["fixed_key"] == "target_ue" else "U_C"
    result = []
    for row in rows:
        scheduler = row["scheduler"]
        timing, blocking = scheduler.split("-", 1)
        result_row = {
            "scan_label": slice_config["label"],
            "fixed_key": slice_config["fixed_key"],
            "fixed_value": slice_config["fixed_value"],
            "x_key": slice_config["x_key"],
            "x_value": row[slice_config["x_key"]],
            "scheduler": scheduler,
            "timing_policy": timing,
            "blocking_policy": blocking,
            "whole_taskset_pass_ratio": row["wholepass_ratio"],
            "pass_count": row["n_wholepass"],
            "sample_total": row["n_total"],
        }
        if "deadline_mode" in row:
            result_row["deadline_mode"] = row["deadline_mode"]
        for key in (
            "figure_priority_policy", "source_priority_policy",
            "implicit_data_reused", "implicit_canonical_priority_policy",
            "implicit_priority_equivalence", "source_run_identity",
        ):
            if key in row:
                result_row[key] = row[key]
        result.append(result_row)
    return result


def _dmr_slice_csv_rows(
    slice_config: dict[str, Any], rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        scheduler = row["scheduler"]
        timing, blocking = scheduler.split("-", 1)
        result_row = {
            "scan_label": slice_config["label"],
            "fixed_key": slice_config["fixed_key"],
            "fixed_value": slice_config["fixed_value"],
            "x_key": slice_config["x_key"],
            "x_value": row[slice_config["x_key"]],
            "scheduler": scheduler,
            "timing_policy": timing,
            "blocking_policy": blocking,
            "dmr": row["dmr"],
            "dmr_ci95_low": row["dmr_ci95_low"],
            "dmr_ci95_high": row["dmr_ci95_high"],
            "deadline_miss_count": row["total_deadline_miss_jobs"],
            "adjudicable_job_total": row["total_adjudicable_jobs"],
        }
        if "deadline_mode" in row:
            result_row["deadline_mode"] = row["deadline_mode"]
        for key in (
            "figure_priority_policy", "source_priority_policy",
            "implicit_data_reused", "implicit_canonical_priority_policy",
            "implicit_priority_equivalence", "source_run_identity",
        ):
            if key in row:
                result_row[key] = row[key]
        result.append(result_row)
    return result


def _v6_config_signature(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in config.items()
        if key not in {
            "priority_policy", "deadline_modes", "expected_request_count",
            "expected_taskset_count", "run_identity", "status", "telemetry",
            "execution", "workers", "keep_traces", "parse_concurrency",
        }
    }


def _v6_validate_config(
    root: Path, *, expected_policy: str | None = None,
) -> tuple[dict[str, Any], tuple[tuple[Fraction, Fraction], ...], dict[str, Any], dict[str, Any], str]:
    try:
        config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"v6 run_config cannot be read: {root}") from exc
    if config.get("experiment") != experiment.V6_EXPERIMENT:
        raise SystemExit("v6 shared source requires scheduler-load-cross-v6")
    if config.get("domain") != experiment.V6_DOMAIN:
        raise SystemExit("v6 run_config domain mismatch")
    if config.get("campaign_contract") != experiment.V6_CAMPAIGN_CONTRACT:
        raise SystemExit("v6 campaign contract mismatch")
    for key, expected in (
        ("implicit_priority_equivalence", experiment.V6_IMPLICIT_PRIORITY_EQUIVALENCE),
        ("implicit_canonical_priority_policy", experiment.V6_IMPLICIT_CANONICAL_PRIORITY_POLICY),
        ("implicit_reuse_policy", experiment.V6_IMPLICIT_REUSE_POLICY),
        ("shared_implicit_contract_version", experiment.V6_SHARED_IMPLICIT_CONTRACT_VERSION),
    ):
        if config.get(key) != expected:
            raise SystemExit(f"v6 {key} mismatch")
    _validate_harvest_model(
        {key: config.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
        "v6 run_config harvest model",
    )
    if config.get("use_real_solar_data") is not False:
        raise SystemExit("v6 run_config must disable real solar data")
    try:
        priority_policy = experiment.normalize_scheduler_priority_policy(
            config.get("priority_policy")
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if expected_policy is not None and priority_policy != expected_policy:
        raise SystemExit(
            f"v6 run_config priority policy mismatch: expected {expected_policy}"
        )
    expected_modes = list(experiment.deadline_modes_for_priority_policy(priority_policy))
    if config.get("deadline_modes") != expected_modes:
        raise SystemExit("v6 deadline mode plan does not match priority policy")
    cells = _configured_cells(config)
    scan_contract, figure_slices = _v4_contract(config)
    canonical_cells = tuple(
        (Fraction(uc), Fraction(ue))
        for uc, ue in scan_contract["ordered_cells"]
    )
    if canonical_cells != cells:
        raise SystemExit("v6 cells do not match scan_contract")
    schedulers = list(config.get("schedulers", ()))
    try:
        if tuple(experiment.parse_schedulers(",".join(schedulers))) != tuple(schedulers):
            raise SystemExit("v6 schedulers are not canonical and unique")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if tuple(schedulers) != tuple(experiment.ALL_SCHEDULERS):
        raise SystemExit("v6 campaign requires all nine canonical schedulers")
    if tuple(cells) == tuple(experiment.FORMAL_CELLS):
        try:
            experiment.validate_v6_main_figure(
                cells, schedulers,
                horizon_ms=int(config.get("simulation_horizon_ms", 0)),
                priority_policy=priority_policy,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    samples = int(config.get("samples_per_cell", 0))
    expected_requests = len(cells) * samples * len(schedulers) * len(expected_modes)
    expected_tasksets = len({uc for uc, _ue in cells}) * samples * len(expected_modes)
    if config.get("expected_request_count") != expected_requests:
        raise SystemExit("v6 expected_request_count is inconsistent")
    if config.get("expected_taskset_count") != expected_tasksets:
        raise SystemExit("v6 expected_taskset_count is inconsistent")
    if config.get("run_identity") != experiment.run_identity(config):
        raise SystemExit("v6 run_identity is invalid")
    return config, cells, scan_contract, figure_slices, priority_policy


def _v6_validate_energy(row: dict[str, Any]) -> None:
    energy = row.get("energy")
    if not isinstance(energy, dict):
        raise SystemExit("v6 result energy material is missing")
    _validate_harvest_model(
        {key: energy.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
        "v6 energy harvest model",
    )
    try:
        ue = Fraction(row["target_ue"])
        if Fraction(energy["eta"]) != experiment.eta_for_ue(ue):
            raise SystemExit("v6 eta != 1/U_E")
        demand = Fraction(energy["P_dem_j_per_tick"])
        supply = Fraction(energy["target_supply_mean_j_per_tick"])
        raw = Fraction(energy["raw_reference_mean_j_per_tick"])
        if demand / supply != ue or Fraction(energy["solar_scale"]) * raw != supply:
            raise SystemExit("v6 U_E service identity mismatch")
        runtime_supply = Fraction(
            energy["runtime_configured_average_supply_j_per_tick"]
        )
        actual_ue = Fraction(energy["actual_ue"])
        abs_error = Fraction(energy["actual_ue_abs_error"])
        rel_error = Fraction(energy["actual_ue_rel_error"])
        minus_error = Fraction(energy["actual_ue_minus_target_ue"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit("v6 runtime average supply and actual U_E are invalid") from exc
    if runtime_supply <= 0 or actual_ue <= 0:
        raise SystemExit("v6 runtime average supply and actual U_E must be positive")
    calculated = demand / runtime_supply
    calculated_abs = abs(calculated - ue)
    calculated_rel = calculated_abs / ue
    if (
        actual_ue != calculated or abs_error != calculated_abs
        or rel_error != calculated_rel or minus_error != calculated - ue
    ):
        raise SystemExit("v6 actual U_E does not match runtime average supply")
    if calculated_rel > ACTUAL_UE_RELATIVE_TOLERANCE:
        raise SystemExit("v6 actual U_E relative error exceeds 1e-12")


def _v7_validate_config(
    root: Path,
) -> tuple[dict[str, Any], tuple[tuple[Fraction, Fraction], ...], dict[str, Any], dict[str, Any], str]:
    try:
        config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"v7 run_config cannot be read: {root}") from exc
    if config.get("experiment") != experiment.V7_EXPERIMENT:
        raise SystemExit("v7 run_config experiment mismatch")
    if config.get("domain") != experiment.V7_DOMAIN:
        raise SystemExit("v7 run_config domain mismatch")
    campaign = config.get("campaign")
    try:
        spec = experiment.v7_campaign_spec(campaign)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"v7 campaign is invalid: {exc}") from exc
    if config.get("campaign_contract") != spec["campaign_contract"]:
        raise SystemExit("v7 campaign contract mismatch")
    if config.get("energy_control") != spec["energy_control"]:
        raise SystemExit("v7 energy control does not match campaign")
    if config["energy_control"] == "FIXED_ABSOLUTE_SUPPLY":
        expected_levels = {
            level: {
                "reference_ue": str(experiment.V7_REFERENCE_UES[level]),
                "fixed_supply_mean_j_per_tick": str(experiment.V7_FIXED_SUPPLIES[level]),
            }
            for level in ("low", "medium", "high")
        }
        if config.get("fixed_supply_levels") != expected_levels:
            raise SystemExit("v7 fixed supply level map is not exact")
    if config.get("deadline_modes") != ["constrained"]:
        raise SystemExit("v7 campaigns require constrained deadline mode only")
    try:
        priority_policy = experiment.normalize_scheduler_priority_policy(
            config.get("priority_policy")
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    cells = _configured_cells(config)
    if tuple(cells) != tuple(spec["cells"]):
        raise SystemExit("v7 cells do not match the frozen campaign grid")
    if config.get("scan_contract") != spec["scan_contract"]:
        raise SystemExit("v7 scan_contract is not canonical")
    if config.get("figure_slices") != spec["figure_slices"]:
        raise SystemExit("v7 figure_slices are not canonical")
    schedulers = list(config.get("schedulers", ()))
    try:
        if tuple(experiment.parse_schedulers(",".join(schedulers))) != tuple(schedulers):
            raise SystemExit("v7 schedulers are not canonical and unique")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if tuple(schedulers) != tuple(experiment.ALL_SCHEDULERS):
        raise SystemExit("v7 campaign requires all nine canonical schedulers")
    samples = int(config.get("samples_per_cell", 0))
    expected_requests = len(cells) * samples * len(schedulers)
    expected_tasksets = len({uc for uc, _ue in cells}) * samples
    if config.get("expected_request_count") != expected_requests:
        raise SystemExit("v7 expected_request_count is inconsistent")
    if config.get("expected_taskset_count") != expected_tasksets:
        raise SystemExit("v7 expected_taskset_count is inconsistent")
    if config.get("run_identity") != experiment.run_identity(config):
        raise SystemExit("v7 run_identity is invalid")
    return config, cells, spec["scan_contract"], spec["figure_slices"], priority_policy


def _v7_validate_energy(
    row: dict[str, Any], config: dict[str, Any], taskset: dict[str, Any],
) -> None:
    energy = row.get("energy")
    if not isinstance(energy, dict):
        raise SystemExit("v7 result energy material is missing")
    _validate_harvest_model(
        {key: energy.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
        "v7 energy harvest model",
    )
    try:
        reference_ue = Fraction(row["target_ue"])
        demand = Fraction(energy["P_dem_j_per_tick"])
        supply = Fraction(energy["target_supply_mean_j_per_tick"])
        raw = Fraction(energy["raw_reference_mean_j_per_tick"])
        runtime_supply = Fraction(energy["runtime_configured_average_supply_j_per_tick"])
        actual_ue = Fraction(energy["actual_ue"])
        abs_error = Fraction(energy["actual_ue_abs_error"])
        rel_error = Fraction(energy["actual_ue_rel_error"])
        minus_error = Fraction(energy["actual_ue_minus_target_ue"])
        if energy.get("energy_control") != config["energy_control"]:
            raise SystemExit("v7 result energy control does not match run_config")
        if Fraction(energy["target_ue"]) != reference_ue:
            raise SystemExit("v7 energy target U_E does not match request reference")
        if config["energy_control"] == "FIXED_ABSOLUTE_SUPPLY":
            level = str(energy["energy_level"])
            expected = experiment.V7_FIXED_SUPPLIES[level]
            if Fraction(energy["fixed_supply_mean_j_per_tick"]) != expected:
                raise SystemExit("v7 fixed supply does not match energy level")
            if Fraction(energy["reference_ue"]) != reference_ue:
                raise SystemExit("v7 fixed-supply reference U_E is invalid")
            if experiment.V7_REFERENCE_UES[level] != reference_ue:
                raise SystemExit("v7 fixed-supply level does not match reference U_E")
            if actual_ue != demand / supply or runtime_supply != supply:
                raise SystemExit("v7 fixed-supply actual U_E mismatch")
        else:
            if Fraction(energy["eta"]) != experiment.eta_for_ue(reference_ue):
                raise SystemExit("v7 eta != 1/U_E")
            if demand / supply != reference_ue:
                raise SystemExit("v7 service-only supply identity mismatch")
            if actual_ue != reference_ue or runtime_supply != supply:
                raise SystemExit("v7 service-only actual U_E mismatch")
        if Fraction(energy["solar_scale"]) * raw != supply:
            raise SystemExit("v7 harvest identity mismatch")
        payload = json.loads(taskset["task_input_json"])
        powers = sorted((Fraction(item["P"]) for item in payload), reverse=True)
        burst = sum(
            powers[: min(int(config["processors"]), len(payload))], Fraction(0),
        )
        if Fraction(energy["E_burst_j"]) != burst:
            raise SystemExit("v7 burst energy changed")
        kappa = Fraction(config["kappa"])
        if (
            Fraction(energy["battery_capacity_j"]) != kappa * burst
            or Fraction(energy["initial_energy_j"]) != kappa * burst / 2
        ):
            raise SystemExit("v7 battery or initial energy rule changed")
        if runtime_supply <= 0 or actual_ue <= 0:
            raise SystemExit("v7 runtime average supply and actual U_E must be positive")
        calculated_abs = abs(actual_ue - reference_ue)
        if abs_error != calculated_abs or rel_error != calculated_abs / reference_ue:
            raise SystemExit("v7 actual U_E error fields are invalid")
        if minus_error != actual_ue - reference_ue:
            raise SystemExit("v7 actual U_E delta field is invalid")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit("v7 energy material is invalid") from exc


def _v6_load_dataset(
    root: Path, *, expected_policy: str | None = None,
) -> dict[str, Any]:
    config, cells, scan_contract, figure_slices, priority_policy = _v6_validate_config(
        root, expected_policy=expected_policy,
    )
    try:
        tasksets = read_jsonl(root / "tasksets.jsonl")
        requests = read_jsonl(root / "requests.jsonl")
        results = read_jsonl(root / "results.jsonl")
    except OSError as exc:
        raise SystemExit(f"v6 dataset cannot be read: {root}") from exc
    modes = tuple(config["deadline_modes"])
    samples = int(config["samples_per_cell"])
    schedulers = list(config["schedulers"])
    if {row.get("deadline_mode") for row in tasksets} != set(modes):
        raise SystemExit("v6 tasksets do not contain exactly the configured modes")
    if {row.get("deadline_mode") for row in requests} != set(modes):
        raise SystemExit("v6 requests do not contain exactly the configured modes")
    if {row.get("deadline_mode") for row in results} != set(modes):
        raise SystemExit("v6 results do not contain exactly the configured modes")
    expected_request_count = int(config["expected_request_count"])
    expected_taskset_count = int(config["expected_taskset_count"])
    if len(tasksets) != expected_taskset_count:
        raise SystemExit("v6 taskset count does not match the run contract")
    if len(requests) != expected_request_count or len(results) != expected_request_count:
        raise SystemExit("v6 request/result count does not match the run contract")
    taskset_by_id = {str(row.get("taskset_id")): row for row in tasksets}
    if len(taskset_by_id) != len(tasksets):
        raise SystemExit("v6 tasksets contain duplicate identities")
    for taskset in tasksets:
        mode = experiment.normalize_deadline_mode(taskset.get("deadline_mode"))
        try:
            payload = json.loads(taskset["task_input_json"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit("v6 taskset task payload is missing or invalid") from exc
        if not isinstance(payload, list):
            raise SystemExit("v6 taskset task payload is invalid")
        if taskset.get("canonical_task_power") is False:
            raise SystemExit("v6 taskset uses non-canonical task power")
        for item in payload:
            try:
                c, d, t = int(item["C"]), int(item["D"]), int(item["T"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit("v6 taskset deadline fields are invalid") from exc
            if not (0 < c <= d <= t):
                raise SystemExit("v6 taskset violates C <= D <= T")
            if mode == "implicit" and d != t:
                raise SystemExit("v6 implicit taskset violates D == T")
    request_by_id: dict[str, dict[str, Any]] = {}
    for request in requests:
        request_id = str(request.get("request_id"))
        if request_id in request_by_id:
            raise SystemExit("v6 requests contain duplicate request IDs")
        request_by_id[request_id] = request
        if (
            request.get("experiment") != experiment.V6_EXPERIMENT
            or request.get("domain") != experiment.V6_DOMAIN
            or request.get("priority_policy") != priority_policy
            or request.get("deadline_mode") not in modes
        ):
            raise SystemExit("v6 request identity does not match run_config")
        taskset = taskset_by_id.get(str(request.get("taskset_id")))
        if taskset is None or taskset.get("deadline_mode") != request.get("deadline_mode"):
            raise SystemExit("v6 request/taskset identity mismatch")
        _validate_harvest_model(
            {key: request.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
            "v6 request harvest model",
        )
    observed_ids = [str(row.get("request_id")) for row in results]
    if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != set(request_by_id):
        raise SystemExit("v6 results have duplicate, missing, or unexpected request IDs")
    technical = 0
    for result in results:
        request = request_by_id[str(result["request_id"])]
        if (
            result.get("experiment") != experiment.V6_EXPERIMENT
            or result.get("domain") != experiment.V6_DOMAIN
            or result.get("priority_policy") != priority_policy
            or result.get("deadline_mode") != request.get("deadline_mode")
            or result.get("simulation_status") not in {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
            or result.get("technical_error") is not None
        ):
            technical += 1
            continue
        for key in (
            "taskset_id", "taskset_hash", "target_uc", "target_ue",
            "generation_index", "scheduler", "scheduler_cli",
        ):
            if result.get(key) != request.get(key):
                raise SystemExit(f"v6 result/request identity mismatch for {key}")
        if result.get("scheduler") not in schedulers:
            raise SystemExit("v6 result contains an unknown scheduler")
        _validate_harvest_model(
            {key: result.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
            "v6 result harvest model",
        )
        _validate_energy = _v6_validate_energy(result)
        del _validate_energy
    groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    for request in requests:
        key = (
            str(request["deadline_mode"]), str(request["target_uc"]),
            str(request["target_ue"]), int(request["generation_index"]),
        )
        groups.setdefault(key, []).append(request)
    expected_groups = {
        (mode, str(uc), str(ue), index)
        for mode in modes
        for uc, ue in cells
        for index in range(samples)
    }
    if set(groups) != expected_groups:
        raise SystemExit("v6 requests contain missing or extra cells/samples")
    for key, group in groups.items():
        if len(group) != len(schedulers) or {row["scheduler"] for row in group} != set(schedulers):
            raise SystemExit(f"v6 scheduler coverage is incomplete: {key}")
    if technical:
        raise SystemExit(f"v6 technical failures are not scientific rows: {technical}")
    return {
        "root": root, "config": config, "cells": cells,
        "scan_contract": scan_contract, "figure_slices": figure_slices,
        "priority_policy": priority_policy, "tasksets": tasksets,
        "requests": requests, "results": results, "modes": modes,
        "schedulers": schedulers, "samples": samples,
        "run_identity": config["run_identity"],
    }


def _v6_validate_shared_source(
    target: dict[str, Any], source_root: Path,
) -> dict[str, Any]:
    if source_root.resolve() == target["root"].resolve():
        raise SystemExit("shared implicit source must be a separate v6 RM run")
    source = _v6_load_dataset(source_root, expected_policy="RM")
    if source["modes"] != experiment.DEADLINE_MODES:
        raise SystemExit("shared RM source must contain constrained and implicit modes")
    if target["priority_policy"] != "DM" or target["modes"] != ("constrained",):
        raise SystemExit("shared implicit source is only valid for a DM v6 run")
    if _v6_config_signature(source["config"]) != _v6_config_signature(target["config"]):
        raise SystemExit("shared v6 source and DM run configurations do not match")
    implicit_requests = [
        row for row in source["requests"] if row.get("deadline_mode") == "implicit"
    ]
    implicit_results = [
        row for row in source["results"] if row.get("deadline_mode") == "implicit"
    ]
    implicit_tasksets = [
        row for row in source["tasksets"] if row.get("deadline_mode") == "implicit"
    ]
    expected = len(source["cells"]) * source["samples"] * len(source["schedulers"])
    if len(implicit_requests) != expected or len(implicit_results) != expected:
        raise SystemExit("shared RM source implicit data is incomplete")
    if len(implicit_tasksets) != len({uc for uc, _ue in source["cells"]}) * source["samples"]:
        raise SystemExit("shared RM source implicit tasksets are incomplete")
    if any(row.get("priority_policy") != "RM" for row in (*implicit_requests, *implicit_results)):
        raise SystemExit("shared implicit source contains non-RM data")
    return {
        **source,
        "requests": implicit_requests,
        "results": implicit_results,
        "tasksets": implicit_tasksets,
        "modes": ("implicit",),
    }


def _v6_build_summaries(
    rows: list[dict[str, Any]], *, config: dict[str, Any],
    figure_priority_policy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    schedulers = list(config["schedulers"])
    samples = int(config["samples_per_cell"])
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            (str(row["deadline_mode"]), str(row["target_uc"]), str(row["target_ue"])),
            [],
        ).append(row)
    summaries: list[dict[str, Any]] = []
    dmr_summaries: list[dict[str, Any]] = []
    for (mode, uc, ue), group in sorted(
        groups.items(), key=lambda item: (
            experiment.DEADLINE_MODES.index(item[0][0]),
            Fraction(item[0][1]), Fraction(item[0][2]),
        ),
    ):
        for scheduler in schedulers:
            selected = [row for row in group if row["scheduler"] == scheduler]
            if len(selected) != samples:
                raise SystemExit("v6 missing scheduler result in cell")
            n_total = len(selected)
            n_wholepass = sum(
                row.get("wholepass", row.get("taskset_pass")) is True
                for row in selected
            )
            n_miss = sum(row.get("deadline_miss") is True for row in selected)
            ci_low, ci_high = wilson_ci(n_wholepass, n_total)
            source_policy = str(selected[0]["priority_policy"])
            metadata = {
                "figure_priority_policy": figure_priority_policy,
                "source_priority_policy": source_policy,
                "implicit_data_reused": bool(selected[0].get("_v6_implicit_data_reused", False)),
                "implicit_canonical_priority_policy": experiment.V6_IMPLICIT_CANONICAL_PRIORITY_POLICY,
                "implicit_priority_equivalence": experiment.V6_IMPLICIT_PRIORITY_EQUIVALENCE,
                "source_run_identity": selected[0]["_v6_source_run_identity"],
            }
            summary = {
                "priority_policy": source_policy,
                "target_uc": uc, "target_ue": ue, "scheduler": scheduler,
                "runtime_configured_average_supply_j_per_tick": selected[0]["energy"][
                    "runtime_configured_average_supply_j_per_tick"
                ],
                "actual_ue": selected[0]["energy"]["actual_ue"],
                "actual_ue_minus_target_ue": selected[0]["energy"][
                    "actual_ue_minus_target_ue"
                ],
                "n_total": n_total, "n_valid_tasksets": n_total,
                "n_technical": 0, "n_wholepass": n_wholepass,
                "wholepass_ratio": n_wholepass / n_total,
                "ci95_low": ci_low, "ci95_high": ci_high,
                "n_schedulable": n_wholepass, "n_deadline_miss": n_miss,
                "acceptance_ratio": n_wholepass / n_total,
                "deadline_mode": mode, **metadata,
            }
            summaries.append(summary)
            dmr = summarize_dmr(
                selected, target_uc=uc, target_ue=ue, scheduler=scheduler,
                campaign_seed=int(config.get("seed", 0)),
                priority_policy=source_policy, deadline_mode=mode,
            )
            dmr.update(metadata)
            dmr_summaries.append(dmr)
    return summaries, dmr_summaries


def _analyze_v6(
    root: Path, *, shared_implicit_run_dir: Path | None,
    analysis_workers: int, uc_dmr_ymin: float, ue_dmr_ymin: float,
) -> dict[str, Any]:
    target = _v6_load_dataset(root)
    source = None
    if target["priority_policy"] == "DM":
        if shared_implicit_run_dir is None:
            raise SystemExit(
                "DM v6 analysis requires --shared-implicit-run-dir before plotting"
            )
        source = _v6_validate_shared_source(target, shared_implicit_run_dir)
    elif shared_implicit_run_dir is not None:
        raise SystemExit("--shared-implicit-run-dir is only valid for DM v6 analysis")
    rows: list[dict[str, Any]] = []
    for row in target["results"]:
        rows.append({
            **row,
            "_v6_source_run_identity": target["run_identity"],
            "_v6_implicit_data_reused": False,
        })
    if source is not None:
        for row in source["results"]:
            if row.get("deadline_mode") == "implicit":
                rows.append({
                    **row,
                    "_v6_source_run_identity": source["run_identity"],
                    "_v6_implicit_data_reused": True,
                })
    summaries, dmr_summaries = _v6_build_summaries(
        rows, config=target["config"],
        figure_priority_policy=target["priority_policy"],
    )
    scan_contract = target["scan_contract"]
    figure_slices = target["figure_slices"]
    axis = _axis_plot_values(scan_contract)
    uc_slice_rows = []
    uc_dmr_slice_rows = []
    for index, uc_slice in enumerate(figure_slices["uc_scans"]):
        for mode in experiment.DEADLINE_MODES:
            uc_rows = [
                row for row in summaries
                if row["deadline_mode"] == mode
                and row[uc_slice["fixed_key"]] == uc_slice["fixed_value"]
            ]
            dmr_rows = [
                row for row in dmr_summaries
                if row["deadline_mode"] == mode
                and row[uc_slice["fixed_key"]] == uc_slice["fixed_value"]
            ]
            _validate_v5_slice(uc_rows, uc_slice, mode, target["schedulers"], f"U_C[{index}]")
            _validate_v5_slice(dmr_rows, uc_slice, mode, target["schedulers"], f"U_C DMR[{index}]")
            uc_slice_rows.append((uc_slice, mode, uc_rows))
            uc_dmr_slice_rows.append((uc_slice, mode, dmr_rows))
    ue_slice_rows = []
    ue_dmr_slice_rows = []
    for index, ue_slice in enumerate(figure_slices["ue_scans"]):
        for mode in experiment.DEADLINE_MODES:
            ue_rows = [
                row for row in summaries
                if row["deadline_mode"] == mode
                and row[ue_slice["fixed_key"]] == ue_slice["fixed_value"]
            ]
            dmr_rows = [
                row for row in dmr_summaries
                if row["deadline_mode"] == mode
                and row[ue_slice["fixed_key"]] == ue_slice["fixed_value"]
            ]
            _validate_v5_slice(ue_rows, ue_slice, mode, target["schedulers"], f"U_E[{index}]")
            _validate_v5_slice(dmr_rows, ue_slice, mode, target["schedulers"], f"U_E DMR[{index}]")
            ue_slice_rows.append((ue_slice, mode, ue_rows))
            ue_dmr_slice_rows.append((ue_slice, mode, dmr_rows))
    write_csv(root / "summary.csv", summaries)
    write_csv(root / "summary_dmr.csv", dmr_summaries)
    write_csv(root / "figure_scheduler_uc_slices.csv", [
        row for slice_config, _mode, values in uc_slice_rows
        for row in _slice_csv_rows(slice_config, values)
    ])
    write_csv(root / "figure_scheduler_ue_slices.csv", [
        row for slice_config, _mode, values in ue_slice_rows
        for row in _slice_csv_rows(slice_config, values)
    ])
    write_csv(root / "figure_scheduler_uc_slices_dmr.csv", [
        row for slice_config, _mode, values in uc_dmr_slice_rows
        for row in _dmr_slice_csv_rows(slice_config, values)
    ])
    write_csv(root / "figure_scheduler_ue_slices_dmr.csv", [
        row for slice_config, _mode, values in ue_dmr_slice_rows
        for row in _dmr_slice_csv_rows(slice_config, values)
    ])
    plot_jobs = [
        {"v5_composite": True, "slice_rows": uc_slice_rows, "output": str(root),
         "filename": "figure_scheduler_uc_slices.png", "xkey": "target_uc",
         "schedulers": target["schedulers"], "xlabel": "U_C",
         "title": f"{target['priority_policy']} — Whole-taskset pass ratio versus U_C", **axis},
        {"v5_composite": True, "slice_rows": ue_slice_rows, "output": str(root),
         "filename": "figure_scheduler_ue_slices.png", "xkey": "target_ue",
         "schedulers": target["schedulers"], "xlabel": "U_E",
         "title": f"{target['priority_policy']} — Whole-taskset pass ratio versus U_E", **axis},
        {"v5_composite": True, "slice_rows": uc_dmr_slice_rows, "output": str(root),
         "filename": "figure_scheduler_uc_slices_dmr.png", "xkey": "target_uc",
         "schedulers": target["schedulers"], "xlabel": "U_C", "metric": "dmr",
         "ymin": uc_dmr_ymin,
         "title": f"{target['priority_policy']} — Job-level deadline-meeting ratio (DMR) versus U_C", **axis},
        {"v5_composite": True, "slice_rows": ue_dmr_slice_rows, "output": str(root),
         "filename": "figure_scheduler_ue_slices_dmr.png", "xkey": "target_ue",
         "schedulers": target["schedulers"], "xlabel": "U_E", "metric": "dmr",
         "ymin": ue_dmr_ymin,
         "title": f"{target['priority_policy']} — Job-level deadline-meeting ratio (DMR) versus U_E", **axis},
    ]
    try:
        import matplotlib
        matplotlib.use("Agg")
        run_independent_jobs(plot_jobs, _plot_any_scan_job, workers=analysis_workers)
    except ImportError:
        pass
    report = {
        "complete": True, "experiment": experiment.V6_EXPERIMENT,
        "domain": experiment.V6_DOMAIN,
        "priority_policy": target["priority_policy"],
        "deadline_modes": list(target["modes"]),
        "shared_implicit_run_identity": source["run_identity"] if source else None,
        "implicit_data_reused": source is not None,
        "tasksets": len(target["tasksets"]), "requests": len(rows),
        "results": len(rows), "summary_rows": len(summaries),
        "dmr_summary_rows": len(dmr_summaries), "technical": 0,
        "harvest_model": experiment.HARVEST_MODEL,
        "expected_request_count": target["config"]["expected_request_count"],
    }
    (root / "analysis_report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    return report


def analyze(
    root: Path, *, analysis_workers: int = 1,
    uc_dmr_ymin: float = 0.0, ue_dmr_ymin: float = 0.0,
    shared_implicit_run_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        initial_config = json.loads(
            (root / "run_config.json").read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError):
        initial_config = {}
    if shared_implicit_run_dir is not None and (
        initial_config.get("experiment") in {
            experiment.V3_EXPERIMENT,
            experiment.V4_EXPERIMENT,
            experiment.V5_EXPERIMENT,
            experiment.V7_EXPERIMENT,
        }
        or initial_config.get("domain") in {
            experiment.V3_DOMAIN,
            experiment.V4_DOMAIN,
            experiment.V5_DOMAIN,
            experiment.V7_DOMAIN,
        }
    ):
        raise SystemExit(
            "--shared-implicit-run-dir is only valid for "
            "scheduler-load-cross-v6"
        )
    if initial_config.get("experiment") == experiment.V6_EXPERIMENT:
        validate_workers(analysis_workers, "analysis-workers")
        uc_dmr_ymin = _validate_dmr_ymin(uc_dmr_ymin, "U_C DMR y-axis lower bound")
        ue_dmr_ymin = _validate_dmr_ymin(ue_dmr_ymin, "U_E DMR y-axis lower bound")
        return _analyze_v6(
            root, shared_implicit_run_dir=shared_implicit_run_dir,
            analysis_workers=analysis_workers, uc_dmr_ymin=uc_dmr_ymin,
            ue_dmr_ymin=ue_dmr_ymin,
        )
    validate_workers(analysis_workers, "analysis-workers")
    uc_dmr_ymin = _validate_dmr_ymin(uc_dmr_ymin, "U_C DMR y-axis lower bound")
    ue_dmr_ymin = _validate_dmr_ymin(ue_dmr_ymin, "U_E DMR y-axis lower bound")
    analysis_started = time.perf_counter()
    validation_started = analysis_started
    config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    is_v7 = config.get("experiment") == experiment.V7_EXPERIMENT
    if is_v7:
        config, cells, scan_contract, figure_slices, priority_policy = _v7_validate_config(root)
    _validate_harvest_model(
        {key: config.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
        "run_config harvest model",
    )
    if config.get("use_real_solar_data") is not False:
        raise SystemExit("run_config must disable real solar data")
    is_v5 = config.get("experiment") == experiment.V5_EXPERIMENT
    is_v4 = config.get("experiment") == experiment.V4_EXPERIMENT
    if is_v5 and config.get("deadline_modes") != list(experiment.DEADLINE_MODES):
        raise SystemExit("v5 run_config must bind constrained and implicit deadline modes")
    if not is_v7:
        scan_contract = None
        try:
            priority_policy = experiment.normalize_scheduler_priority_policy(
                config.get("priority_policy", "RM")
            )
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        cells = _configured_cells(config)
        if is_v5 or is_v4:
            scan_contract, figure_slices = _v4_contract(config)
            cells = tuple(
                (Fraction(uc), Fraction(ue))
                for uc, ue in scan_contract["ordered_cells"]
            )
        else:
            figure_slices = _figure_slices(config, cells)
    schedulers = list(config.get("schedulers", ()))
    try:
        parsed_schedulers = experiment.parse_schedulers(",".join(schedulers))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if tuple(parsed_schedulers) != tuple(schedulers):
        raise SystemExit("run_config schedulers are not canonical and unique")
    if (is_v5 or is_v4 or is_v7) and set(schedulers) != set(experiment.ALL_SCHEDULERS):
        raise SystemExit("v4/v5 campaign requires all nine canonical schedulers")
    if tuple(cells) == tuple(experiment.FORMAL_CELLS) and priority_policy == "RM":
        try:
            experiment.validate_frozen_main_figure(
                cells, schedulers, horizon_ms=int(config.get("simulation_horizon_ms", 0)),
                priority_policy=priority_policy,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    tasksets = read_jsonl(root / "tasksets.jsonl")
    requests = read_jsonl(root / "requests.jsonl")
    results = read_jsonl(root / "results.jsonl")
    if is_v5 or is_v7:
        for collection_name, collection in (
            ("tasksets", tasksets), ("requests", requests), ("results", results),
        ):
            observed_modes = {row.get("deadline_mode") for row in collection}
            if any(mode not in experiment.DEADLINE_MODES for mode in observed_modes):
                raise SystemExit(f"{collection_name} contains an invalid deadline_mode")
            expected_modes = {"constrained"} if is_v7 else set(experiment.DEADLINE_MODES)
            if observed_modes != expected_modes:
                raise SystemExit(
                    f"{'v7' if is_v7 else 'v5'} {collection_name} has an invalid deadline-mode contract"
                )
    if not is_v5 and not is_v4 and not is_v7 and any(
        row.get("experiment") in {experiment.V4_EXPERIMENT, experiment.V5_EXPERIMENT}
        for row in (*requests, *results)
    ):
        raise SystemExit("v3/v4/v5 result mixing is not allowed")
    if not requests:
        raise SystemExit("incomplete campaign: requests are empty")
    expected = {str(row["request_id"]) for row in requests}
    observed = [str(row.get("request_id")) for row in results]
    duplicate = len(observed) - len(set(observed))
    missing = len(expected - set(observed))
    unexpected = len(set(observed) - expected)
    if duplicate or missing or unexpected:
        raise SystemExit(f"incomplete campaign: duplicate={duplicate} missing={missing} unexpected={unexpected}")
    request_by_id = {str(row["request_id"]): row for row in requests}
    if len(request_by_id) != len(requests):
        raise SystemExit("requests contain duplicate request IDs")
    if any(row.get("priority_policy", "RM") != priority_policy for row in requests):
        raise SystemExit("request priority policy does not match run_config")
    for row in results:
        request = request_by_id.get(str(row.get("request_id")))
        if request is None:
            raise SystemExit("result request identity is unexpected")
        if row.get("priority_policy", "RM") != priority_policy:
            raise SystemExit("result priority policy does not match run_config")
        if request.get("priority_policy", "RM") != priority_policy:
            raise SystemExit("request priority policy does not match run_config")
        for key in (
            "taskset_id", "taskset_hash", "target_uc", "target_ue",
            "generation_index", "scheduler", "scheduler_cli",
        ):
            if row.get(key) != request.get(key):
                raise SystemExit(f"result/request identity mismatch for {key}")
        if is_v5 or is_v4 or is_v7:
            expected_experiment = experiment.V5_EXPERIMENT if is_v5 else experiment.V4_EXPERIMENT
            if is_v7:
                expected_experiment = experiment.V7_EXPERIMENT
            for key, expected_value in (("experiment", expected_experiment),):
                if request.get(key) != expected_value or row.get(key) != expected_value:
                    raise SystemExit(f"{expected_experiment} result/request identity mismatch for {key}")
        if is_v7:
            for key in ("domain", "campaign", "energy_control", "deadline_mode"):
                if request.get(key) != config.get(key) and key != "deadline_mode":
                    raise SystemExit(f"v7 request {key} does not match run_config")
                if key == "domain" and request.get(key) != experiment.V7_DOMAIN:
                    raise SystemExit("v7 request domain mismatch")
                if key == "deadline_mode" and request.get(key) != "constrained":
                    raise SystemExit("v7 request deadline mode mismatch")
            if row.get("campaign") != config.get("campaign") or row.get("energy_control") != config.get("energy_control"):
                raise SystemExit("v7 result energy-control identity mismatch")
            if config["energy_control"] == "FIXED_ABSOLUTE_SUPPLY":
                if (
                    request.get("reference_ue") != request.get("target_ue")
                    or request.get("target_ue_role") != "calibration_reference"
                    or request.get("energy_level") != experiment.v7_energy_level(Fraction(request["target_ue"]))
                ):
                    raise SystemExit("v7 fixed-supply request reference identity is invalid")
        if (is_v5 or is_v7) and row.get("deadline_mode") != request.get("deadline_mode"):
            raise SystemExit("deadline-mode result/request mismatch")
        _validate_harvest_model(
            {key: request.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
            "request harvest model",
        )
        _validate_harvest_model(
            {key: row.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
            "result harvest model",
        )
    expected_tasksets = len({row["taskset_id"] for row in requests})
    taskset_by_id = {str(row["taskset_id"]): row for row in tasksets}
    if len(taskset_by_id) != expected_tasksets:
        raise SystemExit("taskset identity count mismatch")
    if is_v5 or is_v7:
        for request in requests:
            taskset = taskset_by_id.get(str(request["taskset_id"]))
            if taskset is None or request.get("deadline_mode") != taskset.get("deadline_mode"):
                raise SystemExit("request/taskset deadline_mode mismatch")
    if any(row.get("canonical_task_power") is not True for row in tasksets):
        raise SystemExit("non-canonical task power in taskset store")
    if is_v5 or is_v7:
        for taskset in tasksets:
            mode = experiment.normalize_deadline_mode(taskset["deadline_mode"])
            try:
                payload = json.loads(taskset["task_input_json"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit("taskset task payload is missing or invalid") from exc
            if not isinstance(payload, list):
                raise SystemExit("taskset task payload is invalid")
            for item in payload:
                try:
                    c, d, t = int(item["C"]), int(item["D"]), int(item["T"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise SystemExit("taskset task deadline fields are invalid") from exc
                if not (0 < c <= d <= t):
                    raise SystemExit("taskset violates C <= D <= T")
                if mode == "implicit" and d != t:
                    raise SystemExit("implicit taskset violates D == T")
    uc_tolerance = Fraction(str(config["util_tolerance_total"])) / Fraction(str(config["processors"]))
    if any(
        abs(Fraction(row["actual_uc"]) - Fraction(row["target_uc"])) > uc_tolerance
        for row in tasksets
    ):
        raise SystemExit("actual U_C exceeds configured tolerance")
    technical_rows = []
    for row in results:
        if (
            row.get("technical_error") is not None
            or row.get("simulation_status") not in {
                "SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS",
            }
        ):
            technical_rows.append(row)
            continue
        outcome = row.get("outcome", {})
        if outcome.get("outcome_status") not in (None, "AVAILABLE"):
            technical_rows.append(row)
            continue
        if "wholepass" in outcome and row.get("wholepass") != outcome.get("wholepass"):
            raise SystemExit("row WholePass does not match evaluate_outcome(...).wholepass")
        if "wholepass" in outcome and row.get("taskset_pass") != outcome.get("wholepass"):
            raise SystemExit("schedulable/taskset_pass is not evaluate_outcome(...).taskset_pass")
        taskset = taskset_by_id[str(row["taskset_id"])]
        if row["taskset_hash"] != taskset["taskset_hash"]:
            raise SystemExit("scheduler changed taskset identity")
        if (is_v5 or is_v7) and row.get("deadline_mode") != taskset.get("deadline_mode"):
            raise SystemExit("result/taskset deadline_mode mismatch")
        energy = row["energy"]
        if is_v7:
            _v7_validate_energy(row, config, taskset)
            continue
        _validate_harvest_model(
            {key: energy.get(key) for key in experiment.HARVEST_MODEL_IDENTITY},
            "energy harvest model",
        )
        ue = Fraction(row["target_ue"])
        if Fraction(energy["eta"]) != experiment.eta_for_ue(ue):
            raise SystemExit("eta != 1/U_E")
        demand = Fraction(energy["P_dem_j_per_tick"])
        supply = Fraction(energy["target_supply_mean_j_per_tick"])
        raw = Fraction(energy["raw_reference_mean_j_per_tick"])
        if demand / supply != ue or Fraction(energy["solar_scale"]) * raw != supply:
            raise SystemExit("U_E service identity mismatch")
        if Fraction(energy["eta"]) * ue != 1:
            raise SystemExit("eta * U_E != 1")
        try:
            runtime_supply = Fraction(
                energy["runtime_configured_average_supply_j_per_tick"]
            )
            actual_ue = Fraction(energy["actual_ue"])
            actual_ue_abs_error = Fraction(energy["actual_ue_abs_error"])
            actual_ue_rel_error = Fraction(energy["actual_ue_rel_error"])
            actual_ue_minus_target_ue = Fraction(
                energy["actual_ue_minus_target_ue"]
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise SystemExit(
                "runtime average supply and actual U_E are invalid"
            ) from exc
        if runtime_supply <= 0 or actual_ue <= 0:
            raise SystemExit("runtime average supply and actual U_E must be positive")
        calculated_actual_ue = demand / runtime_supply
        calculated_abs_error = abs(calculated_actual_ue - ue)
        calculated_rel_error = calculated_abs_error / ue
        if (
            actual_ue != calculated_actual_ue
            or actual_ue_abs_error != calculated_abs_error
            or actual_ue_rel_error != calculated_rel_error
            or actual_ue_minus_target_ue != calculated_actual_ue - ue
        ):
            raise SystemExit(
                "recorded actual U_E does not match runtime average supply"
            )
        if calculated_rel_error > ACTUAL_UE_RELATIVE_TOLERANCE:
            raise SystemExit(
                "actual U_E relative error exceeds 1e-12: "
                f"{float(calculated_rel_error):.17g}"
            )
    if technical_rows:
        raise SystemExit(f"technical failures are not scientific WholePass rows: {len(technical_rows)}")
    if is_v5 or is_v7:
        trace_ids = {
            str(row["energy"].get("harvest_trace_id")) for row in results
        }
        if len(trace_ids) != 1:
            raise SystemExit("campaign results do not share one harvest trace identity")

    # Requests are the authority for the pairing grid.  Every cell/taskset
    # must have exactly one result for every requested scheduler.
    request_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for request in requests:
        group_key = (
            (str(request["deadline_mode"]),) if is_v5 else ()
        ) + (
            str(request["target_uc"]), str(request["target_ue"]),
            str(request["generation_index"]),
        )
        request_groups.setdefault(
            group_key,
            [],
        ).append(request)
    for key, group in request_groups.items():
        if len(group) != len(schedulers):
            raise SystemExit(f"cell/taskset does not contain exactly {len(schedulers)} scheduler requests: {key}")
        if {row["scheduler"] for row in group} != set(schedulers):
            raise SystemExit(f"cell/taskset scheduler coverage is incomplete: {key}")

    # Same taskset + U_E must share immutable energy material across all nine
    # schedulers; across U_E, only service scaling may vary.
    by_taskset_ue: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    by_taskset: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in results:
        mode_key = (str(row["deadline_mode"]),) if is_v5 else ()
        by_taskset_ue.setdefault(
            mode_key + (str(row["taskset_id"]), str(row["target_ue"])),
            [],
        ).append(row)
        by_taskset.setdefault(mode_key + (str(row["taskset_id"]),), []).append(row)
    invariant_fields = (
        "P_dem_j_per_tick", "E_burst_j", "battery_capacity_j",
        "initial_energy_j", "raw_reference_mean_j_per_tick", "harvest_trace_id",
    )
    for key, group in by_taskset_ue.items():
        if len(group) != len(schedulers) or len({str(row["energy"].get("harvest_trace_id")) for row in group}) != 1:
            raise SystemExit(f"energy material is not paired across schedulers: {key}")
    for taskset_id, group in by_taskset.items():
        invariants = {
            field: {str(row["energy"].get(field)) for row in group}
            for field in invariant_fields
        }
        if any(len(values) != 1 for values in invariants.values()):
            raise SystemExit(f"battery/E0/task demand/trace invariant changed across U_E: {taskset_id}")
    paired_tasksets: dict[tuple[str, ...], set[str]] = {}
    for request in requests:
        mode_key = (str(request["deadline_mode"]),) if (is_v5 or is_v7) else ()
        paired_tasksets.setdefault(
            mode_key + (str(request["target_uc"]), str(request["generation_index"])),
            set(),
        ).add(str(request["taskset_id"]))
    if any(len(values) != 1 for values in paired_tasksets.values()):
        raise SystemExit("paired CPU taskset identity changed across U_E")
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in results:
        mode_key = (str(row["deadline_mode"]),) if (is_v5 or is_v7) else ()
        groups.setdefault(
            mode_key + (str(row["target_uc"]), str(row["target_ue"])), []
        ).append(row)
    summaries = []
    dmr_summaries = []
    campaign_seed = int(config.get("seed", 0))
    def group_sort(item: tuple[tuple[str, ...], list[dict[str, Any]]]) -> tuple[Any, ...]:
        key = item[0]
        if is_v5 or is_v7:
            mode, uc, ue = key
            return (experiment.DEADLINE_MODES.index(mode), Fraction(uc), Fraction(ue))
        uc, ue = key
        return (Fraction(uc), Fraction(ue))

    for group_key, group in sorted(groups.items(), key=group_sort):
        if is_v5 or is_v7:
            deadline_mode, uc, ue = group_key
        else:
            deadline_mode = None
            uc, ue = group_key
        for scheduler in schedulers:
            selected = [row for row in group if row["scheduler"] == scheduler]
            if len(selected) != int(config["samples_per_cell"]):
                raise SystemExit("missing scheduler result in cell")
            n_total = len(selected)
            wholepass_values = [
                row.get("wholepass", row.get("taskset_pass")) is True
                for row in selected
            ]
            n_wholepass = sum(wholepass_values)
            n_miss = sum(row.get("deadline_miss") is True for row in selected)
            ci_low, ci_high = wilson_ci(n_wholepass, n_total)
            summary_row = {
                "priority_policy": priority_policy,
                "target_uc": uc, "target_ue": ue, "scheduler": scheduler,
                "runtime_configured_average_supply_j_per_tick": selected[0][
                    "energy"
                ]["runtime_configured_average_supply_j_per_tick"],
                "actual_ue": selected[0]["energy"]["actual_ue"],
                "actual_ue_minus_target_ue": selected[0]["energy"][
                    "actual_ue_minus_target_ue"
                ],
                "n_total": n_total, "n_valid_tasksets": n_total,
                "n_technical": 0, "n_wholepass": n_wholepass,
                "wholepass_ratio": n_wholepass / n_total,
                "ci95_low": ci_low, "ci95_high": ci_high,
                # Compatibility diagnostics; these are not used as the
                # scientific y-axis.
                "n_schedulable": n_wholepass,
                "n_deadline_miss": n_miss,
                "acceptance_ratio": n_wholepass / n_total,
            }
            if deadline_mode is not None:
                summary_row["deadline_mode"] = deadline_mode
            summaries.append(summary_row)
            dmr_summaries.append(summarize_dmr(
                selected,
                target_uc=uc,
                target_ue=ue,
                scheduler=scheduler,
                campaign_seed=campaign_seed,
                priority_policy=priority_policy,
                deadline_mode=deadline_mode,
            ))
    write_csv(root / "summary.csv", summaries)
    write_csv(root / "summary_dmr.csv", dmr_summaries)
    plot_jobs: list[dict[str, Any]] = []
    if is_v5:
        axis = _axis_plot_values(scan_contract)
        uc_slice_rows = []
        uc_dmr_slice_rows = []
        for index, uc_slice in enumerate(figure_slices["uc_scans"]):
            for deadline_mode in experiment.DEADLINE_MODES:
                uc_rows = [
                    row for row in summaries
                    if row.get("deadline_mode") == deadline_mode
                    and row[uc_slice["fixed_key"]] == uc_slice["fixed_value"]
                ]
                dmr_rows = [
                    row for row in dmr_summaries
                    if row.get("deadline_mode") == deadline_mode
                    and row[uc_slice["fixed_key"]] == uc_slice["fixed_value"]
                ]
                _validate_v5_slice(
                    uc_rows, uc_slice, deadline_mode, schedulers, f"U_C[{index}]",
                )
                _validate_v5_slice(
                    dmr_rows, uc_slice, deadline_mode, schedulers, f"U_C DMR[{index}]",
                )
                uc_slice_rows.append((uc_slice, deadline_mode, uc_rows))
                uc_dmr_slice_rows.append((uc_slice, deadline_mode, dmr_rows))
        ue_slice_rows = []
        ue_dmr_slice_rows = []
        for index, ue_slice in enumerate(figure_slices["ue_scans"]):
            for deadline_mode in experiment.DEADLINE_MODES:
                ue_rows = [
                    row for row in summaries
                    if row.get("deadline_mode") == deadline_mode
                    and row[ue_slice["fixed_key"]] == ue_slice["fixed_value"]
                ]
                dmr_rows = [
                    row for row in dmr_summaries
                    if row.get("deadline_mode") == deadline_mode
                    and row[ue_slice["fixed_key"]] == ue_slice["fixed_value"]
                ]
                _validate_v5_slice(
                    ue_rows, ue_slice, deadline_mode, schedulers, f"U_E[{index}]",
                )
                _validate_v5_slice(
                    dmr_rows, ue_slice, deadline_mode, schedulers, f"U_E DMR[{index}]",
                )
                ue_slice_rows.append((ue_slice, deadline_mode, ue_rows))
                ue_dmr_slice_rows.append((ue_slice, deadline_mode, dmr_rows))
        write_csv(root / "figure_scheduler_uc_slices.csv", [
            row for slice_config, _mode, rows in uc_slice_rows
            for row in _slice_csv_rows(slice_config, rows)
        ])
        write_csv(root / "figure_scheduler_ue_slices.csv", [
            row for slice_config, _mode, rows in ue_slice_rows
            for row in _slice_csv_rows(slice_config, rows)
        ])
        write_csv(root / "figure_scheduler_uc_slices_dmr.csv", [
            row for slice_config, _mode, rows in uc_dmr_slice_rows
            for row in _dmr_slice_csv_rows(slice_config, rows)
        ])
        write_csv(root / "figure_scheduler_ue_slices_dmr.csv", [
            row for slice_config, _mode, rows in ue_dmr_slice_rows
            for row in _dmr_slice_csv_rows(slice_config, rows)
        ])
        plot_jobs = [
            {"v5_composite": True, "slice_rows": uc_slice_rows, "output": str(root),
             "filename": "figure_scheduler_uc_slices.png", "xkey": "target_uc",
             "schedulers": schedulers, "xlabel": "U_C",
             "title": f"{priority_policy} — Whole-taskset pass ratio versus U_C",
             **axis},
            {"v5_composite": True, "slice_rows": ue_slice_rows, "output": str(root),
             "filename": "figure_scheduler_ue_slices.png", "xkey": "target_ue",
             "schedulers": schedulers, "xlabel": "U_E",
             "title": f"{priority_policy} — Whole-taskset pass ratio versus U_E",
             **axis},
            {"v5_composite": True, "slice_rows": uc_dmr_slice_rows, "output": str(root),
             "filename": "figure_scheduler_uc_slices_dmr.png", "xkey": "target_uc",
             "schedulers": schedulers, "xlabel": "U_C", "metric": "dmr",
             "ymin": uc_dmr_ymin,
             "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus U_C",
             **axis},
            {"v5_composite": True, "slice_rows": ue_dmr_slice_rows, "output": str(root),
             "filename": "figure_scheduler_ue_slices_dmr.png", "xkey": "target_ue",
             "schedulers": schedulers, "xlabel": "U_E", "metric": "dmr",
             "ymin": ue_dmr_ymin,
             "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus U_E",
             **axis},
        ]
    elif is_v4:
        axis = _axis_plot_values(scan_contract)
        uc_slice_rows = []
        uc_dmr_slice_rows = []
        for index, uc_slice in enumerate(figure_slices["uc_scans"]):
            uc_rows = select_scan_rows(summaries, uc_slice["fixed_key"], uc_slice["fixed_value"])
            dmr_uc_rows = select_scan_rows(dmr_summaries, uc_slice["fixed_key"], uc_slice["fixed_value"])
            _validate_v4_slice(uc_rows, uc_slice, schedulers, f"U_C[{index}]")
            _validate_v4_slice(dmr_uc_rows, uc_slice, schedulers, f"U_C DMR[{index}]")
            uc_slice_rows.append((uc_slice, uc_rows))
            uc_dmr_slice_rows.append((uc_slice, dmr_uc_rows))
            _validate_dmr_ci_lower_bounds(dmr_uc_rows, uc_dmr_ymin, f"U_C[{index}]")
        ue_slice_rows = []
        ue_dmr_slice_rows = []
        for index, ue_slice in enumerate(figure_slices["ue_scans"]):
            ue_rows = select_scan_rows(summaries, ue_slice["fixed_key"], ue_slice["fixed_value"])
            dmr_ue_rows = select_scan_rows(dmr_summaries, ue_slice["fixed_key"], ue_slice["fixed_value"])
            _validate_v4_slice(ue_rows, ue_slice, schedulers, f"U_E[{index}]")
            _validate_v4_slice(dmr_ue_rows, ue_slice, schedulers, f"U_E DMR[{index}]")
            ue_slice_rows.append((ue_slice, ue_rows))
            ue_dmr_slice_rows.append((ue_slice, dmr_ue_rows))
            _validate_dmr_ci_lower_bounds(dmr_ue_rows, ue_dmr_ymin, f"U_E[{index}]")
        write_csv(root / "figure_scheduler_uc_slices.csv", [
            row for slice_config, rows in uc_slice_rows for row in _slice_csv_rows(slice_config, rows)
        ])
        write_csv(root / "figure_scheduler_ue_slices.csv", [
            row for slice_config, rows in ue_slice_rows for row in _slice_csv_rows(slice_config, rows)
        ])
        write_csv(root / "figure_scheduler_uc_slices_dmr.csv", [
            row for slice_config, rows in uc_dmr_slice_rows for row in _dmr_slice_csv_rows(slice_config, rows)
        ])
        write_csv(root / "figure_scheduler_ue_slices_dmr.csv", [
            row for slice_config, rows in ue_dmr_slice_rows for row in _dmr_slice_csv_rows(slice_config, rows)
        ])
        plot_jobs = [
            {"composite": True, "slice_rows": uc_slice_rows, "output": str(root),
             "filename": "figure_scheduler_uc_slices.png", "xkey": "target_uc",
             "schedulers": schedulers, "xlabel": "U_C",
             "title": f"{priority_policy} — Whole-taskset pass ratio versus U_C",
             **axis},
            {"composite": True, "slice_rows": ue_slice_rows, "output": str(root),
             "filename": "figure_scheduler_ue_slices.png", "xkey": "target_ue",
             "schedulers": schedulers, "xlabel": "U_E",
             "title": f"{priority_policy} — Whole-taskset pass ratio versus U_E",
             **axis},
            {"composite": True, "slice_rows": uc_dmr_slice_rows, "output": str(root),
             "filename": "figure_scheduler_uc_slices_dmr.png", "xkey": "target_uc",
             "schedulers": schedulers, "xlabel": "U_C", "metric": "dmr",
             "ymin": uc_dmr_ymin,
             "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus U_C",
             **axis},
            {"composite": True, "slice_rows": ue_dmr_slice_rows, "output": str(root),
             "filename": "figure_scheduler_ue_slices_dmr.png", "xkey": "target_ue",
             "schedulers": schedulers, "xlabel": "U_E", "metric": "dmr",
             "ymin": ue_dmr_ymin,
             "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus U_E",
             **axis},
        ]
    elif is_v7:
        axis = _axis_plot_values(scan_contract)
        scan_key = "uc_scans" if config["campaign"] == experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN else "ue_scans"
        x_key = "target_uc" if scan_key == "uc_scans" else "target_ue"
        xlabel = "U_C" if scan_key == "uc_scans" else "U_E"
        slice_rows = []
        dmr_slice_rows = []
        for index, slice_config in enumerate(figure_slices[scan_key]):
            values = select_scan_rows(
                summaries, slice_config["fixed_key"], slice_config["fixed_value"],
            )
            dmr_values = select_scan_rows(
                dmr_summaries, slice_config["fixed_key"], slice_config["fixed_value"],
            )
            _validate_v4_slice(values, slice_config, schedulers, f"{xlabel}[{index}]")
            _validate_v4_slice(dmr_values, slice_config, schedulers, f"{xlabel} DMR[{index}]")
            _validate_dmr_ci_lower_bounds(dmr_values, uc_dmr_ymin if scan_key == "uc_scans" else ue_dmr_ymin, f"{xlabel}[{index}]")
            slice_rows.append((slice_config, values))
            dmr_slice_rows.append((slice_config, dmr_values))
        write_csv(root / f"figure_scheduler_{'uc' if scan_key == 'uc_scans' else 'ue'}_slices.csv", [
            row for slice_config, values in slice_rows
            for row in _slice_csv_rows(slice_config, values)
        ])
        write_csv(root / f"figure_scheduler_{'uc' if scan_key == 'uc_scans' else 'ue'}_slices_dmr.csv", [
            row for slice_config, values in dmr_slice_rows
            for row in _dmr_slice_csv_rows(slice_config, values)
        ])
        plot_jobs = [
            {"composite": True, "slice_rows": slice_rows, "output": str(root),
             "filename": f"figure_scheduler_{'uc' if scan_key == 'uc_scans' else 'ue'}_slices.png",
             "xkey": x_key, "schedulers": schedulers, "xlabel": xlabel,
             "title": f"{priority_policy} — Whole-taskset pass ratio versus {xlabel}", **axis},
            {"composite": True, "slice_rows": dmr_slice_rows, "output": str(root),
             "filename": f"figure_scheduler_{'uc' if scan_key == 'uc_scans' else 'ue'}_slices_dmr.png",
             "xkey": x_key, "schedulers": schedulers, "xlabel": xlabel, "metric": "dmr",
             "ymin": uc_dmr_ymin if scan_key == "uc_scans" else ue_dmr_ymin,
             "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus {xlabel}", **axis},
        ]
    else:
        uc_slice = figure_slices["uc_scan"]
        ue_slice = figure_slices["ue_scan"]
        uc_rows = select_scan_rows(summaries, uc_slice["fixed_key"], uc_slice["fixed_value"])
        ue_rows = select_scan_rows(summaries, ue_slice["fixed_key"], ue_slice["fixed_value"])
        _validate_scan_rows(uc_rows, cells, fixed_key=uc_slice["fixed_key"], x_key=uc_slice["x_key"], fixed_value=uc_slice["fixed_value"], label="U_C")
        _validate_scan_rows(ue_rows, cells, fixed_key=ue_slice["fixed_key"], x_key=ue_slice["x_key"], fixed_value=ue_slice["fixed_value"], label="U_E")
        dmr_uc_rows = select_scan_rows(dmr_summaries, uc_slice["fixed_key"], uc_slice["fixed_value"])
        dmr_ue_rows = select_scan_rows(dmr_summaries, ue_slice["fixed_key"], ue_slice["fixed_value"])
        _validate_dmr_scan_rows(dmr_uc_rows, cells, fixed_key=uc_slice["fixed_key"], x_key=uc_slice["x_key"], fixed_value=uc_slice["fixed_value"], label="U_C DMR")
        _validate_dmr_scan_rows(dmr_ue_rows, cells, fixed_key=ue_slice["fixed_key"], x_key=ue_slice["x_key"], fixed_value=ue_slice["fixed_value"], label="U_E DMR")
        _validate_dmr_ci_lower_bounds(dmr_uc_rows, uc_dmr_ymin, "U_C")
        _validate_dmr_ci_lower_bounds(dmr_ue_rows, ue_dmr_ymin, "U_E")
        write_csv(root / "figure_scheduler_uc.csv", uc_rows)
        write_csv(root / "figure_scheduler_ue.csv", ue_rows)
        write_csv(root / "figure_scheduler_uc_dmr.csv", dmr_uc_rows)
        write_csv(root / "figure_scheduler_ue_dmr.csv", dmr_ue_rows)
        plot_jobs = [
            {"rows": uc_rows, "output": str(root), "filename": "figure_scheduler_uc.png", "xkey": uc_slice["x_key"], "schedulers": schedulers, "xlabel": "U_C", "title": f"{priority_policy} — Whole-taskset pass ratio versus U_C (U_E={uc_slice['fixed_value']})"},
            {"rows": ue_rows, "output": str(root), "filename": "figure_scheduler_ue.png", "xkey": ue_slice["x_key"], "schedulers": schedulers, "xlabel": "U_E", "title": f"{priority_policy} — Whole-taskset pass ratio versus U_E (U_C={ue_slice['fixed_value']})"},
            {"rows": dmr_uc_rows, "output": str(root), "filename": "figure_scheduler_uc_dmr.png", "xkey": uc_slice["x_key"], "schedulers": schedulers, "xlabel": "U_C", "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus U_C (U_E={uc_slice['fixed_value']})" + (f" (zoomed y-axis: {uc_dmr_ymin:g}–1.0)" if uc_dmr_ymin > 0 else ""), "metric": "dmr", "ymin": uc_dmr_ymin},
            {"rows": dmr_ue_rows, "output": str(root), "filename": "figure_scheduler_ue_dmr.png", "xkey": ue_slice["x_key"], "schedulers": schedulers, "xlabel": "U_E", "title": f"{priority_policy} — Job-level deadline-meeting ratio (DMR) versus U_E (U_C={ue_slice['fixed_value']})" + (f" (zoomed y-axis: {ue_dmr_ymin:g}–1.0)" if ue_dmr_ymin > 0 else ""), "metric": "dmr", "ymin": ue_dmr_ymin},
        ]
    validation_summary_seconds = time.perf_counter() - validation_started
    plot_started = time.perf_counter()
    try:
        import matplotlib
        matplotlib.use("Agg")
        run_independent_jobs(plot_jobs, _plot_any_scan_job, workers=analysis_workers)
    except ImportError:
        pass
    plot_seconds = time.perf_counter() - plot_started
    ue_errors = [
        Fraction(row["energy"]["actual_ue_abs_error"])
        / Fraction(row["energy"]["target_ue"])
        for row in results
    ]
    report = {"complete": True, "experiment": config.get("experiment"),
              "deadline_modes": list(config.get("deadline_modes", ())) if (is_v5 or is_v7) else None,
              "tasksets": len(tasksets), "requests": len(requests),
              "results": len(results), "duplicate_request_ids": duplicate,
              "missing_request_ids": missing, "unexpected_request_ids": unexpected,
              "summary_rows": len(summaries),
              "dmr_summary_rows": len(dmr_summaries),
              "technical_result_count": len(technical_rows),
              "missing": missing, "duplicate": duplicate,
              "unexpected": unexpected, "technical": len(technical_rows),
              "harvest_model": experiment.HARVEST_MODEL,
              "actual_ue_validated": (
                  bool(ue_errors) if is_v7 and config["energy_control"] == "FIXED_ABSOLUTE_SUPPLY"
                  else bool(ue_errors) and max(ue_errors) <= ACTUAL_UE_RELATIVE_TOLERANCE
              ),
              "actual_ue_max_relative_error": str(max(ue_errors, default=Fraction(0))),
              "telemetry": {
                  "validation_summary_seconds": validation_summary_seconds,
                  "plot_seconds": plot_seconds,
                  "analysis_total_seconds": time.perf_counter() - analysis_started,
                  "analysis_workers": analysis_workers,
              }}
    (root / "analysis_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--shared-implicit-run-dir", type=Path, default=None,
        help="v6 DM analysis source directory containing the canonical RM implicit run",
    )
    parser.add_argument("--analysis-workers", type=int, default=1)
    parser.add_argument(
        "--uc-dmr-ymin", type=_parse_dmr_ymin, default=0.0,
        help="lower y-axis bound for the U_C-scan DMR figure (default: 0.0)",
    )
    parser.add_argument(
        "--ue-dmr-ymin", type=_parse_dmr_ymin, default=0.0,
        help="lower y-axis bound for the U_E-scan DMR figure (default: 0.0)",
    )
    args = parser.parse_args(argv)
    try:
        result = analyze(
            args.input,
            analysis_workers=args.analysis_workers,
            uc_dmr_ymin=args.uc_dmr_ymin,
            ue_dmr_ymin=args.ue_dmr_ymin,
            shared_implicit_run_dir=args.shared_implicit_run_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
