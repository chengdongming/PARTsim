"""Production B2/B3 observation outputs derived from retained native traces."""

from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import canonical_json, fraction_text
from .ext1b_b2_batch_audit import (
    B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER,
    B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER,
    B2_STATE_CONTINUATION_CANDIDATE_WAIT,
    B2_STATE_CONTINUATION_ONLY,
    B2_STATE_ILLEGAL_PARTIAL_LAUNCH,
    B2_STATE_ILLEGAL_TRANSITION,
    B2_STATE_UNCLASSIFIABLE,
    audit_asap_block_pair_trace,
    audit_asap_sync_trace,
    summarize_b2_observations,
)
from .ext1b_b3_timing_audit import (
    ALAP_POSITIVE_SLACK_DEFER,
    ALAP_URGENT_ELIGIBILITY,
    ASAP_IMMEDIATE_ELIGIBILITY,
    ILLEGAL_TIMING_TRANSITION,
    NOT_APPLICABLE,
    SCHEDULERS as TIMING_SCHEDULERS,
    ST_AFFORDABLE_ASAP_BEHAVIOR,
    ST_ENERGY_INSUFFICIENT_SLACK_WAIT,
    UNCLASSIFIABLE,
    audit_timing_trace,
)
from .result_writer import read_csv, write_csv


UNAVAILABLE = "UNAVAILABLE"
AUDITABLE_STATUSES = {
    "SIM_PASS_OBSERVED",
    "SIM_DEADLINE_MISS",
    "SIM_HORIZON_INSUFFICIENT",
}

B2_DECISION_COLUMNS = (
    "request_id", "paired_instance_id", "taskset_hash", "scheduler_id",
    "scenario_cell_id", "normalized_utilization",
    "nominal_energy_supply_ratio", "tick", "classified_state",
    "active_top_m_count", "continuation_count",
    "candidate_count", "affordable_prefix_length", "whole_batch_required_energy_mJ",
    "available_energy_mJ", "whole_batch_affordable", "feasible_subset_exists",
    "selected_count", "actual_launch_count", "atomic_opportunity",
    "atomic_wait_with_affordable_member", "ready_but_idle",
    "partial_launch_violation", "classification_errors_json",
    "evidence_event_ids_json",
)
B2_SUMMARY_COLUMNS = (
    "paired_instance_id", "scenario_cell_id", "normalized_utilization",
    "nominal_energy_supply_ratio", "taskset_hash", "input_hash",
    "comparison_scope", "asap_block_request_id", "asap_sync_request_id",
    "asap_block_status", "asap_sync_status", "batch_candidate_decision_count",
    "affordable_atomic_launch_count", "unaffordable_atomic_wait_count",
    "atomic_wait_with_affordable_member_count", "illegal_partial_launch_count",
    "illegal_transition_count", "continuation_only_decision_count",
    "unclassifiable_decision_count", "active_batch_opportunity_count",
    "atomic_wait_share", "denominator_zero", "ready_but_idle_ticks",
    "audited_ready_but_idle_ticks", "first_execution_time", "response_time",
    "deadline_miss", "asap_block_ready_but_idle_ticks",
    "asap_block_first_execution_time", "asap_block_response_time",
    "asap_block_deadline_miss", "matched_control_failure_count",
    "matched_control_success_count", "control_not_applicable_count",
    "control_evidence_incomplete_count", "state_counts_json", "audit_closed",
    "mechanism_activated",
)
B3_EVENT_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_cell_id", "taskset_hash",
    "normalized_utilization", "timing_subtype",
    "nominal_energy_supply_ratio", "deadline_ratio_min",
    "deadline_ratio_max",
    "scheduler_id", "scheduler_family", "blocking_policy", "time",
    "task_name", "arrival_time", "job_id", "classified_state", "reason",
)
B3_SUMMARY_COLUMNS = (
    "request_id", "paired_instance_id", "scenario_cell_id", "taskset_hash",
    "normalized_utilization", "timing_subtype",
    "nominal_energy_supply_ratio", "deadline_ratio_min",
    "deadline_ratio_max",
    "input_hash", "scheduler_id", "scheduler_family", "blocking_policy",
    "comparison_scope", "status", "asap_immediate_count",
    "alap_positive_slack_defer_count", "alap_urgent_eligible_count",
    "st_affordable_asap_count", "st_energy_insufficient_slack_wait_count",
    "st_wait_ticks", "st_release_energy_recovered_count",
    "st_release_slack_urgent_count", "st_release_other_count",
    "timing_not_applicable_count", "timing_unclassifiable_count",
    "timing_illegal_count", "ready_but_idle_ticks", "first_execution_time",
    "response_time", "deadline_miss", "timing_activation", "audit_error_count",
    "audit_closed", "same_job_transition_count",
    "activation_candidate_job_count", "activation_denominator_zero",
)


