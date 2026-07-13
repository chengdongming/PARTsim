"""Persisted-data-only resource, censoring, and scalability aggregation."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Mapping, Sequence

from .censored_runtime import runtime_summary
from .config import canonical_json
from .resource_measurement import materialize_resource_usage
from .result_writer import FAILURE_COLUMNS, atomic_write_json, read_csv, write_csv


RUNTIME_COLUMNS = (
    "scalability_cell_id", "scaling_axis", "level_id", "worker_count",
    "analysis_id", "analysis_variant", "event_observed", "censoring_status",
    "observed_time_seconds", "timeout_budget_seconds",
    "right_censored_lower_bound_seconds",
)

SUMMARY_COLUMNS = (
    "scaling_axis", "level_id", "level_value", "worker_count", "variant",
    "analysis_count", "completed_count", "completed_mean_seconds",
    "completed_median_seconds", "completed_p95_seconds", "completed_max_seconds",
    "timeout_count", "timeout_rate", "censored_count", "timeout_budget_seconds",
    "restricted_mean_runtime_seconds", "restriction_tau_seconds",
    "peak_rss_mean_kib", "peak_rss_max_kib", "checked_w_total",
    "checked_h_total", "checked_q_total", "envelope_call_total",
    "candidate_found_task_total", "cell_wall_seconds",
    "throughput_analyses_per_second",
)

WORKER_CHECK_COLUMNS = (
    "taskset_hash", "analysis_variant", "left_worker_count",
    "right_worker_count", "left_analysis_id", "right_analysis_id", "status",
    "detail",
)


def _truth(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def _analysis_cells(root: Path) -> Dict[str, Mapping[str, str]]:
    result = {}
    for cell in read_csv(root / "scalability_cells.csv"):
        for analysis_id in json.loads(cell["analysis_ids_json"]):
            result[str(analysis_id)] = cell
    return result


def _runtime_rows(root: Path) -> list[Dict[str, Any]]:
    cells = _analysis_cells(root)
    rows = []
    for result in read_csv(root / "per_taskset_results.csv"):
        cell = cells[result["analysis_id"]]
        timeout = result["solver_status"] == "TIMEOUT" or _truth(result["outer_timeout"])
        completed = result["solver_status"] in {"COMPLETED", "NO_CANDIDATE"}
        if timeout:
            observed = float(result["timeout_budget_seconds"])
            status = "RIGHT_CENSORED_TIMEOUT"
        elif completed:
            observed = float(result["runtime_wall_seconds"])
            status = "COMPLETED_EVENT"
        else:
            observed = float(result["runtime_wall_seconds"] or 0)
            status = "NON_RUNTIME_TERMINAL"
        rows.append({
            "scalability_cell_id": cell["scalability_cell_id"],
            "scaling_axis": cell["scaling_axis"], "level_id": cell["level_id"],
            "worker_count": cell["worker_count"], "analysis_id": result["analysis_id"],
            "analysis_variant": result["analysis_variant"],
            "event_observed": completed,
            "censoring_status": status,
            "observed_time_seconds": f"{observed:.9f}",
            "timeout_budget_seconds": result["timeout_budget_seconds"],
            "right_censored_lower_bound_seconds": (
                result["timeout_budget_seconds"] if timeout else ""
            ),
        })
    return rows


def _worker_semantic_checks(root: Path) -> list[Dict[str, Any]]:
    cells = _analysis_cells(root)
    results = read_csv(root / "per_taskset_results.csv")
    tasks: Dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in read_csv(root / "per_task_results.csv"):
        tasks[row["analysis_id"]].append((
            row["task_id"], row["task_solver_status"], row["candidate_response_time"]
        ))
    workers = [
        row for row in results if cells[row["analysis_id"]]["scaling_axis"] == "worker_count"
    ]
    groups: Dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in workers:
        groups[(row["taskset_hash"], row["analysis_variant"])].append(row)
    checks = []
    for (taskset_hash, variant), members in sorted(groups.items()):
        members.sort(key=lambda row: int(cells[row["analysis_id"]]["worker_count"]))
        for left, right in zip(members, members[1:]):
            left_semantic = (
                left["solver_status"], left["certification_status"],
                left["taskset_proven"], sorted(tasks[left["analysis_id"]]),
            )
            right_semantic = (
                right["solver_status"], right["certification_status"],
                right["taskset_proven"], sorted(tasks[right["analysis_id"]]),
            )
            equal = left_semantic == right_semantic
            checks.append({
                "taskset_hash": taskset_hash, "analysis_variant": variant,
                "left_worker_count": cells[left["analysis_id"]]["worker_count"],
                "right_worker_count": cells[right["analysis_id"]]["worker_count"],
                "left_analysis_id": left["analysis_id"],
                "right_analysis_id": right["analysis_id"],
                "status": "SEMANTICALLY_EQUAL" if equal else "SEMANTIC_MISMATCH",
                "detail": "" if equal else "terminal/candidate vectors differ",
            })
    return checks


def aggregate_core5(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    resources = materialize_resource_usage(root)
    runtime_rows = _runtime_rows(root)
    write_csv(root / "runtime_censoring.csv", RUNTIME_COLUMNS, runtime_rows)
    cells = {row["scalability_cell_id"]: row for row in read_csv(root / "scalability_cells.csv")}
    grouped: Dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in runtime_rows:
        grouped[(row["scaling_axis"], row["level_id"], row["analysis_variant"])].append(row)
    resource_by_analysis: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in resources:
        resource_by_analysis[row["analysis_id"]].append(row)
    summary_rows = []
    for (axis, level_id, variant), members in sorted(grouped.items()):
        cell = cells[members[0]["scalability_cell_id"]]
        runtime = runtime_summary(members)
        resource_members = [
            resource for member in members for resource in resource_by_analysis[member["analysis_id"]]
        ]
        peaks = [
            float(row["peak_rss"]) for row in resource_members
            if row["peak_rss"] not in (None, "", "UNAVAILABLE")
        ]
        def count(field: str) -> int:
            return sum(
                int(row[field]) for row in resource_members
                if row[field] not in (None, "", "UNAVAILABLE")
            )
        summary_rows.append({
            "scaling_axis": axis, "level_id": level_id,
            "level_value": cell["level_value"], "worker_count": cell["worker_count"],
            "variant": variant, **runtime,
            "timeout_budget_seconds": max(float(row["timeout_budget_seconds"]) for row in members),
            "peak_rss_mean_kib": mean(peaks) if peaks else "UNAVAILABLE",
            "peak_rss_max_kib": max(peaks) if peaks else "UNAVAILABLE",
            "checked_w_total": count("checked_w_count"),
            "checked_h_total": count("checked_h_count"),
            "checked_q_total": count("checked_q_count"),
            "envelope_call_total": count("envelope_call_count"),
            "candidate_found_task_total": count("candidate_found_task_count"),
            "cell_wall_seconds": cell["cell_wall_seconds"],
            "throughput_analyses_per_second": cell["throughput_analyses_per_second"],
        })
    write_csv(root / "scalability_summary.csv", SUMMARY_COLUMNS, summary_rows)
    worker_checks = _worker_semantic_checks(root)
    write_csv(root / "worker_semantic_checks.csv", WORKER_CHECK_COLUMNS, worker_checks)
    mismatches = [row for row in worker_checks if row["status"] == "SEMANTIC_MISMATCH"]
    if mismatches:
        failures = read_csv(root / "failures.csv")
        for row in mismatches:
            failure_path = root / "failure_inputs" / f"worker-{row['right_analysis_id']}.json"
            atomic_write_json(failure_path, row)
            failures.append({
                "severity": "P0", "stage": "WORKER_SEMANTICS",
                "analysis_id": row["right_analysis_id"], "cell_id": "",
                "taskset_id": row["taskset_hash"], "variant": row["analysis_variant"],
                "code": "WORKER_SEMANTIC_MISMATCH", "detail": row["detail"],
                "traceback": "", "failure_input": str(failure_path),
            })
        write_csv(root / "failures.csv", FAILURE_COLUMNS, failures)

    plot_rows = []
    metric_map = (
        ("runtime", "completed_median_seconds"),
        ("runtime", "completed_p95_seconds"),
        ("peak_rss", "peak_rss_max_kib"),
        ("search_counts", "checked_w_total"),
        ("timeout_rate", "timeout_rate"),
        ("throughput", "throughput_analyses_per_second"),
    )
    for row in summary_rows:
        for plot, metric in metric_map:
            plot_rows.append({
                "plot": plot, "scaling_axis": row["scaling_axis"],
                "level_id": row["level_id"], "level_value": row["level_value"],
                "worker_count": row["worker_count"], "variant": row["variant"],
                "metric": metric, "value": row[metric],
            })
    write_csv(
        root / "core5_plot_data.csv",
        ("plot", "scaling_axis", "level_id", "level_value", "worker_count", "variant", "metric", "value"),
        plot_rows,
    )
    recorded_p0 = sum(row["severity"] == "P0" for row in read_csv(root / "failures.csv"))
    summary = {
        "parallel_throughput_is_not_algorithmic_complexity": True,
        "resource_semantics": {
            "peak_rss_scope": "child solver process; descendants excluded; shared libraries included",
            "peak_rss_unit": "KiB",
            "unavailable_values_are_not_zero": True,
        },
        "groups": summary_rows,
        "worker_semantic_check_count": len(worker_checks),
        "worker_semantic_mismatch_count": len(mismatches),
        "p0_count": recorded_p0,
    }
    atomic_write_json(root / "scalability_summary.json", summary)
    return summary
