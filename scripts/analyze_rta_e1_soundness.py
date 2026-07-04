#!/usr/bin/env python3
"""Analyze E1 ASAP-BLOCK RTA soundness rows from per_taskset_results.csv.

The observed/bound scatter is intentionally taskset-level: the observed value is
the maximum completed-job response time seen in the taskset trace, and the RTA
bound is the taskset aggregate maximum response bound reported by the RTA path.
It is not a per-task exact-bound comparison.
"""

import argparse
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


SUMMARY_FILENAME = "e1_summary.csv"
SUMMARY_BY_CONFIG_FILENAME = "e1_summary_by_config.csv"
CONFUSION_FILENAME = "confusion_matrix.csv"
VIOLATIONS_FILENAME = "soundness_violations.csv"
OBSERVED_VS_BOUND_FILENAME = "observed_vs_bound.csv"
CONFUSION_PLOT = "e1_confusion_matrix.png"
OBSERVED_VS_BOUND_PLOT = "e1_observed_vs_bound.png"
ASAP_BLOCK_ALGORITHM = "gpfp_asap_block"

TRUE_VALUES = {"1", "true", "yes", "y"}
TIMEOUT_STATUSES = {"timeout", "simulation_timeout"}
INFRASTRUCTURE_STATUSES = {
    "simulation_error",
    "build_error",
    "config_error",
    "trace_parse_error",
    "missing_binary",
    "unknown_error",
    "exception",
    "error",
}
PRIORITY_VIOLATION_COLUMNS = [
    "config_id",
    "taskset_id",
    "taskset_seed",
    "rta_version",
    "status",
    "rta_schedulable",
    "sim_schedulable",
    "soundness_violation",
    "observed_max_response_time",
    "rta_response_bound",
    "first_missed_task",
    "first_missed_job_release",
    "first_missed_deadline",
    "deadline_miss_time",
    "soundness_excluded_reason",
]
OBSERVED_COLUMNS = [
    "config_id",
    "taskset_id",
    "taskset_seed",
    "rta_version",
    "observed_max_response_time",
    "rta_response_bound",
    "pessimism_ratio",
]
SUMMARY_BY_CONFIG_COLUMNS = [
    "config_id",
    "total_rows",
    "valid_count",
    "excluded_count",
    "excluded_timeout_count",
    "excluded_infrastructure_count",
    "rta_pass_sim_pass_count",
    "rta_fail_sim_pass_count",
    "rta_fail_sim_fail_count",
    "rta_pass_sim_fail_count",
    "soundness_violation_count",
    "consistency_warning_count",
    "rta_version_values",
    "input_file",
    "observed_vs_bound_rows",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze E1 RTA soundness and RTA-vs-simulation confusion matrix "
            "from acceptance_ratio_test.py per_taskset_results.csv. "
            "Observed-vs-bound output uses taskset-level aggregate values: "
            "observed_max_response_time is the maximum completed-job response "
            "observed in the taskset, and rta_response_bound is the taskset "
            "aggregate max RTA bound."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "exit nonzero if any soundness violation or consistency warning "
            "is present"
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


def _column(frame: pd.DataFrame, name: str, default="") -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series([default] * len(frame), index=frame.index)


def _bool_column(frame: pd.DataFrame, name: str, default=False) -> pd.Series:
    if name not in frame.columns:
        return pd.Series([bool(default)] * len(frame), index=frame.index)
    return frame[name].map(_truthy)


def _select_e1_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the rows that actually carry ASAP-BLOCK RTA observations."""
    if "rta_enabled" in frame.columns:
        return frame.loc[frame["rta_enabled"].map(_truthy)].copy()
    if "algorithm" in frame.columns:
        return frame.loc[
            frame["algorithm"].astype(str).str.strip() == ASAP_BLOCK_ALGORITHM
        ].copy()
    return frame.copy()


def _status_series(frame: pd.DataFrame) -> pd.Series:
    if "status" in frame.columns:
        return frame["status"].map(_normalized)
    if "simulation_status" in frame.columns:
        return frame["simulation_status"].map(_normalized)
    return pd.Series([""] * len(frame), index=frame.index)


def _classify_rows(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    status = _status_series(result)
    excluded_reason = _column(result, "soundness_excluded_reason").map(_normalized)
    timeout_flag = _bool_column(result, "timeout", default=False)

    result["_excluded_timeout"] = (
        status.isin(TIMEOUT_STATUSES)
        | timeout_flag
        | excluded_reason.isin(TIMEOUT_STATUSES)
    )
    result["_excluded_infrastructure"] = (
        status.isin(INFRASTRUCTURE_STATUSES)
        | (
            excluded_reason.map(_nonempty)
            & ~excluded_reason.isin(TIMEOUT_STATUSES)
        )
    )
    if "soundness_valid" in result.columns:
        base_valid = result["soundness_valid"].map(_truthy)
    else:
        base_valid = ~(
            result["_excluded_timeout"] | result["_excluded_infrastructure"]
        )
    result["_valid_sample"] = (
        base_valid
        & ~result["_excluded_timeout"]
        & ~result["_excluded_infrastructure"]
    )

    if "rta_schedulable" in result.columns:
        result["_rta_pass"] = result["rta_schedulable"].map(_truthy)
    else:
        result["_rta_pass"] = _bool_column(result, "rta_proven", default=False)

    if "sim_schedulable" in result.columns:
        result["_sim_pass"] = result["sim_schedulable"].map(_truthy)
    else:
        accepted = _bool_column(result, "accepted", default=False)
        result["_sim_pass"] = accepted | (status == "accepted")

    result["_soundness_violation"] = _bool_column(
        result, "soundness_violation", default=False
    )
    result["_true_soundness_violation"] = (
        result["_valid_sample"]
        & result["_rta_pass"]
        & ~result["_sim_pass"]
        & result["_soundness_violation"]
    )
    result["_consistency_warning"] = (
        result["_valid_sample"]
        & result["_rta_pass"]
        & ~result["_sim_pass"]
        & ~result["_soundness_violation"]
    )
    return result


def _format_rta_versions(frame: pd.DataFrame) -> str:
    if "rta_version" not in frame.columns or frame.empty:
        return ""
    values = sorted(
        value for value in frame["rta_version"].astype(str).str.strip().unique()
        if value
    )
    return " ".join(values)


def _summary_row(frame: pd.DataFrame, input_path: Path, config_id="") -> dict:
    valid = frame["_valid_sample"]
    rta_pass = frame["_rta_pass"]
    sim_pass = frame["_sim_pass"]
    row = {
        "total_rows": int(len(frame)),
        "valid_count": int(valid.sum()),
        "excluded_count": int((~valid).sum()),
        "excluded_timeout_count": int((~valid & frame["_excluded_timeout"]).sum()),
        "excluded_infrastructure_count": int(
            (~valid & frame["_excluded_infrastructure"]).sum()
        ),
        "rta_pass_sim_pass_count": int((valid & rta_pass & sim_pass).sum()),
        "rta_fail_sim_pass_count": int((valid & ~rta_pass & sim_pass).sum()),
        "rta_fail_sim_fail_count": int((valid & ~rta_pass & ~sim_pass).sum()),
        "rta_pass_sim_fail_count": int((valid & rta_pass & ~sim_pass).sum()),
        "soundness_violation_count": int(
            frame["_true_soundness_violation"].sum()
        ),
        "consistency_warning_count": int(frame["_consistency_warning"].sum()),
        "rta_version_values": _format_rta_versions(frame),
        "input_file": str(input_path),
        "observed_vs_bound_rows": int(
            (valid & rta_pass & sim_pass).sum()
        ),
    }
    if config_id != "":
        row["config_id"] = config_id
    return row


def build_summary(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    return pd.DataFrame([_summary_row(frame, input_path)])


def build_summary_by_config(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    if "config_id" not in frame.columns:
        return pd.DataFrame(columns=SUMMARY_BY_CONFIG_COLUMNS)
    grouped_frame = frame.loc[frame["config_id"].map(_nonempty)]
    rows = []
    for config_id, group in grouped_frame.groupby("config_id", sort=True):
        rows.append(_summary_row(group, input_path, str(config_id)))
    return pd.DataFrame(rows, columns=SUMMARY_BY_CONFIG_COLUMNS)


def build_confusion_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame["_valid_sample"]
    rta_pass = frame["_rta_pass"]
    sim_pass = frame["_sim_pass"]
    rows = [
        ("rta_pass", "sim_pass", valid & rta_pass & sim_pass),
        ("rta_fail", "sim_pass", valid & ~rta_pass & sim_pass),
        ("rta_fail", "sim_fail", valid & ~rta_pass & ~sim_pass),
        ("rta_pass", "sim_fail", valid & rta_pass & ~sim_pass),
    ]
    return pd.DataFrame([
        {"rta_result": rta_result, "sim_result": sim_result, "count": int(mask.sum())}
        for rta_result, sim_result, mask in rows
    ])


def _ensure_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result.columns:
            result[column] = ""
    return result


def build_violations(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame.loc[frame["_true_soundness_violation"]].copy()
    selected = _ensure_columns(selected, PRIORITY_VIOLATION_COLUMNS)
    helper_columns = [column for column in selected.columns if column.startswith("_")]
    selected = selected.drop(columns=helper_columns, errors="ignore")
    ordered = PRIORITY_VIOLATION_COLUMNS + [
        column for column in selected.columns
        if column not in PRIORITY_VIOLATION_COLUMNS
    ]
    return selected.loc[:, ordered]


def _first_existing(frame: pd.DataFrame, names: Sequence[str]) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series([""] * len(frame), index=frame.index)


def build_observed_vs_bound(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame.loc[
        frame["_valid_sample"] & frame["_rta_pass"] & frame["_sim_pass"]
    ].copy()
    observed_source = _first_existing(
        selected, ["observed_max_response_time", "simulated_response_time"]
    )
    bound_source = _first_existing(
        selected, ["rta_response_bound", "rta_response_time_bound"]
    )
    selected = _ensure_columns(selected, OBSERVED_COLUMNS[:-1])
    selected["observed_max_response_time"] = observed_source
    selected["rta_response_bound"] = bound_source
    observed = pd.to_numeric(
        selected["observed_max_response_time"], errors="coerce"
    )
    bound = pd.to_numeric(selected["rta_response_bound"], errors="coerce")
    ratio = bound / observed
    valid_ratio = (
        observed.notna()
        & bound.notna()
        & observed.map(lambda value: math.isfinite(value) and value > 0)
        & bound.map(lambda value: math.isfinite(value))
    )
    selected["pessimism_ratio"] = ""
    selected.loc[valid_ratio, "pessimism_ratio"] = ratio.loc[valid_ratio]
    return selected.loc[:, OBSERVED_COLUMNS]


def plot_confusion(confusion: pd.DataFrame, output: Path, excluded_count: int) -> None:
    plot_data = {
        (row["rta_result"], row["sim_result"]): int(row["count"])
        for _, row in confusion.iterrows()
    }
    matrix = [
        [
            plot_data.get(("rta_pass", "sim_pass"), 0),
            plot_data.get(("rta_pass", "sim_fail"), 0),
        ],
        [
            plot_data.get(("rta_fail", "sim_pass"), 0),
            plot_data.get(("rta_fail", "sim_fail"), 0),
        ],
    ]
    figure, axis = plt.subplots(figsize=(5.6, 4.6))
    image = axis.imshow(matrix, cmap="Blues")
    axis.set_xticks([0, 1])
    axis.set_xticklabels(["sim pass", "sim fail"])
    axis.set_yticks([0, 1])
    axis.set_yticklabels(["RTA pass", "RTA fail"])
    axis.set_title("E1 Confusion Matrix\ninvalid/excluded rows not included")
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            axis.text(x, y, str(value), ha="center", va="center", color="black")
    axis.set_xlabel("Simulation result")
    axis.set_ylabel("RTA result")
    figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    figure.text(0.5, 0.01, "Excluded rows: {}".format(excluded_count), ha="center")
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(output, dpi=160)
    plt.close(figure)


def plot_observed_vs_bound(data: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(5.8, 4.8))
    observed = pd.to_numeric(data["observed_max_response_time"], errors="coerce")
    bound = pd.to_numeric(data["rta_response_bound"], errors="coerce")
    mask = (
        observed.notna()
        & bound.notna()
        & observed.map(lambda value: math.isfinite(value) and value > 0)
        & bound.map(lambda value: math.isfinite(value))
    )
    if mask.any():
        axis.scatter(observed[mask], bound[mask], alpha=0.75)
        minimum = min(float(observed[mask].min()), float(bound[mask].min()))
        maximum = max(float(observed[mask].max()), float(bound[mask].max()))
        if minimum == maximum:
            minimum = 0
            maximum = maximum + 1
        axis.plot([minimum, maximum], [minimum, maximum], color="black", linewidth=1)
        axis.set_xlim(left=min(0, minimum), right=maximum * 1.05)
        axis.set_ylim(bottom=min(0, minimum), top=maximum * 1.05)
    else:
        axis.text(
            0.5, 0.5, "No valid RTA-pass / simulation-pass rows to plot",
            ha="center", va="center", transform=axis.transAxes,
        )
    axis.set_xlabel("observed_max_response_time")
    axis.set_ylabel("rta_response_bound")
    axis.set_title("E1 Observed vs RTA Bound\nTaskset-level aggregate values")
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def analyze(input_path, output_dir):
    input_path = Path(input_path)
    output = Path(output_dir)
    plots = output / "plots"
    output.mkdir(parents=True, exist_ok=True)
    plots.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(input_path, keep_default_na=False)
    frame = _classify_rows(_select_e1_rows(raw))

    summary = build_summary(frame, input_path)
    summary.to_csv(output / SUMMARY_FILENAME, index=False)

    by_config = build_summary_by_config(frame, input_path)
    by_config.to_csv(output / SUMMARY_BY_CONFIG_FILENAME, index=False)

    confusion = build_confusion_matrix(frame)
    confusion.to_csv(output / CONFUSION_FILENAME, index=False)

    violations = build_violations(frame)
    violations.to_csv(output / VIOLATIONS_FILENAME, index=False)

    observed_vs_bound = build_observed_vs_bound(frame)
    observed_vs_bound.to_csv(output / OBSERVED_VS_BOUND_FILENAME, index=False)

    excluded_count = int(summary.iloc[0]["excluded_count"]) if len(summary) else 0
    plot_confusion(confusion, plots / CONFUSION_PLOT, excluded_count)
    plot_observed_vs_bound(observed_vs_bound, plots / OBSERVED_VS_BOUND_PLOT)
    return summary, confusion, violations, observed_vs_bound


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    summary, _, _, _ = analyze(args.input, args.output_dir)
    if args.strict and len(summary):
        row = summary.iloc[0]
        if (
            int(row["soundness_violation_count"]) > 0
            or int(row["consistency_warning_count"]) > 0
        ):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
