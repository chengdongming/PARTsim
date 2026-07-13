"""Pure comparison helpers; no solver calls and no statistical mutation."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping


def candidate_index(rows: Iterable[Mapping[str, str]]) -> Dict[tuple, Mapping[str, str]]:
    result = {}
    for row in rows:
        if row.get("task_solver_status") != "CANDIDATE_FOUND":
            continue
        key = (
            row["cell_id"], row["taskset_id"], row["analysis_variant"], row["task_id"]
        )
        if key in result:
            raise ValueError(f"duplicate task candidate {key}")
        result[key] = row
    return result


def compare_tasks(
    rows: Iterable[Mapping[str, str]],
    left_variant: str,
    right_variant: str,
    relation: str,
    *,
    assume_dominance: bool,
) -> list[Dict[str, Any]]:
    index = candidate_index(rows)
    bases = sorted({
        (cell, taskset_id, task_id)
        for cell, taskset_id, variant, task_id in index
        if variant == left_variant
    })
    output = []
    for cell, taskset_id, task_id in bases:
        left = index.get((cell, taskset_id, left_variant, task_id))
        right = index.get((cell, taskset_id, right_variant, task_id))
        if left is None or right is None:
            continue
        left_value = int(left["candidate_response_time"])
        right_value = int(right["candidate_response_time"])
        reduction = left_value - right_value
        status = "TIGHTER" if reduction > 0 else "EQUAL" if reduction == 0 else "VIOLATION"
        output.append({
            "cell_id": cell,
            "taskset_id": taskset_id,
            "exact_e0": left["exact_e0"],
            "relation": relation,
            "left_variant": left_variant,
            "right_variant": right_variant,
            "task_id": task_id,
            "priority_rank": left["priority_rank"],
            "left_candidate": left_value,
            "right_candidate": right_value,
            "reduction": reduction,
            "normalized_reduction": (reduction / left_value) if left_value else 0,
            "status": status,
            "dominance_expected": assume_dominance,
        })
    return output


def by_taskset(rows: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    groups: Dict[tuple, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (
            row["cell_id"], row["taskset_id"], row["exact_e0"], row["relation"],
            row["left_variant"], row["right_variant"], row["dominance_expected"],
        )
        groups.setdefault(key, []).append(row)
    output = []
    for key, members in sorted(groups.items()):
        reductions = [int(row["reduction"]) for row in members]
        output.append({
            "cell_id": key[0], "taskset_id": key[1], "exact_e0": key[2],
            "relation": key[3], "left_variant": key[4], "right_variant": key[5],
            "dominance_expected": key[6],
            "common_candidate_task_count": len(members),
            "tighter_count": sum(row["status"] == "TIGHTER" for row in members),
            "equal_count": sum(row["status"] == "EQUAL" for row in members),
            "violation_count": sum(row["status"] == "VIOLATION" for row in members),
            "mean_reduction": sum(reductions) / len(reductions),
            "median_reduction": _median(reductions),
            "max_reduction": max(reductions),
            "mean_normalized_reduction": sum(float(row["normalized_reduction"]) for row in members) / len(members),
        })
    return output


def _median(values: list[int | float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2
