"""Independent EXT-1B nine-scheduler mechanism-stress runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path
import signal
import subprocess
from typing import Any, Dict, Mapping, Optional, Sequence

from .cell_model import expand_cells
from .config import canonical_json, domain_hash, fraction_text
from .ext1b_aggregation import aggregate_ext1b
from .ext1b_config import (
    dump_ext1b_config, ext1b_config_hash, load_ext1b_config,
)
from .ext1b_generation import (
    ScenarioInstance, StructuralRejection, build_scenario_instance,
    scenario_cells,
)
from .result_writer import (
    FAILURE_COLUMNS, atomic_write_json, read_csv, write_csv, write_file_hashes,
)
from .scheduler_pairing import assert_scheduler_only_difference
from .scheduler_registry import SCHEDULERS, audited_scheduler_registry
from .simulation_engine import (
    SimulationConfigurationError, SimulationExecution, load_simulation_terminal,
    run_paired_simulation, write_simulation_terminal,
)
from .simulation_result import SimulationStatus
from .taskset_store import TasksetStore, prepare_service_curve


REGISTRY_COLUMNS = tuple(SCHEDULERS[0].row())
GENERATION_ATTEMPT_COLUMNS = (
    "attempt_id", "scenario_kind", "scenario_subtype", "scenario_cell_id",
    "logical_taskset_index", "attempt_index", "source_taskset_index",
    "generation_seed", "source_taskset_id", "source_taskset_hash",
    "attempt_status", "rejection_code", "rejection_detail",
)
GENERATED_COLUMNS = (
    "taskset_id", "taskset_hash", "source_taskset_id", "source_taskset_hash",
    "scenario_kind", "scenario_subtype", "scenario_cell_id",
    "logical_taskset_index", "accepted_attempt_index", "generation_seed", "M",
    "task_n", "priority_hash", "power_hash", "deadline_hash", "release_hash",
    "workload_power_mapping_json", "task_input_json", "canonical_taskset_json",
)
SCENARIO_INSTANCE_COLUMNS = (
    "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "logical_taskset_index", "taskset_id", "taskset_hash",
    "trace_hash", "generation_seed", "M", "horizon", "maximum_horizon",
    "initial_battery", "battery_capacity", "nominal_demand_j_per_tick",
    "nominal_harvest_j_per_tick", "nominal_energy_supply_ratio",
    "base_harvesting_rate_w", "allow_harvest_clipping", "priority_hash",
    "power_hash", "deadline_hash", "release_hash", "structure_json",
    "system_template_path",
)
REQUEST_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "taskset_id", "taskset_hash", "trace_hash",
    "simulation_config_hash", "input_hash", "scheduler_id", "generation_seed",
    "M", "initial_battery", "battery_capacity", "horizon", "maximum_horizon",
    "priority_hash", "power_hash", "deadline_hash", "release_hash",
    "workload_vector_hash", "simulator_build_hash", "request_status",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "request_id", "scheduler_id", "attempt_number", "status",
    "runtime_seconds", "horizons_attempted", "recorded_at_utc",
)
RESULT_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "taskset_id", "taskset_hash", "trace_hash",
    "simulation_config_hash", "input_hash", "scheduler_id", "status", "reason",
    "comparison_eligible", "horizon", "horizon_censoring", "runtime_seconds",
    "attempt_count", "overall_success", "top_m_success",
    "top_m_max_response_time", "top_m_first_execution_vector",
    "first_missed_priority_rank", "first_missed_priority_rank_numeric",
    "timing_activation", "missed_jobs", "first_miss_time",
    "maximum_observed_response_time", "mean_response_time", "completed_jobs",
    "preemptions", "processor_wait_ticks", "energy_blocked_ticks",
    "bypass_count", "synchronization_wait_ticks",
    "idle_cores_while_ready_jobs_exist_ticks", "st_charge_begin_count",
    "st_charge_hold_ticks", "st_charge_release_count",
    "st_charge_release_reasons", "harvested_energy_j", "consumed_energy_j",
    "battery_minimum_j", "battery_maximum_j", "battery_trajectory_json",
    "retained_trace_path",
)
TASK_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "scheduler_id", "taskset_id", "taskset_hash", "task_id",
    "priority_rank", "C", "D", "T", "D_over_T", "workload", "unit_energy",
    "observed_jobs", "completed_jobs", "missed_jobs", "censored_jobs",
    "maximum_observed_response_time", "first_execution_time", "horizon_coverage",
    "minimum_jobs_satisfied", "request_comparison_eligible",
)

EXT1B_FAIRNESS_FIELDS = (
    "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
    "initial_battery", "battery_capacity", "horizon", "maximum_horizon",
    "generation_seed", "M", "priority_hash", "power_hash", "deadline_hash",
    "release_hash", "workload_vector_hash", "simulator_build_hash",
)
UNAVAILABLE = "UNAVAILABLE"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _available(value: Any) -> Any:
    return UNAVAILABLE if value is None else value


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return f"MISSING:{path}"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project_root),
        capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else UNAVAILABLE


def assert_ext1b_fair_pairing(rows: Sequence[Mapping[str, Any]]) -> None:
    assert_scheduler_only_difference(rows)
    grouped: Dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["paired_instance_id"]), []).append(row)
    for pair_id, members in grouped.items():
        for field in EXT1B_FAIRNESS_FIELDS:
            values = {str(row[field]) for row in members}
            if len(values) != 1:
                raise RuntimeError(f"P0 EXT-1B fairness mismatch in {field} for {pair_id}")


@dataclass(frozen=True)
class Ext1BOutcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


class Ext1BRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        if self.config.get("extension") != "EXT-1B":
            raise ValueError("EXT-1B runner requires extension: EXT-1B")
        audited_scheduler_registry()
        self.project_root = Path(__file__).resolve().parents[2]
        self.root = Path(self.config["execution"]["output_root"])
        self.terminals = self.root / "simulation_terminal_results"
        self.stop_requested = False

    @classmethod
    def from_path(cls, path: Path | str) -> "Ext1BRunner":
        return cls(load_ext1b_config(path))

    def describe(
        self, *, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Dict[str, Any]:
        cells = [
            (base.cell_id, scenario.row())
            for base in expand_cells(self.config)
            for scenario in scenario_cells(self.config)
        ]
        if max_cells is not None:
            cells = cells[:max_cells]
        tasksets = int(self.config["grid"]["tasksets_per_cell"])
        if max_tasksets is not None:
            tasksets = min(tasksets, max_tasksets)
        return {
            "extension": "EXT-1B",
            "parameter_status": self.config["parameter_status"],
            "config_hash": ext1b_config_hash(self.config),
            "cell_count": len(cells),
            "tasksets_per_cell": tasksets,
            "scheduler_count": len(SCHEDULERS),
            "paired_instance_count": len(cells) * tasksets,
            "simulation_request_count": len(cells) * tasksets * len(SCHEDULERS),
            "cells": cells,
            "scheduler_ids": [item.scheduler_id for item in SCHEDULERS],
        }

    def _install_signal_handlers(self) -> Dict[int, Any]:
        previous: Dict[int, Any] = {}

        def stop(_signum: int, _frame: Any) -> None:
            self.stop_requested = True

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, stop)
        return previous

    def _simulator_path(self) -> Path:
        path = Path(str(self.config["simulation"]["simulator_bin"]))
        return path if path.is_absolute() else self.project_root / path

    def _initialize(self, resume: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.terminals.mkdir(parents=True, exist_ok=True)
        run_config = self.root / "run_config.yaml"
        if run_config.is_file():
            observed = load_ext1b_config(run_config)
            if ext1b_config_hash(observed) != ext1b_config_hash(self.config):
                raise RuntimeError("resume config hash mismatch")
            if not resume and any(self.terminals.glob("*.json")):
                raise RuntimeError("terminal results exist; use --resume")
        else:
            dump_ext1b_config(self.config, run_config)
        write_csv(
            self.root / "scheduler_registry.csv", REGISTRY_COLUMNS,
            [item.row() for item in audited_scheduler_registry()],
        )
        for name, columns in (
            ("generation_attempts.csv", GENERATION_ATTEMPT_COLUMNS),
            ("simulation_attempts.csv", ATTEMPT_COLUMNS),
            ("failures.csv", FAILURE_COLUMNS),
        ):
            if not (self.root / name).is_file():
                write_csv(self.root / name, columns, [])
        metadata_path = self.root / "run_metadata.json"
        if not metadata_path.is_file():
            atomic_write_json(metadata_path, {
                "schema": "ASAP_BLOCK_V9_3_EXT1B_METADATA_V1",
                "experiment_id": self.config["experiment_id"],
                "extension": "EXT-1B",
                "parameter_status": self.config["parameter_status"],
                "seed_space": self.config["seed_space"],
                "config_hash": ext1b_config_hash(self.config),
                "git_head": _git_head(self.project_root),
                "simulator_path": str(self._simulator_path()),
                "simulator_build_hash": _sha256_file(self._simulator_path()),
                "bootstrap_seed": self.config["statistics"]["bootstrap_seed"],
                "bootstrap_resamples": self.config["statistics"]["bootstrap_resamples"],
                "holm_family": "eight ASAP-BLOCK comparator tests within each cell and binary endpoint",
                "selection_policy": "structural predicates and runtime activation only; scheduler outcomes excluded",
                "simulation_is_schedulability_proof": False,
                "created_at_utc": _utc_now(),
            })

    @staticmethod
    def _validate_terminal_identity(
        request: Mapping[str, Any], execution: SimulationExecution,
    ) -> None:
        if execution.simulation_id != str(request["request_id"]):
            raise RuntimeError("P0 EXT-1B terminal simulation_id mismatch")
        if execution.result.configured_scheduler != str(request["scheduler_id"]):
            raise RuntimeError("P0 EXT-1B terminal scheduler mismatch")

    def _checkpoint(self, requested: int, terminal: int) -> None:
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": "ASAP_BLOCK_V9_3_EXT1B_CHECKPOINT_V1",
            "config_hash": ext1b_config_hash(self.config),
            "requested": requested,
            "terminal": terminal,
            "pending": requested - terminal,
            "stop_requested": self.stop_requested,
            "updated_at_utc": _utc_now(),
        })

    @staticmethod
    def _csv_data_rows(path: Path) -> int:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader)
            return sum(1 for _ in reader)

    def _completed_resume_outcome(
        self, *, resume: bool, max_cells: Optional[int],
        max_tasksets: Optional[int],
    ) -> Optional[Ext1BOutcome]:
        """Return a validated completed result without touching its bytes."""

        if not resume or max_cells is not None or max_tasksets is not None:
            return None
        run_config = self.root / "run_config.yaml"
        if not run_config.is_file():
            return None
        observed = load_ext1b_config(run_config)
        config_hash = ext1b_config_hash(self.config)
        if ext1b_config_hash(observed) != config_hash:
            raise RuntimeError("resume config hash mismatch")
        if not verify_file_hashes(self.root):
            return None

        table_names = {
            "generated_tasksets.csv", "generation_attempts.csv",
            "scenario_instances.csv", "simulation_requests.csv",
            "simulation_attempts.csv", "simulation_results.csv",
            "task_outcomes.csv", "failures.csv", "mechanism_activation.csv",
            "paired_scheduler_outcomes.csv", "scheduler_summary.csv",
            "scenario_summary.csv", "priority_rank_summary.csv",
            "paired_statistics.csv", "ext1b_plot_data.csv",
            "scheduler_registry.csv",
        }
        observation_files: Dict[str, str] = {}
        scenario_kind = str(self.config["scenario"]["kind"])
        if scenario_kind == "SYNC_BATCH_STRESS":
            observation_files = {
                "b2_decision_rows": "b2_batch_decisions.csv",
                "b2_summary_rows": "b2_summary.csv",
            }
        elif scenario_kind == "TIMING_STRESS":
            observation_files = {
                "b3_event_rows": "b3_timing_events.csv",
                "b3_summary_rows": "b3_summary.csv",
            }
        table_names.update(observation_files.values())
        required = table_names | {
            "checkpoint.json", "file_hashes.sha256", "run_config.yaml",
            "run_metadata.json",
        }
        if any(not (self.root / name).is_file() for name in required):
            return None

        description = self.describe()
        expected_requests = int(description["simulation_request_count"])
        expected_pairs = int(description["paired_instance_count"])
        requests = read_csv(self.root / "simulation_requests.csv")
        if len(requests) != expected_requests:
            return None
        request_ids = [str(row["request_id"]) for row in requests]
        if len(set(request_ids)) != expected_requests:
            raise RuntimeError("P0 duplicate EXT-1B persisted request")
        assert_ext1b_fair_pairing(requests)
        grouped: Dict[str, list[Mapping[str, Any]]] = {}
        for request in requests:
            pair_id = str(request["paired_instance_id"])
            grouped.setdefault(pair_id, []).append(request)
            expected_id = domain_hash(
                "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v1",
                {"paired_instance_id": pair_id,
                 "scheduler_id": str(request["scheduler_id"])},
            )
            if str(request["request_id"]) != expected_id:
                raise RuntimeError("P0 EXT-1B persisted request identity mismatch")
            if str(request["request_status"]) != "PLANNED":
                return None
        scheduler_order = [item.scheduler_id for item in SCHEDULERS]
        if len(grouped) != expected_pairs or any(
            [str(row["scheduler_id"]) for row in members] != scheduler_order
            for members in grouped.values()
        ):
            raise RuntimeError("P0 EXT-1B persisted plan ordering mismatch")

        generated = read_csv(self.root / "generated_tasksets.csv")
        instances = read_csv(self.root / "scenario_instances.csv")
        if (
            len(generated) != expected_pairs
            or len(instances) != expected_pairs
            or len({row["taskset_hash"] for row in generated}) != expected_pairs
            or {row["paired_instance_id"] for row in instances} != set(grouped)
        ):
            return None

        try:
            checkpoint = json.loads(
                (self.root / "checkpoint.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        checkpoint_state = {
            "schema": "ASAP_BLOCK_V9_3_EXT1B_CHECKPOINT_V1",
            "config_hash": config_hash,
            "requested": expected_requests,
            "terminal": expected_requests,
            "pending": 0,
            "stop_requested": False,
        }
        if set(checkpoint) != {*checkpoint_state, "updated_at_utc"}:
            return None
        if any(checkpoint.get(key) != value for key, value in checkpoint_state.items()):
            return None
        if not isinstance(checkpoint.get("updated_at_utc"), str):
            return None

        terminal_ids = {path.stem for path in self.terminals.glob("*.json")}
        planned_ids = set(request_ids)
        unexpected = terminal_ids - planned_ids
        if unexpected:
            raise RuntimeError(
                f"unplanned EXT-1B terminal results in output root: {sorted(unexpected)}"
            )
        if terminal_ids != planned_ids:
            return None

        results = read_csv(self.root / "simulation_results.csv")
        attempts = read_csv(self.root / "simulation_attempts.csv")
        result_by_id = {str(row["request_id"]): row for row in results}
        attempt_by_id = {str(row["request_id"]): row for row in attempts}
        if (
            len(results) != expected_requests
            or len(result_by_id) != expected_requests
            or set(result_by_id) != planned_ids
            or len(attempts) != expected_requests
            or len(attempt_by_id) != expected_requests
            or set(attempt_by_id) != planned_ids
            or read_csv(self.root / "failures.csv")
        ):
            return None

        result_identity_fields = (
            "request_id", "paired_instance_id", "scenario_kind",
            "scenario_subtype", "scenario_cell_id", "taskset_id",
            "taskset_hash", "trace_hash", "simulation_config_hash",
            "input_hash", "scheduler_id",
        )
        for request in requests:
            request_id = str(request["request_id"])
            execution = load_simulation_terminal(
                self.terminals / f"{request_id}.json"
            )
            self._validate_terminal_identity(request, execution)
            result = result_by_id[request_id]
            attempt = attempt_by_id[request_id]
            if any(
                str(result[field]) != str(request[field])
                for field in result_identity_fields
            ):
                raise RuntimeError("P0 EXT-1B result identity mismatch")
            if (
                execution.attempt_count != 1
                or str(result["scheduler_id"]) != str(request["scheduler_id"])
                or str(result["status"]) != execution.result.status.value
                or str(attempt["scheduler_id"]) != str(request["scheduler_id"])
                or str(attempt["status"]) != execution.result.status.value
                or str(attempt["attempt_number"]) != "1"
                or execution.result.status in {
                    SimulationStatus.INTERNAL_ERROR,
                    SimulationStatus.RUNTIME_TIMEOUT,
                }
                or execution.retained_trace_path is None
                or not execution.retained_trace_path.is_file()
            ):
                return None

        aggregation_files = {
            "activation_rows": "mechanism_activation.csv",
            "paired_rows": "paired_scheduler_outcomes.csv",
            "scheduler_summary_rows": "scheduler_summary.csv",
            "scenario_summary_rows": "scenario_summary.csv",
            "priority_summary_rows": "priority_rank_summary.csv",
            "statistics_rows": "paired_statistics.csv",
            "plots_rows": "ext1b_plot_data.csv",
            **observation_files,
        }
        summary = {
            "requested": expected_requests,
            "terminal": expected_requests,
            "complete": True,
            "parameter_status": self.config["parameter_status"],
            **{
                key: self._csv_data_rows(self.root / name)
                for key, name in aggregation_files.items()
            },
        }
        return Ext1BOutcome(
            self.root, expected_requests, expected_requests, False, summary,
        )

    def _selected_cells(self, max_cells: Optional[int]) -> list[tuple[Any, Any]]:
        values = [
            (base, scenario)
            for base in expand_cells(self.config)
            for scenario in scenario_cells(self.config)
        ]
        return values if max_cells is None else values[:max_cells]

    def _plan(
        self, max_cells: Optional[int], max_tasksets: Optional[int],
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
        service = prepare_service_curve(self.config, self.root / "generation_service")
        store = TasksetStore(
            Path(self.config["execution"]["taskset_store"]), self.config, service
        )
        count = int(self.config["grid"]["tasksets_per_cell"])
        if max_tasksets is not None:
            count = min(count, max_tasksets)
        retry_limit = int(self.config["scenario"]["structural_retry_limit"])
        start = int(self.config["grid"].get("taskset_index_start", 0))
        simulator_hash = _sha256_file(self._simulator_path())
        generated_rows: list[Dict[str, Any]] = []
        attempt_rows: list[Dict[str, Any]] = []
        instance_rows: list[Dict[str, Any]] = []
        requests: list[Dict[str, Any]] = []

        for base_cell, scenario_cell in self._selected_cells(max_cells):
            for logical_offset in range(count):
                logical_index = start + logical_offset
                accepted: Optional[ScenarioInstance] = None
                for attempt_index in range(retry_limit):
                    source_index = logical_index * retry_limit + attempt_index
                    stored = store.get_or_create(base_cell, source_index)
                    attempt_id = domain_hash(
                        "ASAP_BLOCK:V9.3:EXT1B:GENERATION_ATTEMPT:v1",
                        {
                            "scenario_cell_id": scenario_cell.cell_id,
                            "logical_taskset_index": logical_index,
                            "attempt_index": attempt_index,
                            "generation_seed": stored.seed,
                        },
                    )
                    common = {
                        "attempt_id": attempt_id,
                        "scenario_kind": scenario_cell.kind,
                        "scenario_subtype": scenario_cell.subtype,
                        "scenario_cell_id": scenario_cell.cell_id,
                        "logical_taskset_index": logical_index,
                        "attempt_index": attempt_index,
                        "source_taskset_index": source_index,
                        "generation_seed": stored.seed,
                        "source_taskset_id": stored.taskset_id,
                        "source_taskset_hash": stored.semantic_hash,
                    }
                    try:
                        accepted = build_scenario_instance(
                            stored, self.config, scenario_cell,
                            logical_taskset_index=logical_index,
                            attempt_index=attempt_index,
                            system_root=self.root / "scenario_systems",
                        )
                    except StructuralRejection as exc:
                        attempt_rows.append({
                            **common, "attempt_status": "REJECTED",
                            "rejection_code": exc.code,
                            "rejection_detail": exc.detail,
                        })
                        continue
                    attempt_rows.append({
                        **common, "attempt_status": "ACCEPTED",
                        "rejection_code": "", "rejection_detail": "",
                    })
                    break
                if accepted is None:
                    write_csv(
                        self.root / "generation_attempts.csv",
                        GENERATION_ATTEMPT_COLUMNS, attempt_rows,
                    )
                    raise RuntimeError(
                        f"structural retry limit exhausted for {scenario_cell.cell_id}/{logical_index}"
                    )

                instance = accepted
                canonical_path = self.root / "scenario_tasksets" / f"{instance.taskset_hash}.json"
                power_map = [
                    {"task_id": row["task_id"], "priority_rank": row["priority_rank"],
                     "workload": row["workload"], "unit_energy": row["P"]}
                    for row in instance.tasks
                ]
                generated_rows.append({
                    "taskset_id": instance.taskset_id,
                    "taskset_hash": instance.taskset_hash,
                    "source_taskset_id": instance.source_taskset_id,
                    "source_taskset_hash": instance.source_taskset_hash,
                    "scenario_kind": instance.scenario_cell.kind,
                    "scenario_subtype": instance.subtype,
                    "scenario_cell_id": instance.scenario_cell.cell_id,
                    "logical_taskset_index": instance.logical_taskset_index,
                    "accepted_attempt_index": instance.attempt_index,
                    "generation_seed": instance.generation_seed,
                    "M": instance.processors,
                    "task_n": len(instance.tasks),
                    "priority_hash": instance.priority_hash,
                    "power_hash": instance.power_hash,
                    "deadline_hash": instance.deadline_hash,
                    "release_hash": instance.release_hash,
                    "workload_power_mapping_json": canonical_json(power_map),
                    "task_input_json": canonical_json(instance.tasks),
                    "canonical_taskset_json": str(canonical_path),
                })
                instance_rows.append({
                    "paired_instance_id": instance.paired_instance_id,
                    "scenario_kind": instance.scenario_cell.kind,
                    "scenario_subtype": instance.subtype,
                    "scenario_cell_id": instance.scenario_cell.cell_id,
                    "logical_taskset_index": instance.logical_taskset_index,
                    "taskset_id": instance.taskset_id,
                    "taskset_hash": instance.taskset_hash,
                    "trace_hash": instance.trace_hash,
                    "generation_seed": instance.generation_seed,
                    "M": instance.processors,
                    "horizon": self.config["simulation"]["horizon"],
                    "maximum_horizon": self.config["simulation"]["maximum_horizon"],
                    "initial_battery": fraction_text(instance.initial_battery),
                    "battery_capacity": fraction_text(instance.battery_capacity),
                    "nominal_demand_j_per_tick": fraction_text(instance.nominal_demand_j_per_tick),
                    "nominal_harvest_j_per_tick": fraction_text(instance.nominal_harvest_j_per_tick),
                    "nominal_energy_supply_ratio": fraction_text(instance.scenario_cell.nominal_supply_ratio),
                    "base_harvesting_rate_w": fraction_text(instance.base_harvesting_rate_w),
                    "allow_harvest_clipping": instance.allow_harvest_clipping,
                    "priority_hash": instance.priority_hash,
                    "power_hash": instance.power_hash,
                    "deadline_hash": instance.deadline_hash,
                    "release_hash": instance.release_hash,
                    "structure_json": canonical_json(instance.structure),
                    "system_template_path": str(instance.system_template_path),
                })
                simulation_hash = domain_hash(
                    "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_CONFIG:v1",
                    {
                        "simulation": self.config["simulation"],
                        "initial_battery": fraction_text(instance.initial_battery),
                        "battery_capacity": fraction_text(instance.battery_capacity),
                        "allow_harvest_clipping": instance.allow_harvest_clipping,
                        "system_template_hash": _sha256_file(instance.system_template_path),
                    },
                )
                workload_vector_hash = domain_hash(
                    "ASAP_BLOCK:V9.3:EXT1B:WORKLOAD_VECTOR:v1", power_map
                )
                fair_material = {
                    "taskset_hash": instance.taskset_hash,
                    "trace_hash": instance.trace_hash,
                    "simulation_config_hash": simulation_hash,
                    "generation_seed": instance.generation_seed,
                    "M": instance.processors,
                    "initial_battery": fraction_text(instance.initial_battery),
                    "battery_capacity": fraction_text(instance.battery_capacity),
                    "horizon": self.config["simulation"]["horizon"],
                    "maximum_horizon": self.config["simulation"]["maximum_horizon"],
                    "priority_hash": instance.priority_hash,
                    "power_hash": instance.power_hash,
                    "deadline_hash": instance.deadline_hash,
                    "release_hash": instance.release_hash,
                    "workload_vector_hash": workload_vector_hash,
                    "simulator_build_hash": simulator_hash,
                }
                input_hash = domain_hash(
                    "ASAP_BLOCK:V9.3:EXT1B:FAIR_INPUT:v1", fair_material
                )
                for registration in SCHEDULERS:
                    request_id = domain_hash(
                        "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v1",
                        {"paired_instance_id": instance.paired_instance_id,
                         "scheduler_id": registration.scheduler_id},
                    )
                    requests.append({
                        "request_id": request_id,
                        "paired_instance_id": instance.paired_instance_id,
                        "scenario_kind": instance.scenario_cell.kind,
                        "scenario_subtype": instance.subtype,
                        "scenario_cell_id": instance.scenario_cell.cell_id,
                        "taskset_id": instance.taskset_id,
                        **fair_material,
                        "input_hash": input_hash,
                        "scheduler_id": registration.scheduler_id,
                        "request_status": "PLANNED",
                        "instance": instance,
                    })
        assert_ext1b_fair_pairing(requests)
        return generated_rows, attempt_rows, instance_rows, requests

    def _task_and_request_outcomes(
        self, request: Mapping[str, Any], execution: Any,
    ) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
        instance: ScenarioInstance = request["instance"]
        result = execution.result
        observations = {task.task_id: task for task in result.tasks}
        jobs: Dict[str, list[Any]] = {}
        for job in result.jobs:
            jobs.setdefault(job.task_id, []).append(job)
        task_rows = []
        for payload in instance.tasks:
            task_id = str(payload["task_id"])
            observed = observations.get(task_id)
            task_jobs = [job for job in jobs.get(task_id, []) if job.eligible_after_warmup]
            first_execution = min(
                (job.first_execution for job in task_jobs if job.first_execution is not None),
                default=None,
            )
            task_rows.append({
                "request_id": request["request_id"],
                "paired_instance_id": request["paired_instance_id"],
                "scenario_kind": request["scenario_kind"],
                "scenario_subtype": request["scenario_subtype"],
                "scenario_cell_id": request["scenario_cell_id"],
                "scheduler_id": request["scheduler_id"],
                "taskset_id": request["taskset_id"],
                "taskset_hash": request["taskset_hash"],
                "task_id": task_id,
                "priority_rank": payload["priority_rank"],
                "C": payload["C"], "D": payload["D"], "T": payload["T"],
                "D_over_T": payload["D_over_T"],
                "workload": payload["workload"], "unit_energy": payload["P"],
                "observed_jobs": observed.observed_jobs if observed else 0,
                "completed_jobs": observed.completed_jobs if observed else 0,
                "missed_jobs": observed.missed_jobs if observed else 0,
                "censored_jobs": observed.censored_jobs if observed else 0,
                "maximum_observed_response_time": _available(observed.r_sim_max if observed else None),
                "first_execution_time": _available(first_execution),
                "horizon_coverage": _available(observed.horizon_coverage if observed else None),
                "minimum_jobs_satisfied": observed.minimum_jobs_satisfied if observed else False,
                "request_comparison_eligible": result.comparison_eligible,
            })
        top_count = min(int(self.config["statistics"]["top_m"]), len(task_rows))
        top_rows = sorted(task_rows, key=lambda row: int(row["priority_rank"]))[:top_count]
        if result.comparison_eligible:
            top_success: Any = all(
                int(row["missed_jobs"]) == 0 and bool(row["minimum_jobs_satisfied"])
                for row in top_rows
            )
            overall: Any = result.status is SimulationStatus.PASS_OBSERVED
        else:
            top_success = overall = UNAVAILABLE
        missed_ranks = [
            int(row["priority_rank"]) for row in task_rows if int(row["missed_jobs"]) > 0
        ]
        if not result.comparison_eligible:
            first_missed: Any = UNAVAILABLE
            first_missed_numeric: Any = UNAVAILABLE
        elif missed_ranks:
            first_missed = first_missed_numeric = min(missed_ranks)
        else:
            first_missed = "NONE"
            first_missed_numeric = UNAVAILABLE
        top_responses = [
            int(row["maximum_observed_response_time"])
            for row in top_rows
            if row["maximum_observed_response_time"] != UNAVAILABLE
        ]
        top_first = [row["first_execution_time"] for row in top_rows]
        metrics = dict(result.metrics)
        scheduler_id = str(request["scheduler_id"])
        if scheduler_id != "gpfp_asap_nonblock":
            metrics["bypass_count"] = None
        if scheduler_id != "gpfp_asap_sync":
            metrics["synchronization_wait_ticks"] = None
        if not scheduler_id.startswith("gpfp_st_"):
            for key in (
                "st_charge_begin_count", "st_charge_hold_ticks",
                "st_charge_release_count", "st_charge_release_reasons",
            ):
                metrics[key] = None
        row = {
            **{key: request[key] for key in (
                "request_id", "paired_instance_id", "scenario_kind",
                "scenario_subtype", "scenario_cell_id", "taskset_id",
                "taskset_hash", "trace_hash", "simulation_config_hash",
                "input_hash", "scheduler_id",
            )},
            "status": result.status.value,
            "reason": result.reason,
            "comparison_eligible": result.comparison_eligible,
            "horizon": result.horizon,
            "horizon_censoring": result.status is SimulationStatus.HORIZON_INSUFFICIENT,
            "runtime_seconds": f"{execution.runtime_seconds:.9f}",
            "attempt_count": execution.attempt_count,
            "overall_success": overall,
            "top_m_success": top_success,
            "top_m_max_response_time": max(top_responses) if top_responses else UNAVAILABLE,
            "top_m_first_execution_vector": canonical_json(top_first),
            "first_missed_priority_rank": first_missed,
            "first_missed_priority_rank_numeric": first_missed_numeric,
            "timing_activation": UNAVAILABLE,
            **{key: _available(metrics.get(key)) for key in (
                "missed_jobs", "first_miss_time", "maximum_observed_response_time",
                "mean_response_time", "completed_jobs", "preemptions",
                "processor_wait_ticks", "energy_blocked_ticks", "bypass_count",
                "synchronization_wait_ticks", "idle_cores_while_ready_jobs_exist_ticks",
                "st_charge_begin_count", "st_charge_hold_ticks",
                "st_charge_release_count", "harvested_energy_j",
                "consumed_energy_j", "battery_minimum_j", "battery_maximum_j",
            )},
            "st_charge_release_reasons": (
                canonical_json(metrics["st_charge_release_reasons"])
                if metrics.get("st_charge_release_reasons") is not None
                else UNAVAILABLE
            ),
            "battery_trajectory_json": canonical_json(metrics.get("battery_trajectory", [])),
            "retained_trace_path": str(execution.retained_trace_path or ""),
        }
        return row, task_rows

    def run(
        self, *, resume: bool = False, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Ext1BOutcome:
        completed = self._completed_resume_outcome(
            resume=resume, max_cells=max_cells, max_tasksets=max_tasksets,
        )
        if completed is not None:
            return completed
        self._initialize(resume)
        generated, generation_attempts, instances, plan = self._plan(max_cells, max_tasksets)
        planned_ids = {str(row["request_id"]) for row in plan}
        terminal_ids = {path.stem for path in self.terminals.glob("*.json")}
        unexpected = sorted(terminal_ids - planned_ids)
        if unexpected:
            raise RuntimeError(
                f"unplanned EXT-1B terminal results in output root: {unexpected}"
            )
        public_requests = [{key: row[key] for key in REQUEST_COLUMNS} for row in plan]
        write_csv(self.root / "generated_tasksets.csv", GENERATED_COLUMNS, generated)
        write_csv(self.root / "generation_attempts.csv", GENERATION_ATTEMPT_COLUMNS, generation_attempts)
        write_csv(self.root / "scenario_instances.csv", SCENARIO_INSTANCE_COLUMNS, instances)
        write_csv(self.root / "simulation_requests.csv", REQUEST_COLUMNS, public_requests)
        previous = self._install_signal_handlers()
        terminal_count = 0
        failures: list[Dict[str, Any]] = []
        try:
            for request in plan:
                if self.stop_requested:
                    break
                terminal_path = self.terminals / f"{request['request_id']}.json"
                if terminal_path.is_file():
                    if not resume:
                        raise RuntimeError("terminal result exists; use --resume")
                    execution = load_simulation_terminal(terminal_path)
                    self._validate_terminal_identity(request, execution)
                else:
                    instance: ScenarioInstance = request["instance"]
                    energy = dict(self.config["energy"])
                    energy["simulation_initial_battery"] = fraction_text(instance.initial_battery)
                    energy["battery_capacity"] = fraction_text(instance.battery_capacity)
                    energy["allow_harvest_clipping"] = instance.allow_harvest_clipping
                    try:
                        execution = run_paired_simulation(
                            simulation_id_value=str(request["request_id"]),
                            base_system_path=instance.system_template_path,
                            run_root=self.root,
                            task_payload=instance.tasks,
                            taskset_hash=instance.taskset_hash,
                            processors=instance.processors,
                            # EXT-1B E_init is a t=0 mechanism input, not a
                            # proof-oriented lower bound at every later release.
                            exact_e0=Fraction(0),
                            energy_config=energy,
                            simulation_config=self.config["simulation"],
                            scheduler_id=str(request["scheduler_id"]),
                        )
                    except SimulationConfigurationError as exc:
                        raise RuntimeError(f"P0 simulation configuration failure: {exc}") from exc
                    write_simulation_terminal(terminal_path, execution)
                self._validate_terminal_identity(request, execution)
                terminal_count += 1
                if execution.result.status in {
                    SimulationStatus.INTERNAL_ERROR, SimulationStatus.RUNTIME_TIMEOUT,
                }:
                    failures.append({
                        "severity": "P1" if execution.result.status is SimulationStatus.INTERNAL_ERROR else "P2",
                        "stage": "SIMULATION", "analysis_id": request["request_id"],
                        "cell_id": request["scenario_cell_id"], "taskset_id": request["taskset_id"],
                        "variant": request["scheduler_id"],
                        "code": execution.result.status.value,
                        "detail": execution.result.reason,
                        "traceback": execution.stderr_tail,
                        "failure_input": str(execution.system_config_path),
                    })
                if terminal_count % int(self.config["execution"]["checkpoint_every"]) == 0:
                    self._checkpoint(len(plan), terminal_count)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        self._checkpoint(len(plan), terminal_count)

        result_rows, task_rows, attempt_rows = [], [], []
        for request in plan:
            path = self.terminals / f"{request['request_id']}.json"
            if not path.is_file():
                continue
            execution = load_simulation_terminal(path)
            self._validate_terminal_identity(request, execution)
            result_row, per_task = self._task_and_request_outcomes(request, execution)
            result_rows.append(result_row)
            task_rows.extend(per_task)
            attempt_rows.append({
                "attempt_id": domain_hash(
                    "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_ATTEMPT:v1",
                    {"request_id": request["request_id"], "attempt_number": 1},
                ),
                "request_id": request["request_id"],
                "scheduler_id": request["scheduler_id"],
                "attempt_number": 1,
                "status": execution.result.status.value,
                "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                "horizons_attempted": canonical_json(execution.horizons_attempted),
                "recorded_at_utc": "TERMINAL_MATERIALIZED",
            })
        if len({row["request_id"] for row in result_rows}) != len(result_rows):
            raise RuntimeError("P0 duplicate EXT-1B terminal result")
        write_csv(self.root / "simulation_attempts.csv", ATTEMPT_COLUMNS, attempt_rows)
        write_csv(self.root / "simulation_results.csv", RESULT_COLUMNS, result_rows)
        write_csv(self.root / "task_outcomes.csv", TASK_COLUMNS, task_rows)
        write_csv(self.root / "failures.csv", FAILURE_COLUMNS, failures)
        aggregation = aggregate_ext1b(self.root, self.config)
        summary = {
            "requested": len(plan), "terminal": len(result_rows),
            "complete": len(result_rows) == len(plan),
            "parameter_status": self.config["parameter_status"],
            **aggregation,
        }
        write_file_hashes(self.root)
        return Ext1BOutcome(
            self.root, len(plan), len(result_rows), self.stop_requested, summary
        )


def analyze_ext1b(root: Path) -> Mapping[str, Any]:
    config = load_ext1b_config(root / "run_config.yaml")
    summary = aggregate_ext1b(root, config)
    write_file_hashes(root)
    return {
        "parameter_status": config["parameter_status"],
        **summary,
    }


def verify_file_hashes(root: Path) -> bool:
    manifest = root / "file_hashes.sha256"
    if not manifest.is_file():
        return False
    listed = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        fields = line.split("  ", 1)
        if len(fields) != 2 or len(fields[0]) != 64:
            return False
        digest, relative = fields
        if relative in listed or relative == "file_hashes.sha256":
            return False
        listed.add(relative)
        path = root / relative
        if not path.is_file() or _sha256_file(path) != digest:
            return False
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "file_hashes.sha256"
    }
    return listed == actual
