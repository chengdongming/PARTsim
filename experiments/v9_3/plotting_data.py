"""Long-form plot-data exports constructed only from persisted result CSVs."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def _exact_x(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return int(numerator) / int(denominator)
    return float(value)


def core1_plot_rows(
    tasksets: Iterable[Mapping[str, str]],
    comparisons: Iterable[Mapping[str, Any]],
    certification: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for row in tasksets:
        rows.extend((
            {"plot": "certification_ratio", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "x": row["utilization"], "y": 1 if row["taskset_proven"] == "True" else 0, "outcome": row["solver_status"]},
            {"plot": "certification_ratio_e0", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "x": _exact_x(row["exact_e0"]), "y": 1 if row["taskset_proven"] == "True" else 0, "outcome": row["solver_status"]},
            {"plot": "runtime", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "x": row["utilization"], "y": row["runtime_wall_seconds"], "outcome": row["solver_status"]},
            {"plot": "timeout_rate", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "utilization": row["utilization"], "exact_e0": row["exact_e0"], "variant": row["analysis_variant"], "x": row["utilization"], "y": 1 if row["solver_status"] == "TIMEOUT" else 0, "outcome": row["solver_status"]},
        ))
    for row in comparisons:
        common = {
            "cell_id": row["cell_id"],
            "taskset_id": row["taskset_id"], "utilization": "",
            "exact_e0": row["exact_e0"], "variant": row["right_variant"],
            "outcome": row["status"],
        }
        rows.append({"plot": "loc_vs_cw_scatter", **common, "x": row["left_candidate"], "y": row["right_candidate"]})
        rows.append({"plot": "response_reduction_distribution", **common, "x": row["reduction"], "y": 1})
    for row in certification:
        rows.append({
            "plot": "certification_outcome_matrix", "cell_id": row["cell_id"],
            "taskset_id": row["taskset_id"], "utilization": "",
            "exact_e0": row["exact_e0"], "variant": row["right_variant"],
            "x": int(row["left_certified"]), "y": int(row["right_certified"]),
            "outcome": f"{int(row['left_certified'])}{int(row['right_certified'])}",
        })
    return rows


def core2_plot_rows(
    tasksets: Iterable[Mapping[str, str]],
    task_results: Iterable[Mapping[str, str]],
    comparisons: Iterable[Mapping[str, Any]],
    taskset_comparisons: Iterable[Mapping[str, Any]],
    dependencies: Iterable[Mapping[str, str]],
) -> list[dict[str, Any]]:
    task_results = list(task_results)
    rows = []
    for row in tasksets:
        rows.extend((
            {"plot": "variant_certification", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["analysis_variant"], "relation": "", "x": row["utilization"], "y": 1 if row["taskset_proven"] == "True" else 0, "outcome": row["solver_status"]},
            {"plot": "variant_runtime", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["analysis_variant"], "relation": "", "x": row["utilization"], "y": row["runtime_wall_seconds"], "outcome": row["solver_status"]},
            {"plot": "first_failed_priority", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["analysis_variant"], "relation": "", "x": row["utilization"], "y": row["first_failed_priority"], "outcome": row["solver_status"]},
        ))
        members = [task for task in task_results if task["analysis_id"] == row["analysis_id"]]
        rows.append({
            "plot": "envelope_search_cost", "cell_id": row["cell_id"],
            "taskset_id": row["taskset_id"], "variant": row["analysis_variant"],
            "relation": "", "x": row["utilization"],
            "y": sum(int(task["envelope_call_count"] or 0) for task in members),
            "outcome": row["solver_status"],
        })
    for row in comparisons:
        rows.append({"plot": "ablation", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["right_variant"], "relation": row["relation"], "x": row["priority_rank"], "y": row["reduction"], "outcome": row["status"]})
    for row in taskset_comparisons:
        outcome = "GAIN" if row["certification_gain"] else "LOSS" if row["certification_loss"] else "UNCHANGED"
        rows.append({"plot": "ablation_gain_loss", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["right_variant"], "relation": row["relation"], "x": _exact_x(str(row["exact_e0"])), "y": int(row["certification_gain"]) - int(row["certification_loss"]), "outcome": outcome})
    for row in dependencies:
        rows.append({"plot": "dependency_applicability", "cell_id": row["cell_id"], "taskset_id": row["taskset_id"], "variant": row["target_variant"], "relation": "FIXED_CW_DEPENDENCY", "x": row["target_e0"], "y": 1 if row["applicable"] == "True" else 0, "outcome": row["dependency_check_status"]})
    return rows
