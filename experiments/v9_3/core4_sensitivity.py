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
from .core4_contract import (
    CORE4_CHECKPOINT_SCHEMA,
    CORE4_RUN_SCHEMA,
    SENSITIVITY_REQUEST_COLUMNS,
    Core4ContractError,
    core4_analysis_input_hash,
    validate_core4_artifact_contract,
    validate_core4_hash_manifest,
    validate_core4_resume_envelope,
    write_core4_hash_manifest,
)
from .core4_aggregation import aggregate_core4, analyze_core4_artifacts
from .execution_engine import ExecutionEngine
from .formal_authorization import (
    requires_formal_authorization,
    verify_authorization,
)
from .monotonicity import (
    MonotonicityStatus,
    service_curve_relation,
    terminal_status_class,
)
from .paired_sweep import make_sweep, paired_analysis_id
from .result_writer import (
    ATTEMPT_COLUMNS, FAILURE_COLUMNS, GENERATED_COLUMNS, REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS, ResultWriterError,
    atomic_write_json, read_csv, validate_csv_header, write_csv,
)
from .taskset_store import ServiceCurveMaterial, StoredTaskset, TasksetStore, prepare_service_curve

@dataclass(frozen=True)
class Core4Outcome:
    output_root: Path
    planned_sensitivity_row_count: int
    available_solver_request_count: int
    expected_terminal_count: int
    actual_terminal_count: int
    dependency_unavailable_row_count: int
    technical_failure_count: int
    stopped: bool
    summary: Mapping[str, Any]

    @property
    def requested(self) -> int:
        """Compatibility alias; this is the planned sensitivity-row count."""

        return self.planned_sensitivity_row_count

    @property
    def terminal(self) -> int:
        """Compatibility alias for actual solver terminals."""

        return self.actual_terminal_count


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
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        authorization_path: Optional[Path] = None,
        source_config_path: Optional[Path] = None,
        prepared_config_path: Optional[Path] = None,
    ) -> None:
        self.config = dict(config)
        self.root = Path(config["execution"]["output_root"])
        self.identity = config_hash(config)
        self._authorization_path = authorization_path
        self._source_config_path = source_config_path
        self._prepared_config_path = prepared_config_path
        self._authorization_seal: Optional[Dict[str, Any]] = None

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
        base_cell_count = len({
            (cell.processors, cell.task_count, fraction_text(cell.utilization))
            for cell in expand_cells(self.config)
        })
        base_taskset_count = (
            base_cell_count * self.config["grid"]["tasksets_per_cell"]
        )
        planned_per_base = sum(
            1 if row["parameter_name"] == "method"
            else len(self.config["analysis"]["variants"])
            for row in cells
        )
        available_per_base = sum(
            (1 if row["parameter_name"] == "method"
             else len(self.config["analysis"]["variants"]))
            for row in cells if row["availability"] == "AVAILABLE"
        )
        unavailable_per_base = planned_per_base - available_per_base
        planned = planned_per_base * base_taskset_count
        available = available_per_base * base_taskset_count
        unavailable = unavailable_per_base * base_taskset_count
        result = {
            "experiment_id": self.config["experiment_id"], "core": "CORE-4",
            "cell_count": len(cells), "cells": cells,
            "tasksets_per_cell": self.config["grid"]["tasksets_per_cell"],
            "planned_sensitivity_row_count": planned,
            "available_solver_request_count": available,
            "expected_terminal_count": available,
            "actual_terminal_count": 0,
            "dependency_unavailable_row_count": unavailable,
            "technical_failure_count": 0,
            "finite_sample_consistency_check_only": True,
        }
        if self.config["sensitivity"].get("profile") == "formal-sustainability-v1":
            axis_counts: Dict[str, Dict[str, int]] = {}
            for axis, _levels in self._levels():
                axis_cells = [
                    row for row in cells if row["parameter_name"] == axis
                ]
                planned_axis_per_base = sum(
                    1 if axis == "method"
                    else len(self.config["analysis"]["variants"])
                    for _row in axis_cells
                )
                available_axis_per_base = sum(
                    (1 if axis == "method"
                     else len(self.config["analysis"]["variants"]))
                    for row in axis_cells
                    if row["availability"] == "AVAILABLE"
                )
                axis_planned = planned_axis_per_base * base_taskset_count
                axis_available = available_axis_per_base * base_taskset_count
                axis_counts[axis] = {
                    "planned_row_count": axis_planned,
                    "solver_request_count": axis_available,
                    "dependency_unavailable_count": axis_planned - axis_available,
                    "terminal_count": axis_available,
                }
            result["axis_counts"] = axis_counts
            result["solver_request_count"] = available
            result["total_terminal_count"] = available
        return result

    def _initialize(self, resume: bool) -> None:
        seal = verify_authorization(
            self.config,
            authorization_path=self._authorization_path,
            source_freeze_config=self._source_config_path,
            prepared_config=self._prepared_config_path,
            project_root=Path(__file__).resolve().parents[2],
        )
        self._authorization_seal = dict(seal)
        self.root.mkdir(parents=True, exist_ok=True)
        metadata_path = self.root / "run_metadata.json"
        seal_path = self.root / "formal_authorization_seal.json"
        counts = self._static_counts()
        if metadata_path.is_file():
            if not resume:
                raise ConfigError("CORE-4 run directory exists; use --resume")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if bool(metadata.get("formal_large_scale_run")):
                if not seal_path.is_file():
                    raise ConfigError(
                        "formal CORE-4 run is missing its authorization seal"
                    )
                persisted_seal = json.loads(
                    seal_path.read_text(encoding="utf-8")
                )
                if canonical_json(persisted_seal) != canonical_json(seal):
                    raise ConfigError(
                        "formal CORE-4 authorization changed during resume"
                    )
                if metadata.get("formal_authorization_id") != seal.get(
                    "authorization_id"
                ):
                    raise ConfigError(
                        "CORE-4 metadata/authorization seal mismatch"
                    )
            elif seal.get("formal_large_scale_run"):
                raise ConfigError("cannot promote a nonformal CORE-4 run")
            try:
                validate_core4_resume_envelope(
                    self.root,
                    expected_config_hash=self.identity,
                    expected_experiment_id=self.config["experiment_id"],
                    expected_counts={
                        key: counts[key]
                        for key in (
                            "planned_sensitivity_row_count",
                            "available_solver_request_count",
                            "expected_terminal_count",
                            "dependency_unavailable_row_count",
                        )
                    },
                )
            except Core4ContractError as exc:
                raise ConfigError(str(exc)) from exc
        else:
            existing = sorted(path.name for path in self.root.iterdir())
            if existing:
                raise ConfigError(
                    "CORE-4 output root has artifacts but no run_metadata.json"
                )
            if seal["formal_large_scale_run"]:
                atomic_write_json(seal_path, seal)
            atomic_write_json(metadata_path, {
                "schema": CORE4_RUN_SCHEMA,
                "experiment_id": self.config["experiment_id"],
                "core": "CORE-4",
                "config_hash": self.identity,
                **{
                    key: counts[key]
                    for key in (
                        "planned_sensitivity_row_count",
                        "available_solver_request_count",
                        "expected_terminal_count",
                        "dependency_unavailable_row_count",
                    )
                },
                "formal_large_scale_run": seal["formal_large_scale_run"],
                "formal_authorization_id": seal["authorization_id"],
                "finite_sample_consistency_check_only": True,
            })
            dump_config(self.config, self.root / "run_config.yaml")
            self._write_checkpoint(
                phase="INITIALIZED", actual_terminal_count=0,
                technical_failure_count=0, stop_requested=False,
            )

    def _static_counts(self, *, max_cells: Optional[int] = None) -> Dict[str, int]:
        description = self.describe(max_cells=max_cells)
        return {
            key: int(description[key])
            for key in (
                "planned_sensitivity_row_count",
                "available_solver_request_count",
                "expected_terminal_count",
                "actual_terminal_count",
                "dependency_unavailable_row_count",
                "technical_failure_count",
            )
        }

    def _write_checkpoint(
        self, *, phase: str, actual_terminal_count: int,
        technical_failure_count: int, stop_requested: bool,
        completed_analysis_ids: Optional[list[str]] = None,
    ) -> None:
        counts = self._static_counts()
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": CORE4_CHECKPOINT_SCHEMA,
            "core": "CORE-4",
            "config_hash": self.identity,
            "phase": phase,
            "planned_sensitivity_row_count": counts["planned_sensitivity_row_count"],
            "available_solver_request_count": counts["available_solver_request_count"],
            "expected_terminal_count": counts["expected_terminal_count"],
            "actual_terminal_count": int(actual_terminal_count),
            "dependency_unavailable_row_count": counts["dependency_unavailable_row_count"],
            "technical_failure_count": int(technical_failure_count),
            "completed_analysis_ids": sorted(completed_analysis_ids or []),
            "stop_requested": bool(stop_requested),
        })

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
                    **(
                        {"exact_scale": spec["exact_scale"]}
                        if "exact_scale" in spec else {}
                    ),
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
        catalog = []
        catalog_specs = (
            self.config["sensitivity"]["axes"]["service_curve"]["variants"]
            if self.config["sensitivity"].get("profile")
            == "formal-sustainability-v1"
            else []
        )
        for spec in catalog_specs:
            if spec["availability"] != "AVAILABLE":
                continue
            material = materials[spec["id"]]
            raw = json.loads(material.raw_spec)
            catalog.append({
                "identity": spec["id"],
                "scale": raw["exact_scale"],
                "source_template": raw["system_template"],
                "source_template_sha256": raw["source_template_sha256"],
                "curve_sha256": domain_hash(
                    "ASAP_BLOCK:V9.3:CORE4:CURVE_VALUES:v1",
                    raw["validated_prefix"],
                ),
                "semantic_hash": material.identity,
                "horizon": spec["horizon"],
                "point_count": len(material.values),
            })
        if catalog:
            atomic_write_json(self.root / "service_curve_catalog.json", {
                "schema": "ASAP_BLOCK_V9_3_CORE4_SERVICE_CURVE_CATALOG_V1",
                "curves": catalog,
            })
        return base, materials, relations

    def run(
        self, *, resume: bool = False, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Core4Outcome:
        if requires_formal_authorization(self.config) and (
            max_cells is not None or max_tasksets is not None
        ):
            raise ConfigError(
                "formal CORE-4 execution cannot be truncated; use --dry-run "
                "for inspection"
            )
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
                    child.pop("parameter_status", None)
                    child["sensitivity"]["profile"] = (
                        "formal-sustainability-child-v1"
                    )
                    child["parent_formal_authorization_id"] = (
                        self._authorization_seal["authorization_id"]
                        if self._authorization_seal else None
                    )
                    child["experiment_id"] = (
                        f"{self.config['experiment_id']}::{axis}::{level_index}"
                    )
                    child["energy"]["initial_energy_values"] = [e0]
                    child["energy"]["service_curve"] = {
                        "id": service_id,
                        "system_template": service_spec["system_template"],
                        "horizon": service_spec["horizon"],
                        **(
                            {"exact_scale": service_spec["exact_scale"]}
                            if "exact_scale" in service_spec else {}
                        ),
                    }
                    child["analysis"]["variants"] = variants
                    child_root = self.root / "cell_runs" / axis / f"level_{level_index:03d}"
                    child["execution"]["output_root"] = str(child_root)
                    child["grid"]["tasksets_per_cell"] = per_cell
                    view = FrozenStoreView(base_store, base_cells, scale)
                    engine = ExecutionEngine(
                        child, service_override=services[service_id], store_override=view
                    )
                    child_outcome = engine.run(resume=resume, max_tasksets=per_cell)
                    child_artifact_errors = []
                    for filename, columns in (
                        ("per_taskset_results.csv", TASKSET_RESULT_COLUMNS),
                        ("failures.csv", FAILURE_COLUMNS),
                    ):
                        path = child_root / filename
                        if not path.is_file():
                            child_artifact_errors.append(
                                f"missing child artifact: {filename}"
                            )
                            continue
                        try:
                            validate_csv_header(path, columns)
                        except ResultWriterError as exc:
                            child_artifact_errors.append(str(exc))
                    child_results = read_csv(child_root / "per_taskset_results.csv")
                    child_generated = read_csv(child_root / "generated_tasksets.csv")
                    child_failures = read_csv(child_root / "failures.csv")
                    expected_child_requests = len(base_tasksets) * len(variants)
                    technical_statuses = [
                        row for row in child_results
                        if terminal_status_class(
                            row.get("solver_status"),
                            outer_timeout=row.get("outer_timeout"),
                        ) in {"TECHNICAL_FAILURE", "DEPENDENCY_UNAVAILABLE"}
                    ]
                    p0_failures = [
                        row for row in child_failures if row.get("severity") == "P0"
                    ]
                    child_analysis_ids = [row.get("analysis_id", "") for row in child_results]
                    reasons = []
                    reasons.extend(child_artifact_errors)
                    if child_outcome.stopped:
                        reasons.append("child_outcome.stopped=True")
                    if child_outcome.requested != expected_child_requests:
                        reasons.append(
                            "child requested count does not match the CORE-4 level plan"
                        )
                    if child_outcome.terminal != child_outcome.requested:
                        reasons.append("child terminal count does not equal child requested count")
                    if child_outcome.terminal != len(child_results):
                        reasons.append("child outcome terminal count does not match materialized results")
                    if len(child_analysis_ids) != len(set(child_analysis_ids)):
                        reasons.append("child contains duplicate terminal analysis_id")
                    if p0_failures:
                        reasons.append("child failures.csv contains P0")
                    if technical_statuses:
                        reasons.append("child contains technical or unknown terminal status")
                    if reasons:
                        self._materialize_children(request_rows, base_tasksets)
                        return self._technical_failure_outcome(
                            code="CORE4_CHILD_EXECUTION_FAILURE",
                            detail="; ".join(reasons),
                            context={
                                "axis": axis,
                                "level_index": level_index,
                                "child_output_root": str(child_root),
                                "child_outcome": {
                                    "requested": child_outcome.requested,
                                    "terminal": child_outcome.terminal,
                                    "stopped": child_outcome.stopped,
                                    "status_counts": dict(child_outcome.status_counts),
                                },
                                "p0_failures": p0_failures,
                                "technical_terminals": technical_statuses,
                            },
                        )
                for key, base_stored in base_tasksets.items():
                    taskset_index = key[1]
                    for variant in variants:
                        match = None
                        if availability == "AVAILABLE":
                            matches = [
                                row for row in child_results
                                if row["analysis_variant"] == variant
                                and row["taskset_hash"] == base_stored.semantic_hash
                                and int(row["M"]) == key[0][0]
                                and int(row["task_n"]) == key[0][1]
                            ]
                            if len(matches) != 1:
                                self._materialize_children(request_rows, base_tasksets)
                                return self._technical_failure_outcome(
                                    code="CORE4_CHILD_PAIRING_FAILURE",
                                    detail=(
                                        "available child result does not map exactly once "
                                        "to its frozen base taskset and variant"
                                    ),
                                    context={
                                        "axis": axis, "level_index": level_index,
                                        "variant": variant,
                                        "base_taskset_hash": base_stored.semantic_hash,
                                        "match_count": len(matches),
                                    },
                                )
                            match = matches[0]
                        analysis_id = (
                            match["analysis_id"] if match is not None else domain_hash(
                                "ASAP_BLOCK:V9.3:UNAVAILABLE_SENSITIVITY_REQUEST:v1",
                                {
                                    "sweep": sweep.sweep_id,
                                    "base": base_stored.semantic_hash,
                                    "level": level_encoding, "variant": variant,
                                },
                            )
                        )
                        transformed = scale_taskset_power(base_stored, scale)
                        material = services.get(service_id) if availability == "AVAILABLE" else None
                        row = {
                            "experiment_id": self.config["experiment_id"],
                            "sweep_id": sweep.sweep_id,
                            "base_taskset_id": base_stored.taskset_id,
                            "base_taskset_hash": base_stored.semantic_hash,
                            "taskset_index": taskset_index,
                            "M": base_stored.processors,
                            "task_n": base_stored.task_count,
                            "base_priority_hash": base_stored.priority_hash,
                            "base_power_hash": base_stored.power_hash,
                            "base_service_curve_identity": base_stored.service_curve_reference,
                            "base_task_input_json": canonical_json(base_stored.task_payload),
                            "parameter_name": axis,
                            "ordered_parameter_levels": canonical_json(sweep.level_encodings),
                            "level_index": level_index, "level_encoding": level_encoding,
                            "variant": variant, "analysis_id": analysis_id,
                            "analysis_input_hash": "",
                            "exact_e0": fraction_text(Fraction(e0)),
                            "service_curve_declaration_id": service_id,
                            "service_curve_identity": material.identity if material else "",
                            "service_curve_values_json": (
                                canonical_json([fraction_text(value) for value in material.values])
                                if material else ""
                            ),
                            "power_scale": fraction_text(scale),
                            "analysis_power_hash": transformed.power_hash,
                            "analysis_task_input_json": canonical_json(transformed.task_payload),
                            "numerical_mode": self.config["analysis"]["numerical_mode"],
                            "availability": availability,
                            "availability_reason": reason,
                            "service_curve_relation_to_previous": (
                                service_relations.get(service_id, "NOT_APPLICABLE")
                                if axis == "service_curve" else "NOT_APPLICABLE"
                            ),
                            "paired_analysis_ids": "",
                        }
                        row["analysis_input_hash"] = core4_analysis_input_hash(row)
                        request_rows.append(row)
            if max_cells is not None and selected_cells >= max_cells:
                break

        self._fill_pair_ids(request_rows)
        try:
            self._materialize_children(request_rows, base_tasksets)
            summary = aggregate_core4(self.root)
        except Exception as exc:
            return self._technical_failure_outcome(
                code="CORE4_AGGREGATION_CONTRACT_FAILURE",
                detail=str(exc),
                context={"exception_type": type(exc).__name__},
            )
        stopped = bool(
            summary["p0_monotonicity_violation_count"]
            or summary["technical_failure_count"]
        )
        if stopped:
            self._record_monotonicity_failures()
        results = read_csv(self.root / "per_taskset_results.csv")
        completed_ids = [row["analysis_id"] for row in results]
        if stopped:
            self._write_checkpoint(
                phase="STOPPED", actual_terminal_count=len(results),
                technical_failure_count=int(summary["technical_failure_count"]),
                stop_requested=True, completed_analysis_ids=completed_ids,
            )
            write_core4_hash_manifest(self.root)
            validate_core4_hash_manifest(
                self.root, require_completed_files=False
            )
        else:
            self._write_checkpoint(
                phase="FINALIZING", actual_terminal_count=len(results),
                technical_failure_count=0, stop_requested=False,
                completed_analysis_ids=completed_ids,
            )
            write_core4_hash_manifest(self.root)
            validate_core4_hash_manifest(
                self.root, require_completed_files=True
            )
            validate_core4_artifact_contract(
                self.root, require_completed=False
            )
            self._write_checkpoint(
                phase="COMPLETED", actual_terminal_count=len(results),
                technical_failure_count=0, stop_requested=False,
                completed_analysis_ids=completed_ids,
            )
        counts = self._static_counts()
        return Core4Outcome(
            self.root,
            counts["planned_sensitivity_row_count"],
            counts["available_solver_request_count"],
            counts["expected_terminal_count"],
            len(results),
            counts["dependency_unavailable_row_count"],
            int(summary["technical_failure_count"]),
            stopped,
            summary,
        )

    def _technical_failure_outcome(
        self, *, code: str, detail: str, context: Mapping[str, Any]
    ) -> Core4Outcome:
        for name in (
            "paired_parameter_results.csv", "monotonicity_checks.csv",
            "sensitivity_summary.csv", "sensitivity_summary.json",
            "core4_plot_data.csv",
        ):
            path = self.root / name
            if path.is_file():
                path.unlink()
        failure_id = domain_hash(
            "ASAP_BLOCK:V9.3:CORE4_TECHNICAL_FAILURE:v1",
            {"code": code, "detail": detail, "context": context},
        )
        failure_path = self.root / "failure_inputs" / f"{failure_id}.json"
        atomic_write_json(failure_path, {
            "schema": "ASAP_BLOCK_V9_3_CORE4_TECHNICAL_FAILURE_V1",
            "failure_id": failure_id,
            "code": code,
            "detail": detail,
            "context": context,
        })
        failures_path = self.root / "failures.csv"
        failures = read_csv(failures_path)
        failures.append({
            "severity": "P0", "stage": "CORE4_CONTRACT",
            "analysis_id": failure_id, "cell_id": "", "taskset_id": "",
            "variant": "", "code": code, "detail": detail, "traceback": "",
            "failure_input": str(failure_path),
        })
        write_csv(failures_path, FAILURE_COLUMNS, failures)
        results = read_csv(self.root / "per_taskset_results.csv")
        technical_ids = {
            row["analysis_id"] for row in results
            if terminal_status_class(
                row.get("solver_status"), outer_timeout=row.get("outer_timeout")
            ) == "TECHNICAL_FAILURE"
        }
        technical_ids.update(
            row["analysis_id"] for row in failures if row.get("severity") == "P0"
        )
        technical_count = max(1, len(technical_ids))
        counts = self._static_counts()
        summary = {
            "finite_sample_consistency_check_only": True,
            "stopped": True,
            "technical_failure_code": code,
            "technical_failure_detail": detail,
            "planned_sensitivity_row_count": counts["planned_sensitivity_row_count"],
            "available_solver_request_count": counts["available_solver_request_count"],
            "expected_terminal_count": counts["expected_terminal_count"],
            "actual_terminal_count": len(results),
            "dependency_unavailable_row_count": counts["dependency_unavailable_row_count"],
            "technical_failure_count": technical_count,
        }
        atomic_write_json(self.root / "technical_failure_summary.json", summary)
        self._write_checkpoint(
            phase="STOPPED", actual_terminal_count=len(results),
            technical_failure_count=technical_count, stop_requested=True,
            completed_analysis_ids=[row["analysis_id"] for row in results],
        )
        write_core4_hash_manifest(self.root)
        validate_core4_hash_manifest(
            self.root, require_completed_files=False
        )
        return Core4Outcome(
            self.root,
            counts["planned_sensitivity_row_count"],
            counts["available_solver_request_count"],
            counts["expected_terminal_count"],
            len(results),
            counts["dependency_unavailable_row_count"],
            technical_count,
            True,
            summary,
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
                if key in deduped:
                    qualifier = (
                        "conflicting "
                        if canonical_json(deduped[key]) != canonical_json(row)
                        else ""
                    )
                    raise ConfigError(
                        f"{qualifier}duplicate in {filename}: {key}"
                    )
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
    return analyze_core4_artifacts(Path(config["execution"]["output_root"]))
