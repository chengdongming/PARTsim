#!/usr/bin/env python3
"""Analyze v20.4 utilization/release-time-E0 sensitivity results."""

import argparse
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    diagnostic_output_directory, finalize_diagnostic_outputs,
    validate_attested_analyzer_input,
)


SUMMARY_FILENAME = "rta_parameter_sensitivity_summary.csv"
BY_SWEEP_FILENAME = "rta_parameter_sensitivity_by_sweep.csv"
BY_VALUE_FILENAME = "rta_parameter_sensitivity_by_value.csv"
BY_CONFIG_FILENAME = "rta_parameter_sensitivity_by_config.csv"

PROVEN_UTILIZATION_PLOT = "proven_ratio_vs_utilization.png"
PROVEN_E0_PLOT = "proven_ratio_vs_e0.png"
PROVEN_YIELD_PLOT = "proven_yield_vs_parameter.png"
RUNTIME_UTILIZATION_PLOT = "runtime_vs_utilization.png"
RUNTIME_E0_PLOT = "runtime_vs_e0.png"
TIMEOUT_RATE_PLOT = "timeout_rate_vs_parameter.png"

SUMMARY_COLUMNS = [
    "group_key",
    "group_value",
    "sweep_name",
    "sweep_parameter",
    "sweep_value",
    "config_id",
    "total_rows",
    "attempted_count",
    "completed_count",
    "timeout_count",
    "error_count",
    "unattempted_count",
    "proven_count",
    "unproven_count",
    "rta_proven_ratio",
    "rta_proven_yield",
    "conditional_denominator_count",
    "conditional_proven_ratio",
    "timeout_rate",
    "error_rate",
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
    "sweep_name",
    "sweep_parameter",
    "sweep_value",
    "config_id",
    "rta_status",
    "rta_error",
    "result_status",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_timed_out",
    "rta_profile_task_time_sum_sec",
}

TRUE_VALUES = {"1", "true", "yes", "y"}
VALID_SWEEPS = {"utilization", "e0"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze v20.4 RTA-only utilization and release-time energy "
            "lower-bound E0 sensitivity results. No scheduler simulation "
            "metrics are computed."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest")
    parser.add_argument(
        "--allow-unattested-diagnostic-input", action="store_true"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "validate input existence and required schema; timeout/error rows "
            "remain valid sensitivity observations"
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
    return str(value).strip().lower() not in {"", "none", "nan", "null"}


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
            "config_error",
            "task_generation_error",
            "rta_timeout",
            "timeout",
        }
        or "error" in status
        or "exception" in status
        or status.endswith("_failed")
    )


