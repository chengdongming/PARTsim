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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3.parallel_prepare import run_independent_jobs, validate_workers


DMR_BOOTSTRAP_REPLICATES = 10000

_PLOT_COLORS = {
    "ASAP": "tab:blue",
    "ALAP": "tab:orange",
    "ST": "tab:green",
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0]) if rows else ["target_uc", "target_ue", "scheduler", "acceptance_ratio"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)


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
) -> int:
    material = f"DMR_CLUSTER_BOOTSTRAP\0{campaign_seed}\0{target_uc}\0{target_ue}\0{scheduler}"
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
        seed=_dmr_bootstrap_seed(campaign_seed, target_uc, target_ue, scheduler),
        replicates=bootstrap_replicates,
    )
    return {
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
    schedulers: list[str], xlabel: str, title: str,
) -> None:
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
        axis.grid(alpha=0.25)
        axis.legend(fontsize="small")
    axes[0].set_ylabel("Whole-taskset pass ratio")
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(output / filename)
    plt.close(figure)


def _plot_scan_job(job: dict[str, Any]) -> None:
    plot_scan(
        job["rows"], Path(job["output"]), job["filename"], job["xkey"],
        job["schedulers"], job["xlabel"], job["title"],
    )


def plot_dmr_scan(
    rows: list[dict[str, Any]], output: Path, filename: str, xkey: str,
    schedulers: list[str], xlabel: str, title: str, ymin: float = 0.0,
) -> None:
    ymin = _validate_dmr_ymin(ymin)
    _validate_dmr_ci_lower_bounds(rows, ymin, filename)
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
        axis.grid(alpha=0.25)
        axis.legend(fontsize="small")
    axes[0].set_ylabel("Deadline-meeting ratio (DMR)")
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(output / filename)
    plt.close(figure)


def _plot_dmr_scan_job(job: dict[str, Any]) -> None:
    plot_dmr_scan(
        job["rows"], Path(job["output"]), job["filename"], job["xkey"],
        job["schedulers"], job["xlabel"], job["title"], job.get("ymin", 0.0),
    )


def _plot_any_scan_job(job: dict[str, Any]) -> None:
    if job.get("metric") == "dmr":
        _plot_dmr_scan_job(job)
    else:
        _plot_scan_job(job)


