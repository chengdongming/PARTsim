"""Soundness classification and empirical tightness aggregation for CORE-3."""

from __future__ import annotations

from collections import Counter, defaultdict
from enum import Enum
import math
from statistics import mean, median
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .simulation_result import SimulationStatus


class SoundnessClass(str, Enum):
    RTA_PASS_SIM_PASS = "RTA_PASS_SIM_PASS"
    RTA_PASS_SIM_FAIL = "RTA_PASS_SIM_FAIL"
    RTA_FAIL_SIM_PASS = "RTA_FAIL_SIM_PASS"
    RTA_FAIL_SIM_FAIL = "RTA_FAIL_SIM_FAIL"
    RTA_PASS_SIM_CENSORED = "RTA_PASS_SIM_CENSORED"
    RTA_FAIL_SIM_CENSORED = "RTA_FAIL_SIM_CENSORED"
    RTA_TIMEOUT = "RTA_TIMEOUT"
    SIM_TIMEOUT_OR_ERROR = "SIM_TIMEOUT_OR_ERROR"


def _truth(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def classify_soundness(
    rta_row: Mapping[str, Any],
    simulation_status: str,
    *,
    release_e0_valid: bool = True,
) -> SoundnessClass:
    if str(rta_row.get("solver_status")) == "TIMEOUT":
        return SoundnessClass.RTA_TIMEOUT
    if (
        simulation_status in {
            SimulationStatus.RUNTIME_TIMEOUT.value,
            SimulationStatus.INTERNAL_ERROR.value,
        }
        or not release_e0_valid
    ):
        return SoundnessClass.SIM_TIMEOUT_OR_ERROR
    rta_pass = bool(
        _truth(rta_row.get("taskset_proven"))
        and str(rta_row.get("certification_status")) == "CERTIFIED_TASKSET"
    )
    if simulation_status == SimulationStatus.PASS_OBSERVED.value:
        return (
            SoundnessClass.RTA_PASS_SIM_PASS
            if rta_pass else SoundnessClass.RTA_FAIL_SIM_PASS
        )
    if simulation_status == SimulationStatus.DEADLINE_MISS.value:
        return (
            SoundnessClass.RTA_PASS_SIM_FAIL
            if rta_pass else SoundnessClass.RTA_FAIL_SIM_FAIL
        )
    if simulation_status == SimulationStatus.HORIZON_INSUFFICIENT.value:
        return (
            SoundnessClass.RTA_PASS_SIM_CENSORED
            if rta_pass else SoundnessClass.RTA_FAIL_SIM_CENSORED
        )
    raise ValueError(f"unknown simulation status: {simulation_status}")


def tightness_row(
    rta_task_row: Mapping[str, Any],
    simulation_task_row: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    candidate_raw = rta_task_row.get("candidate_response_time")
    simulated_raw = simulation_task_row.get("r_sim_max")
    if candidate_raw in (None, "") or simulated_raw in (None, ""):
        return None
    if str(rta_task_row.get("task_solver_status")) != "CANDIDATE_FOUND":
        return None
    if not _truth(simulation_task_row.get("tightness_eligible")):
        return None
    candidate, simulated = int(candidate_raw), int(simulated_raw)
    if simulated <= 0:
        return None
    deadline = int(rta_task_row["D"])
    gap = candidate - simulated
    return {
        "analysis_id": rta_task_row["analysis_id"],
        "cell_id": rta_task_row["cell_id"],
        "taskset_id": rta_task_row["taskset_id"],
        "exact_e0": rta_task_row["exact_e0"],
        "analysis_variant": rta_task_row["analysis_variant"],
        "task_id": rta_task_row["task_id"],
        "priority_rank": rta_task_row["priority_rank"],
        "D": deadline,
        "r_rta": candidate,
        "r_sim_max": simulated,
        "absolute_gap": gap,
        "normalized_gap": gap / simulated,
        "ratio": candidate / simulated,
        "slack_to_deadline": deadline - candidate,
        "exact_equality": candidate == simulated,
    }


def _p95(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def distribution(values: Iterable[float]) -> Dict[str, Optional[float]]:
    data = list(values)
    return {
        "mean": mean(data) if data else None,
        "median": median(data) if data else None,
        "p95": _p95(data),
        "max": max(data) if data else None,
    }


def aggregate_tightness(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    by_taskset: Dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_taskset[(str(row["taskset_id"]), str(row["analysis_variant"]))].append(row)
    taskset_rows = []
    for (taskset_id, variant), values in sorted(by_taskset.items()):
        taskset_rows.append({
            "taskset_id": taskset_id,
            "analysis_variant": variant,
            "task_count": len(values),
            "mean_absolute_gap": mean(float(row["absolute_gap"]) for row in values),
            "max_absolute_gap": max(float(row["absolute_gap"]) for row in values),
            "mean_normalized_gap": mean(float(row["normalized_gap"]) for row in values),
            "mean_ratio": mean(float(row["ratio"]) for row in values),
            "exact_equality_count": sum(_truth(row["exact_equality"]) for row in values),
        })

    by_method: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_method[str(row["analysis_variant"])].append(row)
    method_summary = {}
    for method in ("CW_THETA_CW", "LOC_THETA_LOC"):
        values = by_method.get(method, [])
        method_summary[method] = {
            "task_count": len(values),
            "taskset_count": len({row["taskset_id"] for row in values}),
            "absolute_gap": distribution(float(row["absolute_gap"]) for row in values),
            "normalized_gap": distribution(float(row["normalized_gap"]) for row in values),
            "ratio": distribution(float(row["ratio"]) for row in values),
            "exact_equality_count": sum(_truth(row["exact_equality"]) for row in values),
        }

    indexed = {
        (str(row["taskset_id"]), str(row["task_id"]), str(row["analysis_variant"])): row
        for row in rows
    }
    comparison = Counter()
    common = {
        (taskset_id, task_id)
        for taskset_id, task_id, method in indexed
        if method == "CW_THETA_CW"
        and (taskset_id, task_id, "LOC_THETA_LOC") in indexed
    }
    for taskset_id, task_id in common:
        cw = indexed[(taskset_id, task_id, "CW_THETA_CW")]
        loc = indexed[(taskset_id, task_id, "LOC_THETA_LOC")]
        if int(cw["r_rta"]) == int(loc["r_rta"]) and not math.isclose(
            float(cw["absolute_gap"]), float(loc["absolute_gap"]), abs_tol=0.0
        ):
            raise ValueError("equal CW/LOC candidates produced unequal tightness")
        loc_gap, cw_gap = float(loc["absolute_gap"]), float(cw["absolute_gap"])
        comparison[
            "LOC_GAP_LT_CW" if loc_gap < cw_gap else
            "LOC_GAP_EQ_CW" if loc_gap == cw_gap else
            "LOC_GAP_GT_CW"
        ] += 1
    summary = {
        "methods": method_summary,
        "loc_vs_cw_common_task_count": len(common),
        "loc_gap_lt_cw": comparison["LOC_GAP_LT_CW"],
        "loc_gap_eq_cw": comparison["LOC_GAP_EQ_CW"],
        "loc_gap_gt_cw": comparison["LOC_GAP_GT_CW"],
    }
    return taskset_rows, summary
