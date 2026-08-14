"""SENS-SMALL exact one-factor-at-a-time sensitivity experiment."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.v9_3.constrained_taskset_identity import fixed_slack_deadline
from experiments.v9_3.rta_load_cross import (
    FROZEN_WORKLOADS,
    METHOD_DISPLAY_TO_ID,
    _load_exact_energy_model,
    _request_payload,
    fraction_text,
    generate_cpu_skeleton,
    scale_skeleton,
    stable_seed,
)


SENS_SMALL_PROTOCOL = "ASAP_BLOCK_V9_3_SENS_SMALL_V41"
SENS_SMALL_SEED_NAMESPACE = "ASAP_BLOCK_V9_3_SENS_SMALL_V41"
PROCESSORS = 4
TASK_COUNT = 10
E0 = Fraction(0)
RHO = Fraction(11, 2)
TARGET_UE = Fraction(4, 5)
U_C_VALUES = (Fraction(3, 10), Fraction(7, 10))
SKELETONS_PER_UC = 300
METHODS = ("CW", "LOC", "PH", "SEQ")
METHOD_IDS = tuple(METHOD_DISPLAY_TO_ID[name] for name in METHODS)
DEADLINE_DELTAS = (Fraction(1, 2), Fraction(3, 4), Fraction(1))
LATENCIES = (Fraction(0), Fraction(2, 5), Fraction(2))
FORMAL_TIMEOUT_FIRST = 600.0
FORMAL_TIMEOUT_RETRY = 1200.0


class SensSmallError(ValueError):
    """Raised when the SENS-SMALL scientific contract is violated."""


@dataclass(frozen=True)
class SensCondition:
    name: str
    axis: str
    axis_value: Fraction
    deadline_slack_fraction: Fraction
    latency: Fraction
    views: tuple[str, ...]


def conditions() -> tuple[SensCondition, ...]:
    return (
        SensCondition("D_LOW", "deadline", Fraction(1, 2), Fraction(1, 2), Fraction(2, 5), ("deadline",)),
        SensCondition("CENTER", "deadline", Fraction(3, 4), Fraction(3, 4), Fraction(2, 5), ("deadline", "latency")),
        SensCondition("D_HIGH", "deadline", Fraction(1), Fraction(1), Fraction(2, 5), ("deadline",)),
        SensCondition("L_LOW", "latency", Fraction(0), Fraction(3, 4), Fraction(0), ("latency",)),
        SensCondition("L_HIGH", "latency", Fraction(2), Fraction(3, 4), Fraction(2), ("latency",)),
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(
        SENS_SMALL_SEED_NAMESPACE.encode("ascii") + b"\0"
        + json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def stable_sens_seed(target_uc: Fraction, index: int) -> int:
    namespace_seed = int.from_bytes(
        hashlib.sha256(SENS_SMALL_SEED_NAMESPACE.encode("ascii")).digest()[:8],
        "big",
    )
    return stable_seed(namespace_seed, PROCESSORS, TASK_COUNT, target_uc, index)


def skeleton_id(target_uc: Fraction, index: int) -> str:
    return "sens-small-skeleton-" + _hash({
        "target_uc": fraction_text(target_uc), "index": index,
    })[:32]


def taskset_id(skeleton_identifier: str, condition: SensCondition) -> str:
    return f"{skeleton_identifier}-{condition.name.lower()}"


def generate_scaled_skeletons(
    *, target_ucs: Sequence[Fraction] = U_C_VALUES,
    count: int = SKELETONS_PER_UC,
    system_config: Path,
    allow_bounded: bool = False,
) -> list[dict[str, Any]]:
    if (
        (not allow_bounded and (tuple(target_ucs) != U_C_VALUES or count != SKELETONS_PER_UC))
        or not target_ucs
        or count < 1
        or any(value not in U_C_VALUES for value in target_ucs)
        or count > SKELETONS_PER_UC
    ):
        raise SensSmallError("scientific SENS-SMALL grid is not CLI-configurable")
    base_energies = _load_exact_energy_model(system_config)
    rows: list[dict[str, Any]] = []
    for target_uc in target_ucs:
        for index in range(count):
            seed = stable_sens_seed(target_uc, index)
            skeleton = generate_cpu_skeleton(
                seed=seed, target_uc=target_uc, processors=PROCESSORS,
                tasks=TASK_COUNT, period_min=40, period_max=200,
                min_task_util=Fraction(1, 100), max_task_util=Fraction(4, 5),
                tolerance_total=Fraction(1, 100), system_config=system_config,
            )
            scaled = scale_skeleton(
                skeleton, target_uc=target_uc, target_ue=TARGET_UE,
                generation_index=index, seed=seed, processors=PROCESSORS,
                rho=RHO, base_energies=base_energies,
            )
            scaled["taskset_id"] = skeleton_id(target_uc, index)
            scaled["skeleton_id"] = scaled["taskset_id"]
            scaled["seed_namespace"] = SENS_SMALL_SEED_NAMESPACE
            scaled["rho"] = fraction_text(RHO)
            scaled["e0"] = fraction_text(E0)
            rows.append(scaled)
    _validate_skeletons(rows, target_ucs=tuple(target_ucs), count=count)
    return rows


def _validate_skeletons(
    skeletons: Sequence[Mapping[str, Any]],
    *, target_ucs: Sequence[Fraction], count: int,
) -> None:
    if not skeletons:
        raise SensSmallError("unexpected SENS-SMALL skeleton count")
    for target_uc in target_ucs:
        group = [row for row in skeletons if row["target_uc"] == fraction_text(target_uc)]
        if len(group) != count:
            raise SensSmallError("SENS-SMALL U_C skeleton count mismatch")
        if {row["actual_ue"] for row in group} != {fraction_text(TARGET_UE)}:
            raise SensSmallError("SENS-SMALL exact U_E projection drift")


def condition_taskset(skeleton: Mapping[str, Any], condition: SensCondition) -> dict[str, Any]:
    tasks = []
    for task in skeleton["tasks"]:
        c, period = int(task["C"]), int(task["T"])
        deadline = fixed_slack_deadline(c, period, condition.deadline_slack_fraction)
        tasks.append({
            **dict(task), "D": deadline,
            "deadline_slack_fraction": fraction_text(condition.deadline_slack_fraction),
        })
    row = {
        "protocol": SENS_SMALL_PROTOCOL,
        "skeleton_id": str(skeleton["skeleton_id"]),
        "taskset_id": taskset_id(str(skeleton["skeleton_id"]), condition),
        "target_uc": str(skeleton["target_uc"]),
        "actual_uc": str(skeleton["actual_uc"]),
        "target_ue": fraction_text(TARGET_UE),
        "actual_ue": fraction_text(TARGET_UE),
        "generation_index": int(skeleton["generation_index"]),
        "seed": int(skeleton["seed"]),
        "seed_namespace": SENS_SMALL_SEED_NAMESPACE,
        "condition": condition.name,
        "axis": condition.axis,
        "axis_value": fraction_text(condition.axis_value),
        "deadline_slack_fraction": fraction_text(condition.deadline_slack_fraction),
        "latency": fraction_text(condition.latency),
        "rho": fraction_text(RHO),
        "e0": fraction_text(E0),
        "tasks": tasks,
    }
    if _energy_demand(row) != _energy_demand(skeleton):
        raise SensSmallError("condition changed exact energy demand")
    return row


def _energy_demand(taskset: Mapping[str, Any]) -> Fraction:
    return sum(
        Fraction(int(task["C"]), int(task["T"]))
        * Fraction(str(task["energy_per_tick"]))
        for task in taskset["tasks"]
    )


def expand_condition_tasksets(skeletons: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    expanded = [condition_taskset(skeleton, condition) for skeleton in skeletons for condition in conditions()]
    if len(expanded) != len(skeletons) * 5:
        raise SensSmallError("condition expansion count mismatch")
    return expanded


def request_id(skeleton: Mapping[str, Any], condition: SensCondition, method: str) -> str:
    if method not in METHODS:
        raise SensSmallError(f"unknown SENS-SMALL method: {method}")
    return "sens-small-request-" + _hash({
        "skeleton_id": skeleton["skeleton_id"],
        "condition": condition.name,
        "method": method,
        "e0": fraction_text(E0),
    })[:32]


def make_requests(tasksets: Sequence[Mapping[str, Any]], timeout: float) -> list[dict[str, Any]]:
    by_condition = {condition.name: condition for condition in conditions()}
    requests: list[dict[str, Any]] = []
    for taskset in tasksets:
        condition = by_condition[str(taskset["condition"])]
        for method in METHODS:
            identifier = request_id(taskset, condition, method)
            metadata = {
                key: taskset[key] for key in (
                    "taskset_id", "skeleton_id", "target_uc", "actual_uc",
                    "target_ue", "actual_ue", "condition", "axis", "axis_value",
                    "deadline_slack_fraction", "latency", "rho", "e0",
                )
            }
            metadata["method"] = method
            metadata["request_id"] = identifier
            requests.append({
                "request_id": identifier,
                "metadata": metadata,
                "payload": _request_payload(
                    taskset, E0, method, PROCESSORS, RHO,
                    condition.latency, timeout,
                ),
            })
    return requests


def plan_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for uc in U_C_VALUES:
        for index in range(SKELETONS_PER_UC):
            skeleton = {
                "skeleton_id": skeleton_id(uc, index),
                "target_uc": fraction_text(uc),
            }
            for condition in conditions():
                for method in METHODS:
                    identifier = request_id(skeleton, condition, method)
                    rows.append({
                        "request_id": identifier,
                        "skeleton_id": skeleton["skeleton_id"],
                        "target_uc": fraction_text(uc),
                        "condition": condition.name,
                        "axis": condition.axis,
                        "axis_value": fraction_text(condition.axis_value),
                        "deadline_slack_fraction": fraction_text(condition.deadline_slack_fraction),
                        "latency": fraction_text(condition.latency),
                        "method": method,
                        "e0": fraction_text(E0),
                    })
    _validate_plan_rows(rows)
    return rows


def _validate_plan_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    expected = len(U_C_VALUES) * SKELETONS_PER_UC * len(conditions()) * len(METHODS)
    if len(rows) != expected or len({row["request_id"] for row in rows}) != expected:
        raise SensSmallError("SENS-SMALL request identity/count mismatch")
    method_groups: dict[tuple[str, str], set[str]] = {}
    condition_groups: dict[str, set[str]] = {}
    for row in rows:
        method_groups.setdefault((str(row["skeleton_id"]), str(row["condition"])), set()).add(str(row["method"]))
        condition_groups.setdefault(str(row["skeleton_id"]), set()).add(str(row["condition"]))
    if any(methods != set(METHODS) for methods in method_groups.values()):
        raise SensSmallError("partial SENS-SMALL method group")
    if any(names != {condition.name for condition in conditions()} for names in condition_groups.values()):
        raise SensSmallError("partial SENS-SMALL condition group")


def plan_summary() -> dict[str, Any]:
    rows = plan_rows()
    return {
        "protocol": SENS_SMALL_PROTOCOL,
        "U_C_POINTS": len(U_C_VALUES),
        "SKELETONS_PER_UC": SKELETONS_PER_UC,
        "UNIQUE_SKELETONS": len(U_C_VALUES) * SKELETONS_PER_UC,
        "CONDITIONS_PER_SKELETON": len(conditions()),
        "METHODS": len(METHODS),
        "REQUESTS": len(rows),
        "CENTER_REQUESTS": len(U_C_VALUES) * SKELETONS_PER_UC * len(METHODS),
        "DEADLINE_AXIS_UNIQUE_CONDITIONS": 3,
        "LATENCY_AXIS_UNIQUE_CONDITIONS": 3,
        "PAIRING": "PASS",
        "MISSING": 0,
        "DUPLICATE": 0,
        "PARTIAL_METHOD_GROUP": 0,
        "PARTIAL_CONDITION_GROUP": 0,
        "solver_invocations": 0,
    }


__all__ = [
    "E0", "FORMAL_TIMEOUT_FIRST",
    "FORMAL_TIMEOUT_RETRY", "METHODS", "PROCESSORS", "RHO",
    "SENS_SMALL_PROTOCOL", "SENS_SMALL_SEED_NAMESPACE", "SKELETONS_PER_UC",
    "TARGET_UE", "TASK_COUNT", "U_C_VALUES", "SensCondition", "SensSmallError",
    "_energy_demand", "condition_taskset", "conditions", "expand_condition_tasksets",
    "generate_scaled_skeletons", "make_requests", "plan_rows", "plan_summary",
    "request_id", "skeleton_id", "stable_sens_seed",
]
