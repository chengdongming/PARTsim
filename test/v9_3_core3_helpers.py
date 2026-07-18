"""Small deterministic fixtures for CORE-3 unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def task_payload(
    *, c: int = 2, d: int = 5, t: int = 10, power: str = "1/10"
) -> list[dict[str, Any]]:
    return [{
        "task_id": "0", "source_name": "task_0", "priority_rank": 0,
        "C": c, "D": d, "T": t, "P": power,
        "D_over_T": f"{d}/{t}", "workload": "control",
        "arrival_offset": 0,
    }]


def trace_document(
    *,
    horizon: int = 10,
    completion: int | None = 2,
    deadline_miss: bool = False,
    energy_j: float = 20.0,
    taskset_hash: str = "a" * 64,
    c: int = 2,
    d: int = 5,
    t: int = 10,
) -> dict[str, Any]:
    name = "v93_task_0"
    events: list[dict[str, Any]] = [{
        "run_generation": 1, "time": "0", "event_type": "arrival",
        "task_name": name, "arrival_time": "0",
        "current_energy_mJ": energy_j * 1000,
    }, {
        "run_generation": 1, "time": "0", "event_type": "scheduled",
        "task_name": name, "arrival_time": "0",
        "task_unit_energy_mJ": 100.0,
    }]
    execution_ticks = (
        max(0, c - 1)
        if completion is None and deadline_miss else c
    )
    for tick in range(execution_ticks):
        events.append({
            "run_generation": 1, "time": str(tick),
            "event_type": "scheduler_decision", "scheduler": "ASAP-Block",
            "available_energy_mJ": 20000,
            "ready_jobs": [{
                "task_name": name, "arrival_time": 0, "priority": t,
                "ready_order": 0, "task_unit_energy_mJ": 100,
                "remaining_time_ms": c - tick, "absolute_deadline": d,
            }],
            "selected_jobs": [{
                "task_name": name, "arrival_time": 0, "priority": t,
                "ready_order": 0, "task_unit_energy_mJ": 100,
                "remaining_time_ms": c - tick, "absolute_deadline": d,
            }],
            "decision_reason": "selected_prefix",
        })
    if completion is not None:
        events.append({
            "run_generation": 1, "time": str(completion),
            "event_type": "end_instance", "task_name": name,
            "arrival_time": "0", "task_unit_energy_mJ": 100.0,
        })
    if completion is None and deadline_miss:
        events.append({
            "run_generation": 1, "time": str(execution_ticks),
            "event_type": "descheduled", "task_name": name,
            "arrival_time": "0", "reason": "preemption",
        })
    if deadline_miss:
        events.append({
            "run_generation": 1, "time": str(d),
            "event_type": "dline_miss", "task_name": name,
            "job_id": name + "@0", "arrival_time": "0",
            "deadline": str(d),
            "remaining_execution_ms": c - execution_ticks,
        })
    events.append({
        "run_generation": 1, "time": str(horizon),
        "event_type": "simulation_run_outcome", "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
    })
    return {
        "events": events,
        "trace_schema_version": 2,
        "run_count": 1,
        "target_run_generation": 1,
        "run_generation": 1,
        "run_id": "core3-test",
        "taskset_semantic_hash": taskset_hash,
        "configured_scheduler": "gpfp_asap_block",
        "scheduler_display_name": "ASAP-Block",
        "scheduler_implementation": "GPFPASAPBlockScheduler",
        "expected_simulation_horizon_ms": horizon,
        "observed_simulation_end_ms": horizon,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
    }


def write_trace(path: Path, **kwargs: Any) -> Path:
    path.write_text(json.dumps(trace_document(**kwargs)), encoding="utf-8")
    return path
