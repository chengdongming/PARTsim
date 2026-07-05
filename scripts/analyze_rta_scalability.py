#!/usr/bin/env python3
"""Analyze v20.4 ASAP-BLOCK RTA scalability result rows."""

import argparse
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


SUMMARY_FILENAME = "rta_scalability_summary.csv"
BY_N_FILENAME = "rta_scalability_by_n.csv"
BY_M_FILENAME = "rta_scalability_by_m.csv"
BY_UTILIZATION_FILENAME = "rta_scalability_by_utilization.csv"
BY_CONFIG_FILENAME = "rta_scalability_by_config.csv"

RUNTIME_N_PLOT = "runtime_vs_n.png"
RUNTIME_M_PLOT = "runtime_vs_m.png"
RUNTIME_UTILIZATION_PLOT = "runtime_vs_utilization.png"
TIMEOUT_RATE_PLOT = "timeout_rate.png"

SUMMARY_COLUMNS = [
    "group_key",
    "group_value",
    "total_rows",
    "rta_attempted_count",
    "rta_completed_count",
    "rta_timeout_count",
    "rta_error_count",
    "rta_unattempted_count",
    "rta_proven_count",
    "rta_unproven_count",
    "rta_proven_ratio",
    "rta_timeout_rate",
    "rta_error_rate",
    "runtime_sample_count",
    "runtime_mean_sec",
    "runtime_median_sec",
    "runtime_p75_sec",
    "runtime_p90_sec",
    "runtime_p95_sec",
    "runtime_max_sec",
    "profile_runtime_sample_count",
    "profile_runtime_mean_sec",
    "profile_runtime_median_sec",
    "profile_runtime_p95_sec",
    "profile_runtime_max_sec",
    "input_file",
]

REQUIRED_COLUMNS = {
    "config_id",
    "task_n",
    "M",
    "utilization",
    "rta_status",
    "rta_error",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_timed_out",
    "rta_profile_task_time_sum_sec",
}

TRUE_VALUES = {"1", "true", "yes", "y"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze v20.4 RTA-only scalability wall-clock and internal "
            "profile measurements"
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "validate input existence and required schema; timeout/error rows "
            "remain valid scalability observations"
        ),
    )
    return parser


