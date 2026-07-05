#!/usr/bin/env python3
"""Analyze E3 RTA ablation/refinement endpoint result rows."""

import argparse
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


SUMMARY_FILENAME = "rta_ablation_summary.csv"
BY_VARIANT_FILENAME = "rta_ablation_by_variant.csv"
BY_UTILIZATION_FILENAME = "rta_ablation_by_utilization.csv"
BY_VARIANT_UTILIZATION_FILENAME = "rta_ablation_by_variant_utilization.csv"
BY_CONFIG_FILENAME = "rta_ablation_by_config.csv"

RTA_PASS_VARIANT_PLOT = "rta_pass_ratio_by_variant.png"
RTA_PASS_VARIANT_UTILIZATION_PLOT = "rta_pass_ratio_by_variant_utilization.png"
PROOF_CLAIM_VARIANT_PLOT = "proof_claim_pass_ratio_by_variant.png"
RUNTIME_VARIANT_PLOT = "runtime_by_variant.png"
TIMEOUT_VARIANT_PLOT = "timeout_rate_by_variant.png"
BOUND_VARIANT_PLOT = "bound_by_variant.png"

SUMMARY_COLUMNS = [
    "group_key",
    "group_value",
    "variant_name",
    "variant_label",
    "variant_safety_label",
    "variant_is_default",
    "variant_is_experimental",
    "proof_claim_eligible",
    "diagnostic_only",
    "normalized_utilization",
    "config_id",
    "total_rows",
    "attempted_count",
    "completed_count",
    "timeout_count",
    "error_count",
    "unattempted_count",
    "rta_pass_count",
    "rta_fail_count",
    "rta_pass_ratio",
    "rta_pass_yield",
    "proof_claim_eligible_completed_count",
    "proof_claim_pass_count",
    "proof_claim_pass_ratio",
    "timeout_rate",
    "error_rate",
    "runtime_sample_count",
    "runtime_mean_sec",
    "runtime_median_sec",
    "runtime_p75_sec",
    "runtime_p90_sec",
    "runtime_p95_sec",
    "runtime_max_sec",
    "bound_sample_count",
    "bound_mean",
    "bound_median",
    "bound_p75",
    "bound_p90",
    "bound_p95",
    "bound_max",
    "profile_runtime_sample_count",
    "profile_runtime_mean_sec",
    "profile_runtime_median_sec",
    "profile_runtime_p95_sec",
    "profile_runtime_max_sec",
    "input_file",
]

REQUIRED_COLUMNS = {
    "variant_name",
    "variant_label",
    "variant_safety_label",
    "variant_is_default",
    "variant_is_experimental",
    "proof_claim_eligible",
    "diagnostic_only",
    "normalized_utilization",
    "config_id",
    "rta_status",
    "rta_error",
    "result_status",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_timed_out",
    "rta_response_time_bound",
    "rta_response_bound",
    "rta_profile_task_time_sum_sec",
}

TRUE_VALUES = {"1", "true", "yes", "y"}
FORBIDDEN_OUTPUT_FIELDS = {
    "acceptance_ratio",
    "pessimism",
    "observed_max_response_time",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze RTA-only ablation/refinement endpoint results. This does "
            "not compute acceptance ratios, observed pessimism, or scheduler "
            "simulation metrics."
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
            "remain valid ablation observations"
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
            "version_mismatch",
        }
        or "error" in status
        or "exception" in status
        or status.endswith("_failed")
    )


def _single_value(group: pd.DataFrame, column: str):
    if column not in group.columns or group.empty:
        return "all"
    values = group[column].drop_duplicates().tolist()
    return values[0] if len(values) == 1 else "all"


def _ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else math.nan


