"""Priority transformations limited to policies registered by v9.3."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence, Tuple

from .config import KNOWN_PRIORITY_POLICIES, domain_hash


def registered_priority_policies() -> Tuple[str, ...]:
    return tuple(sorted(KNOWN_PRIORITY_POLICIES))


def priority_mapping_hash(payload: Sequence[Mapping[str, Any]]) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
        [{"task_id": row["task_id"], "priority_rank": row["priority_rank"]} for row in payload],
    )


def apply_priority_policy(
    payload: Sequence[Mapping[str, Any]], policy: str
) -> Tuple[Dict[str, Any], ...]:
    if policy not in KNOWN_PRIORITY_POLICIES:
        raise ValueError(f"unsupported priority policy: {policy}")
    if policy != "RM":
        raise ValueError(f"priority policy is registered but has no audited adapter: {policy}")
    ordered = sorted(payload, key=lambda row: (int(row["T"]), str(row["task_id"])))
    return tuple({**dict(row), "priority_rank": rank} for rank, row in enumerate(ordered))