def _truthy(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip().lower() in TRUE_VALUES


def _nonempty(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip() != ""


def _normalized(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _finite_nonnegative(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    valid = values.map(
        lambda value: (
            pd.notna(value)
            and math.isfinite(value)
            and value >= 0
        )
    ).astype(bool)
    return values.where(valid)


def _error_status(value) -> bool:
    status = _normalized(value)
    return (
        status in {
            "rta_error",
            "error",
            "failed",
            "failure",
            "exception",
            "invalid_json",
            "runner_error",
            "task_generation_error",
        }
        or "error" in status
        or status.endswith("_failed")
    )


def load_results(input_path) -> pd.DataFrame:
    path = Path(input_path)
    frame = pd.read_csv(path, keep_default_na=False)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if "rta_schedulable" not in frame.columns and "rta_proven" not in frame.columns:
        missing.append("rta_schedulable or rta_proven")
    if missing:
        raise ValueError(
            "scalability results are missing fields: {}".format(
                ", ".join(missing)
            )
        )

    for column in ("task_n", "M", "utilization"):
        values = pd.to_numeric(frame[column], errors="coerce")
        if len(frame) and values.isna().any():
            raise ValueError("{} contains non-numeric values".format(column))
        frame[column] = values
    if len(frame) and frame["config_id"].map(_nonempty).eq(False).any():
        raise ValueError("config_id must be non-empty")

    frame["_attempted"] = frame["rta_attempted"].map(_truthy)
    frame["_timeout"] = (
        frame["_attempted"] & frame["rta_timed_out"].map(_truthy)
    )
    error_text = frame["rta_error"].map(_nonempty)
    error_status = frame["rta_status"].map(_error_status)
    frame["_error"] = (
        frame["_attempted"]
        & ~frame["_timeout"]
        & (error_text | error_status)
    )
    frame["_completed"] = (
        frame["_attempted"] & ~frame["_timeout"] & ~frame["_error"]
    )

    proven_column = (
        "rta_schedulable"
        if "rta_schedulable" in frame.columns
        else "rta_proven"
    )
    frame["_proven"] = frame[proven_column].map(_truthy)
    frame["_runtime"] = _finite_nonnegative(frame["rta_runtime_sec"])
    # Canonical paper runtime excludes timeout and infrastructure/error rows.
    frame["_runtime_sample"] = frame["_completed"] & frame["_runtime"].notna()
    frame["_profile_runtime"] = _finite_nonnegative(
        frame["rta_profile_task_time_sum_sec"]
    )
    frame["_profile_runtime_sample"] = frame["_profile_runtime"].notna()
    return frame


def _ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else math.nan


def _stats(values: pd.Series, prefix: str) -> dict:
    finite = _finite_nonnegative(values).dropna()
    result = {
        "{}_sample_count".format(prefix): int(len(finite)),
        "{}_mean_sec".format(prefix): (
            finite.mean() if len(finite) else math.nan
        ),
        "{}_median_sec".format(prefix): (
            finite.median() if len(finite) else math.nan
        ),
        "{}_p95_sec".format(prefix): (
            finite.quantile(0.95) if len(finite) else math.nan
        ),
        "{}_max_sec".format(prefix): (
            finite.max() if len(finite) else math.nan
        ),
    }
    if prefix == "runtime":
        result.update({
            "runtime_p75_sec": (
                finite.quantile(0.75) if len(finite) else math.nan
            ),
            "runtime_p90_sec": (
                finite.quantile(0.90) if len(finite) else math.nan
            ),
        })
    return result


def summarize_group(
    group: pd.DataFrame,
    group_key: str,
    group_value,
    input_path: Path,
) -> dict:
    attempted = int(group["_attempted"].sum())
    completed = int(group["_completed"].sum())
    timeout = int(group["_timeout"].sum())
    errors = int(group["_error"].sum())
    proven = int((group["_completed"] & group["_proven"]).sum())
    unproven = int((group["_completed"] & ~group["_proven"]).sum())
    runtime_values = group.loc[group["_runtime_sample"], "_runtime"]
    profile_values = group.loc[
        group["_profile_runtime_sample"], "_profile_runtime"
    ]
    row = {
        "group_key": group_key,
        "group_value": group_value,
        "total_rows": int(len(group)),
        "rta_attempted_count": attempted,
        "rta_completed_count": completed,
        "rta_timeout_count": timeout,
        "rta_error_count": errors,
        "rta_unattempted_count": int(len(group) - attempted),
        "rta_proven_count": proven,
        "rta_unproven_count": unproven,
        "rta_proven_ratio": _ratio(proven, completed),
        "rta_timeout_rate": _ratio(timeout, attempted),
        "rta_error_rate": _ratio(errors, attempted),
        "input_file": str(input_path),
    }
    row.update(_stats(runtime_values, "runtime"))
    row.update(_stats(profile_values, "profile_runtime"))
    return {column: row.get(column, math.nan) for column in SUMMARY_COLUMNS}


def build_overall(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    row = summarize_group(frame, "overall", "all", input_path)
    return pd.DataFrame([row], columns=SUMMARY_COLUMNS)


def build_grouped(
    frame: pd.DataFrame,
    source_column: str,
    group_key: str,
    input_path: Path,
) -> pd.DataFrame:
    rows = [
        summarize_group(group, group_key, value, input_path)
        for value, group in frame.groupby(source_column, sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _finite_plot_values(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype=float)
    return _finite_nonnegative(frame[column]).dropna()


def _plot_runtime(
    grouped: pd.DataFrame,
    output: Path,
    x_label: str,
    title: str,
) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    x = pd.to_numeric(grouped.get("group_value"), errors="coerce")
    median = pd.to_numeric(grouped.get("runtime_median_sec"), errors="coerce")
    p95 = pd.to_numeric(grouped.get("runtime_p95_sec"), errors="coerce")
    median_mask = x.notna() & median.notna()
    p95_mask = x.notna() & p95.notna()
    plotted = False
    if median_mask.any():
        axis.plot(
            x[median_mask], median[median_mask],
            marker="o", label="median",
        )
        plotted = True
    if p95_mask.any():
        axis.plot(
            x[p95_mask], p95[p95_mask],
            marker="s", linestyle="--", label="p95",
        )
        plotted = True
    if plotted:
        axis.legend()
    else:
        axis.text(
            0.5, 0.5, "No canonical runtime data",
            ha="center", va="center", transform=axis.transAxes,
        )
    axis.set_xlabel(x_label)
    axis.set_ylabel("RTA subprocess wall-clock runtime (sec)")
    axis.set_title(title)
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_timeout_rate(by_n: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    x = pd.to_numeric(by_n.get("group_value"), errors="coerce")
    rates = pd.to_numeric(by_n.get("rta_timeout_rate"), errors="coerce")
    mask = x.notna() & rates.notna()
    if mask.any():
        axis.plot(x[mask], rates[mask], marker="o")
    else:
        axis.text(
            0.5, 0.5, "No attempted samples",
            ha="center", va="center", transform=axis.transAxes,
        )
    axis.set_xlabel("Number of tasks (n)")
    axis.set_ylabel("RTA timeout rate")
    axis.set_ylim(0, 1.02)
    axis.set_title("Timeout rate by task count")
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def analyze(input_path, output_dir, manifest_path=None):
    input_path = Path(input_path)
    if manifest_path is not None and not Path(manifest_path).is_file():
        raise FileNotFoundError("manifest does not exist: {}".format(manifest_path))
    frame = load_results(input_path)
    output = Path(output_dir)
    plots = output / "plots"
    output.mkdir(parents=True, exist_ok=True)
    plots.mkdir(parents=True, exist_ok=True)

    overall = build_overall(frame, input_path)
    by_n = build_grouped(frame, "task_n", "task_n", input_path)
    by_m = build_grouped(frame, "M", "M", input_path)
    by_utilization = build_grouped(
        frame, "utilization", "utilization", input_path
    )
    by_config = build_grouped(
        frame, "config_id", "config_id", input_path
    )

    overall.to_csv(output / SUMMARY_FILENAME, index=False)
    by_n.to_csv(output / BY_N_FILENAME, index=False)
    by_m.to_csv(output / BY_M_FILENAME, index=False)
    by_utilization.to_csv(output / BY_UTILIZATION_FILENAME, index=False)
    by_config.to_csv(output / BY_CONFIG_FILENAME, index=False)

    _plot_runtime(
        by_n, plots / RUNTIME_N_PLOT,
        "Number of tasks (n)", "RTA runtime by task count",
    )
    _plot_runtime(
        by_m, plots / RUNTIME_M_PLOT,
        "Processors (M)", "RTA runtime by processor count",
    )
    _plot_runtime(
        by_utilization, plots / RUNTIME_UTILIZATION_PLOT,
        "Total utilization", "RTA runtime by utilization",
    )
    _plot_timeout_rate(by_n, plots / TIMEOUT_RATE_PLOT)
    return overall, by_n, by_m, by_utilization, by_config


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analyze(args.input, args.output_dir, args.manifest)
    except (OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