def _stats(values: pd.Series, prefix: str, suffix: str = "_sec") -> dict:
    finite = _finite_nonnegative(values).dropna()
    names = {
        "sample_count": "{}_sample_count".format(prefix),
        "mean": "{}_mean{}".format(prefix, suffix),
        "median": "{}_median{}".format(prefix, suffix),
        "p75": "{}_p75{}".format(prefix, suffix),
        "p90": "{}_p90{}".format(prefix, suffix),
        "p95": "{}_p95{}".format(prefix, suffix),
        "max": "{}_max{}".format(prefix, suffix),
    }
    return {
        names["sample_count"]: int(len(finite)),
        names["mean"]: float(finite.mean()) if len(finite) else math.nan,
        names["median"]: float(finite.median()) if len(finite) else math.nan,
        names["p75"]: float(finite.quantile(0.75)) if len(finite) else math.nan,
        names["p90"]: float(finite.quantile(0.90)) if len(finite) else math.nan,
        names["p95"]: float(finite.quantile(0.95)) if len(finite) else math.nan,
        names["max"]: float(finite.max()) if len(finite) else math.nan,
    }


def _profile_stats(values: pd.Series) -> dict:
    finite = _finite_nonnegative(values).dropna()
    return {
        "profile_runtime_sample_count": int(len(finite)),
        "profile_runtime_mean_sec": (
            float(finite.mean()) if len(finite) else math.nan
        ),
        "profile_runtime_median_sec": (
            float(finite.median()) if len(finite) else math.nan
        ),
        "profile_runtime_p95_sec": (
            float(finite.quantile(0.95)) if len(finite) else math.nan
        ),
        "profile_runtime_max_sec": (
            float(finite.max()) if len(finite) else math.nan
        ),
    }


def load_results(input_path) -> pd.DataFrame:
    path = Path(input_path)
    frame = pd.read_csv(path, keep_default_na=False)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if "rta_schedulable" not in frame.columns and "rta_proven" not in frame.columns:
        missing.append("rta_schedulable or rta_proven")
    if missing:
        raise ValueError(
            "ablation results are missing fields: {}".format(
                ", ".join(missing)
            )
        )

    utilization = pd.to_numeric(
        frame["normalized_utilization"], errors="coerce"
    )
    if len(frame) and utilization.isna().any():
        raise ValueError("normalized_utilization contains non-numeric values")
    frame["normalized_utilization"] = utilization

    if len(frame) and frame["variant_name"].map(_nonempty).eq(False).any():
        raise ValueError("variant_name must be non-empty")
    if len(frame) and frame["config_id"].map(_nonempty).eq(False).any():
        raise ValueError("config_id must be non-empty")

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

    pass_column = (
        "rta_schedulable"
        if "rta_schedulable" in frame.columns
        else "rta_proven"
    )
    frame["_rta_pass"] = frame[pass_column].map(_truthy)
    frame["_proof_claim_eligible"] = frame["proof_claim_eligible"].map(_truthy)
    frame["_runtime"] = _finite_nonnegative(frame["rta_runtime_sec"])
    frame["_runtime_sample"] = frame["_completed"] & frame["_runtime"].notna()

    response_bound = _finite_nonnegative(frame["rta_response_bound"])
    response_time_bound = _finite_nonnegative(frame["rta_response_time_bound"])
    frame["_bound"] = response_bound.where(response_bound.notna(), response_time_bound)
    frame["_bound_sample"] = frame["_completed"] & frame["_bound"].notna()

    frame["_profile_runtime"] = _finite_nonnegative(
        frame["rta_profile_task_time_sum_sec"]
    )
    frame["_profile_runtime_sample"] = frame["_profile_runtime"].notna()
    return frame


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
    passes = int((group["_completed"] & group["_rta_pass"]).sum())
    fails = int(completed - passes)
    eligible_completed_mask = group["_completed"] & group["_proof_claim_eligible"]
    eligible_completed = int(eligible_completed_mask.sum())
    proof_pass = int((eligible_completed_mask & group["_rta_pass"]).sum())

    values = {
        "variant_name": _single_value(group, "variant_name"),
        "variant_label": _single_value(group, "variant_label"),
        "variant_safety_label": _single_value(group, "variant_safety_label"),
        "variant_is_default": _single_value(group, "variant_is_default"),
        "variant_is_experimental": _single_value(group, "variant_is_experimental"),
        "proof_claim_eligible": _single_value(group, "proof_claim_eligible"),
        "diagnostic_only": _single_value(group, "diagnostic_only"),
        "normalized_utilization": _single_value(group, "normalized_utilization"),
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
        "rta_pass_count": passes,
        "rta_fail_count": fails,
        "rta_pass_ratio": _ratio(passes, completed),
        "rta_pass_yield": _ratio(passes, attempted),
        "proof_claim_eligible_completed_count": eligible_completed,
        "proof_claim_pass_count": proof_pass,
        "proof_claim_pass_ratio": _ratio(proof_pass, eligible_completed),
        "timeout_rate": _ratio(timeout, attempted),
        "error_rate": _ratio(errors, attempted),
        "input_file": str(input_path),
    }
    row.update(_stats(group.loc[group["_runtime_sample"], "_runtime"], "runtime"))
    row.update(_stats(group.loc[group["_bound_sample"], "_bound"], "bound", suffix=""))
    row.update(_profile_stats(
        group.loc[group["_profile_runtime_sample"], "_profile_runtime"]
    ))
    return {column: row.get(column, math.nan) for column in SUMMARY_COLUMNS}