class Ext1BObservationError(RuntimeError):
    """A retained trace cannot support the requested scientific observation."""


def _integer(value: Any, default: int = 0) -> int:
    if value in {None, "", UNAVAILABLE}:
        return default
    return int(value)


def _trace_path(root: Path, row: Mapping[str, Any]) -> Path:
    raw = str(row.get("retained_trace_path", "")).strip()
    if not raw:
        raise Ext1BObservationError(
            f"missing retained semantic trace for {row.get('request_id')}"
        )
    supplied = Path(raw)
    candidates = [supplied]
    if not supplied.is_absolute():
        candidates.extend((root / supplied, root.parent / supplied))
    candidates.append(root / "retained_traces" / supplied.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise Ext1BObservationError(
        f"retained semantic trace does not exist for {row.get('request_id')}: {raw}"
    )


def _json_list(value: Any) -> list[str]:
    if value in {None, "", UNAVAILABLE}:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Ext1BObservationError(f"invalid JSON list in result row: {value!r}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise Ext1BObservationError("result JSON value must be a string list")
    return parsed


def _by_pair(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Mapping[str, Any]]]:
    grouped: Dict[str, Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["paired_instance_id"])][str(row["scheduler_id"])] = row
    return grouped


def _dimension_text(value: Any, label: str) -> str:
    try:
        parsed = Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise Ext1BObservationError(f"{label} must be an exact rational") from exc
    return fraction_text(parsed)


def _b2_dimensions_by_pair(
    scenarios: Iterable[Mapping[str, Any]],
) -> Dict[str, Dict[str, str]]:
    dimensions: Dict[str, Dict[str, str]] = {}
    for row in scenarios:
        if str(row.get("scenario_kind")) != "SYNC_BATCH_STRESS":
            continue
        pair_id = str(row.get("paired_instance_id", ""))
        if not pair_id or pair_id in dimensions:
            raise Ext1BObservationError(
                f"duplicate or empty B2 scenario instance: {pair_id!r}"
            )
        dimensions[pair_id] = {
            "scenario_cell_id": str(row.get("scenario_cell_id", "")),
            "normalized_utilization": _dimension_text(
                row.get("normalized_utilization"),
                f"B2 normalized_utilization for {pair_id}",
            ),
            "nominal_energy_supply_ratio": _dimension_text(
                row.get("nominal_energy_supply_ratio"),
                f"B2 nominal_energy_supply_ratio for {pair_id}",
            ),
        }
        if not dimensions[pair_id]["scenario_cell_id"]:
            raise Ext1BObservationError(
                f"missing B2 scenario_cell_id for {pair_id}"
            )
    return dimensions


