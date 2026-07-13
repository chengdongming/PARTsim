"""Single-axis pair identities and unchanged-field verification."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence, Tuple

from .config import domain_hash


UNCHANGED_BY_AXIS = {
    "priority_policy": ("task_id", "C", "D", "T", "P", "workload", "arrival_offset"),
    "deadline_mode": ("task_id", "C", "T", "P", "workload", "arrival_offset", "priority_rank"),
    "power_mode": ("task_id", "C", "D", "T", "priority_rank", "arrival_offset"),
}


def sample_input_hash(payload: Sequence[Mapping[str, Any]]) -> str:
    return domain_hash("ASAP_BLOCK:V9.3:EXT4:CANONICAL_SAMPLE:v1", list(payload))


def verify_single_axis(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    changed_axis: str,
) -> Tuple[Dict[str, Any], ...]:
    if changed_axis not in UNCHANGED_BY_AXIS:
        raise ValueError(f"axis cannot form a field-level pair: {changed_axis}")
    before_by_id = {str(row["task_id"]): row for row in before}
    after_by_id = {str(row["task_id"]): row for row in after}
    if set(before_by_id) != set(after_by_id):
        raise RuntimeError("P0 single-axis transformation changed task IDs")
    checks = []
    for task_id in sorted(before_by_id):
        left, right = before_by_id[task_id], after_by_id[task_id]
        for field in UNCHANGED_BY_AXIS[changed_axis]:
            passed = left[field] == right[field]
            checks.append({
                "task_id": task_id, "changed_axis": changed_axis,
                "field": field, "before_value": left[field],
                "after_value": right[field], "status": "PASS" if passed else "P0_FAIL",
            })
            if not passed:
                raise RuntimeError(f"P0 {changed_axis} transformation changed {field}")
    return tuple(checks)


def pairing_type(changed_axis: str) -> str:
    return (
        "PAIRED_SINGLE_AXIS" if changed_axis in UNCHANGED_BY_AXIS
        else "UNPAIRED_STRATIFIED_COMPARISON"
    )
