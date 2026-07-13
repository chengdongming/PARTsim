"""Formal cell identity, deterministic expansion, and request identities."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, Iterable, Mapping, Tuple

from .config import domain_hash, fraction_text


CORE1_VARIANTS = ("CW_THETA_CW", "LOC_THETA_LOC")
CORE2_VARIANTS = (
    "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC",
)


@dataclass(frozen=True)
class Cell:
    experiment_id: str
    core: str
    processors: int
    task_count: int
    utilization: Fraction
    utilization_index: int
    exact_e0: Fraction
    deadline_mode: str
    deadline_profile: str
    power_mode: str
    priority_policy: str
    service_curve_id: str
    numerical_mode: str
    cell_id: str
    generation_id: str

    def row(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "experiment_id": self.experiment_id,
            "core": self.core,
            "M": self.processors,
            "task_n": self.task_count,
            "utilization": fraction_text(self.utilization),
            "utilization_index": self.utilization_index,
            "exact_e0": fraction_text(self.exact_e0),
            "deadline_mode": self.deadline_mode,
            "deadline_profile": self.deadline_profile,
            "power_mode": self.power_mode,
            "priority_policy": self.priority_policy,
            "service_curve_id": self.service_curve_id,
            "numerical_mode": self.numerical_mode,
            "generation_id": self.generation_id,
        }


def _deadline_profile(config: Mapping[str, Any]) -> str:
    generation = config["generation"]
    if generation["deadline_mode"] == "implicit":
        return "implicit:D=T"
    constrained = generation["constrained_deadline"]
    return "constrained:{}:{}:{}:{}".format(
        constrained["distribution"], constrained["d_over_t_min"],
        constrained["d_over_t_max"], ",".join(constrained["d_over_t_values"]),
    )


def generation_dimensions(
    config: Mapping[str, Any], processors: int, task_count: int, utilization: Fraction
) -> Dict[str, Any]:
    generation = config["generation"]
    return {
        "M": processors,
        "task_n": task_count,
        "utilization": fraction_text(utilization),
        "deadline_mode": generation["deadline_mode"],
        "deadline_profile": _deadline_profile(config),
        "period_min": generation["period_min"],
        "period_max": generation["period_max"],
        "wcet_rounding": generation["wcet_rounding"],
        "utilization_tolerance": generation["utilization_tolerance"],
        "min_task_util": generation["min_task_util"],
        "max_task_util": generation["max_task_util"],
        "priority_policy": generation["priority_policy"],
        "power_mode": generation["power_mode"],
        "service_curve_id": config["energy"]["service_curve"]["id"],
    }


def expand_cells(config: Mapping[str, Any]) -> Tuple[Cell, ...]:
    result = []
    selected = config["grid"].get("cell_filter")
    selected_pairs = (
        {(row["utilization"], row["exact_e0"]) for row in selected}
        if selected else None
    )
    for processors in config["platform"]["cores"]:
        for task_count in config["platform"]["task_count"]:
            for utilization_index, utilization_text in enumerate(config["grid"]["utilization_points"]):
                utilization = Fraction(utilization_text)
                dimensions = generation_dimensions(config, processors, task_count, utilization)
                generation_id = domain_hash("ASAP_BLOCK:V9.3:TASKSET_GENERATION_CELL:v1", dimensions)
                for e0_text in config["energy"]["initial_energy_values"]:
                    if selected_pairs is not None and (utilization_text, e0_text) not in selected_pairs:
                        continue
                    e0 = Fraction(e0_text)
                    identity = {
                        "experiment_id": config["experiment_id"],
                        "core": config["core"],
                        **dimensions,
                        "exact_e0": fraction_text(e0),
                        "numerical_mode": config["analysis"]["numerical_mode"],
                    }
                    result.append(Cell(
                        config["experiment_id"], config["core"], processors,
                        task_count, utilization, utilization_index, e0,
                        config["generation"]["deadline_mode"], _deadline_profile(config),
                        config["generation"]["power_mode"],
                        config["generation"]["priority_policy"],
                        config["energy"]["service_curve"]["id"],
                        config["analysis"]["numerical_mode"],
                        domain_hash("ASAP_BLOCK:V9.3:FORMAL_CELL:v1", identity),
                        generation_id,
                    ))
    return tuple(result)


def derive_seed(
    base_seed: int, generation_id: str, taskset_index: int, *,
    seed_mode: str = "generation_dimensions", utilization_index: int | None = None,
) -> int:
    if seed_mode == "generation_dimensions":
        material = {
            "base_seed": base_seed,
            "generation_id": generation_id,
            "taskset_index": taskset_index,
        }
    elif seed_mode == "utilization_index_taskset_index":
        if utilization_index is None:
            raise ValueError("utilization-index seed mode requires utilization_index")
        material = {
            "base_seed": base_seed,
            "utilization_index": utilization_index,
            "taskset_index": taskset_index,
        }
    else:
        raise ValueError(f"unknown seed mode: {seed_mode}")
    digest = domain_hash("ASAP_BLOCK:V9.3:TASKSET_SEED:v1", material)
    return int(digest[:16], 16) % 2147483647


def taskset_id(generation_id: str, taskset_index: int, semantic_hash: str) -> str:
    return f"v93-{generation_id[:10]}-t{taskset_index:05d}-{semantic_hash[:12]}"


def analysis_id(cell: Cell, taskset_hash: str, variant: str) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:FORMAL_ANALYSIS:v1",
        {
            "experiment_id": cell.experiment_id,
            "core": cell.core,
            "cell_id": cell.cell_id,
            "taskset_hash": taskset_hash,
            "exact_e0": fraction_text(cell.exact_e0),
            "variant": variant,
            "numerical_mode": cell.numerical_mode,
            "service_curve_id": cell.service_curve_id,
        },
    )


def iter_requests(
    config: Mapping[str, Any], cells: Iterable[Cell]
) -> Iterable[tuple[Cell, int, str]]:
    for cell in cells:
        start = config["grid"].get("taskset_index_start", 0)
        for taskset_index in range(start, start + config["grid"]["tasksets_per_cell"]):
            for variant in config["analysis"]["variants"]:
                yield cell, taskset_index, variant
