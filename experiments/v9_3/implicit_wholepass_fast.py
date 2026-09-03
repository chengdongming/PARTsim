"""Strict validation for the v6 implicit hard-RT WholePass fast result."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


FAST_SCHEMA = "PARTSIM_V6_IMPLICIT_HARDRT_WHOLEPASS_FAST_V1"
FAST_MODE = "v6_rm_implicit_hardrt_wholepass"
_MISSING = object()


class FastWholePassError(ValueError):
    """Raised when a compact fast result is not an admissible observation."""


def _pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FastWholePassError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    FastWholePassError(f"invalid JSON constant: {value}")
                ),
            )
    except FastWholePassError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise FastWholePassError(f"cannot read compact result: {exc}") from exc
    if not isinstance(value, dict):
        raise FastWholePassError("compact result must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise FastWholePassError(
            f"compact result fields differ: missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise FastWholePassError(f"{name} must be a nonnegative integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    result = _nonnegative_int(value, name)
    if result == 0:
        raise FastWholePassError(f"{name} must be positive")
    return result


def validate_fast_result(
    path: Path,
    *,
    expected_run_id: str,
    expected_taskset_hash: str,
    expected_scheduler: str,
    expected_processors: int,
    expected_task_ids: Sequence[str],
    expected_horizon: int,
) -> dict[str, Any]:
    return validate_fast_document(
        _load(path),
        expected_run_id=expected_run_id,
        expected_taskset_hash=expected_taskset_hash,
        expected_scheduler=expected_scheduler,
        expected_processors=expected_processors,
        expected_task_ids=expected_task_ids,
        expected_horizon=expected_horizon,
    )


def validate_fast_document(
    value: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_taskset_hash: str,
    expected_scheduler: str,
    expected_processors: int,
    expected_task_ids: Sequence[str],
    expected_horizon: int,
) -> dict[str, Any]:
    """Validate one compact result and return its immutable document."""

    value = dict(value)
    _exact_keys(value, {
        "schema", "fast_mode", "run_id", "taskset_semantic_hash",
        "configured_scheduler", "processors", "task_count", "task_ids",
        "deadline_mode", "horizon", "simulation_generation",
        "simulation_completed", "completion_reason", "taskset_pass",
        "released_jobs", "adjudicable_jobs", "completed_adjudicable_jobs",
        "first_deadline_miss",
    })
    if value["schema"] != FAST_SCHEMA or value["fast_mode"] != FAST_MODE:
        raise FastWholePassError("compact result schema/mode mismatch")
    if value["run_id"] != expected_run_id:
        raise FastWholePassError("compact result run identity mismatch")
    if value["taskset_semantic_hash"] != expected_taskset_hash:
        raise FastWholePassError("compact result taskset hash mismatch")
    if value["configured_scheduler"] != expected_scheduler:
        raise FastWholePassError("compact result scheduler mismatch")
    if value["processors"] != expected_processors:
        raise FastWholePassError("compact result processor mismatch")
    if value["task_count"] != len(expected_task_ids):
        raise FastWholePassError("compact result task count mismatch")
    task_ids = value["task_ids"]
    if (not isinstance(task_ids, list)
            or any(type(item) is not str or not item for item in task_ids)
            or len(set(task_ids)) != len(task_ids)
            or task_ids != list(expected_task_ids)):
        raise FastWholePassError("compact result task identity mismatch")
    if value["deadline_mode"] != "implicit":
        raise FastWholePassError("compact result deadline mode mismatch")
    if value["horizon"] != expected_horizon:
        raise FastWholePassError("compact result horizon mismatch")
    _positive_int(value["simulation_generation"], "simulation_generation")
    if type(value["simulation_completed"]) is not bool:
        raise FastWholePassError("simulation_completed must be boolean")
    if type(value["taskset_pass"]) is not bool:
        raise FastWholePassError("taskset_pass must be boolean")
    released = _nonnegative_int(value["released_jobs"], "released_jobs")
    adjudicable = _nonnegative_int(value["adjudicable_jobs"], "adjudicable_jobs")
    completed = _nonnegative_int(
        value["completed_adjudicable_jobs"], "completed_adjudicable_jobs"
    )
    if adjudicable > released or completed > adjudicable:
        raise FastWholePassError("compact lifecycle counts are inconsistent")
    miss = value["first_deadline_miss"]
    if value["completion_reason"] == "first_hardrt_deadline_miss":
        if value["taskset_pass"] or value["simulation_completed"]:
            raise FastWholePassError("early fail result has invalid completion flags")
        if not isinstance(miss, dict):
            raise FastWholePassError("early fail result lacks miss evidence")
        _exact_keys(miss, {
            "task_id", "job_id", "release", "absolute_deadline",
            "miss_time", "evidence",
        })
        if (miss["task_id"] not in expected_task_ids
                or miss["job_id"] != f'{miss["task_id"]}@{miss["release"]}'):
            raise FastWholePassError("miss identity is invalid")
        release = _nonnegative_int(miss["release"], "miss release")
        deadline = _nonnegative_int(
            miss["absolute_deadline"], "miss absolute deadline"
        )
        miss_time = _nonnegative_int(miss["miss_time"], "miss time")
        if not (release <= deadline < expected_horizon and miss_time >= deadline):
            raise FastWholePassError("miss timing is invalid")
        if not isinstance(miss["evidence"], str) or not miss["evidence"]:
            raise FastWholePassError("miss evidence is missing")
    elif value["completion_reason"] == "reached_horizon":
        if (not value["taskset_pass"] or not value["simulation_completed"]
                or miss is not None or adjudicable == 0
                or completed != adjudicable):
            raise FastWholePassError("horizon pass result is invalid")
    else:
        raise FastWholePassError("unknown compact termination reason")
    return value


__all__ = [
    "FAST_SCHEMA", "FAST_MODE", "FastWholePassError",
    "validate_fast_document", "validate_fast_result",
]
