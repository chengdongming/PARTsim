"""Strict persisted-data-only CORE-5 aggregation and analyzer closure."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Mapping, Sequence

from .censored_runtime import runtime_summary
from .config import canonical_json, domain_hash
from .core5_contract import (
    RUNTIME_COLUMNS,
    SUMMARY_COLUMNS,
    WORKER_CHECK_COLUMNS,
    Core5ContractError,
    validate_core5_artifact_contract,
    validate_core5_hash_manifest,
    write_core5_hash_manifest,
)
from .core5_terminal import Core5TerminalClass, classify_core5_terminal, truth
from .resource_measurement import materialize_resource_usage
from .result_writer import FAILURE_COLUMNS, atomic_write_json, read_csv, write_csv


DERIVED_WORKER_FAILURE_CODES = frozenset({
    "MISSING_WORKER_PAIR",
    "WORKER_MATHEMATICAL_INPUT_MISMATCH",
    "WORKER_TECHNICAL_FAILURE",
    "WORKER_SEMANTIC_MISMATCH",
})


def _unique_by(
    rows: Sequence[Mapping[str, str]], field: str, label: str,
) -> Dict[str, Mapping[str, str]]:
    result: Dict[str, Mapping[str, str]] = {}
    for row in rows:
        key = row.get(field, "")
        if not key:
            raise Core5ContractError(f"{label} has an empty {field}")
        if key in result:
            raise Core5ContractError(f"duplicate {label}: {key}")
        result[key] = row
    return result


def _analysis_cells(root: Path) -> Dict[str, Mapping[str, str]]:
    result: Dict[str, Mapping[str, str]] = {}
    for cell in read_csv(root / "scalability_cells.csv"):
        try:
            analysis_ids = json.loads(cell["analysis_ids_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise Core5ContractError("cell analysis_ids_json is malformed") from exc
        if not isinstance(analysis_ids, list):
            raise Core5ContractError("cell analysis_ids_json is not a list")
        for analysis_id in analysis_ids:
            key = str(analysis_id)
            if not key or key in result:
                raise Core5ContractError(
                    f"duplicate or empty scalability analysis ID: {key}"
                )
            result[key] = cell
    return result


def _runtime_rows(root: Path) -> list[Dict[str, Any]]:
    cells = _analysis_cells(root)
    rows = []
    for result in read_csv(root / "per_taskset_results.csv"):
        cell = cells.get(result["analysis_id"])
        if cell is None:
            raise Core5ContractError("terminal is not assigned to a scalability cell")
        terminal_class = classify_core5_terminal(
            result["solver_status"], outer_timeout=result["outer_timeout"]
        )
        if terminal_class == Core5TerminalClass.RIGHT_CENSORED:
            observed: Any = float(result["timeout_budget_seconds"])
            status = "RIGHT_CENSORED_TIMEOUT"
            event_observed = False
        elif terminal_class == Core5TerminalClass.SCIENTIFIC_COMPLETION:
            observed = float(result["runtime_wall_seconds"])
            status = "COMPLETED_EVENT"
            event_observed = True
        else:
            observed = "UNAVAILABLE"
            status = "TECHNICAL_FAILURE"
            event_observed = False
        rows.append({
            "scalability_cell_id": cell["scalability_cell_id"],
            "scaling_axis": cell["scaling_axis"],
            "level_id": cell["level_id"],
            "worker_count": cell["worker_count"],
            "analysis_id": result["analysis_id"],
            "analysis_variant": result["analysis_variant"],
            "terminal_class": terminal_class.value,
            "event_observed": event_observed,
            "censoring_status": status,
            "observed_time_seconds": (
                f"{observed:.9f}" if isinstance(observed, float) else observed
            ),
            "timeout_budget_seconds": result["timeout_budget_seconds"],
            "right_censored_lower_bound_seconds": (
                result["timeout_budget_seconds"]
                if terminal_class == Core5TerminalClass.RIGHT_CENSORED else ""
            ),
        })
    return rows


def _planned_groups(
    root: Path, cells: Mapping[str, Mapping[str, str]],
) -> Dict[tuple[str, str, str], int]:
    requests = _unique_by(
        read_csv(root / "analysis_requests.csv"), "analysis_id", "analysis request"
    )
    counts: Dict[tuple[str, str, str], int] = defaultdict(int)
    for analysis_id, cell in cells.items():
        request = requests.get(analysis_id)
        if request is None:
            raise Core5ContractError("scalability cell has no analysis request")
        counts[(cell["scaling_axis"], cell["level_id"], request["variant"])] += 1
    return counts


def _task_semantics(
    tasks: Sequence[Mapping[str, str]], analysis_id: str,
) -> list[tuple[str, ...]]:
    ignored = {
        "analysis_id", "cell_id", "taskset_id", "checked_w_count",
        "checked_h_count", "checked_q_count", "envelope_call_count",
    }
    return sorted(
        tuple(row[field] for field in row if field not in ignored)
        for row in tasks if row["analysis_id"] == analysis_id
    )


def _worker_semantic_checks(root: Path) -> list[Dict[str, Any]]:
    cells_by_analysis = _analysis_cells(root)
    worker_cells = [
        row for row in read_csv(root / "scalability_cells.csv")
        if row["scaling_axis"] == "worker_count"
    ]
    worker_levels = sorted({int(row["worker_count"]) for row in worker_cells})
    if len(worker_levels) < 2:
        raise Core5ContractError("worker axis does not declare at least two levels")
    requests = _unique_by(
        read_csv(root / "analysis_requests.csv"), "analysis_id", "analysis request"
    )
    generated = _unique_by(
        read_csv(root / "generated_tasksets.csv"), "taskset_id", "generated taskset"
    )
    results = _unique_by(
        read_csv(root / "per_taskset_results.csv"), "analysis_id", "terminal result"
    )
    tasks = read_csv(root / "per_task_results.csv")

    groups: Dict[
        tuple[str, str], Dict[int, tuple[Mapping[str, str], Mapping[str, str], Mapping[str, str], Mapping[str, str] | None]]
    ] = defaultdict(dict)
    for analysis_id, cell in cells_by_analysis.items():
        if cell["scaling_axis"] != "worker_count":
            continue
        request = requests.get(analysis_id)
        if request is None:
            raise Core5ContractError("worker cell analysis is missing its request")
        generated_row = generated.get(request["taskset_id"])
        if generated_row is None:
            raise Core5ContractError("worker request is missing its generated taskset")
        key = (generated_row["taskset_index"], request["variant"])
        worker = int(cell["worker_count"])
        if worker in groups[key]:
            raise Core5ContractError("duplicate worker level in semantic pairing group")
        groups[key][worker] = (
            cell, request, generated_row, results.get(analysis_id)
        )

    checks: list[Dict[str, Any]] = []
    for (taskset_index, variant), members in sorted(groups.items()):
        for left_worker, right_worker in zip(worker_levels, worker_levels[1:]):
            left = members.get(left_worker)
            right = members.get(right_worker)
            left_id = left[1]["analysis_id"] if left else ""
            right_id = right[1]["analysis_id"] if right else ""
            status = "SEMANTICALLY_EQUAL"
            detail = ""
            if left is None or right is None or left[3] is None or right[3] is None:
                status = "MISSING_WORKER_PAIR"
                detail = "configured worker level or terminal is missing"
            else:
                def mathematical_signature(bundle: tuple[Any, ...]) -> tuple[Any, ...]:
                    cell, request, generated_row, _ = bundle
                    return (
                        generated_row["generation_id"],
                        generated_row["taskset_id"],
                        generated_row["taskset_hash"],
                        generated_row["taskset_index"],
                        generated_row["generation_seed"],
                        generated_row["M"], generated_row["task_n"],
                        generated_row["target_total_utilization"],
                        generated_row["deadline_mode"],
                        generated_row["d_over_t_values_json"],
                        generated_row["task_input_json"],
                        generated_row["priority_hash"],
                        generated_row["power_hash"],
                        generated_row["service_curve_reference"],
                        cell["M"], cell["task_n"], cell["utilization"],
                        cell["period_min"], cell["period_max"],
                        request["exact_e0"], request["numerical_mode"],
                        request["variant"],
                    )

                if mathematical_signature(left) != mathematical_signature(right):
                    status = "WORKER_MATHEMATICAL_INPUT_MISMATCH"
                    detail = "worker levels changed a frozen mathematical input"
                else:
                    left_result = left[3]
                    right_result = right[3]
                    assert left_result is not None and right_result is not None
                    left_class = classify_core5_terminal(
                        left_result["solver_status"],
                        outer_timeout=left_result["outer_timeout"],
                    )
                    right_class = classify_core5_terminal(
                        right_result["solver_status"],
                        outer_timeout=right_result["outer_timeout"],
                    )
                    if Core5TerminalClass.TECHNICAL_FAILURE in {
                        left_class, right_class,
                    }:
                        status = "TECHNICAL_FAILURE"
                        detail = "worker pair contains a technical terminal"
                    elif Core5TerminalClass.RIGHT_CENSORED in {
                        left_class, right_class,
                    }:
                        status = "TIMEOUT_CENSORED"
                        detail = "worker equality is not evaluated across censoring"
                    else:
                        result_fields = (
                            "solver_status", "certification_status",
                            "taskset_proven", "first_failed_priority",
                            "n_tasks_total", "n_tasks_evaluated",
                            "n_tasks_candidate_found", "n_tasks_certified",
                        )
                        left_semantic = (
                            tuple(left_result[field] for field in result_fields),
                            _task_semantics(tasks, left_id),
                        )
                        right_semantic = (
                            tuple(right_result[field] for field in result_fields),
                            _task_semantics(tasks, right_id),
                        )
                        if left_semantic != right_semantic:
                            status = "WORKER_SEMANTIC_MISMATCH"
                            detail = "solver/certification/task vectors differ"
            checks.append({
                "taskset_index": taskset_index,
                "analysis_variant": variant,
                "left_worker_count": left_worker,
                "right_worker_count": right_worker,
                "left_analysis_id": left_id,
                "right_analysis_id": right_id,
                "status": status,
                "detail": detail,
            })
    return checks


def _availability_status(available: int, total: int, *, partial: bool = False) -> str:
    if available == 0:
        return "UNAVAILABLE"
    if available == total and not partial:
        return "AVAILABLE"
    return "PARTIAL"


def _available_sum(rows: Sequence[Mapping[str, Any]], field: str) -> Any:
    values = [
        int(row[field]) for row in rows
        if row.get(field) not in (None, "", "UNAVAILABLE")
    ]
    return sum(values) if values else "UNAVAILABLE"


def _record_worker_failures(
    root: Path, checks: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, str]]:
    failures = [
        row for row in read_csv(root / "failures.csv")
        if row.get("code") not in DERIVED_WORKER_FAILURE_CODES
    ]
    p0_statuses = {
        "MISSING_WORKER_PAIR": "MISSING_WORKER_PAIR",
        "WORKER_MATHEMATICAL_INPUT_MISMATCH": "WORKER_MATHEMATICAL_INPUT_MISMATCH",
        "TECHNICAL_FAILURE": "WORKER_TECHNICAL_FAILURE",
        "WORKER_SEMANTIC_MISMATCH": "WORKER_SEMANTIC_MISMATCH",
    }
    for check in checks:
        code = p0_statuses.get(str(check["status"]))
        if code is None:
            continue
        failure_id = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5_WORKER_FAILURE:v2", check
        )
        failure_path = root / "failure_inputs" / f"worker-{failure_id}.json"
        atomic_write_json(failure_path, {
            "schema": "ASAP_BLOCK_V9_3_CORE5_WORKER_FAILURE_V2",
            "failure_id": failure_id,
            "check": check,
        })
        failures.append({
            "severity": "P0", "stage": "CORE5_WORKER_PAIRING",
            "analysis_id": failure_id, "cell_id": "", "taskset_id": "",
            "variant": check["analysis_variant"], "code": code,
            "detail": check["detail"], "traceback": "",
            "failure_input": str(failure_path),
        })
    write_csv(root / "failures.csv", FAILURE_COLUMNS, failures)
    return failures


def aggregate_core5(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    resources = materialize_resource_usage(root)
    runtime_rows = _runtime_rows(root)
    write_csv(root / "runtime_censoring.csv", RUNTIME_COLUMNS, runtime_rows)
    cells_by_analysis = _analysis_cells(root)
    cells = _unique_by(
        read_csv(root / "scalability_cells.csv"),
        "scalability_cell_id", "scalability cell",
    )
    planned_groups = _planned_groups(root, cells_by_analysis)
    grouped: Dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in runtime_rows:
        grouped[(row["scaling_axis"], row["level_id"], row["analysis_variant"])].append(row)
    resource_by_analysis: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in resources:
        resource_by_analysis[row["analysis_id"]].append(row)

    summary_rows = []
    for key, planned_count in sorted(planned_groups.items()):
        axis, level_id, variant = key
        members = grouped.get(key, [])
        matching_cells = [
            row for row in cells.values()
            if row["scaling_axis"] == axis and row["level_id"] == level_id
        ]
        if len(matching_cells) != 1:
            raise Core5ContractError("summary group does not map to exactly one cell")
        cell = matching_cells[0]
        runtime = runtime_summary(
            members, planned_analysis_count=planned_count
        )
        final_resources = [
            resource
            for member in members
            for resource in resource_by_analysis[member["analysis_id"]]
            if truth(resource.get("is_final_attempt"))
        ]
        peaks = [
            float(row["peak_rss"]) for row in final_resources
            if row["resource_observation_status"] == "AVAILABLE"
        ]
        search_available = sum(
            row["search_counter_observation_status"] in {"AVAILABLE", "PARTIAL"}
            for row in final_resources
        )
        search_partial = any(
            row["search_counter_observation_status"] == "PARTIAL"
            for row in final_resources
        )
        timeout_budgets = [
            float(row["timeout_budget_seconds"]) for row in members
            if row["terminal_class"] != Core5TerminalClass.TECHNICAL_FAILURE.value
        ]
        summary_rows.append({
            "scaling_axis": axis, "level_id": level_id,
            "level_value": cell["level_value"],
            "worker_count": cell["worker_count"], "variant": variant,
            **runtime,
            "timeout_budget_seconds": max(timeout_budgets) if timeout_budgets else None,
            "final_attempt_resource_count": len(final_resources),
            "peak_rss_available_observation_count": len(peaks),
            "peak_rss_observation_status": _availability_status(
                len(peaks), len(final_resources)
            ),
            "peak_rss_final_attempt_mean_kib": mean(peaks) if peaks else "UNAVAILABLE",
            "peak_rss_final_attempt_max_kib": max(peaks) if peaks else "UNAVAILABLE",
            "search_counter_available_analysis_count": search_available,
            "search_counter_observation_status": _availability_status(
                search_available, len(final_resources), partial=search_partial
            ),
            "checked_w_final_attempt_total": _available_sum(
                final_resources, "checked_w_count"
            ),
            "checked_h_final_attempt_total": _available_sum(
                final_resources, "checked_h_count"
            ),
            "checked_q_final_attempt_total": _available_sum(
                final_resources, "checked_q_count"
            ),
            "envelope_call_final_attempt_total": _available_sum(
                final_resources, "envelope_call_count"
            ),
            "candidate_found_task_final_attempt_total": _available_sum(
                final_resources, "candidate_found_task_count"
            ),
            "cell_wall_seconds": cell["cell_wall_seconds"],
            "throughput_analyses_per_second": cell[
                "throughput_analyses_per_second"
            ],
        })
    write_csv(root / "scalability_summary.csv", SUMMARY_COLUMNS, summary_rows)

    worker_checks = _worker_semantic_checks(root)
    write_csv(
        root / "worker_semantic_checks.csv", WORKER_CHECK_COLUMNS, worker_checks
    )
    failures = _record_worker_failures(root, worker_checks)

    plot_rows = []
    metric_map = (
        ("runtime", "completed_median_seconds"),
        ("runtime", "completed_p95_seconds"),
        ("peak_rss", "peak_rss_final_attempt_max_kib"),
        ("search_counts", "checked_w_final_attempt_total"),
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
        (
            "plot", "scaling_axis", "level_id", "level_value",
            "worker_count", "variant", "metric", "value",
        ),
        plot_rows,
    )

    technical_ids = {
        row["analysis_id"] for row in runtime_rows
        if row["terminal_class"] == Core5TerminalClass.TECHNICAL_FAILURE.value
    }
    recorded_p0 = sum(row["severity"] == "P0" for row in failures)
    bad_checks = [
        row for row in worker_checks
        if row["status"] not in {"SEMANTICALLY_EQUAL", "TIMEOUT_CENSORED"}
    ]
    summary = {
        "parallel_throughput_is_not_algorithmic_complexity": True,
        "stopped": bool(recorded_p0 or technical_ids),
        "planned_analysis_count": sum(row["planned_analysis_count"] for row in summary_rows),
        "terminal_analysis_count": len(runtime_rows),
        "runtime_evaluable_count": sum(row["runtime_evaluable_count"] for row in summary_rows),
        "completed_count": sum(row["completed_count"] for row in summary_rows),
        "timeout_count": sum(row["timeout_count"] for row in summary_rows),
        "technical_failure_count": len(technical_ids),
        "censored_count": sum(row["censored_count"] for row in summary_rows),
        "timeout_rate_evaluable_denominator": sum(
            row["timeout_rate_evaluable_denominator"] for row in summary_rows
        ),
        "resource_semantics": {
            "peak_rss_scope": (
                "final attempt child solver process; descendants excluded; "
                "shared libraries included"
            ),
            "peak_rss_unit": "KiB",
            "resource_usage_scope": "all attempts",
            "scientific_search_counter_scope": "final attempt only",
            "unavailable_values_are_not_zero": True,
        },
        "groups": summary_rows,
        "worker_semantic_check_count": len(worker_checks),
        "worker_semantic_failure_count": len(bad_checks),
        "worker_semantic_mismatch_count": sum(
            row["status"] == "WORKER_SEMANTIC_MISMATCH"
            for row in worker_checks
        ),
        "p0_count": recorded_p0,
    }
    atomic_write_json(root / "scalability_summary.json", summary)
    return summary


def analyze_core5_artifacts(root: Path | str) -> Dict[str, Any]:
    """Validate completed raw evidence, rebuild, and reseal derived artifacts."""

    root = Path(root)
    validate_core5_artifact_contract(root)
    summary = aggregate_core5(root)
    if summary["stopped"]:
        raise Core5ContractError(
            "rebuilt CORE-5 summary contains a technical or P0 failure"
        )
    write_core5_hash_manifest(root)
    validate_core5_hash_manifest(root, require_completed_files=True)
    validate_core5_artifact_contract(root)
    return summary
