"""Aggregation for EXT-2 trace-driven simulations."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict

from .result_writer import read_csv, write_csv


SUMMARY_COLUMNS = (
    "scheduler_id", "trace_id", "trace_scale", "requested_denominator",
    "valid_terminal_denominator", "sufficient_observation_denominator",
    "pass_count", "deadline_miss_count", "horizon_insufficient_count",
    "timeout_count", "internal_error_count", "pass_ratio_requested",
    "pass_ratio_valid",
)
PLOT_COLUMNS = (
    "plot", "scheduler_id", "trace_id", "trace_scale", "request_id",
    "category", "x", "y",
)


def _ratio(numerator: int, denominator: int) -> Any:
    return numerator / denominator if denominator else "UNAVAILABLE"


def aggregate_ext2(root: Path) -> Dict[str, int]:
    requests = read_csv(root / "simulation_requests.csv")
    results = read_csv(root / "simulation_results.csv")
    keys = sorted({
        (row["scheduler_id"], row["trace_id"], row["trace_scale"])
        for row in requests
    })
    summaries = []
    plots = []
    for scheduler_id, trace_id, scale in keys:
        requested = [row for row in requests if (
            row["scheduler_id"], row["trace_id"], row["trace_scale"]
        ) == (scheduler_id, trace_id, scale)]
        terminal = [row for row in results if (
            row["scheduler_id"], row["trace_id"], row["trace_scale"]
        ) == (scheduler_id, trace_id, scale)]
        status = Counter(row["status"] for row in terminal)
        valid = status["SIM_PASS_OBSERVED"] + status["SIM_DEADLINE_MISS"]
        summaries.append({
            "scheduler_id": scheduler_id, "trace_id": trace_id,
            "trace_scale": scale, "requested_denominator": len(requested),
            "valid_terminal_denominator": valid,
            "sufficient_observation_denominator": valid,
            "pass_count": status["SIM_PASS_OBSERVED"],
            "deadline_miss_count": status["SIM_DEADLINE_MISS"],
            "horizon_insufficient_count": status["SIM_HORIZON_INSUFFICIENT"],
            "timeout_count": status["SIM_RUNTIME_TIMEOUT"],
            "internal_error_count": status["SIM_INTERNAL_ERROR"],
            "pass_ratio_requested": _ratio(status["SIM_PASS_OBSERVED"], len(requested)),
            "pass_ratio_valid": _ratio(status["SIM_PASS_OBSERVED"], valid),
        })
        for row in terminal:
            for plot, field in (
                ("maximum_response", "maximum_observed_response_time"),
                ("energy_blocked_ticks", "energy_blocked_ticks"),
                ("runtime", "runtime_seconds"),
            ):
                plots.append({
                    "plot": plot, "scheduler_id": scheduler_id,
                    "trace_id": trace_id, "trace_scale": scale,
                    "request_id": row["request_id"], "category": row["status"],
                    "x": row.get(field, "UNAVAILABLE"), "y": 1,
                })
            try:
                trajectory = __import__("json").loads(row.get("battery_trajectory_json", "[]"))
            except (ValueError, TypeError):
                trajectory = []
            for sample in trajectory:
                plots.append({
                    "plot": "battery_trajectory", "scheduler_id": scheduler_id,
                    "trace_id": trace_id, "trace_scale": scale,
                    "request_id": row["request_id"], "category": row["status"],
                    "x": sample["time"], "y": sample["energy_j"],
                })
    write_csv(root / "trace_summary.csv", SUMMARY_COLUMNS, summaries)
    write_csv(root / "ext2_plot_data.csv", PLOT_COLUMNS, plots)
    return {"summary_rows": len(summaries), "plot_rows": len(plots)}
