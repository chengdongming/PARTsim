"""Platform-explicit resource records derived from persisted CORE-5 attempts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .result_writer import read_csv, write_csv


RESOURCE_OBSERVATION_COLUMNS = (
    "attempt_id", "analysis_id", "peak_rss_kib", "peak_rss_scope",
    "peak_rss_unit",
)

RESOURCE_USAGE_COLUMNS = (
    "scalability_cell_id", "scaling_axis", "level_id", "worker_count",
    "analysis_id", "attempt_id", "attempt_number", "analysis_variant",
    "solver_wall_seconds", "solver_cpu_seconds", "total_wall_seconds",
    "worker_startup_seconds", "serialization_seconds",
    "deserialization_seconds", "ipc_seconds", "peak_rss", "peak_rss_unit",
    "peak_rss_scope", "checked_w_count", "checked_h_count", "checked_q_count",
    "envelope_call_count", "fixed_point_iterations",
    "candidate_found_task_count", "first_failed_priority",
    "timeout_budget_seconds", "terminal_status", "outer_timeout",
)


def materialize_resource_usage(root: Path | str) -> list[Dict[str, Any]]:
    root = Path(root)
    cell_by_analysis: Dict[str, Mapping[str, Any]] = {}
    for cell in read_csv(root / "scalability_cells.csv"):
        for analysis_id in json.loads(cell["analysis_ids_json"]):
            cell_by_analysis[str(analysis_id)] = cell
    observed = {
        row["attempt_id"]: row
        for row in read_csv(root / "attempt_resource_observations.csv")
    }
    results = {
        row["analysis_id"]: row for row in read_csv(root / "per_taskset_results.csv")
    }
    task_rows: Dict[str, list[Mapping[str, str]]] = {}
    for row in read_csv(root / "per_task_results.csv"):
        task_rows.setdefault(row["analysis_id"], []).append(row)
    rows = []
    for attempt in read_csv(root / "analysis_attempts.csv"):
        analysis_id = attempt["analysis_id"]
        cell = cell_by_analysis.get(analysis_id, {})
        result = results.get(analysis_id, {})
        tasks = task_rows.get(analysis_id, [])
        is_final = attempt["attempt_id"] == result.get("final_attempt_id")
        resource = observed.get(attempt["attempt_id"], {})
        def total(field: str) -> Any:
            return sum(int(row[field]) for row in tasks if row.get(field) not in (None, "")) if is_final else "UNAVAILABLE"
        rows.append({
            "scalability_cell_id": cell.get("scalability_cell_id", "UNAVAILABLE"),
            "scaling_axis": cell.get("scaling_axis", "UNAVAILABLE"),
            "level_id": cell.get("level_id", "UNAVAILABLE"),
            "worker_count": cell.get("worker_count", "UNAVAILABLE"),
            "analysis_id": analysis_id, "attempt_id": attempt["attempt_id"],
            "attempt_number": attempt["attempt_number"],
            "analysis_variant": result.get("analysis_variant", "UNAVAILABLE"),
            "solver_wall_seconds": attempt["solver_wall_seconds"],
            "solver_cpu_seconds": attempt["solver_cpu_seconds"],
            "total_wall_seconds": attempt["total_wall_seconds"],
            "worker_startup_seconds": attempt["worker_startup_seconds"],
            "serialization_seconds": attempt["serialization_seconds"],
            # multiprocessing spawn does not expose this component reliably.
            "deserialization_seconds": "UNAVAILABLE",
            "ipc_seconds": attempt["ipc_seconds"],
            "peak_rss": resource.get("peak_rss_kib", "UNAVAILABLE"),
            "peak_rss_unit": resource.get("peak_rss_unit", "UNAVAILABLE"),
            "peak_rss_scope": resource.get("peak_rss_scope", "UNAVAILABLE"),
            "checked_w_count": total("checked_w_count"),
            "checked_h_count": total("checked_h_count"),
            "checked_q_count": total("checked_q_count"),
            "envelope_call_count": total("envelope_call_count"),
            # The frozen v9.3 production record does not expose this counter.
            "fixed_point_iterations": "UNAVAILABLE",
            "candidate_found_task_count": result.get(
                "n_tasks_candidate_found", "UNAVAILABLE"
            ) if is_final else "UNAVAILABLE",
            "first_failed_priority": result.get(
                "first_failed_priority", "UNAVAILABLE"
            ) if is_final else "UNAVAILABLE",
            "timeout_budget_seconds": attempt["timeout_budget_seconds"],
            "terminal_status": attempt["solver_status"],
            "outer_timeout": attempt["outer_timeout"],
        })
    write_csv(root / "resource_usage.csv", RESOURCE_USAGE_COLUMNS, rows)
    return rows
