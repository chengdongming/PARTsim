"""Production EXT-2 trace validation, resampling, and simulation runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import signal
from typing import Any, Dict, Mapping, Optional

import yaml

from .cell_model import expand_cells
from .config import config_hash, domain_hash, dump_config, fraction_text, load_config
from .energy_trace_loader import (
    INVENTORY_COLUMNS, EnergyTraceError, load_energy_trace_csv,
    repository_trace_inventory,
)
from .energy_trace_model import CanonicalEnergyTrace
from .energy_trace_resampling import resample_trace, scale_trace
from .ext2_aggregation import aggregate_ext2
from .result_writer import (
    FAILURE_COLUMNS, append_csv_row, atomic_write_json, atomic_write_text,
    write_csv, write_file_hashes,
)
from .scheduler_registry import scheduler_by_id
from .service_lower_bound import (
    construct_window_minimum_bound, validate_service_lower_bound,
)
from .simulation_engine import (
    SimulationConfigurationError, load_simulation_terminal,
    run_paired_simulation, write_simulation_terminal,
)
from .simulation_result import SimulationStatus
from .taskset_store import TasksetStore, prepare_service_curve


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEGMENT_COLUMNS = (
    "segment_id", "trace_id", "source_trace_hash", "selection_rule",
    "start_sample", "sample_count", "start_timestamp_utc", "end_timestamp_utc",
    "fixture_label",
)
RESAMPLED_COLUMNS = (
    "trace_id", "trace_hash", "sample_index", "timestamp_ns", "timestamp_utc",
    "interval_duration_ns", "interval_energy_j", "mean_power_w",
    "missing_data", "fixture_label",
)
CONSERVATION_COLUMNS = (
    "input_trace_hash", "output_trace_hash", "operation", "policy", "scale",
    "input_total_energy_j", "output_total_energy_j", "difference_j",
    "rounding_bound_j", "status",
)
SERVICE_COLUMNS = (
    "trace_id", "construction_method", "applicable_horizon_intervals",
    "validated_interval_count", "minimum_slack_j", "violation_count",
    "status", "rta_status",
)
REQUEST_COLUMNS = (
    "request_id", "cell_id", "taskset_id", "taskset_hash", "trace_id",
    "trace_hash", "trace_scale", "segment_id", "scheduler_id",
    "simulation_config_hash", "input_hash", "request_status",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "request_id", "attempt_number", "scheduler_id", "status",
    "runtime_seconds", "recorded_at_utc",
)
RESULT_COLUMNS = (
    "request_id", "cell_id", "taskset_id", "taskset_hash", "trace_id",
    "trace_hash", "trace_scale", "segment_id", "scheduler_id", "status",
    "reason", "comparison_eligible", "horizon", "horizon_censoring",
    "runtime_seconds", "maximum_observed_response_time", "missed_jobs",
    "energy_blocked_ticks", "harvested_energy_j", "consumed_energy_j",
    "battery_minimum_j", "battery_maximum_j", "battery_trajectory_json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _segment(trace: CanonicalEnergyTrace, start: int, count: int, rule: str) -> CanonicalEnergyTrace:
    if start < 0 or count <= 0 or start + count > len(trace.samples):
        raise EnergyTraceError("configured trace segment is outside the source")
    return CanonicalEnergyTrace(
        trace_id=f"{trace.trace_id}:segment:{start}:{count}",
        source_id=trace.source_id, source_file_hash=trace.source_file_hash,
        quantity_kind=trace.quantity_kind, physical_unit=trace.physical_unit,
        preprocessing_version=f"{trace.preprocessing_version}:SEGMENT:{rule}",
        fixture_label=trace.fixture_label, samples=trace.samples[start:start + count],
    )


def _simulator_system(template: Path, destination: Path, irradiance_path: Path) -> Path:
    document = yaml.safe_load(template.read_text(encoding="utf-8"))
    energy = document["energy_management"]
    energy.update({
        "use_real_solar_data": True, "solar_data_file": str(irradiance_path.resolve()),
        "pv_efficiency": 1.0, "pv_area_m2": 1.0, "start_offset_minutes": 0,
        "day_of_year": 1, "time_of_day_ms": 0,
    })
    atomic_write_text(destination, yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    return destination


@dataclass(frozen=True)
class Ext2Outcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


class Ext2Runner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        if self.config.get("extension") != "EXT-2":
            raise ValueError("EXT-2 runner requires extension: EXT-2")
        if not isinstance(self.config.get("real_trace"), dict):
            raise ValueError("EXT-2 requires real_trace configuration")
        self.root = Path(self.config["execution"]["output_root"])
        self.terminals = self.root / "simulation_terminal_results"
        self.stop_requested = False

    @classmethod
    def from_path(cls, path: Path | str) -> "Ext2Runner":
        return cls(load_config(path, expected_core="CORE-3"))

    def describe(self, *, max_cells: Optional[int] = None, max_tasksets: Optional[int] = None) -> Dict[str, Any]:
        cells = list(expand_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        tasksets = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            tasksets = min(tasksets, max_tasksets)
        schedulers = self.config["real_trace"]["schedulers"]
        return {
            "extension": "EXT-2", "config_hash": config_hash(self.config),
            "data_status": self.config["real_trace"]["data_status"],
            "cell_count": len(cells), "tasksets_per_cell": tasksets,
            "scheduler_count": len(schedulers),
            "simulation_request_count": len(cells) * tasksets * len(schedulers),
            "cells": [cell.row() for cell in cells],
        }

    def _initialize(self, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.terminals.mkdir(parents=True, exist_ok=True)
        run_config = self.root / "run_config.yaml"
        if run_config.is_file():
            prior = load_config(run_config, expected_core="CORE-3")
            if config_hash(prior) != config_hash(self.config):
                raise RuntimeError("resume config hash mismatch")
            if not resume and any(self.terminals.glob("*.json")):
                raise RuntimeError("terminal results exist; use --resume")
        else:
            dump_config(self.config, run_config)
        write_csv(
            self.root / "trace_inventory.csv", INVENTORY_COLUMNS,
            repository_trace_inventory(PROJECT_ROOT),
        )
        if not (self.root / "failures.csv").is_file():
            write_csv(self.root / "failures.csv", FAILURE_COLUMNS, [])
        if not (self.root / "simulation_attempts.csv").is_file():
            write_csv(self.root / "simulation_attempts.csv", ATTEMPT_COLUMNS, [])

    def _prepare_trace(self) -> tuple[CanonicalEnergyTrace, str, Path]:
        spec = self.config["real_trace"]
        path = PROJECT_ROOT / spec["input_file"]
        source = load_energy_trace_csv(path, spec["schema"])
        if spec["data_status"] == "REAL_TRACE_DATA_UNAVAILABLE" and source.fixture_label != "SYNTHETIC_TEST_FIXTURE":
            raise EnergyTraceError("unavailable formal data status requires a synthetic fixture smoke")
        selection = spec["segment_selection"]
        segment = _segment(
            source, int(selection["start_sample"]), int(selection["sample_count"]),
            str(selection["rule"]),
        )
        scale = Fraction(str(spec["scale"]))
        scaled, scale_check = scale_trace(segment, scale)
        target_ns = int(Fraction(str(spec["resampling"]["target_interval_seconds"])) * 1_000_000_000)
        resampled, conservation = resample_trace(
            scaled, target_ns, policy=str(spec["resampling"]["policy"]),
            declared_interpolation=str(spec["resampling"].get("declared_interpolation", "piecewise_constant")),
        )
        segment_id = domain_hash("ASAP_BLOCK:V9.3:EXT2:SEGMENT:v1", {
            "source_hash": source.trace_hash, "selection": selection,
        })
        write_csv(self.root / "trace_segments.csv", SEGMENT_COLUMNS, [{
            "segment_id": segment_id, "trace_id": segment.trace_id,
            "source_trace_hash": source.trace_hash,
            "selection_rule": selection["rule"],
            "start_sample": selection["start_sample"], "sample_count": selection["sample_count"],
            "start_timestamp_utc": segment.samples[0].timestamp_utc,
            "end_timestamp_utc": segment.samples[-1].timestamp_utc,
            "fixture_label": segment.fixture_label or "",
        }])
        atomic_write_json(self.root / "trace_metadata.json", {
            **resampled.document(), "trace_hash": resampled.trace_hash,
            "data_status": spec["data_status"], "selection_rule": selection["rule"],
            "scale": fraction_text(scale),
        })
        rows = []
        for index, sample in enumerate(resampled.samples):
            seconds = Fraction(sample.interval_duration_ns, 1_000_000_000)
            rows.append({
                "trace_id": resampled.trace_id, "trace_hash": resampled.trace_hash,
                "sample_index": index, "timestamp_ns": sample.timestamp_ns,
                "timestamp_utc": sample.timestamp_utc,
                "interval_duration_ns": sample.interval_duration_ns,
                "interval_energy_j": fraction_text(sample.interval_energy_j),
                "mean_power_w": fraction_text(sample.interval_energy_j / seconds),
                "missing_data": sample.missing_data,
                "fixture_label": resampled.fixture_label or "",
            })
        write_csv(self.root / "resampled_trace.csv", RESAMPLED_COLUMNS, rows)
        write_csv(self.root / "energy_conservation_checks.csv", CONSERVATION_COLUMNS, [
            {
                "input_trace_hash": scale_check["input_trace_hash"],
                "output_trace_hash": scale_check["output_trace_hash"],
                "operation": "EXACT_SCALE", "policy": "", "scale": scale_check["scale"],
                "input_total_energy_j": scale_check["input_total_energy_j"],
                "output_total_energy_j": scale_check["output_total_energy_j"],
                "difference_j": scale_check["total_energy_change_j"],
                "rounding_bound_j": "0", "status": "EXACT_SCALE_RECORDED",
            },
            {
                "input_trace_hash": conservation["input_trace_hash"],
                "output_trace_hash": conservation["output_trace_hash"],
                "operation": "RESAMPLE", "policy": conservation["policy"], "scale": "",
                "input_total_energy_j": conservation["input_total_energy_j"],
                "output_total_energy_j": conservation["output_total_energy_j"],
                "difference_j": conservation["difference_j"],
                "rounding_bound_j": conservation["rounding_bound_j"],
                "status": conservation["status"],
            },
        ])
        service_spec = spec["service_bound"]
        if service_spec["enabled"]:
            horizon = int(service_spec["applicable_horizon_intervals"])
            bound = construct_window_minimum_bound(
                [sample.interval_energy_j for sample in resampled.samples], horizon
            )
            check = validate_service_lower_bound(
                [sample.interval_energy_j for sample in resampled.samples], bound, horizon
            )
            rta_status = "NOT_REQUESTED_TRACE_DRIVEN_SIMULATION_ONLY"
            if check["violation_count"]:
                raise EnergyTraceError("P0 service lower bound exceeds actual service")
        else:
            check = {
                "construction_method": "NONE", "applicable_horizon_intervals": 0,
                "validated_interval_count": 0, "minimum_slack_j": "UNAVAILABLE",
                "violation_count": 0, "status": "NOT_CONSTRUCTED",
            }
            rta_status = "NOT_APPLICABLE_NO_CERTIFIED_SERVICE_BOUND"
        write_csv(self.root / "service_bound_checks.csv", SERVICE_COLUMNS, [{
            "trace_id": resampled.trace_id, **check, "rta_status": rta_status,
        }])
        irradiance_path = self.root / "simulator_trace.csv"
        atomic_write_text(
            irradiance_path,
            "irradiance_W_per_m2\n" + "\n".join(
                format(float(Fraction(row["mean_power_w"])), ".17g") for row in rows
            ) + "\n",
        )
        template = PROJECT_ROOT / self.config["energy"]["service_curve"]["system_template"]
        system_path = _simulator_system(template, self.root / "trace_system_template.yaml", irradiance_path)
        return resampled, segment_id, system_path

    def _checkpoint(self, requested: int, terminal: int) -> None:
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": "ASAP_BLOCK_V9_3_EXT2_CHECKPOINT_V1",
            "config_hash": config_hash(self.config), "requested": requested,
            "terminal": terminal, "pending": requested - terminal,
            "stop_requested": self.stop_requested, "updated_at_utc": _utc_now(),
        })

    def run(self, *, resume: bool = False, max_cells: Optional[int] = None, max_tasksets: Optional[int] = None) -> Ext2Outcome:
        self._initialize(resume)
        trace, segment_id, system_path = self._prepare_trace()
        service = prepare_service_curve(self.config, self.root)
        store = TasksetStore(Path(self.config["execution"]["taskset_store"]), self.config, service)
        cells = list(expand_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        taskset_count = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            taskset_count = min(taskset_count, max_tasksets)
        registry = scheduler_by_id()
        schedulers = list(self.config["real_trace"]["schedulers"])
        if any(value not in registry for value in schedulers) or len(set(schedulers)) != len(schedulers):
            raise RuntimeError("EXT-2 scheduler list must contain unique audited IDs")
        simulation_hash = domain_hash("ASAP_BLOCK:V9.3:EXT2:SIMULATION_CONFIG:v1", {
            "simulation": self.config["simulation"], "energy": self.config["energy"],
            "trace_hash": trace.trace_hash,
        })
        plan = []
        for cell in cells:
            start = self.config["grid"].get("taskset_index_start", 0)
            for index in range(start, start + taskset_count):
                stored = store.get_or_create(cell, index)
                for scheduler in schedulers:
                    material = {
                        "taskset_hash": stored.semantic_hash, "trace_hash": trace.trace_hash,
                        "scheduler_id": scheduler, "simulation_config_hash": simulation_hash,
                    }
                    request_id = domain_hash("ASAP_BLOCK:V9.3:EXT2:SIMULATION_REQUEST:v1", material)
                    plan.append({
                        "request_id": request_id, "cell_id": cell.cell_id,
                        "taskset_id": stored.taskset_id, "taskset_hash": stored.semantic_hash,
                        "trace_id": trace.trace_id, "trace_hash": trace.trace_hash,
                        "trace_scale": self.config["real_trace"]["scale"],
                        "segment_id": segment_id, "scheduler_id": scheduler,
                        "simulation_config_hash": simulation_hash,
                        "input_hash": domain_hash("ASAP_BLOCK:V9.3:EXT2:INPUT:v1", material),
                        "request_status": "PLANNED", "stored": stored,
                    })
        write_csv(self.root / "simulation_requests.csv", REQUEST_COLUMNS, [
            {key: row[key] for key in REQUEST_COLUMNS} for row in plan
        ])
        previous = {}
        def stop(signum: int, _frame: Any) -> None:
            self.stop_requested = True
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, stop)
        terminal = 0
        try:
            for row in plan:
                if self.stop_requested:
                    break
                path = self.terminals / f"{row['request_id']}.json"
                if path.is_file():
                    if not resume:
                        raise RuntimeError("terminal result exists; use --resume")
                    execution = load_simulation_terminal(path)
                else:
                    stored = row["stored"]
                    try:
                        execution = run_paired_simulation(
                            simulation_id_value=row["request_id"], base_system_path=system_path,
                            run_root=self.root, task_payload=stored.task_payload,
                            taskset_hash=stored.semantic_hash, processors=stored.processors,
                            exact_e0=Fraction(self.config["energy"]["initial_energy_values"][0]),
                            energy_config=self.config["energy"],
                            simulation_config=self.config["simulation"],
                            scheduler_id=row["scheduler_id"],
                        )
                    except SimulationConfigurationError as exc:
                        raise RuntimeError(f"P0 simulation input error: {exc}") from exc
                    write_simulation_terminal(path, execution)
                    append_csv_row(self.root / "simulation_attempts.csv", ATTEMPT_COLUMNS, {
                        "attempt_id": domain_hash("ASAP_BLOCK:V9.3:EXT2:ATTEMPT:v1", row["request_id"]),
                        "request_id": row["request_id"], "attempt_number": 1,
                        "scheduler_id": row["scheduler_id"],
                        "status": execution.result.status.value,
                        "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                        "recorded_at_utc": _utc_now(),
                    })
                terminal += 1
                if execution.result.status in {SimulationStatus.INTERNAL_ERROR, SimulationStatus.RUNTIME_TIMEOUT}:
                    severity = "P1" if execution.result.status is SimulationStatus.INTERNAL_ERROR else "P2"
                    append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                        "severity": severity, "stage": "SIMULATION", "analysis_id": row["request_id"],
                        "cell_id": row["cell_id"], "taskset_id": row["taskset_id"],
                        "variant": row["scheduler_id"], "code": execution.result.status.value,
                        "detail": execution.result.reason, "traceback": execution.stderr_tail,
                        "failure_input": execution.system_config_path,
                    })
                self._checkpoint(len(plan), terminal)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        rows = []
        for row in plan:
            path = self.terminals / f"{row['request_id']}.json"
            if not path.is_file():
                continue
            execution = load_simulation_terminal(path)
            result, metrics = execution.result, execution.result.metrics
            rows.append({
                **{key: row[key] for key in (
                    "request_id", "cell_id", "taskset_id", "taskset_hash", "trace_id",
                    "trace_hash", "trace_scale", "segment_id", "scheduler_id",
                )},
                "status": result.status.value, "reason": result.reason,
                "comparison_eligible": result.comparison_eligible,
                "horizon": result.horizon,
                "horizon_censoring": result.status is SimulationStatus.HORIZON_INSUFFICIENT,
                "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                "maximum_observed_response_time": metrics.get("maximum_observed_response_time", "UNAVAILABLE"),
                "missed_jobs": metrics.get("missed_jobs", "UNAVAILABLE"),
                "energy_blocked_ticks": metrics.get("energy_blocked_ticks", "UNAVAILABLE"),
                "harvested_energy_j": metrics.get("harvested_energy_j", "UNAVAILABLE"),
                "consumed_energy_j": metrics.get("consumed_energy_j", "UNAVAILABLE"),
                "battery_minimum_j": metrics.get("battery_minimum_j", "UNAVAILABLE"),
                "battery_maximum_j": metrics.get("battery_maximum_j", "UNAVAILABLE"),
                "battery_trajectory_json": json.dumps(metrics.get("battery_trajectory", []), separators=(",", ":")),
            })
        write_csv(self.root / "simulation_results.csv", RESULT_COLUMNS, rows)
        aggregation = aggregate_ext2(self.root)
        summary = {
            "data_status": self.config["real_trace"]["data_status"],
            "fixture_label": trace.fixture_label, "trace_hash": trace.trace_hash,
            "requested": len(plan), "terminal": len(rows),
            "complete": len(rows) == len(plan), **aggregation,
        }
        atomic_write_json(self.root / "summary.json", summary)
        self._checkpoint(len(plan), len(rows))
        write_file_hashes(self.root)
        return Ext2Outcome(self.root, len(plan), len(rows), self.stop_requested, summary)


def analyze_ext2(root: Path) -> Mapping[str, Any]:
    result = aggregate_ext2(root)
    write_file_hashes(root)
    return result
