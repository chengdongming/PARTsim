"""Production B2/B3 observation outputs derived from retained native traces."""

from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
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
from .ext1b_b3_target_trace import (
    RECOVERY_PREFIX_MATERIAL_FIELDS,
    is_b3_target_trace_v2,
    v2_fair_input_identity,
    v2_paired_instance_identity,
    v2_request_identity,
    v2_recovery_prefix_identity,
    v2_scenario_candidate_identity,
    v2_simulation_config_identity,
    v2_taskset_hash_from_document,
)
from .ext1b_capacity_contract import capacity_feasibility_violations
from .result_writer import read_csv, write_csv
from .task_identity import runtime_job_id, runtime_task_name_for_source_id
from .taskset_store import TasksetStore, TasksetStoreError, prepare_service_curve


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
    "target_source_task_id", "target_runtime_task_name",
    "target_arrival_time", "target_job_id",
    "target_priority_rank", "target_workload", "target_unit_energy",
    "target_initial_slack", "target_recovery_contract_applicable",
    "recovery_prefix_identity", "recovery_prefix_length",
    "recovery_prefix_required_energy", "materialized_battery_capacity",
    "actual_trace_target_affordable_tick", "actual_trace_full_tick",
    "target_wait_observed",
    "target_positive_slack_transition",
    "target_transition_after_slack_exhaustion",
    "target_terminated_without_transition",
    "any_target_job_positive_transition_count",
    "later_target_job_positive_transition_count",
    "non_target_positive_transition_count", "activation_from_other_job_only",
    "target_audit_closed", "target_audit_error_count",
    "full_release_target_present", "full_release_target_selected",
    "full_release_prefix_affordable", "runtime_recovery_prefix_matches",
    "runtime_recovery_prefix_names_json", "recovery_prefix_audit_closed",
    "recovery_prefix_audit_error_count",
)
B3_CALIBRATION_COLUMNS = (
    "scenario_cell_id", "normalized_utilization", "timing_subtype",
    "configured_recovery_margin_ticks", "interpolation_rho",
    "nominal_energy_supply_ratio", "target_recovery_contract_applicable",
    "actual_trace_affordable_ticks_json",
    "actual_trace_target_affordable_ticks_json",
    "actual_trace_full_ticks_json", "target_initial_slacks_json",
    "actual_trace_recovery_headrooms_json", "recovery_prefix_identities_json",
    "recovery_prefix_lengths_json", "recovery_prefix_required_energies_json",
    "materialized_battery_capacities_json", "structurally_accepted_count",
    "structural_rejection_attempt_count", "rejection_code_counts_json",
    "target_observation_denominator", "target_wait_observed_count",
    "target_wait_observed_ratio", "target_positive_slack_transition_count",
    "target_positive_slack_transition_ratio",
    "later_target_job_positive_transition_count",
    "later_target_substitution_count", "later_target_substitution_ratio",
    "non_target_positive_transition_count", "non_target_substitution_count",
    "non_target_substitution_ratio",
    "activation_from_other_job_only_count",
    "activation_from_other_job_only_ratio",
    "target_transition_after_slack_exhaustion_count",
    "target_transition_after_slack_exhaustion_ratio",
    "target_terminated_without_transition_count",
    "target_terminated_without_transition_ratio", "target_audit_closed_count",
    "target_audit_error_count", "full_release_prefix_affordable_count",
    "full_release_prefix_affordable_ratio",
    "recovery_prefix_audit_closed_count",
    "recovery_prefix_audit_error_count",
    "rejected_capacity_infeasible_task_count",
    "rejected_capacity_infeasible_taskset_count",
    "accepted_capacity_infeasible_task_count",
    "accepted_capacity_infeasible_taskset_count",
    "identity_shape_audit_closed", "taskset_hash_audit_closed",
    "scenario_candidate_identity_audit_closed",
    "paired_instance_identity_audit_closed", "request_hash_audit_closed",
    "taskset_store_manifest_audit_closed",
    "output_file_hash_verification_closed", "hash_audit_closed",
    "pairing_audit_closed", "workload_audit_closed",
    "source_index_audit_closed", "calibration_unit_audit_closed",
)


class Ext1BObservationError(RuntimeError):
    """A retained trace cannot support the requested scientific observation."""


def _integer(value: Any, default: int = 0) -> int:
    if value in {None, "", UNAVAILABLE}:
        return default
    return int(value)


def _bool_value(value: Any) -> bool:
    return value is True or str(value).strip().upper() in {"TRUE", "1"}


