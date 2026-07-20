"""Paired CORE-3 production runner built on the shared v9.3 framework."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import shutil
import traceback
from typing import Any, Dict, Mapping, Optional, Sequence

from .censoring import censoring_label, task_is_tightness_eligible
from .cell_model import expand_cells
from .config import config_hash, fraction_text
from .core3_aggregation import (
    SoundnessClass,
    aggregate_tightness,
    classify_soundness,
    response_bound_violation_row,
    tightness_row,
)
from .execution_engine import ExecutionEngine, RunOutcome
from .result_writer import (
    FAILURE_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    append_csv_row,
    atomic_write_json,
    read_csv,
    write_csv,
    write_file_hashes,
)
from .simulation_engine import (
    SimulationConfigurationError,
    SimulationExecution,
    core3_energy_preflight,
    load_simulation_terminal,
    run_paired_simulation,
    shared_e0_simulation_identity,
    simulation_identity,
    write_simulation_terminal,
)
from .simulation_result import SimulationResult, SimulationStatus


CORE3_ARTIFACT_CONTRACT_VERSION = 2
CORE3_CHECKPOINT_SCHEMA = "ASAP_BLOCK_V9_3_CORE3_CHECKPOINT"
CORE3_CHECKPOINT_SCHEMA_VERSION = 2

SIMULATION_TASKSET_COLUMNS = (
    "simulation_id", "cell_id", "taskset_id", "taskset_hash", "exact_e0",
    "M", "status", "reason", "comparison_eligible",
    "comparison_ineligible_reason", "horizon",
    "simulation_initial_battery", "release_e0_valid",
    "minimum_release_energy_j", "service_curve_reference",
    "no_overflow_guard", "runtime_seconds", "attempt_count",
    "horizons_attempted", "observed_jobs", "completed_jobs", "missed_jobs",
    "censored_jobs", "retained_trace_path", "system_config_path",
    "taskset_path", "trace_schema_version", "configured_scheduler",
    "simulation_completed", "completion_reason",
)
SIMULATION_TASK_COLUMNS = (
    "simulation_id", "cell_id", "taskset_id", "taskset_hash", "exact_e0",
    "task_id", "observed_jobs", "completed_jobs", "missed_jobs",
    "censored_jobs", "r_sim_max", "horizon_coverage",
    "minimum_jobs_satisfied", "tightness_eligible",
    "response_bound_eligible", "censoring_label",
)
SIMULATION_JOB_COLUMNS = (
    "simulation_id", "cell_id", "taskset_id", "taskset_hash", "exact_e0",
    "task_id", "job_index", "release", "completion", "absolute_deadline",
    "response_time", "deadline_miss", "first_execution", "preemption_count",
    "energy_blocked_ticks", "processor_wait_ticks", "executed_ticks",
    "eligible_after_warmup", "censored", "censoring_reason",
)
SOUNDNESS_COLUMNS = (
    "analysis_id", "simulation_id", "cell_id", "taskset_id", "taskset_hash",
    "exact_e0", "analysis_variant", "rta_solver_status",
    "rta_certification_status", "rta_taskset_proven", "simulation_status",
    "release_e0_valid", "no_overflow_guard", "comparison_eligible",
    "comparison_ineligible_reason", "soundness_class", "p0_violation_candidate",
)
TIGHTNESS_COLUMNS = (
    "analysis_id", "cell_id", "taskset_id", "exact_e0", "analysis_variant",
    "task_id", "priority_rank", "D", "r_rta", "r_sim_max", "absolute_gap",
    "normalized_gap", "ratio", "slack_to_deadline", "exact_equality",
)
TIGHTNESS_TASKSET_COLUMNS = (
    "taskset_id", "analysis_variant", "task_count", "mean_absolute_gap",
    "max_absolute_gap", "mean_normalized_gap", "mean_ratio",
    "exact_equality_count",
)
RESPONSE_BOUND_VIOLATION_COLUMNS = (
    "code", "analysis_id", "simulation_id", "cell_id", "taskset_id",
    "taskset_hash", "exact_e0", "analysis_variant", "task_id",
    "priority_rank", "C", "D", "T", "P", "candidate_response_time",
    "r_sim_max", "absolute_gap", "simulation_status", "release_e0_valid",
    "no_overflow_guard", "comparison_eligible",
    "simulation_taskset_results_path", "simulation_task_results_path",
    "simulation_job_results_path", "retained_trace_path", "system_config_path",
    "taskset_path", "observation_trace_path", "failure_input",
)
CENSORING_COLUMNS = (
    "scope", "status", "reason", "count", "denominator",
)
RUNTIME_COLUMNS = (
    "kind", "identifier", "method", "status", "runtime_seconds",
    "attempt_count",
)
PLOT_COLUMNS = (
    "plot", "method", "taskset_id", "task_id", "category", "x", "y",
)
SUMMARY_COLUMNS = ("metric", "value", "denominator")


@dataclass(frozen=True)
class ResponseBoundEligibility:
    eligible: bool
    reason: str


@dataclass(frozen=True)
class Core3Outcome:
    output_root: Path
    requested_rta: int
    terminal_rta: int
    requested_simulations: int
    terminal_simulations: int
    stopped: bool
    summary: Mapping[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _truth(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _required_artifact_bool(
    row: Mapping[str, Any],
    field: str,
) -> bool:
    if field not in row:
        raise RuntimeError(
            f"CORE-3 contract-v2 row is missing boolean field {field!r}"
        )
    value = row[field]
    if type(value) is bool:
        return value
    if value == "True":
        return True
    if value == "False":
        return False
    raise RuntimeError(
        "CORE-3 contract-v2 boolean field "
        f"{field!r} has invalid value {value!r}"
    )


def _release_e0_valid(
    execution: SimulationExecution, exact_e0: Optional[Fraction] = None,
) -> bool:
    result = execution.result
    if exact_e0 is None:
        return result.release_e0_valid
    return bool(
        result.minimum_release_energy_j is not None
        and result.minimum_release_energy_j + 1e-12 >= float(exact_e0)
    )


def _observation_comparison_eligibility(
    execution: SimulationExecution,
    exact_e0: Optional[Fraction] = None,
) -> tuple[bool, str]:
    """Return the fail-closed CORE-3 observation gate and its first reason."""

    result = execution.result
    if result.status is SimulationStatus.HORIZON_INSUFFICIENT:
        return False, SoundnessClass.HORIZON_CENSORED.value
    if result.status in {
        SimulationStatus.RUNTIME_TIMEOUT,
        SimulationStatus.INTERNAL_ERROR,
    }:
        return False, SoundnessClass.SIM_TIMEOUT_OR_ERROR.value
    if not _release_e0_valid(execution, exact_e0):
        return False, SoundnessClass.ASSUMPTION_E0_NOT_SATISFIED.value
    if execution.attempt_count <= 0:
        return False, SoundnessClass.NO_OVERFLOW_GUARD_NOT_SATISFIED.value
    if (
        result.trace_schema_version != 2
        or result.configured_scheduler != "gpfp_asap_block"
        or not result.simulation_completed
        or result.completion_reason != "reached_horizon"
    ):
        return False, SoundnessClass.OBSERVATION_COMPARISON_INELIGIBLE.value
    return True, ""


def _response_bound_eligibility(
    execution: SimulationExecution,
    task: Optional[Any],
    rta_task_row: Mapping[str, Any],
    exact_e0: Optional[Fraction] = None,
) -> ResponseBoundEligibility:
    observation_eligible, observation_reason = (
        _observation_comparison_eligibility(execution, exact_e0)
    )
    if not observation_eligible:
        return ResponseBoundEligibility(False, observation_reason)
    if task is None:
        return ResponseBoundEligibility(False, "TASK_OBSERVATION_MISSING")
    if str(rta_task_row.get("task_solver_status")) != "CANDIDATE_FOUND":
        return ResponseBoundEligibility(False, "TASK_CANDIDATE_NOT_FOUND")
    if rta_task_row.get("candidate_response_time") in (None, ""):
        return ResponseBoundEligibility(
            False, "CANDIDATE_RESPONSE_TIME_MISSING"
        )
    if not task.minimum_jobs_satisfied:
        return ResponseBoundEligibility(False, "MINIMUM_JOBS_NOT_SATISFIED")
    if task.censored_jobs != 0:
        return ResponseBoundEligibility(False, "RIGHT_CENSORED")
    if task.r_sim_max is None:
        return ResponseBoundEligibility(False, "R_SIM_MAX_MISSING")
    return ResponseBoundEligibility(True, "ELIGIBLE")


def _rta_taskset_certified(row: Mapping[str, Any]) -> bool:
    return bool(
        str(row.get("solver_status")) == "COMPLETED"
        and str(row.get("certification_status")) == "CERTIFIED_TASKSET"
        and _truth(row.get("taskset_proven"))
    )


def deadline_soundness_violation(
    rta_rows: Sequence[Mapping[str, Any]],
    execution: SimulationExecution,
    exact_e0: Optional[Fraction] = None,
) -> bool:
    eligible, _reason = _observation_comparison_eligibility(execution, exact_e0)
    return bool(
        eligible
        and execution.result.status is SimulationStatus.DEADLINE_MISS
        and any(_rta_taskset_certified(row) for row in rta_rows)
    )


def _failed_execution(
    simulation_id_value: str,
    horizon: int,
    reason: str,
) -> SimulationExecution:
    result = SimulationResult(
        SimulationStatus.INTERNAL_ERROR, reason, horizon, (), (), False, None,
        {}, 2, "gpfp_asap_block", False, reason,
    )
    return SimulationExecution(
        simulation_id_value, result, 0.0, 0, (), Path(""), Path(""), None,
        "", traceback.format_exc()[-6000:],
    )


class Core3PairingRunner:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.root = Path(config["execution"]["output_root"])
        self.config_identity = config_hash(config)
        self.simulation_terminals = self.root / "simulation_terminal_results"
        self.stop_requested = False
        self._simulation_context: Dict[str, Dict[str, Any]] = {}
        self._energy_preflight: Optional[Dict[str, Any]] = None

    def energy_preflight(self) -> Dict[str, Any]:
        if self._energy_preflight is None:
            self._energy_preflight = core3_energy_preflight(self.config)
        return dict(self._energy_preflight)

    def require_energy_preflight(self) -> Dict[str, Any]:
        report = self.energy_preflight()
        if not report["no_overflow_preflight_valid"]:
            raise SimulationConfigurationError(
                "CORE-3 energy preflight failed before artifact creation: "
                f"initial={report['simulation_initial_battery_j']} "
                f"capacity={report['battery_capacity_j']} "
                f"offered_harvest={report['scaled_offered_harvest_j']} "
                f"required_capacity={report['required_capacity_j']} "
                f"required_safety_margin={report['required_safety_margin_j']}"
            )
        return report

    def _validate_existing_core3_contract(self) -> None:
        checkpoint_path = self.root / "checkpoint.json"
        existing_comparisons = bool(
            any(self.simulation_terminals.glob("*.json"))
            or (self.root / "soundness_matrix.csv").is_file()
            or (self.root / "response_bound_violations.csv").is_file()
            or (self.root / "summary.json").is_file()
        )
        if not checkpoint_path.exists():
            if existing_comparisons:
                raise RuntimeError(
                    "CORE-3 comparison artifacts exist without checkpoint.json"
                )
            return
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "CORE-3 comparison artifacts lack a readable contract checkpoint"
            ) from exc
        if not isinstance(checkpoint, dict):
            raise RuntimeError(
                "CORE-3 checkpoint must be a JSON object"
            )

        contract_version = checkpoint.get("core3_artifact_contract_version")
        if (
            type(contract_version) is not int
            or contract_version != CORE3_ARTIFACT_CONTRACT_VERSION
        ):
            raise RuntimeError(
                "CORE-3 artifact contract version must be integer "
                f"{CORE3_ARTIFACT_CONTRACT_VERSION}; actual="
                f"{contract_version!r}"
            )
        checkpoint_schema = checkpoint.get("schema")
        if checkpoint_schema != CORE3_CHECKPOINT_SCHEMA:
            raise RuntimeError(
                "CORE-3 checkpoint schema mismatch: expected "
                f"{CORE3_CHECKPOINT_SCHEMA!r}; actual={checkpoint_schema!r}"
            )
        checkpoint_version = checkpoint.get("schema_version")
        if (
            type(checkpoint_version) is not int
            or checkpoint_version != CORE3_CHECKPOINT_SCHEMA_VERSION
        ):
            raise RuntimeError(
                "CORE-3 checkpoint schema_version must be integer "
                f"{CORE3_CHECKPOINT_SCHEMA_VERSION}; actual="
                f"{checkpoint_version!r}"
            )
        checkpoint_core = checkpoint.get("core")
        if checkpoint_core != "CORE-3":
            raise RuntimeError(
                "CORE-3 checkpoint core mismatch: expected 'CORE-3'; "
                f"actual={checkpoint_core!r}"
            )
        checkpoint_config_hash = checkpoint.get("config_hash")
        if checkpoint_config_hash != self.config_identity:
            raise RuntimeError(
                "CORE-3 checkpoint configuration hash mismatch: expected "
                f"{self.config_identity!r}; actual={checkpoint_config_hash!r}"
            )

    def describe(self) -> Dict[str, Any]:
        description = ExecutionEngine(self.config).describe()
        generation_cell_count = len({
            cell.generation_id for cell in expand_cells(self.config)
        })
        unique_taskset_count = (
            generation_cell_count * self.config["grid"]["tasksets_per_cell"]
        )
        simulation_request_count = (
            unique_taskset_count
            if self.config["simulation"].get("reuse_across_e0", False)
            else description["cell_count"] * self.config["grid"]["tasksets_per_cell"]
        )
        result = {
            **description,
            "rta_request_count": description["request_count"],
            "simulation_request_count": simulation_request_count,
            "simulation": self.config["simulation"],
            "energy_mapping": {
                "rta_release_e0_values": self.config["energy"]["initial_energy_values"],
                "simulation_initial_battery": self.config["energy"]["simulation_initial_battery"],
                "battery_capacity": self.config["energy"]["battery_capacity"],
            },
            "energy_preflight": self.energy_preflight(),
        }
        if self.config["simulation"].get("reuse_across_e0", False):
            result["unique_taskset_count"] = unique_taskset_count
            result["total_terminal_count"] = (
                description["request_count"] + simulation_request_count
            )
        return result

    def _simulation_plan(self) -> list[Dict[str, Any]]:
        cells = {row["cell_id"]: row for row in read_csv(self.root / "cells.csv")}
        generated = {
            row["taskset_id"]: row
            for row in read_csv(self.root / "generated_tasksets.csv")
        }
        rta_rows = read_csv(self.root / "per_taskset_results.csv")
        reuse = self.config["simulation"].get("reuse_across_e0", False)
        unique: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in rta_rows:
            cell = cells[row["cell_id"]]
            key = (
                cell["generation_id"] if reuse else row["cell_id"],
                row["taskset_id"],
            )
            unique.setdefault(key, row)
        plan = []
        for (_scope_id, taskset_id), row in sorted(unique.items()):
            cell_id = row["cell_id"]
            cell = cells[cell_id]
            generated_row = generated[taskset_id]
            exact_e0 = Fraction(cell["exact_e0"])
            if reuse:
                simulation_id_value = shared_e0_simulation_identity(
                    cell["generation_id"], row["taskset_hash"],
                    self.config["simulation"],
                )
            else:
                simulation_id_value = simulation_identity(
                    cell_id, row["taskset_hash"], exact_e0,
                    self.config["simulation"],
                )
            canonical = Path(generated_row["canonical_taskset_json"])
            document = json.loads(canonical.read_text(encoding="utf-8"))
            if document.get("taskset_hash") != row["taskset_hash"]:
                raise RuntimeError("canonical/RTA taskset hash mismatch")
            comparisons = [
                {
                    "cell_id": candidate["cell_id"],
                    "cell": cells[candidate["cell_id"]],
                    "exact_e0": Fraction(cells[candidate["cell_id"]]["exact_e0"]),
                    "rta_rows": [
                        item for item in rta_rows
                        if item["cell_id"] == candidate["cell_id"]
                        and item["taskset_id"] == taskset_id
                    ],
                }
                for candidate in rta_rows
                if candidate["taskset_id"] == taskset_id
                and (
                    not reuse or cells[candidate["cell_id"]]["generation_id"]
                    == cell["generation_id"]
                )
            ]
            deduped_comparisons = {
                item["cell_id"]: item for item in comparisons
            }
            context = {
                "simulation_id": simulation_id_value,
                "cell_id": cell_id,
                "cell": cell,
                "taskset_id": taskset_id,
                "taskset_hash": row["taskset_hash"],
                "exact_e0": exact_e0,
                "generated": generated_row,
                "canonical": canonical,
                "document": document,
                "rta_rows": [
                    rta_row
                    for comparison in deduped_comparisons.values()
                    for rta_row in comparison["rta_rows"]
                ],
                "comparisons": list(deduped_comparisons.values()),
            }
            self._simulation_context[simulation_id_value] = context
            plan.append(context)
        return plan

    def _checkpoint(
        self,
        rta_outcome: RunOutcome,
        plan: Sequence[Mapping[str, Any]],
    ) -> None:
        completed_simulations = sorted(
            path.stem for path in self.simulation_terminals.glob("*.json")
        )
        completed_rta = sorted(
            path.stem for path in (self.root / "terminal_results").glob("*.json")
        )
        atomic_write_json(self.root / "checkpoint.json", {
            "core3_artifact_contract_version": CORE3_ARTIFACT_CONTRACT_VERSION,
            "schema": CORE3_CHECKPOINT_SCHEMA,
            "schema_version": CORE3_CHECKPOINT_SCHEMA_VERSION,
            "core": "CORE-3",
            "config_hash": self.config_identity,
            "completed_analysis_ids": completed_rta,
            "completed_rta_count": len(completed_rta),
            "requested_rta_count": rta_outcome.requested,
            "completed_simulation_ids": completed_simulations,
            "completed_simulation_count": len(completed_simulations),
            "requested_simulation_count": len(plan),
            "stop_requested": self.stop_requested or rta_outcome.stopped,
            "updated_at_utc": _utc_now(),
        })

    def _record_failure(
        self,
        context: Mapping[str, Any],
        execution: SimulationExecution,
        *,
        severity: str,
        code: str,
        detail: str,
    ) -> Path:
        reproduction = self.root / "failure_inputs" / (
            "core3_" + str(context["simulation_id"])
        )
        reproduction.mkdir(parents=True, exist_ok=True)
        for source, name in (
            (context.get("canonical", Path("")), "canonical_taskset.json"),
            (execution.system_config_path, "system_config.yaml"),
            (execution.taskset_path, "taskset.yaml"),
            (self.root / "run_config.yaml", "run_config.yaml"),
        ):
            if Path(source).is_file():
                shutil.copy2(source, reproduction / name)
        if execution.retained_trace_path and execution.retained_trace_path.is_file():
            shutil.copy2(execution.retained_trace_path, reproduction / "simulation_trace.json")
        taskset_rows = context.get("rta_rows") or [
            row for row in read_csv(self.root / "per_taskset_results.csv")
            if row.get("cell_id") == str(context["cell_id"])
            and row.get("taskset_id") == str(context["taskset_id"])
        ]
        task_rows = read_csv(self.root / "per_task_results.csv")
        atomic_write_json(reproduction / "rta_taskset_results.json", taskset_rows)
        atomic_write_json(reproduction / "rta_task_results.json", [
            row for row in task_rows
            if row["cell_id"] == context["cell_id"]
            and row["taskset_id"] == context["taskset_id"]
        ])
        existing = read_csv(self.root / "failures.csv")
        failure_key = (str(context["simulation_id"]), code)
        if not any(
            (row.get("analysis_id"), row.get("code")) == failure_key
            for row in existing
        ):
            append_csv_row(self.root / "failures.csv", FAILURE_COLUMNS, {
                "severity": severity,
                "stage": "SIMULATION",
                "analysis_id": context["simulation_id"],
                "cell_id": context["cell_id"],
                "taskset_id": context["taskset_id"],
                "variant": "ASAP_BLOCK_SIMULATION",
                "code": code,
                "detail": detail,
                "traceback": execution.stderr_tail,
                "failure_input": str(reproduction),
            })
        return reproduction

    def _response_bound_violations(
        self,
        context: Mapping[str, Any],
        execution: SimulationExecution,
        rta_task_rows: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        tasks = {task.task_id: task for task in execution.result.tasks}
        no_overflow_guard = execution.attempt_count > 0
        violations = []
        for rta_task in rta_task_rows:
            if (
                str(rta_task.get("cell_id")) != str(context["cell_id"])
                or str(rta_task.get("taskset_id")) != str(context["taskset_id"])
            ):
                continue
            task = tasks.get(str(rta_task.get("task_id")))
            exact_e0 = Fraction(
                context.get("exact_e0", rta_task.get("exact_e0", "0"))
            )
            eligibility = _response_bound_eligibility(
                execution, task, rta_task, exact_e0
            )
            witness = response_bound_violation_row(
                rta_task,
                {
                    **(task.row() if task is not None else {}),
                    "simulation_status": execution.result.status.value,
                },
                eligible=eligibility.eligible,
            )
            if witness is None:
                continue
            violations.append({
                "code": "RTA_RESPONSE_BOUND_VIOLATION",
                **witness,
                "simulation_id": execution.simulation_id,
                "taskset_hash": context["taskset_hash"],
                "release_e0_valid": _release_e0_valid(
                    execution, exact_e0
                ),
                "no_overflow_guard": no_overflow_guard,
                "comparison_eligible": eligibility.eligible,
                "simulation_taskset_results_path": str(
                    self.root / "simulation_taskset_results.csv"
                ),
                "simulation_task_results_path": str(
                    self.root / "simulation_task_results.csv"
                ),
                "simulation_job_results_path": str(
                    self.root / "simulation_job_results.csv"
                ),
                "retained_trace_path": str(execution.retained_trace_path or ""),
                "system_config_path": str(execution.system_config_path),
                "taskset_path": str(execution.taskset_path),
                "observation_trace_path": "",
                "failure_input": "",
            })
        return violations

    def _record_response_bound_failure(
        self,
        context: Mapping[str, Any],
        execution: SimulationExecution,
        violations: Sequence[Mapping[str, Any]],
    ) -> Path:
        reproduction = self._record_failure(
            context,
            execution,
            severity="P0",
            code="RTA_RESPONSE_BOUND_VIOLATION",
            detail=(
                f"{len(violations)} task/analysis response bound(s) were exceeded "
                "by observed maximum response time"
            ),
        )
        violating_tasks = {str(row["task_id"]) for row in violations}
        observation_trace_path = reproduction / "response_bound_job_trace.json"
        atomic_write_json(observation_trace_path, {
            "simulation_id": execution.simulation_id,
            "simulation_status": execution.result.status.value,
            "horizon": execution.result.horizon,
            "jobs": [
                job.row() for job in execution.result.jobs
                if job.task_id in violating_tasks
            ],
        })
        materialized = []
        for violation in violations:
            row = {
                **violation,
                "observation_trace_path": str(observation_trace_path),
                "failure_input": str(reproduction),
            }
            materialized.append(row)
            if isinstance(violation, dict):
                violation["observation_trace_path"] = str(
                    observation_trace_path
                )
                violation["failure_input"] = str(reproduction)
        atomic_write_json(
            reproduction / "response_bound_violations.json",
            materialized,
        )
        return reproduction

    def _synchronize_soundness_failures(
        self,
        plan: Sequence[Mapping[str, Any]],
        executions: Mapping[str, SimulationExecution],
        response_bound_violations: Sequence[Dict[str, Any]],
    ) -> None:
        """Replace derived CORE-3 soundness failures from authoritative terminals."""

        scoped_simulations = set(executions)
        derived_codes = {
            "RTA_PASS_SIM_FAIL",
            "RTA_RESPONSE_BOUND_VIOLATION",
        }
        preserved = [
            row for row in read_csv(self.root / "failures.csv")
            if not (
                row.get("analysis_id") in scoped_simulations
                and row.get("code") in derived_codes
            )
        ]
        write_csv(self.root / "failures.csv", FAILURE_COLUMNS, preserved)

        by_simulation: Dict[str, list[Dict[str, Any]]] = {}
        for row in response_bound_violations:
            by_simulation.setdefault(str(row["simulation_id"]), []).append(row)
        for context in plan:
            simulation_id_value = str(context["simulation_id"])
            execution = executions.get(simulation_id_value)
            if execution is None:
                continue
            rta_rows = context.get("rta_rows") or [
                row for row in read_csv(self.root / "per_taskset_results.csv")
                if row.get("cell_id") == str(context["cell_id"])
                and row.get("taskset_id") == str(context["taskset_id"])
            ]
            comparisons = context.get("comparisons") or [{
                "cell_id": context["cell_id"],
                "cell": context["cell"],
                "exact_e0": context["exact_e0"],
                "rta_rows": rta_rows,
            }]
            deadline_comparisons = [
                comparison for comparison in comparisons
                if deadline_soundness_violation(
                    comparison["rta_rows"], execution,
                    Fraction(comparison["exact_e0"]),
                )
            ]
            if deadline_comparisons:
                failure_context = {**context, **deadline_comparisons[0]}
                self._record_failure(
                    failure_context,
                    execution,
                    severity="P0",
                    code="RTA_PASS_SIM_FAIL",
                    detail=(
                        "RTA-certified taskset produced a simulation deadline miss "
                        f"at E0={fraction_text(Fraction(failure_context['exact_e0']))}"
                    ),
                )
            violations = by_simulation.get(simulation_id_value, [])
            if violations:
                violation_cell = violations[0]["cell_id"]
                failure_comparison = next(
                    comparison for comparison in comparisons
                    if comparison["cell_id"] == violation_cell
                )
                reproduction = self._record_response_bound_failure(
                    {**context, **failure_comparison}, execution, violations
                )
                for violation in violations:
                    violation["failure_input"] = str(reproduction)

    def _run_simulations(
        self,
        rta_outcome: RunOutcome,
        plan: Sequence[Mapping[str, Any]],
        *,
        resume: bool,
    ) -> Dict[str, SimulationExecution]:
        self.simulation_terminals.mkdir(parents=True, exist_ok=True)
        results: Dict[str, SimulationExecution] = {}
        rta_task_rows = read_csv(self.root / "per_task_results.csv")
        for index, context in enumerate(plan, start=1):
            simulation_id_value = str(context["simulation_id"])
            terminal = self.simulation_terminals / f"{simulation_id_value}.json"
            if terminal.is_file():
                if not resume:
                    raise RuntimeError("simulation result exists; use resume")
                execution = load_simulation_terminal(terminal)
            else:
                try:
                    execution = run_paired_simulation(
                        simulation_id_value=simulation_id_value,
                        base_system_path=(
                            Path(__file__).resolve().parents[2]
                            / self.config["energy"]["service_curve"]["system_template"]
                        ),
                        run_root=self.root,
                        task_payload=context["document"]["tasks"],
                        taskset_hash=context["taskset_hash"],
                        processors=int(context["cell"]["M"]),
                        exact_e0=context["exact_e0"],
                        energy_config=self.config["energy"],
                        simulation_config=self.config["simulation"],
                    )
                except SimulationConfigurationError as exc:
                    execution = _failed_execution(
                        simulation_id_value,
                        int(self.config["simulation"]["horizon"]),
                        f"simulation_configuration_error:{exc}",
                    )
                write_simulation_terminal(terminal, execution)
            results[simulation_id_value] = execution

            if execution.result.status is SimulationStatus.INTERNAL_ERROR:
                self._record_failure(
                    context, execution, severity="P1", code="SIM_INTERNAL_ERROR",
                    detail=execution.result.reason,
                )
            elif execution.result.status is SimulationStatus.RUNTIME_TIMEOUT:
                self._record_failure(
                    context, execution, severity="P2", code="SIM_RUNTIME_TIMEOUT",
                    detail=execution.result.reason,
                )
            else:
                invalid_comparisons = [
                    comparison for comparison in context.get("comparisons", [context])
                    if not _release_e0_valid(
                        execution, Fraction(comparison["exact_e0"])
                    )
                ]
                if invalid_comparisons:
                    invalid_context = {**context, **invalid_comparisons[0]}
                    self._record_failure(
                        invalid_context, execution, severity="P1",
                        code="RTA_E0_RELEASE_CERTIFICATE_NOT_OBSERVED",
                        detail=(
                            "simulation does not empirically satisfy the configured "
                            "per-release E0 premise at E0="
                            f"{fraction_text(Fraction(invalid_context['exact_e0']))}"
                        ),
                    )

            comparisons = context.get("comparisons") or [context]
            deadline_comparisons = [
                comparison for comparison in comparisons
                if deadline_soundness_violation(
                    comparison["rta_rows"], execution,
                    Fraction(comparison["exact_e0"]),
                )
            ]
            deadline_p0 = bool(deadline_comparisons)
            response_bound_violations = []
            for comparison in comparisons:
                response_bound_violations.extend(
                    self._response_bound_violations(
                        {**context, **comparison}, execution, rta_task_rows
                    )
                )
            if deadline_p0:
                failure_context = {**context, **deadline_comparisons[0]}
                self._record_failure(
                    failure_context, execution, severity="P0",
                    code="RTA_PASS_SIM_FAIL",
                    detail=(
                        "RTA-certified taskset produced a simulation deadline miss "
                        f"at E0={fraction_text(Fraction(failure_context['exact_e0']))}"
                    ),
                )
            if response_bound_violations:
                violation_cell = response_bound_violations[0]["cell_id"]
                failure_comparison = next(
                    comparison for comparison in comparisons
                    if comparison["cell_id"] == violation_cell
                )
                reproduction = self._record_response_bound_failure(
                    {**context, **failure_comparison}, execution,
                    response_bound_violations,
                )
                for violation in response_bound_violations:
                    violation["failure_input"] = str(reproduction)
            p0 = deadline_p0 or bool(response_bound_violations)
            if p0:
                self.stop_requested = True
            if index % int(self.config["execution"]["checkpoint_every"]) == 0 or p0:
                self._checkpoint(rta_outcome, plan)
            if p0 and self.config["simulation"]["deadline_miss_fail_fast"]:
                break
        self._checkpoint(rta_outcome, plan)
        return results

    def _materialize(
        self,
        rta_outcome: RunOutcome,
        plan: Sequence[Mapping[str, Any]],
        executions: Mapping[str, SimulationExecution],
    ) -> Dict[str, Any]:
        shutil.copy2(self.root / "per_taskset_results.csv", self.root / "rta_results.csv")
        rta_task_rows = read_csv(self.root / "per_task_results.csv")
        rta_task_rows_by_key: Dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for row in rta_task_rows:
            key = (
                str(row["cell_id"]), str(row["taskset_id"]),
                str(row["task_id"]),
            )
            rta_task_rows_by_key.setdefault(key, []).append(row)
        simulation_tasksets = []
        simulation_tasks = []
        simulation_jobs = []
        for context in plan:
            simulation_id_value = str(context["simulation_id"])
            execution = executions.get(simulation_id_value)
            if execution is None:
                continue
            result = execution.result
            no_overflow_guard = execution.attempt_count > 0
            for comparison in context.get("comparisons", [context]):
                exact_e0 = Fraction(comparison["exact_e0"])
                comparison_eligible, comparison_ineligible_reason = (
                    _observation_comparison_eligibility(execution, exact_e0)
                )
                release_e0_valid = _release_e0_valid(execution, exact_e0)
                cell_id = comparison["cell_id"]
                simulation_tasksets.append({
                    "simulation_id": simulation_id_value,
                    "cell_id": cell_id,
                    "taskset_id": context["taskset_id"],
                    "taskset_hash": context["taskset_hash"],
                    "exact_e0": fraction_text(exact_e0),
                    "M": comparison["cell"]["M"],
                    "status": result.status.value,
                    "reason": result.reason,
                    "comparison_eligible": comparison_eligible,
                    "comparison_ineligible_reason": comparison_ineligible_reason,
                    "horizon": result.horizon,
                    "simulation_initial_battery": self.config["energy"]["simulation_initial_battery"],
                    "release_e0_valid": release_e0_valid,
                    "minimum_release_energy_j": result.minimum_release_energy_j,
                    "service_curve_reference": context["generated"]["service_curve_reference"],
                    "no_overflow_guard": no_overflow_guard,
                    "runtime_seconds": f"{execution.runtime_seconds:.9f}",
                    "attempt_count": execution.attempt_count,
                    "horizons_attempted": json.dumps(execution.horizons_attempted),
                    "observed_jobs": sum(task.observed_jobs for task in result.tasks),
                    "completed_jobs": sum(task.completed_jobs for task in result.tasks),
                    "missed_jobs": sum(task.missed_jobs for task in result.tasks),
                    "censored_jobs": sum(task.censored_jobs for task in result.tasks),
                    "retained_trace_path": execution.retained_trace_path,
                    "system_config_path": execution.system_config_path,
                    "taskset_path": execution.taskset_path,
                    "trace_schema_version": result.trace_schema_version,
                    "configured_scheduler": result.configured_scheduler,
                    "simulation_completed": result.simulation_completed,
                    "completion_reason": result.completion_reason,
                })
                for task in result.tasks:
                    task_rta_rows = rta_task_rows_by_key.get((
                        str(cell_id), str(context["taskset_id"]),
                        str(task.task_id),
                    ), [])
                    simulation_tasks.append({
                        "simulation_id": simulation_id_value,
                        "cell_id": cell_id,
                        "taskset_id": context["taskset_id"],
                        "taskset_hash": context["taskset_hash"],
                        "exact_e0": fraction_text(exact_e0),
                        **task.row(),
                        "tightness_eligible": (
                            comparison_eligible
                            and task_is_tightness_eligible(result, task)
                        ),
                        "response_bound_eligible": any(
                            _response_bound_eligibility(
                                execution, task, rta_task, exact_e0
                            ).eligible
                            for rta_task in task_rta_rows
                        ),
                        "censoring_label": censoring_label(task),
                    })
                for job in result.jobs:
                    simulation_jobs.append({
                        "simulation_id": simulation_id_value,
                        "cell_id": cell_id,
                        "taskset_id": context["taskset_id"],
                        "taskset_hash": context["taskset_hash"],
                        "exact_e0": fraction_text(exact_e0),
                        **job.row(),
                    })
        write_csv(
            self.root / "simulation_taskset_results.csv",
            SIMULATION_TASKSET_COLUMNS, simulation_tasksets,
        )
        write_csv(
            self.root / "simulation_task_results.csv",
            SIMULATION_TASK_COLUMNS, simulation_tasks,
        )
        write_csv(
            self.root / "simulation_job_results.csv",
            SIMULATION_JOB_COLUMNS, simulation_jobs,
        )

        rta_rows = read_csv(self.root / "per_taskset_results.csv")
        simulation_by_pair = {
            (row["cell_id"], row["taskset_id"]): row
            for row in simulation_tasksets
        }
        soundness = []
        for row in rta_rows:
            simulation = simulation_by_pair.get((row["cell_id"], row["taskset_id"]))
            if simulation is None:
                continue
            release_e0_valid = _required_artifact_bool(
                simulation, "release_e0_valid"
            )
            comparison_eligible = _required_artifact_bool(
                simulation, "comparison_eligible"
            )
            no_overflow_guard = _required_artifact_bool(
                simulation, "no_overflow_guard"
            )
            classification = classify_soundness(
                row, str(simulation["status"]),
                release_e0_valid=release_e0_valid,
                comparison_eligible=comparison_eligible,
                no_overflow_guard=no_overflow_guard,
            )
            soundness.append({
                "analysis_id": row["analysis_id"],
                "simulation_id": simulation["simulation_id"],
                "cell_id": row["cell_id"],
                "taskset_id": row["taskset_id"],
                "taskset_hash": row["taskset_hash"],
                "exact_e0": row["exact_e0"],
                "analysis_variant": row["analysis_variant"],
                "rta_solver_status": row["solver_status"],
                "rta_certification_status": row["certification_status"],
                "rta_taskset_proven": row["taskset_proven"],
                "simulation_status": simulation["status"],
                "release_e0_valid": simulation["release_e0_valid"],
                "no_overflow_guard": simulation["no_overflow_guard"],
                "comparison_eligible": simulation["comparison_eligible"],
                "comparison_ineligible_reason": simulation[
                    "comparison_ineligible_reason"
                ],
                "soundness_class": classification.value,
                "p0_violation_candidate": classification is SoundnessClass.RTA_PASS_SIM_FAIL,
            })
        write_csv(self.root / "soundness_matrix.csv", SOUNDNESS_COLUMNS, soundness)

        simulation_task_index = {
            (row["cell_id"], row["taskset_id"], row["task_id"]): row
            for row in simulation_tasks
        }
        certified_analysis_ids = {
            row["analysis_id"] for row in rta_rows
            if _rta_taskset_certified(row)
        }
        partial_candidate_rows_excluded = 0
        tightness = []
        for rta_task in rta_task_rows:
            if rta_task["analysis_id"] not in certified_analysis_ids:
                if (
                    rta_task.get("task_solver_status") == "CANDIDATE_FOUND"
                    and rta_task.get("candidate_response_time") not in (None, "")
                ):
                    partial_candidate_rows_excluded += 1
                continue
            simulation_task = simulation_task_index.get((
                rta_task["cell_id"], rta_task["taskset_id"], rta_task["task_id"]
            ))
            if simulation_task is None:
                continue
            row = tightness_row(rta_task, simulation_task)
            if row is not None:
                tightness.append(row)
        write_csv(self.root / "tightness_by_task.csv", TIGHTNESS_COLUMNS, tightness)
        tightness_tasksets, tightness_summary = aggregate_tightness(tightness)
        write_csv(
            self.root / "tightness_by_taskset.csv",
            TIGHTNESS_TASKSET_COLUMNS, tightness_tasksets,
        )

        response_bound_violations = []
        for context in plan:
            execution = executions.get(str(context["simulation_id"]))
            if execution is not None:
                response_bound_violations.extend(
                    self._response_bound_violations(
                        context, execution, rta_task_rows
                    )
                )
        self._synchronize_soundness_failures(
            plan, executions, response_bound_violations
        )
        write_csv(
            self.root / "response_bound_violations.csv",
            RESPONSE_BOUND_VIOLATION_COLUMNS,
            response_bound_violations,
        )

        censor_counts = Counter(
            ("TASKSET", row["status"], row["reason"]) for row in simulation_tasksets
        )
        censor_counts.update(
            ("TASK", row["censoring_label"], "") for row in simulation_tasks
        )
        censoring_rows = [
            {
                "scope": scope, "status": status, "reason": reason,
                "count": count,
                "denominator": (
                    len(simulation_tasksets) if scope == "TASKSET" else len(simulation_tasks)
                ),
            }
            for (scope, status, reason), count in sorted(censor_counts.items())
        ]
        write_csv(
            self.root / "censoring_summary.csv", CENSORING_COLUMNS, censoring_rows
        )

        runtime_rows = [
            {
                "kind": "RTA", "identifier": row["analysis_id"],
                "method": row["analysis_variant"], "status": row["solver_status"],
                "runtime_seconds": row["runtime_wall_seconds"],
                "attempt_count": row["attempt_count"],
            }
            for row in rta_rows
        ] + [
            {
                "kind": "SIMULATION", "identifier": row["simulation_id"],
                "method": "ASAP_BLOCK", "status": row["status"],
                "runtime_seconds": row["runtime_seconds"],
                "attempt_count": row["attempt_count"],
            }
            for row in simulation_tasksets
        ]
        write_csv(self.root / "runtime_summary.csv", RUNTIME_COLUMNS, runtime_rows)

        plot_rows = []
        for row in tightness:
            for plot, field in (
                ("absolute_gap", "absolute_gap"),
                ("normalized_gap", "normalized_gap"),
                ("ratio", "ratio"),
            ):
                plot_rows.append({
                    "plot": plot, "method": row["analysis_variant"],
                    "taskset_id": row["taskset_id"], "task_id": row["task_id"],
                    "category": "COMMON_TASK", "x": row[field], "y": 1,
                })
        for category, count in sorted(Counter(
            row["soundness_class"] for row in soundness
        ).items()):
            plot_rows.append({
                "plot": "soundness_matrix", "method": "ALL",
                "taskset_id": "", "task_id": "", "category": category,
                "x": category, "y": count,
            })
        for row in response_bound_violations:
            plot_rows.append({
                "plot": "response_bound_violation",
                "method": row["analysis_variant"],
                "taskset_id": row["taskset_id"],
                "task_id": row["task_id"],
                "category": row["code"],
                "x": row["candidate_response_time"],
                "y": row["r_sim_max"],
            })
        write_csv(self.root / "core3_plot_data.csv", PLOT_COLUMNS, plot_rows)

        soundness_counts = Counter(row["soundness_class"] for row in soundness)
        soundness_quadrants = {
            SoundnessClass.RTA_PASS_SIM_PASS.value,
            SoundnessClass.RTA_PASS_SIM_FAIL.value,
            SoundnessClass.RTA_FAIL_SIM_PASS.value,
            SoundnessClass.RTA_FAIL_SIM_FAIL.value,
        }
        raw_comparison_outcomes = {
            SimulationStatus.PASS_OBSERVED.value,
            SimulationStatus.DEADLINE_MISS.value,
        }
        deadline_counterexample_tasksets = {
            (row["cell_id"], row["taskset_id"], row["exact_e0"])
            for row in soundness
            if _truth(row["p0_violation_candidate"])
        }
        deadline_violation_comparisons = sum(
            _truth(row["p0_violation_candidate"]) for row in soundness
        )
        response_counterexample_tasks = {
            (
                row["cell_id"], row["taskset_id"], row["exact_e0"],
                row["task_id"],
            )
            for row in response_bound_violations
        }
        response_counterexample_tasksets = {
            (row["cell_id"], row["taskset_id"], row["exact_e0"])
            for row in response_bound_violations
        }
        unique_soundness_counterexample_tasksets = (
            deadline_counterexample_tasksets | response_counterexample_tasksets
        )
        release_e0_valid_count = sum(
            _truth(row["release_e0_valid"]) for row in soundness
        )
        no_overflow_guard_valid_count = sum(
            _truth(row["no_overflow_guard"]) for row in soundness
        )
        comparison_eligible_count = sum(
            _truth(row["comparison_eligible"]) for row in soundness
        )
        failures = read_csv(self.root / "failures.csv")
        severity = Counter(row["severity"] for row in failures)
        completed_rta = sum(row["solver_status"] == "COMPLETED" for row in rta_rows)
        summary = {
            "schema": "ASAP_BLOCK_V9_3_CORE3_SUMMARY_V2",
            "core3_artifact_contract_version": CORE3_ARTIFACT_CONTRACT_VERSION,
            "empirical_only": True,
            "rta_requested": rta_outcome.requested,
            "rta_terminal": len(rta_rows),
            "rta_completed": completed_rta,
            "rta_timeout": sum(row["solver_status"] == "TIMEOUT" for row in rta_rows),
            "simulation_requested": len(plan),
            "simulation_terminal": len(executions),
            "simulation_observed_pass": sum(
                execution.result.status is SimulationStatus.PASS_OBSERVED
                for execution in executions.values()
            ),
            "simulation_deadline_miss": sum(
                execution.result.status is SimulationStatus.DEADLINE_MISS
                for execution in executions.values()
            ),
            "simulation_horizon_insufficient": sum(
                execution.result.status is SimulationStatus.HORIZON_INSUFFICIENT
                for execution in executions.values()
            ),
            "simulation_runtime_timeout": sum(
                execution.result.status is SimulationStatus.RUNTIME_TIMEOUT
                for execution in executions.values()
            ),
            "simulation_internal_error": sum(
                execution.result.status is SimulationStatus.INTERNAL_ERROR
                for execution in executions.values()
            ),
            "simulation_release_e0_invalid": sum(
                not _truth(row["release_e0_valid"])
                for row in simulation_tasksets
            ),
            "simulation_no_overflow_guard_invalid": sum(
                not _truth(row["no_overflow_guard"])
                for row in simulation_tasksets
            ),
            "soundness_matrix_rows": len(soundness),
            "soundness_counts": dict(sorted(soundness_counts.items())),
            "soundness_raw_evaluable_denominator": sum(
                row["simulation_status"] in raw_comparison_outcomes
                for row in soundness
            ),
            "soundness_evaluable_denominator": sum(
                row["soundness_class"] in soundness_quadrants
                for row in soundness
            ),
            "assumption_e0_not_satisfied_count": soundness_counts[
                SoundnessClass.ASSUMPTION_E0_NOT_SATISFIED.value
            ],
            "release_e0_valid_count": release_e0_valid_count,
            "release_e0_invalid_count": len(soundness) - release_e0_valid_count,
            "no_overflow_guard_valid_count": no_overflow_guard_valid_count,
            "no_overflow_guard_invalid_count": (
                len(soundness) - no_overflow_guard_valid_count
            ),
            "comparison_eligible_count": comparison_eligible_count,
            "comparison_ineligible_count": (
                len(soundness) - comparison_eligible_count
            ),
            "certification_taskset_denominator": len(rta_rows),
            "certified_taskset_numerator": sum(
                _truth(row.get("taskset_proven")) for row in rta_rows
            ),
            "tightness_common_task_denominator": len(tightness),
            "tightness_denominator_scope": "jointly_certified_tasksets_only",
            "partial_candidate_rows_excluded_from_certified_tightness": (
                partial_candidate_rows_excluded
            ),
            "tightness": tightness_summary,
            "deadline_miss_soundness_violation_count": len(
                deadline_counterexample_tasksets
            ),
            "deadline_miss_soundness_violation_comparison_count": (
                deadline_violation_comparisons
            ),
            "response_bound_violation_task_count": len(
                response_counterexample_tasks
            ),
            "response_bound_violation_comparison_count": len(
                response_bound_violations
            ),
            "response_bound_violation_taskset_count": len(
                response_counterexample_tasksets
            ),
            "total_unique_soundness_counterexample_taskset_count": len(
                unique_soundness_counterexample_tasksets
            ),
            "p0": severity["P0"], "p1": severity["P1"], "p2": severity["P2"],
            "soundness_violation_candidate": bool(
                unique_soundness_counterexample_tasksets
            ),
            "stopped": self.stop_requested or rta_outcome.stopped,
        }
        atomic_write_json(self.root / "summary.json", summary)
        summary_rows = [
            {"metric": "rta_requested", "value": summary["rta_requested"], "denominator": "unconditional_rta_requests"},
            {"metric": "rta_completed", "value": summary["rta_completed"], "denominator": "terminal_rta_results"},
            {"metric": "simulation_requested", "value": summary["simulation_requested"], "denominator": "unconditional_simulation_requests"},
            {"metric": "simulation_observed_pass", "value": summary["simulation_observed_pass"], "denominator": "terminal_simulations"},
            {"metric": "soundness_evaluable", "value": summary["soundness_evaluable_denominator"], "denominator": "soundness_matrix_rows"},
            {"metric": "assumption_e0_not_satisfied", "value": summary["assumption_e0_not_satisfied_count"], "denominator": "soundness_matrix_rows"},
            {"metric": "no_overflow_guard_invalid", "value": summary["no_overflow_guard_invalid_count"], "denominator": "soundness_matrix_rows"},
            {"metric": "comparison_ineligible", "value": summary["comparison_ineligible_count"], "denominator": "soundness_matrix_rows"},
            {"metric": "deadline_miss_soundness_violations", "value": summary["deadline_miss_soundness_violation_count"], "denominator": "unique_tasksets"},
            {"metric": "deadline_miss_soundness_violation_comparisons", "value": summary["deadline_miss_soundness_violation_comparison_count"], "denominator": "eligible_rta_simulation_comparisons"},
            {"metric": "response_bound_violation_tasks", "value": summary["response_bound_violation_task_count"], "denominator": "eligible_candidate_tasks"},
            {"metric": "response_bound_violation_comparisons", "value": summary["response_bound_violation_comparison_count"], "denominator": "eligible_candidate_analysis_comparisons"},
            {"metric": "response_bound_violation_tasksets", "value": summary["response_bound_violation_taskset_count"], "denominator": "unique_tasksets"},
            {"metric": "total_unique_soundness_counterexample_tasksets", "value": summary["total_unique_soundness_counterexample_taskset_count"], "denominator": "unique_tasksets"},
            {"metric": "tightness_common_tasks", "value": summary["tightness_common_task_denominator"], "denominator": "legal_candidate_and_observed_tasks"},
            {"metric": "p0", "value": summary["p0"], "denominator": "recorded_failures"},
            {"metric": "p1", "value": summary["p1"], "denominator": "recorded_failures"},
            {"metric": "p2", "value": summary["p2"], "denominator": "recorded_failures"},
        ]
        write_csv(self.root / "summary.csv", SUMMARY_COLUMNS, summary_rows)
        write_file_hashes(self.root)
        return summary

    def run(self, *, resume: bool = False) -> Core3Outcome:
        self.require_energy_preflight()
        self._validate_existing_core3_contract()
        rta_outcome = ExecutionEngine(self.config).run(resume=resume)
        plan = self._simulation_plan()
        executions: Dict[str, SimulationExecution] = {}
        if not rta_outcome.stopped:
            # Persist the new comparison contract before the first simulation
            # terminal, so an interrupted run can only resume under v2 rules.
            self._checkpoint(rta_outcome, plan)
            executions = self._run_simulations(
                rta_outcome, plan, resume=resume,
            )
        else:
            self.stop_requested = True
            for context in plan:
                simulation_id_value = str(context["simulation_id"])
                terminal = self.simulation_terminals / f"{simulation_id_value}.json"
                if terminal.is_file():
                    executions[simulation_id_value] = load_simulation_terminal(
                        terminal
                    )
            self._checkpoint(rta_outcome, plan)
        summary = self._materialize(rta_outcome, plan, executions)
        return Core3Outcome(
            self.root, rta_outcome.requested, rta_outcome.terminal,
            len(plan), len(executions), self.stop_requested or rta_outcome.stopped,
            summary,
        )


def analyze_core3(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Rebuild CORE-3 summaries from terminal RTA/simulation records only."""

    runner = Core3PairingRunner(config)
    runner._validate_existing_core3_contract()
    plan = runner._simulation_plan()
    executions = {}
    for context in plan:
        simulation_id_value = str(context["simulation_id"])
        terminal = runner.simulation_terminals / f"{simulation_id_value}.json"
        if terminal.is_file():
            executions[simulation_id_value] = load_simulation_terminal(terminal)
    checkpoint_path = runner.root / "checkpoint.json"
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.is_file() else {}
    )
    rta_rows = read_csv(runner.root / "per_taskset_results.csv")
    outcome = RunOutcome(
        runner.root,
        int(checkpoint.get("requested_rta_count", len(rta_rows))),
        len(rta_rows),
        dict(Counter(row["solver_status"] for row in rta_rows)),
        bool(checkpoint.get("stop_requested", False)),
    )
    return runner._materialize(outcome, plan, executions)
