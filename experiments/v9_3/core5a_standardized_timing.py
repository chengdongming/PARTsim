"""CORE-5A standardized timing, paper protocol v4.1.

This module is deliberately independent of the legacy CORE-5A grid.  It owns
the 16-point timing contract, exact axis semantics, deterministic method
pairing, and conversion of the existing v9.3 task-source payloads into the
unchanged exact RTA adapter input type.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
from typing import Any, Mapping

from experiments.common.exact_service_curve import (
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
    ExactServiceCurve,
    fraction_text,
    normalize_exact_service_curve,
)

from .cell_model import expand_cells
from .config import canonical_json, fraction_text as config_fraction_text, validate_config
from .perf_g import _task_generation_config
from .rta4_task_source_v4 import (
    CONTENT_CERTIFICATE_DOMAIN_V4,
    TASK_ORDER_DOMAIN_V4,
    TASKSET_DOMAIN_V4,
    TaskV4,
    TasksetV4,
)
from .taskset_store import StoredTaskset, TasksetStore, prepare_service_curve


CORE5A_TIMING_PROTOCOL = "ASAP_BLOCK_CORE5A_STANDARDIZED_TIMING_V4_1"
CORE5A_TIMING_DOMAIN = "ASAP_BLOCK:V9.3:CORE5A:STANDARDIZED_TIMING:v4.1"
CORE5A_SCALED_E0_V1 = "CORE5A_SCALED_E0_V1"
CORE5A_SCALED_LATENCY_SERVICE_V1 = "CORE5A_SCALED_LATENCY_SERVICE_V1"
TIMING_METHODS = (
    "CW_THETA_CW",
    "LOC_THETA_LOC",
    "PH_THETA_PH",
    "SEQ_THETA_SEQ",
)
TASKSET_COUNT = 10
REPETITIONS = (0, 1, 2)
WARMUP_REPETITION = 0
MEASURED_REPETITIONS = (1, 2)
HARD_TIMEOUT_SECONDS = 1200
PROCESSORS_BASE = 4
TASK_COUNT_BASE = 10
BASE_NORMALIZED_UTILIZATION = Fraction(1, 2)
BASE_TOTAL_UTILIZATION = Fraction(2)
SERVICE_RATE = Fraction(11, 2)
BASE_LATENCY = Fraction(2, 5)
BASE_E0 = Fraction(37)


class Core5ATimingError(ValueError):
    """Raised when the independent CORE-5A timing contract is invalid."""


@dataclass(frozen=True)
class TimingPoint:
    axis: str
    axis_value: int
    task_count: int
    processors: int
    time_scale: int
    target_total_utilization: Fraction
    target_normalized_utilization: Fraction

    @property
    def store_key(self) -> str:
        if self.axis == "task_count":
            return f"A1-n{self.axis_value}"
        if self.axis == "processors":
            return f"A2-m{self.axis_value}"
        return "A3-base"


def timing_points() -> tuple[TimingPoint, ...]:
    a1 = tuple(
        TimingPoint(
            "task_count", n, n, PROCESSORS_BASE, 1,
            BASE_TOTAL_UTILIZATION, BASE_NORMALIZED_UTILIZATION,
        )
        for n in (5, 8, 10, 12, 16, 20)
    )
    a2 = tuple(
        TimingPoint(
            "processors", m, TASK_COUNT_BASE, m, 1,
            Fraction(8, 5), Fraction(8, 5) / PROCESSORS_BASE,
        )
        for m in (2, 3, 4, 6, 8, 10)
    )
    a3 = tuple(
        TimingPoint(
            "time_scale", scale, TASK_COUNT_BASE, PROCESSORS_BASE, scale,
            BASE_TOTAL_UTILIZATION, BASE_NORMALIZED_UTILIZATION,
        )
        for scale in (1, 2, 3, 4)
    )
    return a1 + a2 + a3


def validate_timing_points(points: tuple[TimingPoint, ...] | None = None) -> None:
    observed = timing_points() if points is None else points
    if len(observed) != 16:
        raise Core5ATimingError(f"CORE5A timing point count is {len(observed)}")
    if [p.axis_value for p in observed[:6]] != [5, 8, 10, 12, 16, 20]:
        raise Core5ATimingError("A1 axis drift")
    if [p.axis_value for p in observed[6:12]] != [2, 3, 4, 6, 8, 10]:
        raise Core5ATimingError("A2 axis drift")
    if [p.axis_value for p in observed[12:]] != [1, 2, 3, 4]:
        raise Core5ATimingError("A3 axis drift")
    for point in observed:
        if point.axis == "processors":
            if point.target_total_utilization != Fraction(8, 5):
                raise Core5ATimingError("A2 total utilization drift")
            if point.target_normalized_utilization != Fraction(2, 5):
                raise Core5ATimingError("A2 normalized utilization drift")
        elif point.axis in {"task_count", "time_scale"}:
            if point.target_total_utilization != BASE_TOTAL_UTILIZATION:
                raise Core5ATimingError("A1/A3 total utilization drift")


def exact_service_curve(point: TimingPoint) -> ExactServiceCurve:
    scale = Fraction(point.time_scale)
    raw = {
        "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
        "rate": fraction_text(SERVICE_RATE),
        "latency": fraction_text(BASE_LATENCY * scale),
        "time_unit": "tick",
    }
    return normalize_exact_service_curve(raw)


def scaled_e0(point: TimingPoint) -> Fraction:
    return BASE_E0 * point.time_scale


def method_order(taskset_index: int) -> tuple[str, ...]:
    if type(taskset_index) is not int or not 0 <= taskset_index < TASKSET_COUNT:
        raise Core5ATimingError("taskset index outside timing taskset set")
    offset = taskset_index % len(TIMING_METHODS)
    return TIMING_METHODS[offset:] + TIMING_METHODS[:offset]


def plan_rows() -> list[dict[str, Any]]:
    validate_timing_points()
    rows: list[dict[str, Any]] = []
    for point in timing_points():
        for taskset_index in range(TASKSET_COUNT):
            slot_id = taskset_slot_id(point, taskset_index)
            for method in method_order(taskset_index):
                math_id = mathematical_request_id(point, taskset_index, method)
                for repetition in REPETITIONS:
                    rows.append({
                        "protocol": CORE5A_TIMING_PROTOCOL,
                        "axis": point.axis,
                        "axis_value": point.axis_value,
                        "task_count": point.task_count,
                        "processors": point.processors,
                        "time_scale": point.time_scale,
                        "target_total_utilization": fraction_text(
                            point.target_total_utilization
                        ),
                        "target_normalized_utilization": fraction_text(
                            point.target_normalized_utilization
                        ),
                        "taskset_index": taskset_index,
                        "taskset_slot_id": slot_id,
                        "taskset_identity": slot_id,
                        "method": method,
                        "mathematical_request_id": math_id,
                        "execution_id": execution_id(math_id, repetition),
                        "repetition": repetition,
                        "measurement_class": (
                            "WARMUP" if repetition == WARMUP_REPETITION
                            else "MEASURED"
                        ),
                        "timeout_seconds": HARD_TIMEOUT_SECONDS,
                    })
    return rows


def taskset_slot_id(point: TimingPoint, taskset_index: int) -> str:
    if type(taskset_index) is not int or not 0 <= taskset_index < TASKSET_COUNT:
        raise Core5ATimingError("taskset index outside timing taskset set")
    return f"core5a-timing-v41-{point.store_key}-i{taskset_index:02d}"


def mathematical_request_id(point: TimingPoint, taskset_index: int, method: str) -> str:
    if method not in TIMING_METHODS:
        raise Core5ATimingError(f"unknown timing method: {method}")
    material = {
        "protocol": CORE5A_TIMING_PROTOCOL,
        "axis": point.axis,
        "axis_value": point.axis_value,
        "taskset_index": taskset_index,
        "method": method,
    }
    return "core5a-math-" + _sha256(material)[:32]


def execution_id(math_id: str, repetition: int) -> str:
    if repetition not in REPETITIONS:
        raise Core5ATimingError("invalid timing repetition")
    return "core5a-exec-" + _sha256({"math": math_id, "rep": repetition})[:32]


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        CORE5A_TIMING_DOMAIN.encode("ascii") + b"\0"
        + canonical_json(value).encode("utf-8")
    ).hexdigest()


def generation_config(point: TimingPoint) -> dict[str, Any]:
    """Build a validated independent store config from the current generator."""
    config = _task_generation_config(
        f"CORE5A_TIMING_{point.store_key}",
        [point.target_normalized_utilization],
        100,
    )
    config["experiment_id"] = (
        f"asap-block-v9.3-core5a-standardized-timing-v41-{point.store_key.lower()}"
    )
    config["platform"] = {
        "cores": [point.processors], "task_count": [point.task_count],
    }
    config["grid"]["base_seed"] = 581204 + point.axis_value
    config["grid"]["taskset_index_start"] = 0
    config["energy"]["service_curve"]["id"] = (
        f"core5a-timing-v41-generator-{point.store_key.lower()}"
    )
    # The shared generator configuration validator intentionally retains its
    # CORE-3 production method list.  Timing methods are an independent
    # request-layer contract and are supplied to the unchanged RTA adapter;
    # changing this unrelated validator input would mix the two protocols.
    return validate_config(config, expected_core="CORE-3")


def materialize_taskset_store(root: Path, point: TimingPoint) -> tuple[TasksetStore, Any]:
    config = generation_config(point)
    service = prepare_service_curve(config, Path(root) / point.store_key / "service")
    store = TasksetStore(Path(root) / point.store_key / "tasksets", config, service)
    cells = expand_cells(config)
    if len(cells) != 1:
        raise Core5ATimingError("timing store must contain exactly one cell")
    return store, cells[0]


def stored_taskset(store: TasksetStore, cell: Any, index: int) -> StoredTaskset:
    stored = store.get_or_create(cell, index)
    if stored.taskset_index != index:
        raise Core5ATimingError("taskset index identity drift")
    if any(int(item.get("arrival_offset", 0)) != 0 for item in stored.task_payload):
        raise Core5ATimingError("CORE5A timing requires synchronous arrivals")
    if any(item.get("workload") == "idle" for item in stored.task_payload):
        raise Core5ATimingError("idle is not a timing task workload")
    return stored


def taskset_v4(stored: StoredTaskset, point: TimingPoint) -> TasksetV4:
    """Convert the existing exact generator payload, scaling A3 C/D/T only."""
    scale = point.time_scale
    tasks = tuple(
        TaskV4(
            name=f"t{index + 1}",
            C=int(item["C"]) * scale,
            D=int(item["D"]) * scale,
            T=int(item["T"]) * scale,
            power=fraction_text(Fraction(item["P"])),
        )
        for index, item in enumerate(stored.task_payload)
    )
    order = tuple(task.name for task in tasks)
    order_hash = _task_order_hash(order)
    content = {
        "source_semantic_hash": stored.semantic_hash,
        "store_key": point.store_key,
        "taskset_index": stored.taskset_index,
        "time_scale": scale,
        "tasks": [task.material() for task in tasks],
    }
    content_hash = _domain_hash(CONTENT_CERTIFICATE_DOMAIN_V4, content)
    identity_material = {
        "taskset_id": taskset_slot_id(point, stored.taskset_index),
        "source_seed": stored.seed,
        "task_order": list(order),
        "task_order_sha256": order_hash,
        "tasks": [task.material() for task in tasks],
        "content_sha256": content_hash,
    }
    identity = _domain_hash(TASKSET_DOMAIN_V4, identity_material)
    return TasksetV4(
        taskset_slot_id(point, stored.taskset_index), stored.seed, tasks, order,
        order_hash, content_hash, identity,
    )


def _task_order_hash(order: tuple[str, ...]) -> str:
    return _domain_hash(TASK_ORDER_DOMAIN_V4, list(order))


def _domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def plan_summary() -> dict[str, Any]:
    rows = plan_rows()
    return {
        "protocol": CORE5A_TIMING_PROTOCOL,
        "A1_points": 6,
        "A2_points": 6,
        "A3_points": 4,
        "grid_points": 16,
        "tasksets_per_point": TASKSET_COUNT,
        "methods": len(TIMING_METHODS),
        "mathematical_requests": 16 * TASKSET_COUNT * len(TIMING_METHODS),
        "repetitions": len(REPETITIONS),
        "executions": len(rows),
        "warmup_executions": sum(row["repetition"] == 0 for row in rows),
        "measured_executions": sum(row["repetition"] in MEASURED_REPETITIONS for row in rows),
        "solver_invocations": 0,
        "rotation": "taskset_index_mod_4_deterministic",
        "retry_policy": "no_retry",
        "hard_timeout_seconds": HARD_TIMEOUT_SECONDS,
    }


__all__ = [
    "BASE_E0", "BASE_LATENCY", "CORE5A_SCALED_E0_V1",
    "CORE5A_SCALED_LATENCY_SERVICE_V1", "CORE5A_TIMING_PROTOCOL",
    "Core5ATimingError", "HARD_TIMEOUT_SECONDS", "MEASURED_REPETITIONS",
    "REPETITIONS", "TIMING_METHODS", "TimingPoint", "exact_service_curve",
    "execution_id", "generation_config", "materialize_taskset_store",
    "mathematical_request_id", "method_order", "plan_rows", "plan_summary",
    "scaled_e0", "stored_taskset", "taskset_slot_id", "taskset_v4",
    "timing_points", "validate_timing_points",
]
