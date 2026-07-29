"""Version-neutral mathematical grid for the six RTA4 formal experiments.

This module enumerates only scientific axes, stable task-set slot operands and
execution replicas.  It deliberately owns no V1/V2 profile, schema, numeric,
record, store, output or authorization identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence, Tuple

from .release_applicability import (
    ASYNC_HASH_PHASE_V1,
    FINITE_BATTERY_EMPIRICAL,
    RELEASE_HORIZON,
    SYNC_V1,
    THEOREM_ALIGNED,
)


MAIN_UTILIZATIONS = (
    "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5",
)
CORE4_UTILIZATIONS = ("3/10", "2/5", "1/2", "3/5", "7/10")
EXPECTED_STREAM_COUNTS = {
    "CORE-1": 19_200,
    "CORE-2": 28_800,
    "CORE-3": 6_400,
    "CORE-4": 72_000,
    "CORE-5A": 4_400,
    "CORE-5B": 12_000,
}


class RTA4FormalPlanGridError(ValueError):
    """Raised when the stable mathematical grid cannot be enumerated."""


@dataclass(frozen=True)
class TasksetGridSpec:
    namespace: str
    normalized_utilization: str
    replicate_index: int
    processor_count: int = 4
    task_count: int = 10
    scenario: str = "MAIN"
    deadline_variant: str = "constrained_uniform_slack_v1"
    power_scale: str = "1"
    integer_time_scale: int = 1

    def skeleton_material(self) -> dict[str, Any]:
        material: dict[str, Any] = {
            "namespace": self.namespace,
            "scenario": self.scenario,
            "processor_count": self.processor_count,
            "task_count": self.task_count,
            "normalized_utilization": self.normalized_utilization,
            "replicate_index": self.replicate_index,
        }
        if self.integer_time_scale != 1:
            material["integer_time_scale"] = self.integer_time_scale
        return material


@dataclass(frozen=True)
class FormalPlanGridPoint:
    kind: str
    core: str
    ordinal: int
    slot: TasksetGridSpec
    material: Mapping[str, Any]
    source_mathematical_request_id: str | None = None


@dataclass(frozen=True)
class Core5BSelectedGridSource:
    selection_hash: str
    mathematical_request_id: str
    point: FormalPlanGridPoint


Core5BRanker = Callable[[FormalPlanGridPoint], Tuple[str, str, str]]


def _main_slot(utilization: str, replicate: int) -> TasksetGridSpec:
    return TasksetGridSpec(
        "RTA4_CORE1_SHARED_TASKSETS_V1", utilization, replicate,
    )


def _rta_point(
    *, core: str, ordinal: int, slot: TasksetGridSpec, method: str,
    e0: str, service_scale: str = "1", power_scale: str = "1",
    deadline_variant: str = "constrained_uniform_slack_v1",
    scenario: str = "MAIN", axis: str = "baseline",
    axis_value: str = "baseline",
    timeout_contract: str = "UNFROZEN_PRE_PILOT",
) -> FormalPlanGridPoint:
    return FormalPlanGridPoint(
        "rta_request", core, ordinal, slot, {
            "scenario": scenario,
            "method": method,
            "exact_e0": e0,
            "service_scale": service_scale,
            "power_scale": power_scale,
            "deadline_variant": deadline_variant,
            "axis": axis,
            "axis_value": axis_value,
            "timeout_contract": timeout_contract,
            "normalized_utilization": slot.normalized_utilization,
            "processor_count": slot.processor_count,
            "task_count": slot.task_count,
            "replicate_index": slot.replicate_index,
        },
    )


def iter_core1_grid(methods: Sequence[str]) -> Iterator[FormalPlanGridPoint]:
    ordinal = 0
    for utilization in MAIN_UTILIZATIONS:
        for replicate in range(200):
            slot = _main_slot(utilization, replicate)
            for e0 in ("0", "1/20", "1"):
                for method in methods:
                    yield _rta_point(
                        core="CORE-1", ordinal=ordinal, slot=slot,
                        method=method, e0=e0,
                    )
                    ordinal += 1


def iter_core2_grid(methods: Sequence[str]) -> Iterator[FormalPlanGridPoint]:
    ordinal = 0
    for utilization in MAIN_UTILIZATIONS:
        for replicate in range(200):
            slot = _main_slot(utilization, replicate)
            for e0 in ("0", "1/20", "1"):
                for method in methods:
                    yield _rta_point(
                        core="CORE-2", ordinal=ordinal, slot=slot,
                        method=method, e0=e0,
                    )
                    ordinal += 1


def iter_core2_source_reference_grid() -> Iterator[FormalPlanGridPoint]:
    for utilization in MAIN_UTILIZATIONS:
        for replicate in range(200):
            slot = _main_slot(utilization, replicate)
            for e0 in ("0", "1/20", "1"):
                for method in ("LOC_THETA_LOC", "PH_THETA_PH"):
                    yield _rta_point(
                        core="CORE-1", ordinal=0, slot=slot,
                        method=method, e0=e0,
                    )


def iter_core3_grid() -> Iterator[FormalPlanGridPoint]:
    ordinal = 0
    tracks: Sequence[tuple[str, str, str | None]] = (
        (THEOREM_ALIGNED, ASYNC_HASH_PHASE_V1, None),
        (THEOREM_ALIGNED, SYNC_V1, None),
        (FINITE_BATTERY_EMPIRICAL, ASYNC_HASH_PHASE_V1, "20"),
        (FINITE_BATTERY_EMPIRICAL, ASYNC_HASH_PHASE_V1, "100"),
    )
    for utilization in MAIN_UTILIZATIONS:
        for replicate in range(200):
            slot = _main_slot(utilization, replicate)
            for track, release_mode, battery_capacity in tracks:
                yield FormalPlanGridPoint(
                    "simulation", "CORE-3", ordinal, slot, {
                        "source_core": "CORE-1",
                        "release_mode": release_mode,
                        "applicability_track": track,
                        "battery_model": (
                            "FINITE_CAPACITY_EXACT"
                            if track == FINITE_BATTERY_EMPIRICAL
                            else "THEOREM_NO_OVERFLOW_EXACT"
                        ),
                        "battery_capacity": battery_capacity or "1000000000",
                        "physical_initial_energy": "0",
                        "service_scale": "1",
                        "release_horizon": RELEASE_HORIZON,
                        "observation_horizon": "release_horizon_plus_dmax",
                        "scheduler": "gpfp_asap_block",
                        "normalized_utilization": utilization,
                        "processor_count": 4,
                        "task_count": 10,
                        "replicate_index": replicate,
                    },
                )
                ordinal += 1


def core4_conditions() -> tuple[tuple[str, str, str, str, str, str], ...]:
    baseline = ("baseline", "baseline", "1/20", "1", "1", "3/4")
    conditions = [baseline]
    for value in ("0", "1/100", "1/50", "3/100", "1/5", "1"):
        conditions.append(("e0", value, value, "1", "1", "3/4"))
    for value in ("1/2", "3/4", "5/4", "3/2"):
        conditions.append(("service_scale", value, "1/20", value, "1", "3/4"))
    for value in ("1/2", "3/4", "5/4", "3/2"):
        conditions.append(("power_scale", value, "1/20", "1", value, "3/4"))
    for value in ("1/4", "1/2", "1"):
        conditions.append((
            "deadline_slack_fraction", value, "1/20", "1", "1", value,
        ))
    if len(conditions) != 18:
        raise RTA4FormalPlanGridError("CORE-4 OFAT condition count drift")
    return tuple(conditions)


def core4_grid_point(
    utilization: str, replicate: int, condition_index: int,
    method: str, ordinal: int,
) -> FormalPlanGridPoint:
    axis, value, e0, service, power, deadline = core4_conditions()[condition_index]
    deadline_material = "fixed_slack_fraction_v1:" + deadline
    slot = TasksetGridSpec(
        "RTA4_CORE4_SENSITIVITY_V1", utilization, replicate,
        scenario="MAIN", deadline_variant=deadline_material,
        power_scale=power,
    )
    return _rta_point(
        core="CORE-4", ordinal=ordinal, slot=slot, method=method, e0=e0,
        service_scale=service, power_scale=power,
        deadline_variant=deadline_material, scenario="CORE4_OFAT",
        axis=axis, axis_value=value,
    )


def iter_core4_grid(methods: Sequence[str]) -> Iterator[FormalPlanGridPoint]:
    ordinal = 0
    for utilization in CORE4_UTILIZATIONS:
        for replicate in range(200):
            for condition_index in range(18):
                for method in methods:
                    yield core4_grid_point(
                        utilization, replicate, condition_index, method, ordinal,
                    )
                    ordinal += 1


def iter_core5a_grid(methods: Sequence[str]) -> Iterator[FormalPlanGridPoint]:
    ordinal = 0
    for task_count in (5, 10, 20, 30):
        for replicate in range(100):
            slot = TasksetGridSpec(
                "RTA4_CORE5A_TASK_COUNT_V1", "1/2", replicate,
                task_count=task_count, scenario=f"TASK_COUNT:{task_count}",
                deadline_variant="fixed_slack_fraction_v1:3/4",
            )
            for method in methods:
                yield _rta_point(
                    core="CORE-5A", ordinal=ordinal, slot=slot,
                    method=method, e0="1/20",
                    deadline_variant="fixed_slack_fraction_v1:3/4",
                    scenario="TASK_COUNT", axis="task_count",
                    axis_value=str(task_count),
                )
                ordinal += 1
    for processors in (2, 4, 8):
        for replicate in range(100):
            slot = TasksetGridSpec(
                "RTA4_CORE5A_PROCESSORS_V1", "1/2", replicate,
                processor_count=processors,
                scenario=f"PROCESSORS:{processors}",
                deadline_variant="fixed_slack_fraction_v1:3/4",
            )
            for method in methods:
                yield _rta_point(
                    core="CORE-5A", ordinal=ordinal, slot=slot,
                    method=method, e0="1/20",
                    deadline_variant="fixed_slack_fraction_v1:3/4",
                    scenario="PROCESSORS", axis="processor_count",
                    axis_value=str(processors),
                )
                ordinal += 1
    for time_scale in (1, 2, 4, 8):
        for replicate in range(100):
            slot = TasksetGridSpec(
                "RTA4_CORE5A_TIME_SCALE_V1", "1/2", replicate,
                scenario="TIME_SCALE_BASE",
                deadline_variant="fixed_slack_fraction_v1:3/4",
                integer_time_scale=time_scale,
            )
            for method in methods:
                yield _rta_point(
                    core="CORE-5A", ordinal=ordinal, slot=slot,
                    method=method, e0="1/20",
                    deadline_variant="fixed_slack_fraction_v1:3/4",
                    scenario="INTEGER_TIME_SCALE", axis="integer_time_scale",
                    axis_value=str(time_scale),
                )
                ordinal += 1


def iter_core5b_candidate_grid(
    methods: Sequence[str],
) -> Iterator[FormalPlanGridPoint]:
    for utilization in CORE4_UTILIZATIONS:
        for method in methods:
            for replicate in range(200):
                yield core4_grid_point(utilization, replicate, 0, method, 0)


def iter_core5b_selected_sources(
    methods: Sequence[str], ranker: Core5BRanker,
) -> Iterator[Core5BSelectedGridSource]:
    candidates = iter(iter_core5b_candidate_grid(methods))
    for _utilization in CORE4_UTILIZATIONS:
        for _method in methods:
            group = []
            for _replicate in range(200):
                point = next(candidates)
                selection_hash, tie_breaker, mathematical_request_id = ranker(point)
                group.append((
                    selection_hash, tie_breaker, mathematical_request_id, point,
                ))
            group.sort(key=lambda item: (item[0], item[1]))
            for selection_hash, _, mathematical_request_id, point in group[:150]:
                yield Core5BSelectedGridSource(
                    selection_hash, mathematical_request_id, point,
                )


def iter_core5b_grid(
    methods: Sequence[str], ranker: Core5BRanker,
) -> Iterator[FormalPlanGridPoint]:
    ordinal = 0
    for selected in iter_core5b_selected_sources(methods, ranker):
        source = selected.point
        for worker_count in (1, 2, 4, 8):
            yield FormalPlanGridPoint(
                "worker_execution", "CORE-5B", ordinal, source.slot, {
                    "worker_count": worker_count,
                    "selection_hash": selected.selection_hash,
                    "execution_role": "WORKER_CONSISTENCY",
                    "method": source.material["method"],
                    "exact_e0": "1/20",
                    "scenario": "CORE5B_WORKER_CONSISTENCY",
                    "axis": "worker_count",
                    "axis_value": str(worker_count),
                    "service_scale": "1",
                    "power_scale": "1",
                    "deadline_variant": "fixed_slack_fraction_v1:3/4",
                    "normalized_utilization": source.material[
                        "normalized_utilization"
                    ],
                    "processor_count": source.material["processor_count"],
                    "task_count": source.material["task_count"],
                    "replicate_index": source.material["replicate_index"],
                },
                selected.mathematical_request_id,
            )
            ordinal += 1


def iter_formal_plan_grid(
    core: str, *, recursive_methods: Sequence[str],
    core2_methods: Sequence[str], core5b_ranker: Core5BRanker,
) -> Iterator[FormalPlanGridPoint]:
    if core == "CORE-1":
        yield from iter_core1_grid(recursive_methods)
    elif core == "CORE-2":
        yield from iter_core2_grid(core2_methods)
    elif core == "CORE-3":
        yield from iter_core3_grid()
    elif core == "CORE-4":
        yield from iter_core4_grid(recursive_methods)
    elif core == "CORE-5A":
        yield from iter_core5a_grid(recursive_methods)
    elif core == "CORE-5B":
        yield from iter_core5b_grid(recursive_methods, core5b_ranker)
    else:
        raise RTA4FormalPlanGridError(f"unknown RTA4 formal core: {core!r}")


__all__ = [
    "CORE4_UTILIZATIONS", "EXPECTED_STREAM_COUNTS", "FormalPlanGridPoint",
    "MAIN_UTILIZATIONS", "RTA4FormalPlanGridError", "TasksetGridSpec",
    "core4_conditions", "core4_grid_point", "iter_core1_grid",
    "iter_core2_grid", "iter_core2_source_reference_grid", "iter_core3_grid",
    "iter_core4_grid", "iter_core5a_grid", "iter_core5b_candidate_grid",
    "iter_core5b_grid", "iter_core5b_selected_sources",
    "iter_formal_plan_grid",
]
