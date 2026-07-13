"""Formal CORE-1/CORE-2 summaries with explicit statistical domains."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .plotting_data import core1_plot_rows, core2_plot_rows
from .result_reader import RunResults
from .result_writer import atomic_write_json, write_csv, write_file_hashes
from .tightness import by_taskset, compare_tasks


def _float(row: Mapping[str, str], key: str) -> float:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return math.nan


def _quantile(values: Sequence[float], ratio: float) -> float | None:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return None
    index = math.ceil(ratio * len(finite)) - 1
    return finite[max(0, min(index, len(finite) - 1))]


def _median(values: Sequence[float]) -> float | None:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return None
    middle = len(finite) // 2
    return finite[middle] if len(finite) % 2 else (finite[middle - 1] + finite[middle]) / 2


def variant_summary(
    requests: Iterable[Mapping[str, str]], results: Iterable[Mapping[str, str]]
) -> list[Dict[str, Any]]:
    requested = Counter((row["cell_id"], row["variant"]) for row in requests)
    groups: Dict[tuple, list[Mapping[str, str]]] = defaultdict(list)
    for row in results:
        groups[(row["cell_id"], row["analysis_variant"])].append(row)
    output = []
    for key in sorted(requested):
        members = groups.get(key, [])
        count = requested[key]
        statuses = Counter(row["solver_status"] for row in members)
        certified = sum(row["taskset_proven"] == "True" for row in members)
        excluded = {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"}
        completed_only = sum(row["solver_status"] not in excluded for row in members)
        runtime = [_float(row, "runtime_wall_seconds") for row in members]
        output.append({
            "cell_id": key[0], "variant": key[1],
            "unconditional_denominator": count,
            "terminal_count": len(members),
            "completed_only_denominator": completed_only,
            "certified_count": certified,
            "no_candidate_count": statuses["NO_CANDIDATE"],
            "timeout_count": statuses["TIMEOUT"],
            "not_applicable_count": statuses["NOT_APPLICABLE_DEPENDENCY"],
            "numeric_error_count": statuses["NUMERIC_ERROR"],
            "internal_failure_count": statuses["INTERNAL_CONFORMANCE_FAILURE"],
            "certification_ratio_unconditional": certified / count if count else None,
            "certification_ratio_completed_only": certified / completed_only if completed_only else None,
            "runtime_mean": sum(value for value in runtime if math.isfinite(value)) / sum(math.isfinite(value) for value in runtime) if any(math.isfinite(value) for value in runtime) else None,
            "runtime_median": _median(runtime),
            "runtime_p95": _quantile(runtime, .95),
            "runtime_max": max((value for value in runtime if math.isfinite(value)), default=None),
        })
    return output


def _write_dynamic(path: Path, rows: list[Mapping[str, Any]]) -> None:
    columns = list(rows[0]) if rows else ()
    write_csv(path, columns, rows)


def _certification_pairs(results: Iterable[Mapping[str, str]], left: str, right: str) -> list[Dict[str, Any]]:
    index = {(row["cell_id"], row["taskset_id"], row["analysis_variant"]): row for row in results}
    bases = sorted({(cell, taskset_id) for cell, taskset_id, variant in index if variant == left})
    output = []
    for cell, taskset_id in bases:
        lrow = index.get((cell, taskset_id, left))
        rrow = index.get((cell, taskset_id, right))
        if not lrow or not rrow:
            continue
        lc = lrow["taskset_proven"] == "True"
        rc = rrow["taskset_proven"] == "True"
        output.append({
            "cell_id": cell, "taskset_id": taskset_id,
            "exact_e0": lrow["exact_e0"], "left_variant": left,
            "right_variant": right, "left_certified": lc, "right_certified": rc,
            "certification_gain": int(rc and not lc),
            "certification_loss": int(lc and not rc),
            "both_certified": int(lc and rc), "neither_certified": int(not lc and not rc),
            "left_status": lrow["solver_status"], "right_status": rrow["solver_status"],
        })
    return output


def aggregate_core1(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    run = RunResults(root)
    tasksets, tasks = run.tasksets, run.tasks
    requests = run.table("analysis_requests.csv")
    summary_rows = variant_summary(requests, tasksets)
    comparisons = compare_tasks(
        tasks, "CW_THETA_CW", "LOC_THETA_LOC", "MAIN_METHOD_LOCAL_VS_COMPLETE",
        assume_dominance=True,
    )
    by_set = by_taskset(comparisons)
    certification = _certification_pairs(tasksets, "CW_THETA_CW", "LOC_THETA_LOC")
    runtime = []
    result_index = {(row["cell_id"], row["taskset_id"], row["analysis_variant"]): row for row in tasksets}
    for pair in certification:
        key = (pair["cell_id"], pair["taskset_id"])
        left = result_index[key + ("CW_THETA_CW",)]
        right = result_index[key + ("LOC_THETA_LOC",)]
        runtime.append({
            "cell_id": key[0], "taskset_id": key[1], "exact_e0": pair["exact_e0"],
            "cw_runtime": left["runtime_wall_seconds"],
            "loc_runtime": right["runtime_wall_seconds"],
            "cw_status": left["solver_status"], "loc_status": right["solver_status"],
        })
    comparison_by_cell = []
    cells = sorted({row["cell_id"] for row in requests})
    for cell in cells:
        tight = [row for row in comparisons if row["cell_id"] == cell]
        cert = [row for row in certification if row["cell_id"] == cell]
        reductions = [row["reduction"] for row in tight]
        comparison_by_cell.append({
            "cell_id": cell,
            "unconditional_requested_tasksets": sum(row["cell_id"] == cell and row["variant"] == "CW_THETA_CW" for row in requests),
            "completed_only_pairs": sum(row["left_status"] not in {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"} and row["right_status"] not in {"TIMEOUT", "NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"} for row in cert),
            "common_candidate_task_count": len(tight),
            "loc_tighter_count": sum(row["status"] == "TIGHTER" for row in tight),
            "equal_count": sum(row["status"] == "EQUAL" for row in tight),
            "violation_count": sum(row["status"] == "VIOLATION" for row in tight),
            "mean_response_reduction": sum(reductions) / len(reductions) if reductions else None,
            "median_response_reduction": _median(reductions),
            "max_response_reduction": max(reductions, default=None),
            "mean_normalized_reduction": sum(row["normalized_reduction"] for row in tight) / len(tight) if tight else None,
            "certification_gain": sum(row["certification_gain"] for row in cert),
            "certification_loss": sum(row["certification_loss"] for row in cert),
            "both_certified": sum(row["both_certified"] for row in cert),
            "neither_certified": sum(row["neither_certified"] for row in cert),
        })
    _write_dynamic(root / "summary.csv", summary_rows)
    _write_dynamic(root / "core1_method_comparison.csv", comparison_by_cell)
    _write_dynamic(root / "core1_tightness_by_task.csv", comparisons)
    _write_dynamic(root / "core1_tightness_by_taskset.csv", by_set)
    _write_dynamic(root / "core1_certification_comparison.csv", certification)
    _write_dynamic(root / "core1_runtime_comparison.csv", runtime)
    plots = core1_plot_rows(tasksets, comparisons, certification)
    _write_dynamic(root / "core1_plot_data.csv", plots)
    summary = {
        "core": "CORE-1", "requested": len(requests), "terminal": len(tasksets),
        "variants": summary_rows,
        "common_candidate_tasks": len(comparisons),
        "dominance_violations": sum(row["status"] == "VIOLATION" for row in comparisons),
        "formal_large_scale_run": False,
    }
    atomic_write_json(root / "summary.json", summary)
    write_file_hashes(root)
    return summary


CORE2_RELATIONS = (
    ("LOC_D_VS_CW_D", "CW_D", "LOC_D", True),
    ("LOC_THETA_CW_VS_CW_THETA_CW", "CW_THETA_CW", "LOC_THETA_CW", True),
    ("LOC_THETA_LOC_VS_CW_THETA_CW", "CW_THETA_CW", "LOC_THETA_LOC", True),
    ("CW_THETA_CW_VS_CW_D", "CW_D", "CW_THETA_CW", False),
    ("LOC_THETA_LOC_VS_LOC_D", "LOC_D", "LOC_THETA_LOC", False),
)


def aggregate_core2(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    run = RunResults(root)
    tasksets, tasks = run.tasksets, run.tasks
    requests = run.table("analysis_requests.csv")
    dependencies = run.table("dependency_records.csv")
    variants = variant_summary(requests, tasksets)
    task_groups: Dict[tuple, list[Mapping[str, str]]] = defaultdict(list)
    result_groups: Dict[tuple, list[Mapping[str, str]]] = defaultdict(list)
    for row in tasks:
        task_groups[(row["cell_id"], row["analysis_variant"])].append(row)
    for row in tasksets:
        result_groups[(row["cell_id"], row["analysis_variant"])].append(row)
    for row in variants:
        key = (row["cell_id"], row["variant"])
        task_members = task_groups.get(key, [])
        result_members = result_groups.get(key, [])
        row.update({
            "mean_candidate_task_count": (
                sum(int(item["n_tasks_candidate_found"]) for item in result_members) / len(result_members)
                if result_members else None
            ),
            "first_failed_priority_observation_count": sum(bool(item["first_failed_priority"]) for item in result_members),
            "checked_w_total": sum(int(item["checked_w_count"] or 0) for item in task_members),
            "checked_h_total": sum(int(item["checked_h_count"] or 0) for item in task_members),
            "checked_q_total": sum(int(item["checked_q_count"] or 0) for item in task_members),
            "envelope_call_total": sum(int(item["envelope_call_count"] or 0) for item in task_members),
        })
    task_rows = []
    for relation, left, right, dominance in CORE2_RELATIONS:
        task_rows.extend(compare_tasks(tasks, left, right, relation, assume_dominance=dominance))
    tightness_by_set = {
        (row["cell_id"], row["taskset_id"], row["relation"]): row
        for row in by_taskset(task_rows)
    }
    taskset_rows = []
    for relation, left, right, dominance in CORE2_RELATIONS:
        for pair in _certification_pairs(tasksets, left, right):
            tight = tightness_by_set.get((pair["cell_id"], pair["taskset_id"], relation), {})
            taskset_rows.append({
                "cell_id": pair["cell_id"], "taskset_id": pair["taskset_id"],
                "exact_e0": pair["exact_e0"], "relation": relation,
                "left_variant": left, "right_variant": right,
                "dominance_expected": dominance,
                "common_candidate_task_count": tight.get("common_candidate_task_count", 0),
                "tighter_count": tight.get("tighter_count", 0),
                "equal_count": tight.get("equal_count", 0),
                "violation_count": tight.get("violation_count", 0),
                "mean_reduction": tight.get("mean_reduction"),
                "median_reduction": tight.get("median_reduction"),
                "max_reduction": tight.get("max_reduction"),
                "mean_normalized_reduction": tight.get("mean_normalized_reduction"),
                "left_status": pair["left_status"], "right_status": pair["right_status"],
                "left_certified": pair["left_certified"], "right_certified": pair["right_certified"],
                "certification_gain": pair["certification_gain"],
                "certification_loss": pair["certification_loss"],
            })
    dominance_rows = [row for row in task_rows if row["dominance_expected"]]
    dominance_summary = []
    for relation in sorted({row["relation"] for row in dominance_rows}):
        members = [row for row in dominance_rows if row["relation"] == relation]
        dominance_summary.append({
            "relation": relation, "common_candidate_task_count": len(members),
            "tighter_count": sum(row["status"] == "TIGHTER" for row in members),
            "equal_count": sum(row["status"] == "EQUAL" for row in members),
            "violation_count": sum(row["status"] == "VIOLATION" for row in members),
        })
    dependency_summary = []
    for status in sorted({row["dependency_check_status"] for row in dependencies}):
        members = [row for row in dependencies if row["dependency_check_status"] == status]
        dependency_summary.append({
            "dependency_check_status": status, "count": len(members),
            "applicable_count": sum(row["applicable"] == "True" for row in members),
            "source_certified_count": sum(row["source_certified"] == "True" for row in members),
            "fallback_count": sum(row["fallback_used"] == "True" for row in members),
        })
    _write_dynamic(root / "summary.csv", variants)
    _write_dynamic(root / "core2_variant_summary.csv", variants)
    _write_dynamic(root / "core2_ablation_by_task.csv", task_rows)
    _write_dynamic(root / "core2_ablation_by_taskset.csv", taskset_rows)
    _write_dynamic(root / "core2_dependency_summary.csv", dependency_summary)
    _write_dynamic(root / "core2_dominance_summary.csv", dominance_summary)
    plots = core2_plot_rows(tasksets, tasks, task_rows, taskset_rows, dependencies)
    _write_dynamic(root / "core2_plot_data.csv", plots)
    summary = {
        "core": "CORE-2", "requested": len(requests), "terminal": len(tasksets),
        "variants": variants,
        "dependency_records": len(dependencies),
        "dominance_violations": sum(row["status"] == "VIOLATION" for row in dominance_rows),
        "formal_large_scale_run": False,
    }
    atomic_write_json(root / "summary.json", summary)
    write_file_hashes(root)
    return summary
