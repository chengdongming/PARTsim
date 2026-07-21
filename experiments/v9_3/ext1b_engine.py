"""Independent EXT-1B configured-scheduler mechanism-stress runner."""

from __future__ import annotations

from collections import Counter
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
from .ext1b_capacity_contract import (
    CAPACITY_FEASIBILITY_ERROR_CODE,
    capacity_contract_identity,
    capacity_feasibility_violations,
)
from .ext1b_b3_target_trace import (
    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
    is_b3_target_trace_v2,
    target_trace_contract_material,
    v2_fair_input_identity,
    v2_request_identity,
    v2_simulation_config_identity,
    v2_taskset_hash_from_document,
)
from .ext1b_generation import (
    ScenarioInstance, StructuralRejection, build_scenario_instance,
    enforce_b3_capacity_feasibility, scenario_cells,
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
from .task_identity import runtime_job_id


REGISTRY_COLUMNS = tuple(SCHEDULERS[0].row())
GENERATION_ATTEMPT_COLUMNS = (
    "attempt_id", "scenario_kind", "scenario_subtype", "scenario_cell_id",
    "normalized_utilization", "logical_taskset_index", "attempt_index",
    "source_taskset_index",
    "generation_seed", "source_taskset_id", "source_taskset_hash",
    "attempt_status", "rejection_code", "rejection_detail",
    "experiment_id", "paired_instance_id", "logical_index", "source_index",
    "capacity_infeasible_task_count", "capacity_infeasible_taskset_count",
    "task_name", "workload", "actual_power", "actual_power_unit",
    "tick_duration", "tick_duration_unit", "task_tick_energy_mJ",
    "battery_capacity_mJ", "native_affordability_epsilon_mJ",
    "excess_energy_mJ", "energy_unit", "power_model_identity",
    "workload_contract_version", "capacity_feasibility_contract_version",
    "capacity_feasibility_contract_identity", "scenario_contract_id",
    "actual_trace_affordable_tick", "actual_trace_full_tick",
    "target_initial_slack", "configured_recovery_margin_ticks",
    "actual_trace_recovery_headroom", "predicate_satisfied",
    "violations_json", "diagnostics_json",
)
GENERATED_COLUMNS = (
    "taskset_id", "taskset_hash", "source_taskset_id", "source_taskset_hash",
    "scenario_kind", "scenario_subtype", "scenario_cell_id",
    "logical_taskset_index", "accepted_attempt_index", "generation_seed", "M",
    "task_n", "priority_hash", "power_hash", "deadline_hash", "release_hash",
    "workload_power_mapping_json", "task_input_json", "canonical_taskset_json",
    "scenario_candidate_identity", "capacity_feasibility_contract_version",
    "capacity_feasibility_contract_identity", "scenario_contract_id",
    "target_runtime_task_name", "target_arrival_time", "target_job_id",
    "target_recovery_contract_applicable", "recovery_prefix_identity",
    "recovery_prefix_length", "recovery_prefix_runtime_names_json",
    "recovery_prefix_required_energy",
    "materialized_battery_capacity", "actual_trace_target_affordable_tick",
    "actual_trace_full_tick",
)
SCENARIO_INSTANCE_COLUMNS = (
    "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "normalized_utilization", "logical_taskset_index",
    "taskset_id", "taskset_hash", "trace_hash", "generation_seed", "M",
    "horizon", "maximum_horizon",
    "initial_battery", "battery_capacity", "nominal_demand_j_per_tick",
    "nominal_harvest_j_per_tick", "nominal_energy_supply_ratio",
    "base_harvesting_rate_w", "allow_harvest_clipping", "priority_hash",
    "power_hash", "deadline_hash", "release_hash", "structure_json",
    "system_template_path", "scenario_candidate_identity",
    "capacity_feasibility_contract_version",
    "capacity_feasibility_contract_identity", "scenario_contract_id",
    "target_runtime_task_name", "target_arrival_time", "target_job_id",
    "target_recovery_contract_applicable", "recovery_prefix_identity",
    "recovery_prefix_length", "recovery_prefix_runtime_names_json",
    "recovery_prefix_required_energy",
    "materialized_battery_capacity", "actual_trace_target_affordable_tick",
    "actual_trace_full_tick",
)
REQUEST_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "taskset_id", "taskset_hash", "trace_hash",
    "simulation_config_hash", "input_hash", "scheduler_id", "generation_seed",
    "M", "initial_battery", "battery_capacity", "horizon", "maximum_horizon",
    "priority_hash", "power_hash", "deadline_hash", "release_hash",
    "workload_vector_hash", "simulator_build_hash", "request_status",
    "capacity_feasibility_contract_version",
    "capacity_feasibility_contract_identity", "scenario_contract_id",
    "target_runtime_task_name", "target_arrival_time", "target_job_id",
    "target_recovery_contract_applicable", "recovery_prefix_identity",
    "recovery_prefix_length", "recovery_prefix_runtime_names_json",
    "recovery_prefix_required_energy",
    "materialized_battery_capacity", "actual_trace_target_affordable_tick",
    "actual_trace_full_tick",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "request_id", "scheduler_id", "attempt_number", "status",
    "runtime_seconds", "horizons_attempted", "recorded_at_utc",
)
RESULT_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "scenario_contract_id", "target_runtime_task_name",
    "target_arrival_time", "target_job_id", "taskset_id", "taskset_hash",
    "target_recovery_contract_applicable", "recovery_prefix_identity",
    "recovery_prefix_length", "recovery_prefix_runtime_names_json",
    "recovery_prefix_required_energy",
    "materialized_battery_capacity", "actual_trace_target_affordable_tick",
    "actual_trace_full_tick",
    "trace_hash",
    "simulation_config_hash", "input_hash", "scheduler_id", "status", "reason",
    "comparison_eligible", "horizon", "horizon_censoring", "runtime_seconds",
    "attempt_count", "overall_success", "top_m_success",
    "top_m_max_response_time", "top_m_first_execution_vector",
    "first_missed_priority_rank", "first_missed_priority_rank_numeric",
    "timing_activation", "target_wait_observed",
    "target_positive_slack_transition",
    "target_transition_after_slack_exhaustion",
    "target_terminated_without_transition",
    "any_target_job_positive_transition_count",
    "later_target_job_positive_transition_count",
    "non_target_positive_transition_count", "activation_from_other_job_only",
    "target_audit_closed", "target_audit_error_count",
    "missed_jobs", "first_miss_time",
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
    "scenario_contract_id", "target_runtime_task_name",
    "target_arrival_time", "target_job_id",
    "target_recovery_contract_applicable", "recovery_prefix_identity",
    "recovery_prefix_length", "recovery_prefix_runtime_names_json",
    "recovery_prefix_required_energy",
    "materialized_battery_capacity", "actual_trace_target_affordable_tick",
    "actual_trace_full_tick",
)
UNAVAILABLE = "UNAVAILABLE"
TARGET_JOB_FIELDS = (
    "target_runtime_task_name", "target_arrival_time", "target_job_id",
)
RECOVERY_CONTRACT_FIELDS = (
    "target_recovery_contract_applicable", "recovery_prefix_identity",
    "recovery_prefix_length", "recovery_prefix_runtime_names_json",
    "recovery_prefix_required_energy",
    "materialized_battery_capacity", "actual_trace_target_affordable_tick",
    "actual_trace_full_tick",
)
TRACE_TARGET_CONTRACT_FIELDS = TARGET_JOB_FIELDS + RECOVERY_CONTRACT_FIELDS


