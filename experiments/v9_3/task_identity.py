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
