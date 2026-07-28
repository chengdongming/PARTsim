#!/usr/bin/env python3
"""Strict schema3 observability validation shared by B4 audit paths."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent
CONTRACT_PATHS = {
    version: B4_DIR / f"observability_summary_contract_v{version}.json"
    for version in (1, 2)
}
CONTRACTS = {
    version: json.loads(path.read_text(encoding="utf-8"))
    for version, path in CONTRACT_PATHS.items()
}
CONTRACT_SHA256S = {
    version: hashlib.sha256(path.read_bytes()).hexdigest()
    for version, path in CONTRACT_PATHS.items()
}
# Historical aliases remain v1 so existing v2 protocol identities and callers
# are byte/behavior compatible.
CONTRACT_PATH = CONTRACT_PATHS[1]
CONTRACT_SHA256 = CONTRACT_SHA256S[1]
CONTRACT = CONTRACTS[1]


class ObservabilityValidationError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ObservabilityValidationError(message)


def _counter(value, name):
    _require(
        type(value) is int and value >= 0,
        f"{name} must be a nonnegative integer",
    )
    return value


def _energy(value, name):
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0,
        f"{name} must be a finite nonnegative JSON number",
    )
    return float(value)


def _approximately_equal(lhs, rhs):
    return abs(lhs - rhs) <= 1e-9 * max(
        1.0, abs(lhs), abs(rhs)
    )


def _field_names(contract, contract_field):
    return tuple(
        item["name"] for item in contract[contract_field]
    )


def contract_identity(version):
    _require(version in CONTRACTS, "unsupported observability contract version")
    return {
        "contract": CONTRACTS[version],
        "path": CONTRACT_PATHS[version],
        "sha256": CONTRACT_SHA256S[version],
    }


def adjudicable_jobs_from_taskset(taskset_document, horizon_ms):
    """Independently derive jobs whose absolute deadline is at or before H_B4."""
    _require(type(horizon_ms) is int and horizon_ms > 0, "invalid adjudication horizon")
    tasks = taskset_document.get("taskset") if isinstance(taskset_document, dict) else None
    _require(isinstance(tasks, list), "taskset must contain a task list")
    release_horizon = taskset_document.get("release_horizon", horizon_ms)
    _require(
        type(release_horizon) is int and release_horizon > 0,
        "taskset release horizon is invalid",
    )
    expected = {}
    for task in tasks:
        _require(isinstance(task, dict), "taskset item is not an object")
        name = task.get("name")
        period = task.get("iat")
        deadline = task.get("deadline", period)
        offset = task.get("ph", 0)
        params = task.get("params", "")
        if isinstance(params, str):
            for item in params.strip().strip('"').split(","):
                if "=" not in item:
                    continue
                key, value = (part.strip().strip('"') for part in item.split("=", 1))
                if key == "arrival_offset":
                    try:
                        offset = int(value)
                    except ValueError as exc:
                        raise ObservabilityValidationError(
                            f"taskset arrival offset is invalid: {name}"
                        ) from exc
        _require(
            isinstance(name, str) and name and type(period) is int and period > 0
            and type(deadline) is int and deadline > 0
            and type(offset) is int and offset >= 0,
            f"taskset timing parameters are invalid: {name}",
        )
        last_release = min(release_horizon - 1, horizon_ms - deadline)
        count = 0 if last_release < offset else ((last_release - offset) // period) + 1
        expected[name] = count
    return expected


def task_ranks_from_taskset(taskset_document):
    """Recreate C++ RM order using (period, creation-order task number)."""
    _require(
        isinstance(taskset_document, dict),
        "taskset root must be an object",
    )
    tasks = taskset_document.get("taskset")
    _require(
        isinstance(tasks, list)
        and len(tasks) == CONTRACT["invariants"]["task_count"],
        "taskset must contain exactly ten tasks",
    )
    sortable = []
    names = set()
    for task_number, task in enumerate(tasks):
        _require(isinstance(task, dict), "taskset item is not an object")
        name = task.get("name")
        period = task.get("iat")
        _require(
            isinstance(name, str)
            and name
            and name not in names,
            "taskset task identity is invalid or duplicated",
        )
        _require(
            type(period) is int and period > 0,
            f"taskset period is invalid: {name}",
        )
        names.add(name)
        sortable.append((period, task_number, name))
    sortable.sort(key=lambda item: (item[0], item[1]))
    return {
        name: rank
        for rank, (_period, _task_number, name)
        in enumerate(sortable)
    }


def validate_schema3_summary(
    document,
    *,
    expected_horizon_ms,
    initial_energy_j,
    capacity_j,
    processor_count,
    expected_task_ranks=None,
    taskset_document=None,
    expected_contract_version=None,
):
    _require(isinstance(document, dict), "result is not an object")
    observed_contract_version = document.get(
        "observability_summary_contract_version"
    )
    _require(
        type(observed_contract_version) is int
        and observed_contract_version in CONTRACTS,
        "observability contract version mismatch",
    )
    if expected_contract_version is not None:
        _require(
            observed_contract_version == expected_contract_version,
            "observability contract version mismatch",
        )
    contract = CONTRACTS[observed_contract_version]
    _require(
        type(document.get("trace_schema_version")) is int
        and document["trace_schema_version"]
        == contract["trace_schema_version"],
        "trace schema is not schema3",
    )
    _require(
        type(document.get(
            "observability_summary_contract_version"
        )) is int
        and document["observability_summary_contract_version"]
        == contract["contract_version"],
        "observability contract version mismatch",
    )
    horizon = document.get("observability_summary_horizon_ms")
    _require(
        type(horizon) is int
        and horizon > 0
        and horizon == expected_horizon_ms,
        "observability summary horizon mismatch",
    )
    _require(
        type(processor_count) is int and processor_count > 0,
        "processor count is invalid",
    )

    mechanism_fields = _field_names(
        contract,
        "mechanism_summary_fields"
    )
    mechanism = document.get("mechanism_summary")
    _require(
        isinstance(mechanism, dict)
        and set(mechanism) == set(mechanism_fields),
        "mechanism_summary fields mismatch",
    )
    mechanism = {
        name: _counter(
            mechanism[name], f"mechanism_summary.{name}"
        )
        for name in mechanism_fields
    }
    _require(
        mechanism["observed_decision_ticks"] == horizon
        and mechanism["bypass_opportunity_ticks"] <= horizon
        and mechanism["actual_bypass_ticks"]
        <= mechanism["bypass_opportunity_ticks"]
        and mechanism["low_priority_bypass_core_ticks"]
        <= mechanism["actual_bypass_ticks"] * processor_count
        and mechanism["hp_dispatch_demand_ticks"] <= horizon
        and mechanism["hp_energy_blocked_ticks"]
        <= mechanism["hp_dispatch_demand_ticks"]
        and mechanism["hp_energy_blocked_job_ticks"]
        <= mechanism["hp_energy_blocked_ticks"]
        * min(processor_count, 4),
        "mechanism_summary bounds mismatch",
    )
    if observed_contract_version == 2:
        _require(
            mechanism["sync_batch_evaluation_ticks"] <= horizon
            and mechanism["sync_batch_reject_ticks"]
            <= mechanism["sync_batch_evaluation_ticks"]
            and mechanism["alap_deferral_opportunity_ticks"] <= horizon
            and mechanism["positive_slack_deferral_ticks"]
            <= mechanism["alap_deferral_opportunity_ticks"]
            and mechanism["st_charging_opportunity_ticks"] <= horizon
            and mechanism["st_slack_charging_wait_ticks"]
            <= mechanism["st_charging_opportunity_ticks"],
            "version 2 mechanism_summary bounds mismatch",
        )

    energy_fields = _field_names(contract, "energy_summary_fields")
    energy = document.get("energy_summary")
    _require(
        isinstance(energy, dict)
        and set(energy) == set(energy_fields),
        "energy_summary fields mismatch",
    )
    energy_counter_fields = {
        "battery_empty_ticks",
        "battery_full_ticks",
        "observed_energy_intervals",
    }
    energy_values = {
        name: (
            _counter(energy[name], f"energy_summary.{name}")
            if name in energy_counter_fields
            else _energy(energy[name], f"energy_summary.{name}")
        )
        for name in energy_fields
    }
    e0 = _energy(initial_energy_j, "initial_energy_j")
    emax = _energy(capacity_j, "capacity_j")
    _require(e0 <= emax, "initial energy exceeds capacity")
    _require(
        _approximately_equal(
            energy_values["offered_energy_j"],
            energy_values["credited_energy_j"]
            + energy_values["clipped_energy_j"],
        ),
        "offered energy does not reconcile",
    )
    _require(
        _approximately_equal(
            e0
            + energy_values["credited_energy_j"]
            - energy_values["consumed_energy_j"],
            energy_values["battery_final_j"],
        ),
        "battery conservation mismatch",
    )
    tolerance = 1e-9 * max(
        1.0,
        e0,
        emax,
        *(
            value
            for name, value in energy_values.items()
            if name not in energy_counter_fields
        ),
    )
    _require(
        energy_values["battery_min_j"] >= -tolerance
        and energy_values["battery_min_j"]
        <= energy_values["battery_final_j"] + tolerance
        and energy_values["battery_final_j"]
        <= energy_values["battery_max_j"] + tolerance
        and energy_values["battery_max_j"] <= emax + tolerance
        and energy_values["observed_energy_intervals"] == horizon
        and energy_values["battery_empty_ticks"]
        <= energy_values["observed_energy_intervals"]
        and energy_values["battery_full_ticks"]
        <= energy_values["observed_energy_intervals"],
        "energy_summary bounds mismatch",
    )

    task_fields = _field_names(contract, "per_task_summary_fields")
    per_task = document.get("per_task_summary")
    _require(
        isinstance(per_task, list)
        and len(per_task) == contract["invariants"]["task_count"],
        "per_task_summary task count mismatch",
    )
    if expected_task_ranks is not None:
        _require(
            isinstance(expected_task_ranks, dict)
            and set(expected_task_ranks)
            == {
                item.get("task_name")
                for item in per_task
                if isinstance(item, dict)
            },
            "taskset/result task identity join mismatch",
        )
    seen_names = set()
    total_executed = 0
    expected_adjudicable = None
    if observed_contract_version == 2:
        _require(taskset_document is not None, "version 2 requires taskset adjudication input")
        expected_adjudicable = adjudicable_jobs_from_taskset(
            taskset_document, horizon
        )
    for expected_rank, task in enumerate(per_task):
        _require(
            isinstance(task, dict)
            and set(task) == set(task_fields),
            "per_task_summary item fields mismatch",
        )
        name = task["task_name"]
        _require(
            isinstance(name, str)
            and name
            and name not in seen_names,
            "per-task identity is invalid or duplicated",
        )
        seen_names.add(name)
        reported_rank = task["priority_rank"]
        _require(
            type(reported_rank) is int
            and reported_rank == expected_rank,
            "reported ranks are not ordered and contiguous",
        )
        if expected_task_ranks is not None:
            _require(
                expected_task_ranks[name] == expected_rank,
                "reported rank disagrees with taskset RM order",
            )
        _require(
            type(task["is_top4"]) is bool
            and type(task["is_bottom6"]) is bool
            and task["is_top4"] == (expected_rank < 4)
            and task["is_bottom6"] == (expected_rank >= 4),
            "priority group flags disagree with RM rank",
        )
        counts = {
            field: _counter(task[field], f"{name}.{field}")
            for field in task_fields[4:]
        }
        released = counts["released_jobs"]
        adjudicable = counts.get("adjudicable_jobs", released)
        completed = counts["completed_jobs"]
        terminated = counts["terminated_jobs"]
        unfinished = counts["unfinished_at_horizon_jobs"]
        response_count = counts[
            "completed_response_time_count"
        ]
        response_sum = counts[
            "completed_response_time_sum_ms"
        ]
        response_max = counts[
            "completed_response_time_max_ms"
        ]
        _require(
            completed + terminated <= released
            and unfinished
            == released - completed - terminated
            and counts["deadline_miss_jobs"] <= adjudicable <= released
            and response_count <= completed
            and (observed_contract_version != 1 or response_count == completed)
            and (
                response_count != 0
                or (response_sum == 0 and response_max == 0)
            )
            and (
                response_count == 0
                or response_max <= response_sum
            ),
            f"lifecycle closure mismatch: {name}",
        )
        if observed_contract_version == 2:
            _require(
                name in expected_adjudicable
                and adjudicable == expected_adjudicable[name],
                f"adjudicable job count mismatch: {name}",
            )
            _require(
                adjudicable >= contract["invariants"][
                    "minimum_adjudicable_jobs_per_task"
                ],
                f"insufficient adjudicable jobs: {name}",
            )
        total_executed += counts["executed_core_ticks"]
    _require(
        total_executed <= processor_count * horizon,
        "executed core ticks exceed processor capacity",
    )
    return {
        "trace_schema_version": 3,
        "observability_summary_contract_version": observed_contract_version,
        "horizon_ms": horizon,
        "task_count": len(per_task),
        "top4_count": sum(item["is_top4"] for item in per_task),
        "bottom6_count": sum(
            item["is_bottom6"] for item in per_task
        ),
        "total_executed_core_ticks": total_executed,
    }