def build_overall(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    row = summarize_group(
        frame,
        "overall",
        "all",
        input_path,
        {
            "variant_name": "all",
            "variant_label": "all",
            "variant_safety_label": "all",
            "variant_is_default": "all",
            "variant_is_experimental": "all",
            "proof_claim_eligible": "all",
            "diagnostic_only": "all",
            "normalized_utilization": "all",
            "config_id": "all",
        },
    )
    return pd.DataFrame([row], columns=SUMMARY_COLUMNS)


def build_by_variant(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = [
        summarize_group(
            group,
            "variant_name",
            variant,
            input_path,
            {
                "variant_name": variant,
                "normalized_utilization": "all",
                "config_id": "all",
            },
        )
        for variant, group in frame.groupby("variant_name", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_utilization(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = [
        summarize_group(
            group,
            "normalized_utilization",
            utilization,
            input_path,
            {
                "variant_name": "all",
                "variant_label": "all",
                "variant_safety_label": "all",
                "variant_is_default": "all",
                "variant_is_experimental": "all",
                "proof_claim_eligible": "all",
                "diagnostic_only": "all",
                "normalized_utilization": utilization,
                "config_id": "all",
            },
        )
        for utilization, group in frame.groupby("normalized_utilization", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_variant_utilization(
    frame: pd.DataFrame, input_path: Path
) -> pd.DataFrame:
    rows = []
    for (variant, utilization), group in frame.groupby(
        ["variant_name", "normalized_utilization"], sort=True
    ):
        rows.append(summarize_group(
            group,
            "variant_utilization",
            "{}@{}".format(variant, utilization),
            input_path,
            {
                "variant_name": variant,
                "normalized_utilization": utilization,
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


def _no_data(axis, message: str) -> None:
    axis.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        transform=axis.transAxes,
    )


def _plot_bar(
    frame: pd.DataFrame,
    column: str,
    output: Path,
    y_label: str,
    title: str,
    no_data: str,
) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    labels = frame.get("variant_name", pd.Series(dtype=str)).astype(str)
    values = pd.to_numeric(frame.get(column), errors="coerce")
    mask = labels.map(_nonempty) & values.notna()
    if mask.any():
        positions = range(int(mask.sum()))
        axis.bar(list(positions), values[mask])
        axis.set_xticks(list(positions))
        axis.set_xticklabels(labels[mask], rotation=25, ha="right")
    else:
        _no_data(axis, no_data)
    axis.set_ylabel(y_label)
    axis.set_title(title)
    if "ratio" in column or "rate" in column:
        axis.set_ylim(0, 1.02)
    axis.grid(True, axis="y", linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_variant_utilization(by_variant_utilization: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    plotted = False
    for variant, group in by_variant_utilization.groupby("variant_name", sort=True):
        x = pd.to_numeric(group.get("normalized_utilization"), errors="coerce")
        y = pd.to_numeric(group.get("rta_pass_ratio"), errors="coerce")
        mask = x.notna() & y.notna()
        if mask.any():
            order = x[mask].sort_values().index
            axis.plot(
                x.loc[order],
                y.loc[order],
                marker="o",
                label=str(variant),
            )
            plotted = True
    if plotted:
        axis.legend()
    else:
        _no_data(axis, "No endpoint pass-ratio data")
    axis.set_xlabel("Normalized utilization")
    axis.set_ylabel("Endpoint RTA pass ratio")
    axis.set_ylim(0, 1.02)
    axis.set_title("Endpoint RTA pass ratio by variant and utilization")
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_runtime(by_variant: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    labels = by_variant.get("variant_name", pd.Series(dtype=str)).astype(str)
    median = pd.to_numeric(by_variant.get("runtime_median_sec"), errors="coerce")
    p95 = pd.to_numeric(by_variant.get("runtime_p95_sec"), errors="coerce")
    mask = labels.map(_nonempty) & (median.notna() | p95.notna())
    if mask.any():
        positions = list(range(int(mask.sum())))
        selected_labels = labels[mask]
        selected_median = median[mask]
        selected_p95 = p95[mask]
        width = 0.35
        axis.bar(
            [position - width / 2 for position in positions],
            selected_median,
            width=width,
            label="median",
        )
        axis.bar(
            [position + width / 2 for position in positions],
            selected_p95,
            width=width,
            label="p95",
        )
        axis.set_xticks(positions)
        axis.set_xticklabels(selected_labels, rotation=25, ha="right")
        axis.legend()
    else:
        _no_data(axis, "No canonical runtime data")
    axis.set_ylabel("RTA subprocess wall-clock runtime (sec)")
    axis.set_title("RTA wall-clock runtime by variant")
    axis.grid(True, axis="y", linestyle="--", alpha=0.35)
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
    by_variant = build_by_variant(frame, input_path)
    by_utilization = build_by_utilization(frame, input_path)
    by_variant_utilization = build_by_variant_utilization(frame, input_path)
    by_config = build_by_config(frame, input_path)

    overall.to_csv(output / SUMMARY_FILENAME, index=False)
    by_variant.to_csv(output / BY_VARIANT_FILENAME, index=False)
    by_utilization.to_csv(output / BY_UTILIZATION_FILENAME, index=False)
    by_variant_utilization.to_csv(
        output / BY_VARIANT_UTILIZATION_FILENAME, index=False
    )
    by_config.to_csv(output / BY_CONFIG_FILENAME, index=False)

    _plot_bar(
        by_variant,
        "rta_pass_ratio",
        plots / RTA_PASS_VARIANT_PLOT,
        "Endpoint RTA pass ratio",
        "Endpoint RTA pass ratio by variant",
        "No completed endpoint rows",
    )
    _plot_variant_utilization(
        by_variant_utilization,
        plots / RTA_PASS_VARIANT_UTILIZATION_PLOT,
    )
    proof_rows = by_variant.loc[
        by_variant["proof_claim_eligible"].map(_truthy)
    ].copy()
    _plot_bar(
        proof_rows,
        "proof_claim_pass_ratio",
        plots / PROOF_CLAIM_VARIANT_PLOT,
        "Proof-claim pass ratio",
        "Proof-claim pass ratio for eligible variants only",
        "No proof-claim eligible completed rows",
    )
    _plot_runtime(by_variant, plots / RUNTIME_VARIANT_PLOT)
    _plot_bar(
        by_variant,
        "timeout_rate",
        plots / TIMEOUT_VARIANT_PLOT,
        "RTA timeout rate",
        "RTA timeout rate by variant",
        "No attempted rows",
    )
    _plot_bar(
        by_variant,
        "bound_median",
        plots / BOUND_VARIANT_PLOT,
        "Median RTA response bound",
        "RTA response bound by variant",
        "No completed bound samples",
    )
    return overall, by_variant, by_utilization, by_variant_utilization, by_config


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
