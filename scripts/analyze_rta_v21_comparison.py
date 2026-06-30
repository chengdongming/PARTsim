#!/usr/bin/env python3
"""Analyze isolated v20.4 versus v21-local-window comparison results."""

import argparse
import math
import sys
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

    boolean_columns = [
        "v20p4_proven", "v21_proven", "both_proven",
        "v21_proven_v20p4_unproven", "v20p4_proven_v21_unproven",
        "v21_bound_lt_v20p4", "v21_bound_gt_v20p4",
        "v21_soundness_proven_but_rejected",
        "v21_soundness_observed_exceeds_bound",
        "v20p4_soundness_proven_but_rejected",
        "v20p4_soundness_observed_exceeds_bound",
    ]
    for column in boolean_columns:
        if column in frame:
            frame[column] = _as_bool(frame[column])
    for column in (
        "E0", "normalized_utilization", "v21_minus_v20p4_bound",
        "simulated_response_time", "v20p4_bound", "v21_bound",
        "v20p4_tightness", "v21_tightness",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    accepted = _as_bool(frame["accepted"])
    frame["v21_soundness_proven_but_rejected"] |= (
        frame["v21_proven"] & ~accepted
    )
    frame["v20p4_soundness_proven_but_rejected"] |= (
        frame["v20p4_proven"] & ~accepted
    )
    frame["v21_soundness_observed_exceeds_bound"] |= (
        frame["v21_proven"]
        & frame["simulated_response_time"].notna()
        & frame["v21_bound"].notna()
        & (frame["simulated_response_time"] > frame["v21_bound"])
    )
    frame["v20p4_soundness_observed_exceeds_bound"] |= (
        frame["v20p4_proven"]
        & frame["simulated_response_time"].notna()
        & frame["v20p4_bound"].notna()
        & (frame["simulated_response_time"] > frame["v20p4_bound"])
    )

    violations = int(
        frame[
            [
                "v21_soundness_proven_but_rejected",
                "v21_soundness_observed_exceeds_bound",
                "v20p4_soundness_proven_but_rejected",
                "v20p4_soundness_observed_exceeds_bound",
            ]
        ].sum().sum()
    )
    if violations:
        raise ValueError(
            "RTA soundness violation present in comparison rows: {}".format(
                violations
            )
        )
    return frame


def _finite_stats(series: pd.Series, prefix: str) -> dict:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return {
        "mean_{}".format(prefix): values.mean() if len(values) else math.nan,
        "median_{}".format(prefix): values.median() if len(values) else math.nan,
        "max_{}".format(prefix): values.max() if len(values) else math.nan,
    }


def summarize_group(group: pd.DataFrame) -> dict:
    both = group[group["both_proven"]]
    v20_proven = group[group["v20p4_proven"]]
    v21_proven = group[group["v21_proven"]]
    result = {
        "num_tasksets": len(group),
        "v20p4_proven_count": int(group["v20p4_proven"].sum()),
        "v21_proven_count": int(group["v21_proven"].sum()),
        "both_proven_count": int(group["both_proven"].sum()),
        "v21_proven_v20p4_unproven_count": int(
            group["v21_proven_v20p4_unproven"].sum()
        ),
        "v20p4_proven_v21_unproven_count": int(
            group["v20p4_proven_v21_unproven"].sum()
        ),
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
        "soundness_violations": 0,
    }
    deltas = both["v21_minus_v20p4_bound"].dropna()
    result.update({
        "mean_bound_delta": deltas.mean() if len(deltas) else math.nan,
        "median_bound_delta": deltas.median() if len(deltas) else math.nan,
    })
    result.update(_finite_stats(v20_proven["v20p4_tightness"], "v20p4_tightness"))
    result.update(_finite_stats(v21_proven["v21_tightness"], "v21_tightness"))
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
    axis.set_ylabel("Task-level mean tightness")
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


def analyze(input_path, output_dir) -> tuple:
    frame = load_results(input_path)
    output = Path(output_dir)
    if "rta-e0-sensitivity-v20p4" in str(output).lower():
        raise ValueError("v21 analysis cannot overwrite frozen v20.4 analysis")
    output.mkdir(parents=True, exist_ok=True)
    summary = build_summary(frame)
    by_util = build_by_utilization(frame)
    summary.to_csv(output / SUMMARY_FILENAME, index=False)
    by_util.to_csv(output / BY_UTIL_FILENAME, index=False)
    _plot_delta(frame, output / "rta_v21_bound_delta.png")
    _plot_tightness(frame, output / "rta_v21_tightness_comparison.png")
    _plot_proven(by_util, output / "rta_v21_proven_ratio.png")
    return summary, by_util


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    analyze(args.input, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
