from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.v9_3.config import canonical_json, domain_hash, fraction_text
from experiments.v9_3.core4_contract import (
    SENSITIVITY_REQUEST_COLUMNS,
    core4_analysis_input_hash,
)
from experiments.v9_3.paired_sweep import make_sweep, paired_analysis_id
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS,
    FAILURE_COLUMNS,
    GENERATED_COLUMNS,
    REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS,
    write_csv,
)


BASE_TASKS = [{
    "task_id": "0", "source_name": "task", "priority_rank": 0,
    "C": 1, "D": 5, "T": 7, "P": "1", "D_over_T": "5/7",
    "workload": "idle", "arrival_offset": 0,
}]


def _priority_hash(tasks: Sequence[Mapping[str, Any]]) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
        [{"task_id": row["task_id"], "priority_rank": row["priority_rank"]} for row in tasks],
    )


def _power_hash(tasks: Sequence[Mapping[str, Any]], *, sensitivity: bool) -> str:
    return domain_hash(
        (
            "ASAP_BLOCK:V9.3:SENSITIVITY_POWER_VECTOR:v1"
            if sensitivity else "ASAP_BLOCK:V9.3:POWER_VECTOR:v1"
        ),
        [{"task_id": row["task_id"], "P": row["P"]} for row in tasks],
    )


def _levels(parameter: str, second_unavailable: bool) -> list[Any]:
    if parameter == "initial_energy":
        return ["0", "1"]
    if parameter == "power_scale":
        return ["1", "2"]
    if parameter == "method":
        return ["CW_THETA_CW", "LOC_THETA_LOC"]
    if parameter == "service_curve":
        second = {
            "id": "service-1",
            "availability": "UNAVAILABLE" if second_unavailable else "AVAILABLE",
        }
        if second_unavailable:
            second["reason"] = "fixture dependency unavailable"
        else:
            second.update({"system_template": "fixture.yml", "horizon": 10})
        return [
            {
                "id": "service-0", "availability": "AVAILABLE",
                "system_template": "fixture.yml", "horizon": 10,
            },
            second,
        ]
    raise AssertionError(parameter)


