"""Paired EXT-1 aggregation with explicit denominator scopes."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .result_writer import read_csv, write_csv
from .scheduler_pairing import paired_relation
from .scheduler_registry import scheduler_by_id


PAIRED_COLUMNS = (
    "paired_instance_id", "left_scheduler", "right_scheduler", "left_status",
    "right_status", "relation", "taskset_hash", "trace_hash",
)
SUMMARY_COLUMNS = (
    "scheduler_id", "timing_family", "mechanism", "requested_denominator",
    "terminal_denominator", "valid_terminal_denominator",
    "sufficient_observation_denominator", "pass_count", "deadline_miss_count",
    "horizon_insufficient_count", "timeout_count", "internal_error_count",
    "schedulability_ratio_requested", "schedulability_ratio_valid",
    "deadline_miss_ratio_requested", "deadline_miss_ratio_valid",
    "wins", "ties", "losses", "not_comparable",
)
PLOT_COLUMNS = (
    "plot", "scheduler_id", "timing_family", "mechanism",
    "paired_instance_id", "category", "x", "y",
)


VALID = {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}


def _ratio(numerator: int, denominator: int) -> Any:
    return numerator / denominator if denominator else "UNAVAILABLE"


def aggregate_ext1_rows(
    requests: Iterable[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    request_rows = list(requests)
    result_rows = list(results)
    by_request = {str(row["request_id"]): row for row in result_rows}
    by_instance: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in result_rows:
        by_instance[str(row["paired_instance_id"])][str(row["scheduler_id"])] = row
    paired = []
    scheduler_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    for pair_id, members in sorted(by_instance.items()):
        for left_id, right_id in combinations(sorted(members), 2):
            left, right = members[left_id], members[right_id]
            relation = paired_relation(left, right)
            paired.append({
                "paired_instance_id": pair_id,
                "left_scheduler": left_id, "right_scheduler": right_id,
                "left_status": left["status"], "right_status": right["status"],
                "relation": relation, "taskset_hash": left["taskset_hash"],
                "trace_hash": left["trace_hash"],
            })
            if relation == "LEFT_WIN":
                scheduler_counts[left_id]["wins"] += 1
                scheduler_counts[right_id]["losses"] += 1
            elif relation == "RIGHT_WIN":
                scheduler_counts[right_id]["wins"] += 1
                scheduler_counts[left_id]["losses"] += 1
            elif relation == "TIE":
                scheduler_counts[left_id]["ties"] += 1
                scheduler_counts[right_id]["ties"] += 1
            else:
                scheduler_counts[left_id]["not_comparable"] += 1
                scheduler_counts[right_id]["not_comparable"] += 1

    registry = scheduler_by_id()
    summaries = []
    plots = []
    for scheduler_id, registration in registry.items():
        requested = sum(str(row["scheduler_id"]) == scheduler_id for row in request_rows)
        terminal = [row for row in result_rows if str(row["scheduler_id"]) == scheduler_id]
        statuses = Counter(str(row["status"]) for row in terminal)
        valid = sum(statuses[value] for value in VALID)
        sufficient = statuses["SIM_PASS_OBSERVED"] + statuses["SIM_DEADLINE_MISS"]
        counts = scheduler_counts[scheduler_id]
        summaries.append({
            "scheduler_id": scheduler_id,
            "timing_family": registration.timing_family,
            "mechanism": registration.mechanism,
            "requested_denominator": requested,
            "terminal_denominator": len(terminal),
            "valid_terminal_denominator": valid,
            "sufficient_observation_denominator": sufficient,
            "pass_count": statuses["SIM_PASS_OBSERVED"],
            "deadline_miss_count": statuses["SIM_DEADLINE_MISS"],
            "horizon_insufficient_count": statuses["SIM_HORIZON_INSUFFICIENT"],
            "timeout_count": statuses["SIM_RUNTIME_TIMEOUT"],
            "internal_error_count": statuses["SIM_INTERNAL_ERROR"],
            "schedulability_ratio_requested": _ratio(statuses["SIM_PASS_OBSERVED"], requested),
            "schedulability_ratio_valid": _ratio(statuses["SIM_PASS_OBSERVED"], valid),
            "deadline_miss_ratio_requested": _ratio(statuses["SIM_DEADLINE_MISS"], requested),
            "deadline_miss_ratio_valid": _ratio(statuses["SIM_DEADLINE_MISS"], valid),
            "wins": counts["wins"], "ties": counts["ties"],
            "losses": counts["losses"],
            "not_comparable": counts["not_comparable"],
        })
        for row in terminal:
            for plot, field in (
                ("response_time", "maximum_observed_response_time"),
                ("energy_blocked_time", "energy_blocked_ticks"),
                ("processor_wait_time", "processor_wait_ticks"),
                ("runtime", "runtime_seconds"),
            ):
                value = row.get(field, "UNAVAILABLE")
                plots.append({
                    "plot": plot, "scheduler_id": scheduler_id,
                    "timing_family": registration.timing_family,
                    "mechanism": registration.mechanism,
                    "paired_instance_id": row["paired_instance_id"],
                    "category": row["status"], "x": value, "y": 1,
                })
    if len(by_request) != len(result_rows):
        raise RuntimeError("duplicate EXT-1 terminal result")
    return paired, summaries, plots


def aggregate_ext1(root: Path) -> Dict[str, int]:
    paired, summaries, plots = aggregate_ext1_rows(
        read_csv(root / "simulation_requests.csv"),
        read_csv(root / "simulation_results.csv"),
    )
    write_csv(root / "paired_scheduler_outcomes.csv", PAIRED_COLUMNS, paired)
    write_csv(root / "scheduler_summary.csv", SUMMARY_COLUMNS, summaries)
    write_csv(root / "ext1_plot_data.csv", PLOT_COLUMNS, plots)
    return {"paired_rows": len(paired), "scheduler_rows": len(summaries), "plot_rows": len(plots)}
