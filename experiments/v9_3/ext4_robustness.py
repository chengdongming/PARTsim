"""Production EXT-4 single-axis robustness runner."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import random
import signal
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import asap_block_rta_v9_3 as rta_core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner

from .cell_model import Cell, expand_cells
from .config import config_hash, domain_hash, dump_config, fraction_text, load_config
from .execution_engine import _analysis_input, _variant, execute_isolated
from .ext4_aggregation import aggregate_ext4
from .generator_family import audited_generator_capabilities, required_service_period_max
from .priority_policy import priority_mapping_hash, registered_priority_policies
from .result_writer import (
    FAILURE_COLUMNS, append_csv_row, atomic_write_json, read_csv, write_csv,
    write_file_hashes,
)
from .robustness_pairing import pairing_type, sample_input_hash, verify_single_axis
from .simulation_engine import (
    load_simulation_terminal, run_paired_simulation, write_simulation_terminal,
)
from .simulation_result import SimulationStatus
from .taskset_store import StoredTaskset, TasksetStore, prepare_service_curve
from .validation import validate_analysis_result


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CELL_COLUMNS = (
    "cell_id", "changed_axis", "level", "availability", "pairing_type",
    "reason",
)
SAMPLE_COLUMNS = (
    "sample_id", "base_sample_id", "derived_sample_id", "cell_id",
    "changed_axis", "level", "pairing_type", "taskset_id", "taskset_hash",
    "before_canonical_input_hash", "after_canonical_input_hash",
    "priority_mapping_hash", "generation_seed", "canonical_path",
)
CHECK_COLUMNS = (
    "base_sample_id", "derived_sample_id", "changed_axis", "task_id",
    "field", "before_value", "after_value", "status",
)
RTA_COLUMNS = (
    "analysis_id", "sample_id", "taskset_id", "taskset_hash", "method",
    "solver_status", "certification_status", "taskset_proven",
    "response_bound", "candidate_vector_json", "runtime_seconds",
    "attempt_count", "timeout", "soundness_class", "tightness_gap",
    "p0_rta_pass_sim_fail", "dominance_violation",
)
SIM_COLUMNS = (
    "simulation_id", "sample_id", "taskset_id", "taskset_hash",
    "scheduler_id", "status", "reason", "comparison_eligible", "horizon",
    "runtime_seconds", "maximum_observed_response_time", "missed_jobs",
    "energy_blocked_ticks", "horizon_censoring",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "analysis_id", "sample_id", "method", "attempt_number",
    "timeout_budget_seconds", "solver_status", "runtime_seconds",
    "recorded_at_utc",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RobustnessSample:
    sample_id: str
    base_sample_id: str
    cell_id: str
    changed_axis: str
    level: str
    pairing_type: str
    stored: StoredTaskset
    before_hash: str
    after_hash: str

    def row(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id, "base_sample_id": self.base_sample_id,
            "derived_sample_id": self.sample_id, "cell_id": self.cell_id,
            "changed_axis": self.changed_axis, "level": self.level,
            "pairing_type": self.pairing_type,
            "taskset_id": self.stored.taskset_id, "taskset_hash": self.stored.semantic_hash,
            "before_canonical_input_hash": self.before_hash,
            "after_canonical_input_hash": self.after_hash,
            "priority_mapping_hash": self.stored.priority_hash,
            "generation_seed": self.stored.seed,
            "canonical_path": self.stored.canonical_path,
        }


def derive_constrained_deadline_sample(
    base: StoredTaskset, root: Path
) -> tuple[StoredTaskset, Tuple[Dict[str, Any], ...]]:
    """Apply the existing generator_uniform_integer D rule without changing C/T/P."""

    seed_hash = domain_hash(
        "ASAP_BLOCK:V9.3:EXT4:CONSTRAINED_DEADLINE_AXIS_SEED:v1",
        {"base_taskset_hash": base.semantic_hash, "distribution": "generator_uniform_integer"},
    )
    rng = random.Random(int(seed_hash[:16], 16))
    payload = []
    for row in base.task_payload:
        c_value, t_value = int(row["C"]), int(row["T"])
        deadline = t_value if c_value >= t_value else rng.randint(c_value, t_value - 1)
        payload.append({
            **dict(row), "D": deadline,
            "D_over_T": fraction_text(Fraction(deadline, t_value)),
        })
    checks = verify_single_axis(base.task_payload, payload, "deadline_mode")
    semantic_hash = sample_input_hash(payload)
    sample_id = f"ext4-deadline-{semantic_hash[:20]}"
    path = root / f"{sample_id}.json"
    atomic_write_json(path, {
        "schema": "ASAP_BLOCK_V9_3_EXT4_DERIVED_SAMPLE_V1",
        "base_taskset_hash": base.semantic_hash, "changed_axis": "deadline_mode",
        "distribution": "generator_uniform_integer", "tasks": payload,
        "taskset_hash": semantic_hash,
    })
    tasks = tuple(rta_core.V93Task(
        str(row["task_id"]), int(row["C"]), int(row["D"]), int(row["T"]),
        Fraction(str(row["P"])),
    ) for row in payload)
    return StoredTaskset(
        sample_id, base.generation_id, base.taskset_index, base.seed,
        semantic_hash, priority_mapping_hash(payload), base.power_hash,
        base.target_utilization, base.actual_utilization, base.processors,
        base.task_count, "constrained", tasks, tuple(payload), 0.0,
        base.service_curve_reference, path,
    ), checks


@dataclass(frozen=True)
class Ext4Outcome:
    output_root: Path
    rta_requested: int
    rta_terminal: int
    simulation_requested: int
    simulation_terminal: int
    stopped: bool
    summary: Mapping[str, Any]


class Ext4Runner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        if self.config.get("extension") != "EXT-4":
            raise ValueError("EXT-4 runner requires extension: EXT-4")
        if not isinstance(self.config.get("robustness"), dict):
            raise ValueError("EXT-4 requires robustness configuration")
        capabilities = audited_generator_capabilities()
        if self.config["robustness"].get("generator_family") not in capabilities["generator_families"]:
            raise ValueError("unsupported generator family")
        methods = self.config["robustness"].get("rta_methods")
        if methods != ["CW_THETA_CW", "LOC_THETA_LOC"]:
            raise ValueError("EXT-4 CORE-1 methods must be CW_THETA_CW and LOC_THETA_LOC")
        if self.config["robustness"].get("priority_policies") != list(registered_priority_policies()):
            raise ValueError("EXT-4 priority policies must equal audited registered policies")
        self.root = Path(self.config["execution"]["output_root"])
        self.rta_terminals = self.root / "rta_terminal_results"
        self.sim_terminals = self.root / "simulation_terminal_results"
        self.stop_requested = False

    @classmethod
    def from_path(cls, path: Path | str) -> "Ext4Runner":
        return cls(load_config(path, expected_core="CORE-3"))

    def describe(self, *, max_cells: Optional[int] = None, max_tasksets: Optional[int] = None) -> Dict[str, Any]:
        period_ranges = self.config["robustness"]["period_ranges"]
        sample_count = 2 + max(0, len(period_ranges) - 1)
        if max_tasksets is not None:
            sample_count = min(sample_count, max_tasksets)
        if max_cells == 0:
            sample_count = 0
        rta = sample_count * len(self.config["robustness"]["rta_methods"])
        simulations = sample_count
        return {
            "extension": "EXT-4", "config_hash": config_hash(self.config),
            "available_generator_families": ["UUNIFAST_DISCARD"],
            "available_priority_policies": list(registered_priority_policies()),
            "available_power_modes": ["generator_default_heterogeneous"],
            "sample_count": sample_count, "rta_analysis_count": rta,
            "simulation_request_count": simulations,
            "cells": list(self.config["robustness"]["period_ranges"]),
        }

    def _initialize(self, resume: bool) -> None:
        for path in (self.root, self.rta_terminals, self.sim_terminals, self.root / "derived_tasksets"):
            path.mkdir(parents=True, exist_ok=True)
        run_config = self.root / "run_config.yaml"
        if run_config.is_file():
            prior = load_config(run_config, expected_core="CORE-3")
            if config_hash(prior) != config_hash(self.config):
                raise RuntimeError("resume config hash mismatch")
            if not resume and (any(self.rta_terminals.glob("*.json")) or any(self.sim_terminals.glob("*.json"))):
                raise RuntimeError("terminal results exist; use --resume")
        else:
            dump_config(self.config, run_config)
        if not (self.root / "failures.csv").is_file():
            write_csv(self.root / "failures.csv", FAILURE_COLUMNS, [])
        if not (self.root / "analysis_attempts.csv").is_file():
            write_csv(self.root / "analysis_attempts.csv", ATTEMPT_COLUMNS, [])

    def _samples(self) -> tuple[list[RobustnessSample], list[Dict[str, Any]], list[Dict[str, Any]], Any]:
        service_config = deepcopy(self.config)
        service_config["generation"]["period_max"] = required_service_period_max(self.config)
        service = prepare_service_curve(service_config, self.root)
        base_cell = expand_cells(self.config)[0]
        store_root = (
            Path(self.config["execution"]["taskset_store"])
            / f"service_{service.identity[:16]}"
        )
        store = TasksetStore(store_root, self.config, service)
        index = self.config["grid"].get("taskset_index_start", 0)
        base_stored = store.get_or_create(base_cell, index)
        base_hash = sample_input_hash(base_stored.task_payload)
        base_sample_id = f"ext4-base-{base_stored.semantic_hash[:20]}"
        cells = []
        def add_cell(axis: str, level: str, availability: str, pair_kind: str, reason: str = "") -> str:
            cell_id = domain_hash("ASAP_BLOCK:V9.3:EXT4:CELL:v1", {
                "axis": axis, "level": level, "availability": availability,
            })
            cells.append({
                "cell_id": cell_id, "changed_axis": axis, "level": level,
                "availability": availability, "pairing_type": pair_kind, "reason": reason,
            })
            return cell_id
        base_cell_id = add_cell("baseline", "implicit_default_range", "AVAILABLE", "BASELINE")
        samples = [RobustnessSample(
            base_sample_id, base_sample_id, base_cell_id, "baseline",
            "implicit_default_range", "BASELINE", base_stored, base_hash, base_hash,
        )]
        derived, checks = derive_constrained_deadline_sample(
            base_stored, self.root / "derived_tasksets"
        )
        deadline_cell = add_cell("deadline_mode", "constrained", "AVAILABLE", "PAIRED_SINGLE_AXIS")
        samples.append(RobustnessSample(
            f"ext4-deadline-{derived.semantic_hash[:20]}", base_sample_id,
            deadline_cell, "deadline_mode", "constrained", "PAIRED_SINGLE_AXIS",
            derived, base_hash, sample_input_hash(derived.task_payload),
        ))
        check_rows = [{
            "base_sample_id": base_sample_id,
            "derived_sample_id": samples[-1].sample_id, **row,
        } for row in checks]
        ranges = self.config["robustness"]["period_ranges"]
        for alternate in ranges[1:]:
            child = deepcopy(self.config)
            child["generation"]["period_min"] = int(alternate["min"])
            child["generation"]["period_max"] = int(alternate["max"])
            alt_cell = expand_cells(child)[0]
            alt_store = TasksetStore(
                store_root / f"period_{alternate['id']}",
                child, service,
            )
            stored = alt_store.get_or_create(alt_cell, index)
            cell_id = add_cell(
                "period_range", str(alternate["id"]), "AVAILABLE",
                "UNPAIRED_STRATIFIED_COMPARISON",
                "changing the generator period range changes the generated task parameters",
            )
            samples.append(RobustnessSample(
                f"ext4-period-{stored.semantic_hash[:20]}", base_sample_id,
                cell_id, "period_range", str(alternate["id"]),
                "UNPAIRED_STRATIFIED_COMPARISON", stored, base_hash,
                sample_input_hash(stored.task_payload),
            ))
            check_rows.append({
                "base_sample_id": base_sample_id,
                "derived_sample_id": samples[-1].sample_id,
                "changed_axis": "period_range", "task_id": "",
                "field": "pairing", "before_value": ranges[0]["id"],
                "after_value": alternate["id"],
                "status": "UNPAIRED_STRATIFIED_COMPARISON",
            })
        add_cell("priority_policy", "RM_ONLY", "UNAVAILABLE", "UNAVAILABLE", "only RM is registered")
        add_cell("power_mode", "generator_default_heterogeneous_ONLY", "UNAVAILABLE", "UNAVAILABLE", "only one power mode is registered")
        add_cell("generator_family", "UUNIFAST_DISCARD_ONLY", "UNAVAILABLE", "UNAVAILABLE", "only one generator family is registered")
        return samples, cells, check_rows, service

    def _rta(self, samples: Sequence[RobustnessSample], base_cell: Cell, service: Any, resume: bool) -> list[Dict[str, Any]]:
        rows = []
        for sample in samples:
            for method in self.config["robustness"]["rta_methods"]:
                analysis_id = domain_hash("ASAP_BLOCK:V9.3:EXT4:RTA:v1", {
                    "sample_id": sample.sample_id, "taskset_hash": sample.stored.semantic_hash,
                    "method": method, "exact_e0": fraction_text(base_cell.exact_e0),
                })
                terminal = self.rta_terminals / f"{analysis_id}.json"
                if terminal.is_file():
                    if not resume:
                        raise RuntimeError("RTA terminal exists; use --resume")
                    rows.append(json.loads(terminal.read_text(encoding="utf-8")))
                    continue
                budgets = [float(self.config["analysis"]["timeout_seconds"])]
                if self.config["analysis"]["retry_policy"] == "timeout_once":
                    budgets.append(float(self.config["analysis"]["retry_timeout_seconds"]))
                final = None
                result = None
                attempts = 0
                for attempts, budget in enumerate(budgets, start=1):
                    request = production_runner.V93DispatchRequest(
                        analysis_id, _variant(method),
                        _analysis_input(sample.stored, base_cell, service, budget),
                        configuration_timeout_seconds=budget,
                    )
                    final = execute_isolated(request, budget)
                    append_csv_row(self.root / "analysis_attempts.csv", ATTEMPT_COLUMNS, {
                        "attempt_id": domain_hash("ASAP_BLOCK:V9.3:EXT4:ATTEMPT:v1", {"analysis_id": analysis_id, "attempt": attempts}),
                        "analysis_id": analysis_id, "sample_id": sample.sample_id,
                        "method": method, "attempt_number": attempts,
                        "timeout_budget_seconds": budget,
                        "solver_status": final.solver_status,
                        "runtime_seconds": f"{final.total_wall_seconds:.9f}",
                        "recorded_at_utc": _utc_now(),
                    })
                    result = final.result
                    if final.solver_status != "TIMEOUT":
                        break
                assert final is not None
                if result is not None:
                    validate_analysis_result(
                        result, sample.stored, expected_analysis_id=analysis_id,
                        expected_variant=_variant(method), source=None,
                    )
                    vector = {
                        record.task_id: record.candidate_response_time
                        for record in result.task_records
                        if record.candidate_response_time is not None
                    }
                    response_bound = max(vector.values(), default=None)
                    solver_status = result.solver_status.value
                    certification_status = result.certification_status.value
                    proven = result.taskset_proven
                else:
                    vector, response_bound = {}, None
                    solver_status, certification_status, proven = final.solver_status, "NOT_CERTIFIED", False
                row = {
                    "analysis_id": analysis_id, "sample_id": sample.sample_id,
                    "taskset_id": sample.stored.taskset_id,
                    "taskset_hash": sample.stored.semantic_hash, "method": method,
                    "solver_status": solver_status,
                    "certification_status": certification_status,
                    "taskset_proven": proven,
                    "response_bound": response_bound if response_bound is not None else "UNAVAILABLE",
                    "candidate_vector_json": json.dumps(vector, sort_keys=True, separators=(",", ":")),
                    "runtime_seconds": f"{final.total_wall_seconds:.9f}",
                    "attempt_count": attempts, "timeout": solver_status == "TIMEOUT",
                    "soundness_class": "PENDING_SIMULATION", "tightness_gap": "UNAVAILABLE",
                    "p0_rta_pass_sim_fail": False, "dominance_violation": False,
                }
                if result is None and solver_status == "INTERNAL_CONFORMANCE_FAILURE":
                    append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                        "severity": "P1", "stage": "RTA", "analysis_id": analysis_id,
                        "cell_id": sample.cell_id, "taskset_id": sample.stored.taskset_id,
                        "variant": method, "code": "RTA_INTERNAL_CONFORMANCE_FAILURE",
                        "detail": f"{final.exception_type}: {final.exception_message}",
                        "traceback": final.traceback_text or "",
                        "failure_input": sample.stored.canonical_path,
                    })
                atomic_write_json(terminal, row)
                rows.append(row)
                if self.stop_requested:
                    return rows
        return rows

    def _simulations(self, samples: Sequence[RobustnessSample], base_cell: Cell, resume: bool) -> list[Dict[str, Any]]:
        rows = []
        for sample in samples:
            simulation_id = domain_hash("ASAP_BLOCK:V9.3:EXT4:SIMULATION:v1", {
                "sample_id": sample.sample_id, "taskset_hash": sample.stored.semantic_hash,
                "scheduler": "gpfp_asap_block", "simulation": self.config["simulation"],
            })
            terminal = self.sim_terminals / f"{simulation_id}.json"
            if terminal.is_file():
                if not resume:
                    raise RuntimeError("simulation terminal exists; use --resume")
                execution = load_simulation_terminal(terminal)
            else:
                execution = run_paired_simulation(
                    simulation_id_value=simulation_id,
                    base_system_path=PROJECT_ROOT / self.config["energy"]["service_curve"]["system_template"],
                    run_root=self.root, task_payload=sample.stored.task_payload,
                    taskset_hash=sample.stored.semantic_hash,
                    processors=sample.stored.processors, exact_e0=base_cell.exact_e0,
                    energy_config=self.config["energy"], simulation_config=self.config["simulation"],
                    scheduler_id="gpfp_asap_block",
                )
                write_simulation_terminal(terminal, execution)
            metrics = execution.result.metrics
            rows.append({
                "simulation_id": simulation_id, "sample_id": sample.sample_id,
                "taskset_id": sample.stored.taskset_id,
                "taskset_hash": sample.stored.semantic_hash,
                "scheduler_id": "gpfp_asap_block", "status": execution.result.status.value,
                "reason": execution.result.reason,
                "comparison_eligible": execution.result.comparison_eligible,
                "horizon": execution.result.horizon,
                "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                "maximum_observed_response_time": metrics.get("maximum_observed_response_time", "UNAVAILABLE"),
                "missed_jobs": metrics.get("missed_jobs", "UNAVAILABLE"),
                "energy_blocked_ticks": metrics.get("energy_blocked_ticks", "UNAVAILABLE"),
                "horizon_censoring": execution.result.status is SimulationStatus.HORIZON_INSUFFICIENT,
            })
            if self.stop_requested:
                break
        return rows

    def _checkpoint(self, rta_requested: int, rta_terminal: int, sim_requested: int, sim_terminal: int) -> None:
        atomic_write_json(self.root / "checkpoint.json", {
            "schema": "ASAP_BLOCK_V9_3_EXT4_CHECKPOINT_V1",
            "config_hash": config_hash(self.config),
            "rta_requested": rta_requested, "rta_terminal": rta_terminal,
            "simulation_requested": sim_requested, "simulation_terminal": sim_terminal,
            "stop_requested": self.stop_requested, "updated_at_utc": _utc_now(),
        })

    def run(self, *, resume: bool = False, max_cells: Optional[int] = None, max_tasksets: Optional[int] = None) -> Ext4Outcome:
        self._initialize(resume)
        samples, cells, checks, service = self._samples()
        if max_tasksets is not None:
            samples = samples[:max_tasksets]
        if max_cells == 0:
            samples = []
        rta_requested = len(samples) * len(self.config["robustness"]["rta_methods"])
        sim_requested = len(samples)
        if rta_requested > int(self.config["robustness"]["max_rta_analyses"]):
            raise RuntimeError("EXT-4 RTA hard limit exceeded")
        if sim_requested > int(self.config["robustness"]["max_simulations"]):
            raise RuntimeError("EXT-4 simulation hard limit exceeded")
        write_csv(self.root / "robustness_cells.csv", CELL_COLUMNS, cells)
        write_csv(self.root / "base_and_derived_samples.csv", SAMPLE_COLUMNS, [sample.row() for sample in samples])
        write_csv(self.root / "unchanged_field_checks.csv", CHECK_COLUMNS, checks)
        base_cell = expand_cells(self.config)[0]
        prior = {}
        def stop(signum: int, _frame: Any) -> None:
            self.stop_requested = True
        for signum in (signal.SIGINT, signal.SIGTERM):
            prior[signum] = signal.getsignal(signum)
            signal.signal(signum, stop)
        try:
            rta_rows = self._rta(samples, base_cell, service, resume)
            sim_rows = [] if self.stop_requested else self._simulations(samples, base_cell, resume)
        finally:
            for signum, handler in prior.items():
                signal.signal(signum, handler)
        sim_by_sample = {row["sample_id"]: row for row in sim_rows}
        vectors = {(row["sample_id"], row["method"]): json.loads(row["candidate_vector_json"]) for row in rta_rows}
        dominance_samples = set()
        for sample in samples:
            cw = vectors.get((sample.sample_id, "CW_THETA_CW"), {})
            loc = vectors.get((sample.sample_id, "LOC_THETA_LOC"), {})
            if set(cw) == set(loc) and any(int(loc[key]) > int(cw[key]) for key in cw):
                dominance_samples.add(sample.sample_id)
        p0 = 0
        for row in rta_rows:
            simulation = sim_by_sample.get(row["sample_id"])
            if simulation is None:
                row["soundness_class"] = "SIMULATION_UNAVAILABLE"
            elif str(row["taskset_proven"]).lower() == "true" and simulation["status"] == "SIM_DEADLINE_MISS":
                row["soundness_class"] = "RTA_PASS_SIM_FAIL"
                row["p0_rta_pass_sim_fail"] = True
                p0 += 1
            elif str(row["taskset_proven"]).lower() == "true" and simulation["status"] == "SIM_PASS_OBSERVED":
                row["soundness_class"] = "RTA_PASS_SIM_PASS"
            elif simulation["status"] == "SIM_PASS_OBSERVED":
                row["soundness_class"] = "RTA_FAIL_SIM_PASS"
            elif simulation["status"] == "SIM_DEADLINE_MISS":
                row["soundness_class"] = "RTA_FAIL_SIM_FAIL"
            else:
                row["soundness_class"] = "NOT_EVALUABLE"
            observed = simulation.get("maximum_observed_response_time") if simulation else None
            if row["response_bound"] != "UNAVAILABLE" and observed not in (None, "UNAVAILABLE"):
                row["tightness_gap"] = int(row["response_bound"]) - int(observed)
            if row["sample_id"] in dominance_samples:
                row["dominance_violation"] = True
        if dominance_samples:
            p0 += len(dominance_samples)
        write_csv(self.root / "rta_results.csv", RTA_COLUMNS, rta_rows)
        write_csv(self.root / "simulation_results.csv", SIM_COLUMNS, sim_rows)
        existing_failure_keys = {
            (row["analysis_id"], row["code"])
            for row in read_csv(self.root / "failures.csv")
        }
        for row in rta_rows:
            classification = {
                "TIMEOUT": ("P2", "RTA_RUNTIME_TIMEOUT"),
                "INTERNAL_CONFORMANCE_FAILURE": ("P1", "RTA_INTERNAL_CONFORMANCE_FAILURE"),
            }.get(str(row["solver_status"]))
            if classification and (row["analysis_id"], classification[1]) not in existing_failure_keys:
                append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                    "severity": classification[0], "stage": "RTA",
                    "analysis_id": row["analysis_id"], "cell_id": "",
                    "taskset_id": row["taskset_id"], "variant": row["method"],
                    "code": classification[1], "detail": row["solver_status"],
                    "traceback": "", "failure_input": "",
                })
                existing_failure_keys.add((row["analysis_id"], classification[1]))
        for row in sim_rows:
            classification = {
                "SIM_RUNTIME_TIMEOUT": ("P2", "SIM_RUNTIME_TIMEOUT"),
                "SIM_INTERNAL_ERROR": ("P1", "SIM_INTERNAL_ERROR"),
            }.get(str(row["status"]))
            if classification and (row["simulation_id"], classification[1]) not in existing_failure_keys:
                append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                    "severity": classification[0], "stage": "SIMULATION",
                    "analysis_id": row["simulation_id"], "cell_id": "",
                    "taskset_id": row["taskset_id"], "variant": row["scheduler_id"],
                    "code": classification[1], "detail": row["reason"],
                    "traceback": "", "failure_input": "",
                })
                existing_failure_keys.add((row["simulation_id"], classification[1]))
        if p0:
            append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                "severity": "P0", "stage": "VALIDATION", "analysis_id": "EXT4",
                "cell_id": "", "taskset_id": "", "variant": "",
                "code": "RTA_SOUNDNESS_OR_DOMINANCE_VIOLATION",
                "detail": f"{p0} P0 validation violations", "traceback": "",
                "failure_input": self.root,
            })
            self.stop_requested = True
        aggregation = aggregate_ext4(self.root)
        summary = {
            "rta_requested": rta_requested, "rta_terminal": len(rta_rows),
            "simulation_requested": sim_requested, "simulation_terminal": len(sim_rows),
            "p0": p0, "available_priority_policies": list(registered_priority_policies()),
            "available_power_modes": ["generator_default_heterogeneous"],
            "available_generator_families": ["UUNIFAST_DISCARD"], **aggregation,
        }
        atomic_write_json(self.root / "summary.json", summary)
        self._checkpoint(rta_requested, len(rta_rows), sim_requested, len(sim_rows))
        write_file_hashes(self.root)
        return Ext4Outcome(
            self.root, rta_requested, len(rta_rows), sim_requested, len(sim_rows),
            self.stop_requested, summary,
        )


def analyze_ext4(root: Path) -> Mapping[str, Any]:
    result = aggregate_ext4(root)
    write_file_hashes(root)
    return result