def load_results(input_path) -> pd.DataFrame:
    path = Path(input_path)
    frame = pd.read_csv(path, keep_default_na=False)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if (
        "rta_schedulable" not in frame.columns
        and "rta_proven" not in frame.columns
    ):
        missing.append("rta_schedulable or rta_proven")
    if missing:
        raise ValueError(
            "parameter sensitivity results are missing fields: {}".format(
                ", ".join(missing)
            )
        )

    sweep_values = pd.to_numeric(frame["sweep_value"], errors="coerce")
    if len(frame) and sweep_values.isna().any():
        raise ValueError("sweep_value contains non-numeric values")
    frame["sweep_value"] = sweep_values
    if len(frame) and frame["config_id"].map(_nonempty).eq(False).any():
        raise ValueError("config_id must be non-empty")
    if len(frame) and frame["sweep_name"].map(_nonempty).eq(False).any():
        raise ValueError("sweep_name must be non-empty")
    parameters = frame["sweep_parameter"].map(_normalized)
    if len(frame) and not parameters.isin(VALID_SWEEPS).all():
        invalid = sorted(set(parameters) - VALID_SWEEPS)
        raise ValueError(
            "unsupported sweep_parameter values: {}".format(
                ", ".join(invalid)
            )
        )
    frame["sweep_parameter"] = parameters

    frame["_attempted"] = frame["rta_attempted"].map(_truthy)
    frame["_timeout"] = (
        frame["_attempted"] & frame["rta_timed_out"].map(_truthy)
    )
    error_text = frame["rta_error"].map(_nonempty)
    rta_error_status = frame["rta_status"].map(_error_status)
    result_error_status = frame["result_status"].map(_error_status)
    frame["_error"] = (
        frame["_attempted"]
        & ~frame["_timeout"]
        & (error_text | rta_error_status | result_error_status)
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
    frame["_runtime_sample"] = frame["_completed"] & frame["_runtime"].notna()
    frame["_profile_runtime"] = _finite_nonnegative(
        frame["rta_profile_task_time_sum_sec"]
    )
    frame["_profile_runtime_sample"] = frame["_profile_runtime"].notna()
    if "assumption_eligible" in frame.columns:
        frame["_assumption_eligible"] = frame[
            "assumption_eligible"
        ].map(_truthy)
    return frame


def _ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else math.nan


def _stats(values: pd.Series, prefix: str) -> dict:
    finite = _finite_nonnegative(values).dropna()
    result = {
        "{}_sample_count".format(prefix): int(len(finite)),
        "{}_mean_sec".format(prefix): (
            float(finite.mean()) if len(finite) else math.nan
        ),
        "{}_median_sec".format(prefix): (
            float(finite.median()) if len(finite) else math.nan
        ),
        "{}_p95_sec".format(prefix): (
            float(finite.quantile(0.95)) if len(finite) else math.nan
        ),
        "{}_max_sec".format(prefix): (
            float(finite.max()) if len(finite) else math.nan
        ),
    }
    if prefix == "runtime":
        result.update({
            "runtime_p75_sec": (
                float(finite.quantile(0.75)) if len(finite) else math.nan
            ),
            "runtime_p90_sec": (
                float(finite.quantile(0.90)) if len(finite) else math.nan
            ),
        })
    return result


def _single_value(group: pd.DataFrame, column: str):
    values = group[column].drop_duplicates().tolist()
    return values[0] if len(values) == 1 else "all"


def summarize_group(
    group: pd.DataFrame,
    group_key: str,
    group_value,
    input_path: Path,
    metadata=None,
) -> dict:
    attempted = int(group["_attempted"].sum())
    completed = int(group["_completed"].sum())
    timeout = int(group["_timeout"].sum())
    errors = int(group["_error"].sum())
    proven = int((group["_completed"] & group["_proven"]).sum())
    unproven = int((group["_completed"] & ~group["_proven"]).sum())
    if "_assumption_eligible" in group.columns:
        eligible_completed = group["_completed"] & group[
            "_assumption_eligible"
        ]
        conditional_denominator = int(eligible_completed.sum())
        conditional_proven = int(
            (eligible_completed & group["_proven"]).sum()
        )
        conditional_ratio = _ratio(
            conditional_proven, conditional_denominator
        )
    else:
        conditional_denominator = math.nan
        conditional_ratio = math.nan

    values = {
        "sweep_name": _single_value(group, "sweep_name"),
        "sweep_parameter": _single_value(group, "sweep_parameter"),
        "sweep_value": _single_value(group, "sweep_value"),
        "config_id": _single_value(group, "config_id"),
    }
    if metadata:
        values.update(metadata)
    row = {
        "group_key": group_key,
        "group_value": group_value,
        **values,
        "total_rows": int(len(group)),
        "attempted_count": attempted,
        "completed_count": completed,
        "timeout_count": timeout,
        "error_count": errors,
        "unattempted_count": int(len(group) - attempted),
        "proven_count": proven,
        "unproven_count": unproven,
        "rta_proven_ratio": _ratio(proven, completed),
        "rta_proven_yield": _ratio(proven, attempted),
        "conditional_denominator_count": conditional_denominator,
        "conditional_proven_ratio": conditional_ratio,
        "timeout_rate": _ratio(timeout, attempted),
        "error_rate": _ratio(errors, attempted),
        "input_file": str(input_path),
    }
    runtime_values = group.loc[group["_runtime_sample"], "_runtime"]
    profile_values = group.loc[
        group["_profile_runtime_sample"], "_profile_runtime"
    ]
    row.update(_stats(runtime_values, "runtime"))
    row.update(_stats(profile_values, "profile_runtime"))
    return {column: row.get(column, math.nan) for column in SUMMARY_COLUMNS}


def build_overall(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    row = summarize_group(
        frame,
        "overall",
        "all",
        input_path,
        {
            "sweep_name": "all",
            "sweep_parameter": "all",
            "sweep_value": "all",
            "config_id": "all",
        },
    )
    return pd.DataFrame([row], columns=SUMMARY_COLUMNS)


def build_by_sweep(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = []
    for parameter, group in frame.groupby("sweep_parameter", sort=True):
        rows.append(summarize_group(
            group,
            "sweep_parameter",
            parameter,
            input_path,
            {
                "sweep_parameter": parameter,
                "sweep_value": "all",
                "config_id": "all",
            },
        ))
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_value(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = []
    for (parameter, value), group in frame.groupby(
        ["sweep_parameter", "sweep_value"], sort=True
    ):
        rows.append(summarize_group(
            group,
            "sweep_value",
            value,
            input_path,
            {
                "sweep_parameter": parameter,
                "sweep_value": value,
                "config_id": "all",
            },
        ))
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_config(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = [
        summarize_group(
            group,
            "config_id",
            config_id,
            input_path,
            {"config_id": config_id},
        )
        for config_id, group in frame.groupby("config_id", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _sweep_rows(frame: pd.DataFrame, parameter: str) -> pd.DataFrame:
    selected = frame.loc[
        frame["sweep_parameter"].map(_normalized) == parameter
    ].copy()
    selected["_plot_x"] = pd.to_numeric(
        selected["sweep_value"], errors="coerce"
    )
    return selected.sort_values("_plot_x")


def _no_data(axis, message: str) -> None:
    axis.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        transform=axis.transAxes,
    )


def _plot_ratio(
    by_value: pd.DataFrame,
    parameter: str,
    column: str,
    output: Path,
    x_label: str,
    y_label: str,
    title: str,
) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    selected = _sweep_rows(by_value, parameter)
    x = pd.to_numeric(selected.get("_plot_x"), errors="coerce")
    y = pd.to_numeric(selected.get(column), errors="coerce")
    mask = x.notna() & y.notna()
    if mask.any():
        axis.plot(x[mask], y[mask], marker="o")
    else:
        _no_data(axis, "No {} sensitivity data".format(parameter))
    axis.set_xlabel(x_label)
    axis.set_ylabel(y_label)
    axis.set_ylim(0, 1.02)
    axis.set_title(title)
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_runtime(
    by_value: pd.DataFrame,
    parameter: str,
    output: Path,
    x_label: str,
    title: str,
) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    selected = _sweep_rows(by_value, parameter)
    x = pd.to_numeric(selected.get("_plot_x"), errors="coerce")
    median = pd.to_numeric(
        selected.get("runtime_median_sec"), errors="coerce"
    )
    p95 = pd.to_numeric(selected.get("runtime_p95_sec"), errors="coerce")
    median_mask = x.notna() & median.notna()
    p95_mask = x.notna() & p95.notna()
    plotted = False
    if median_mask.any():
        axis.plot(x[median_mask], median[median_mask], marker="o", label="median")
        plotted = True
    if p95_mask.any():
        axis.plot(
            x[p95_mask],
            p95[p95_mask],
            marker="s",
            linestyle="--",
            label="p95",
        )
        plotted = True
    if plotted:
        axis.legend()
    else:
        _no_data(axis, "No canonical runtime data")
    axis.set_xlabel(x_label)
    axis.set_ylabel("RTA subprocess wall-clock runtime (sec)")
    axis.set_title(title)
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_multi_sweep(
    by_value: pd.DataFrame,
    column: str,
    output: Path,
    y_label: str,
    title: str,
) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    plotted = False
    for parameter, marker in (("utilization", "o"), ("e0", "s")):
        selected = _sweep_rows(by_value, parameter)
        x = pd.to_numeric(selected.get("_plot_x"), errors="coerce")
        y = pd.to_numeric(selected.get(column), errors="coerce")
        mask = x.notna() & y.notna()
        if mask.any():
            axis.plot(
                x[mask],
                y[mask],
                marker=marker,
                label=parameter,
            )
            plotted = True
    if plotted:
        axis.legend()
    else:
        _no_data(axis, "No parameter sensitivity data")
    axis.set_xlabel("Sweep parameter value")
    axis.set_ylabel(y_label)
    axis.set_ylim(0, 1.02)
    axis.set_title(title)
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def analyze(input_path, output_dir, manifest_path=None):
    input_path = Path(input_path)
    if manifest_path is not None and not Path(manifest_path).is_file():
        raise FileNotFoundError(
            "manifest does not exist: {}".format(manifest_path)
        )
    frame = load_results(input_path)
    output = Path(output_dir)
    plots = output / "plots"
    output.mkdir(parents=True, exist_ok=True)
    plots.mkdir(parents=True, exist_ok=True)

    overall = build_overall(frame, input_path)
    by_sweep = build_by_sweep(frame, input_path)
    by_value = build_by_value(frame, input_path)
    by_config = build_by_config(frame, input_path)

    overall.to_csv(output / SUMMARY_FILENAME, index=False)
    by_sweep.to_csv(output / BY_SWEEP_FILENAME, index=False)
    by_value.to_csv(output / BY_VALUE_FILENAME, index=False)
    by_config.to_csv(output / BY_CONFIG_FILENAME, index=False)

    _plot_ratio(
        by_value,
        "utilization",
        "rta_proven_ratio",
        plots / PROVEN_UTILIZATION_PLOT,
        "Normalized utilization",
        "RTA proven ratio",
        "RTA proven ratio by utilization",
    )
    _plot_ratio(
        by_value,
        "e0",
        "rta_proven_ratio",
        plots / PROVEN_E0_PLOT,
        "Conditional initial-energy lower bound E0 (J)",
        "RTA proven ratio",
        "RTA proven ratio by E0",
    )
    _plot_multi_sweep(
        by_value,
        "rta_proven_yield",
        plots / PROVEN_YIELD_PLOT,
        "RTA proven yield",
        "RTA proven yield by parameter",
    )
    _plot_runtime(
        by_value,
        "utilization",
        plots / RUNTIME_UTILIZATION_PLOT,
        "Normalized utilization",
        "RTA runtime by utilization",
    )
    _plot_runtime(
        by_value,
        "e0",
        plots / RUNTIME_E0_PLOT,
        "Conditional initial-energy lower bound E0 (J)",
        "RTA runtime by E0",
    )
    _plot_multi_sweep(
        by_value,
        "timeout_rate",
        plots / TIMEOUT_RATE_PLOT,
        "RTA timeout rate",
        "RTA timeout rate by parameter",
    )
    return overall, by_sweep, by_value, by_config


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_dir = Path(args.output_dir)
        if args.allow_unattested_diagnostic_input:
            output_dir = diagnostic_output_directory(output_dir)
        else:
            validate_attested_analyzer_input(args.input)
        analyze(args.input, output_dir, args.manifest)
        if args.allow_unattested_diagnostic_input:
            finalize_diagnostic_outputs(output_dir)
    except (OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
