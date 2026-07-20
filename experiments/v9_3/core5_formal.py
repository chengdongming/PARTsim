"""CORE-5 formal plan identities, exact transforms, and profile isolation."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import random
import resource
import statistics
import time
from typing import Any, Dict, Mapping, Sequence

from .config import (
    ConfigError, canonical_json, config_hash, domain_hash, dump_config,
    fraction_text, load_config,
)
from .cell_model import expand_cells, taskset_id
from .core5_scalability import ResourceExecutionEngine
from .result_writer import atomic_write_json, read_csv
from .taskset_store import (
    ServiceCurveMaterial, StoredTaskset, TasksetStore, prepare_service_curve,
)

import asap_block_rta_v9_3 as rta_core


CORE5_FORMAL_PLAN_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_FORMAL_PLAN_V1"
CORE5_FORMAL_RUN_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_FORMAL_RUN_V1"
CORE5_FORMAL_CHECKPOINT_SCHEMA = "ASAP_BLOCK_V9_3_CORE5_FORMAL_CHECKPOINT_V1"
CORE5A_PROFILE = "formal-algorithmic-v1"
CORE5B_PROFILE = "formal-workers-v1"


class Core5FormalContractError(RuntimeError):
    """Raised when formal CORE-5 profiles or artifacts are mixed."""


@dataclass(frozen=True)
class Core5AFormalCell:
    scaling_axis: str
    level_id: str
    processors: int
    task_count: int
    period_min: int
    period_max: int
    exact_time_scale: Fraction
    utilization: Fraction
    source_family_id: str
    cell_id: str

    def mathematical_input(self) -> Dict[str, Any]:
        return {
            "M": self.processors,
            "task_n": self.task_count,
            "period_min": self.period_min,
            "period_max": self.period_max,
            "utilization": fraction_text(self.utilization),
        }

    def row(self) -> Dict[str, Any]:
        return {
            "formal_cell_id": self.cell_id,
            "scaling_axis": self.scaling_axis,
            "level_id": self.level_id,
            **self.mathematical_input(),
            "exact_time_scale": fraction_text(self.exact_time_scale),
            "source_taskset_family_id": self.source_family_id,
        }


def _source_family(
    config: Mapping[str, Any], *, processors: int, task_count: int,
    utilization: Fraction,
) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:CORE5A:SOURCE_TASKSET_FAMILY:v1",
        {
            "M": processors,
            "task_n": task_count,
            "period_min": 40,
            "period_max": 200,
            "utilization": fraction_text(utilization),
            "base_seed": config["grid"]["base_seed"],
            "generation": config["generation"],
        },
    )


def expand_core5a_cells(config: Mapping[str, Any]) -> tuple[Core5AFormalCell, ...]:
    if config["scalability"].get("profile") != CORE5A_PROFILE:
        raise ConfigError("CORE-5A expansion requires formal-algorithmic-v1")
    scale = config["scalability"]
    cells = []
    for utilization_text in scale["utilization_points"]:
        utilization = Fraction(utilization_text)
        specs = []
        specs.extend(
            ("task_count", f"n-{n}", 4, n, 40, 200, Fraction(1))
            for n in scale["task_counts"]
        )
        specs.extend(
            ("core_count", f"m-{m}", m, 20, 40, 200, Fraction(1))
            for m in scale["core_counts"]
        )
        specs.extend(
            (
                "time_scale", f"time-{factor}x", 4, 10,
                40 * int(Fraction(factor)), 200 * int(Fraction(factor)),
                Fraction(factor),
            )
            for factor in scale["time_scales"]
        )
        seen: set[tuple[int, int, int, int]] = set()
        for axis, level_id, processors, task_count, pmin, pmax, factor in specs:
            key = (processors, task_count, pmin, pmax)
            if key in seen:
                continue
            seen.add(key)
            source_processors = 4 if axis == "time_scale" else processors
            source_task_count = 10 if axis == "time_scale" else task_count
            source_family_id = _source_family(
                config, processors=source_processors,
                task_count=source_task_count, utilization=utilization,
            )
            identity = {
                "profile": CORE5A_PROFILE,
                "axis": axis,
                "level_id": level_id,
                "M": processors,
                "task_n": task_count,
                "period_min": pmin,
                "period_max": pmax,
                "exact_time_scale": fraction_text(factor),
                "utilization": fraction_text(utilization),
                "source_taskset_family_id": source_family_id,
            }
            cells.append(Core5AFormalCell(
                axis, level_id, processors, task_count, pmin, pmax, factor,
                utilization, source_family_id,
                domain_hash("ASAP_BLOCK:V9.3:CORE5A:FORMAL_CELL:v1", identity),
            ))
    return tuple(cells)


def exact_time_scale_payload(
    task_payload: Sequence[Mapping[str, Any]], exact_scale: Fraction,
) -> tuple[Dict[str, Any], ...]:
    """Scale C/D/T exactly while preserving utilization, D/T, P, and identity."""

    factor = Fraction(exact_scale)
    if factor.denominator != 1 or factor <= 0:
        raise Core5FormalContractError("CORE-5A time scale must be a positive integer")
    multiplier = factor.numerator
    transformed = []
    for source in task_payload:
        row = dict(source)
        c_value = int(source["C"]) * multiplier
        d_value = int(source["D"]) * multiplier
        t_value = int(source["T"]) * multiplier
        if not 0 < c_value <= d_value <= t_value:
            raise Core5FormalContractError("scaled task violates C <= D <= T")
        row.update({
            "C": c_value, "D": d_value, "T": t_value,
            "D_over_T": fraction_text(Fraction(d_value, t_value)),
            "source_task_id": str(source["task_id"]),
            "exact_time_scale": fraction_text(factor),
        })
        transformed.append(row)
    return tuple(transformed)


def core5b_math_request_rows(config: Mapping[str, Any]) -> tuple[Dict[str, Any], ...]:
    if config["scalability"].get("profile") != CORE5B_PROFILE:
        raise ConfigError("CORE-5B expansion requires formal-workers-v1")
    rows = []
    start = int(config["grid"].get("taskset_index_start", 0))
    count = int(config["grid"]["tasksets_per_cell"])
    for utilization in config["scalability"]["utilization_points"]:
        for taskset_index in range(start, start + count):
            taskset_input = {
                "M": 4, "task_n": 10, "period_min": 40,
                "period_max": 200, "utilization": utilization,
                "taskset_index": taskset_index,
                "base_seed": config["grid"]["base_seed"],
            }
            taskset_input_hash = domain_hash(
                "ASAP_BLOCK:V9.3:CORE5B:TASKSET_INPUT:v1", taskset_input
            )
            for variant in config["analysis"]["variants"]:
                mathematical_input = {
                    **taskset_input,
                    "taskset_input_hash": taskset_input_hash,
                    "E0": config["energy"]["initial_energy_values"][0],
                    "battery_capacity": config["energy"]["battery_capacity"],
                    "service_curve": config["energy"]["service_curve"]["id"],
                    "variant": variant,
                    "numerical_mode": config["analysis"]["numerical_mode"],
                }
                rows.append({
                    **mathematical_input,
                    "mathematical_request_id": domain_hash(
                        "ASAP_BLOCK:V9.3:CORE5B:MATHEMATICAL_REQUEST:v1",
                        mathematical_input,
                    ),
                    "input_hash": domain_hash(
                        "ASAP_BLOCK:V9.3:CORE5B:INPUT:v1", mathematical_input
                    ),
                })
    return tuple(rows)


def core5b_execution_schedule(config: Mapping[str, Any]) -> tuple[Dict[str, int], ...]:
    scale = config["scalability"]
    schedule = [
        {"worker_count": worker, "repetition": repetition}
        for worker in scale["worker_counts"]
        for repetition in range(scale["repetitions_per_worker"])
    ]
    random.Random(scale["schedule_seed"]).shuffle(schedule)
    return tuple({"run_order": index, **row} for index, row in enumerate(schedule))


def assert_worker_semantic_identity(rows: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed if repeated worker executions disagree mathematically."""

    grouped: Dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        request_id = str(row.get("mathematical_request_id", ""))
        if not request_id:
            raise Core5FormalContractError("worker result lacks mathematical request ID")
        grouped.setdefault(request_id, []).append(row)
    required = {
        "input_hash", "terminal_class", "response_bound",
        "fixed_point_iterations", "search_states", "inverse_service_queries",
        "candidate_count",
    }
    for request_id, members in grouped.items():
        if any(not required.issubset(member) for member in members):
            raise Core5FormalContractError(
                f"worker result lacks semantic fields: {request_id}"
            )
        signatures = {
            canonical_json({key: member[key] for key in sorted(required)})
            for member in members
        }
        if len(signatures) != 1:
            raise Core5FormalContractError(
                f"P0 worker semantic mismatch: {request_id}"
            )


