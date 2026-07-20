"""Explicit-denominator aggregation and mechanism classification for EXT-1B."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .ext1b_b1_analysis import write_ext1b_b1_outputs
from .ext1b_observation import write_ext1b_observation_outputs
from .ext1b_statistics import (
    B3_STATISTIC_COLUMNS,
    STATISTIC_COLUMNS,
    paired_statistics_rows,
)
from .result_writer import read_csv, write_csv
from .scheduler_pairing import assert_scheduler_only_difference
from .scheduler_registry import SCHEDULER_IDS, scheduler_by_id


UNAVAILABLE = "UNAVAILABLE"
VALID_STATUSES = {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
EXT1B_FAIRNESS_FIELDS = (
    "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
    "initial_battery", "battery_capacity", "horizon", "maximum_horizon",
    "generation_seed", "M", "priority_hash", "power_hash", "deadline_hash",
    "release_hash", "workload_vector_hash", "simulator_build_hash",
)

ACTIVATION_COLUMNS = (
    "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "mechanism_scope", "structural_activation",
    "runtime_observable", "runtime_activation", "activation_class", "outcome_comparable",
    "deadline_outcome_different", "activation_evidence",
)
PAIRED_COLUMNS = (
    "paired_instance_id", "scenario_kind", "scenario_subtype",
    "scenario_cell_id", "left_scheduler", "right_scheduler", "left_status",
    "right_status", "overall_relation", "left_top_m_success",
    "right_top_m_success", "top_m_relation", "activation_class",
    "taskset_hash", "trace_hash",
)
SCHEDULER_SUMMARY_COLUMNS = (
    "scenario_kind", "scenario_subtype", "scenario_cell_id", "scheduler_id",
    "timing_family", "mechanism", "requested_denominator",
    "terminal_denominator", "valid_terminal_denominator",
    "sufficiently_observed_denominator", "structural_activation_denominator",
    "runtime_activation_denominator", "runtime_activation_count",
    "outcome_comparable_denominator",
    "pass_count", "deadline_miss_count", "top_m_success_count",
    "horizon_insufficient_count", "timeout_count", "internal_error_count",
    "pass_ratio_valid", "top_m_success_ratio_valid", "wins", "ties",
    "losses", "not_comparable",
)
SCENARIO_SUMMARY_COLUMNS = (
    "scenario_kind", "scenario_subtype", "scenario_cell_id",
    "requested_denominator", "terminal_denominator",
    "valid_terminal_denominator", "sufficiently_observed_denominator",
    "structural_activation_denominator", "runtime_activation_denominator",
    "runtime_activation_count",
    "outcome_comparable_denominator", "pass_count", "deadline_miss_count",
    "horizon_insufficient_count", "timeout_count", "internal_error_count",
    "generation_rejection_count", "wins", "ties", "losses",
    "not_comparable",
)
PRIORITY_SUMMARY_COLUMNS = (
    "scenario_kind", "scenario_subtype", "scenario_cell_id", "scheduler_id",
    "priority_rank", "task_denominator", "observed_jobs", "completed_jobs",
    "missed_jobs", "censored_jobs", "minimum_jobs_satisfied_count",
    "maximum_observed_response_time", "first_execution_min",
)
PLOT_COLUMNS = (
    "plot", "scenario_kind", "scenario_subtype", "scenario_cell_id",
    "scheduler_id", "comparator_scheduler", "paired_instance_id",
    "activation_class", "category", "x", "y", "denominator",
)


def _ratio(numerator: int, denominator: int) -> Any:
    return numerator / denominator if denominator else UNAVAILABLE


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).upper()
    if text in {"TRUE", "1"}:
        return True
    if text in {"FALSE", "0"}:
        return False
    return None


def _integer(value: Any) -> int | None:
    if value in {None, "", UNAVAILABLE, "NONE"}:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _result_comparable(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("status")) in VALID_STATUSES
        and _bool(row.get("comparison_eligible")) is True
    )


def _validate_request_groups(
    rows: list[Mapping[str, Any]], scheduler_ids: Sequence[str],
) -> Dict[str, Mapping[str, Any]]:
    assert_scheduler_only_difference(rows, scheduler_ids)
    by_id: Dict[str, Mapping[str, Any]] = {}
    grouped: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        request_id = str(row.get("request_id", ""))
        if not request_id or request_id in by_id:
            raise RuntimeError("duplicate or empty EXT-1B request_id")
        by_id[request_id] = row
        grouped[str(row["paired_instance_id"])].append(row)
    for pair_id, members in grouped.items():
        for field in EXT1B_FAIRNESS_FIELDS:
            if len({str(row[field]) for row in members}) != 1:
                raise RuntimeError(f"P0 EXT-1B fairness mismatch in {field} for {pair_id}")
    return by_id


def _top_relation(left: Any, right: Any) -> str:
    left_value, right_value = _bool(left), _bool(right)
    if left_value is None or right_value is None:
        return "NOT_COMPARABLE"
    if left_value == right_value:
        return "TIE"
    return "LEFT_WIN" if left_value else "RIGHT_WIN"


def _overall_relation(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    """Compare deadline outcomes without turning failures into false ties."""

    left_status, right_status = str(left["status"]), str(right["status"])
    if not _result_comparable(left) or not _result_comparable(right):
        return "NOT_COMPARABLE"
    if left_status == right_status:
        return "TIE"
    return "LEFT_WIN" if left_status == "SIM_PASS_OBSERVED" else "RIGHT_WIN"


def _first_execution_vectors(
    task_rows: Iterable[Mapping[str, Any]], top_m: int,
) -> Dict[tuple[str, str], tuple[Any, ...]]:
    grouped: Dict[tuple[str, str], list[tuple[int, Any]]] = defaultdict(list)
    for row in task_rows:
        rank = int(row["priority_rank"])
        if rank < top_m and _bool(row.get("request_comparison_eligible")) is True:
            grouped[(str(row["paired_instance_id"]), str(row["scheduler_id"]))].append(
                (rank, row.get("first_execution_time", UNAVAILABLE))
            )
    result = {}
    for key, values in grouped.items():
        ordered = sorted(values)
        numeric = [_integer(value) for _, value in ordered]
        if [rank for rank, _ in ordered] == list(range(top_m)) and all(
            value is not None for value in numeric
        ):
            result[key] = tuple(int(value) for value in numeric if value is not None)
    return result


def classify_mechanism_activation(
    results: Iterable[Mapping[str, Any]],
    task_rows: Iterable[Mapping[str, Any]],
    generation_attempts: Iterable[Mapping[str, Any]],
    *,
    top_m: int,
    b2_summaries: Iterable[Mapping[str, Any]] = (),
) -> list[Dict[str, Any]]:
    result_rows = list(results)
    by_pair: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in result_rows:
        by_pair[str(row["paired_instance_id"])][str(row["scheduler_id"])] = row
    b2_by_pair = {
        str(row["paired_instance_id"]): row for row in b2_summaries
    }
    registry = scheduler_by_id()
    activations: list[Dict[str, Any]] = []

    for pair_id, members in sorted(by_pair.items()):
        exemplar = next(iter(members.values()))
        kind = str(exemplar["scenario_kind"])
        scopes = ("ALL",) if kind != "TIMING_STRESS" else ("BLOCK", "NONBLOCK", "SYNC")
        for scope in scopes:
            scoped_ids = [
                scheduler for scheduler in members
                if scope == "ALL" or registry[scheduler].mechanism == scope
            ]
            scoped = [members[scheduler] for scheduler in scoped_ids]
            runtime_observable = False
            runtime: Any = UNAVAILABLE
            evidence: Dict[str, Any] = {}
            if kind == "BYPASS_STRESS":
                anchor = "gpfp_asap_nonblock"
                anchor_row = members.get(anchor)
                value = (
                    _integer(anchor_row.get("bypass_count"))
                    if anchor_row is not None else None
                )
                runtime_observable = (
                    anchor_row is not None
                    and _result_comparable(anchor_row)
                    and value is not None
                )
                if runtime_observable:
                    runtime = bool(value and value > 0)
                evidence = {
                    "native_event_anchor": anchor,
                    "bypass_count": value if value is not None else UNAVAILABLE,
                }
            elif kind == "SYNC_BATCH_STRESS":
                anchor = "gpfp_asap_sync"
                anchor_row = members.get(anchor)
                summary = b2_by_pair.get(pair_id)
                value = (
                    _integer(summary.get("atomic_wait_with_affordable_member_count"))
                    if summary is not None else None
                )
                runtime_observable = (
                    anchor_row is not None
                    and _result_comparable(anchor_row)
                    and summary is not None
                    and _bool(summary.get("audit_closed")) is True
                )
                if runtime_observable:
                    runtime = bool(value and value > 0)
                evidence = {
                    "native_event_anchor": anchor,
                    "atomic_wait_with_affordable_member_count": (
                        value if value is not None else UNAVAILABLE
                    ),
                    "atomic_wait_share": (
                        summary.get("atomic_wait_share", UNAVAILABLE)
                        if summary is not None else UNAVAILABLE
                    ),
                    "denominator_zero": (
                        summary.get("denominator_zero", UNAVAILABLE)
                        if summary is not None else UNAVAILABLE
                    ),
                }
            else:
                family_activation = {
                    registry[scheduler].timing_family: _bool(
                        members[scheduler].get("timing_activation")
                    )
                    for scheduler in scoped_ids
                }
                runtime_observable = all(
                    family_activation.get(family) is not None
                    for family in ("ASAP", "ALAP", "ST")
                )
                if runtime_observable:
                    runtime = any(bool(value) for value in family_activation.values())
                evidence = {
                    "dedicated_timing_audit_activation_by_family": family_activation,
                }

            statuses = [str(row["status"]) for row in scoped]
            comparable = bool(statuses) and all(_result_comparable(row) for row in scoped)
            different = comparable and len(set(statuses)) > 1
            if not runtime_observable:
                activation_class = "B_RUNTIME_UNOBSERVABLE"
            elif not runtime:
                activation_class = "B_STRUCTURAL_ONLY"
            elif different:
                activation_class = "C2_RUNTIME_ACTIVATED_OUTCOME_DIFFERENT"
            else:
                activation_class = "C1_RUNTIME_ACTIVATED_OUTCOME_SAME"
            activations.append({
                "paired_instance_id": pair_id,
                "scenario_kind": kind,
                "scenario_subtype": exemplar["scenario_subtype"],
                "scenario_cell_id": exemplar["scenario_cell_id"],
                "mechanism_scope": scope,
                "structural_activation": True,
                "runtime_observable": runtime_observable,
                "runtime_activation": runtime,
                "activation_class": activation_class,
                "outcome_comparable": comparable,
                "deadline_outcome_different": different if comparable else UNAVAILABLE,
                "activation_evidence": json.dumps(evidence, sort_keys=True),
            })

    for attempt in generation_attempts:
        if str(attempt.get("attempt_status")) != "REJECTED":
            continue
        activations.append({
            "paired_instance_id": UNAVAILABLE,
            "scenario_kind": attempt["scenario_kind"],
            "scenario_subtype": attempt["scenario_subtype"],
            "scenario_cell_id": attempt["scenario_cell_id"],
            "mechanism_scope": "ALL",
            "structural_activation": False,
            "runtime_observable": False,
            "runtime_activation": UNAVAILABLE,
            "activation_class": "A_STRUCTURAL_REJECTED",
            "outcome_comparable": False,
            "deadline_outcome_different": UNAVAILABLE,
            "activation_evidence": json.dumps({
                "rejection_code": attempt.get("rejection_code"),
                "rejection_detail": attempt.get("rejection_detail"),
            }, sort_keys=True),
        })
    return activations


def _activation_for_scheduler(
    activation_rows: Iterable[Mapping[str, Any]],
    scheduler_ids: Sequence[str] = SCHEDULER_IDS,
) -> Dict[tuple[str, str], Mapping[str, Any]]:
    registry = scheduler_by_id()
    by_pair: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in activation_rows:
        if row["paired_instance_id"] != UNAVAILABLE:
            by_pair[str(row["paired_instance_id"])].append(row)
    result = {}
    for pair_id, rows in by_pair.items():
        for scheduler in scheduler_ids:
            mechanism = registry[scheduler].mechanism
            matches = [
                row for row in rows
                if row["mechanism_scope"] in {"ALL", mechanism}
            ]
            if len(matches) != 1:
                raise RuntimeError(f"ambiguous EXT-1B activation scope for {pair_id}/{scheduler}")
            result[(pair_id, scheduler)] = matches[0]
    return result


def aggregate_ext1b_rows(
    requests: Iterable[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
    task_rows: Iterable[Mapping[str, Any]],
    generation_attempts: Iterable[Mapping[str, Any]],
    *,
    top_m: int,
    bootstrap_seed: int,
    bootstrap_resamples: int,
    b2_summaries: Iterable[Mapping[str, Any]] = (),
    scenario_instances: Iterable[Mapping[str, Any]] = (),
    scheduler_ids: Sequence[str] = SCHEDULER_IDS,
) -> Dict[str, list[Dict[str, Any]]]:
    request_rows, result_rows, tasks = list(requests), list(results), list(task_rows)
    attempts = list(generation_attempts)
    selected_scheduler_ids = tuple(scheduler_ids)
    request_by_id = _validate_request_groups(request_rows, selected_scheduler_ids)
    registry = scheduler_by_id()
    result_by_pair: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in result_rows:
        pair_id, scheduler = str(row["paired_instance_id"]), str(row["scheduler_id"])
        if scheduler in result_by_pair[pair_id]:
            raise RuntimeError("duplicate EXT-1B terminal result")
        request_id = str(row.get("request_id", ""))
        planned = request_by_id.get(request_id)
        if planned is None:
            raise RuntimeError(f"unplanned EXT-1B terminal result: {request_id}")
        if pair_id != str(planned["paired_instance_id"]) or scheduler != str(planned["scheduler_id"]):
            raise RuntimeError("P0 EXT-1B terminal/request identity mismatch")
        for field in (
            "scenario_kind", "scenario_subtype", "scenario_cell_id",
            "taskset_hash", "trace_hash", "simulation_config_hash", "input_hash",
        ):
            if str(row.get(field)) != str(planned.get(field)):
                raise RuntimeError(
                    f"P0 EXT-1B terminal/request {field} mismatch"
                )
        result_by_pair[pair_id][scheduler] = row
    complete_pairs = {
        pair_id for pair_id, members in result_by_pair.items()
        if set(members) == set(selected_scheduler_ids)
        and len(members) == len(selected_scheduler_ids)
    }
    dimensions_by_pair = {
        str(row["paired_instance_id"]): {
            "normalized_utilization": str(row.get("normalized_utilization", "")),
        }
        for row in scenario_instances
        if str(row.get("scenario_kind")) == "TIMING_STRESS"
    }
    comparison_results = []
    for row in result_rows:
        pair_id = str(row["paired_instance_id"])
        if pair_id not in complete_pairs:
            continue
        enriched = dict(row)
        enriched.update(dimensions_by_pair.get(pair_id, {}))
        comparison_results.append(enriched)
    comparison_tasks = [
        row for row in tasks
        if str(row["paired_instance_id"]) in complete_pairs
    ]
    activation_rows = classify_mechanism_activation(
        comparison_results, comparison_tasks, attempts, top_m=top_m,
        b2_summaries=b2_summaries,
    )
    activation_index = _activation_for_scheduler(
        activation_rows, selected_scheduler_ids,
    )

    paired_rows = []
    relation_counts: Dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for pair_id, members in sorted(result_by_pair.items()):
        if pair_id not in complete_pairs:
            continue
        for left_id, right_id in combinations(selected_scheduler_ids, 2):
            left, right = members[left_id], members[right_id]
            relation = _overall_relation(left, right)
            activation = activation_index[(pair_id, left_id)]
            paired_rows.append({
                "paired_instance_id": pair_id,
                "scenario_kind": left["scenario_kind"],
                "scenario_subtype": left["scenario_subtype"],
                "scenario_cell_id": left["scenario_cell_id"],
                "left_scheduler": left_id,
                "right_scheduler": right_id,
                "left_status": left["status"],
                "right_status": right["status"],
                "overall_relation": relation,
                "left_top_m_success": left["top_m_success"],
                "right_top_m_success": right["top_m_success"],
                "top_m_relation": _top_relation(left["top_m_success"], right["top_m_success"]),
                "activation_class": activation["activation_class"],
                "taskset_hash": left["taskset_hash"],
                "trace_hash": left["trace_hash"],
            })
            cell = str(left["scenario_cell_id"])
            opposite = {
                "LEFT_WIN": "RIGHT_WIN", "RIGHT_WIN": "LEFT_WIN",
                "TIE": "TIE", "NOT_COMPARABLE": "NOT_COMPARABLE",
            }
            if left_id == "gpfp_asap_block":
                relation_counts[(cell, left_id)][relation] += 1
                relation_counts[(cell, right_id)][opposite[relation]] += 1
            elif right_id == "gpfp_asap_block":
                relation_counts[(cell, left_id)][relation] += 1
                relation_counts[(cell, right_id)][opposite[relation]] += 1

    scheduler_rows = []
    cells = sorted({str(row["scenario_cell_id"]) for row in request_rows})
    for cell in cells:
        exemplar = next(row for row in request_rows if str(row["scenario_cell_id"]) == cell)
        for scheduler in selected_scheduler_ids:
            requested = [row for row in request_rows if str(row["scenario_cell_id"]) == cell and row["scheduler_id"] == scheduler]
            terminal = [row for row in result_rows if str(row["scenario_cell_id"]) == cell and row["scheduler_id"] == scheduler]
            statuses = Counter(str(row["status"]) for row in terminal)
            valid = [row for row in terminal if _result_comparable(row)]
            valid_statuses = Counter(str(row["status"]) for row in valid)
            activations = [
                activation_index[(str(row["paired_instance_id"]), scheduler)]
                for row in terminal
                if (str(row["paired_instance_id"]), scheduler) in activation_index
            ]
            runtime_observable = sum(_bool(row["runtime_observable"]) is True for row in activations)
            runtime_count = sum(_bool(row["runtime_activation"]) is True for row in activations)
            comparable = sum(_bool(row["outcome_comparable"]) is True for row in activations)
            counts = relation_counts[(cell, scheduler)]
            registration = registry[scheduler]
            scheduler_rows.append({
                "scenario_kind": exemplar["scenario_kind"],
                "scenario_subtype": exemplar["scenario_subtype"],
                "scenario_cell_id": cell,
                "scheduler_id": scheduler,
                "timing_family": registration.timing_family,
                "mechanism": registration.mechanism,
                "requested_denominator": len(requested),
                "terminal_denominator": len(terminal),
                "valid_terminal_denominator": len(valid),
                "sufficiently_observed_denominator": len(valid),
                "structural_activation_denominator": len(requested),
                "runtime_activation_denominator": runtime_observable,
                "runtime_activation_count": runtime_count,
                "outcome_comparable_denominator": comparable,
                "pass_count": valid_statuses["SIM_PASS_OBSERVED"],
                "deadline_miss_count": valid_statuses["SIM_DEADLINE_MISS"],
                "top_m_success_count": sum(_bool(row["top_m_success"]) is True for row in valid),
                "horizon_insufficient_count": statuses["SIM_HORIZON_INSUFFICIENT"],
                "timeout_count": statuses["SIM_RUNTIME_TIMEOUT"],
                "internal_error_count": statuses["SIM_INTERNAL_ERROR"],
                "pass_ratio_valid": _ratio(valid_statuses["SIM_PASS_OBSERVED"], len(valid)),
                "top_m_success_ratio_valid": _ratio(sum(_bool(row["top_m_success"]) is True for row in valid), len(valid)),
                "wins": counts["LEFT_WIN"],
                "ties": counts["TIE"],
                "losses": counts["RIGHT_WIN"],
                "not_comparable": counts["NOT_COMPARABLE"],
            })

    scenario_rows = []
    for cell in cells:
        requested = [row for row in request_rows if str(row["scenario_cell_id"]) == cell]
        terminal = [row for row in result_rows if str(row["scenario_cell_id"]) == cell]
        statuses = Counter(str(row["status"]) for row in terminal)
        valid_rows = [row for row in terminal if _result_comparable(row)]
        valid_statuses = Counter(str(row["status"]) for row in valid_rows)
        cell_activations = [
            activation_index[(str(row["paired_instance_id"]), str(row["scheduler_id"]))]
            for row in terminal
            if (str(row["paired_instance_id"]), str(row["scheduler_id"])) in activation_index
        ]
        rejected = [row for row in activation_rows if str(row["scenario_cell_id"]) == cell and row["activation_class"] == "A_STRUCTURAL_REJECTED"]
        primary_relations = [
            row for row in paired_rows
            if str(row["scenario_cell_id"]) == cell
            and "gpfp_asap_block" in {row["left_scheduler"], row["right_scheduler"]}
        ]
        normalized = Counter()
        for row in primary_relations:
            relation = row["overall_relation"]
            if row["right_scheduler"] == "gpfp_asap_block":
                relation = {"LEFT_WIN": "RIGHT_WIN", "RIGHT_WIN": "LEFT_WIN"}.get(relation, relation)
            normalized[relation] += 1
        exemplar = requested[0]
        scenario_rows.append({
            "scenario_kind": exemplar["scenario_kind"],
            "scenario_subtype": exemplar["scenario_subtype"],
            "scenario_cell_id": cell,
            "requested_denominator": len(requested),
            "terminal_denominator": len(terminal),
            "valid_terminal_denominator": len(valid_rows),
            "sufficiently_observed_denominator": len(valid_rows),
            "structural_activation_denominator": len(requested),
            "runtime_activation_denominator": sum(
                _bool(row["runtime_observable"]) is True for row in cell_activations
            ),
            "runtime_activation_count": sum(
                _bool(row["runtime_activation"]) is True for row in cell_activations
            ),
            "outcome_comparable_denominator": sum(
                _bool(row["outcome_comparable"]) is True for row in cell_activations
            ),
            "pass_count": valid_statuses["SIM_PASS_OBSERVED"],
            "deadline_miss_count": valid_statuses["SIM_DEADLINE_MISS"],
            "horizon_insufficient_count": statuses["SIM_HORIZON_INSUFFICIENT"],
            "timeout_count": statuses["SIM_RUNTIME_TIMEOUT"],
            "internal_error_count": statuses["SIM_INTERNAL_ERROR"],
            "generation_rejection_count": len(rejected),
            "wins": normalized["LEFT_WIN"],
            "ties": normalized["TIE"],
            "losses": normalized["RIGHT_WIN"],
            "not_comparable": normalized["NOT_COMPARABLE"],
        })

    priority_rows = []
    priority_groups: Dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in comparison_tasks:
        priority_groups[(str(row["scenario_cell_id"]), str(row["scheduler_id"]), int(row["priority_rank"]))].append(row)
    for (cell, scheduler, rank), members in sorted(priority_groups.items()):
        exemplar = members[0]
        responses = [_integer(row.get("maximum_observed_response_time")) for row in members]
        firsts = [_integer(row.get("first_execution_time")) for row in members]
        priority_rows.append({
            "scenario_kind": exemplar["scenario_kind"],
            "scenario_subtype": exemplar["scenario_subtype"],
            "scenario_cell_id": cell,
            "scheduler_id": scheduler,
            "priority_rank": rank,
            "task_denominator": len(members),
            "observed_jobs": sum(int(row["observed_jobs"]) for row in members),
            "completed_jobs": sum(int(row["completed_jobs"]) for row in members),
            "missed_jobs": sum(int(row["missed_jobs"]) for row in members),
            "censored_jobs": sum(int(row["censored_jobs"]) for row in members),
            "minimum_jobs_satisfied_count": sum(_bool(row["minimum_jobs_satisfied"]) is True for row in members),
            "maximum_observed_response_time": max((value for value in responses if value is not None), default=UNAVAILABLE),
            "first_execution_min": min((value for value in firsts if value is not None), default=UNAVAILABLE),
        })

    statistic_rows = paired_statistics_rows(
        comparison_results,
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
        scheduler_ids=selected_scheduler_ids,
    )
    plots = []
    for row in comparison_results:
        activation = activation_index[(str(row["paired_instance_id"]), str(row["scheduler_id"]))]
        for plot, field in (
            ("overall_pass_ratio", "overall_success"),
            ("top_m_success_ratio", "top_m_success"),
            ("bypass_activation", "bypass_count"),
            ("sync_activation", "synchronization_wait_ticks"),
            ("timing_activation", "timing_activation"),
            ("top_m_response_time", "top_m_max_response_time"),
            ("first_missed_priority_rank", "first_missed_priority_rank"),
        ):
            value = (
                activation["runtime_activation"]
                if field == "timing_activation"
                else row.get(field, UNAVAILABLE)
            )
            plots.append({
                "plot": plot, "scenario_kind": row["scenario_kind"],
                "scenario_subtype": row["scenario_subtype"],
                "scenario_cell_id": row["scenario_cell_id"],
                "scheduler_id": row["scheduler_id"],
                "comparator_scheduler": "",
                "paired_instance_id": row["paired_instance_id"],
                "activation_class": activation["activation_class"],
                "category": row["status"], "x": value, "y": 1,
                "denominator": 1,
            })
        trajectory = row.get("battery_trajectory_json")
        if trajectory not in {None, "", UNAVAILABLE}:
            for point in json.loads(str(trajectory)):
                plots.append({
                    "plot": "battery_trajectory", "scenario_kind": row["scenario_kind"],
                    "scenario_subtype": row["scenario_subtype"],
                    "scenario_cell_id": row["scenario_cell_id"],
                    "scheduler_id": row["scheduler_id"], "comparator_scheduler": "",
                    "paired_instance_id": row["paired_instance_id"],
                    "activation_class": activation["activation_class"],
                    "category": row["status"], "x": point["time"],
                    "y": point["energy_j"], "denominator": 1,
                })
    for row in comparison_tasks:
        activation = activation_index[(
            str(row["paired_instance_id"]), str(row["scheduler_id"]),
        )]
        plots.append({
            "plot": "first_execution_timeline",
            "scenario_kind": row["scenario_kind"],
            "scenario_subtype": row["scenario_subtype"],
            "scenario_cell_id": row["scenario_cell_id"],
            "scheduler_id": row["scheduler_id"],
            "comparator_scheduler": "",
            "paired_instance_id": row["paired_instance_id"],
            "activation_class": activation["activation_class"],
            "category": f"task:{row.get('task_id', row['priority_rank'])}",
            "x": row.get("first_execution_time", UNAVAILABLE),
            "y": row["priority_rank"],
            "denominator": 1,
        })
    for row in statistic_rows:
        if row["metric"] in {"overall_success", "top_m_max_response_time"}:
            value = row["risk_difference"] if row["metric_type"] == "BINARY" else row["median_paired_difference"]
            plots.append({
                "plot": "paired_risk_difference" if row["metric_type"] == "BINARY" else "paired_response_difference",
                "scenario_kind": row["scenario_kind"], "scenario_subtype": row["scenario_subtype"],
                "scenario_cell_id": row["scenario_cell_id"],
                "scheduler_id": row["primary_scheduler"],
                "comparator_scheduler": row["comparator_scheduler"],
                "paired_instance_id": "", "activation_class": "ALL",
                "category": row["metric"], "x": value, "y": row["paired_count"],
                "denominator": row["paired_count"],
            })
    return {
        "activation": activation_rows,
        "paired": paired_rows,
        "scheduler_summary": scheduler_rows,
        "scenario_summary": scenario_rows,
        "priority_summary": priority_rows,
        "statistics": statistic_rows,
        "plots": plots,
    }


def aggregate_ext1b(root: Path, config: Mapping[str, Any]) -> Dict[str, int]:
    b1_counts = write_ext1b_b1_outputs(root, config)
    observation_counts = write_ext1b_observation_outputs(root, config)
    tables = aggregate_ext1b_rows(
        read_csv(root / "simulation_requests.csv"),
        read_csv(root / "simulation_results.csv"),
        read_csv(root / "task_outcomes.csv"),
        read_csv(root / "generation_attempts.csv"),
        top_m=int(config["statistics"]["top_m"]),
        bootstrap_seed=int(config["statistics"]["bootstrap_seed"]),
        bootstrap_resamples=int(config["statistics"]["bootstrap_resamples"]),
        b2_summaries=read_csv(root / "b2_summary.csv"),
        scenario_instances=read_csv(root / "scenario_instances.csv"),
        scheduler_ids=tuple(config["scheduler_ids"]),
    )
    statistic_columns = (
        B3_STATISTIC_COLUMNS
        if config["scenario"]["kind"] == "TIMING_STRESS"
        else STATISTIC_COLUMNS
    )
    outputs = (
        ("mechanism_activation.csv", ACTIVATION_COLUMNS, "activation"),
        ("paired_scheduler_outcomes.csv", PAIRED_COLUMNS, "paired"),
        ("scheduler_summary.csv", SCHEDULER_SUMMARY_COLUMNS, "scheduler_summary"),
        ("scenario_summary.csv", SCENARIO_SUMMARY_COLUMNS, "scenario_summary"),
        ("priority_rank_summary.csv", PRIORITY_SUMMARY_COLUMNS, "priority_summary"),
        ("paired_statistics.csv", statistic_columns, "statistics"),
        ("ext1b_plot_data.csv", PLOT_COLUMNS, "plots"),
    )
    for name, columns, key in outputs:
        write_csv(root / name, columns, tables[key])
    return {
        **b1_counts,
        **observation_counts,
        **{f"{key}_rows": len(tables[key]) for _, _, key in outputs},
    }