def _ratio(numerator: int, denominator: int) -> Any:
    return numerator / denominator if denominator else UNAVAILABLE


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
) -> Dict[str, Dict[str, Any]]:
    dimensions: Dict[str, Dict[str, Any]] = {}
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
        scenario_contract_id = str(row.get("scenario_contract_id", ""))
        if scenario_contract_id:
            dimensions[pair_id]["scenario_contract_id"] = scenario_contract_id
            required_target = (
                "target_source_task_id", "target_runtime_task_name",
                "target_arrival_time", "target_job_id",
                "target_priority_rank", "target_workload",
                "target_unit_energy", "target_initial_slack",
                "target_recovery_contract_applicable",
                "recovery_prefix_identity", "recovery_prefix_length",
                "recovery_prefix_required_energy",
                "materialized_battery_capacity",
                "actual_trace_target_affordable_tick",
                "actual_trace_full_tick",
            )
            missing = [key for key in required_target if key not in structure]
            if missing:
                raise Ext1BObservationError(
                    f"missing B3-v2 target identity fields for {pair_id}: {missing}"
                )
            source_id = str(structure["target_source_task_id"])
            runtime_name = str(structure["target_runtime_task_name"])
            if runtime_name != runtime_task_name_for_source_id(source_id):
                raise Ext1BObservationError(
                    f"B3-v2 target source/runtime identity mismatch for {pair_id}"
                )
            arrival_time = int(structure["target_arrival_time"])
            job_id = str(structure["target_job_id"])
            if arrival_time != 0 or job_id != runtime_job_id(
                runtime_name, arrival_time,
            ):
                raise Ext1BObservationError(
                    f"B3-v2 target job identity mismatch for {pair_id}"
                )
            if (
                str(row.get("target_runtime_task_name", "")) != runtime_name
                or _integer(row.get("target_arrival_time"), -1) != arrival_time
                or str(row.get("target_job_id", "")) != job_id
            ):
                raise Ext1BObservationError(
                    f"B3-v2 scenario/structure target identity mismatch for {pair_id}"
                )
            applicable = _bool_value(
                structure["target_recovery_contract_applicable"]
            )
            recovery_names = list(
                structure.get("recovery_prefix_runtime_names", [])
            )
            recovery_names_json = canonical_json(recovery_names)
            recovery_fields = {
                "target_recovery_contract_applicable": applicable,
                "recovery_prefix_identity": str(
                    structure["recovery_prefix_identity"]
                ),
                "recovery_prefix_length": int(
                    structure["recovery_prefix_length"]
                ),
                "recovery_prefix_runtime_names_json": recovery_names_json,
                "recovery_prefix_required_energy": str(
                    structure["recovery_prefix_required_energy"]
                ),
                "materialized_battery_capacity": str(
                    structure["materialized_battery_capacity"]
                ),
                "actual_trace_target_affordable_tick": int(
                    structure["actual_trace_target_affordable_tick"]
                ),
                "actual_trace_full_tick": int(
                    structure["actual_trace_full_tick"]
                ),
            }
            if any(
                str(row.get(key, "")) != str(value)
                for key, value in recovery_fields.items()
                if key != "target_recovery_contract_applicable"
            ) or _bool_value(row.get(
                "target_recovery_contract_applicable"
            )) != applicable:
                raise Ext1BObservationError(
                    f"B3-v2 scenario/structure recovery prefix mismatch for {pair_id}"
                )
            if _dimension_text(
                row.get("battery_capacity"), "scenario battery capacity"
            ) != _dimension_text(
                structure["materialized_battery_capacity"],
                "materialized battery capacity",
            ):
                raise Ext1BObservationError(
                    f"B3-v2 scenario recovery capacity mismatch for {pair_id}"
                )
            if applicable:
                prefix_missing = [
                    key for key in RECOVERY_PREFIX_MATERIAL_FIELDS
                    if key not in structure
                ]
                if prefix_missing:
                    raise Ext1BObservationError(
                        f"missing B3-v2 recovery prefix fields for {pair_id}: "
                        f"{prefix_missing}"
                    )
                try:
                    expected_prefix = v2_recovery_prefix_identity(structure)
                except (KeyError, TypeError, ValueError) as exc:
                    raise Ext1BObservationError(
                        f"invalid B3-v2 recovery prefix for {pair_id}"
                    ) from exc
                if (
                    expected_prefix != recovery_fields["recovery_prefix_identity"]
                    or recovery_fields["recovery_prefix_length"]
                    != len(recovery_names)
                    or not recovery_names
                    or recovery_names[0] != runtime_name
                    or structure["recovery_prefix_affordable_at_full"] is not True
                    or structure["target_blocked_at_initial_energy"] is not True
                ):
                    raise Ext1BObservationError(
                        f"B3-v2 recovery prefix contract mismatch for {pair_id}"
                    )
            elif any((
                recovery_fields["recovery_prefix_identity"],
                recovery_fields["recovery_prefix_length"],
                recovery_names,
                recovery_fields["recovery_prefix_required_energy"],
            )):
                raise Ext1BObservationError(
                    f"B3-v2 non-applicable recovery prefix is non-empty for {pair_id}"
                )
            dimensions[pair_id].update({
                "target_source_task_id": source_id,
                "target_runtime_task_name": runtime_name,
                "target_arrival_time": arrival_time,
                "target_job_id": job_id,
                "target_priority_rank": int(structure["target_priority_rank"]),
                "target_workload": str(structure["target_workload"]),
                "target_unit_energy": _dimension_text(
                    structure["target_unit_energy"],
                    f"B3-v2 target unit energy for {pair_id}",
                ),
                "target_initial_slack": int(structure["target_initial_slack"]),
                **recovery_fields,
                "recovery_prefix_runtime_names": recovery_names,
            })
        if not scenario_cell_id or not timing_subtype:
            raise Ext1BObservationError(
                f"missing B3 scenario identity for {pair_id}"
            )
    return dimensions


