"""Pair identities and fairness validation for EXT-1."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import domain_hash
from .scheduler_registry import SCHEDULER_IDS


FAIRNESS_FIELDS = (
    "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
    "initial_battery", "battery_capacity", "horizon", "generation_seed",
)


def instance_id(material: Mapping[str, Any]) -> str:
    return domain_hash("ASAP_BLOCK:V9.3:EXT1:PAIRED_INSTANCE:v1", material)


def simulation_request_id(paired_instance_id: str, scheduler_id: str) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:EXT1:SIMULATION_REQUEST:v1",
        {"paired_instance_id": paired_instance_id, "scheduler_id": scheduler_id},
    )


def assert_scheduler_only_difference(
    rows: Iterable[Mapping[str, Any]],
    schedulers: Sequence[str] = SCHEDULER_IDS,
) -> None:
    grouped: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["paired_instance_id"])].append(row)
    expected = set(schedulers)
    for pair_id, members in grouped.items():
        observed = [str(row["scheduler_id"]) for row in members]
        if len(observed) != len(expected) or set(observed) != expected:
            raise RuntimeError(f"missing/duplicate scheduler request for {pair_id}")
        for field in FAIRNESS_FIELDS:
            values = {str(row[field]) for row in members}
            if len(values) != 1:
                raise RuntimeError(f"P0 scheduler pairing mismatch in {field} for {pair_id}")


def paired_relation(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    """Compare paired outcomes without treating schedulers as independent samples."""

    pass_status = "SIM_PASS_OBSERVED"
    miss_status = "SIM_DEADLINE_MISS"
    left_status, right_status = str(left["status"]), str(right["status"])
    if left_status == right_status:
        return "TIE"
    if left_status == pass_status and right_status == miss_status:
        return "LEFT_WIN"
    if left_status == miss_status and right_status == pass_status:
        return "RIGHT_WIN"
    return "NOT_COMPARABLE"
