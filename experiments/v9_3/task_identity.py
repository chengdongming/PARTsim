"""Shared source/runtime task identity projections for v9.3 simulations."""

from __future__ import annotations

from typing import Any


RUNTIME_TASK_NAME_PREFIX = "v93_task_"


def runtime_task_name_for_source_id(source_task_id: Any) -> str:
    """Return the exact runtime name emitted by the task input materializer."""

    if isinstance(source_task_id, bool) or source_task_id is None:
        raise ValueError("source task ID must be a non-empty scalar")
    value = str(source_task_id)
    if not value or value.strip() != value:
        raise ValueError("source task ID must be a non-empty canonical string")
    return f"{RUNTIME_TASK_NAME_PREFIX}{value}"


def runtime_job_id(task_name: Any, arrival_time: Any) -> str:
    """Return the exact native trace job identity for one task release."""

    if not isinstance(task_name, str) or not task_name or task_name.strip() != task_name:
        raise ValueError("runtime task name must be a non-empty canonical string")
    if isinstance(arrival_time, bool) or not isinstance(arrival_time, int):
        raise ValueError("arrival time must be an integer")
    if arrival_time < 0:
        raise ValueError("arrival time must be non-negative")
    return f"{task_name}@{arrival_time}"
