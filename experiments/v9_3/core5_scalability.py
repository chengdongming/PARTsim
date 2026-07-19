"""CORE-5 single-axis expansion and fail-closed production runner."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import time
import traceback
from typing import Any, Dict, Mapping, Optional, Sequence

from .config import ConfigError, canonical_json, config_hash, domain_hash, dump_config
from .core5_aggregation import aggregate_core5, analyze_core5_artifacts
from .core5_contract import (
    CHILD_OUTCOME_COLUMNS,
    CORE5_CHECKPOINT_SCHEMA,
    CORE5_RUN_SCHEMA,
    SCALABILITY_CELL_COLUMNS,
    Core5ContractError,
    configured_core5_counts,
    validate_core5_artifact_contract,
    validate_core5_child_evidence,
    validate_core5_hash_manifest,
    validate_core5_resume_envelope,
    validate_core5_raw_tables,
    write_core5_hash_manifest,
)
from .core5_terminal import Core5TerminalClass, classify_core5_terminal
from .execution_engine import AttemptExecution, ExecutionEngine, ExecutionPlanItem
from .resource_measurement import RESOURCE_OBSERVATION_COLUMNS
from .result_writer import (
    ATTEMPT_COLUMNS,
    FAILURE_COLUMNS,
    GENERATED_COLUMNS,
    REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS,
    append_csv_row,
    atomic_write_json,
    read_csv,
    write_csv,
)
from .taskset_store import prepare_service_curve


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


def expand_scalability_cells(
    config: Mapping[str, Any],
) -> tuple[ScalabilityCell, ...]:
    scale = config["scalability"]
    base = {
        "processors": scale["core_counts"][0],
        "task_count": scale["task_counts"][0],
        "period": scale["period_ranges"][0],
        "utilization": scale["utilization_points"][0],
        "worker_count": scale["worker_counts"][0],
    }
    specs = []
    specs.extend(
        ("task_count", i, str(value), value)
        for i, value in enumerate(scale["task_counts"])
    )
    specs.extend(
        ("core_count", i, str(value), value)
        for i, value in enumerate(scale["core_counts"])
    )
    specs.extend(
        ("period_range", i, value["id"], value)
        for i, value in enumerate(scale["period_ranges"])
    )
    if len(scale["utilization_points"]) > 1:
        specs.extend(
            ("utilization", i, str(value), value)
            for i, value in enumerate(scale["utilization_points"])
        )
    specs.extend(
        ("worker_count", i, str(value), value)
        for i, value in enumerate(scale["worker_counts"])
    )
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
            axis, index, level_id, canonical_json(value), processors,
            task_count, period["min"], period["max"], utilization, workers,
            domain_hash(
                "ASAP_BLOCK:V9.3:SCALABILITY_CELL:v1", identity_input
            ),
        ))
    return tuple(cells)


def assert_single_axis_isolation(
    config: Mapping[str, Any], cell: ScalabilityCell,
) -> None:
    scale = config["scalability"]
    baseline = (
        scale["core_counts"][0], scale["task_counts"][0],
        scale["period_ranges"][0]["min"],
        scale["period_ranges"][0]["max"],
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
    if any(
        actual[index] != baseline[index]
        for index in range(6) if index not in slots
    ):
        raise ConfigError(
            f"scalability cell changes more than {cell.scaling_axis}"
        )


class ResourceExecutionEngine(ExecutionEngine):
    def _observe_attempt(
        self, item: ExecutionPlanItem, attempt_row: Mapping[str, Any],
        execution: AttemptExecution,
    ) -> None:
        if execution.payload_received and execution.peak_rss_kib is None:
            raise Core5ContractError(
                "payload-bearing attempt did not return an RSS observation"
            )
        if execution.peak_rss_kib is not None:
            peak = execution.peak_rss_kib
            scope = "CHILD_PROCESS"
            unit = "KiB"
            status = "AVAILABLE"
            reason = ""
        else:
            peak = "UNAVAILABLE"
            scope = "UNAVAILABLE"
            unit = "UNAVAILABLE"
            terminal_class = classify_core5_terminal(
                execution.solver_status, outer_timeout=execution.outer_timeout
            )
            if terminal_class == Core5TerminalClass.RIGHT_CENSORED:
                status = "EXPECTED_UNAVAILABLE"
                reason = "NO_PAYLOAD_TIMEOUT"
            else:
                status = "TECHNICAL_UNAVAILABLE"
                reason = "NO_PAYLOAD_TECHNICAL_FAILURE"
        append_csv_row(
            self.root / "attempt_resource_observations.csv",
            RESOURCE_OBSERVATION_COLUMNS,
            {
                "attempt_id": attempt_row["attempt_id"],
                "analysis_id": item.analysis_id,
                "peak_rss_kib": peak,
                "peak_rss_scope": scope,
                "peak_rss_unit": unit,
                "observation_status": status,
                "unavailability_reason": reason,
            },
        )


@dataclass(frozen=True)
class Core5Outcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


def _cell_timing(
    prior: Optional[Mapping[str, Any]], terminal_count: int, elapsed: float,
) -> tuple[Any, Any]:
    """Keep first-run timing when resume only materializes old terminals."""

    if prior and int(prior["terminal_analysis_count"]) == terminal_count:
        return (
            prior["cell_wall_seconds"],
            prior["throughput_analyses_per_second"],
        )
    return (
        f"{elapsed:.9f}",
        terminal_count / elapsed if elapsed else None,
    )


class Core5ScalabilityRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.root = Path(config["execution"]["output_root"])
        self.identity = config_hash(config)
        self._resume_phase: Optional[str] = None

    def describe(self, *, max_cells: Optional[int] = None) -> Dict[str, Any]:
        cells = list(expand_scalability_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        per_cell = (
            self.config["grid"]["tasksets_per_cell"]
            * len(self.config["analysis"]["variants"])
        )
        return {
            "experiment_id": self.config["experiment_id"],
            "core": "CORE-5", "cell_count": len(cells),
            "request_count": len(cells) * per_cell,
            "hard_analysis_limit": self.config["scalability"]["max_analyses"],
            "cells": [self._cell_row(cell) for cell in cells],
        }

    @staticmethod
    def _cell_row(cell: ScalabilityCell) -> Dict[str, Any]:
        return {
            "scalability_cell_id": cell.cell_id,
            "scaling_axis": cell.scaling_axis,
            "level_index": cell.level_index, "level_id": cell.level_id,
            "level_value": cell.level_value, "M": cell.processors,
            "task_n": cell.task_count, "period_min": cell.period_min,
            "period_max": cell.period_max, "utilization": cell.utilization,
            "worker_count": cell.worker_count,
        }

    def _write_checkpoint(
        self, *, phase: str, cell_rows: Sequence[Mapping[str, Any]],
        actual_terminal_count: int, technical_failure_count: int,
        p0_failure_count: int, stop_requested: bool,
        completed_analysis_ids: Optional[Sequence[str]] = None,
    ) -> None:
        counts = configured_core5_counts(self.config)
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": CORE5_CHECKPOINT_SCHEMA,
            "core": "CORE-5", "config_hash": self.identity,
            "phase": phase, **counts,
            "completed_scalability_cell_count": len(cell_rows),
            "completed_scalability_cell_ids": [
                row["scalability_cell_id"] for row in cell_rows
            ],
            "actual_terminal_count": int(actual_terminal_count),
            "technical_failure_count": int(technical_failure_count),
            "p0_failure_count": int(p0_failure_count),
            "completed_analysis_ids": sorted(completed_analysis_ids or []),
            "stop_requested": bool(stop_requested),
        })

    def _initialize(self, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        metadata_path = self.root / "run_metadata.json"
        if metadata_path.is_file():
            if not resume:
                raise ConfigError("CORE-5 run directory exists; use --resume")
            try:
                self._resume_phase = validate_core5_resume_envelope(
                    self.root, expected_config_hash=self.identity
                )
            except Core5ContractError as exc:
                raise ConfigError(str(exc)) from exc
            return
        existing = sorted(path.name for path in self.root.iterdir())
        if existing:
            raise ConfigError(
                "CORE-5 output root has artifacts but no run_metadata.json"
            )
        counts = configured_core5_counts(self.config)
        if counts != {
            "planned_scalability_cell_count": 8,
            "planned_analysis_count": 16,
            "hard_analysis_limit": 20,
        }:
            raise ConfigError("CORE-5 V2 requires the exact 8/16/20 plan")
        atomic_write_json(metadata_path, {
            "schema": CORE5_RUN_SCHEMA,
            "experiment_id": self.config["experiment_id"],
            "core": "CORE-5", "config_hash": self.identity, **counts,
            "formal_large_scale_run": False,
            "parallel_throughput_is_not_algorithmic_complexity": True,
        })
        dump_config(self.config, self.root / "run_config.yaml")
        self._write_checkpoint(
            phase="INITIALIZED", cell_rows=[], actual_terminal_count=0,
            technical_failure_count=0, p0_failure_count=0,
            stop_requested=False,
        )
        self._resume_phase = "INITIALIZED"

    def _child_config(self, cell: ScalabilityCell, per_cell: int) -> Dict[str, Any]:
        child = deepcopy(self.config)
        child["experiment_id"] = f"{self.config['experiment_id']}::{cell.cell_id}"
        child["platform"] = {
            "cores": [cell.processors], "task_count": [cell.task_count]
        }
        child["generation"]["period_min"] = cell.period_min
        child["generation"]["period_max"] = cell.period_max
        child["grid"]["utilization_points"] = [cell.utilization]
        child["grid"]["tasksets_per_cell"] = per_cell
        child["analysis"]["worker_count"] = cell.worker_count
        child_root = self.root / "cell_runs" / cell.cell_id
        child["execution"]["output_root"] = str(child_root)
        shared_store_id = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5_SHARED_TASKSET_STORE:v2",
            {
                "mathematical_input": cell.mathematical_input(),
                "generation": child["generation"],
                "energy": child["energy"],
                "grid_seed": child["grid"]["base_seed"],
            },
        )
        child["execution"]["taskset_store"] = str(
            Path(self.config["execution"]["taskset_store"])
            / "core5-shared" / shared_store_id
        )
        return child

    @staticmethod
    def _child_failure_code(
        exc: Exception, outcome: Optional[Any], child_root: Path,
    ) -> str:
        if outcome is not None and bool(outcome.stopped):
            return "CHILD_STOPPED"
        if outcome is not None and int(outcome.requested) != int(outcome.terminal):
            return "CHILD_INCOMPLETE"
        if any(
            row.get("severity") == "P0"
            for row in read_csv(child_root / "failures.csv")
        ):
            return "CHILD_P0_FAILURE"
        if any(
            classify_core5_terminal(
                row.get("solver_status"), outer_timeout=row.get("outer_timeout")
            ) == Core5TerminalClass.TECHNICAL_FAILURE
            for row in read_csv(child_root / "per_taskset_results.csv")
        ):
            return "CHILD_TECHNICAL_TERMINAL"
        if "resource" in str(exc).lower() or "rss" in str(exc).lower():
            return "RESOURCE_OBSERVATION_CONTRACT_FAILURE"
        if outcome is None:
            return "CHILD_EXECUTION_EXCEPTION"
        return "CHILD_CONTRACT_FAILURE"

    def run(
        self, *, resume: bool = False, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Core5Outcome:
        if max_cells is not None and max_cells <= 0:
            raise ConfigError("max_cells must be positive")
        if max_tasksets is not None and max_tasksets <= 0:
            raise ConfigError("max_tasksets must be positive")
        cells = list(expand_scalability_cells(self.config))
        per_cell_tasksets = self.config["grid"]["tasksets_per_cell"]
        selected_cells = cells if max_cells is None else cells[:max_cells]
        selected_tasksets = (
            per_cell_tasksets if max_tasksets is None
            else min(per_cell_tasksets, max_tasksets)
        )
        requested = (
            len(selected_cells) * selected_tasksets
            * len(self.config["analysis"]["variants"])
        )
        counts = configured_core5_counts(self.config)
        if requested > counts["hard_analysis_limit"]:
            raise ConfigError(
                f"CORE-5 request count {requested} exceeds hard limit "
                f"{counts['hard_analysis_limit']}"
            )
        if requested != counts["planned_analysis_count"]:
            raise ConfigError(
                "CORE-5 V2 execution must materialize the full 8-cell/16-analysis plan"
            )

        self._initialize(resume)
        if self._resume_phase == "COMPLETED":
            summary = json.loads(
                (self.root / "scalability_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            return Core5Outcome(
                self.root, counts["planned_analysis_count"],
                counts["planned_analysis_count"], False, summary,
            )
        self._write_checkpoint(
            phase="RUNNING", cell_rows=[], actual_terminal_count=0,
            technical_failure_count=0, p0_failure_count=0,
            stop_requested=False,
        )

        prior_cells = {
            row["scalability_cell_id"]: row
            for row in read_csv(self.root / "scalability_cells.csv")
        }
        cell_rows: list[Dict[str, Any]] = []
        child_rows: list[Dict[str, Any]] = []
        stop_record: Optional[tuple[str, str, Dict[str, Any]]] = None
        for cell in selected_cells:
            assert_single_axis_isolation(self.config, cell)
            child = self._child_config(cell, selected_tasksets)
            child_root = Path(child["execution"]["output_root"])
            outcome: Optional[Any] = None
            started = time.perf_counter()
            try:
                service = prepare_service_curve(
                    child, child_root / "service_material"
                )
                outcome = ResourceExecutionEngine(
                    child, service_override=service
                ).run(
                    resume=resume, max_tasksets=selected_tasksets
                )
            except Exception as exc:
                elapsed = time.perf_counter() - started
                analysis_ids = [
                    row["analysis_id"]
                    for row in read_csv(child_root / "analysis_requests.csv")
                ]
                terminal_count = len(
                    read_csv(child_root / "per_taskset_results.csv")
                )
                wall, throughput = _cell_timing(
                    prior_cells.get(cell.cell_id) if resume else None,
                    terminal_count, elapsed,
                )
                cell_rows.append(self._completed_cell_row(
                    cell, selected_tasksets, analysis_ids, terminal_count,
                    wall, throughput,
                ))
                code = self._child_failure_code(exc, outcome, child_root)
                child_rows.append(self._child_outcome_row(
                    cell, outcome, analysis_ids, terminal_count,
                    contract_status=code,
                ))
                stop_record = (code, str(exc), {
                    "scalability_cell_id": cell.cell_id,
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                })
                break

            elapsed = time.perf_counter() - started
            analysis_ids = [
                row["analysis_id"]
                for row in read_csv(child_root / "analysis_requests.csv")
            ]
            wall, throughput = _cell_timing(
                prior_cells.get(cell.cell_id) if resume else None,
                outcome.terminal, elapsed,
            )
            cell_rows.append(self._completed_cell_row(
                cell, selected_tasksets, analysis_ids, outcome.terminal,
                wall, throughput,
            ))
            try:
                validation = validate_core5_child_evidence(
                    child_root, outcome
                )
            except Exception as exc:
                code = self._child_failure_code(exc, outcome, child_root)
                child_rows.append(self._child_outcome_row(
                    cell, outcome, analysis_ids, outcome.terminal,
                    contract_status=code,
                ))
                stop_record = (code, str(exc), {
                    "scalability_cell_id": cell.cell_id,
                    "status_counts": dict(outcome.status_counts),
                })
                break
            child_rows.append(self._child_outcome_row(
                cell, outcome, validation["request_ids"], outcome.terminal,
                contract_status="VALID",
            ))
            self._write_checkpoint(
                phase="RUNNING", cell_rows=cell_rows,
                actual_terminal_count=sum(
                    int(row["terminal_analysis_count"]) for row in cell_rows
                ),
                technical_failure_count=0, p0_failure_count=0,
                stop_requested=False,
                completed_analysis_ids=[
                    analysis_id for row in cell_rows
                    for analysis_id in json.loads(row["analysis_ids_json"])
                ],
            )

        try:
            self._materialize_children(cell_rows, child_rows)
        except Exception as exc:
            if stop_record is None:
                stop_record = (
                    "TOP_LEVEL_MATERIALIZATION_FAILURE", str(exc),
                    {"exception_type": type(exc).__name__},
                )
            self._ensure_top_level_tables(cell_rows, child_rows)
        if stop_record is not None:
            code, detail, context = stop_record
            return self._technical_failure_outcome(
                code=code, detail=detail, context=context,
                cell_rows=cell_rows,
            )

        try:
            validate_core5_raw_tables(self.root, require_complete=True)
            summary = aggregate_core5(self.root)
        except Exception as exc:
            return self._technical_failure_outcome(
                code="TOP_LEVEL_CONTRACT_FAILURE", detail=str(exc),
                context={"exception_type": type(exc).__name__},
                cell_rows=cell_rows,
            )
        if summary["stopped"]:
            return self._technical_failure_outcome(
                code="CORE5_AGGREGATION_P0",
                detail="worker pairing or technical aggregation failure",
                context={
                    "worker_semantic_failure_count": summary[
                        "worker_semantic_failure_count"
                    ],
                    "technical_failure_count": summary[
                        "technical_failure_count"
                    ],
                },
                cell_rows=cell_rows,
                preserve_summary=True,
            )

        results = read_csv(self.root / "per_taskset_results.csv")
        completed_ids = [row["analysis_id"] for row in results]
        self._write_checkpoint(
            phase="FINALIZING", cell_rows=cell_rows,
            actual_terminal_count=len(results), technical_failure_count=0,
            p0_failure_count=0, stop_requested=False,
            completed_analysis_ids=completed_ids,
        )
        write_core5_hash_manifest(self.root)
        validate_core5_hash_manifest(
            self.root, require_completed_files=True
        )
        validate_core5_artifact_contract(
            self.root, require_completed=False
        )
        self._write_checkpoint(
            phase="COMPLETED", cell_rows=cell_rows,
            actual_terminal_count=len(results), technical_failure_count=0,
            p0_failure_count=0, stop_requested=False,
            completed_analysis_ids=completed_ids,
        )
        validate_core5_artifact_contract(self.root)
        return Core5Outcome(
            self.root, counts["planned_analysis_count"], len(results),
            False, summary,
        )

    def _completed_cell_row(
        self, cell: ScalabilityCell, tasksets: int,
        analysis_ids: Sequence[str], terminal_count: int,
        wall_seconds: Any, throughput: Any,
    ) -> Dict[str, Any]:
        return {
            **self._cell_row(cell),
            "variants": canonical_json(self.config["analysis"]["variants"]),
            "tasksets_requested": tasksets,
            "analysis_ids_json": canonical_json(list(analysis_ids)),
            "cell_wall_seconds": wall_seconds,
            "terminal_analysis_count": terminal_count,
            "throughput_analyses_per_second": throughput,
        }

    @staticmethod
    def _child_outcome_row(
        cell: ScalabilityCell, outcome: Optional[Any],
        analysis_ids: Sequence[str], terminal_count: int, *,
        contract_status: str,
    ) -> Dict[str, Any]:
        status_counts = dict(outcome.status_counts) if outcome else {}
        return {
            "scalability_cell_id": cell.cell_id,
            "requested_count": int(outcome.requested) if outcome else len(analysis_ids),
            "terminal_count": int(outcome.terminal) if outcome else terminal_count,
            "stopped": bool(outcome.stopped) if outcome else True,
            "status_counts_json": canonical_json(status_counts),
            "request_set_status": (
                "CLOSED" if outcome and int(outcome.requested) == len(analysis_ids)
                else "MISMATCH"
            ),
            "terminal_status": (
                "CLOSED" if outcome and int(outcome.terminal) == terminal_count
                else "MISMATCH"
            ),
            "resource_status": (
                "VALID" if contract_status == "VALID"
                else "FAILED"
            ),
            "p0_failure_count": (
                1 if contract_status == "CHILD_P0_FAILURE" else 0
            ),
            "contract_status": contract_status,
        }

    def _ensure_top_level_tables(
        self, cell_rows: Sequence[Mapping[str, Any]],
        child_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        write_csv(
            self.root / "scalability_cells.csv",
            SCALABILITY_CELL_COLUMNS, cell_rows,
        )
        write_csv(
            self.root / "child_outcomes.csv", CHILD_OUTCOME_COLUMNS, child_rows
        )
        tables = {
            "generated_tasksets.csv": GENERATED_COLUMNS,
            "analysis_requests.csv": REQUEST_COLUMNS,
            "analysis_attempts.csv": ATTEMPT_COLUMNS,
            "per_taskset_results.csv": TASKSET_RESULT_COLUMNS,
            "per_task_results.csv": TASK_RESULT_COLUMNS,
            "failures.csv": FAILURE_COLUMNS,
            "attempt_resource_observations.csv": RESOURCE_OBSERVATION_COLUMNS,
        }
        for name, columns in tables.items():
            if not (self.root / name).is_file():
                write_csv(self.root / name, columns, [])

    def _materialize_children(
        self, cell_rows: Sequence[Mapping[str, Any]],
        child_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        self._ensure_top_level_tables(cell_rows, child_rows)
        aggregate_files = {
            "generated_tasksets.csv": (GENERATED_COLUMNS, ("taskset_id",), True),
            "analysis_requests.csv": (REQUEST_COLUMNS, ("analysis_id",), False),
            "analysis_attempts.csv": (ATTEMPT_COLUMNS, ("attempt_id",), False),
            "per_taskset_results.csv": (
                TASKSET_RESULT_COLUMNS, ("analysis_id",), False
            ),
            "per_task_results.csv": (
                TASK_RESULT_COLUMNS, ("analysis_id", "task_id"), False
            ),
            "failures.csv": (
                FAILURE_COLUMNS, ("analysis_id", "code"), False
            ),
            "attempt_resource_observations.csv": (
                RESOURCE_OBSERVATION_COLUMNS, ("attempt_id",), False
            ),
        }
        for filename, (columns, unique_fields, allow_identical_reuse) in (
            aggregate_files.items()
        ):
            deduped: Dict[tuple[str, ...], Mapping[str, Any]] = {}
            for path in sorted(
                (self.root / "cell_runs").glob("*/" + filename)
            ):
                for row in read_csv(path):
                    key = tuple(row.get(field, "") for field in unique_fields)
                    if any(not value for value in key):
                        raise ConfigError(
                            f"empty identity in {filename}: {key}"
                        )
                    if key in deduped:
                        identical = (
                            canonical_json(deduped[key]) == canonical_json(row)
                        )
                        if not allow_identical_reuse or not identical:
                            qualifier = "conflicting " if not identical else ""
                            raise ConfigError(
                                f"{qualifier}duplicate in {filename}: {key}"
                            )
                        continue
                    deduped[key] = row
            write_csv(self.root / filename, columns, deduped.values())

    def _technical_failure_outcome(
        self, *, code: str, detail: str, context: Mapping[str, Any],
        cell_rows: Sequence[Mapping[str, Any]],
        preserve_summary: bool = False,
    ) -> Core5Outcome:
        if not preserve_summary:
            for name in (
                "resource_usage.csv", "runtime_censoring.csv",
                "scalability_summary.csv", "scalability_summary.json",
                "worker_semantic_checks.csv", "core5_plot_data.csv",
            ):
                path = self.root / name
                if path.is_file():
                    path.unlink()
        failure_id = domain_hash(
            "ASAP_BLOCK:V9.3:CORE5_TECHNICAL_FAILURE:v2",
            {"code": code, "detail": detail, "context": context},
        )
        failure_path = self.root / "failure_inputs" / f"{failure_id}.json"
        atomic_write_json(failure_path, {
            "schema": "ASAP_BLOCK_V9_3_CORE5_TECHNICAL_FAILURE_V2",
            "failure_id": failure_id, "code": code,
            "detail": detail, "context": context,
        })
        failures = [
            row for row in read_csv(self.root / "failures.csv")
            if not (
                row.get("stage") == "CORE5_CONTRACT"
                and row.get("analysis_id") == failure_id
            )
        ]
        failures.append({
            "severity": "P0", "stage": "CORE5_CONTRACT",
            "analysis_id": failure_id, "cell_id": context.get(
                "scalability_cell_id", ""
            ),
            "taskset_id": "", "variant": "", "code": code,
            "detail": detail, "traceback": context.get("traceback", ""),
            "failure_input": str(failure_path),
        })
        write_csv(self.root / "failures.csv", FAILURE_COLUMNS, failures)
        results = read_csv(self.root / "per_taskset_results.csv")
        technical_ids = {
            row["analysis_id"] for row in results
            if classify_core5_terminal(
                row.get("solver_status"), outer_timeout=row.get("outer_timeout")
            ) == Core5TerminalClass.TECHNICAL_FAILURE
        }
        p0_count = sum(row.get("severity") == "P0" for row in failures)
        summary = {
            "stopped": True, "technical_failure_code": code,
            "technical_failure_detail": detail,
            "planned_analysis_count": configured_core5_counts(self.config)[
                "planned_analysis_count"
            ],
            "terminal_analysis_count": len(results),
            "technical_failure_count": len(technical_ids),
            "p0_count": p0_count,
        }
        atomic_write_json(
            self.root / "technical_failure_summary.json", summary
        )
        self._write_checkpoint(
            phase="STOPPED", cell_rows=cell_rows,
            actual_terminal_count=len(results),
            technical_failure_count=len(technical_ids),
            p0_failure_count=p0_count, stop_requested=True,
            completed_analysis_ids=[row["analysis_id"] for row in results],
        )
        write_core5_hash_manifest(self.root)
        validate_core5_hash_manifest(
            self.root, require_completed_files=False
        )
        return Core5Outcome(
            self.root,
            configured_core5_counts(self.config)["planned_analysis_count"],
            len(results), True, summary,
        )


def analyze_core5(config: Mapping[str, Any]) -> Mapping[str, Any]:
    return analyze_core5_artifacts(
        Path(config["execution"]["output_root"])
    )