def _b3_dimensions_by_pair(
    scenarios: Iterable[Mapping[str, Any]],
) -> Dict[str, Dict[str, str]]:
    dimensions: Dict[str, Dict[str, str]] = {}
    for row in scenarios:
        if str(row.get("scenario_kind")) != "TIMING_STRESS":
            continue
        pair_id = str(row.get("paired_instance_id", ""))
        if not pair_id or pair_id in dimensions:
            raise Ext1BObservationError(
                f"duplicate or empty B3 scenario instance: {pair_id!r}"
            )
        try:
            structure = json.loads(str(row.get("structure_json", "")))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise Ext1BObservationError(
                f"invalid B3 frozen structure metadata for {pair_id}"
            ) from exc
        timing = structure.get("timing_dimensions") if isinstance(structure, dict) else None
        if not isinstance(timing, dict):
            raise Ext1BObservationError(
                f"missing B3 frozen timing dimensions for {pair_id}"
            )
        scenario_cell_id = str(row.get("scenario_cell_id", ""))
        timing_subtype = str(row.get("scenario_subtype", ""))
        if (
            str(timing.get("scenario_cell_id", "")) != scenario_cell_id
            or str(timing.get("scenario_kind", "")) != "TIMING_STRESS"
            or str(timing.get("scenario_subtype", "")) != timing_subtype
        ):
            raise Ext1BObservationError(
                f"B3 frozen timing dimension identity mismatch for {pair_id}"
            )
        nominal = _dimension_text(
            row.get("nominal_energy_supply_ratio"),
            f"B3 nominal_energy_supply_ratio for {pair_id}",
        )
        frozen_nominal = _dimension_text(
            timing.get("nominal_energy_supply_ratio"),
            f"B3 frozen nominal_energy_supply_ratio for {pair_id}",
        )
        if nominal != frozen_nominal:
            raise Ext1BObservationError(
                f"B3 nominal energy dimension mismatch for {pair_id}"
            )
        dimensions[pair_id] = {
            "scenario_cell_id": scenario_cell_id,
            "normalized_utilization": _dimension_text(
                row.get("normalized_utilization"),
                f"B3 normalized_utilization for {pair_id}",
            ),
            "timing_subtype": timing_subtype,
            "nominal_energy_supply_ratio": nominal,
            "deadline_ratio_min": _dimension_text(
                timing.get("deadline_ratio_min"),
                f"B3 deadline_ratio_min for {pair_id}",
            ),
            "deadline_ratio_max": _dimension_text(
                timing.get("deadline_ratio_max"),
                f"B3 deadline_ratio_max for {pair_id}",
            ),
        }
        if not scenario_cell_id or not timing_subtype:
            raise Ext1BObservationError(
                f"missing B3 scenario identity for {pair_id}"
            )
    return dimensions


