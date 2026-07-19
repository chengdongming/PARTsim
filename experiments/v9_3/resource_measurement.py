"""Strict per-attempt resource joins and explicit CORE-5 availability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .core5_terminal import Core5TerminalClass, classify_core5_terminal, truth
from .result_writer import read_csv, write_csv


RESOURCE_OBSERVATION_COLUMNS = (
    "attempt_id", "analysis_id", "peak_rss_kib", "peak_rss_scope",
    "peak_rss_unit", "observation_status", "unavailability_reason",
)

RESOURCE_USAGE_COLUMNS = (
    "scalability_cell_id", "scaling_axis", "level_id", "worker_count",
    "analysis_id", "attempt_id", "attempt_number", "analysis_variant",
    "solver_wall_seconds", "solver_cpu_seconds", "total_wall_seconds",
    "worker_startup_seconds", "serialization_seconds",
    "deserialization_seconds", "ipc_seconds", "peak_rss", "peak_rss_unit",
    "peak_rss_scope", "resource_observation_status",
    "resource_unavailability_reason", "is_final_attempt",
    "checked_w_count", "checked_h_count", "checked_q_count",
    "envelope_call_count", "fixed_point_iterations",
    "search_counter_observation_status", "search_counter_available_task_count",
    "search_counter_total_task_count", "candidate_found_task_count",
    "first_failed_priority", "timeout_budget_seconds", "terminal_status",
    "outer_timeout",
)


class ResourceContractError(RuntimeError):
    """Raised when attempt/resource joins are not one-to-one and explicit."""


def _unique(
    rows: Sequence[Mapping[str, str]], field: str, label: str,
) -> Dict[str, Mapping[str, str]]:
    result: Dict[str, Mapping[str, str]] = {}
    for row in rows:
        value = row.get(field, "")
        if not value:
            raise ResourceContractError(f"{label} has an empty {field}")
        if value in result:
            raise ResourceContractError(f"duplicate {label} {field}: {value}")
        result[value] = row
    return result


def _counter_total(
    tasks: Sequence[Mapping[str, str]], field: str,
) -> tuple[Any, int]:
    available = [
        row[field] for row in tasks
        if row.get(field) not in (None, "", "UNAVAILABLE")
    ]
    if not available:
        return "UNAVAILABLE", 0
    return sum(int(value) for value in available), len(available)


def materialize_resource_usage(root: Path | str) -> list[Dict[str, Any]]:
    root = Path(root)
    cell_by_analysis: Dict[str, Mapping[str, Any]] = {}
    for cell in read_csv(root / "scalability_cells.csv"):
        for analysis_id in json.loads(cell["analysis_ids_json"]):
            if str(analysis_id) in cell_by_analysis:
                raise ResourceContractError(
                    f"duplicate scalability analysis identity: {analysis_id}"
                )
            cell_by_analysis[str(analysis_id)] = cell

    requests = _unique(
        read_csv(root / "analysis_requests.csv"), "analysis_id",
        "analysis request",
    )
    attempt_rows = read_csv(root / "analysis_attempts.csv")
    attempts = _unique(attempt_rows, "attempt_id", "analysis attempt")
    observed = _unique(
        read_csv(root / "attempt_resource_observations.csv"),
        "attempt_id", "resource observation",
    )
    results = _unique(
        read_csv(root / "per_taskset_results.csv"),
        "analysis_id", "terminal result",
    )

    for attempt in attempts.values():
        if attempt["analysis_id"] not in requests:
            raise ResourceContractError("analysis attempt has no request")
    for observation in observed.values():
        attempt = attempts.get(observation["attempt_id"])
        if attempt is None:
            raise ResourceContractError("resource observation has no attempt")
        if observation["analysis_id"] != attempt["analysis_id"]:
            raise ResourceContractError(
                "resource observation/attempt analysis mismatch"
            )
    for attempt in attempts.values():
        observation = observed.get(attempt["attempt_id"])
        if observation is None:
            raise ResourceContractError(
                "analysis attempt has no explicit resource observation"
            )
        if truth(attempt["payload_received"]) and observation.get(
            "observation_status"
        ) != "AVAILABLE":
            raise ResourceContractError(
                "payload-bearing attempt is missing peak RSS"
            )
        terminal_class = classify_core5_terminal(
            attempt["solver_status"], outer_timeout=attempt["outer_timeout"]
        )
        if (
            observation.get("observation_status") == "EXPECTED_UNAVAILABLE"
            and terminal_class != Core5TerminalClass.RIGHT_CENSORED
        ):
            raise ResourceContractError(
                "expected-unavailable peak RSS is only valid for no-payload timeout"
            )
    for result in results.values():
        attempt = attempts.get(result.get("final_attempt_id", ""))
        if attempt is None or attempt["analysis_id"] != result["analysis_id"]:
            raise ResourceContractError(
                "terminal final_attempt_id is not a matching attempt"
            )

    task_rows: Dict[str, list[Mapping[str, str]]] = {}
    task_keys: set[tuple[str, str]] = set()
    for row in read_csv(root / "per_task_results.csv"):
        key = (row["analysis_id"], row["task_id"])
        if key in task_keys:
            raise ResourceContractError(f"duplicate task result: {key}")
        task_keys.add(key)
        task_rows.setdefault(row["analysis_id"], []).append(row)

    rows = []
    for attempt in attempt_rows:
        analysis_id = attempt["analysis_id"]
        cell = cell_by_analysis.get(analysis_id, {})
        result = results.get(analysis_id, {})
        tasks = task_rows.get(analysis_id, [])
        is_final = attempt["attempt_id"] == result.get("final_attempt_id")
        resource = observed[attempt["attempt_id"]]
        counter_values = {
            field: _counter_total(tasks, field)
            for field in (
                "checked_w_count", "checked_h_count", "checked_q_count",
                "envelope_call_count",
            )
        }
        available_counts = [count for _, count in counter_values.values()]
        available_task_count = min(available_counts, default=0)
        if not is_final:
            search_status = "NOT_APPLICABLE_RETRY_ATTEMPT"
        elif not tasks or max(available_counts, default=0) == 0:
            search_status = "UNAVAILABLE"
        elif all(count == len(tasks) for count in available_counts):
            search_status = "AVAILABLE"
        else:
            search_status = "PARTIAL"

        terminal_class = (
            classify_core5_terminal(
                result.get("solver_status", ""),
                outer_timeout=result.get("outer_timeout", False),
            )
            if result else Core5TerminalClass.TECHNICAL_FAILURE
        )
        if not is_final:
            first_failed_priority = "UNAVAILABLE"
        elif result.get("first_failed_priority") not in (None, ""):
            first_failed_priority = result["first_failed_priority"]
        elif (
            terminal_class == Core5TerminalClass.SCIENTIFIC_COMPLETION
            and truth(result.get("taskset_proven"))
        ):
            first_failed_priority = "NOT_APPLICABLE"
        else:
            first_failed_priority = "UNAVAILABLE"

        rows.append({
            "scalability_cell_id": cell.get(
                "scalability_cell_id", "UNAVAILABLE"
            ),
            "scaling_axis": cell.get("scaling_axis", "UNAVAILABLE"),
            "level_id": cell.get("level_id", "UNAVAILABLE"),
            "worker_count": cell.get("worker_count", "UNAVAILABLE"),
            "analysis_id": analysis_id,
            "attempt_id": attempt["attempt_id"],
            "attempt_number": attempt["attempt_number"],
            "analysis_variant": result.get(
                "analysis_variant", "UNAVAILABLE"
            ),
            "solver_wall_seconds": attempt["solver_wall_seconds"],
            "solver_cpu_seconds": attempt["solver_cpu_seconds"],
            "total_wall_seconds": attempt["total_wall_seconds"],
            "worker_startup_seconds": attempt["worker_startup_seconds"],
            "serialization_seconds": attempt["serialization_seconds"],
            "deserialization_seconds": "UNAVAILABLE",
            "ipc_seconds": attempt["ipc_seconds"],
            "peak_rss": resource["peak_rss_kib"],
            "peak_rss_unit": resource["peak_rss_unit"],
            "peak_rss_scope": resource["peak_rss_scope"],
            "resource_observation_status": resource["observation_status"],
            "resource_unavailability_reason": resource[
                "unavailability_reason"
            ],
            "is_final_attempt": is_final,
            "checked_w_count": (
                counter_values["checked_w_count"][0]
                if is_final else "UNAVAILABLE"
            ),
            "checked_h_count": (
                counter_values["checked_h_count"][0]
                if is_final else "UNAVAILABLE"
            ),
            "checked_q_count": (
                counter_values["checked_q_count"][0]
                if is_final else "UNAVAILABLE"
            ),
            "envelope_call_count": (
                counter_values["envelope_call_count"][0]
                if is_final else "UNAVAILABLE"
            ),
            "fixed_point_iterations": "UNAVAILABLE",
            "search_counter_observation_status": search_status,
            "search_counter_available_task_count": (
                available_task_count if is_final else 0
            ),
            "search_counter_total_task_count": len(tasks) if is_final else 0,
            "candidate_found_task_count": result.get(
                "n_tasks_candidate_found", "UNAVAILABLE"
            ) if is_final else "UNAVAILABLE",
            "first_failed_priority": first_failed_priority,
            "timeout_budget_seconds": attempt["timeout_budget_seconds"],
            "terminal_status": attempt["solver_status"],
            "outer_timeout": attempt["outer_timeout"],
        })
    write_csv(root / "resource_usage.csv", RESOURCE_USAGE_COLUMNS, rows)
    return rows