def make_pair_fixture(
    parameter: str = "initial_energy",
    *,
    second_unavailable: bool = False,
    statuses: Sequence[str] = ("COMPLETED", "COMPLETED"),
    candidates: Sequence[int] = (5, 4),
) -> dict[str, list[dict[str, Any]]]:
    experiment_id = "core4-contract-fixture"
    levels = _levels(parameter, second_unavailable)
    sweep = make_sweep(experiment_id, parameter, levels)
    base_tasks = deepcopy(BASE_TASKS)
    base_task_json = canonical_json(base_tasks)
    base_priority_hash = _priority_hash(base_tasks)
    base_power_hash = _power_hash(base_tasks, sensitivity=False)
    requests: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    analysis_requests: list[dict[str, Any]] = []
    for index, level in enumerate(levels):
        availability = (
            level.get("availability", "AVAILABLE")
            if isinstance(level, dict) else "AVAILABLE"
        )
        variant = str(level) if parameter == "method" else "CW_THETA_CW"
        scale = Fraction(str(level)) if parameter == "power_scale" else Fraction(1)
        effective_tasks = deepcopy(base_tasks)
        for task in effective_tasks:
            task["P"] = fraction_text(Fraction(task["P"]) * scale)
        if parameter == "service_curve":
            declaration_id = level["id"]
            service_identity = f"service-identity-{index}" if availability == "AVAILABLE" else ""
            service_values = (
                canonical_json(["0", "1" if index == 0 else "2"])
                if availability == "AVAILABLE" else ""
            )
            relation = (
                "DEPENDENCY_UNAVAILABLE" if availability == "UNAVAILABLE"
                else "FIRST_LEVEL" if index == 0 else "RIGHT_STRONGER"
            )
        else:
            declaration_id = "service-base"
            service_identity = "service-identity-base"
            service_values = canonical_json(["0", "1"])
            relation = "NOT_APPLICABLE"
        exact_e0 = fraction_text(Fraction(str(level))) if parameter == "initial_energy" else "0"
        analysis_id = f"analysis-{index}"
        row = {
            "experiment_id": experiment_id,
            "sweep_id": sweep.sweep_id,
            "base_taskset_id": "taskset", "base_taskset_hash": "taskset-hash",
            "taskset_index": 0, "M": 4, "task_n": 1,
            "base_priority_hash": base_priority_hash,
            "base_power_hash": base_power_hash,
            "base_service_curve_identity": "service-identity-base",
            "base_task_input_json": base_task_json,
            "parameter_name": parameter,
            "ordered_parameter_levels": canonical_json(sweep.level_encodings),
            "level_index": index, "level_encoding": sweep.level_encodings[index],
            "variant": variant, "analysis_id": analysis_id,
            "analysis_input_hash": "", "exact_e0": exact_e0,
            "service_curve_declaration_id": declaration_id,
            "service_curve_identity": service_identity,
            "service_curve_values_json": service_values,
            "power_scale": fraction_text(scale),
            "analysis_power_hash": _power_hash(effective_tasks, sensitivity=True),
            "analysis_task_input_json": canonical_json(effective_tasks),
            "numerical_mode": "EXACT_RATIONAL", "availability": availability,
            "availability_reason": level.get("reason", "") if isinstance(level, dict) else "",
            "service_curve_relation_to_previous": relation,
            "paired_analysis_ids": "",
        }
        row["analysis_input_hash"] = core4_analysis_input_hash(row)
        requests.append(row)
        if availability != "AVAILABLE":
            continue
        status = statuses[index]
        results.append({
            "analysis_id": analysis_id, "request_id": f"request-{index}",
            "taskset_id": "taskset", "taskset_hash": "taskset-hash",
            "M": 4, "task_n": 1, "exact_e0": exact_e0,
            "analysis_variant": variant, "solver_status": status,
            "taskset_proven": status == "COMPLETED",
            "runtime_wall_seconds": "0.1", "outer_timeout": False,
        })
        analysis_requests.append({
            "request_id": f"request-{index}", "analysis_id": analysis_id,
            "cell_id": f"cell-{index}", "taskset_id": "taskset",
            "taskset_hash": "taskset-hash", "exact_e0": exact_e0,
            "variant": variant, "numerical_mode": "EXACT_RATIONAL",
            "timeout_seconds": 4, "retry_timeout_seconds": 8,
            "source_analysis_id": "", "request_status": "TERMINAL",
        })
        if status == "COMPLETED":
            tasks.append({
                "analysis_id": analysis_id, "taskset_id": "taskset", "task_id": "0",
                "priority_rank": 0, "C": 1, "D": 5, "T": 7,
                "P": effective_tasks[0]["P"],
                "task_solver_status": "CANDIDATE_FOUND",
                "candidate_response_time": candidates[index],
            })
    pair_variant = (
        "CW_THETA_CW->LOC_THETA_LOC" if parameter == "method" else "CW_THETA_CW"
    )
    pair_ids = [paired_analysis_id(
        sweep.sweep_id, "taskset-hash", pair_variant,
        sweep.level_encodings[0], sweep.level_encodings[1],
    )]
    for row in requests:
        row["paired_analysis_ids"] = canonical_json(pair_ids)
    generated = [{
        "generation_id": "generation", "taskset_id": "taskset", "taskset_index": 0,
        "generation_seed": 7, "M": 4, "task_n": 1,
        "taskset_hash": "taskset-hash", "priority_hash": base_priority_hash,
        "power_hash": base_power_hash, "task_input_json": base_task_json,
        "service_curve_reference": "service-identity-base",
    }]
    return {
        "sensitivity_requests": requests,
        "generated_tasksets": generated,
        "analysis_requests": analysis_requests,
        "analysis_attempts": [],
        "per_taskset_results": results,
        "per_task_results": tasks,
        "failures": [],
    }


def write_pair_fixture(root: Path, fixture: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    write_csv(root / "sensitivity_requests.csv", SENSITIVITY_REQUEST_COLUMNS, fixture["sensitivity_requests"])
    write_csv(root / "generated_tasksets.csv", GENERATED_COLUMNS, fixture["generated_tasksets"])
    write_csv(root / "analysis_requests.csv", REQUEST_COLUMNS, fixture["analysis_requests"])
    write_csv(root / "analysis_attempts.csv", ATTEMPT_COLUMNS, fixture["analysis_attempts"])
    write_csv(root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, fixture["per_taskset_results"])
    write_csv(root / "per_task_results.csv", TASK_RESULT_COLUMNS, fixture["per_task_results"])
    write_csv(root / "failures.csv", FAILURE_COLUMNS, fixture["failures"])