def _b2_outputs(
    root: Path,
    config: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[str]]:
    decision_rows: list[Dict[str, Any]] = []
    summary_rows: list[Dict[str, Any]] = []
    failures: list[str] = []
    requests_by_pair = _by_pair(requests)
    results_by_pair = _by_pair(results)
    dimensions_by_pair = _b2_dimensions_by_pair(scenarios)
    expected_prefix = int(config["scenario"].get("affordable_prefix_length", 1))

    for pair_id, members in sorted(requests_by_pair.items()):
        sync_request = members.get("gpfp_asap_sync")
        if sync_request is None or str(sync_request.get("scenario_kind")) != "SYNC_BATCH_STRESS":
            continue
        block_request = members.get("gpfp_asap_block")
        pair_results = results_by_pair.get(pair_id, {})
        sync_result = pair_results.get("gpfp_asap_sync")
        block_result = pair_results.get("gpfp_asap_block")
        if sync_result is None or block_result is None or block_request is None:
            continue
        if str(sync_result.get("status")) not in AUDITABLE_STATUSES:
            continue
        try:
            dimensions = dimensions_by_pair.get(pair_id)
            if dimensions is None:
                raise Ext1BObservationError(
                    f"B2 request has no frozen scenario instance: {pair_id}"
                )
            if any(
                str(request.get("scenario_cell_id"))
                != dimensions["scenario_cell_id"]
                for request in (sync_request, block_request)
            ):
                raise Ext1BObservationError(
                    f"B2 request/scenario cell mismatch for {pair_id}"
                )
            sync_trace = _trace_path(root, sync_result)
            block_trace = _trace_path(root, block_result)
            audited = audit_asap_sync_trace(
                sync_trace,
                processors=int(sync_request["M"]),
                request_id=str(sync_request["request_id"]),
                pair_id=pair_id,
            )
            controls = audit_asap_block_pair_trace(
                audited,
                block_trace,
                processors=int(block_request["M"]),
                expected_min_prefix_length=expected_prefix,
            )
            summary = summarize_b2_observations(
                audited,
                controls,
                reported_synchronization_wait_ticks=_integer(
                    sync_result.get("synchronization_wait_ticks")
                ),
                require_matched_controls=True,
            )
        except Exception as exc:
            failures.append(f"B2 {pair_id}: {exc}")
            continue

        audited_idle_ticks: set[int] = set()
        for row in audited:
            ready_but_idle = (
                _integer(row.get("idle_core_count")) > 0
                and _integer(row.get("candidate_count"))
                > _integer(row.get("actual_launch_count"))
            )
            if ready_but_idle:
                audited_idle_ticks.add(int(row["tick"]))
            decision_rows.append({
                "request_id": row["request_id"],
                "paired_instance_id": pair_id,
                "taskset_hash": row["taskset_semantic_hash"],
                "scheduler_id": row["scheduler_id"],
                **dimensions,
                "tick": row["tick"],
                "classified_state": row["classified_state"],
                "active_top_m_count": len(row.get("active_top_m_job_ids", [])),
                "continuation_count": len(row.get("continuation_job_ids", [])),
                "candidate_count": row.get("candidate_count"),
                "affordable_prefix_length": row.get("affordable_prefix_length"),
                "whole_batch_required_energy_mJ": row.get("whole_batch_required_energy_mJ"),
                "available_energy_mJ": row.get("available_energy_mJ"),
                "whole_batch_affordable": row.get("whole_batch_affordable"),
                "feasible_subset_exists": row.get("feasible_subset_exists"),
                "selected_count": row.get("selected_count"),
                "actual_launch_count": row.get("actual_launch_count"),
                "atomic_opportunity": row.get("atomic_opportunity"),
                "atomic_wait_with_affordable_member": row.get(
                    "atomic_wait_with_affordable_member"
                ),
                "ready_but_idle": ready_but_idle,
                "partial_launch_violation": row.get("partial_launch_violation"),
                "classification_errors_json": canonical_json(
                    row.get("classification_errors", [])
                ),
                "evidence_event_ids_json": canonical_json(
                    row.get("evidence_event_ids", {})
                ),
            })

        state_counts = dict(summary["state_counts"])
        unaffordable_wait = sum(state_counts[state] for state in (
            B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT_WITH_AFFORDABLE_MEMBER,
            B2_STATE_CONTINUATION_CANDIDATE_WAIT,
            B2_STATE_BATCH_UNAFFORDABLE_ENERGY_WAIT_NO_AFFORDABLE_MEMBER,
        ))
        denominator_zero = int(summary["active_batch_opportunity_count"]) == 0
        audit_closed = not any((
            summary["illegal_partial_count"],
            summary["illegal_transition_count"],
            summary["state_unclassifiable_count"],
            summary["matched_control_failure_count"],
            summary["control_evidence_incomplete_count"],
            summary["continuation_evidence_failure_count"],
            summary["synchronization_wait_ticks_mismatch_count"],
            (
                summary["atomic_wait_with_affordable_member_count"] > 0
                and summary["matched_control_success_count"] == 0
            ),
        ))
        summary_rows.append({
            "paired_instance_id": pair_id,
            **dimensions,
            "taskset_hash": sync_request["taskset_hash"],
            "input_hash": sync_request["input_hash"],
            "comparison_scope": "PRIMARY_ASAP_BLOCK_VS_ASAP_SYNC",
            "asap_block_request_id": block_request["request_id"],
            "asap_sync_request_id": sync_request["request_id"],
            "asap_block_status": block_result["status"],
            "asap_sync_status": sync_result["status"],
            "batch_candidate_decision_count": sum(
                _integer(row.get("candidate_count")) > 0 for row in audited
            ),
            "affordable_atomic_launch_count": summary["affordable_atomic_launch_count"],
            "unaffordable_atomic_wait_count": unaffordable_wait,
            "atomic_wait_with_affordable_member_count": summary[
                "atomic_wait_with_affordable_member_count"
            ],
            "illegal_partial_launch_count": summary["illegal_partial_count"],
            "illegal_transition_count": summary["illegal_transition_count"],
            "continuation_only_decision_count": state_counts[B2_STATE_CONTINUATION_ONLY],
            "unclassifiable_decision_count": state_counts[B2_STATE_UNCLASSIFIABLE],
            "active_batch_opportunity_count": summary["active_batch_opportunity_count"],
            "atomic_wait_share": None if denominator_zero else summary["atomic_wait_share"],
            "denominator_zero": denominator_zero,
            "ready_but_idle_ticks": sync_result[
                "idle_cores_while_ready_jobs_exist_ticks"
            ],
            "audited_ready_but_idle_ticks": len(audited_idle_ticks),
            "first_execution_time": sync_result["top_m_first_execution_vector"],
            "response_time": sync_result["maximum_observed_response_time"],
            "deadline_miss": sync_result["missed_jobs"],
            "asap_block_ready_but_idle_ticks": block_result[
                "idle_cores_while_ready_jobs_exist_ticks"
            ],
            "asap_block_first_execution_time": block_result[
                "top_m_first_execution_vector"
            ],
            "asap_block_response_time": block_result[
                "maximum_observed_response_time"
            ],
            "asap_block_deadline_miss": block_result["missed_jobs"],
            "matched_control_failure_count": summary["matched_control_failure_count"],
            "matched_control_success_count": summary["matched_control_success_count"],
            "control_not_applicable_count": summary["control_not_applicable_count"],
            "control_evidence_incomplete_count": summary[
                "control_evidence_incomplete_count"
            ],
            "state_counts_json": canonical_json(state_counts),
            "audit_closed": audit_closed,
            "mechanism_activated": (
                audit_closed
                and int(summary["atomic_wait_with_affordable_member_count"]) > 0
            ),
        })
        if not audit_closed:
            failures.append(f"B2 {pair_id}: batch audit did not close")
    return decision_rows, summary_rows, failures


