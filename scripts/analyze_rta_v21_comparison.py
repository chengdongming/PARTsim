#!/usr/bin/env python3
"""Analyze isolated v20.4 versus v21-local-window comparison results."""

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


V20_VERSION = "v20.4"
V21_VERSION = "v21-local-window"
RESULT_FILENAME = "rta_v21_comparison_results.csv"
SUMMARY_FILENAME = "rta_v21_comparison_summary.csv"
BY_UTIL_FILENAME = "rta_v21_comparison_by_utilization.csv"
PESSIMISM_CDF_PLOT = "pessimism_cdf.png"
INTERSECTION_PESSIMISM_BOXPLOT = "intersection_pessimism_boxplot.png"
RUNTIME_SLOWDOWN_PLOT = "runtime_slowdown.png"

V20_METADATA_DEFAULTS = {
    "v20_rta_version": V20_VERSION,
    "v20_theory_family": "complete_window",
    "v20_closure_method": "fixed_point_complete_window",
    "v20_uses_local_window": False,
    "v20_uses_delta_closure": False,
    "v20_empty_state_guard": True,
}

V21_METADATA_DEFAULTS = {
    "v21_theory_family": "local_window_closure",
    "v21_closure_method": "delta_closure",
    "v21_empty_set_guard": True,
    "v21_fallback_guard": True,
    "v21_consistency_guard": True,
    "v21_certified_carry_in_source": "v21_recursive_certification",
    "v21_uses_local_window": True,
    "v21_uses_delta_closure": True,
    "v21_uses_parallel_u_compression": False,
}

V21_PROFILE_SUM_COUNTERS = (
    "v21_delta_iterations",
    "v21_g_loc_calls",
    "v21_omega_feasibility_calls",
    "v21_empty_omega_count",
    "v21_no_closure_count",
    "v21_closed_prefix_count",
    "v21_delta_cap_exceeded_count",
    "v21_delta_jump_count",
)
V21_PROFILE_MAX_COUNTERS = ("v21_max_delta_cap", "v21_max_delta_seen")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze v20.4 versus v21-local-window RTA comparisons"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def _as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(
        {"1", "true", "yes"}
    )


def _is_schedulability_failure_status(status: str) -> bool:
    normalized = str(status or "").strip().lower().replace("-", "_")
    normalized = normalized.replace(" ", "_")
    return (
        normalized in {
            "rejected",
            "simulation_rejected",
            "deadline_miss",
            "deadline_missed",
            "dline_miss",
        }
        or "dline_miss" in normalized
        or ("deadline" in normalized and "miss" in normalized)
    )


def _soundness_failure_mask(frame: pd.DataFrame, accepted: pd.Series) -> pd.Series:
    if "simulation_status" not in frame.columns:
        return ~accepted
    return frame["simulation_status"].map(_is_schedulability_failure_status)


def _pessimism_series(
    frame: pd.DataFrame,
    bound_column: str,
    proven: pd.Series,
    accepted: pd.Series,
) -> pd.Series:
    observed = pd.to_numeric(frame["simulated_response_time"], errors="coerce")
    bound = pd.to_numeric(frame[bound_column], errors="coerce")
    valid = (
        proven
        & accepted
        & observed.notna()
        & bound.notna()
        & observed.map(lambda value: math.isfinite(value) and value > 0)
        & bound.map(math.isfinite)
    )
    result = pd.Series(math.nan, index=frame.index, dtype=float)
    result.loc[valid] = bound.loc[valid] / observed.loc[valid]
    return result


def _ensure_column(frame: pd.DataFrame, column: str, default) -> None:
    if column not in frame.columns:
        frame[column] = default