def _validate_b3_target_identity(
    dimensions: Mapping[str, Any],
    *sources: Mapping[str, Any],
) -> None:
    fields = (
        "target_runtime_task_name", "target_arrival_time", "target_job_id",
        "target_recovery_contract_applicable", "recovery_prefix_identity",
        "recovery_prefix_length", "recovery_prefix_runtime_names_json",
        "recovery_prefix_required_energy", "materialized_battery_capacity",
        "actual_trace_target_affordable_tick", "actual_trace_full_tick",
    )
    if any(
        str(source.get(key, "")) != str(dimensions[key])
        for source in sources
        for key in fields
    ):
        raise Ext1BObservationError(
            "B3 request/result/scenario target identity mismatch"
        )


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
    config: Mapping[str, Any],
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
            expected_contract = str(
                config["scenario"].get("scenario_contract_id", "")
            )
            if (
                str(request.get("scenario_contract_id", ""))
                != expected_contract
                or str(dimensions.get("scenario_contract_id", ""))
                != expected_contract
            ):
                raise Ext1BObservationError(
                    f"B3 request/scenario contract mismatch for {pair_id}"
                )
            target_trace_v2 = is_b3_target_trace_v2(config)
            if target_trace_v2:
                _validate_b3_target_identity(dimensions, request, result)
            applicable = (
                target_trace_v2
                and _bool_value(
                    dimensions["target_recovery_contract_applicable"]
                )
            )
            report = audit_timing_trace(
                _trace_path(root, result),
                expected_scheduler=scheduler_id,
                target_runtime_task_name=(
                    str(dimensions["target_runtime_task_name"])
                    if target_trace_v2
                    else None
                ),
                target_arrival_time=(
                    int(dimensions["target_arrival_time"])
                    if target_trace_v2
                    else None
                ),
                target_recovery_contract_applicable=(
                    applicable if target_trace_v2 else None
                ),
                recovery_prefix_identity=(
                    str(dimensions["recovery_prefix_identity"])
                    if applicable else None
                ),
                recovery_prefix_runtime_names=(
                    dimensions["recovery_prefix_runtime_names"]
                    if applicable else ()
                ),
                recovery_prefix_required_energy=(
                    str(dimensions["recovery_prefix_required_energy"])
                    if applicable else None
                ),
                materialized_battery_capacity=(
                    str(dimensions["materialized_battery_capacity"])
                    if applicable else None
                ),
                actual_trace_full_tick=(
                    int(dimensions["actual_trace_full_tick"])
                    if applicable else None
                ),
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
        target_fields = {
            "target_wait_observed": report.target_wait_observed,
            "target_positive_slack_transition": (
                report.target_positive_slack_transition
            ),
            "target_transition_after_slack_exhaustion": (
                report.target_transition_after_slack_exhaustion
            ),
            "target_terminated_without_transition": (
                report.target_terminated_without_transition
            ),
            "any_target_job_positive_transition_count": (
                report.any_target_job_positive_transition_count
            ),
            "later_target_job_positive_transition_count": (
                report.later_target_job_positive_transition_count
            ),
            "non_target_positive_transition_count": (
                report.non_target_positive_transition_count
            ),
            "activation_from_other_job_only": (
                report.activation_from_other_job_only
            ),
            "target_audit_closed": report.target_audit_closed,
            "target_audit_error_count": report.target_audit_error_count,
        }
        if target_trace_v2 and not applicable:
            target_fields = {key: UNAVAILABLE for key in target_fields}
        prefix_runtime_fields = {
            "full_release_target_present": (
                report.full_release_target_present
            ),
            "full_release_target_selected": (
                report.full_release_target_selected
            ),
            "full_release_prefix_affordable": (
                report.full_release_prefix_affordable
            ),
            "runtime_recovery_prefix_matches": (
                report.runtime_recovery_prefix_matches
            ),
            "runtime_recovery_prefix_names_json": canonical_json(
                report.runtime_recovery_prefix_names
            ),
            "recovery_prefix_audit_closed": (
                report.recovery_prefix_audit_closed
            ),
            "recovery_prefix_audit_error_count": (
                report.recovery_prefix_audit_error_count
            ),
        }
        if not applicable or family != "ST":
            prefix_runtime_fields = {
                key: UNAVAILABLE for key in prefix_runtime_fields
            }
        if target_trace_v2:
            result.update(target_fields)
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
            **({key: dimensions[key] for key in (
                "target_source_task_id", "target_runtime_task_name",
                "target_arrival_time", "target_job_id",
                "target_priority_rank", "target_workload",
                "target_unit_energy", "target_initial_slack",
                "target_recovery_contract_applicable",
                "recovery_prefix_identity", "recovery_prefix_length",
                "recovery_prefix_required_energy",
                "materialized_battery_capacity",
                "actual_trace_target_affordable_tick",
                "actual_trace_full_tick",
            )} if target_trace_v2 else {
                key: UNAVAILABLE for key in (
                    "target_source_task_id", "target_runtime_task_name",
                    "target_arrival_time", "target_job_id",
                    "target_priority_rank", "target_workload",
                    "target_unit_energy", "target_initial_slack",
                    "target_recovery_contract_applicable",
                    "recovery_prefix_identity", "recovery_prefix_length",
                    "recovery_prefix_required_energy",
                    "materialized_battery_capacity",
                    "actual_trace_target_affordable_tick",
                    "actual_trace_full_tick",
                )
            }),
            **(target_fields if target_trace_v2 else {
                key: UNAVAILABLE for key in target_fields
            }),
            **prefix_runtime_fields,
        })
        if not audit_closed:
            failures.append(
                f"B3 {result['request_id']}: timing audit did not close: "
                + report.closure_diagnostic(
                    request_id=str(result["request_id"]),
                    scheduler_id=scheduler_id,
                    sample_limit=5,
                )
            )
    return event_rows, summary_rows, failures


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _persisted_path(root: Path, value: Any) -> Path:
    supplied = Path(str(value))
    candidates = [supplied]
    if not supplied.is_absolute():
        candidates.extend((root / supplied, root.parent / supplied))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _identity_shape(value: Any) -> bool:
    text = str(value)
    if len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def _store_manifest_audit(
    root: Path,
    config: Mapping[str, Any],
) -> tuple[bool, Dict[str, Mapping[str, Any]]]:
    try:
        service = prepare_service_curve(config, root / "generation_service")
        store = TasksetStore(
            Path(str(config["execution"]["taskset_store"])), config, service,
        )
        store.verify_pairing_manifest(require_complete=False)
        manifest = store.manifest_document()
        entries = {
            str(row["taskset_id"]): row for row in manifest["entries"]
        }
        if len(entries) != len(manifest["entries"]):
            raise TasksetStoreError("duplicate taskset ID in pairing manifest")
        return True, entries
    except (KeyError, OSError, TasksetStoreError, ValueError):
        return False, {}