def _b3_outputs(
    root: Path,
    scenarios: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    results: Sequence[Dict[str, Any]],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[str]]:
    event_rows: list[Dict[str, Any]] = []
    summary_rows: list[Dict[str, Any]] = []
    failures: list[str] = []
    request_index = {str(row["request_id"]): row for row in requests}
    try:
        dimensions_by_pair = _b3_dimensions_by_pair(scenarios)
    except Exception as exc:
        return event_rows, summary_rows, [f"B3 dimensions: {exc}"]

    for result in results:
        if str(result.get("scenario_kind")) != "TIMING_STRESS":
            continue
        if str(result.get("status")) not in AUDITABLE_STATUSES:
            continue
        request = request_index.get(str(result["request_id"]))
        scheduler_id = str(result["scheduler_id"])
        if request is None or scheduler_id not in TIMING_SCHEDULERS:
            failures.append(f"B3 {result.get('request_id')}: missing request/scheduler identity")
            continue
        display_name, family, policy = TIMING_SCHEDULERS[scheduler_id]
        try:
            pair_id = str(result["paired_instance_id"])
            dimensions = dimensions_by_pair.get(pair_id)
            if dimensions is None:
                raise Ext1BObservationError(
                    f"request has no frozen B3 scenario instance: {pair_id}"
                )
            if (
                str(request.get("scenario_cell_id"))
                != dimensions["scenario_cell_id"]
                or str(request.get("scenario_subtype"))
                != dimensions["timing_subtype"]
            ):
                raise Ext1BObservationError(
                    f"B3 request/scenario dimension mismatch for {pair_id}"
                )
            report = audit_timing_trace(
                _trace_path(root, result), expected_scheduler=scheduler_id
            )
        except Exception as exc:
            failures.append(f"B3 {result['request_id']}: {exc}")
            continue

        counts = Counter(finding.state for finding in report.findings)
        for finding in report.findings:
            tick, task_name, arrival = finding.identity
            event_rows.append({
                "request_id": result["request_id"],
                "paired_instance_id": result["paired_instance_id"],
                "scenario_cell_id": result["scenario_cell_id"],
                "taskset_hash": result["taskset_hash"],
                **{key: dimensions[key] for key in (
                    "normalized_utilization", "timing_subtype",
                    "nominal_energy_supply_ratio", "deadline_ratio_min",
                    "deadline_ratio_max",
                )},
                "scheduler_id": scheduler_id,
                "scheduler_family": family,
                "blocking_policy": policy,
                "time": tick,
                "task_name": task_name,
                "arrival_time": arrival,
                "job_id": f"{task_name}@{arrival}",
                "classified_state": finding.state,
                "reason": finding.reason,
            })

        release_reasons = _json_list(result.get("st_charge_release_reasons"))
        energy_recovered = sum(reason in {
            "battery_full", "battery_full_and_slack_exhausted"
        } for reason in release_reasons)
        slack_urgent = sum(reason in {
            "slack_exhausted", "battery_full_and_slack_exhausted"
        } for reason in release_reasons)
        known_release = {
            "battery_full", "slack_exhausted", "battery_full_and_slack_exhausted"
        }
        release_other = sum(reason not in known_release for reason in release_reasons)
        timing_activation = report.timing_activation
        audit_error_count = (
            len(report.errors)
            + counts[UNCLASSIFIABLE]
            + counts[ILLEGAL_TIMING_TRANSITION]
        )
        audit_closed = audit_error_count == 0
        result["timing_activation"] = timing_activation
        summary_rows.append({
            "request_id": result["request_id"],
            "paired_instance_id": result["paired_instance_id"],
            "scenario_cell_id": result["scenario_cell_id"],
            "taskset_hash": result["taskset_hash"],
            **{key: dimensions[key] for key in (
                "normalized_utilization", "timing_subtype",
                "nominal_energy_supply_ratio", "deadline_ratio_min",
                "deadline_ratio_max",
            )},
            "input_hash": result["input_hash"],
            "scheduler_id": scheduler_id,
            "scheduler_family": family,
            "blocking_policy": policy,
            "comparison_scope": (
                "PRIMARY_BLOCK" if policy == "BLOCK" else f"SECONDARY_{policy}"
            ),
            "status": result["status"],
            "asap_immediate_count": counts[ASAP_IMMEDIATE_ELIGIBILITY],
            "alap_positive_slack_defer_count": counts[ALAP_POSITIVE_SLACK_DEFER],
            "alap_urgent_eligible_count": counts[ALAP_URGENT_ELIGIBILITY],
            "st_affordable_asap_count": counts[ST_AFFORDABLE_ASAP_BEHAVIOR],
            "st_energy_insufficient_slack_wait_count": counts[
                ST_ENERGY_INSUFFICIENT_SLACK_WAIT
            ],
            "st_wait_ticks": len({
                finding.identity[0] for finding in report.findings
                if finding.state == ST_ENERGY_INSUFFICIENT_SLACK_WAIT
            }),
            "st_release_energy_recovered_count": energy_recovered,
            "st_release_slack_urgent_count": slack_urgent,
            "st_release_other_count": release_other,
            "timing_not_applicable_count": counts[NOT_APPLICABLE],
            "timing_unclassifiable_count": counts[UNCLASSIFIABLE],
            "timing_illegal_count": counts[ILLEGAL_TIMING_TRANSITION],
            "ready_but_idle_ticks": result[
                "idle_cores_while_ready_jobs_exist_ticks"
            ],
            "first_execution_time": result["top_m_first_execution_vector"],
            "response_time": result["maximum_observed_response_time"],
            "deadline_miss": result["missed_jobs"],
            "timing_activation": timing_activation,
            "audit_error_count": audit_error_count,
            "audit_closed": audit_closed,
            "same_job_transition_count": report.same_job_transition_count,
            "activation_candidate_job_count": report.activation_candidate_job_count,
            "activation_denominator_zero": report.activation_denominator_zero,
        })
        if not audit_closed:
            failures.append(
                f"B3 {result['request_id']}: timing audit did not close: "
                + "; ".join(report.errors)
            )
    return event_rows, summary_rows, failures


