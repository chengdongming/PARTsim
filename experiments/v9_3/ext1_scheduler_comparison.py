"""Production EXT-1 nine-scheduler paired simulation runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import json
import signal
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .cell_model import expand_cells
from .config import config_hash, domain_hash, dump_config, fraction_text, load_config
from .ext1_aggregation import aggregate_ext1
from .result_writer import (
    FAILURE_COLUMNS, GENERATED_COLUMNS, append_csv_row, atomic_write_json,
    read_csv, write_csv, write_file_hashes,
)
from .scheduler_pairing import (
    assert_scheduler_only_difference, instance_id, simulation_request_id,
)
from .scheduler_registry import SCHEDULERS, audited_scheduler_registry
from .simulation_engine import (
    SimulationConfigurationError, load_simulation_terminal,
    run_paired_simulation, write_simulation_terminal,
)
from .simulation_result import SimulationStatus
from .taskset_store import TasksetStore, prepare_service_curve


REGISTRY_COLUMNS = tuple(SCHEDULERS[0].row())
REQUEST_COLUMNS = (
    "request_id", "paired_instance_id", "cell_id", "taskset_id",
    "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
    "scheduler_id", "generation_seed", "M", "initial_battery",
    "battery_capacity", "horizon", "request_status",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "request_id", "scheduler_id", "attempt_number",
    "status", "runtime_seconds", "horizons_attempted", "recorded_at_utc",
)
RESULT_COLUMNS = (
    "request_id", "paired_instance_id", "cell_id", "taskset_id",
    "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
    "scheduler_id", "status", "reason", "comparison_eligible",
    "horizon", "horizon_censoring", "runtime_seconds", "attempt_count",
    "missed_jobs", "first_miss_time", "maximum_observed_response_time",
    "mean_response_time", "completed_jobs", "preemptions",
    "processor_wait_ticks", "energy_blocked_ticks", "bypass_count",
    "synchronization_wait_ticks", "idle_cores_while_ready_jobs_exist_ticks",
    "harvested_energy_j", "consumed_energy_j", "battery_minimum_j",
    "battery_maximum_j", "retained_trace_path",
)
TASK_COLUMNS = (
    "request_id", "paired_instance_id", "scheduler_id", "taskset_id",
    "taskset_hash", "task_id", "observed_jobs", "completed_jobs",
    "missed_jobs", "censored_jobs", "r_sim_max", "horizon_coverage",
    "minimum_jobs_satisfied",
)
JOB_COLUMNS = (
    "request_id", "paired_instance_id", "scheduler_id", "taskset_id",
    "taskset_hash", "task_id", "job_index", "release", "completion",
    "absolute_deadline", "response_time", "deadline_miss",
    "first_execution", "preemption_count", "energy_blocked_ticks",
    "processor_wait_ticks", "executed_ticks", "eligible_after_warmup",
    "censored", "censoring_reason",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _available(value: Any) -> Any:
    return "UNAVAILABLE" if value is None else value


@dataclass(frozen=True)
class Ext1Outcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


class Ext1Runner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        if self.config.get("extension") != "EXT-1":
            raise ValueError("EXT-1 runner requires extension: EXT-1")
        audited_scheduler_registry()
        self.root = Path(self.config["execution"]["output_root"])
        self.terminals = self.root / "simulation_terminal_results"
        self.stop_requested = False

    @classmethod
    def from_path(cls, path: Path | str) -> "Ext1Runner":
        return cls(load_config(path, expected_core="CORE-3"))

    def describe(self, *, max_cells: Optional[int] = None, max_tasksets: Optional[int] = None) -> Dict[str, Any]:
        cells = list(expand_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        per_cell = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            per_cell = min(per_cell, max_tasksets)
        return {
            "extension": "EXT-1", "config_hash": config_hash(self.config),
            "cell_count": len(cells), "tasksets_per_cell": per_cell,
            "scheduler_count": len(SCHEDULERS),
            "simulation_request_count": len(cells) * per_cell * len(SCHEDULERS),
            "cells": [cell.row() for cell in cells],
            "scheduler_ids": [item.scheduler_id for item in SCHEDULERS],
        }

    def _install_signal_handlers(self) -> Dict[int, Any]:
        previous: Dict[int, Any] = {}
        def stop(signum: int, _frame: Any) -> None:
            self.stop_requested = True
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, stop)
        return previous

    def _checkpoint(self, requests: Sequence[Mapping[str, Any]], terminal: int) -> None:
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": "ASAP_BLOCK_V9_3_EXT1_CHECKPOINT_V1",
            "config_hash": config_hash(self.config),
            "requested": len(requests), "terminal": terminal,
            "pending": len(requests) - terminal,
            "stop_requested": self.stop_requested, "updated_at_utc": _utc_now(),
        })

    def _initialize(self, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.terminals.mkdir(parents=True, exist_ok=True)
        existing_config = self.root / "run_config.yaml"
        if existing_config.is_file():
            observed = load_config(existing_config, expected_core="CORE-3")
            if config_hash(observed) != config_hash(self.config):
                raise RuntimeError("resume config hash mismatch")
            if not resume and any(self.terminals.glob("*.json")):
                raise RuntimeError("terminal results exist; use --resume")
        else:
            dump_config(self.config, existing_config)
        write_csv(self.root / "scheduler_registry.csv", REGISTRY_COLUMNS, [
            item.row() for item in audited_scheduler_registry()
        ])
        if not (self.root / "failures.csv").is_file():
            write_csv(self.root / "failures.csv", FAILURE_COLUMNS, [])
        if not (self.root / "simulation_attempts.csv").is_file():
            write_csv(self.root / "simulation_attempts.csv", ATTEMPT_COLUMNS, [])

    def _plan(self, max_cells: Optional[int], max_tasksets: Optional[int]) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        service = prepare_service_curve(self.config, self.root)
        trace_hash = domain_hash(
            "ASAP_BLOCK:V9.3:EXT1:HARVEST_TRACE:v1",
            [fraction_text(value) for value in service.values[:int(self.config["simulation"]["maximum_horizon"])]],
        )
        store = TasksetStore(
            Path(self.config["execution"]["taskset_store"]), self.config, service
        )
        cells = list(expand_cells(self.config))
        if max_cells is not None:
            cells = cells[:max_cells]
        count = self.config["grid"]["tasksets_per_cell"]
        if max_tasksets is not None:
            count = min(count, max_tasksets)
        simulation_hash = domain_hash(
            "ASAP_BLOCK:V9.3:EXT1:SIMULATION_CONFIG:v1",
            {"simulation": self.config["simulation"], "energy": self.config["energy"]},
        )
        generated: Dict[str, Dict[str, Any]] = {}
        requests: list[Dict[str, Any]] = []
        for cell in cells:
            start = self.config["grid"].get("taskset_index_start", 0)
            for taskset_index in range(start, start + count):
                stored = store.get_or_create(cell, taskset_index)
                generated[stored.taskset_id] = stored.generated_row()
                material = {
                    "taskset_hash": stored.semantic_hash,
                    "trace_hash": trace_hash,
                    "simulation_config_hash": simulation_hash,
                    "M": stored.processors,
                    "initial_battery": self.config["energy"]["simulation_initial_battery"],
                    "battery_capacity": self.config["energy"]["battery_capacity"],
                    "horizon": self.config["simulation"]["horizon"],
                    "deadline_mode": stored.deadline_mode,
                    "power_hash": stored.power_hash,
                    "generation_seed": stored.seed,
                }
                paired_id = instance_id(material)
                input_hash = domain_hash("ASAP_BLOCK:V9.3:EXT1:FAIR_INPUT:v1", material)
                for registration in SCHEDULERS:
                    request_id = simulation_request_id(paired_id, registration.scheduler_id)
                    requests.append({
                        "request_id": request_id, "paired_instance_id": paired_id,
                        "cell_id": cell.cell_id, "taskset_id": stored.taskset_id,
                        "taskset_hash": stored.semantic_hash, "trace_hash": trace_hash,
                        "simulation_config_hash": simulation_hash, "input_hash": input_hash,
                        "scheduler_id": registration.scheduler_id,
                        "generation_seed": stored.seed, "M": stored.processors,
                        "initial_battery": self.config["energy"]["simulation_initial_battery"],
                        "battery_capacity": self.config["energy"]["battery_capacity"],
                        "horizon": self.config["simulation"]["horizon"],
                        "request_status": "PLANNED", "stored": stored,
                    })
        assert_scheduler_only_difference(requests)
        return list(generated.values()), requests

    def run(self, *, resume: bool = False, max_cells: Optional[int] = None, max_tasksets: Optional[int] = None) -> Ext1Outcome:
        self._initialize(resume)
        generated, plan = self._plan(max_cells, max_tasksets)
        public_requests = [{key: row[key] for key in REQUEST_COLUMNS} for row in plan]
        write_csv(self.root / "generated_tasksets.csv", GENERATED_COLUMNS, generated)
        write_csv(self.root / "simulation_requests.csv", REQUEST_COLUMNS, public_requests)
        previous = self._install_signal_handlers()
        terminal = 0
        try:
            for row in plan:
                if self.stop_requested:
                    break
                request_id = str(row["request_id"])
                terminal_path = self.terminals / f"{request_id}.json"
                if terminal_path.is_file():
                    if not resume:
                        raise RuntimeError("terminal result exists; use --resume")
                    execution = load_simulation_terminal(terminal_path)
                else:
                    stored = row["stored"]
                    try:
                        execution = run_paired_simulation(
                            simulation_id_value=request_id,
                            base_system_path=Path(__file__).resolve().parents[2] / self.config["energy"]["service_curve"]["system_template"],
                            run_root=self.root, task_payload=stored.task_payload,
                            taskset_hash=stored.semantic_hash,
                            processors=stored.processors,
                            exact_e0=Fraction(self.config["energy"]["initial_energy_values"][0]),
                            energy_config=self.config["energy"],
                            simulation_config=self.config["simulation"],
                            scheduler_id=str(row["scheduler_id"]),
                        )
                    except SimulationConfigurationError as exc:
                        raise RuntimeError(f"P0 simulation configuration failure: {exc}") from exc
                    write_simulation_terminal(terminal_path, execution)
                    append_csv_row(self.root / "simulation_attempts.csv", ATTEMPT_COLUMNS, {
                        "attempt_id": domain_hash("ASAP_BLOCK:V9.3:EXT1:ATTEMPT:v1", {"request_id": request_id, "attempt": 1}),
                        "request_id": request_id, "scheduler_id": row["scheduler_id"],
                        "attempt_number": 1, "status": execution.result.status.value,
                        "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                        "horizons_attempted": json.dumps(execution.horizons_attempted),
                        "recorded_at_utc": _utc_now(),
                    })
                terminal += 1
                if execution.result.status is SimulationStatus.INTERNAL_ERROR:
                    append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                        "severity": "P1", "stage": "SIMULATION", "analysis_id": request_id,
                        "cell_id": row["cell_id"], "taskset_id": row["taskset_id"],
                        "variant": row["scheduler_id"], "code": "SIM_INTERNAL_ERROR",
                        "detail": execution.result.reason, "traceback": execution.stderr_tail,
                        "failure_input": execution.system_config_path,
                    })
                elif execution.result.status is SimulationStatus.RUNTIME_TIMEOUT:
                    append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                        "severity": "P2", "stage": "SIMULATION", "analysis_id": request_id,
                        "cell_id": row["cell_id"], "taskset_id": row["taskset_id"],
                        "variant": row["scheduler_id"], "code": "SIM_RUNTIME_TIMEOUT",
                        "detail": execution.result.reason, "traceback": execution.stderr_tail,
                        "failure_input": execution.system_config_path,
                    })
                if terminal % self.config["execution"]["checkpoint_every"] == 0:
                    self._checkpoint(public_requests, terminal)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        self._checkpoint(public_requests, terminal)
        result_rows, task_rows, job_rows = [], [], []
        for row in plan:
            path = self.terminals / f"{row['request_id']}.json"
            if not path.is_file():
                continue
            execution = load_simulation_terminal(path)
            result, metrics = execution.result, execution.result.metrics
            result_rows.append({
                **{key: row[key] for key in (
                    "request_id", "paired_instance_id", "cell_id", "taskset_id",
                    "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
                    "scheduler_id",
                )},
                "status": result.status.value, "reason": result.reason,
                "comparison_eligible": result.comparison_eligible,
                "horizon": result.horizon,
                "horizon_censoring": result.status is SimulationStatus.HORIZON_INSUFFICIENT,
                "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                "attempt_count": execution.attempt_count,
                **{key: _available(metrics.get(key)) for key in (
                    "missed_jobs", "first_miss_time", "maximum_observed_response_time",
                    "mean_response_time", "completed_jobs", "preemptions",
                    "processor_wait_ticks", "energy_blocked_ticks", "bypass_count",
                    "synchronization_wait_ticks", "idle_cores_while_ready_jobs_exist_ticks",
                    "harvested_energy_j", "consumed_energy_j", "battery_minimum_j",
                    "battery_maximum_j",
                )},
                "retained_trace_path": execution.retained_trace_path or "",
            })
            for task in result.tasks:
                task_rows.append({
                    "request_id": row["request_id"], "paired_instance_id": row["paired_instance_id"],
                    "scheduler_id": row["scheduler_id"], "taskset_id": row["taskset_id"],
                    "taskset_hash": row["taskset_hash"], **task.row(),
                })
            for job in result.jobs:
                job_rows.append({
                    "request_id": row["request_id"], "paired_instance_id": row["paired_instance_id"],
                    "scheduler_id": row["scheduler_id"], "taskset_id": row["taskset_id"],
                    "taskset_hash": row["taskset_hash"], **job.row(),
                })
        if len({row["request_id"] for row in result_rows}) != len(result_rows):
            raise RuntimeError("P0 duplicate terminal result")
        write_csv(self.root / "simulation_results.csv", RESULT_COLUMNS, result_rows)
        write_csv(self.root / "simulation_task_results.csv", TASK_COLUMNS, task_rows)
        write_csv(self.root / "simulation_job_results.csv", JOB_COLUMNS, job_rows)
        aggregation = aggregate_ext1(self.root)
        summary = {
            "requested": len(plan), "terminal": len(result_rows),
            "complete": len(result_rows) == len(plan), **aggregation,
        }
        atomic_write_json(self.root / "summary.json", summary)
        write_file_hashes(self.root)
        return Ext1Outcome(self.root, len(plan), len(result_rows), self.stop_requested, summary)


def analyze_ext1(root: Path) -> Mapping[str, Any]:
    summary = aggregate_ext1(root)
    write_file_hashes(root)
    return summary