def _v2_identity_audit(
    root: Path,
    config: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
    generated_by_taskset: Mapping[str, Mapping[str, Any]],
    requests_by_pair: Mapping[str, Mapping[str, Mapping[str, Any]]],
    attempts: Sequence[Mapping[str, Any]],
    store_audit_closed: bool,
    store_entries: Mapping[str, Mapping[str, Any]],
    output_file_hash_verification_closed: bool,
) -> Dict[str, bool]:
    shape_closed = True
    taskset_closed = True
    candidate_closed = True
    paired_closed = True
    request_closed = True
    pairing_closed = True
    scheduler_ids = set(config["scheduler_ids"])

    for instance in instances:
        pair_id = str(instance["paired_instance_id"])
        taskset_id = str(instance["taskset_id"])
        generated = generated_by_taskset.get(taskset_id)
        if generated is None:
            taskset_closed = candidate_closed = paired_closed = False
            request_closed = pairing_closed = False
            continue
        try:
            structure = json.loads(str(instance["structure_json"]))
            canonical_path = _persisted_path(
                root, generated["canonical_taskset_json"],
            )
            canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            recomputed_taskset = v2_taskset_hash_from_document(canonical)
            expected_recovery = {
                "target_recovery_contract_applicable": _bool_value(
                    structure["target_recovery_contract_applicable"]
                ),
                "recovery_prefix_identity": str(
                    structure["recovery_prefix_identity"]
                ),
                "recovery_prefix_length": int(
                    structure["recovery_prefix_length"]
                ),
                "recovery_prefix_runtime_names_json": canonical_json(
                    structure.get("recovery_prefix_runtime_names", [])
                ),
                "recovery_prefix_required_energy": str(
                    structure["recovery_prefix_required_energy"]
                ),
                "materialized_battery_capacity": str(
                    structure["materialized_battery_capacity"]
                ),
                "actual_trace_target_affordable_tick": int(
                    structure["actual_trace_target_affordable_tick"]
                ),
                "actual_trace_full_tick": int(
                    structure["actual_trace_full_tick"]
                ),
            }
            explicit_recovery_closed = all(
                str(source.get(key, "")) == str(value)
                for source in (generated, instance)
                for key, value in expected_recovery.items()
            )
            taskset_closed = taskset_closed and all((
                recomputed_taskset == str(canonical.get("taskset_hash")),
                recomputed_taskset == str(generated["taskset_hash"]),
                recomputed_taskset == str(instance["taskset_hash"]),
                str(canonical.get("taskset_id")) == taskset_id,
                canonical.get("tasks") == json.loads(
                    str(generated["task_input_json"])
                ),
                canonical.get("structure") == structure,
                explicit_recovery_closed,
            ))

            scenario_cell = structure["timing_dimensions"]
            recomputed_candidate = v2_scenario_candidate_identity(
                scenario_cell=scenario_cell,
                source_taskset_hash=str(generated["source_taskset_hash"]),
                logical_taskset_index=int(instance["logical_taskset_index"]),
                attempt_index=int(generated["accepted_attempt_index"]),
                capacity_feasibility_contract_identity=str(
                    instance["capacity_feasibility_contract_identity"]
                ),
                trace_hash=str(instance["trace_hash"]),
                structure=structure,
            )
            candidate_closed = candidate_closed and all((
                recomputed_candidate
                == str(instance["scenario_candidate_identity"]),
                recomputed_candidate
                == str(generated["scenario_candidate_identity"]),
                recomputed_candidate
                == str(canonical.get("scenario_candidate_identity")),
            ))

            recomputed_pair = v2_paired_instance_identity(
                scenario_cell=scenario_cell,
                logical_taskset_index=int(instance["logical_taskset_index"]),
                taskset_hash=recomputed_taskset,
                trace_hash=str(instance["trace_hash"]),
                initial_battery=str(instance["initial_battery"]),
                battery_capacity=str(instance["battery_capacity"]),
                processors=int(instance["M"]),
                horizon=int(instance["horizon"]),
                scenario_candidate_identity=recomputed_candidate,
                capacity_feasibility_contract_identity=str(
                    instance["capacity_feasibility_contract_identity"]
                ),
            )
            paired_closed = paired_closed and recomputed_pair == pair_id

            members = requests_by_pair.get(pair_id, {})
            pairing_closed = pairing_closed and set(members) == scheduler_ids
            system_hash = _sha256_file(_persisted_path(
                root, instance["system_template_path"],
            ))
            expected_simulation_hash = v2_simulation_config_identity(
                simulation=config["simulation"],
                initial_battery=str(instance["initial_battery"]),
                battery_capacity=str(instance["battery_capacity"]),
                allow_harvest_clipping=_bool_value(
                    instance["allow_harvest_clipping"]
                ),
                system_template_hash=system_hash,
            )
            input_hashes = set()
            for scheduler_id, request in members.items():
                fair_material = {
                    "taskset_hash": str(request["taskset_hash"]),
                    "trace_hash": str(request["trace_hash"]),
                    "simulation_config_hash": str(
                        request["simulation_config_hash"]
                    ),
                    "generation_seed": int(request["generation_seed"]),
                    "M": int(request["M"]),
                    "initial_battery": str(request["initial_battery"]),
                    "battery_capacity": str(request["battery_capacity"]),
                    "horizon": int(request["horizon"]),
                    "maximum_horizon": int(request["maximum_horizon"]),
                    "priority_hash": str(request["priority_hash"]),
                    "power_hash": str(request["power_hash"]),
                    "deadline_hash": str(request["deadline_hash"]),
                    "release_hash": str(request["release_hash"]),
                    "workload_vector_hash": str(
                        request["workload_vector_hash"]
                    ),
                    "simulator_build_hash": str(
                        request["simulator_build_hash"]
                    ),
                    "scenario_contract_id": str(
                        request["scenario_contract_id"]
                    ),
                    "target_runtime_task_name": str(
                        request["target_runtime_task_name"]
                    ),
                    "target_arrival_time": int(
                        request["target_arrival_time"]
                    ),
                    "target_job_id": str(request["target_job_id"]),
                    **expected_recovery,
                }
                expected_input_hash = v2_fair_input_identity(fair_material)
                expected_request_id = v2_request_identity(
                    paired_instance_id=pair_id,
                    scheduler_id=scheduler_id,
                    capacity_feasibility_contract_identity=str(
                        request["capacity_feasibility_contract_identity"]
                    ),
                    target_runtime_task_name=str(
                        request["target_runtime_task_name"]
                    ),
                    target_arrival_time=int(request["target_arrival_time"]),
                    target_job_id=str(request["target_job_id"]),
                    recovery_contract=expected_recovery,
                )
                request_closed = request_closed and all((
                    str(request["simulation_config_hash"])
                    == expected_simulation_hash,
                    str(request["input_hash"]) == expected_input_hash,
                    str(request["request_id"]) == expected_request_id,
                    str(request["paired_instance_id"]) == recomputed_pair,
                    str(request["taskset_hash"]) == recomputed_taskset,
                    all(
                        str(request.get(key, "")) == str(value)
                        for key, value in expected_recovery.items()
                    ),
                ))
                input_hashes.add(str(request["input_hash"]))
            pairing_closed = pairing_closed and len(input_hashes) == 1
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            taskset_closed = candidate_closed = paired_closed = False
            request_closed = pairing_closed = False

        shape_closed = shape_closed and all(
            _identity_shape(instance.get(field, ""))
            for field in (
                "paired_instance_id", "taskset_hash", "trace_hash",
                "priority_hash", "power_hash", "deadline_hash",
                "release_hash", "scenario_candidate_identity",
            )
        )
        if _bool_value(instance.get("target_recovery_contract_applicable")):
            shape_closed = shape_closed and _identity_shape(
                instance.get("recovery_prefix_identity", "")
            )

    expected_store_rows = [
        row for row in attempts if str(row.get("source_taskset_id", ""))
    ]
    store_audit_closed = store_audit_closed and all(
        str(store_entries.get(str(row["source_taskset_id"]), {}).get(
            "taskset_semantic_hash", ""
        )) == str(row["source_taskset_hash"])
        and int(store_entries.get(str(row["source_taskset_id"]), {}).get(
            "generation_seed", -1
        ))
        == int(row["generation_seed"])
        for row in expected_store_rows
    )
    hash_closed = all((
        taskset_closed, candidate_closed, paired_closed, request_closed,
        store_audit_closed, output_file_hash_verification_closed,
    ))
    return {
        "identity_shape_audit_closed": shape_closed,
        "taskset_hash_audit_closed": taskset_closed,
        "scenario_candidate_identity_audit_closed": candidate_closed,
        "paired_instance_identity_audit_closed": paired_closed,
        "request_hash_audit_closed": request_closed,
        "taskset_store_manifest_audit_closed": store_audit_closed,
        "output_file_hash_verification_closed": (
            output_file_hash_verification_closed
        ),
        "hash_audit_closed": hash_closed,
        "pairing_audit_closed": pairing_closed,
    }