class ExactTimeScaleStoreView:
    """Expose exact C/D/T-scaled descendants of one frozen source family."""

    def __init__(
        self, base_store: TasksetStore, base_cell: Any,
        exact_scale: Fraction, root: Path,
    ) -> None:
        self.base_store = base_store
        self.base_cell = base_cell
        self.exact_scale = Fraction(exact_scale)
        self.root = Path(root)

    def get_or_create(self, cell: Any, taskset_index: int) -> StoredTaskset:
        source = self.base_store.get_or_create(self.base_cell, taskset_index)
        if self.exact_scale == 1:
            return source
        payload = exact_time_scale_payload(
            source.task_payload, self.exact_scale
        )
        tasks = tuple(
            rta_core.V93Task(
                str(row["task_id"]), int(row["C"]), int(row["D"]),
                int(row["T"]), Fraction(str(row["P"])),
            )
            for row in payload
        )
        semantic_input = {
            "schema": "ASAP_BLOCK_V9_3_CORE5A_EXACT_TIME_SCALE_V1",
            "source_taskset_hash": source.semantic_hash,
            "source_taskset_id": source.taskset_id,
            "target_generation_id": cell.generation_id,
            "exact_time_scale": fraction_text(self.exact_scale),
            "tasks": payload,
        }
        semantic_hash = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5A:SCALED_TASKSET:v1", semantic_input
        )
        target_id = taskset_id(
            cell.generation_id, taskset_index, semantic_hash
        )
        path = (
            self.root / cell.generation_id
            / f"taskset_{taskset_index:05d}.json"
        )
        document = {
            **semantic_input,
            "taskset_id": target_id,
            "taskset_hash": semantic_hash,
            "taskset_index": taskset_index,
            "source_generation_id": source.generation_id,
            "priority_hash": source.priority_hash,
            "power_hash": source.power_hash,
            "target_total_utilization": fraction_text(source.target_utilization),
            "actual_total_utilization": fraction_text(source.actual_utilization),
            "service_curve_reference": source.service_curve_reference,
        }
        if path.is_file():
            observed = json.loads(path.read_text(encoding="utf-8"))
            if observed != document:
                raise Core5FormalContractError(
                    "scaled taskset artifact conflicts with exact transform"
                )
        else:
            atomic_write_json(path, document)
        return StoredTaskset(
            target_id, cell.generation_id, taskset_index, source.seed,
            semantic_hash, source.priority_hash, source.power_hash,
            source.target_utilization, source.actual_utilization,
            cell.processors, cell.task_count, source.deadline_mode,
            tasks, payload, source.generation_seconds,
            source.service_curve_reference, path,
        )


