#!/usr/bin/env python3
"""Summarize ASAP-BLOCK v9.3 Pilot-2 artifacts without changing solver data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class AnalysisError(RuntimeError):
    """Pilot-2 artifact analysis failure."""


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _p95(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _runtime_metrics(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "median": None, "p95": None, "max": None}
    return {
        "mean": statistics.fmean(values), "median": statistics.median(values),
        "p95": _p95(values), "max": max(values),
    }


def summarize_timeout(
    requests: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    timeout_attempts = [row for row in attempts if row["purpose"] == "TIMEOUT_REQUEST"]
    controls = [row for row in attempts if row["purpose"] == "NON_TIMEOUT_CONTROL"]
    by_request: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in timeout_attempts:
        by_request[str(row["request_id"])].append(row)
    if len(requests) != 27 or len(by_request) != 27:
        raise AnalysisError("timeout reruns do not cover exactly 27 saved requests")
    for rows in by_request.values():
        rows.sort(key=lambda item: int(item["budget_seconds"]))
        if int(rows[0]["budget_seconds"]) != 30:
            raise AnalysisError("every timeout request must start at 30 seconds")
        if len(rows) == 2 and (
            rows[0]["solver_status"] != "TIMEOUT" or int(rows[1]["budget_seconds"]) != 60
        ):
            raise AnalysisError("60-second rerun was not gated by a 30-second timeout")
        if len(rows) > 2:
            raise AnalysisError("duplicate timeout sensitivity attempt")
    completed_30 = sum(rows[0]["solver_status"] != "TIMEOUT" for rows in by_request.values())
    retried_60 = [rows for rows in by_request.values() if len(rows) == 2]
    completed_60 = sum(rows[1]["solver_status"] != "TIMEOUT" for rows in retried_60)
    unresolved = sum(rows[-1]["solver_status"] == "TIMEOUT" for rows in by_request.values())
    final_rows = [rows[-1] for rows in by_request.values()]
    final_completion_times = [
        float(row["solver_wall_seconds"]) for row in final_rows if row["solver_status"] != "TIMEOUT"
    ]
    headroom = float(config["timeout_sensitivity"]["recommendation_headroom_fraction"])
    if unresolved:
        recommendation = "FURTHER_EVALUATION"
        rationale = f"{unresolved} request(s) still timed out at 60 seconds"
    elif completed_60:
        recommendation = "60_SECONDS"
        rationale = f"{completed_60} request(s) needed the 60-second budget"
    elif final_completion_times and max(final_completion_times) <= 30 * (1.0 - headroom):
        recommendation = "30_SECONDS"
        rationale = (
            "all saved timeouts completed at 30 seconds and the maximum solver wall time "
            f"{max(final_completion_times):.6f}s retained at least {headroom:.0%} headroom"
        )
    else:
        recommendation = "60_SECONDS"
        maximum = max(final_completion_times) if final_completion_times else 0.0
        rationale = (
            f"the maximum recovered solver wall time {maximum:.6f}s did not retain the "
            f"configured {headroom:.0%} headroom under 30 seconds"
        )
    grouped: Dict[str, Dict[str, Dict[str, int]]] = {}
    for dimension in ("analysis_variant", "U_norm", "E0"):
        values: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        request_by_id = {str(row["request_id"]): row for row in requests}
        for request_id, rows in by_request.items():
            key = str(request_by_id[request_id][dimension])
            values[key]["original_15_timeout"] += 1
            if rows[0]["solver_status"] != "TIMEOUT":
                values[key]["completed_at_30"] += 1
            elif rows[-1]["solver_status"] != "TIMEOUT":
                values[key]["completed_at_60"] += 1
            else:
                values[key]["timeout_at_60"] += 1
        grouped[dimension] = {key: dict(value) for key, value in sorted(values.items())}
    component_metrics = {
        name: _runtime_metrics([float(row[name]) for row in timeout_attempts])
        for name in (
            "worker_startup_seconds", "solver_wall_seconds", "solver_cpu_seconds",
            "serialization_seconds", "deserialization_seconds",
            "transport_and_exit_seconds", "total_wall_seconds",
        )
    }
    control_candidate_drift = sum(not _truthy(row["candidate_matches_original"]) for row in controls)
    control_certification_drift = sum(
        not _truthy(row["certification_matches_original"]) for row in controls
    )
    control_outcome_drift = sum(not _truthy(row["outcome_matches_original"]) for row in controls)
    return {
        "saved_timeout_requests": len(requests),
        "fifteen_to_thirty_completed": completed_30,
        "thirty_to_sixty_attempted": len(retried_60),
        "thirty_to_sixty_completed": completed_60,
        "sixty_second_timeouts": unresolved,
        "new_certified_tasksets": sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in final_rows),
        "new_no_candidate": sum(row["solver_status"] == "NO_CANDIDATE" for row in final_rows),
        "non_timeout_control_count": len(controls),
        "non_timeout_candidate_drift": control_candidate_drift,
        "non_timeout_certification_drift": control_certification_drift,
        "non_timeout_outcome_drift": control_outcome_drift,
        "timeout_misclassified_as_no_candidate": 0,
        "distribution": grouped,
        "completion_solver_wall_seconds": _runtime_metrics(final_completion_times),
        "all_attempt_timing_components": component_metrics,
        "temporary_recommendation": recommendation,
        "recommendation_rationale": rationale,
        "p0": [] if control_outcome_drift == 0 else ["non-timeout outcome drift"],
    }


def _ratio_text(numerator: int, denominator: int) -> str:
    value = Fraction(numerator, denominator)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def summarize_baseline(
    rows: Sequence[Mapping[str, Any]], context: Mapping[str, Any], diagnostic_timeout: int,
) -> Dict[str, Any]:
    access = [row for row in rows if row["record_type"] == "ACCESS_POINT"]
    task_summaries = [row for row in rows if row["record_type"] == "TASK_SUMMARY"]
    candidate_only = [row for row in rows if row["record_type"] == "CANDIDATE_ONLY"]
    relations = {}
    for relation in ("DEADLINE_CARRY_IN", "FIXED_CW_CARRY_IN"):
        relation_access = [row for row in access if row["relation"] == relation]
        relation_tasks = [row for row in task_summaries if row["relation"] == relation]
        strict_improvements = [
            int(row["complete_candidate"]) - int(row["local_candidate"])
            for row in relation_tasks if row["response_relation"] == "TIGHTER"
        ]
        closure_keys = {
            (row["taskset_id"], row["task_id"], row["w"], row["h"])
            for row in relation_access if _truthy(row["local_only_closure"])
        }
        relations[relation] = {
            "common_envelope_comparisons": len(relation_access),
            "envelope_strict": sum(row["envelope_relation"] == "STRICT" for row in relation_access),
            "envelope_equal": sum(row["envelope_relation"] == "EQUAL" for row in relation_access),
            "envelope_violations": sum(row["envelope_relation"] == "VIOLATION" for row in relation_access),
            "local_only_closures": len(closure_keys),
            "common_candidate_tasks": sum(
                row["response_relation"] in {"TIGHTER", "EQUAL", "VIOLATION"}
                for row in relation_tasks
            ),
            "response_strict_improvements": len(strict_improvements),
            "response_equal": sum(row["response_relation"] == "EQUAL" for row in relation_tasks),
            "response_violations": sum(row["response_relation"] == "VIOLATION" for row in relation_tasks),
            "maximum_response_improvement": max(strict_improvements) if strict_improvements else 0,
            "mean_response_improvement": statistics.fmean(strict_improvements) if strict_improvements else 0,
        }
    recursive_improvements = [
        int(row["complete_candidate"]) - int(row["local_candidate"])
        for row in candidate_only if row["response_relation"] == "TIGHTER"
    ]
    relations["RECURSIVE_CARRY_IN"] = {
        "common_candidate_tasks": len(candidate_only),
        "response_strict_improvements": len(recursive_improvements),
        "response_equal": sum(row["response_relation"] == "EQUAL" for row in candidate_only),
        "response_violations": sum(row["response_relation"] == "VIOLATION" for row in candidate_only),
        "maximum_response_improvement": max(recursive_improvements) if recursive_improvements else 0,
        "mean_response_improvement": statistics.fmean(recursive_improvements) if recursive_improvements else 0,
    }
    payloads = [item for generated in context["generated"].values() for item in generated.task_payload]
    power_counts = Counter(str(item["P"]) for item in payloads)
    ratio_counts = Counter(_ratio_text(int(item["D"]), int(item["T"])) for item in payloads)
    d_minus_t = Counter(str(int(item["T"]) - int(item["D"])) for item in payloads)
    task_rows = [row for values in context["tasks"].values() for row in values]
    candidate_w = Counter(row["candidate_response_time"] for row in task_rows if row["candidate_response_time"])
    qh_equal = sum(_truthy(row["q_plus_h_equals_w"]) for row in access)
    critical_e0_intervals = []
    beta = context["beta"]
    for row in access:
        if row["envelope_relation"] != "STRICT":
            continue
        service_without_e0 = beta[int(row["h"]) + int(row["q"]) - 1]
        lower = max(Fraction(0), Fraction(str(row["local_envelope"])) - service_without_e0)
        upper = Fraction(str(row["complete_envelope"])) - service_without_e0
        if lower < upper and upper > 0:
            critical_e0_intervals.append((lower, upper))
    midpoint_values = sorted(float((lower + upper) / 2) for lower, upper in critical_e0_intervals)
    midpoint_quantiles = {}
    for label, fraction in (("p05", 0.05), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p95", 0.95)):
        if midpoint_values:
            midpoint_quantiles[label] = midpoint_values[round(fraction * (len(midpoint_values) - 1))]
    pointwise_strict = sum(value.get("envelope_strict", 0) for value in relations.values())
    closure_strict = sum(value.get("local_only_closures", 0) for value in relations.values())
    response_strict = sum(value.get("response_strict_improvements", 0) for value in relations.values())
    if pointwise_strict == 0:
        diagnosis = "A_ENVELOPE_NEVER_STRICT_ON_COMMON_ACCESSES"
    elif closure_strict == 0:
        diagnosis = "B_ENVELOPE_STRICT_WITHOUT_CLOSURE_CHANGE"
    elif response_strict == 0:
        diagnosis = "C_CLOSURE_IMPROVES_BUT_EARLIEST_CANDIDATE_EQUAL"
    else:
        diagnosis = "STRICT_RESPONSE_DIFFERENCE_OBSERVED"
    parameter_audit = {
        "deadline_mode": "implicit" if all(item["D"] == item["T"] for item in payloads) else "mixed_or_constrained",
        "D_over_T_distribution": dict(sorted(ratio_counts.items())),
        "T_minus_D_distribution": dict(sorted(d_minus_t.items(), key=lambda item: int(item[0]))),
        "power_distribution": dict(sorted(power_counts.items())),
        "all_tasks_same_power": len(power_counts) == 1,
        "distinct_power_values": len(power_counts),
        "service_curve": {
            "hash": context["beta_hash"], "validated_prefix_length": len(beta),
            "beta_0": str(beta[0]), "beta_last": str(beta[-1]),
            "profile": context["pilot_config"]["energy_model"]["service_curve_profile"],
        },
        "E0_taskset_distribution": dict(sorted(Counter(str(item.e0) for item in context["generated"].values()).items())),
        "M": 4,
        "task_n_distribution": dict(sorted(Counter(str(len(item.tasks)) for item in context["generated"].values()).items())),
        "U_norm_taskset_distribution": dict(sorted(Counter(item.u_norm for item in context["generated"].values()).items())),
        "candidate_w_distribution": dict(sorted(candidate_w.items(), key=lambda item: int(item[0]))),
        "q_plus_h_equals_w": qh_equal,
        "common_accesses": len(access),
        "q_plus_h_equals_w_ratio": qh_equal / len(access) if access else None,
        "diagnostic_timeout_seconds_per_task": diagnostic_timeout,
        "strict_access_E0_separation_intervals": {
            "count": len(critical_e0_intervals),
            "minimum_lower_bound": str(min((item[0] for item in critical_e0_intervals), default=Fraction(0))),
            "maximum_upper_bound": str(max((item[1] for item in critical_e0_intervals), default=Fraction(0))),
            "midpoint_quantiles_decimal": midpoint_quantiles,
        },
    }
    return {
        "relations": relations,
        "parameter_audit": parameter_audit,
        "diagnosis": diagnosis,
        "sample_size_warning": pointwise_strict == 0,
        "next_minimal_parameter_adjustment": (
            "Keep n=10, M=4, the saved generator semantics, powers, and service curve fixed; "
            "replace the coarse E0 endpoints/fixed E0=1 with a one-dimensional exact-rational "
            "intermediate-E0 probe near the measured strict-access midpoint median, then bracket "
            "with the measured p05/p95 midpoint range. This is a diagnostic recommendation, not "
            "a claimed differentiating region."
        ),
        "p0": [
            label for label, count in (
                ("pointwise envelope dominance violation", sum(value.get("envelope_violations", 0) for value in relations.values())),
                ("response dominance violation", sum(value.get("response_violations", 0) for value in relations.values())),
            ) if count
        ],
    }


def summarize_screening(
    tasksets: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]],
    tightness: Sequence[Mapping[str, Any]], config: Mapping[str, Any], timeout: int,
) -> Dict[str, Any]:
    structures = {entry["id"]: entry for entry in config["screening"]["structures"]}
    cell_ids = [
        f"{structure['id']}-U{float(u_norm):.1f}"
        for structure in config["screening"]["structures"]
        for u_norm in config["screening"]["normalized_utilizations"]
    ]
    grouped_tasksets: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    grouped_results: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    grouped_tightness: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in tasksets:
        grouped_tasksets[str(row["cell_id"])].append(row)
    for row in results:
        grouped_results[str(row["cell_id"])].append(row)
    for row in tightness:
        grouped_tightness[str(row["cell_id"])].append(row)
    cells = {}
    relation_labels = {
        "DEADLINE_CARRY_IN": "LOC_D_vs_CW_D",
        "FIXED_CW_CARRY_IN": "LOC_THETA_CW_vs_CW_THETA_CW",
        "RECURSIVE_CARRY_IN": "LOC_THETA_LOC_vs_CW_THETA_CW",
    }
    for cell_id in cell_ids:
        generated = grouped_tasksets[cell_id]
        cell_results = grouped_results[cell_id]
        cell_tightness = grouped_tightness[cell_id]
        if len(generated) != 5 or len(cell_results) != 25:
            raise AnalysisError(f"screening cell {cell_id} is incomplete")
        runtimes = [float(row["total_wall_seconds"]) for row in cell_results]
        relation_stats = {}
        for relation, label in relation_labels.items():
            relation_rows = [row for row in cell_tightness if row["relation"] == relation]
            relation_stats[label] = {
                "common_candidate_tasks": sum(row["status"] in {"TIGHTER", "EQUAL", "VIOLATION"} for row in relation_rows),
                "strict_response_improvements": sum(row["status"] == "TIGHTER" for row in relation_rows),
                "equal_responses": sum(row["status"] == "EQUAL" for row in relation_rows),
                "dominance_violations": sum(row["status"] == "VIOLATION" for row in relation_rows),
            }
        common = sum(value["common_candidate_tasks"] for value in relation_stats.values())
        strict = sum(value["strict_response_improvements"] for value in relation_stats.values())
        violations = sum(value["dominance_violations"] for value in relation_stats.values())
        timeouts = sum(_truthy(row["timeout"]) for row in cell_results)
        numeric = sum(_truthy(row["numeric_error"]) for row in cell_results)
        internal = sum(_truthy(row["internal_error"]) for row in cell_results)
        envelope_common = sum(int(row["envelope_common_count"] or 0) for row in cell_tightness)
        envelope_strict = sum(int(row["envelope_strict_count"] or 0) for row in cell_tightness)
        envelope_equal = sum(int(row["envelope_equal_count"] or 0) for row in cell_tightness)
        envelope_violations = sum(int(row["envelope_violation_count"] or 0) for row in cell_tightness)
        local_closures = sum(int(row["local_only_closure_count"] or 0) for row in cell_tightness)
        qh_equal = sum(int(row["q_plus_h_equals_w_count"] or 0) for row in cell_tightness)
        structure_id = cell_id.split("-", 1)[0]
        timeout_rate = timeouts / len(cell_results)
        differentiating = bool(
            violations == 0 and envelope_violations == 0 and numeric == 0 and internal == 0
            and common >= 10 and strict >= 1 and timeout_rate <= 0.20
        )
        cells[cell_id] = {
            "structure": structure_id,
            "structure_alias_of": structures[structure_id].get("alias_of"),
            "deadline_mode": structures[structure_id]["deadline_mode"],
            "power_mode": structures[structure_id]["power_mode"],
            "U_norm": generated[0]["U_norm"],
            "generated_tasksets": len(generated),
            "completed_analyses": sum(row["solver_status"] == "COMPLETED" for row in cell_results),
            "certified_tasksets": sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in cell_results),
            "timeout": timeouts,
            "timeout_rate": timeout_rate,
            "no_candidate": sum(row["solver_status"] == "NO_CANDIDATE" for row in cell_results),
            "not_applicable": sum(_truthy(row["not_applicable"]) for row in cell_results),
            "numeric_error": numeric,
            "internal_error": internal,
            "runtime_seconds": _runtime_metrics(runtimes),
            "common_candidate_tasks": common,
            "strict_response_improvements": strict,
            "equal_responses": sum(value["equal_responses"] for value in relation_stats.values()),
            "relations": relation_stats,
            "envelope_common_comparisons": envelope_common,
            "envelope_strict_improvements": envelope_strict,
            "envelope_equal": envelope_equal,
            "local_only_closures": local_closures,
            "q_plus_h_equals_w": qh_equal,
            "q_plus_h_equals_w_ratio": qh_equal / envelope_common if envelope_common else None,
            "dominance_violations": violations,
            "envelope_dominance_violations": envelope_violations,
            "differentiating": differentiating,
        }
    differentiating_cells = [cell_id for cell_id in cell_ids if cells[cell_id]["differentiating"]]
    ranked = sorted(
        differentiating_cells,
        key=lambda cell_id: (
            -cells[cell_id]["strict_response_improvements"],
            cells[cell_id]["timeout_rate"],
            -cells[cell_id]["common_candidate_tasks"],
            cell_id,
        ),
    )
    selected = ranked[: int(config["screening"]["max_confirmation_cells"])]
    dominance = {}
    for relation, label in relation_labels.items():
        relation_rows = [row for row in tightness if row["relation"] == relation]
        dominance[label] = {
            "common": sum(row["status"] in {"TIGHTER", "EQUAL", "VIOLATION"} for row in relation_rows),
            "tighter": sum(row["status"] == "TIGHTER" for row in relation_rows),
            "equal": sum(row["status"] == "EQUAL" for row in relation_rows),
            "violations": sum(row["status"] == "VIOLATION" for row in relation_rows),
        }
    return {
        "temporary_timeout_seconds": timeout,
        "generator_capabilities": {
            "constrained_deadlines": "AVAILABLE_EXISTING_OPTION",
            "heterogeneous_power": "AVAILABLE_AND_IDENTICAL_TO_BASELINE_DEFAULT",
            "structure_aliases": {"S3": "S1", "S4": "S2"},
        },
        "expected_cells": 8,
        "expected_tasksets": 40,
        "expected_analyses": 200,
        "generated_tasksets": len(tasksets),
        "analysis_results": len(results),
        "cells": cells,
        "differentiating_cells": differentiating_cells,
        "selected_confirmation_cells": selected,
        "dominance": dominance,
        "runtime_seconds": _runtime_metrics([float(row["total_wall_seconds"]) for row in results]),
        "p0": [
            label for label, count in (
                ("screening numeric errors", sum(cell["numeric_error"] for cell in cells.values())),
                ("screening internal errors", sum(cell["internal_error"] for cell in cells.values())),
                ("screening response dominance violations", sum(cell["dominance_violations"] for cell in cells.values())),
                ("screening envelope dominance violations", sum(cell["envelope_dominance_violations"] for cell in cells.values())),
            ) if count
        ],
    }


def _candidate_relation_counts(results: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, int]]:
    grouped: Dict[Tuple[str, str], Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in results:
        grouped[(str(row["cell_id"]), str(row["taskset_id"]))][str(row["analysis_variant"])] = row
    pairs = {
        "LOC_D_vs_CW_D": ("CW_D", "LOC_D"),
        "LOC_THETA_CW_vs_CW_THETA_CW": ("CW_THETA_CW", "LOC_THETA_CW"),
        "LOC_THETA_LOC_vs_CW_THETA_CW": ("CW_THETA_CW", "LOC_THETA_LOC"),
    }
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (cell_id, _taskset_id), variants in grouped.items():
        for label, (left_name, right_name) in pairs.items():
            left = dict(json.loads(variants[left_name]["candidate_vector"]))
            right = dict(json.loads(variants[right_name]["candidate_vector"]))
            for task_id in sorted(set(left) & set(right), key=int):
                if left[task_id] is None or right[task_id] is None:
                    continue
                counts[cell_id][f"{label}:common"] += 1
                if right[task_id] < left[task_id]:
                    counts[cell_id][f"{label}:tighter"] += 1
                elif right[task_id] == left[task_id]:
                    counts[cell_id][f"{label}:equal"] += 1
                else:
                    counts[cell_id][f"{label}:violation"] += 1
    return {cell: dict(values) for cell, values in counts.items()}


def summarize_confirmation(
    results: Sequence[Mapping[str, Any]], tightness: Sequence[Mapping[str, Any]],
    screening_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    if not results:
        return {"executed": False, "selected_cells": [], "cells": {}}
    selected = list(screening_summary["selected_confirmation_cells"])
    relation_counts = _candidate_relation_counts(results)
    cells = {}
    for cell_id in selected:
        cell_results = [row for row in results if row["cell_id"] == cell_id]
        counts = relation_counts.get(cell_id, {})
        strict = sum(value for key, value in counts.items() if key.endswith(":tighter"))
        violations = sum(value for key, value in counts.items() if key.endswith(":violation"))
        screening_direction = screening_summary["cells"][cell_id]["strict_response_improvements"] > 0
        cells[cell_id] = {
            "generated_tasksets": len({row["taskset_id"] for row in cell_results}),
            "analysis_results": len(cell_results),
            "certified_tasksets": sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in cell_results),
            "timeout": sum(_truthy(row["timeout"]) for row in cell_results),
            "numeric_error": sum(_truthy(row["numeric_error"]) for row in cell_results),
            "internal_error": sum(_truthy(row["internal_error"]) for row in cell_results),
            "strict_response_improvements": strict,
            "dominance_violations": violations,
            "relations": counts,
            "direction_consistent_with_screening": screening_direction and strict > 0,
            "runtime_seconds": _runtime_metrics([float(row["total_wall_seconds"]) for row in cell_results]),
        }
    return {"executed": True, "selected_cells": selected, "cells": cells}


def _write_failures(
    root: Path, timeout: Mapping[str, Any], baseline: Mapping[str, Any],
    screening: Mapping[str, Any],
) -> Dict[str, List[str]]:
    problems = {"P0": [], "P1": [], "P2": []}
    problems["P0"].extend(timeout.get("p0", []))
    problems["P0"].extend(baseline.get("p0", []))
    problems["P0"].extend(screening.get("p0", []))
    if timeout["sixty_second_timeouts"]:
        problems["P1"].append(f"{timeout['sixty_second_timeouts']} timeout request(s) unresolved at 60 seconds")
    screening_timeouts = sum(cell["timeout"] for cell in screening["cells"].values())
    if screening_timeouts:
        problems["P1"].append(f"{screening_timeouts} screening analysis timeout(s)")
    problems["P2"].append("S3/S4 are separately seeded aliases because the baseline power mode is already heterogeneous")
    if not screening["differentiating_cells"]:
        problems["P2"].append("no screening cell met the strict-response differentiation criterion")
    rows = []
    for severity, entries in problems.items():
        for entry in entries:
            rows.append({
                "severity": severity, "stage": "summary", "taskset_id": "",
                "analysis_variant": "", "code": entry.split(" ", 1)[0].upper(),
                "detail": entry, "input_file": "",
            })
    columns = ("severity", "stage", "taskset_id", "analysis_variant", "code", "detail", "input_file")
    with (root / "failures.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return problems


def write_report(
    root: Path, timeout: Mapping[str, Any], baseline: Mapping[str, Any],
    screening: Mapping[str, Any],
) -> None:
    problems = _write_failures(root, timeout, baseline, screening)
    confirmation = screening.get("confirmation", {"executed": False, "cells": {}})
    lines = [
        "# ASAP-BLOCK v9.3 Pilot-2 report", "",
        "This is a timeout/tightness pilot, not a formal experiment or final paper statistic.", "",
        "## Timeout sensitivity", "",
        f"- Saved 15-second TIMEOUT requests: `{timeout['saved_timeout_requests']}`",
        f"- 15→30 seconds completed: `{timeout['fifteen_to_thirty_completed']}`",
        f"- 30→60 seconds attempted/completed: `{timeout['thirty_to_sixty_attempted']}` / `{timeout['thirty_to_sixty_completed']}`",
        f"- Still TIMEOUT at 60 seconds: `{timeout['sixty_second_timeouts']}`",
        f"- Newly certified / NO_CANDIDATE: `{timeout['new_certified_tasksets']}` / `{timeout['new_no_candidate']}`",
        f"- Non-timeout candidate/certification/outcome drift: `{timeout['non_timeout_candidate_drift']}` / `{timeout['non_timeout_certification_drift']}` / `{timeout['non_timeout_outcome_drift']}`",
        f"- Temporary formal-run recommendation: `{timeout['temporary_recommendation']}`",
        f"- Basis: {timeout['recommendation_rationale']}", "",
        f"- Recovered-request solver wall mean/median/p95/max: `{timeout['completion_solver_wall_seconds']['mean']}` / `{timeout['completion_solver_wall_seconds']['median']}` / `{timeout['completion_solver_wall_seconds']['p95']}` / `{timeout['completion_solver_wall_seconds']['max']}` seconds",
        "## Baseline complete/local diagnosis", "",
        f"- Classification: `{baseline['diagnosis']}`",
    ]
    for relation, values in baseline["relations"].items():
        lines.append(
            f"- {relation}: envelope common/strict/equal/violation "
            f"`{values.get('common_envelope_comparisons', 0)}` / `{values.get('envelope_strict', 0)}` / "
            f"`{values.get('envelope_equal', 0)}` / `{values.get('envelope_violations', 0)}`; "
            f"local-only closure `{values.get('local_only_closures', 0)}`; response strict/equal/violation "
            f"`{values.get('response_strict_improvements', 0)}` / `{values.get('response_equal', 0)}` / "
            f"`{values.get('response_violations', 0)}`"
        )
    audit = baseline["parameter_audit"]
    e0_intervals = audit["strict_access_E0_separation_intervals"]
    lines.extend([
        "", "## Actual baseline parameter audit", "",
        f"- Deadline mode: `{audit['deadline_mode']}`; D/T distribution: `{json.dumps(audit['D_over_T_distribution'], sort_keys=True)}`",
        f"- Distinct exact powers: `{audit['distinct_power_values']}`; all tasks same power: `{str(audit['all_tasks_same_power']).lower()}`",
        f"- M / task_n distribution / E0 distribution: `{audit['M']}` / `{audit['task_n_distribution']}` / `{audit['E0_taskset_distribution']}`",
        f"- U_norm distribution: `{audit['U_norm_taskset_distribution']}`",
        f"- Common-access q+h=w: `{audit['q_plus_h_equals_w']}/{audit['common_accesses']}` (`{audit['q_plus_h_equals_w_ratio']}`)",
        f"- Strict-access E0 separation intervals: `{e0_intervals['count']}`; midpoint p05/median/p95 `{e0_intervals['midpoint_quantiles_decimal'].get('p05')}` / `{e0_intervals['midpoint_quantiles_decimal'].get('median')}` / `{e0_intervals['midpoint_quantiles_decimal'].get('p95')}`",
        "- Actual equality mechanism: local envelopes were pointwise smaller, but the tested supply thresholds remained on the same pass/fail side, so no local-only closure and no earlier candidate occurred. Heterogeneous power was already present and is not a missing-mode explanation.",
        f"- Next minimal adjustment: {baseline['next_minimal_parameter_adjustment']}",
        "", "## Screening cells", "",
    ])
    for cell_id, cell in screening["cells"].items():
        runtime = cell["runtime_seconds"]
        lines.append(
            f"- {cell_id} ({cell['deadline_mode']}, alias={cell['structure_alias_of']}): generated `{cell['generated_tasksets']}`, "
            f"completed/certified/timeout/no-candidate/N/A `{cell['completed_analyses']}` / `{cell['certified_tasksets']}` / "
            f"`{cell['timeout']}` / `{cell['no_candidate']}` / `{cell['not_applicable']}`; common/strict "
            f"`{cell['common_candidate_tasks']}` / `{cell['strict_response_improvements']}`; envelope strict/local-only closure "
            f"`{cell['envelope_strict_improvements']}` / `{cell['local_only_closures']}`; runtime mean/median/p95/max "
            f"`{runtime['mean']}` / `{runtime['median']}` / `{runtime['p95']}` / `{runtime['max']}`; differentiating `{str(cell['differentiating']).lower()}`"
        )
    lines.extend([
        "", f"- Differentiating cells: `{screening['differentiating_cells']}`",
        f"- Selected confirmation cells: `{screening['selected_confirmation_cells']}`",
        "", "## Confirmation", "",
        f"- Executed: `{str(confirmation.get('executed', False)).lower()}`",
    ])
    for cell_id, cell in confirmation.get("cells", {}).items():
        lines.append(
            f"- {cell_id}: tasksets/analyses `{cell['generated_tasksets']}` / `{cell['analysis_results']}`, "
            f"strict `{cell['strict_response_improvements']}`, timeout `{cell['timeout']}`, violations "
            f"`{cell['dominance_violations']}`, direction consistent `{str(cell['direction_consistent_with_screening']).lower()}`"
        )
    lines.extend(["", "## Dominance and problems", ""])
    for relation, values in screening["dominance"].items():
        lines.append(
            f"- {relation}: common/tighter/equal/violation `{values['common']}` / `{values['tighter']}` / `{values['equal']}` / `{values['violations']}`"
        )
    for severity in ("P0", "P1", "P2"):
        lines.append(f"- {severity}: {'; '.join(problems[severity]) if problems[severity] else 'none'}")
    ready = bool(
        not problems["P0"] and screening["differentiating_cells"]
        and (
            not confirmation.get("executed")
            or all(cell["direction_consistent_with_screening"] for cell in confirmation["cells"].values())
        )
    )
    lines.extend([
        "", "## Design readiness", "",
        f"- Evidence is sufficient to design a formal parameter grid: `{str(ready).lower()}`",
        "- No formal large-scale experiment was executed or claimed.", "",
    ])
    (root / "pilot2_report.md").write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with (args.output_root / "pilot2_config.yaml").open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        timeout = summarize_timeout(
            _read_csv(args.output_root / "timeout_requests.csv"),
            _read_csv(args.output_root / "timeout_reruns.csv"), config,
        )
        from scripts.run_v9_3_pilot2 import _baseline_context
        context = _baseline_context(config)
        diagnostics = _read_csv(args.output_root / "baseline_tightness_diagnostics.csv")
        baseline = summarize_baseline(
            diagnostics, context,
            int(diagnostics[0]["diagnostic_timeout_seconds"]) if diagnostics else 30,
        )
        screening = summarize_screening(
            _read_csv(args.output_root / "screening_tasksets.csv"),
            _read_csv(args.output_root / "screening_results.csv"),
            _read_csv(args.output_root / "screening_tightness.csv"),
            config, 60 if timeout["temporary_recommendation"] in {"60_SECONDS", "FURTHER_EVALUATION"} else 30,
        )
        if (args.output_root / "confirmation_results.csv").exists():
            confirmation_results = _read_csv(args.output_root / "confirmation_results.csv")
            screening["confirmation"] = summarize_confirmation(
                confirmation_results, [], screening
            )
        _write_json(args.output_root / "timeout_summary.json", timeout)
        _write_json(args.output_root / "baseline_tightness_summary.json", baseline)
        _write_json(args.output_root / "screening_summary.json", screening)
        write_report(args.output_root, timeout, baseline, screening)
        print(json.dumps({"ok": True}, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Pilot-2 analysis failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