def _b3_calibration_rows(
    root: Path,
    config: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    generation_attempts: Sequence[Mapping[str, Any]],
    generated_tasksets: Sequence[Mapping[str, Any]],
    *,
    output_file_hash_verification_closed: bool,
) -> list[Dict[str, Any]]:
    """Return one independent audit/result row per v2 calibration unit."""

    if not is_b3_target_trace_v2(config):
        return []
    retry_limit = int(config["scenario"]["structural_retry_limit"])
    workload_contract = config["generation"]["workload_contract"]
    energy_by_workload = {
        str(row["workload"]): _dimension_text(row["energy_per_tick"], "power")
        for row in workload_contract["power_model"]
    }
    generated_by_taskset = {
        str(row["taskset_id"]): row for row in generated_tasksets
    }
    requests_by_pair = _by_pair(requests)
    scenario_groups: Dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in scenarios:
        scenario_groups[(
            str(row["scenario_cell_id"]),
            _dimension_text(row["normalized_utilization"], "normalized utilization"),
        )].append(row)
    attempts_by_unit: Dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in generation_attempts:
        attempts_by_unit[(
            str(row["scenario_cell_id"]),
            _dimension_text(row["normalized_utilization"], "attempt utilization"),
        )].append(row)
    summary_by_unit: Dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in summaries:
        if str(row.get("scheduler_id")) != "gpfp_st_block":
            continue
        summary_by_unit[(
            str(row["scenario_cell_id"]),
            _dimension_text(row["normalized_utilization"], "summary utilization"),
        )].append(row)
    store_audit_closed, store_entries = _store_manifest_audit(root, config)

    rows = []
    for key, instances in sorted(scenario_groups.items()):
        cell_id, utilization = key
        first_structure = json.loads(str(instances[0]["structure_json"]))
        timing = first_structure["timing_dimensions"]
        recovery_applicable = _bool_value(
            first_structure["target_recovery_contract_applicable"]
        )
        attempts = attempts_by_unit.get(key, [])
        st_summaries = summary_by_unit.get(key, [])
        if not recovery_applicable:
            st_summaries = []
        rejected = [
            row for row in attempts
            if str(row.get("attempt_status")) == "REJECTED"
        ]
        rejection_codes = Counter(str(row.get("rejection_code")) for row in rejected)

        def count(field: str) -> int:
            return sum(_bool_value(row.get(field)) for row in st_summaries)

        denominator = len(st_summaries)
        target_wait = count("target_wait_observed")
        target_positive = count("target_positive_slack_transition")
        later_positive_jobs = sum(
            _integer(row.get("later_target_job_positive_transition_count"))
            for row in st_summaries
        )
        later_substitution = sum(
            _integer(row.get("later_target_job_positive_transition_count")) > 0
            and not _bool_value(row.get("target_positive_slack_transition"))
            for row in st_summaries
        )
        non_target_positive = sum(
            _integer(row.get("non_target_positive_transition_count"))
            for row in st_summaries
        )
        non_target_substitution = sum(
            _integer(row.get("non_target_positive_transition_count")) > 0
            and not _bool_value(row.get("target_positive_slack_transition"))
            for row in st_summaries
        )
        other_only = count("activation_from_other_job_only")
        exhausted = count("target_transition_after_slack_exhaustion")
        terminated = count("target_terminated_without_transition")

        identity_audits = _v2_identity_audit(
            root,
            config,
            instances,
            generated_by_taskset,
            requests_by_pair,
            attempts,
            store_audit_closed,
            store_entries,
            output_file_hash_verification_closed,
        )
        workload_audit = True
        for instance in instances:
            generated = generated_by_taskset.get(str(instance["taskset_id"]))
            if generated is None:
                workload_audit = False
                break
            try:
                tasks = json.loads(str(generated["task_input_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                workload_audit = False
                break
            if any(
                str(task.get("workload")) not in energy_by_workload
                or str(task.get("workload")) == "idle"
                or _dimension_text(task.get("P"), "task power")
                != energy_by_workload.get(str(task.get("workload")))
                for task in tasks
            ):
                workload_audit = False
                break
        source_index_audit = all(
            int(row["source_taskset_index"])
            == int(row["logical_taskset_index"]) * retry_limit
            + int(row["attempt_index"])
            and int(row["source_index"]) == int(row["source_taskset_index"])
            and int(row["logical_index"]) == int(row["logical_taskset_index"])
            for row in attempts
        )
        rejected_capacity_tasks = sum(
            _integer(row.get("capacity_infeasible_task_count")) for row in rejected
        )
        rejected_capacity_tasksets = sum(
            _integer(row.get("capacity_infeasible_taskset_count")) for row in rejected
        )
        accepted_capacity_tasks = 0
        accepted_capacity_tasksets = 0
        for instance in instances:
            generated = generated_by_taskset.get(str(instance["taskset_id"]))
            if generated is None:
                accepted_capacity_tasksets += 1
                continue
            try:
                tasks = json.loads(str(generated["task_input_json"]))
                violations = capacity_feasibility_violations(
                    tasks, Fraction(str(instance["battery_capacity"])), config,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                accepted_capacity_tasksets += 1
                continue
            accepted_capacity_tasks += len(violations)
            accepted_capacity_tasksets += bool(violations)
        target_audit_closed = count("target_audit_closed")
        target_audit_errors = sum(
            _integer(row.get("target_audit_error_count")) for row in st_summaries
        )
        prefix_affordable = count("full_release_prefix_affordable")
        prefix_audit_closed = count("recovery_prefix_audit_closed")
        prefix_audit_errors = sum(
            _integer(row.get("recovery_prefix_audit_error_count"))
            for row in st_summaries
        )
        audit_closed = all((
            identity_audits["hash_audit_closed"],
            identity_audits["pairing_audit_closed"],
            workload_audit,
            source_index_audit,
            target_audit_closed == denominator,
            target_audit_errors == 0,
            not recovery_applicable
            or prefix_affordable == denominator,
            not recovery_applicable
            or prefix_audit_closed == denominator,
            prefix_audit_errors == 0,
            accepted_capacity_tasks == 0,
            accepted_capacity_tasksets == 0,
        ))
        structures = [
            json.loads(str(instance["structure_json"]))
            for instance in instances
        ]
        rows.append({
            "scenario_cell_id": cell_id,
            "normalized_utilization": utilization,
            "timing_subtype": str(instances[0]["scenario_subtype"]),
            "configured_recovery_margin_ticks": timing[
                "recovery_margin_ticks"
            ],
            "interpolation_rho": timing["interpolation_rho"],
            "nominal_energy_supply_ratio": timing[
                "nominal_energy_supply_ratio"
            ],
            "target_recovery_contract_applicable": recovery_applicable,
            "actual_trace_affordable_ticks_json": canonical_json([
                int(structure["actual_trace_affordable_tick"])
                for structure in structures
            ]),
            "actual_trace_target_affordable_ticks_json": canonical_json([
                int(structure["actual_trace_target_affordable_tick"])
                for structure in structures
            ]),
            "actual_trace_full_ticks_json": canonical_json([
                int(structure["actual_trace_full_tick"])
                for structure in structures
            ]),
            "target_initial_slacks_json": canonical_json([
                int(structure["target_initial_slack"])
                for structure in structures
            ]),
            "actual_trace_recovery_headrooms_json": canonical_json([
                int(structure["actual_trace_recovery_headroom"])
                for structure in structures
            ]),
            "recovery_prefix_identities_json": canonical_json([
                str(structure["recovery_prefix_identity"])
                for structure in structures
            ]),
            "recovery_prefix_lengths_json": canonical_json([
                int(structure["recovery_prefix_length"])
                for structure in structures
            ]),
            "recovery_prefix_required_energies_json": canonical_json([
                str(structure["recovery_prefix_required_energy"])
                for structure in structures
            ]),
            "materialized_battery_capacities_json": canonical_json([
                str(structure["materialized_battery_capacity"])
                for structure in structures
            ]),
            "structurally_accepted_count": len(instances),
            "structural_rejection_attempt_count": len(rejected),
            "rejection_code_counts_json": canonical_json(rejection_codes),
            "target_observation_denominator": denominator,
            "target_wait_observed_count": target_wait,
            "target_wait_observed_ratio": _ratio(target_wait, denominator),
            "target_positive_slack_transition_count": target_positive,
            "target_positive_slack_transition_ratio": _ratio(
                target_positive, denominator
            ),
            "later_target_job_positive_transition_count": later_positive_jobs,
            "later_target_substitution_count": later_substitution,
            "later_target_substitution_ratio": _ratio(
                later_substitution, denominator
            ),
            "non_target_positive_transition_count": non_target_positive,
            "non_target_substitution_count": non_target_substitution,
            "non_target_substitution_ratio": _ratio(
                non_target_substitution, denominator
            ),
            "activation_from_other_job_only_count": other_only,
            "activation_from_other_job_only_ratio": _ratio(
                other_only, denominator
            ),
            "target_transition_after_slack_exhaustion_count": exhausted,
            "target_transition_after_slack_exhaustion_ratio": _ratio(
                exhausted, denominator
            ),
            "target_terminated_without_transition_count": terminated,
            "target_terminated_without_transition_ratio": _ratio(
                terminated, denominator
            ),
            "target_audit_closed_count": target_audit_closed,
            "target_audit_error_count": target_audit_errors,
            "full_release_prefix_affordable_count": prefix_affordable,
            "full_release_prefix_affordable_ratio": _ratio(
                prefix_affordable, denominator
            ),
            "recovery_prefix_audit_closed_count": prefix_audit_closed,
            "recovery_prefix_audit_error_count": prefix_audit_errors,
            "rejected_capacity_infeasible_task_count": (
                rejected_capacity_tasks
            ),
            "rejected_capacity_infeasible_taskset_count": (
                rejected_capacity_tasksets
            ),
            "accepted_capacity_infeasible_task_count": (
                accepted_capacity_tasks
            ),
            "accepted_capacity_infeasible_taskset_count": (
                accepted_capacity_tasksets
            ),
            **identity_audits,
            "workload_audit_closed": workload_audit,
            "source_index_audit_closed": source_index_audit,
            "calibration_unit_audit_closed": audit_closed,
        })
    return rows


def write_ext1b_observation_outputs(
    root: Path,
    config: Mapping[str, Any],
    *,
    output_file_hash_verification_closed: bool = False,
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
        root, config, scenarios, requests, results
    )
    write_csv(root / "b2_batch_decisions.csv", B2_DECISION_COLUMNS, b2_decisions)
    write_csv(root / "b2_summary.csv", B2_SUMMARY_COLUMNS, b2_summaries)
    write_csv(root / "b3_timing_events.csv", B3_EVENT_COLUMNS, b3_events)
    write_csv(root / "b3_summary.csv", B3_SUMMARY_COLUMNS, b3_summaries)
    calibration_rows = _b3_calibration_rows(
        root,
        config,
        scenarios,
        requests,
        b3_summaries,
        read_csv(root / "generation_attempts.csv"),
        read_csv(root / "generated_tasksets.csv"),
        output_file_hash_verification_closed=(
            output_file_hash_verification_closed
        ),
    )
    if is_b3_target_trace_v2(config):
        write_csv(
            root / "b3_calibration_summary.csv",
            B3_CALIBRATION_COLUMNS,
            calibration_rows,
        )
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
        **({
            "b3_calibration_summary_rows": len(calibration_rows),
        } if is_b3_target_trace_v2(config) else {}),
    }