class Core5FormalRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.profile = self.config["scalability"].get("profile")
        if self.profile not in {CORE5A_PROFILE, CORE5B_PROFILE}:
            raise ConfigError("CORE-5 formal runner requires a formal profile")
        self.root = Path(self.config["execution"]["output_root"])
        self.identity = config_hash(self.config)

    def describe(self, *, max_cells: int | None = None) -> Dict[str, Any]:
        if self.profile == CORE5A_PROFILE:
            all_cells = list(expand_core5a_cells(self.config))
            cells = all_cells if max_cells is None else all_cells[:max_cells]
            requests = (
                len(cells) * self.config["grid"]["tasksets_per_cell"]
                * len(self.config["analysis"]["variants"])
            )
            result = {
                "schema": CORE5_FORMAL_PLAN_SCHEMA,
                "profile": self.profile,
                "experiment_id": self.config["experiment_id"],
                "core": "CORE-5",
                "cell_count": len(cells),
                "unique_scale_configurations_per_utilization": 8,
                "mathematical_request_count": requests,
                "solver_execution_count": requests,
                "hard_analysis_limit": self.config["scalability"]["max_analyses"],
                "cells": [cell.row() for cell in cells],
            }
        else:
            math_rows = core5b_math_request_rows(self.config)
            schedule = list(core5b_execution_schedule(self.config))
            cells = schedule if max_cells is None else schedule[:max_cells]
            executions = len(cells) * len(math_rows)
            result = {
                "schema": CORE5_FORMAL_PLAN_SCHEMA,
                "profile": self.profile,
                "experiment_id": self.config["experiment_id"],
                "core": "CORE-5",
                "cell_count": len(cells),
                "mathematical_request_count": len(math_rows),
                "input_hash_count": len({row["input_hash"] for row in math_rows}),
                "solver_execution_count": executions,
                "repetitions_per_worker": self.config["scalability"]["repetitions_per_worker"],
                "worker_counts": self.config["scalability"]["worker_counts"],
                "schedule_seed": self.config["scalability"]["schedule_seed"],
                "hard_analysis_limit": self.config["scalability"]["max_analyses"],
                "cells": cells,
            }
        result["plan_hash"] = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5:FORMAL_PLAN:v1", result
        )
        return result

    def _write_checkpoint(
        self, *, phase: str, completed_run_ids: Sequence[str],
        terminal_count: int, p0: bool,
    ) -> None:
        plan = self.describe()
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": CORE5_FORMAL_CHECKPOINT_SCHEMA,
            "profile": self.profile,
            "config_hash": self.identity,
            "plan_hash": plan["plan_hash"],
            "phase": phase,
            "completed_run_ids": sorted(completed_run_ids),
            "terminal_count": int(terminal_count),
            "p0": bool(p0),
        })

    def _initialize(self, *, resume: bool) -> tuple[list[str], int]:
        metadata_path = self.root / "run_metadata.json"
        if metadata_path.is_file():
            if not resume:
                raise Core5FormalContractError(
                    "formal CORE-5 output exists; use --resume"
                )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            checkpoint = json.loads(
                (self.root / "checkpoint.json").read_text(encoding="utf-8")
            )
            if (
                metadata.get("schema") != CORE5_FORMAL_RUN_SCHEMA
                or metadata.get("profile") != self.profile
                or metadata.get("config_hash") != self.identity
                or checkpoint.get("schema") != CORE5_FORMAL_CHECKPOINT_SCHEMA
                or checkpoint.get("phase") == "STOPPED"
            ):
                raise Core5FormalContractError(
                    "formal CORE-5 resume envelope mismatch"
                )
            return (
                list(checkpoint.get("completed_run_ids", [])),
                int(checkpoint.get("terminal_count", 0)),
            )
        self.root.mkdir(parents=True, exist_ok=True)
        if any(self.root.iterdir()):
            raise Core5FormalContractError(
                "formal CORE-5 output has artifacts without metadata"
            )
        plan = self.describe()
        seal = {
            "schema": "ASAP_BLOCK_V9_3_CORE5_FORMAL_AUTHORIZATION_SEAL_V1",
            "profile": self.profile,
            "config_hash": self.identity,
            "plan_hash": plan["plan_hash"],
            "output_root": str(self.root),
            "taskset_store": self.config["execution"]["taskset_store"],
        }
        atomic_write_json(self.root / "formal_authorization_seal.json", seal)
        atomic_write_json(metadata_path, {
            "schema": CORE5_FORMAL_RUN_SCHEMA,
            "profile": self.profile,
            "config_hash": self.identity,
            "plan_hash": plan["plan_hash"],
            "formal_large_scale_run": True,
            "authorization_seal_schema": seal["schema"],
        })
        dump_config(self.config, self.root / "run_config.yaml")
        atomic_write_json(self.root / "formal_plan.json", plan)
        self._write_checkpoint(
            phase="INITIALIZED", completed_run_ids=[], terminal_count=0,
            p0=False,
        )
        return [], 0

    def _child_config(
        self, *, run_id: str, processors: int, task_count: int,
        period_min: int, period_max: int, utilizations: Sequence[str],
        tasksets: int, worker_count: int,
    ) -> Dict[str, Any]:
        from copy import deepcopy

        child = deepcopy(self.config)
        # Worker/repetition are operational dimensions; this experiment ID is
        # deliberately stable so mathematical analysis IDs remain identical.
        child["experiment_id"] = self.config["experiment_id"]
        child["platform"] = {"cores": [processors], "task_count": [task_count]}
        child["generation"]["period_min"] = period_min
        child["generation"]["period_max"] = period_max
        child["grid"]["utilization_points"] = list(utilizations)
        child["grid"]["tasksets_per_cell"] = tasksets
        child["analysis"]["worker_count"] = worker_count
        child["execution"]["output_root"] = str(
            self.root / "child_runs" / run_id
        )
        child["execution"]["taskset_store"] = self.config["execution"][
            "taskset_store"
        ]
        child["execution"]["resume"] = False
        return child

    @staticmethod
    def _usage_cpu_seconds() -> float:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return float(usage.ru_utime + usage.ru_stime)

    def _run_child(
        self, child: Mapping[str, Any], *, resume: bool,
        service: ServiceCurveMaterial | None = None,
        store: Any = None,
    ) -> tuple[Any, Dict[str, Any]]:
        started = time.perf_counter()
        cpu_before = self._usage_cpu_seconds()
        outcome = ResourceExecutionEngine(
            child, service_override=service, store_override=store
        ).run(resume=resume)
        wall = time.perf_counter() - started
        cpu = max(self._usage_cpu_seconds() - cpu_before, 0.0)
        observations = read_csv(
            Path(child["execution"]["output_root"])
            / "attempt_resource_observations.csv"
        )
        rss = [
            int(row["peak_rss_kib"]) for row in observations
            if str(row.get("peak_rss_kib", "")).isdigit()
        ]
        return outcome, {
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "peak_rss_kib": max(rss, default=None),
            "analyses_per_second": outcome.terminal / wall if wall else None,
        }

    def run(
        self, *, resume: bool = False, max_cells: int | None = None,
        max_tasksets: int | None = None,
    ) -> Mapping[str, Any]:
        if max_cells is not None or max_tasksets is not None:
            raise Core5FormalContractError(
                "formal CORE-5 execution cannot be truncated; use --dry-run for inspection"
            )
        completed, terminal_count = self._initialize(resume=resume)
        if (self.root / "formal_summary.json").is_file():
            return analyze_core5_formal_artifacts(self.root)
        if self.profile == CORE5A_PROFILE:
            summary = self._run_core5a(
                resume=resume, completed=set(completed),
                terminal_count=terminal_count,
            )
        else:
            summary = self._run_core5b(
                resume=resume, completed=set(completed),
                terminal_count=terminal_count,
            )
        atomic_write_json(self.root / "formal_summary.json", summary)
        self._write_checkpoint(
            phase="COMPLETED", completed_run_ids=summary["completed_run_ids"],
            terminal_count=summary["solver_execution_count"], p0=False,
        )
        return summary

    def _run_core5a(
        self, *, resume: bool, completed: set[str], terminal_count: int,
    ) -> Dict[str, Any]:
        cells = expand_core5a_cells(self.config)
        completed_ids = set(completed)
        run_metrics = []
        for run_id in sorted(completed_ids):
            metric_path = self.root / "run_metrics" / f"{run_id}.json"
            if not metric_path.is_file():
                raise Core5FormalContractError(
                    f"CORE-5A resume lacks run metrics: {run_id}"
                )
            run_metrics.append(json.loads(metric_path.read_text(encoding="utf-8")))
        for cell in cells:
            run_id = cell.cell_id
            child = self._child_config(
                run_id=run_id, processors=cell.processors,
                task_count=cell.task_count, period_min=cell.period_min,
                period_max=cell.period_max,
                utilizations=[fraction_text(cell.utilization)],
                tasksets=self.config["grid"]["tasksets_per_cell"],
                worker_count=1,
            )
            if run_id in completed_ids:
                continue
            child_root = Path(child["execution"]["output_root"])
            service = prepare_service_curve(child, child_root / "service_material")
            store = None
            if cell.scaling_axis == "time_scale":
                from copy import deepcopy

                base_config = deepcopy(child)
                base_config["generation"]["period_min"] = 40
                base_config["generation"]["period_max"] = 200
                base_cell = expand_cells(base_config)[0]
                base_store = TasksetStore(
                    Path(self.config["execution"]["taskset_store"])
                    / "time_scale_sources" / cell.source_family_id,
                    base_config, service,
                )
                store = ExactTimeScaleStoreView(
                    base_store, base_cell, cell.exact_time_scale,
                    Path(self.config["execution"]["taskset_store"])
                    / "time_scaled",
                )
            outcome, metrics = self._run_child(
                child, resume=False, service=service, store=store,
            )
            if outcome.stopped or outcome.terminal != outcome.requested:
                self._write_checkpoint(
                    phase="STOPPED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count + outcome.terminal, p0=True,
                )
                raise Core5FormalContractError(
                    f"P0 CORE-5A child failure: {run_id}"
                )
            terminal_count += outcome.terminal
            completed_ids.add(run_id)
            metric_row = {"run_id": run_id, **cell.row(), **metrics}
            run_metrics.append(metric_row)
            atomic_write_json(
                self.root / "run_metrics" / f"{run_id}.json", metric_row
            )
            self._write_checkpoint(
                phase="RUNNING", completed_run_ids=completed_ids,
                terminal_count=terminal_count, p0=False,
            )
        plan = self.describe()
        if terminal_count != plan["solver_execution_count"]:
            raise Core5FormalContractError("CORE-5A terminal count mismatch")
        return {
            "schema": "ASAP_BLOCK_V9_3_CORE5A_FORMAL_SUMMARY_V1",
            "profile": self.profile,
            "mathematical_request_count": plan["mathematical_request_count"],
            "solver_execution_count": terminal_count,
            "completed_run_ids": sorted(completed_ids),
            "parallel_throughput_is_not_algorithmic_complexity": True,
            "required_metrics": [
                "terminal_status", "runtime_median", "runtime_p95",
                "runtime_max", "peak_rss", "fixed_point_iterations",
                "search_states", "inverse_service_curve_queries",
                "candidate_counts", "timeout_retry_counts", "censoring_state",
            ],
            "run_metrics": run_metrics,
        }

    @staticmethod
    def _worker_semantic_rows(
        child_root: Path, scheduled: Mapping[str, int],
    ) -> list[Dict[str, Any]]:
        task_rows = read_csv(child_root / "per_task_results.csv")
        tasks_by_analysis: Dict[str, list[Mapping[str, str]]] = {}
        for row in task_rows:
            tasks_by_analysis.setdefault(row["analysis_id"], []).append(row)
        semantic_rows = []
        for result in read_csv(child_root / "per_taskset_results.csv"):
            task_signature = [
                {
                    key: row.get(key, "")
                    for key in (
                        "task_id", "task_solver_status",
                        "candidate_response_time", "checked_w_count",
                        "checked_h_count", "checked_q_count",
                        "envelope_call_count",
                    )
                }
                for row in sorted(
                    tasks_by_analysis[result["analysis_id"]],
                    key=lambda value: int(value["task_id"]),
                )
            ]
            signature = {
                "input_hash": domain_hash(
                    "ASAP_BLOCK:V9.3:CORE5B:OBSERVED_INPUT:v1",
                    {
                        "taskset_hash": result["taskset_hash"],
                        "variant": result["analysis_variant"],
                        "exact_e0": result["exact_e0"],
                    },
                ),
                "terminal_class": result["solver_status"],
                "response_bound": canonical_json([
                    row["candidate_response_time"] for row in task_signature
                ]),
                "fixed_point_iterations": canonical_json([
                    row["checked_w_count"] for row in task_signature
                ]),
                "search_states": canonical_json([
                    [row["checked_h_count"], row["checked_q_count"]]
                    for row in task_signature
                ]),
                "inverse_service_queries": canonical_json([
                    row["envelope_call_count"] for row in task_signature
                ]),
                "candidate_count": sum(
                    bool(row["candidate_response_time"])
                    for row in task_signature
                ),
            }
            semantic_rows.append({
                "mathematical_request_id": result["analysis_id"],
                "worker_count": scheduled["worker_count"],
                "repetition": scheduled["repetition"],
                **signature,
            })
        return semantic_rows

    def _run_core5b(
        self, *, resume: bool, completed: set[str], terminal_count: int,
    ) -> Dict[str, Any]:
        schedule = core5b_execution_schedule(self.config)
        completed_ids = set(completed)
        semantic_rows = []
        run_metrics = []
        baseline_throughput: Dict[int, list[float]] = {}
        for scheduled in schedule:
            run_id = f"w{scheduled['worker_count']}-r{scheduled['repetition']}"
            child = self._child_config(
                run_id=run_id, processors=4, task_count=10,
                period_min=40, period_max=200,
                utilizations=self.config["scalability"]["utilization_points"],
                tasksets=self.config["grid"]["tasksets_per_cell"],
                worker_count=scheduled["worker_count"],
            )
            if run_id in completed_ids:
                metric_path = self.root / "run_metrics" / f"{run_id}.json"
                if not metric_path.is_file():
                    raise Core5FormalContractError(
                        f"CORE-5B resume lacks run metrics: {run_id}"
                    )
                metric_row = json.loads(metric_path.read_text(encoding="utf-8"))
                run_metrics.append(metric_row)
                throughput = metric_row.get("analyses_per_second")
                if throughput is not None:
                    baseline_throughput.setdefault(
                        scheduled["worker_count"], []
                    ).append(float(throughput))
                semantic_rows.extend(self._worker_semantic_rows(
                    Path(child["execution"]["output_root"]), scheduled
                ))
                continue
            outcome, metrics = self._run_child(child, resume=False)
            if outcome.stopped or outcome.terminal != outcome.requested:
                self._write_checkpoint(
                    phase="STOPPED", completed_run_ids=completed_ids,
                    terminal_count=terminal_count + outcome.terminal, p0=True,
                )
                raise Core5FormalContractError(
                    f"P0 CORE-5B child failure: {run_id}"
                )
            child_root = Path(child["execution"]["output_root"])
            semantic_rows.extend(
                self._worker_semantic_rows(child_root, scheduled)
            )
            terminal_count += outcome.terminal
            completed_ids.add(run_id)
            throughput = metrics["analyses_per_second"]
            if throughput is not None:
                baseline_throughput.setdefault(
                    scheduled["worker_count"], []
                ).append(float(throughput))
            metric_row = {"run_id": run_id, **scheduled, **metrics}
            run_metrics.append(metric_row)
            atomic_write_json(
                self.root / "run_metrics" / f"{run_id}.json", metric_row
            )
            self._write_checkpoint(
                phase="RUNNING", completed_run_ids=completed_ids,
                terminal_count=terminal_count, p0=False,
            )
        assert_worker_semantic_identity(semantic_rows)
        plan = self.describe()
        if terminal_count != plan["solver_execution_count"]:
            raise Core5FormalContractError("CORE-5B execution count mismatch")
        one = statistics.median(baseline_throughput[1])
        worker_summary = []
        for worker in self.config["scalability"]["worker_counts"]:
            throughput = statistics.median(baseline_throughput[worker])
            speedup = throughput / one
            worker_summary.append({
                "worker_count": worker,
                "median_analyses_per_second": throughput,
                "speedup": speedup,
                "parallel_efficiency": speedup / worker,
            })
        return {
            "schema": "ASAP_BLOCK_V9_3_CORE5B_FORMAL_SUMMARY_V1",
            "profile": self.profile,
            "mathematical_request_count": plan["mathematical_request_count"],
            "solver_execution_count": terminal_count,
            "completed_run_ids": sorted(completed_ids),
            "worker_semantic_mismatch_count": 0,
            "single_request_runtime_excluded_from_algorithmic_regression": True,
            "run_metrics": run_metrics,
            "worker_summary": worker_summary,
        }