def load_results(input_path) -> pd.DataFrame:
    path = Path(input_path)
    if "rta-e0-sensitivity-v20p4" in str(path).lower():
        raise ValueError("v21 analyzer refuses frozen v20.4 experiment paths")
    frame = pd.read_csv(path)
    if "v21_rta_version" not in frame.columns:
        if "results_file" not in frame.columns:
            raise ValueError("input is neither a comparison CSV nor its manifest")
        parts = []
        for value in frame["results_file"].dropna():
            result_path = Path(str(value))
            if "rta-e0-sensitivity-v20p4" in str(result_path).lower():
                raise ValueError("manifest references a frozen v20.4 output")
            parts.append(pd.read_csv(result_path))
        if not parts:
            raise ValueError("manifest contains no comparison result files")
        frame = pd.concat(parts, ignore_index=True)

    required = {
        "E0", "normalized_utilization", "accepted",
        "simulated_response_time", "v20p4_bound", "v21_bound",
        "v20p4_rta_version",
        "v21_rta_version", "v20p4_status", "v21_status",
        "v20p4_proven", "v21_proven", "both_proven",
        "v21_proven_v20p4_unproven", "v20p4_proven_v21_unproven",
        "v21_minus_v20p4_bound", "v20p4_tightness", "v21_tightness",
        "v21_soundness_proven_but_rejected",
        "v21_soundness_observed_exceeds_bound",
        "v20p4_soundness_proven_but_rejected",
        "v20p4_soundness_observed_exceeds_bound",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("comparison CSV is missing fields: {}".format(missing))
    if set(frame["v20p4_rta_version"].dropna().astype(str)) != {V20_VERSION}:
        raise ValueError("comparison CSV mixes or mislabels v20.4 results")
    if set(frame["v21_rta_version"].dropna().astype(str)) != {V21_VERSION}:
        raise ValueError("comparison CSV mixes or mislabels v21 results")

    for column, default in V20_METADATA_DEFAULTS.items():
        _ensure_column(frame, column, default)
    for column, default in V21_METADATA_DEFAULTS.items():
        _ensure_column(frame, column, default)
    for column in V21_PROFILE_SUM_COUNTERS + V21_PROFILE_MAX_COUNTERS:
        _ensure_column(frame, column, 0)
    for column, default in (
        ("v21_no_closure_observed", False),
        ("v21_timeout_or_horizon_failure", False),
        ("v21_fallback_used", False),
        ("v21_fallback_reason", ""),
        ("v21_failure_reason", ""),
        ("v21_certificate_status", ""),
        ("v21_bound_gt_v20", False),
        ("v21_bound_gt_v20_reason", ""),
        ("v20_sim_rejected_violation", False),
        ("v20_observed_bound_violation", False),
        ("v21_sim_rejected_violation", False),
        ("v21_observed_bound_violation", False),
    ):
        _ensure_column(frame, column, default)
    if "v20_only_proven" not in frame.columns:
        frame["v20_only_proven"] = frame["v20p4_proven_v21_unproven"]
    if "v21_only_proven" not in frame.columns:
        frame["v21_only_proven"] = frame["v21_proven_v20p4_unproven"]
    if "both_rejected" not in frame.columns:
        frame["both_rejected"] = (
            (frame["v20p4_status"].astype(str) == "rta_unproven")
            & (frame["v21_status"].astype(str) == "rta_unproven")
        )
    for column in (
        "runtime_v20_sec", "runtime_v21_sec",
        "runtime_slowdown_v21_over_v20",
    ):
        if column not in frame.columns:
            frame[column] = math.nan
    if "v20_soundness_violation" not in frame.columns:
        frame["v20_soundness_violation"] = (
            frame["v20p4_soundness_proven_but_rejected"]
        )
    if "v21_soundness_violation" not in frame.columns:
        frame["v21_soundness_violation"] = (
            frame["v21_soundness_proven_but_rejected"]
        )
    if "soundness_valid" not in frame.columns:
        frame["soundness_valid"] = True
    if "soundness_excluded_reason" not in frame.columns:
        frame["soundness_excluded_reason"] = ""

    boolean_columns = [
        "v20p4_proven", "v21_proven", "both_proven",
        "v21_proven_v20p4_unproven", "v20p4_proven_v21_unproven",
        "v20_only_proven", "v21_only_proven", "both_rejected",
        "v21_bound_lt_v20p4", "v21_bound_gt_v20p4",
        "v21_soundness_proven_but_rejected",
        "v21_soundness_observed_exceeds_bound",
        "v20p4_soundness_proven_but_rejected",
        "v20p4_soundness_observed_exceeds_bound",
        "v20_sim_rejected_violation",
        "v20_observed_bound_violation",
        "v21_sim_rejected_violation",
        "v21_observed_bound_violation",
        "v20_soundness_violation",
        "v21_soundness_violation",
        "soundness_valid",
        "v20_uses_local_window",
        "v20_uses_delta_closure",
        "v20_empty_state_guard",
        "v21_empty_set_guard",
        "v21_fallback_guard",
        "v21_consistency_guard",
        "v21_uses_local_window",
        "v21_uses_delta_closure",
        "v21_uses_parallel_u_compression",
        "v21_no_closure_observed",
        "v21_timeout_or_horizon_failure",
        "v21_fallback_used",
        "v21_bound_gt_v20",
    ]
    for column in boolean_columns:
        if column in frame:
            frame[column] = _as_bool(frame[column])
    for column in (
        "E0", "normalized_utilization", "v21_minus_v20p4_bound",
        "simulated_response_time", "v20p4_bound", "v21_bound",
        "v20p4_tightness", "v21_tightness",
        "pessimism_v20", "pessimism_v21",
        "intersection_pessimism_v20", "intersection_pessimism_v21",
        "intersection_pessimism_improvement",
        "runtime_v20_sec", "runtime_v21_sec",
        "runtime_slowdown_v21_over_v20",
        *V21_PROFILE_SUM_COUNTERS,
        *V21_PROFILE_MAX_COUNTERS,
    ):
        if column not in frame.columns:
            frame[column] = math.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    accepted = _as_bool(frame["accepted"])
    valid_soundness = _as_bool(frame["soundness_valid"])
    raw_v20_soundness = frame["v20_soundness_violation"].copy()
    raw_v21_soundness = frame["v21_soundness_violation"].copy()
    frame["pessimism_v20"] = _pessimism_series(
        frame, "v20p4_bound", frame["v20p4_proven"], accepted
    )
    frame["pessimism_v21"] = _pessimism_series(
        frame, "v21_bound", frame["v21_proven"], accepted
    )
    intersection = (
        frame["both_proven"]
        & accepted
        & frame["pessimism_v20"].notna()
        & frame["pessimism_v21"].notna()
    )
    frame["intersection_pessimism_v20"] = math.nan
    frame["intersection_pessimism_v21"] = math.nan
    frame["intersection_pessimism_improvement"] = math.nan
    frame.loc[intersection, "intersection_pessimism_v20"] = frame.loc[
        intersection, "pessimism_v20"
    ]
    frame.loc[intersection, "intersection_pessimism_v21"] = frame.loc[
        intersection, "pessimism_v21"
    ]
    frame.loc[intersection, "intersection_pessimism_improvement"] = (
        frame.loc[intersection, "pessimism_v20"]
        - frame.loc[intersection, "pessimism_v21"]
    )
    schedulability_failure = (
        valid_soundness & _soundness_failure_mask(frame, accepted)
    )
    v21_rejected = frame["v21_proven"] & schedulability_failure
    v20_rejected = frame["v20p4_proven"] & schedulability_failure
    frame["v21_soundness_proven_but_rejected"] |= v21_rejected
    frame["v20p4_soundness_proven_but_rejected"] |= v20_rejected
    frame["v21_sim_rejected_violation"] |= (
        frame["v21_soundness_proven_but_rejected"]
    )
    frame["v20_sim_rejected_violation"] |= (
        frame["v20p4_soundness_proven_but_rejected"]
    )
    frame["v21_sim_rejected_violation"] |= v21_rejected
    frame["v20_sim_rejected_violation"] |= v20_rejected
    finite_observed = (
        frame["simulated_response_time"].notna()
        & frame["simulated_response_time"].map(math.isfinite)
    )
    v21_observed = (
        valid_soundness
        & frame["v21_proven"]
        & finite_observed
        & frame["v21_bound"].notna()
        & frame["v21_bound"].map(math.isfinite)
        & (frame["simulated_response_time"] > frame["v21_bound"])
    )
    v20_observed = (
        valid_soundness
        & frame["v20p4_proven"]
        & finite_observed
        & frame["v20p4_bound"].notna()
        & frame["v20p4_bound"].map(math.isfinite)
        & (frame["simulated_response_time"] > frame["v20p4_bound"])
    )
    frame["v21_soundness_observed_exceeds_bound"] |= v21_observed
    frame["v20p4_soundness_observed_exceeds_bound"] |= v20_observed
    frame["v21_observed_bound_violation"] |= (
        frame["v21_soundness_observed_exceeds_bound"]
    )
    frame["v20_observed_bound_violation"] |= (
        frame["v20p4_soundness_observed_exceeds_bound"]
    )
    frame["v21_observed_bound_violation"] |= v21_observed
    frame["v20_observed_bound_violation"] |= v20_observed
    frame["v21_soundness_violation"] = (
        raw_v21_soundness
        | frame["v21_sim_rejected_violation"]
        | frame["v21_observed_bound_violation"]
    )
    frame["v20_soundness_violation"] = (
        raw_v20_soundness
        | frame["v20_sim_rejected_violation"]
        | frame["v20_observed_bound_violation"]
    )
    both_bounds = (
        frame["both_proven"]
        & frame["v20p4_bound"].notna()
        & frame["v21_bound"].notna()
        & frame["v20p4_bound"].map(math.isfinite)
        & frame["v21_bound"].map(math.isfinite)
    )
    frame["v21_bound_gt_v20"] |= (
        both_bounds & (frame["v21_bound"] > frame["v20p4_bound"])
    )
    missing_reason = frame["v21_bound_gt_v20_reason"].fillna("").astype(str) == ""
    frame.loc[
        frame["v21_bound_gt_v20"] & missing_reason,
        "v21_bound_gt_v20_reason",
    ] = "both_proven_v21_bound_larger"
    frame.loc[
        frame["v20_only_proven"] & missing_reason,
        "v21_bound_gt_v20_reason",
    ] = "v21_unproven_v20_proven"
    return frame


def _finite_values(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return values.loc[values.map(math.isfinite)]


def _finite_stats(series: pd.Series, prefix: str) -> dict:
    values = _finite_values(series)
    return {
        "mean_{}".format(prefix): values.mean() if len(values) else math.nan,
        "median_{}".format(prefix): values.median() if len(values) else math.nan,
        "p75_{}".format(prefix): values.quantile(0.75) if len(values) else math.nan,
        "p90_{}".format(prefix): values.quantile(0.9) if len(values) else math.nan,
        "p95_{}".format(prefix): values.quantile(0.95) if len(values) else math.nan,
        "max_{}".format(prefix): values.max() if len(values) else math.nan,
    }


def _reason_counts(series: pd.Series) -> str:
    values = []
    for value in series.dropna():
        text = str(value).strip()
        if text and text.lower() != "nan":
            values.append(text)
    return json.dumps(dict(sorted(Counter(values).items())), sort_keys=True)


def summarize_group(group: pd.DataFrame) -> dict:
    both = group[group["both_proven"]]
    v20_proven = group[group["v20p4_proven"]]
    v21_proven = group[group["v21_proven"]]
    both_count = int(group["both_proven"].sum())
    v21_looser_count = int(both["v21_bound_gt_v20"].sum())
    result = {
        "total_rows": len(group),
        "num_tasksets": len(group),
        "v20p4_proven_count": int(group["v20p4_proven"].sum()),
        "v20_proven_count": int(group["v20p4_proven"].sum()),
        "v21_proven_count": int(group["v21_proven"].sum()),
        "both_proven_count": both_count,
        "v20_only_proven_count": int(group["v20_only_proven"].sum()),
        "v21_only_proven_count": int(group["v21_only_proven"].sum()),
        "both_rejected_count": int(group["both_rejected"].sum()),
        "neither_proven_count": int(
            (~group["v20p4_proven"] & ~group["v21_proven"]).sum()
        ),
        "v21_proven_v20p4_unproven_count": int(
            group["v21_proven_v20p4_unproven"].sum()
        ),
        "v20p4_proven_v21_unproven_count": int(
            group["v20p4_proven_v21_unproven"].sum()
        ),
        "v21_unproven_v20_proven_count": int(group["v20_only_proven"].sum()),
        "v21_proven_v20_unproven_count": int(group["v21_only_proven"].sum()),
        "v21_timeout_count": int((group["v21_status"] == "rta_timeout").sum()),
        "v20p4_timeout_count": int(
            (group["v20p4_status"] == "rta_timeout").sum()
        ),
        "v21_error_count": int((group["v21_status"] == "rta_error").sum()),
        "v20p4_error_count": int(
            (group["v20p4_status"] == "rta_error").sum()
        ),
        "v21_bound_gt_v20p4_count": int(both["v21_bound_gt_v20p4"].sum()),
        "v21_bound_lt_v20p4_count": int(both["v21_bound_lt_v20p4"].sum()),
        "v21_bound_gt_v20_count": v21_looser_count,
        "v21_bound_gt_v20_rate": (
            v21_looser_count / both_count if both_count else math.nan
        ),
        "v21_bound_gt_v20_reason_counts": _reason_counts(
            group["v21_bound_gt_v20_reason"]
        ),
        "both_proven_v21_tighter_count": int(
            (both["v21_minus_v20p4_bound"] < 0).sum()
        ),
        "both_proven_equal_count": int(
            (both["v21_minus_v20p4_bound"] == 0).sum()
        ),
        "both_proven_v21_looser_count": v21_looser_count,
        "v20_sim_rejected_violation_count": int(
            group["v20_sim_rejected_violation"].sum()
        ),
        "v20_observed_bound_violation_count": int(
            group["v20_observed_bound_violation"].sum()
        ),
        "v20_soundness_violation_count": int(
            group["v20_soundness_violation"].sum()
        ),
        "v21_sim_rejected_violation_count": int(
            group["v21_sim_rejected_violation"].sum()
        ),
        "v21_observed_bound_violation_count": int(
            group["v21_observed_bound_violation"].sum()
        ),
        "v21_soundness_violation_count": int(
            group["v21_soundness_violation"].sum()
        ),
        "soundness_violations": int(
            (group["v20_soundness_violation"] | group["v21_soundness_violation"]).sum()
        ),
        "v21_fallback_used_count": int(group["v21_fallback_used"].sum()),
        "v21_fallback_reason_counts": _reason_counts(
            group["v21_fallback_reason"]
        ),
        "v21_failure_reason_counts": _reason_counts(
            group["v21_failure_reason"]
        ),
        "v21_no_closure_observed_count": int(
            group["v21_no_closure_observed"].sum()
        ),
        "v21_timeout_or_horizon_failure_count": int(
            group["v21_timeout_or_horizon_failure"].sum()
        ),
    }
    for column in V21_PROFILE_SUM_COUNTERS:
        result["{}_total".format(column)] = int(_finite_values(group[column]).sum())
    for column in V21_PROFILE_MAX_COUNTERS:
        values = _finite_values(group[column])
        result["{}_max".format(column)] = values.max() if len(values) else math.nan
    deltas = both["v21_minus_v20p4_bound"].dropna()
    result.update({
        "mean_bound_delta": deltas.mean() if len(deltas) else math.nan,
        "median_bound_delta": deltas.median() if len(deltas) else math.nan,
    })
    result.update(_finite_stats(v20_proven["v20p4_tightness"], "v20p4_tightness"))
    result.update(_finite_stats(v21_proven["v21_tightness"], "v21_tightness"))
    result.update(_finite_stats(group["pessimism_v20"], "pessimism_v20"))
    result.update(_finite_stats(group["pessimism_v21"], "pessimism_v21"))
    result.update(_finite_stats(
        group["intersection_pessimism_v20"],
        "intersection_pessimism_v20",
    ))
    result.update(_finite_stats(
        group["intersection_pessimism_v21"],
        "intersection_pessimism_v21",
    ))
    result.update(_finite_stats(
        group["intersection_pessimism_improvement"],
        "intersection_pessimism_improvement",
    ))
    result.update(_finite_stats(group["runtime_v20_sec"], "runtime_v20_sec"))
    result.update(_finite_stats(group["runtime_v21_sec"], "runtime_v21_sec"))
    result.update(_finite_stats(
        group["runtime_slowdown_v21_over_v20"],
        "runtime_slowdown_v21_over_v20",
    ))
    result["runtime_p95_v20_sec"] = result["p95_runtime_v20_sec"]
    result["runtime_p95_v21_sec"] = result["p95_runtime_v21_sec"]
    result["runtime_p95_slowdown_v21_over_v20"] = result[
        "p95_runtime_slowdown_v21_over_v20"
    ]
    return result


def build_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [{"scope": "overall", "E0": "all", **summarize_group(frame)}]
    for e0, group in frame.groupby("E0", sort=True):
        rows.append({"scope": "E0", "E0": e0, **summarize_group(group)})
    return pd.DataFrame(rows)


def build_by_utilization(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (e0, utilization), group in frame.groupby(
        ["E0", "normalized_utilization"], sort=True
    ):
        rows.append({
            "E0": e0,
            "normalized_utilization": utilization,
            **summarize_group(group),
        })
    return pd.DataFrame(rows)


def _plot_proven(by_util: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    for e0, group in by_util.groupby("E0", sort=True):
        denominator = group["num_tasksets"].replace(0, math.nan)
        axis.plot(
            group["normalized_utilization"],
            group["v20p4_proven_count"] / denominator,
            marker="o", linestyle="--", label="v20.4 E0={}".format(e0),
        )
        axis.plot(
            group["normalized_utilization"],
            group["v21_proven_count"] / denominator,
            marker="s", label="v21 E0={}".format(e0),
        )
    axis.set_xlabel("Normalized utilization")
    axis.set_ylabel("Proven ratio")
    axis.set_ylim(-0.02, 1.02)
    axis.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_tightness(frame: pd.DataFrame, output: Path) -> None:
    """Plot legacy pessimism-ratio aliases retained for compatibility."""
    figure, axis = plt.subplots(figsize=(7, 4.5))
    values = []
    labels = []
    for version, column in (("v20.4", "v20p4_tightness"), ("v21", "v21_tightness")):
        data = frame.loc[frame[column].notna(), column]
        if len(data):
            values.append(data)
            labels.append(version)
    if values:
        axis.boxplot(values, labels=labels, showfliers=False)
    axis.set_ylabel("Legacy pessimism ratio (bound / observed)")
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_delta(frame: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    values = frame.loc[frame["both_proven"], "v21_minus_v20p4_bound"].dropna()
    if len(values):
        bins = min(30, max(5, int(values.nunique())))
        axis.hist(values, bins=bins)
    axis.axvline(0, color="black", linewidth=1)
    axis.set_xlabel("v21 bound - v20.4 bound")
    axis.set_ylabel("Tasksets")
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_pessimism_cdf(frame: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    plotted = False
    for label, column in (
        ("v20.4", "pessimism_v20"),
        ("v21 experimental", "pessimism_v21"),
    ):
        values = _finite_values(frame[column]).sort_values()
        if len(values):
            cumulative = [
                float(index) / len(values)
                for index in range(1, len(values) + 1)
            ]
            axis.step(values, cumulative, where="post", label=label)
            plotted = True
    if plotted:
        axis.legend()
    else:
        axis.text(
            0.5, 0.5, "No own-proven pessimism data",
            ha="center", va="center", transform=axis.transAxes,
        )
    axis.set_xlabel("Pessimism ratio (RTA bound / observed max response)")
    axis.set_ylabel("Empirical CDF")
    axis.set_ylim(0, 1.02)
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_intersection_pessimism(frame: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    both = frame.loc[frame["both_proven"]]
    values = []
    labels = []
    for label, column in (
        ("v20.4", "intersection_pessimism_v20"),
        ("v21 experimental", "intersection_pessimism_v21"),
    ):
        data = _finite_values(both[column])
        if len(data):
            values.append(data)
            labels.append(label)
    if values:
        axis.boxplot(values, labels=labels, showfliers=False)
    else:
        axis.text(
            0.5, 0.5, "No both-proven intersection data",
            ha="center", va="center", transform=axis.transAxes,
        )
    axis.set_ylabel("Pessimism ratio (RTA bound / observed max response)")
    axis.set_title("Both-proven taskset intersection")
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_runtime_slowdown(frame: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    values = _finite_values(
        frame["runtime_slowdown_v21_over_v20"]
    ).sort_values()
    if len(values):
        cumulative = [
            float(index) / len(values)
            for index in range(1, len(values) + 1)
        ]
        axis.step(values, cumulative, where="post")
    else:
        axis.text(
            0.5, 0.5, "No runtime slowdown data",
            ha="center", va="center", transform=axis.transAxes,
        )
    axis.axvline(1.0, color="black", linewidth=1)
    axis.set_xlabel("Runtime slowdown (v21 / v20.4)")
    axis.set_ylabel("Empirical CDF")
    axis.set_ylim(0, 1.02)
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def analyze(input_path, output_dir) -> tuple:
    frame = load_results(input_path)
    output = Path(output_dir)
    if "rta-e0-sensitivity-v20p4" in str(output).lower():
        raise ValueError("v21 analysis cannot overwrite frozen v20.4 analysis")
    output.mkdir(parents=True, exist_ok=True)
    plots = output / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    summary = build_summary(frame)
    by_util = build_by_utilization(frame)
    summary.to_csv(output / SUMMARY_FILENAME, index=False)
    by_util.to_csv(output / BY_UTIL_FILENAME, index=False)
    _plot_delta(frame, output / "rta_v21_bound_delta.png")
    _plot_tightness(frame, output / "rta_v21_tightness_comparison.png")
    _plot_proven(by_util, output / "rta_v21_proven_ratio.png")
    _plot_pessimism_cdf(frame, plots / PESSIMISM_CDF_PLOT)
    _plot_intersection_pessimism(
        frame, plots / INTERSECTION_PESSIMISM_BOXPLOT
    )
    _plot_runtime_slowdown(frame, plots / RUNTIME_SLOWDOWN_PLOT)
    return summary, by_util


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    analyze(args.input, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
