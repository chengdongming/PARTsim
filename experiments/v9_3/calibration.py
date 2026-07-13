"""Read-only constrained-deadline calibration diagnostics and summaries.

Production outcomes always come from :mod:`execution_engine`.  This module
only replays the audited v9.3 core with its trace observer to expose envelope
and closure access points; it never assigns or changes certification.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import pickle
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner

from .config import fraction_text
from .result_writer import read_csv


class CalibrationError(RuntimeError):
    """Fail-closed calibration diagnostic error."""


@dataclass(frozen=True)
class DiagnosticTaskset:
    taskset_id: str
    semantic_hash: str
    processors: int
    tasks: Tuple[core.V93Task, ...]
    payload: Tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class CalibrationInstance:
    phase: str
    cell_id: str
    utilization: str
    exact_e0: str
    taskset: DiagnosticTaskset
    results: Mapping[taskset.AnalysisVariant, taskset.TasksetAnalysisResult]


def _load_taskset(path: Path) -> DiagnosticTaskset:
    document = json.loads(path.read_text(encoding="utf-8"))
    payload = tuple(document["tasks"])
    tasks = tuple(core.V93Task(
        str(item["task_id"]), int(item["C"]), int(item["D"]),
        int(item["T"]), Fraction(str(item["P"])),
    ) for item in payload)
    return DiagnosticTaskset(
        str(document["taskset_id"]), str(document["taskset_hash"]),
        int(document["generation_parameters"]["M"]), tasks, payload,
    )


def load_instances(run_root: Path, phase: str) -> list[CalibrationInstance]:
    cells = {row["cell_id"]: row for row in read_csv(run_root / "cells.csv")}
    generated = {
        row["taskset_id"]: _load_taskset(Path(row["canonical_taskset_json"]))
        for row in read_csv(run_root / "generated_tasksets.csv")
    }
    grouped: Dict[tuple[str, str], Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult]] = defaultdict(dict)
    for row in read_csv(run_root / "per_taskset_results.csv"):
        state_path = run_root / "result_state" / f"{row['analysis_id']}.pickle"
        if not state_path.is_file():
            raise CalibrationError(f"missing production analyzer state: {state_path}")
        with state_path.open("rb") as handle:
            result = pickle.load(handle)
        if not isinstance(result, taskset.TasksetAnalysisResult):
            raise CalibrationError("invalid analyzer state type")
        variant = taskset.AnalysisVariant[row["analysis_variant"]]
        key = (row["cell_id"], row["taskset_id"])
        if variant in grouped[key]:
            raise CalibrationError("duplicate analysis variant in calibration instance")
        grouped[key][variant] = result
    expected = set(production_runner.VARIANT_ORDER)
    instances = []
    for (cell_id, taskset_id_value), results in sorted(grouped.items()):
        if set(results) != expected:
            raise CalibrationError("calibration instance does not contain five variants")
        cell = cells[cell_id]
        instances.append(CalibrationInstance(
            phase, cell_id, cell["utilization"], cell["exact_e0"],
            generated[taskset_id_value], dict(results),
        ))
    return instances


def _record_evaluated(record: taskset.TaskAnalysisRecord) -> bool:
    return record.solver_status not in {
        taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
        taskset.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
    }


def _trace_search(
    kind: core.EnvelopeKind,
    instance: CalibrationInstance,
    rank: int,
    carry: Mapping[str, int],
    beta: Sequence[Fraction],
    timeout_seconds: float,
    envelope_cache: Dict[tuple, Fraction],
) -> tuple[core.V93SearchResult, Dict[tuple[int, int, int], Mapping[str, Any]], set[tuple[int, int, int]]]:
    trace: Dict[tuple[int, int, int], Mapping[str, Any]] = {}
    closures: set[tuple[int, int, int]] = set()

    def observer(event: Mapping[str, Any]) -> None:
        if event.get("event_type") != "Q_CHECK":
            return
        key = (int(event["w"]), int(event["h"]), int(event["q"]))
        trace[key] = dict(event)
        if event.get("h_result") == "CLOSED":
            closures.add(key)

    tasks = instance.taskset.tasks
    carry_key = tuple(sorted((str(key), int(value)) for key, value in carry.items()))

    def cached_envelope(**kwargs: Any) -> Fraction:
        key = (
            instance.taskset.semantic_hash, kwargs["kind"].value, rank,
            carry_key, int(kwargs["w"]), int(kwargs["h"]), int(kwargs["q"]),
        )
        if key not in envelope_cache:
            envelope_cache[key] = core.exact_energy_envelope_v9_3(**kwargs)
        return envelope_cache[key]

    result = core.canonical_closure_search_v9_3(
        kind, tasks[rank], tasks[:rank], tasks[rank + 1:],
        instance.taskset.processors, carry, Fraction(instance.exact_e0), beta,
        timeout_seconds=timeout_seconds, envelope_function=cached_envelope,
        trace_observer=observer,
    )
    return result, trace, closures


def _response_relation(
    complete_candidate: Optional[int], local_candidate: Optional[int]
) -> tuple[str, Optional[int]]:
    if complete_candidate is not None and local_candidate is not None:
        reduction = complete_candidate - local_candidate
        if reduction > 0:
            return "TIGHTER", reduction
        if reduction == 0:
            return "EQUAL", 0
        return "VIOLATION", reduction
    if complete_candidate is None and local_candidate is not None:
        return "LOCAL_ONLY_CANDIDATE", None
    if complete_candidate is not None:
        return "VIOLATION", None
    return "NO_COMMON_CANDIDATE", None


RELATIONS = (
    ("DEADLINE_CARRY_IN", taskset.AnalysisVariant.CW_D, taskset.AnalysisVariant.LOC_D, True),
    ("FIXED_CW_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_CW, True),
    ("RECURSIVE_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_LOC, False),
)


def diagnose_instance(
    instance: CalibrationInstance,
    beta: Sequence[Fraction],
    *,
    timeout_seconds: float = 60,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    """Return response, envelope-access, and local-only-closure rows."""

    response_rows: list[Dict[str, Any]] = []
    access_rows: list[Dict[str, Any]] = []
    closure_rows: list[Dict[str, Any]] = []
    envelope_cache: Dict[tuple, Fraction] = {}
    for relation, complete_variant, local_variant, trace_envelopes in RELATIONS:
        complete = instance.results[complete_variant]
        local = instance.results[local_variant]
        if relation == "DEADLINE_CARRY_IN":
            carry: Optional[Mapping[str, int]] = {
                task.name: task.deadline for task in instance.taskset.tasks
            }
        elif relation == "FIXED_CW_CARRY_IN" and production_runner._source_is_jointly_certified(complete):
            carry = {
                record.task_id: int(record.candidate_response_time)
                for record in complete.task_records
                if record.candidate_response_time is not None
            }
            if len(carry) != len(instance.taskset.tasks):
                raise CalibrationError("certified fixed-CW source vector is incomplete")
        else:
            carry = None
        for rank, (left, right) in enumerate(zip(complete.task_records, local.task_records)):
            unavailable = {
                taskset.TaskSolverStatus.TIMEOUT,
                taskset.TaskSolverStatus.NUMERIC_ERROR,
                taskset.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
                taskset.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
                taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
            }
            if left.solver_status in unavailable or right.solver_status in unavailable:
                response_relation, reduction = "UNAVAILABLE", None
            else:
                response_relation, reduction = _response_relation(
                    left.candidate_response_time, right.candidate_response_time
                )
            if response_relation == "VIOLATION":
                raise CalibrationError(
                    f"P0 dominance violation in {relation}, task {left.task_id}"
                )
            response_row = {
                "phase": instance.phase,
                "cell_id": instance.cell_id,
                "utilization": instance.utilization,
                "exact_e0": instance.exact_e0,
                "taskset_id": instance.taskset.taskset_id,
                "relation": relation,
                "task_id": left.task_id,
                "priority_rank": rank,
                "complete_solver_status": left.solver_status.value,
                "local_solver_status": right.solver_status.value,
                "complete_certification_status": left.certification_status.value,
                "local_certification_status": right.certification_status.value,
                "complete_candidate": left.candidate_response_time,
                "local_candidate": right.candidate_response_time,
                "response_relation": response_relation,
                "response_reduction": reduction,
                "strict_response": response_relation == "TIGHTER",
                "local_only_candidate": response_relation == "LOCAL_ONLY_CANDIDATE",
                "certification_gain": (
                    right.certification_status is taskset.TaskCertificationStatus.CERTIFIED
                    and left.certification_status is not taskset.TaskCertificationStatus.CERTIFIED
                ),
                "diagnostic_status": "NOT_APPLICABLE_RECURSIVE" if not trace_envelopes else "PENDING",
                "strict_envelope_count": 0,
                "local_only_closure_count": 0,
            }
            response_rows.append(response_row)
            if not trace_envelopes:
                continue
            if carry is None:
                response_row["diagnostic_status"] = "SKIPPED_UNCERTIFIED_FIXED_CW_SOURCE"
                continue
            if not (_record_evaluated(left) and _record_evaluated(right)):
                response_row["diagnostic_status"] = "SKIPPED_NOT_EVALUATED"
                continue
            if (
                left.solver_status is taskset.TaskSolverStatus.TIMEOUT
                or right.solver_status is taskset.TaskSolverStatus.TIMEOUT
            ):
                response_row["diagnostic_status"] = "SKIPPED_PRODUCTION_TIMEOUT"
                continue
            complete_search, complete_trace, complete_closures = _trace_search(
                core.EnvelopeKind.COMPLETE, instance, rank, carry, beta,
                timeout_seconds, envelope_cache,
            )
            local_search, local_trace, local_closures = _trace_search(
                core.EnvelopeKind.LOCAL, instance, rank, carry, beta,
                timeout_seconds, envelope_cache,
            )
            if complete_search.solver_status in {
                core.V93SolverStatus.UNPROVEN_NUMERIC,
                core.V93SolverStatus.UNPROVEN_OVERFLOW,
            } or local_search.solver_status in {
                core.V93SolverStatus.UNPROVEN_NUMERIC,
                core.V93SolverStatus.UNPROVEN_OVERFLOW,
            }:
                raise CalibrationError("P0 numeric diagnostic failure")
            truncated = (
                complete_search.solver_status is core.V93SolverStatus.UNPROVEN_TIMEOUT
                or local_search.solver_status is core.V93SolverStatus.UNPROVEN_TIMEOUT
            )
            response_row["diagnostic_status"] = (
                "TRACED_TRUNCATED" if truncated else "TRACED_COMPLETE"
            )
            local_only = local_closures - complete_closures
            strict_count = 0
            target = instance.taskset.tasks[rank]
            hp_tasks = instance.taskset.tasks[:rank]
            lp_tasks = instance.taskset.tasks[rank + 1:]
            carry_key = tuple(sorted((str(key), int(value)) for key, value in carry.items()))
            for w, h, q in sorted(set(complete_trace) | set(local_trace)):
                values = {}
                for kind in (core.EnvelopeKind.COMPLETE, core.EnvelopeKind.LOCAL):
                    cache_key = (
                        instance.taskset.semantic_hash, kind.value, rank,
                        carry_key, w, h, q,
                    )
                    if cache_key not in envelope_cache:
                        envelope_cache[cache_key] = core.exact_energy_envelope_v9_3(
                            kind, target, hp_tasks, lp_tasks, w, q, h,
                            instance.taskset.processors, carry,
                        )
                    values[kind] = envelope_cache[cache_key]
                complete_value = values[core.EnvelopeKind.COMPLETE]
                local_value = values[core.EnvelopeKind.LOCAL]
                if local_value > complete_value:
                    raise CalibrationError("P0 pointwise local envelope violation")
                envelope_relation = "STRICT" if local_value < complete_value else "EQUAL"
                strict_count += envelope_relation == "STRICT"
                service = Fraction(instance.exact_e0) + beta[h + q - 1]
                access_rows.append({
                    "phase": instance.phase, "cell_id": instance.cell_id,
                    "utilization": instance.utilization, "exact_e0": instance.exact_e0,
                    "taskset_id": instance.taskset.taskset_id, "relation": relation,
                    "task_id": target.name, "priority_rank": rank,
                    "w": w, "h": h, "q": q,
                    "visited_by_complete": (w, h, q) in complete_trace,
                    "visited_by_local": (w, h, q) in local_trace,
                    "complete_envelope": fraction_text(complete_value),
                    "local_envelope": fraction_text(local_value),
                    "envelope_relation": envelope_relation,
                    "service_value": fraction_text(service),
                    "complete_closure": (w, h, q) in complete_closures,
                    "local_closure": (w, h, q) in local_closures,
                    "local_only_closure": (w, h, q) in local_only,
                    "predicted_interval_hit": local_value <= service < complete_value,
                })
            response_row["strict_envelope_count"] = strict_count
            response_row["local_only_closure_count"] = len(local_only)
            for w, h, q in sorted(local_only):
                closure_rows.append({
                    "phase": instance.phase, "cell_id": instance.cell_id,
                    "utilization": instance.utilization, "exact_e0": instance.exact_e0,
                    "taskset_id": instance.taskset.taskset_id, "relation": relation,
                    "task_id": target.name, "priority_rank": rank,
                    "w": w, "h": h, "q": q,
                    "complete_candidate": left.candidate_response_time,
                    "local_candidate": right.candidate_response_time,
                    "strict_response": response_relation == "TIGHTER",
                })
    return response_rows, access_rows, closure_rows


def _p95(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(.95 * len(ordered)) - 1)]


def summarize_cells(
    result_rows: Sequence[Mapping[str, Any]],
    generated_rows: Sequence[Mapping[str, Any]],
    tightness_rows: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    result_groups: Dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in result_rows:
        result_groups[(str(row["utilization"]), str(row["exact_e0"]))].append(row)
    taskset_to_dt: Dict[str, list[Fraction]] = {}
    for row in generated_rows:
        taskset_to_dt[str(row["taskset_id"])] = [
            Fraction(item) for item in json.loads(str(row["d_over_t_values_json"]))
        ]
    tight_groups: Dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in tightness_rows:
        tight_groups[(str(row["utilization"]), str(row["exact_e0"]))].append(row)
    output = []
    for key, members in sorted(result_groups.items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]))):
        statuses = Counter(str(row["solver_status"]) for row in members)
        runtimes = [float(row["runtime_wall_seconds"]) for row in members]
        taskset_ids = {str(row["taskset_id"]) for row in members}
        dts = [value for taskset_id_value in taskset_ids for value in taskset_to_dt[taskset_id_value]]
        tight = tight_groups.get(key, [])
        strict_envelope = sum(int(row["strict_envelope_count"]) for row in tight)
        local_only_closure = sum(int(row["local_only_closure_count"]) for row in tight)
        strict_response = sum(bool(row["strict_response"]) for row in tight)
        certification_gain = sum(bool(row["certification_gain"]) for row in tight)
        equal_count = sum(row["response_relation"] == "EQUAL" for row in tight)
        violations = sum(row["response_relation"] == "VIOLATION" for row in tight)
        if certification_gain:
            category = "E"
        elif strict_response:
            category = "D"
        elif local_only_closure:
            category = "C"
        elif strict_envelope:
            category = "B"
        else:
            category = "A"
        certified = sum(str(row["taskset_proven"]) == "True" for row in members)
        completed = statuses["COMPLETED"]
        output.append({
            "utilization": key[0], "exact_e0": key[1], "category": category,
            "taskset_instances": len(taskset_ids), "requested": len(members),
            "completed": completed, "certified": certified,
            "no_candidate": statuses["NO_CANDIDATE"], "timeout": statuses["TIMEOUT"],
            "not_applicable": statuses["NOT_APPLICABLE_DEPENDENCY"],
            "numeric_failure": statuses["NUMERIC_ERROR"],
            "internal_failure": statuses["INTERNAL_CONFORMANCE_FAILURE"],
            "certification_ratio": certified / len(members) if members else None,
            "runtime_mean": sum(runtimes) / len(runtimes),
            "runtime_median": median(runtimes), "runtime_p95": _p95(runtimes),
            "runtime_max": max(runtimes),
            "d_over_t_min": fraction_text(min(dts)),
            "d_over_t_median": fraction_text(median(dts)),
            "d_over_t_max": fraction_text(max(dts)),
            "common_candidate_count": sum(row["response_relation"] in {"TIGHTER", "EQUAL"} for row in tight),
            "strict_envelope_count": strict_envelope,
            "local_only_closure_count": local_only_closure,
            "strict_response_count": strict_response,
            "certification_gain_count": certification_gain,
            "local_only_candidate_count": sum(bool(row["local_only_candidate"]) for row in tight),
            "equal_count": equal_count,
            "dominance_violation_count": violations,
            "diagnostic_truncated_count": sum(row["diagnostic_status"] == "TRACED_TRUNCATED" for row in tight),
        })
    return output


def stage_b_cells(stage_a_summary: Sequence[Mapping[str, Any]]) -> list[Dict[str, str]]:
    selected = []
    for row in stage_a_summary:
        category = row["category"]
        explicit = category in {"C", "D", "E"}
        informative_b = bool(
            row["strict_envelope_count"] > 0
            and row["timeout"] == 0
            and row["numeric_failure"] == 0
            and row["internal_failure"] == 0
            and row["completed"] >= 4
            and row["certified"] > 0
        )
        if explicit or informative_b:
            selected.append({
                "utilization": str(row["utilization"]),
                "exact_e0": str(row["exact_e0"]),
            })
    if len(selected) > 6:
        raise CalibrationError("Stage B selection exceeds six-cell hard bound")
    return selected
