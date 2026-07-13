"""CORE-5 single-axis scalability cell expansion and production runner."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Dict, Mapping, Optional, Sequence

from .config import ConfigError, canonical_json, config_hash, domain_hash, dump_config
from .core5_aggregation import aggregate_core5
from .execution_engine import AttemptExecution, ExecutionEngine, ExecutionPlanItem
from .resource_measurement import RESOURCE_OBSERVATION_COLUMNS
from .result_writer import (
    ATTEMPT_COLUMNS, FAILURE_COLUMNS, GENERATED_COLUMNS, REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, append_csv_row, atomic_write_json,
    read_csv, write_csv, write_file_hashes,
)
from .taskset_store import prepare_service_curve


SCALABILITY_CELL_COLUMNS = (
    "scalability_cell_id", "scaling_axis", "level_index", "level_id",
    "level_value", "M", "task_n", "period_min", "period_max",
    "utilization", "worker_count", "variants", "tasksets_requested",
    "analysis_ids_json", "cell_wall_seconds", "terminal_analysis_count",
    "throughput_analyses_per_second",
)


@dataclass(frozen=True)
class ScalabilityCell:
    scaling_axis: str
    level_index: int
    level_id: str
    level_value: str
    processors: int
    task_count: int
    period_min: int
    period_max: int
    utilization: str
    worker_count: int
    cell_id: str

    def mathematical_input(self) -> tuple[Any, ...]:
        return (
            self.processors, self.task_count, self.period_min, self.period_max,
            self.utilization,
        )


def expand_scalability_cells(config: Mapping[str, Any]) -> tuple[ScalabilityCell, ...]:
    scale = config["scalability"]
    base = {
        "processors": scale["core_counts"][0],
        "task_count": scale["task_counts"][0],
        "period": scale["period_ranges"][0],
        "utilization": scale["utilization_points"][0],
        "worker_count": scale["worker_counts"][0],
    }
    specs = []
    specs.extend(("task_count", i, str(value), value) for i, value in enumerate(scale["task_counts"]))
    specs.extend(("core_count", i, str(value), value) for i, value in enumerate(scale["core_counts"]))
    specs.extend(("period_range", i, value["id"], value) for i, value in enumerate(scale["period_ranges"]))
    if len(scale["utilization_points"]) > 1:
        specs.extend(("utilization", i, str(value), value) for i, value in enumerate(scale["utilization_points"]))
    specs.extend(("worker_count", i, str(value), value) for i, value in enumerate(scale["worker_counts"]))
    cells = []
    for axis, index, level_id, value in specs:
        processors = value if axis == "core_count" else base["processors"]
        task_count = value if axis == "task_count" else base["task_count"]
        period = value if axis == "period_range" else base["period"]
        utilization = value if axis == "utilization" else base["utilization"]
        workers = value if axis == "worker_count" else base["worker_count"]
        identity_input = {
            "experiment_id": config["experiment_id"], "axis": axis,
            "level_index": index, "level_id": level_id, "M": processors,
            "task_n": task_count, "period_min": period["min"],
            "period_max": period["max"], "utilization": utilization,
            "worker_count": workers, "variants": scale["variants"],
        }
        cells.append(ScalabilityCell(
            axis, index, level_id, canonical_json(value), processors, task_count,
            period["min"], period["max"], utilization, workers,
            domain_hash("ASAP_BLOCK:V9.3:SCALABILITY_CELL:v1", identity_input),
        ))
    return tuple(cells)


def assert_single_axis_isolation(config: Mapping[str, Any], cell: ScalabilityCell) -> None:
    scale = config["scalability"]
    baseline = (
        scale["core_counts"][0], scale["task_counts"][0],
        scale["period_ranges"][0]["min"], scale["period_ranges"][0]["max"],
        scale["utilization_points"][0], scale["worker_counts"][0],
    )
    actual = (
        cell.processors, cell.task_count, cell.period_min, cell.period_max,
        cell.utilization, cell.worker_count,
    )
    slots = {
        "core_count": {0}, "task_count": {1}, "period_range": {2, 3},
        "utilization": {4}, "worker_count": {5},
    }[cell.scaling_axis]
    if any(actual[index] != baseline[index] for index in range(6) if index not in slots):
        raise ConfigError(f"scalability cell changes more than {cell.scaling_axis}")


class ResourceExecutionEngine(ExecutionEngine):
    def _observe_attempt(
        self, item: ExecutionPlanItem, attempt_row: Mapping[str, Any],
        execution: AttemptExecution,
    ) -> None:
        append_csv_row(
            self.root / "attempt_resource_observations.csv",
            RESOURCE_OBSERVATION_COLUMNS,
            {
                "attempt_id": attempt_row["attempt_id"],
                "analysis_id": item.analysis_id,
                "peak_rss_kib": execution.peak_rss_kib
                if execution.peak_rss_kib is not None else "UNAVAILABLE",
                "peak_rss_scope": execution.peak_rss_scope,
                "peak_rss_unit": "KiB" if execution.peak_rss_kib is not None else "UNAVAILABLE",
            },
        )


@dataclass(frozen=True)
class Core5Outcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


class Core5ScalabilityRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.root = Path(config["execution"]["output_root"])
        self.identity = config_hash(config)

    def describe(self, *, max_cells: Optional[int] = None) -> Dict[str, Any]:
        cells = list(expand_scalability_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        per_cell = self.config["grid"]["tasksets_per_cell"] * len(self.config["analysis"]["variants"])
        return {
            "experiment_id": self.config["experiment_id"], "core": "CORE-5",
            "cell_count": len(cells), "request_count": len(cells) * per_cell,
            "hard_analysis_limit": self.config["scalability"]["max_analyses"],
            "cells": [self._cell_row(cell) for cell in cells],
        }

    @staticmethod
    def _cell_row(cell: ScalabilityCell) -> Dict[str, Any]:
        return {
            "scalability_cell_id": cell.cell_id, "scaling_axis": cell.scaling_axis,
            "level_index": cell.level_index, "level_id": cell.level_id,
            "level_value": cell.level_value, "M": cell.processors,
            "task_n": cell.task_count, "period_min": cell.period_min,
            "period_max": cell.period_max, "utilization": cell.utilization,
            "worker_count": cell.worker_count,
        }

    def _initialize(self, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        metadata_path = self.root / "run_metadata.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("config_hash") != self.identity:
                raise ConfigError("CORE-5 configuration hash mismatch")
            if not resume:
                raise ConfigError("CORE-5 run directory exists; use --resume")
        else:
            atomic_write_json(metadata_path, {
                "schema": "ASAP_BLOCK_V9_3_CORE5_RUN_V1",
                "config_hash": self.identity, "formal_large_scale_run": False,
                "parallel_throughput_is_not_algorithmic_complexity": True,
            })
            dump_config(self.config, self.root / "run_config.yaml")

    def run(
        self, *, resume: bool = False, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Core5Outcome:
        self._initialize(resume)
        cells = list(expand_scalability_cells(self.config))
        if max_cells is not None:
            if max_cells <= 0:
                raise ConfigError("max_cells must be positive")
            cells = cells[:max_cells]
        per_cell_tasksets = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            if max_tasksets <= 0:
                raise ConfigError("max_tasksets must be positive")
            per_cell_tasksets = min(per_cell_tasksets, max_tasksets)
        requested = len(cells) * per_cell_tasksets * len(self.config["analysis"]["variants"])
        if requested > self.config["scalability"]["max_analyses"]:
            raise ConfigError(
                f"CORE-5 request count {requested} exceeds hard limit "
                f"{self.config['scalability']['max_analyses']}"
            )
        cell_rows = []
        for cell in cells:
            assert_single_axis_isolation(self.config, cell)
            child = deepcopy(self.config)
            child["experiment_id"] = f"{self.config['experiment_id']}::{cell.cell_id}"
            child["platform"] = {"cores": [cell.processors], "task_count": [cell.task_count]}
            child["generation"]["period_min"] = cell.period_min
            child["generation"]["period_max"] = cell.period_max
            child["grid"]["utilization_points"] = [cell.utilization]
            child["grid"]["tasksets_per_cell"] = per_cell_tasksets
            child["analysis"]["worker_count"] = cell.worker_count
            child_root = self.root / "cell_runs" / cell.cell_id
            child["execution"]["output_root"] = str(child_root)
            # Service values are the same declared curve, but the validated
            # prefix must cover this cell's own period/deadline scale.
            service = prepare_service_curve(child, child_root / "service_material")
            started = time.perf_counter()
            outcome = ResourceExecutionEngine(child, service_override=service).run(
                resume=resume, max_tasksets=per_cell_tasksets
            )
            elapsed = time.perf_counter() - started
            analysis_ids = [
                row["analysis_id"] for row in read_csv(child_root / "analysis_requests.csv")
            ]
            cell_rows.append({
                **self._cell_row(cell),
                "variants": canonical_json(self.config["analysis"]["variants"]),
                "tasksets_requested": per_cell_tasksets,
                "analysis_ids_json": canonical_json(analysis_ids),
                "cell_wall_seconds": f"{elapsed:.9f}",
                "terminal_analysis_count": outcome.terminal,
                "throughput_analyses_per_second": outcome.terminal / elapsed if elapsed else None,
            })
        self._materialize_children(cell_rows)
        summary = aggregate_core5(self.root)
        stopped = bool(summary.get("p0_count", 0))
        atomic_write_json(self.root / "checkpoint.json", {
            "config_hash": self.identity,
            "completed_scalability_cell_ids": [row["scalability_cell_id"] for row in cell_rows],
            "requested_count": requested,
            "terminal_count": len(read_csv(self.root / "per_taskset_results.csv")),
            "stop_requested": stopped,
        })
        write_file_hashes(self.root)
        return Core5Outcome(
            self.root, requested, len(read_csv(self.root / "per_taskset_results.csv")),
            stopped, summary,
        )

    def _materialize_children(self, cell_rows: Sequence[Mapping[str, Any]]) -> None:
        aggregate_files = {
            "generated_tasksets.csv": GENERATED_COLUMNS,
            "analysis_requests.csv": REQUEST_COLUMNS,
            "analysis_attempts.csv": ATTEMPT_COLUMNS,
            "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
            "per_task_results.csv": TASK_RESULT_COLUMNS,
            "failures.csv": FAILURE_COLUMNS,
        }
        unique_fields = {
            "generated_tasksets.csv": ("taskset_id",),
            "analysis_requests.csv": ("analysis_id",),
            "analysis_attempts.csv": ("attempt_id",),
            "per_taskset_results.csv": ("analysis_id",),
            "per_task_results.csv": ("analysis_id", "task_id"),
            "failures.csv": ("analysis_id", "code"),
        }
        for filename, columns in aggregate_files.items():
            deduped = {}
            for path in sorted((self.root / "cell_runs").glob("*/" + filename)):
                for row in read_csv(path):
                    key = tuple(row.get(field, "") for field in unique_fields[filename])
                    if key in deduped and canonical_json(deduped[key]) != canonical_json(row):
                        raise ConfigError(f"conflicting duplicate in {filename}: {key}")
                    deduped[key] = row
            write_csv(self.root / filename, columns, deduped.values())
        write_csv(self.root / "scalability_cells.csv", SCALABILITY_CELL_COLUMNS, cell_rows)
        observations = []
        for path in sorted((self.root / "cell_runs").glob("*/attempt_resource_observations.csv")):
            observations.extend(read_csv(path))
        write_csv(
            self.root / "attempt_resource_observations.csv",
            RESOURCE_OBSERVATION_COLUMNS, observations,
        )


def analyze_core5(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return aggregate_core5(Path(config["execution"]["output_root"]))