def analyze(
    root: Path, *, analysis_workers: int = 1,
    uc_dmr_ymin: float = 0.0, ue_dmr_ymin: float = 0.0,
) -> dict[str, Any]:
    validate_workers(analysis_workers, "analysis-workers")
    uc_dmr_ymin = _validate_dmr_ymin(uc_dmr_ymin, "U_C DMR y-axis lower bound")
    ue_dmr_ymin = _validate_dmr_ymin(ue_dmr_ymin, "U_E DMR y-axis lower bound")
    analysis_started = time.perf_counter()
    validation_started = analysis_started
    config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    try:
        priority_policy = experiment.normalize_scheduler_priority_policy(
            config.get("priority_policy", "RM")
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    cells = _configured_cells(config)
    figure_slices = _figure_slices(config, cells)
    schedulers = list(config.get("schedulers", ()))
    try:
        parsed_schedulers = experiment.parse_schedulers(",".join(schedulers))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if tuple(parsed_schedulers) != tuple(schedulers):
        raise SystemExit("run_config schedulers are not canonical and unique")
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
    expected_tasksets = len({row["taskset_id"] for row in requests})
    taskset_by_id = {str(row["taskset_id"]): row for row in tasksets}
    if len(taskset_by_id) != expected_tasksets:
        raise SystemExit("taskset identity count mismatch")
    if any(row.get("canonical_task_power") is not True for row in tasksets):
        raise SystemExit("non-canonical task power in taskset store")
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
        energy = row["energy"]
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
    if technical_rows:
        raise SystemExit(f"technical failures are not scientific WholePass rows: {len(technical_rows)}")

    # Requests are the authority for the pairing grid.  Every cell/taskset
    # must have exactly one result for every requested scheduler.
    request_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for request in requests:
        request_groups.setdefault(
            (str(request["target_uc"]), str(request["target_ue"]), str(request["generation_index"])),
            [],
        ).append(request)
    for key, group in request_groups.items():
        if len(group) != len(schedulers):
            raise SystemExit(f"cell/taskset does not contain exactly {len(schedulers)} scheduler requests: {key}")
        if {row["scheduler"] for row in group} != set(schedulers):
            raise SystemExit(f"cell/taskset scheduler coverage is incomplete: {key}")

    # Same taskset + U_E must share immutable energy material across all nine
    # schedulers; across U_E, only service scaling may vary.
    by_taskset_ue: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_taskset: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_taskset_ue.setdefault((str(row["taskset_id"]), str(row["target_ue"])), []).append(row)
        by_taskset.setdefault(str(row["taskset_id"]), []).append(row)
    for key, group in by_taskset_ue.items():
        if len(group) != len(schedulers) or len({str(row["energy"].get("harvest_trace_id")) for row in group}) != 1:
            raise SystemExit(f"energy material is not paired across schedulers: {key}")
    invariant_fields = (
        "P_dem_j_per_tick", "E_burst_j", "battery_capacity_j",
        "initial_energy_j", "raw_reference_mean_j_per_tick", "harvest_trace_id",
    )
    for taskset_id, group in by_taskset.items():
        invariants = {
            field: {str(row["energy"].get(field)) for row in group}
            for field in invariant_fields
        }
        if any(len(values) != 1 for values in invariants.values()):
            raise SystemExit(f"battery/E0/task demand/trace invariant changed across U_E: {taskset_id}")
    paired_tasksets: dict[tuple[str, str], set[str]] = {}
    for request in requests:
        paired_tasksets.setdefault(
            (str(request["target_uc"]), str(request["generation_index"])),
            set(),
        ).add(str(request["taskset_id"]))
    if any(len(values) != 1 for values in paired_tasksets.values()):
        raise SystemExit("paired CPU taskset identity changed across U_E")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in results:
        groups.setdefault((str(row["target_uc"]), str(row["target_ue"])), []).append(row)
    summaries = []
    dmr_summaries = []
    campaign_seed = int(config.get("seed", 0))
    for (uc, ue), group in sorted(groups.items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]))):
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
            summaries.append({
                "priority_policy": priority_policy,
                "target_uc": uc, "target_ue": ue, "scheduler": scheduler,
                "n_total": n_total, "n_valid_tasksets": n_total,
                "n_technical": 0, "n_wholepass": n_wholepass,
                "wholepass_ratio": n_wholepass / n_total,
                "ci95_low": ci_low, "ci95_high": ci_high,
                # Compatibility diagnostics; these are not used as the
                # scientific y-axis.
                "n_schedulable": n_wholepass,
                "n_deadline_miss": n_miss,
                "acceptance_ratio": n_wholepass / n_total,
            })
            dmr_summaries.append(summarize_dmr(
                selected,
                target_uc=uc,
                target_ue=ue,
                scheduler=scheduler,
                campaign_seed=campaign_seed,
                priority_policy=priority_policy,
            ))
    uc_slice = figure_slices["uc_scan"]
    ue_slice = figure_slices["ue_scan"]
    uc_rows = select_scan_rows(
        summaries, uc_slice["fixed_key"], uc_slice["fixed_value"],
    )
    ue_rows = select_scan_rows(
        summaries, ue_slice["fixed_key"], ue_slice["fixed_value"],
    )
    _validate_scan_rows(
        uc_rows, cells, fixed_key=uc_slice["fixed_key"], x_key=uc_slice["x_key"],
        fixed_value=uc_slice["fixed_value"], label="U_C",
    )
    _validate_scan_rows(
        ue_rows, cells, fixed_key=ue_slice["fixed_key"], x_key=ue_slice["x_key"],
        fixed_value=ue_slice["fixed_value"], label="U_E",
    )
    dmr_uc_rows = select_scan_rows(
        dmr_summaries, uc_slice["fixed_key"], uc_slice["fixed_value"],
    )
    dmr_ue_rows = select_scan_rows(
        dmr_summaries, ue_slice["fixed_key"], ue_slice["fixed_value"],
    )
    _validate_dmr_scan_rows(
        dmr_uc_rows, cells, fixed_key=uc_slice["fixed_key"],
        x_key=uc_slice["x_key"], fixed_value=uc_slice["fixed_value"], label="U_C DMR",
    )
    _validate_dmr_scan_rows(
        dmr_ue_rows, cells, fixed_key=ue_slice["fixed_key"],
        x_key=ue_slice["x_key"], fixed_value=ue_slice["fixed_value"], label="U_E DMR",
    )
    _validate_dmr_ci_lower_bounds(dmr_uc_rows, uc_dmr_ymin, "U_C")
    _validate_dmr_ci_lower_bounds(dmr_ue_rows, ue_dmr_ymin, "U_E")
    write_csv(root / "summary.csv", summaries)
    write_csv(root / "figure_scheduler_uc.csv", uc_rows)
    write_csv(root / "figure_scheduler_ue.csv", ue_rows)
    write_csv(root / "summary_dmr.csv", dmr_summaries)
    write_csv(root / "figure_scheduler_uc_dmr.csv", dmr_uc_rows)
    write_csv(root / "figure_scheduler_ue_dmr.csv", dmr_ue_rows)
    validation_summary_seconds = time.perf_counter() - validation_started
    plot_started = time.perf_counter()
    try:
        import matplotlib
        matplotlib.use("Agg")
        run_independent_jobs([
            {
                "rows": uc_rows, "output": str(root),
                "filename": "figure_scheduler_uc.png", "xkey": uc_slice["x_key"],
                "schedulers": schedulers, "xlabel": "U_C",
                "title": (
                    f"{priority_policy} — Whole-taskset pass ratio versus U_C "
                    f"(U_E={uc_slice['fixed_value']})"
                ),
            },
            {
                "rows": ue_rows, "output": str(root),
                "filename": "figure_scheduler_ue.png", "xkey": ue_slice["x_key"],
                "schedulers": schedulers, "xlabel": "U_E",
                "title": (
                    f"{priority_policy} — Whole-taskset pass ratio versus U_E "
                    f"(U_C={ue_slice['fixed_value']})"
                ),
            },
            {
                "rows": dmr_uc_rows, "output": str(root),
                "filename": "figure_scheduler_uc_dmr.png", "xkey": uc_slice["x_key"],
                "schedulers": schedulers, "xlabel": "U_C",
                "title": (
                    f"{priority_policy} — Job-level deadline-meeting ratio (DMR) "
                    f"versus U_C (U_E={uc_slice['fixed_value']})"
                    + (f" (zoomed y-axis: {uc_dmr_ymin:g}–1.0)" if uc_dmr_ymin > 0 else "")
                ),
                "metric": "dmr",
                "ymin": uc_dmr_ymin,
            },
            {
                "rows": dmr_ue_rows, "output": str(root),
                "filename": "figure_scheduler_ue_dmr.png", "xkey": ue_slice["x_key"],
                "schedulers": schedulers, "xlabel": "U_E",
                "title": (
                    f"{priority_policy} — Job-level deadline-meeting ratio (DMR) "
                    f"versus U_E (U_C={ue_slice['fixed_value']})"
                    + (f" (zoomed y-axis: {ue_dmr_ymin:g}–1.0)" if ue_dmr_ymin > 0 else "")
                ),
                "metric": "dmr",
                "ymin": ue_dmr_ymin,
            },
        ], _plot_any_scan_job, workers=analysis_workers)
    except ImportError:
        pass
    plot_seconds = time.perf_counter() - plot_started
    report = {"complete": True, "tasksets": len(tasksets), "requests": len(requests),
              "results": len(results), "duplicate_request_ids": duplicate,
              "missing_request_ids": missing, "unexpected_request_ids": unexpected,
              "summary_rows": len(summaries),
              "dmr_summary_rows": len(dmr_summaries),
              "technical_result_count": len(technical_rows),
              "missing": missing, "duplicate": duplicate,
              "unexpected": unexpected, "technical": len(technical_rows),
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
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