def write_ext1b_observation_outputs(
    root: Path,
    config: Mapping[str, Any],
) -> Dict[str, int]:
    """Rebuild trace-derived B2/B3 tables and fail closed on invalid evidence."""

    root = Path(root)
    requests = read_csv(root / "simulation_requests.csv")
    results: list[Dict[str, Any]] = read_csv(root / "simulation_results.csv")
    scenarios = read_csv(root / "scenario_instances.csv")
    b2_decisions, b2_summaries, b2_failures = _b2_outputs(
        root, config, scenarios, requests, results
    )
    b3_events, b3_summaries, b3_failures = _b3_outputs(
        root, scenarios, requests, results
    )
    write_csv(root / "b2_batch_decisions.csv", B2_DECISION_COLUMNS, b2_decisions)
    write_csv(root / "b2_summary.csv", B2_SUMMARY_COLUMNS, b2_summaries)
    write_csv(root / "b3_timing_events.csv", B3_EVENT_COLUMNS, b3_events)
    write_csv(root / "b3_summary.csv", B3_SUMMARY_COLUMNS, b3_summaries)
    if results:
        write_csv(root / "simulation_results.csv", tuple(results[0]), results)
    failures = b2_failures + b3_failures
    if failures:
        raise Ext1BObservationError("; ".join(failures))
    return {
        "b2_decision_rows": len(b2_decisions),
        "b2_summary_rows": len(b2_summaries),
        "b3_event_rows": len(b3_events),
        "b3_summary_rows": len(b3_summaries),
    }
