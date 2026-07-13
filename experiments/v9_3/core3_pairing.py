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
from .config import config_hash, fraction_text
from .core3_aggregation import (
    SoundnessClass,
    aggregate_tightness,
    classify_soundness,
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
    load_simulation_terminal,
    run_paired_simulation,
    simulation_identity,
    write_simulation_terminal,
)
from .simulation_result import SimulationResult, SimulationStatus


SIMULATION_TASKSET_COLUMNS = (
    "simulation_id", "cell_id", "taskset_id", "taskset_hash", "exact_e0",
    "M", "status", "reason", "comparison_eligible", "horizon",
    "simulation_initial_battery", "release_e0_valid",
    "minimum_release_energy_j", "service_curve_reference",
    "no_overflow_guard", "runtime_seconds", "attempt_count",
    "horizons_attempted", "observed_jobs", "completed_jobs", "missed_jobs",
    "censored_jobs", "retained_trace_path", "system_config_path",
    "taskset_path",
)
SIMULATION_TASK_COLUMNS = (
    "simulation_id", "cell_id", "taskset_id", "taskset_hash", "exact_e0",
    "task_id", "observed_jobs", "completed_jobs", "missed_jobs",
    "censored_jobs", "r_sim_max", "horizon_coverage",
    "minimum_jobs_satisfied", "tightness_eligible", "censoring_label",
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
    "release_e0_valid", "comparison_eligible", "soundness_class",
    "p0_violation_candidate",
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

    def describe(self) -> Dict[str, Any]:
        description = ExecutionEngine(self.config).describe()
        return {
            **description,
            "rta_request_count": description["request_count"],
            "simulation_request_count": (
                description["cell_count"] * self.config["grid"]["tasksets_per_cell"]
            ),
            "simulation": self.config["simulation"],
            "energy_mapping": {
                "rta_release_e0_values": self.config["energy"]["initial_energy_values"],
                "simulation_initial_battery": self.config["energy"]["simulation_initial_battery"],
                "battery_capacity": self.config["energy"]["battery_capacity"],
            },
        }

    def _simulation_plan(self) -> list[Dict[str, Any]]:
        cells = {row["cell_id"]: row for row in read_csv(self.root / "cells.csv")}
        generated = {
            row["taskset_id"]: row
            for row in read_csv(self.root / "generated_tasksets.csv")
        }
        rta_rows = read_csv(self.root / "per_taskset_results.csv")
        unique: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in rta_rows:
            key = (row["cell_id"], row["taskset_id"])
            unique.setdefault(key, row)
        plan = []
        for (cell_id, taskset_id), row in sorted(unique.items()):
            cell = cells[cell_id]
            generated_row = generated[taskset_id]
            exact_e0 = Fraction(cell["exact_e0"])
            simulation_id_value = simulation_identity(
                cell_id, row["taskset_hash"], exact_e0,
                self.config["simulation"],
            )
            canonical = Path(generated_row["canonical_taskset_json"])
            document = json.loads(canonical.read_text(encoding="utf-8"))
            if document.get("taskset_hash") != row["taskset_hash"]:
                raise RuntimeError("canonical/RTA taskset hash mismatch")
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
                    item for item in rta_rows
                    if item["cell_id"] == cell_id and item["taskset_id"] == taskset_id
                ],
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
            (context["canonical"], "canonical_taskset.json"),
            (execution.system_config_path, "system_config.yaml"),
            (execution.taskset_path, "taskset.yaml"),
            (self.root / "run_config.yaml", "run_config.yaml"),
        ):
            if Path(source).is_file():
                shutil.copy2(source, reproduction / name)
        if execution.retained_trace_path and execution.retained_trace_path.is_file():
            shutil.copy2(execution.retained_trace_path, reproduction / "simulation_trace.json")
        task_rows = read_csv(self.root / "per_task_results.csv")
        atomic_write_json(reproduction / "rta_taskset_results.json", context["rta_rows"])
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

    def _run_simulations(
        self,
        rta_outcome: RunOutcome,
        plan: Sequence[Mapping[str, Any]],
        *,
        resume: bool,
    ) -> Dict[str, SimulationExecution]:
        self.simulation_terminals.mkdir(parents=True, exist_ok=True)
        results: Dict[str, SimulationExecution] = {}
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
            elif not execution.result.release_e0_valid:
                self._record_failure(
                    context, execution, severity="P1",
                    code="RTA_E0_RELEASE_CERTIFICATE_NOT_OBSERVED",
                    detail=(
                        "simulation does not empirically satisfy the configured "
                        "per-release E0 premise"
                    ),
                )

            p0 = bool(
                execution.result.status is SimulationStatus.DEADLINE_MISS
                and execution.result.release_e0_valid
                and any(_truth(row["taskset_proven"]) for row in context["rta_rows"])
            )
            if p0:
                self._record_failure(
                    context, execution, severity="P0",
                    code="RTA_PASS_SIM_FAIL",
                    detail="RTA-certified taskset produced a simulation deadline miss",
                )
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
        simulation_tasksets = []
        simulation_tasks = []
        simulation_jobs = []
        for context in plan:
            simulation_id_value = str(context["simulation_id"])
            execution = executions.get(simulation_id_value)
            if execution is None:
                continue
            result = execution.result
            simulation_tasksets.append({
                "simulation_id": simulation_id_value,
                "cell_id": context["cell_id"],
                "taskset_id": context["taskset_id"],
                "taskset_hash": context["taskset_hash"],
                "exact_e0": fraction_text(context["exact_e0"]),
                "M": context["cell"]["M"],
                "status": result.status.value,
                "reason": result.reason,
                "comparison_eligible": result.comparison_eligible,
                "horizon": result.horizon,
                "simulation_initial_battery": self.config["energy"]["simulation_initial_battery"],
                "release_e0_valid": result.release_e0_valid,
                "minimum_release_energy_j": result.minimum_release_energy_j,
                "service_curve_reference": context["generated"]["service_curve_reference"],
                "no_overflow_guard": execution.attempt_count > 0,
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
            })
            for task in result.tasks:
                simulation_tasks.append({
                    "simulation_id": simulation_id_value,
                    "cell_id": context["cell_id"],
                    "taskset_id": context["taskset_id"],
                    "taskset_hash": context["taskset_hash"],
                    "exact_e0": fraction_text(context["exact_e0"]),
                    **task.row(),
                    "tightness_eligible": task_is_tightness_eligible(result, task),
                    "censoring_label": censoring_label(task),
                })
            for job in result.jobs:
                simulation_jobs.append({
                    "simulation_id": simulation_id_value,
                    "cell_id": context["cell_id"],
                    "taskset_id": context["taskset_id"],
                    "taskset_hash": context["taskset_hash"],
                    "exact_e0": fraction_text(context["exact_e0"]),
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
            classification = classify_soundness(
                row, str(simulation["status"]),
                release_e0_valid=_truth(simulation["release_e0_valid"]),
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
                "comparison_eligible": simulation["comparison_eligible"],
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
            if _truth(row.get("taskset_proven"))
            and row.get("certification_status") == "CERTIFIED_TASKSET"
        }
        partial_candidate_rows_excluded = 0
        tightness = []
        for rta_task in read_csv(self.root / "per_task_results.csv"):
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
        write_csv(self.root / "core3_plot_data.csv", PLOT_COLUMNS, plot_rows)

        soundness_counts = Counter(row["soundness_class"] for row in soundness)
        failures = read_csv(self.root / "failures.csv")
        severity = Counter(row["severity"] for row in failures)
        completed_rta = sum(row["solver_status"] == "COMPLETED" for row in rta_rows)
        summary = {
            "schema": "ASAP_BLOCK_V9_3_CORE3_SUMMARY_V1",
            "empirical_only": True,
            "rta_requested": rta_outcome.requested,
            "rta_terminal": len(rta_rows),
            "rta_completed": completed_rta,
            "rta_timeout": sum(row["solver_status"] == "TIMEOUT" for row in rta_rows),
            "simulation_requested": len(plan),
            "simulation_terminal": len(simulation_tasksets),
            "simulation_observed_pass": sum(
                row["status"] == SimulationStatus.PASS_OBSERVED.value
                for row in simulation_tasksets
            ),
            "simulation_horizon_insufficient": sum(
                row["status"] == SimulationStatus.HORIZON_INSUFFICIENT.value
                for row in simulation_tasksets
            ),
            "soundness_matrix_rows": len(soundness),
            "soundness_counts": dict(sorted(soundness_counts.items())),
            "soundness_evaluable_denominator": sum(
                row["soundness_class"] in {
                    SoundnessClass.RTA_PASS_SIM_PASS.value,
                    SoundnessClass.RTA_PASS_SIM_FAIL.value,
                    SoundnessClass.RTA_FAIL_SIM_PASS.value,
                    SoundnessClass.RTA_FAIL_SIM_FAIL.value,
                }
                for row in soundness
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
            "p0": severity["P0"], "p1": severity["P1"], "p2": severity["P2"],
            "soundness_violation_candidate": bool(severity["P0"]),
            "stopped": self.stop_requested or rta_outcome.stopped,
        }
        atomic_write_json(self.root / "summary.json", summary)
        summary_rows = [
            {"metric": "rta_requested", "value": summary["rta_requested"], "denominator": "unconditional_rta_requests"},
            {"metric": "rta_completed", "value": summary["rta_completed"], "denominator": "terminal_rta_results"},
            {"metric": "simulation_requested", "value": summary["simulation_requested"], "denominator": "unconditional_simulation_requests"},
            {"metric": "simulation_observed_pass", "value": summary["simulation_observed_pass"], "denominator": "terminal_simulations"},
            {"metric": "soundness_evaluable", "value": summary["soundness_evaluable_denominator"], "denominator": "soundness_matrix_rows"},
            {"metric": "tightness_common_tasks", "value": summary["tightness_common_task_denominator"], "denominator": "legal_candidate_and_observed_tasks"},
            {"metric": "p0", "value": summary["p0"], "denominator": "recorded_failures"},
            {"metric": "p1", "value": summary["p1"], "denominator": "recorded_failures"},
            {"metric": "p2", "value": summary["p2"], "denominator": "recorded_failures"},
        ]
        write_csv(self.root / "summary.csv", SUMMARY_COLUMNS, summary_rows)
        write_file_hashes(self.root)
        return summary

    def run(self, *, resume: bool = False) -> Core3Outcome:
        rta_outcome = ExecutionEngine(self.config).run(resume=resume)
        plan = self._simulation_plan()
        executions: Dict[str, SimulationExecution] = {}
        if not rta_outcome.stopped:
            executions = self._run_simulations(
                rta_outcome, plan, resume=resume,
            )
        else:
            self.stop_requested = True
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