def _generation_attempt_diagnostic_fields(
    diagnostics: Mapping[str, Any],
) -> Dict[str, Any]:
    declared: Dict[str, Any] = {}
    extra: Dict[str, Any] = {}

    for key, value in diagnostics.items():
        if key in GENERATION_ATTEMPT_COLUMNS and key != "diagnostics_json":
            declared[key] = value
        else:
            extra[key] = value

    return {
        **declared,
        "diagnostics_json": canonical_json(extra),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _available(value: Any) -> Any:
    return UNAVAILABLE if value is None else value


def _contract_bool(value: Any) -> bool:
    return value is True or str(value).strip().upper() in {"TRUE", "1"}


def _b3_capacity_contract_identity(config: Mapping[str, Any]) -> str:
    if config.get("scenario", {}).get("kind") != "TIMING_STRESS":
        return ""
    return capacity_contract_identity(config)


def _request_identity(
    paired_instance_id: str,
    scheduler_id: str,
    capacity_identity: str,
    scenario_contract_id: str = "",
    target_runtime_task_name: str = "",
    target_arrival_time: int | str = "",
    target_job_id: str = "",
    recovery_contract: Mapping[str, Any] | None = None,
) -> str:
    if scenario_contract_id:
        if scenario_contract_id != B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2:
            raise ValueError("unknown EXT-1B/B3 scenario contract identity")
        recovery = dict(recovery_contract or {})
        normalized_recovery = {
            "target_recovery_contract_applicable": _contract_bool(
                recovery["target_recovery_contract_applicable"]
            ),
            "recovery_prefix_identity": str(
                recovery["recovery_prefix_identity"]
            ),
            "recovery_prefix_length": int(
                recovery["recovery_prefix_length"]
            ),
            "recovery_prefix_runtime_names_json": str(
                recovery["recovery_prefix_runtime_names_json"]
            ),
            "recovery_prefix_required_energy": str(
                recovery["recovery_prefix_required_energy"]
            ),
            "materialized_battery_capacity": str(
                recovery["materialized_battery_capacity"]
            ),
            "actual_trace_target_affordable_tick": int(
                recovery["actual_trace_target_affordable_tick"]
            ),
            "actual_trace_full_tick": int(
                recovery["actual_trace_full_tick"]
            ),
        }
        return v2_request_identity(
            paired_instance_id=paired_instance_id,
            scheduler_id=scheduler_id,
            capacity_feasibility_contract_identity=capacity_identity,
            target_runtime_task_name=target_runtime_task_name,
            target_arrival_time=int(target_arrival_time),
            target_job_id=target_job_id,
            recovery_contract=normalized_recovery,
        )
    if capacity_identity:
        return domain_hash(
            "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v2",
            {
                "paired_instance_id": paired_instance_id,
                "scheduler_id": scheduler_id,
                "capacity_feasibility_contract_identity": capacity_identity,
            },
        )
    return domain_hash(
        "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_REQUEST:v1",
        {
            "paired_instance_id": paired_instance_id,
            "scheduler_id": scheduler_id,
        },
    )


def _target_contract_fields(
    structure: Mapping[str, Any], *, required: bool,
) -> Dict[str, Any]:
    if not required:
        return {key: "" for key in TRACE_TARGET_CONTRACT_FIELDS}
    structure_fields = tuple(
        key for key in TRACE_TARGET_CONTRACT_FIELDS
        if key != "recovery_prefix_runtime_names_json"
    )
    missing = [key for key in structure_fields if key not in structure]
    if missing:
        raise RuntimeError(f"B3-v2 structure lacks target job identity: {missing}")
    name = str(structure["target_runtime_task_name"])
    arrival = structure["target_arrival_time"]
    if isinstance(arrival, bool) or not isinstance(arrival, int) or arrival != 0:
        raise RuntimeError("B3-v2 target arrival time must equal integer zero")
    job_id = str(structure["target_job_id"])
    if job_id != runtime_job_id(name, arrival):
        raise RuntimeError("B3-v2 target job identity is inconsistent")
    applicable = _contract_bool(
        structure["target_recovery_contract_applicable"]
    )
    prefix_identity = str(structure["recovery_prefix_identity"])
    prefix_length = int(structure["recovery_prefix_length"])
    if applicable:
        if len(prefix_identity) != 64 or prefix_length <= 0:
            raise RuntimeError("B3-v2 recovery prefix identity is invalid")
    elif prefix_identity or prefix_length != 0:
        raise RuntimeError("B3-v2 non-applicable recovery prefix must be empty")
    recovery_values = {
        key: structure[key]
        for key in RECOVERY_CONTRACT_FIELDS
        if key != "recovery_prefix_runtime_names_json"
    }
    recovery_values["recovery_prefix_runtime_names_json"] = canonical_json(
        structure.get("recovery_prefix_runtime_names", [])
    )
    return {
        "target_runtime_task_name": name,
        "target_arrival_time": arrival,
        "target_job_id": job_id,
        **recovery_values,
    }


def _bind_target_identity_to_trace(
    request: Mapping[str, Any], execution: SimulationExecution, *, materialize: bool,
) -> None:
    """Persist or validate the B3-v2 target identity in the retained trace."""

    if not str(request.get("scenario_contract_id", "")):
        return
    path = execution.retained_trace_path
    if path is None or not path.is_file():
        raise RuntimeError("P0 B3-v2 retained trace is missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("P0 B3-v2 retained trace is invalid") from exc
    expected = {
        key: request[key] for key in TRACE_TARGET_CONTRACT_FIELDS
    }
    expected["target_runtime_task_name"] = str(
        expected["target_runtime_task_name"]
    )
    expected["target_arrival_time"] = int(expected["target_arrival_time"])
    expected["target_job_id"] = str(expected["target_job_id"])
    expected["target_recovery_contract_applicable"] = _contract_bool(
        expected["target_recovery_contract_applicable"]
    )
    expected["recovery_prefix_length"] = int(
        expected["recovery_prefix_length"]
    )
    expected["actual_trace_target_affordable_tick"] = int(
        expected["actual_trace_target_affordable_tick"]
    )
    expected["actual_trace_full_tick"] = int(
        expected["actual_trace_full_tick"]
    )
    observed = {
        key: document.get(key) for key in TRACE_TARGET_CONTRACT_FIELDS
    }
    if materialize:
        if any(value is not None for value in observed.values()) and observed != expected:
            raise RuntimeError("P0 B3-v2 retained trace target identity conflict")
        document.update(expected)
        atomic_write_json(path, document)
    elif observed != expected:
        raise RuntimeError("P0 B3-v2 retained trace target identity mismatch")


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


def assert_ext1b_fair_pairing(
    rows: Sequence[Mapping[str, Any]],
    schedulers: Sequence[str] = tuple(item.scheduler_id for item in SCHEDULERS),
) -> None:
    assert_scheduler_only_difference(rows, schedulers)
    grouped: Dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["paired_instance_id"]), []).append(row)
    for pair_id, members in grouped.items():
        for field in EXT1B_FAIRNESS_FIELDS:
            values = {str(row.get(field, "")) for row in members}
            if len(values) != 1:
                raise RuntimeError(f"P0 EXT-1B fairness mismatch in {field} for {pair_id}")


