"""Persisted-data-only aggregation for CORE-4 sensitivity experiments."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import canonical_json
from .core4_contract import (
    ValidatedCore4Rows,
    validate_core4_artifact_contract,
    validate_core4_hash_manifest,
    validate_core4_pairing,
    write_core4_hash_manifest,
)
from .monotonicity import (
    MonotonicityStatus,
    compare_paired_analyses,
    terminal_status_class,
)
from .paired_sweep import paired_analysis_id
from .result_writer import atomic_write_json, read_csv, write_csv


PAIR_COLUMNS = (
    "paired_analysis_id", "sweep_id", "base_taskset_id", "base_taskset_hash",
    "parameter_name", "variant", "left_level", "right_level",
    "left_analysis_id", "right_analysis_id", "monotonicity_status",
    "common_candidate_count", "tighter_count", "equal_count", "looser_count",
    "certification_gain", "certification_loss", "violation_reasons",
    "strict_reasons",
)

SUMMARY_COLUMNS = (
    "parameter_name", "level_index", "level_encoding", "variant",
    "taskset_count", "certified_count", "certification_ratio",
    "completed_count", "completed_certified_count", "completed_only_ratio",
    "candidate_count", "candidate_mean", "candidate_median", "candidate_p95",
    "timeout_count", "dependency_unavailable_count", "runtime_censored_count",
    "runtime_mean_seconds",
    "paired_count", "tighter_count", "equal_count", "looser_count",
    "certification_gain", "certification_loss", "monotonicity_violation_count",
)


def _truth(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _tasks_by_analysis(rows: Iterable[Mapping[str, Any]]) -> Dict[str, list[Mapping[str, Any]]]:
    result: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row["analysis_id"])].append(row)
    return result


def build_monotonicity_rows(
    root: Path | str, *, validated: ValidatedCore4Rows | None = None
) -> list[Dict[str, Any]]:
    root = Path(root)
    evidence = validated or validate_core4_pairing(root)
    requests = list(evidence.requests)
    results = {row["analysis_id"]: row for row in evidence.results}
    tasks = _tasks_by_analysis(evidence.tasks)
    groups: Dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for request in requests:
        key_variant = "METHOD_PAIR" if request["parameter_name"] == "method" else request["variant"]
        groups[(request["sweep_id"] + ":" + request["base_taskset_hash"], key_variant)].append(request)
    rows = []
    for (_, key_variant), members in sorted(groups.items()):
        members.sort(key=lambda row: int(row["level_index"]))
        for left_request, right_request in zip(members, members[1:]):
            left = results.get(left_request["analysis_id"])
            right = results.get(right_request["analysis_id"])
            parameter = left_request["parameter_name"]
            if parameter == "service_curve" and (
                left_request["availability"] != "AVAILABLE"
                or right_request["availability"] != "AVAILABLE"
            ):
                comparison = {
                    "monotonicity_status": MonotonicityStatus.DEPENDENCY_UNAVAILABLE.value,
                    "common_candidate_count": 0, "tighter_count": 0,
                    "equal_count": 0, "looser_count": 0,
                    "certification_gain": 0, "certification_loss": 0,
                    "violation_reasons": "", "strict_reasons": "",
                }
            else:
                if left is None or right is None:
                    raise RuntimeError(
                        "validated available CORE-4 pair is missing a terminal"
                    )
                direction = (
                    "COST_INCREASE" if parameter == "power_scale" else
                    "LOC_DOMINANCE" if parameter == "method" else
                    "RESOURCE_INCREASE"
                )
                comparison = compare_paired_analyses(
                    left, right,
                    tasks.get(left_request["analysis_id"], []),
                    tasks.get(right_request["analysis_id"], []),
                    direction=direction,
                )
            variant = (
                f"{left_request['variant']}->{right_request['variant']}"
                if parameter == "method" else key_variant
            )
            row = {
                "paired_analysis_id": paired_analysis_id(
                    left_request["sweep_id"], left_request["base_taskset_hash"],
                    variant, left_request["level_encoding"], right_request["level_encoding"],
                ),
                "sweep_id": left_request["sweep_id"],
                "base_taskset_id": left_request["base_taskset_id"],
                "base_taskset_hash": left_request["base_taskset_hash"],
                "parameter_name": parameter, "variant": variant,
                "left_level": left_request["level_encoding"],
                "right_level": right_request["level_encoding"],
                "left_analysis_id": left_request["analysis_id"],
                "right_analysis_id": right_request["analysis_id"],
                **comparison,
            }
            rows.append(row)
    return rows


def aggregate_core4(root: Path | str) -> Dict[str, Any]:
    root = Path(root)
    evidence = validate_core4_pairing(root)
    requests = list(evidence.requests)
    results = {row["analysis_id"]: row for row in evidence.results}
    task_rows = list(evidence.tasks)
    tasks = _tasks_by_analysis(task_rows)
    pairs = build_monotonicity_rows(root, validated=evidence)
    write_csv(root / "paired_parameter_results.csv", PAIR_COLUMNS, pairs)
    write_csv(root / "monotonicity_checks.csv", PAIR_COLUMNS, pairs)

    pair_by_right: Dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in pairs:
        target_variant = (
            row["variant"].split("->", 1)[1]
            if row["parameter_name"] == "method" and "->" in row["variant"]
            else row["variant"]
        )
        pair_by_right[(
            row["parameter_name"], row["right_level"], target_variant
        )].append(row)
    grouped: Dict[tuple[str, str, str, str], list[Mapping[str, str]]] = defaultdict(list)
    for request in requests:
        grouped[(
            request["parameter_name"], request["level_index"],
            request["level_encoding"], request["variant"],
        )].append(request)
    summary_rows = []
    for (parameter, level_index, level, variant), members in sorted(grouped.items()):
        available_members = [row for row in members if row["availability"] == "AVAILABLE"]
        level_results = [results[row["analysis_id"]] for row in available_members]
        completed = [
            row for row in level_results
            if row.get("solver_status") in {"COMPLETED", "NO_CANDIDATE"}
        ]
        candidates = [
            float(task["candidate_response_time"])
            for request in members for task in tasks.get(request["analysis_id"], [])
            if task.get("candidate_response_time") not in (None, "")
            and task.get("task_solver_status") == "CANDIDATE_FOUND"
        ]
        runtimes = [
            float(row["runtime_wall_seconds"]) for row in completed
            if row.get("runtime_wall_seconds") not in (None, "")
        ]
        relevant_pairs = pair_by_right.get((parameter, level, variant), [])
        statuses = Counter(row["monotonicity_status"] for row in relevant_pairs)
        certified = sum(_truth(row.get("taskset_proven")) for row in level_results)
        completed_certified = sum(_truth(row.get("taskset_proven")) for row in completed)
        total = len(members)
        available_total = len(available_members)
        unavailable_level = available_total == 0
        summary_rows.append({
            "parameter_name": parameter, "level_index": level_index,
            "level_encoding": level, "variant": variant,
            "taskset_count": total, "certified_count": certified,
            "certification_ratio": (
                None if unavailable_level else certified / available_total
            ),
            "completed_count": len(completed),
            "completed_certified_count": completed_certified,
            "completed_only_ratio": completed_certified / len(completed) if completed else None,
            "candidate_count": None if unavailable_level else len(candidates),
            "candidate_mean": mean(candidates) if candidates and not unavailable_level else None,
            "candidate_median": median(candidates) if candidates and not unavailable_level else None,
            "candidate_p95": _p95(candidates) if not unavailable_level else None,
            "timeout_count": sum(row.get("solver_status") == "TIMEOUT" for row in level_results),
            "dependency_unavailable_count": sum(row["availability"] != "AVAILABLE" for row in members),
            "runtime_censored_count": sum(
                row.get("solver_status") == "TIMEOUT" for row in level_results
            ),
            "runtime_mean_seconds": mean(runtimes) if runtimes else None,
            "paired_count": len(relevant_pairs),
            "tighter_count": sum(int(row["tighter_count"]) for row in relevant_pairs),
            "equal_count": sum(int(row["equal_count"]) for row in relevant_pairs),
            "looser_count": sum(int(row["looser_count"]) for row in relevant_pairs),
            "certification_gain": sum(int(row["certification_gain"]) for row in relevant_pairs),
            "certification_loss": sum(int(row["certification_loss"]) for row in relevant_pairs),
            "monotonicity_violation_count": statuses[MonotonicityStatus.VIOLATION.value],
        })
    write_csv(root / "sensitivity_summary.csv", SUMMARY_COLUMNS, summary_rows)
    plot_rows = []
    for row in summary_rows:
        unavailable_level = (
            int(row["dependency_unavailable_count"]) == int(row["taskset_count"])
        )
        for metric in (
            "certification_ratio", "completed_only_ratio", "candidate_mean",
            "candidate_median", "candidate_p95", "timeout_count",
            "runtime_mean_seconds", "monotonicity_violation_count",
        ):
            plot_rows.append({
                "plot": "core4_sensitivity", "parameter_name": row["parameter_name"],
                "level_index": row["level_index"], "level_encoding": row["level_encoding"],
                "variant": row["variant"], "metric": metric,
                "value": None if unavailable_level else row[metric],
            })
    write_csv(
        root / "core4_plot_data.csv",
        ("plot", "parameter_name", "level_index", "level_encoding", "variant", "metric", "value"),
        plot_rows,
    )
    status_counts = Counter(row["monotonicity_status"] for row in pairs)
    planned = len(requests)
    available = sum(row["availability"] == "AVAILABLE" for row in requests)
    unavailable = planned - available
    technical_ids = {
        row["analysis_id"]
        for row in evidence.results
        if terminal_status_class(
            row.get("solver_status"), outer_timeout=row.get("outer_timeout")
        ) == "TECHNICAL_FAILURE"
    }
    technical_ids.update(
        row["analysis_id"]
        for row in evidence.failures
        if row.get("severity") == "P0" and row.get("analysis_id")
    )
    summary = {
        "finite_sample_consistency_check_only": True,
        "planned_sensitivity_row_count": planned,
        "available_solver_request_count": available,
        "expected_terminal_count": available,
        "actual_terminal_count": len(evidence.results),
        "dependency_unavailable_row_count": unavailable,
        "technical_failure_count": len(technical_ids),
        "level_summaries": summary_rows,
        "paired_count": len(pairs),
        "monotonicity_status_counts": dict(sorted(status_counts.items())),
        "p0_monotonicity_violation_count": status_counts[MonotonicityStatus.VIOLATION.value],
    }
    atomic_write_json(root / "sensitivity_summary.json", summary)
    return summary


def analyze_core4_artifacts(root: Path | str) -> Dict[str, Any]:
    """Validate a completed persisted run before rebuilding its summaries."""

    validate_core4_artifact_contract(root)
    summary = aggregate_core4(root)
    write_core4_hash_manifest(root)
    validate_core4_hash_manifest(root, require_completed_files=True)
    return summary
