"""Production CORE-4 paired sensitivity runner over the shared v9.3 engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from fractions import Fraction
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import asap_block_rta_v9_3 as rta_core

from .cell_model import Cell, expand_cells
from .config import (
    ConfigError, canonical_json, config_hash, domain_hash, dump_config, fraction_text,
)
from .core4_aggregation import aggregate_core4
from .execution_engine import ExecutionEngine
from .monotonicity import MonotonicityStatus, service_curve_relation
from .paired_sweep import make_sweep, paired_analysis_id
from .result_writer import (
    ATTEMPT_COLUMNS, FAILURE_COLUMNS, GENERATED_COLUMNS, REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, atomic_write_json, read_csv,
    write_csv, write_file_hashes,
)
from .taskset_store import ServiceCurveMaterial, StoredTaskset, TasksetStore, prepare_service_curve


SENSITIVITY_REQUEST_COLUMNS = (
    "sweep_id", "base_taskset_id", "base_taskset_hash", "taskset_index",
    "parameter_name", "ordered_parameter_levels", "level_index",
    "level_encoding", "variant", "analysis_id", "analysis_input_hash",
    "availability", "availability_reason", "service_curve_relation_to_previous",
    "paired_analysis_ids",
)


@dataclass(frozen=True)
class Core4Outcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


def scale_taskset_power(stored: StoredTaskset, scale: Fraction) -> StoredTaskset:
    """Apply an exact analysis-only power scale without changing generation identity."""

    if scale <= 0:
        raise ConfigError("power scale must be positive")
    tasks = tuple(replace(task, power=Fraction(task.power) * scale) for task in stored.tasks)
    payload = tuple({
        **row, "P": fraction_text(Fraction(str(row["P"])) * scale),
    } for row in stored.task_payload)
    power_hash = domain_hash(
        "ASAP_BLOCK:V9.3:SENSITIVITY_POWER_VECTOR:v1",
        [{"task_id": row["task_id"], "P": row["P"]} for row in payload],
    )
    return replace(stored, tasks=tasks, task_payload=payload, power_hash=power_hash)


class FrozenStoreView:
    """Present one frozen base taskset under analysis-only parameter changes."""

    def __init__(
        self,
        base_store: TasksetStore,
        base_cells: Mapping[tuple[int, int, str], Cell],
        power_scale: Fraction,
    ) -> None:
        self.base_store = base_store
        self.base_cells = dict(base_cells)
        self.power_scale = power_scale

    def get_or_create(self, cell: Cell, taskset_index: int) -> StoredTaskset:
        key = (cell.processors, cell.task_count, fraction_text(cell.utilization))
        base = self.base_cells.get(key)
        if base is None:
            raise ConfigError(f"no frozen CORE-4 base cell for {key}")
        stored = self.base_store.get_or_create(base, taskset_index)
        return scale_taskset_power(stored, self.power_scale)


class Core4SensitivityRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.root = Path(config["execution"]["output_root"])
        self.identity = config_hash(config)

    def _levels(self) -> list[tuple[str, list[Any]]]:
        axes = self.config["sensitivity"]["axes"]
        return [
            ("initial_energy", list(axes["initial_energy"]["values"])),
            ("service_curve", list(axes["service_curve"]["variants"])),
            ("power_scale", list(axes["power_scale"]["values"])),
            ("method", list(axes["method"]["variants"])),
        ]

    def describe(self, *, max_cells: Optional[int] = None) -> Dict[str, Any]:
        cells = []
        for axis, levels in self._levels():
            for index, level in enumerate(levels):
                cells.append({
                    "parameter_name": axis, "level_index": index,
                    "level_encoding": canonical_json(level),
                    "availability": level.get("availability", "AVAILABLE")
                    if isinstance(level, dict) else "AVAILABLE",
                })
        if max_cells is not None:
            cells = cells[:max_cells]
        available = sum(row["availability"] == "AVAILABLE" for row in cells)
        methods = len(self.config["analysis"]["variants"])
        return {
            "experiment_id": self.config["experiment_id"], "core": "CORE-4",
            "cell_count": len(cells), "cells": cells,
            "tasksets_per_cell": self.config["grid"]["tasksets_per_cell"],
            "maximum_solver_requests": available * methods
            * self.config["grid"]["tasksets_per_cell"],
            "finite_sample_consistency_check_only": True,
        }

    def _initialize(self, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        metadata_path = self.root / "run_metadata.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("config_hash") != self.identity:
                raise ConfigError("CORE-4 configuration hash mismatch")
            if not resume:
                raise ConfigError("CORE-4 run directory exists; use --resume")
        else:
            atomic_write_json(metadata_path, {
                "schema": "ASAP_BLOCK_V9_3_CORE4_RUN_V1",
                "config_hash": self.identity,
                "formal_large_scale_run": False,
                "finite_sample_consistency_check_only": True,
            })
            dump_config(self.config, self.root / "run_config.yaml")

    def _service_materials(self) -> tuple[ServiceCurveMaterial, Dict[str, ServiceCurveMaterial], Dict[str, str]]:
        base = prepare_service_curve(self.config, self.root / "service_material" / "base")
        materials = {self.config["energy"]["service_curve"]["id"]: base}
        relations: Dict[str, str] = {}
        previous: Optional[ServiceCurveMaterial] = None
        for spec in self.config["sensitivity"]["axes"]["service_curve"]["variants"]:
            if spec["availability"] != "AVAILABLE":
                relations[spec["id"]] = "DEPENDENCY_UNAVAILABLE"
                previous = None
                continue
            if spec["id"] in materials:
                material = materials[spec["id"]]
            else:
                child = deepcopy(self.config)
                child["energy"]["service_curve"] = {
                    "id": spec["id"], "system_template": spec["system_template"],
                    "horizon": spec["horizon"],
                }
                material = prepare_service_curve(
                    child, self.root / "service_material" / spec["id"]
                )
                materials[spec["id"]] = material
            relation = "FIRST_LEVEL" if previous is None else service_curve_relation(
                previous.values, material.values
            )
            if previous is not None and relation not in {"RIGHT_STRONGER", "EQUAL"}:
                raise ConfigError(
                    "ordered service-curve levels are not pointwise weak-to-strong"
                )
            relations[spec["id"]] = relation
            previous = material
        return base, materials, relations

    def run(
        self, *, resume: bool = False, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Core4Outcome:
        if max_cells is not None and max_cells <= 0:
            raise ConfigError("max_cells must be positive")
        if max_tasksets is not None and max_tasksets <= 0:
            raise ConfigError("max_tasksets must be positive")
        self._initialize(resume)
        base_service, services, service_relations = self._service_materials()
        base_cells: Dict[tuple[int, int, str], Cell] = {}
        for cell in expand_cells(self.config):
            base_cells.setdefault(
                (cell.processors, cell.task_count, fraction_text(cell.utilization)), cell
            )
        base_store = TasksetStore(
            Path(self.config["execution"]["taskset_store"]), self.config, base_service
        )
        per_cell = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            per_cell = min(per_cell, max_tasksets)
        start = self.config["grid"].get("taskset_index_start", 0)
        base_tasksets: Dict[tuple[tuple[int, int, str], int], StoredTaskset] = {}
        for key, cell in base_cells.items():
            for taskset_index in range(start, start + per_cell):
                base_tasksets[(key, taskset_index)] = base_store.get_or_create(cell, taskset_index)

        request_rows: list[Dict[str, Any]] = []
        selected_cells = 0
        for axis, levels in self._levels():
            sweep = make_sweep(self.config["experiment_id"], axis, levels)
            for level_index, level in enumerate(levels):
                if max_cells is not None and selected_cells >= max_cells:
                    break
                selected_cells += 1
                availability = level.get("availability", "AVAILABLE") if isinstance(level, dict) else "AVAILABLE"
                reason = level.get("reason", "") if isinstance(level, dict) else ""
                variants = [level] if axis == "method" else list(self.config["analysis"]["variants"])
                e0 = (
                    str(level) if axis == "initial_energy" else
                    self.config["energy"]["initial_energy_values"][0]
                )
                scale = Fraction(str(level)) if axis == "power_scale" else Fraction(1)
                service_spec = (
                    level if axis == "service_curve" else self.config["energy"]["service_curve"]
                )
                service_id = service_spec["id"]
                level_encoding = sweep.level_encodings[level_index]
                child_results: list[Mapping[str, str]] = []
                child_generated: list[Mapping[str, str]] = []
                if availability == "AVAILABLE":
                    child = deepcopy(self.config)
                    child["experiment_id"] = (
                        f"{self.config['experiment_id']}::{axis}::{level_index}"
                    )
                    child["energy"]["initial_energy_values"] = [e0]
                    child["energy"]["service_curve"] = {
                        "id": service_id,
                        "system_template": service_spec["system_template"],
                        "horizon": service_spec["horizon"],
                    }
                    child["analysis"]["variants"] = variants
                    child_root = self.root / "cell_runs" / axis / f"level_{level_index:03d}"
                    child["execution"]["output_root"] = str(child_root)
                    child["grid"]["tasksets_per_cell"] = per_cell
                    view = FrozenStoreView(base_store, base_cells, scale)
                    engine = ExecutionEngine(
                        child, service_override=services[service_id], store_override=view
                    )
                    engine.run(resume=resume, max_tasksets=per_cell)
                    child_results = read_csv(child_root / "per_taskset_results.csv")
                    child_generated = read_csv(child_root / "generated_tasksets.csv")
                generated_index = {
                    row["taskset_id"]: int(row["taskset_index"]) for row in child_generated
                }
                by_id = {row["taskset_id"]: row for row in child_results}
                for key, base_stored in base_tasksets.items():
                    taskset_index = key[1]
                    for variant in variants:
                        match = next((
                            row for row in child_results
                            if row["analysis_variant"] == variant
                            and generated_index.get(row["taskset_id"]) == taskset_index
                            and int(row["M"]) == key[0][0]
                            and int(row["task_n"]) == key[0][1]
                        ), None)
                        analysis_id = (
                            match["analysis_id"] if match else domain_hash(
                                "ASAP_BLOCK:V9.3:UNAVAILABLE_SENSITIVITY_REQUEST:v1",
                                {
                                    "sweep": sweep.sweep_id,
                                    "base": base_stored.semantic_hash,
                                    "level": level_encoding, "variant": variant,
                                },
                            )
                        )
                        request_rows.append({
                            "sweep_id": sweep.sweep_id,
                            "base_taskset_id": base_stored.taskset_id,
                            "base_taskset_hash": base_stored.semantic_hash,
                            "taskset_index": taskset_index,
                            "parameter_name": axis,
                            "ordered_parameter_levels": canonical_json(sweep.level_encodings),
                            "level_index": level_index, "level_encoding": level_encoding,
                            "variant": variant, "analysis_id": analysis_id,
                            "analysis_input_hash": domain_hash(
                                "ASAP_BLOCK:V9.3:SENSITIVITY_INPUT:v1",
                                {
                                    "base": base_stored.semantic_hash, "e0": e0,
                                    "service": services[service_id].identity
                                    if availability == "AVAILABLE" else service_id,
                                    "power_scale": fraction_text(scale), "variant": variant,
                                },
                            ),
                            "availability": availability,
                            "availability_reason": reason,
                            "service_curve_relation_to_previous": (
                                service_relations.get(service_id, "NOT_APPLICABLE")
                                if axis == "service_curve" else "NOT_APPLICABLE"
                            ),
                            "paired_analysis_ids": "",
                        })
            if max_cells is not None and selected_cells >= max_cells:
                break

        self._fill_pair_ids(request_rows)
        self._materialize_children(request_rows, base_tasksets)
        summary = aggregate_core4(self.root)
        stopped = bool(summary["p0_monotonicity_violation_count"])
        if stopped:
            self._record_monotonicity_failures()
        atomic_write_json(self.root / "checkpoint.json", {
            "config_hash": self.identity,
            "completed_analysis_ids": sorted(
                row["analysis_id"] for row in read_csv(self.root / "per_taskset_results.csv")
            ),
            "requested_count": len(request_rows),
            "terminal_count": len(read_csv(self.root / "per_taskset_results.csv")),
            "stop_requested": stopped,
        })
        write_file_hashes(self.root)
        return Core4Outcome(
            self.root, len(request_rows),
            len(read_csv(self.root / "per_taskset_results.csv")), stopped, summary,
        )

    def _fill_pair_ids(self, rows: list[Dict[str, Any]]) -> None:
        groups: Dict[tuple[str, str, str], list[Dict[str, Any]]] = {}
        for row in rows:
            variant = "METHOD_PAIR" if row["parameter_name"] == "method" else row["variant"]
            groups.setdefault((row["sweep_id"], row["base_taskset_hash"], variant), []).append(row)
        for (sweep_id, base_hash, variant), members in groups.items():
            members.sort(key=lambda row: int(row["level_index"]))
            ids = []
            for left, right in zip(members, members[1:]):
                pair_variant = (
                    f"{left['variant']}->{right['variant']}"
                    if left["parameter_name"] == "method" else variant
                )
                ids.append(paired_analysis_id(
                    sweep_id, base_hash, pair_variant,
                    left["level_encoding"], right["level_encoding"],
                ))
            encoded = canonical_json(ids)
            for member in members:
                member["paired_analysis_ids"] = encoded

    def _materialize_children(
        self,
        sensitivity_requests: list[Mapping[str, Any]],
        base_tasksets: Mapping[Any, StoredTaskset],
    ) -> None:
        aggregate_files = {
            "analysis_requests.csv": REQUEST_COLUMNS,
            "analysis_attempts.csv": ATTEMPT_COLUMNS,
            "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
            "per_task_results.csv": TASK_RESULT_COLUMNS,
            "failures.csv": FAILURE_COLUMNS,
        }
        for filename, columns in aggregate_files.items():
            rows = []
            for path in sorted((self.root / "cell_runs").glob("**/" + filename)):
                rows.extend(read_csv(path))
            unique_keys = {
                "analysis_requests.csv": ("analysis_id",),
                "analysis_attempts.csv": ("attempt_id",),
                "per_taskset_results.csv": ("analysis_id",),
                "per_task_results.csv": ("analysis_id", "task_id"),
                "failures.csv": ("analysis_id", "code"),
            }[filename]
            deduped = {}
            for row in rows:
                key = tuple(row.get(field, "") for field in unique_keys)
                if key in deduped and canonical_json(deduped[key]) != canonical_json(row):
                    raise ConfigError(f"conflicting duplicate in {filename}: {key}")
                deduped[key] = row
            write_csv(self.root / filename, columns, deduped.values())
        generated = {stored.taskset_id: stored.generated_row() for stored in base_tasksets.values()}
        write_csv(self.root / "generated_tasksets.csv", GENERATED_COLUMNS, generated.values())
        write_csv(
            self.root / "sensitivity_requests.csv", SENSITIVITY_REQUEST_COLUMNS,
            sensitivity_requests,
        )

    def _record_monotonicity_failures(self) -> None:
        failures = read_csv(self.root / "failures.csv")
        tasks = read_csv(self.root / "per_task_results.csv")
        results = {row["analysis_id"]: row for row in read_csv(self.root / "per_taskset_results.csv")}
        for pair in read_csv(self.root / "monotonicity_checks.csv"):
            if pair["monotonicity_status"] != MonotonicityStatus.VIOLATION.value:
                continue
            failure_path = self.root / "failure_inputs" / f"{pair['paired_analysis_id']}.json"
            atomic_write_json(failure_path, {
                "pair": pair,
                "left_result": results.get(pair["left_analysis_id"]),
                "right_result": results.get(pair["right_analysis_id"]),
                "left_tasks": [row for row in tasks if row["analysis_id"] == pair["left_analysis_id"]],
                "right_tasks": [row for row in tasks if row["analysis_id"] == pair["right_analysis_id"]],
                "finite_sample_consistency_check_only": True,
            })
            failures.append({
                "severity": "P0", "stage": "MONOTONICITY",
                "analysis_id": pair["paired_analysis_id"], "cell_id": "",
                "taskset_id": pair["base_taskset_id"], "variant": pair["variant"],
                "code": "MONOTONICITY_VIOLATION",
                "detail": pair["violation_reasons"], "traceback": "",
                "failure_input": str(failure_path),
            })
        write_csv(self.root / "failures.csv", FAILURE_COLUMNS, failures)


def analyze_core4(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return aggregate_core4(Path(config["execution"]["output_root"]))