def analyze_core5_formal_artifacts(root: Path | str) -> Mapping[str, Any]:
    """Validate a completed formal envelope without accepting bounded V2 data."""

    root = Path(root)
    try:
        metadata = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
        checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
        summary = json.loads((root / "formal_summary.json").read_text(encoding="utf-8"))
        seal = json.loads(
            (root / "formal_authorization_seal.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise Core5FormalContractError("incomplete CORE-5 formal artifact envelope") from exc
    if metadata.get("schema") != CORE5_FORMAL_RUN_SCHEMA:
        raise Core5FormalContractError("formal analyzer rejects non-formal run schema")
    if checkpoint.get("schema") != CORE5_FORMAL_CHECKPOINT_SCHEMA:
        raise Core5FormalContractError("formal analyzer rejects checkpoint schema")
    config = load_config(root / "run_config.yaml", expected_core="CORE-5")
    profile = config["scalability"].get("profile")
    if profile not in {CORE5A_PROFILE, CORE5B_PROFILE}:
        raise Core5FormalContractError("formal analyzer rejects bounded profile")
    runner = Core5FormalRunner(config)
    plan = runner.describe()
    expected_seal = {
        "schema": "ASAP_BLOCK_V9_3_CORE5_FORMAL_AUTHORIZATION_SEAL_V1",
        "profile": profile,
        "config_hash": config_hash(config),
        "plan_hash": plan["plan_hash"],
        "output_root": config["execution"]["output_root"],
        "taskset_store": config["execution"]["taskset_store"],
    }
    if seal != expected_seal:
        raise Core5FormalContractError(
            "formal analyzer rejects authorization-seal mismatch"
        )
    if (
        metadata.get("profile") != profile
        or checkpoint.get("profile") != profile
        or summary.get("profile") != profile
        or metadata.get("config_hash") != config_hash(config)
        or checkpoint.get("config_hash") != config_hash(config)
        or metadata.get("plan_hash") != plan["plan_hash"]
        or checkpoint.get("plan_hash") != plan["plan_hash"]
    ):
        raise Core5FormalContractError("CORE-5 formal profile/config/plan mismatch")
    if checkpoint.get("phase") != "COMPLETED":
        raise Core5FormalContractError("formal analyzer requires a completed run")
    for field in ("mathematical_request_count", "solver_execution_count"):
        if summary.get(field) != plan[field]:
            raise Core5FormalContractError(f"formal count mismatch: {field}")
    if profile == CORE5A_PROFILE:
        expected_runs = {
            cell.cell_id: {
                "worker_count": 1,
                "platform": {
                    "cores": [cell.processors], "task_count": [cell.task_count],
                },
                "period_min": cell.period_min,
                "period_max": cell.period_max,
                "utilizations": [fraction_text(cell.utilization)],
            }
            for cell in expand_core5a_cells(config)
        }
    else:
        expected_runs = {
            f"w{row['worker_count']}-r{row['repetition']}": {
                "worker_count": row["worker_count"],
                "platform": {"cores": [4], "task_count": [10]},
                "period_min": 40,
                "period_max": 200,
                "utilizations": config["scalability"]["utilization_points"],
            }
            for row in core5b_execution_schedule(config)
        }
    completed = summary.get("completed_run_ids")
    if (
        not isinstance(completed, list)
        or set(completed) != set(expected_runs)
        or set(checkpoint.get("completed_run_ids", [])) != set(expected_runs)
    ):
        raise Core5FormalContractError(
            "formal analyzer rejects missing worker/cell/repetition child"
        )
    for run_id, expected in expected_runs.items():
        child_root = root / "child_runs" / run_id
        declared_child_root = (
            Path(config["execution"]["output_root"]) / "child_runs" / run_id
        )
        child_config = load_config(
            child_root / "run_config.yaml", expected_core="CORE-5"
        )
        child_metadata = json.loads(
            (child_root / "run_metadata.json").read_text(encoding="utf-8")
        )
        if (
            child_metadata.get("config_hash") != config_hash(child_config)
            or child_config["scalability"].get("profile") != profile
            or child_config["platform"] != expected["platform"]
            or child_config["generation"]["period_min"] != expected["period_min"]
            or child_config["generation"]["period_max"] != expected["period_max"]
            or child_config["grid"]["utilization_points"] != expected["utilizations"]
            or child_config["analysis"]["worker_count"] != expected["worker_count"]
            or child_config["energy"] != config["energy"]
            or child_config["execution"]["taskset_store"]
            != config["execution"]["taskset_store"]
            or child_config["execution"]["output_root"] != str(declared_child_root)
        ):
            raise Core5FormalContractError(
                f"formal analyzer rejects mixed child config: {run_id}"
            )
    return summary