@dataclass(frozen=True)
class Ext1BOutcome:
    output_root: Path
    requested: int
    terminal: int
    stopped: bool
    summary: Mapping[str, Any]


@dataclass(frozen=True)
class Ext1BPlanOutcome:
    output_root: Path
    generated_tasksets: int
    generation_attempts: int
    paired_instances: int
    simulation_requests: int
    summary: Mapping[str, Any]


class Ext1BRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        if self.config.get("extension") != "EXT-1B":
            raise ValueError("EXT-1B runner requires extension: EXT-1B")
        registry = {
            item.scheduler_id: item for item in audited_scheduler_registry()
        }
        self.schedulers = tuple(
            registry[scheduler_id] for scheduler_id in self.config["scheduler_ids"]
        )
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
            "scheduler_count": len(self.schedulers),
            "paired_instance_count": len(cells) * tasksets,
            "simulation_request_count": len(cells) * tasksets * len(self.schedulers),
            "cells": cells,
            "scheduler_ids": [item.scheduler_id for item in self.schedulers],
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
            [item.row() for item in self.schedulers],
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
            comparator_count = sum(
                item.scheduler_id != "gpfp_asap_block" for item in self.schedulers
            )
            comparator_label = (
                "eight" if comparator_count == 8 else str(comparator_count)
            )
            capacity_identity = _b3_capacity_contract_identity(self.config)
            target_trace_v2 = is_b3_target_trace_v2(self.config)
            metadata = {
                "schema": (
                    "ASAP_BLOCK_V9_3_EXT1B_B3_TARGET_TRACE_METADATA_V3"
                    if target_trace_v2
                    else "ASAP_BLOCK_V9_3_EXT1B_METADATA_V2"
                    if capacity_identity
                    else "ASAP_BLOCK_V9_3_EXT1B_METADATA_V1"
                ),
                "experiment_id": self.config["experiment_id"],
                "extension": "EXT-1B",
                "parameter_status": self.config["parameter_status"],
                "seed_space": self.config["seed_space"],
                "config_hash": ext1b_config_hash(self.config),
                "task_workload_contract": self.config["generation"][
                    "workload_contract"
                ],
                "git_head": _git_head(self.project_root),
                "simulator_path": str(self._simulator_path()),
                "simulator_build_hash": _sha256_file(self._simulator_path()),
                "bootstrap_seed": self.config["statistics"]["bootstrap_seed"],
                "bootstrap_resamples": self.config["statistics"]["bootstrap_resamples"],
                "holm_family": (
                    f"{comparator_label} ASAP-BLOCK comparator tests within each "
                    "cell and binary endpoint"
                ),
                "selection_policy": "structural predicates and runtime activation only; scheduler outcomes excluded",
                "simulation_is_schedulability_proof": False,
                "created_at_utc": _utc_now(),
            }
            if capacity_identity:
                metadata.update({
                    "capacity_feasibility_contract_version": self.config[
                        "scenario"
                    ]["capacity_feasibility_contract"],
                    "capacity_feasibility_contract_identity": (
                        capacity_identity
                    ),
                })
            if target_trace_v2:
                metadata["target_trace_contract"] = (
                    target_trace_contract_material()
                )
            atomic_write_json(metadata_path, metadata)

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
        if scenario_kind == "BYPASS_STRESS":
            observation_files = {
                "b1_episode_rows": "b1_bypass_episodes.csv",
                "b1_task_effect_rows": "b1_task_effects.csv",
                "b1_paired_effect_rows": "b1_paired_effects.csv",
                "b1_summary_rows": "b1_summary.csv",
            }
        elif scenario_kind == "SYNC_BATCH_STRESS":
            observation_files = {
                "b2_decision_rows": "b2_batch_decisions.csv",
                "b2_summary_rows": "b2_summary.csv",
            }
        elif scenario_kind == "TIMING_STRESS":
            observation_files = {
                "b3_event_rows": "b3_timing_events.csv",
                "b3_summary_rows": "b3_summary.csv",
            }
            if is_b3_target_trace_v2(self.config):
                observation_files["b3_calibration_summary_rows"] = (
                    "b3_calibration_summary.csv"
                )
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
        assert_ext1b_fair_pairing(requests, self.config["scheduler_ids"])
        grouped: Dict[str, list[Mapping[str, Any]]] = {}
        capacity_identity = _b3_capacity_contract_identity(self.config)
        scenario_contract_id = str(
            self.config.get("scenario", {}).get("scenario_contract_id", "")
        )
        for request in requests:
            pair_id = str(request["paired_instance_id"])
            grouped.setdefault(pair_id, []).append(request)
            expected_id = _request_identity(
                pair_id,
                str(request["scheduler_id"]),
                capacity_identity,
                scenario_contract_id,
                str(request.get("target_runtime_task_name", "")),
                request.get("target_arrival_time", ""),
                str(request.get("target_job_id", "")),
                {
                    key: request.get(key, "")
                    for key in RECOVERY_CONTRACT_FIELDS
                },
            )
            if str(request["request_id"]) != expected_id:
                raise RuntimeError("P0 EXT-1B persisted request identity mismatch")
            if capacity_identity and str(request.get(
                "capacity_feasibility_contract_identity", ""
            )) != capacity_identity:
                raise RuntimeError(
                    "P0 EXT-1B persisted capacity contract identity mismatch"
                )
            if str(request.get("scenario_contract_id", "")) != (
                scenario_contract_id
            ):
                raise RuntimeError(
                    "P0 EXT-1B persisted scenario contract mismatch"
                )
            if str(request["request_status"]) != "PLANNED":
                return None
        scheduler_order = [item.scheduler_id for item in self.schedulers]
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
            "scenario_subtype", "scenario_cell_id", "scenario_contract_id",
            "target_runtime_task_name", "target_arrival_time", "target_job_id",
            "target_recovery_contract_applicable", "recovery_prefix_identity",
            "recovery_prefix_length", "recovery_prefix_runtime_names_json",
            "recovery_prefix_required_energy",
            "materialized_battery_capacity",
            "actual_trace_target_affordable_tick", "actual_trace_full_tick",
            "taskset_id",
            "taskset_hash", "trace_hash", "simulation_config_hash",
            "input_hash", "scheduler_id",
        )
        for request in requests:
            request_id = str(request["request_id"])
            execution = load_simulation_terminal(
                self.terminals / f"{request_id}.json"
            )
            self._validate_terminal_identity(request, execution)
            _bind_target_identity_to_trace(
                request, execution, materialize=False,
            )
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
        target_trace_v2 = is_b3_target_trace_v2(self.config)
        scenario_contract_id = str(
            self.config["scenario"].get("scenario_contract_id", "")
        )
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
                        (
                            "ASAP_BLOCK:V9.3:EXT1B:GENERATION_ATTEMPT:v2"
                            if target_trace_v2
                            else "ASAP_BLOCK:V9.3:EXT1B:GENERATION_ATTEMPT:v1"
                        ),
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
                        "normalized_utilization": fraction_text(
                            base_cell.utilization
                        ),
                        "logical_taskset_index": logical_index,
                        "attempt_index": attempt_index,
                        "source_taskset_index": source_index,
                        "generation_seed": stored.seed,
                        "source_taskset_id": stored.taskset_id,
                        "source_taskset_hash": stored.semantic_hash,
                        "experiment_id": self.config["experiment_id"],
                        "paired_instance_id": "",
                        "logical_index": logical_index,
                        "source_index": source_index,
                        "scenario_contract_id": scenario_contract_id,
                    }
                    try:
                        candidate = build_scenario_instance(
                            stored, self.config, scenario_cell,
                            logical_taskset_index=logical_index,
                            attempt_index=attempt_index,
                            system_root=self.root / "scenario_systems",
                        )
                        if scenario_cell.kind == "TIMING_STRESS":
                            # Independent request-emission preflight.  The
                            # materializer already enforces the same predicate;
                            # this second gate prevents a mutated/reused
                            # ScenarioInstance from reaching any scheduler.
                            enforce_b3_capacity_feasibility(
                                candidate.tasks,
                                candidate.battery_capacity,
                                self.config,
                            )
                        accepted = candidate
                    except StructuralRejection as exc:
                        attempt_rows.append({
                            **common, "attempt_status": "REJECTED",
                            "rejection_code": exc.code,
                            "rejection_detail": exc.detail,
                            **_generation_attempt_diagnostic_fields(
                                exc.diagnostics
                            ),
                        })
                        continue
                    attempt_rows.append({
                        **common, "attempt_status": "ACCEPTED",
                        "rejection_code": "", "rejection_detail": "",
                        "paired_instance_id": accepted.paired_instance_id,
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
                target_job = _target_contract_fields(
                    instance.structure, required=target_trace_v2,
                )
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
                    "scenario_candidate_identity": (
                        instance.scenario_candidate_identity
                    ),
                    "capacity_feasibility_contract_version": (
                        self.config["scenario"].get(
                            "capacity_feasibility_contract", ""
                        )
                    ),
                    "capacity_feasibility_contract_identity": (
                        instance.capacity_feasibility_contract_identity
                    ),
                    "scenario_contract_id": scenario_contract_id,
                    **target_job,
                })
                instance_rows.append({
                    "paired_instance_id": instance.paired_instance_id,
                    "scenario_kind": instance.scenario_cell.kind,
                    "scenario_subtype": instance.subtype,
                    "scenario_cell_id": instance.scenario_cell.cell_id,
                    "normalized_utilization": fraction_text(base_cell.utilization),
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
                    "scenario_candidate_identity": (
                        instance.scenario_candidate_identity
                    ),
                    "capacity_feasibility_contract_version": (
                        self.config["scenario"].get(
                            "capacity_feasibility_contract", ""
                        )
                    ),
                    "capacity_feasibility_contract_identity": (
                        instance.capacity_feasibility_contract_identity
                    ),
                    "scenario_contract_id": scenario_contract_id,
                    **target_job,
                })
                simulation_material = {
                        "simulation": self.config["simulation"],
                        "initial_battery": fraction_text(instance.initial_battery),
                        "battery_capacity": fraction_text(instance.battery_capacity),
                        "allow_harvest_clipping": instance.allow_harvest_clipping,
                        "system_template_hash": _sha256_file(instance.system_template_path),
                }
                simulation_hash = (
                    v2_simulation_config_identity(**simulation_material)
                    if target_trace_v2
                    else domain_hash(
                        "ASAP_BLOCK:V9.3:EXT1B:SIMULATION_CONFIG:v1",
                        simulation_material,
                    )
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
                    "scenario_contract_id": scenario_contract_id,
                    **target_job,
                }
                input_hash = (
                    v2_fair_input_identity(fair_material)
                    if target_trace_v2
                    else domain_hash(
                        "ASAP_BLOCK:V9.3:EXT1B:FAIR_INPUT:v1", fair_material,
                    )
                )
                for registration in self.schedulers:
                    request_id = _request_identity(
                        instance.paired_instance_id,
                        registration.scheduler_id,
                        instance.capacity_feasibility_contract_identity,
                        scenario_contract_id,
                        target_job["target_runtime_task_name"],
                        target_job["target_arrival_time"],
                        target_job["target_job_id"],
                        {
                            key: target_job[key]
                            for key in RECOVERY_CONTRACT_FIELDS
                        },
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
                        "capacity_feasibility_contract_version": (
                            self.config["scenario"].get(
                                "capacity_feasibility_contract", ""
                            )
                        ),
                        "capacity_feasibility_contract_identity": (
                            instance.capacity_feasibility_contract_identity
                        ),
                        "scenario_contract_id": scenario_contract_id,
                        **target_job,
                        "instance": instance,
                    })
        assert_ext1b_fair_pairing(requests, self.config["scheduler_ids"])
        if target_trace_v2:
            store.verify_pairing_manifest(require_complete=False)
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
            "scenario_contract_id": request.get("scenario_contract_id", ""),
            **{
                key: request.get(key, "")
                for key in TRACE_TARGET_CONTRACT_FIELDS
            },
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
            **{key: UNAVAILABLE for key in (
                "target_wait_observed",
                "target_positive_slack_transition",
                "target_transition_after_slack_exhaustion",
                "target_terminated_without_transition",
                "any_target_job_positive_transition_count",
                "later_target_job_positive_transition_count",
                "non_target_positive_transition_count",
                "activation_from_other_job_only",
                "target_audit_closed",
                "target_audit_error_count",
            )},
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
                    _bind_target_identity_to_trace(
                        request, execution, materialize=False,
                    )
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
                    _bind_target_identity_to_trace(
                        request, execution, materialize=True,
                    )
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
            _bind_target_identity_to_trace(
                request, execution, materialize=False,
            )
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
        if is_b3_target_trace_v2(self.config):
            write_file_hashes(self.root)
            if not verify_file_hashes(self.root):
                raise RuntimeError("P0 B3-v2 output file hash verification failed")
            aggregation = aggregate_ext1b(
                self.root,
                self.config,
                output_file_hash_verification_closed=True,
            )
        missing_outputs = [
            name for name in self.config["required_outputs"]
            if not (self.root / name).is_file()
        ]
        if missing_outputs:
            raise RuntimeError(f"missing required EXT-1B outputs: {missing_outputs}")
        summary = {
            "requested": len(plan), "terminal": len(result_rows),
            "complete": len(result_rows) == len(plan),
            "parameter_status": self.config["parameter_status"],
            **aggregation,
        }
        write_file_hashes(self.root)
        if not verify_file_hashes(self.root):
            raise RuntimeError("P0 EXT-1B final output file hash verification failed")
        return Ext1BOutcome(
            self.root, len(plan), len(result_rows), self.stop_requested, summary
        )

    def materialize_plan(
        self, *, max_cells: Optional[int] = None,
        max_tasksets: Optional[int] = None,
    ) -> Ext1BPlanOutcome:
        """Materialize generation, transforms, scenarios, and requests only."""

        self._initialize(resume=False)
        generated, generation_attempts, instances, plan = self._plan(
            max_cells, max_tasksets
        )
        public_requests = [
            {key: row[key] for key in REQUEST_COLUMNS} for row in plan
        ]
        write_csv(
            self.root / "generated_tasksets.csv", GENERATED_COLUMNS, generated,
        )
        write_csv(
            self.root / "generation_attempts.csv",
            GENERATION_ATTEMPT_COLUMNS,
            generation_attempts,
        )
        write_csv(
            self.root / "scenario_instances.csv",
            SCENARIO_INSTANCE_COLUMNS,
            instances,
        )
        write_csv(
            self.root / "simulation_requests.csv",
            REQUEST_COLUMNS,
            public_requests,
        )
        contract = self.config["generation"]["workload_contract"]
        energy_by_workload = {
            str(row["workload"]): Fraction(str(row["energy_per_tick"]))
            for row in contract["power_model"]
        }
        idle_count = 0
        unknown_count = 0
        power_mismatch_count = 0
        task_record_count = 0
        observed_workloads: set[str] = set()
        capacity_infeasible_task_count = 0
        capacity_infeasible_taskset_count = 0
        capacity_identity = _b3_capacity_contract_identity(self.config)
        capacities_by_taskset = {
            str(row["taskset_id"]): Fraction(str(row["battery_capacity"]))
            for row in instances
        }
        for row in generated:
            tasks = json.loads(str(row["task_input_json"]))
            for task in tasks:
                task_record_count += 1
                workload = str(task.get("workload"))
                observed_workloads.add(workload)
                if workload == "idle":
                    idle_count += 1
                if workload not in energy_by_workload:
                    unknown_count += 1
                    continue
                try:
                    observed_power = Fraction(str(task.get("P")))
                except (ValueError, ZeroDivisionError):
                    power_mismatch_count += 1
                    continue
                if observed_power != energy_by_workload[workload]:
                    power_mismatch_count += 1
            if capacity_identity:
                violations = capacity_feasibility_violations(
                    tasks,
                    capacities_by_taskset[str(row["taskset_id"])],
                    self.config,
                )
                capacity_infeasible_task_count += len(violations)
                capacity_infeasible_taskset_count += bool(violations)
        capacity_rejection_count = sum(
            row.get("rejection_code") == CAPACITY_FEASIBILITY_ERROR_CODE
            for row in generation_attempts
        )
        workload_summary = {
            "schema": (
                "ASAP_BLOCK_V9_3_EXT1B_B3_TARGET_TRACE_WORKLOAD_SUMMARY_V2"
                if is_b3_target_trace_v2(self.config)
                else "ASAP_BLOCK_V9_3_WORKLOAD_CONTRACT_SUMMARY_V1"
            ),
            "workload_contract_version": contract["version"],
            "contract_identity": contract["contract_identity"],
            "candidate_identity": contract["candidate_identity"],
            "power_model_identity": contract["power_model_identity"],
            "ordered_candidates": list(contract["ordered_candidates"]),
            "observed_workloads": sorted(observed_workloads),
            "task_record_count": task_record_count,
            "idle_task_count": idle_count,
            "unknown_workload_count": unknown_count,
            "power_mismatch_count": power_mismatch_count,
            "legacy_taskset_count": 0,
        }
        if capacity_identity:
            workload_summary.update({
                "capacity_feasibility_contract_version": self.config[
                    "scenario"
                ]["capacity_feasibility_contract"],
                "capacity_feasibility_contract_identity": capacity_identity,
                "capacity_infeasible_task_count": (
                    capacity_infeasible_task_count
                ),
                "capacity_infeasible_taskset_count": (
                    capacity_infeasible_taskset_count
                ),
                "capacity_feasibility_rejection_count": (
                    capacity_rejection_count
                ),
            })
        if is_b3_target_trace_v2(self.config):
            workload_summary["target_trace_contract"] = (
                target_trace_contract_material()
            )
        atomic_write_json(
            self.root / "workload_contract_summary.json", workload_summary
        )
        if (
            idle_count
            or unknown_count
            or power_mismatch_count
            or capacity_infeasible_task_count
            or capacity_infeasible_taskset_count
        ):
            raise RuntimeError(
                "plan-only workload/capacity contract validation failed: "
                f"idle={idle_count}, unknown={unknown_count}, "
                f"power_mismatch={power_mismatch_count}, "
                f"capacity_tasks={capacity_infeasible_task_count}, "
                f"capacity_tasksets={capacity_infeasible_taskset_count}"
            )
        summary = {
            "schema": (
                "ASAP_BLOCK_V9_3_EXT1B_B3_TARGET_TRACE_PLAN_ONLY_V4"
                if is_b3_target_trace_v2(self.config)
                else "ASAP_BLOCK_V9_3_EXT1B_PLAN_ONLY_V3"
                if capacity_identity
                else "ASAP_BLOCK_V9_3_EXT1B_PLAN_ONLY_V2"
            ),
            "output_root": str(self.root),
            "plan_only": True,
            "simulator_invoked": False,
            "cell_count": len(self._selected_cells(max_cells)),
            "generated_tasksets": len(generated),
            "generation_attempts": len(generation_attempts),
            "paired_instances": len(instances),
            "simulation_requests": len(public_requests),
            "workload_contract_version": contract["version"],
            "candidate_identity": contract["candidate_identity"],
            "power_model_identity": contract["power_model_identity"],
            "idle_task_count": idle_count,
            "unknown_workload_count": unknown_count,
            "power_mismatch_count": power_mismatch_count,
            "legacy_taskset_count": 0,
        }
        if capacity_identity:
            summary.update({
                "capacity_feasibility_contract_version": self.config[
                    "scenario"
                ]["capacity_feasibility_contract"],
                "capacity_feasibility_contract_identity": capacity_identity,
                "capacity_infeasible_task_count": (
                    capacity_infeasible_task_count
                ),
                "capacity_infeasible_taskset_count": (
                    capacity_infeasible_taskset_count
                ),
                "capacity_feasibility_rejection_count": (
                    capacity_rejection_count
                ),
            })
        if is_b3_target_trace_v2(self.config):
            rejection_counts = Counter(
                str(row.get("rejection_code"))
                for row in generation_attempts
                if str(row.get("attempt_status")) == "REJECTED"
            )
            retry_limit = int(self.config["scenario"]["structural_retry_limit"])
            taskset_hash_audit_closed = True
            for row in generated:
                try:
                    document = json.loads(
                        Path(str(row["canonical_taskset_json"])).read_text(
                            encoding="utf-8"
                        )
                    )
                    observed_hash = v2_taskset_hash_from_document(document)
                except (KeyError, OSError, ValueError, json.JSONDecodeError):
                    taskset_hash_audit_closed = False
                    break
                if observed_hash != str(row["taskset_hash"]):
                    taskset_hash_audit_closed = False
                    break
            summary.update({
                "scenario_contract_id": (
                    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2
                ),
                "target_trace_contract": target_trace_contract_material(),
                "calibration_unit_count": len(
                    self._selected_cells(max_cells)
                ),
                "structurally_accepted_count": len(instances),
                "structural_rejection_attempt_count": sum(
                    rejection_counts.values()
                ),
                "structural_rejection_code_counts": dict(
                    sorted(rejection_counts.items())
                ),
                "identity_shape_audit_closed": all(
                    len(str(row["taskset_hash"])) == 64 for row in generated
                ),
                "taskset_hash_audit_closed": taskset_hash_audit_closed,
                "taskset_store_manifest_audit_closed": True,
                "pairing_audit_closed": True,
                "workload_audit_closed": not any((
                    idle_count, unknown_count, power_mismatch_count,
                )),
                "source_index_audit_closed": all(
                    int(row["source_taskset_index"])
                    == int(row["logical_taskset_index"]) * retry_limit
                    + int(row["attempt_index"])
                    for row in generation_attempts
                ),
            })
        atomic_write_json(self.root / "plan_summary.json", summary)
        if any(self.terminals.glob("*.json")):
            raise RuntimeError("plan-only output contains simulator terminals")
        write_file_hashes(self.root)
        return Ext1BPlanOutcome(
            self.root,
            len(generated),
            len(generation_attempts),
            len(instances),
            len(public_requests),
            summary,
        )


def analyze_ext1b(root: Path) -> Mapping[str, Any]:
    config = load_ext1b_config(root / "run_config.yaml")
    summary = aggregate_ext1b(root, config)
    write_file_hashes(root)
    if not verify_file_hashes(root):
        raise RuntimeError("P0 EXT-1B output file hash verification failed")
    if is_b3_target_trace_v2(config):
        summary = aggregate_ext1b(
            root,
            config,
            output_file_hash_verification_closed=True,
        )
        write_file_hashes(root)
        if not verify_file_hashes(root):
            raise RuntimeError("P0 B3-v2 final output file hash verification failed")
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
