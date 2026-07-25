"""Versioned, independent CSV schema for the v9.3 RTA4 formal profile."""

from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

from .rta4_formal_config import (
    RTA4_FORMAL_SCHEMA_VERSION, canonical_json, domain_hash,
)


RTA4_FORMAL_SCHEMA_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_SCHEMA:v1"
RTA4_FORMAL_SCHEMA_MANIFEST = "formal_schema_manifest.json"

COMMON = ("schema_version", "schema_sha256", "plan_sha256", "config_semantic_hash")


_TABLES: Dict[str, Tuple[str, ...]] = {
    "formal_cells.csv": COMMON + (
        "cell_id", "core", "scenario", "axis", "axis_value",
        "processor_count", "task_count", "normalized_utilization", "exact_e0",
        "service_scale", "power_scale", "deadline_variant", "generation_status",
    ),
    "formal_taskset_skeletons.csv": COMMON + (
        "generation_request_id", "taskset_skeleton_id", "formal_seed",
        "processor_count", "task_count", "target_normalized_utilization",
        "actual_normalized_utilization", "generation_status",
        "priority_identity", "base_power_vector_identity", "certificate_path",
        "certificate_sha256",
    ),
    "formal_tasksets.csv": COMMON + (
        "generation_request_id", "taskset_skeleton_id", "taskset_id",
        "taskset_hash", "power_vector_hash", "priority_identity",
        "deadline_variant", "power_variant", "normalized_utilization",
        "normalized_density", "generation_status", "certificate_path",
        "certificate_sha256",
    ),
    "formal_tasks.csv": COMMON + (
        "taskset_skeleton_id", "taskset_id", "task_id", "priority_rank",
        "C", "D", "T", "P_exact", "D_over_T",
        "deadline_slack_fraction", "deadline_variant", "power_variant",
    ),
    "formal_rta_requests.csv": COMMON + (
        "plan_record_id", "analysis_id", "request_id", "execution_run_id", "cell_id",
        "taskset_skeleton_slot_id", "taskset_slot_id", "taskset_skeleton_id",
        "taskset_id", "taskset_hash", "method",
        "method_role", "carry_policy", "exact_e0", "service_identity",
        "power_vector_hash", "theory_document_sha256",
        "numeric_contract_sha256", "exact_input_identity", "timeout_contract",
        "source_analysis_id", "scenario", "axis", "axis_value",
        "service_scale", "power_scale", "deadline_variant", "worker_count",
        "request_status",
    ),
    "formal_rta_attempts.csv": COMMON + (
        "attempt_id", "plan_record_id", "analysis_id", "request_id",
        "execution_run_id", "attempt_number",
        "parent_attempt_id", "worker_count", "timeout_budget_seconds",
        "solver_status", "failure_origin", "runtime_wall_seconds",
        "runtime_cpu_seconds", "peak_rss_bytes", "started_at_utc",
    ),
    "formal_rta_taskset_results.csv": COMMON + (
        "plan_record_id", "analysis_id", "request_id", "execution_run_id", "cell_id",
        "taskset_skeleton_slot_id", "taskset_slot_id", "taskset_skeleton_id",
        "taskset_id", "taskset_hash", "method",
        "method_role", "carry_policy", "exact_e0", "service_identity",
        "power_vector_hash", "theory_document_sha256",
        "numeric_contract_sha256", "exact_input_identity", "timeout_contract",
        "solver_status", "taskset_certification_status",
        "taskset_proven", "first_failed_priority", "failure_reason", "timeout",
        "runtime_wall_seconds", "runtime_cpu_seconds", "peak_rss_bytes",
        "checked_w_count", "checked_q_count", "checked_h_count",
        "exact_result_hash", "candidate_vector_hash", "witness_vector_hash",
        "certification_vector_hash", "failure_reason_vector_hash",
        "source_analysis_id", "fallback_used", "scenario", "axis", "axis_value",
        "service_scale", "power_scale", "deadline_variant",
        "normalized_utilization",
    ),
    "formal_rta_task_results.csv": COMMON + (
        "task_result_id", "plan_record_id", "analysis_id", "request_id", "execution_run_id",
        "taskset_skeleton_id", "taskset_id", "method",
        "exact_e0", "task_id", "priority_rank", "task_solver_status",
        "task_certification_status", "candidate_response_time", "D",
        "checked_w_count", "checked_q_count", "checked_h_count",
        "failure_reason", "witness_hash", "exact_task_result_hash",
    ),
    "formal_rta_mechanisms.csv": COMMON + (
        "analysis_id", "taskset_id", "method", "task_id", "priority_rank",
        "telemetry_status", "impossible_prefix_count", "empty_phase_set_count",
        "strict_ph_lt_loc_checkpoints", "flow_call_count", "flow_node_count",
        "flow_edge_count", "z_branch_count", "flow_optimal_count",
        "flow_infeasible_count", "flow_timeout_count", "flow_internal_count",
        "ph_no_common_h_but_seq_exists", "sequence_kind", "sequence_length",
        "distinct_h_count", "last_h", "strict_seq_lt_ph",
        "safety_predicate_calls", "cache_hits", "cache_misses", "cache_hit_rate",
    ),
    "formal_dependencies.csv": COMMON + (
        "plan_relation_id", "analysis_id", "source_analysis_id",
        "source_request_id", "relation", "source_core",
        "target_core", "taskset_skeleton_id", "taskset_id", "taskset_hash",
        "method", "exact_e0", "service_identity", "power_vector_hash",
        "theory_document_sha256", "numeric_contract_sha256",
        "source_exact_input_identity", "target_exact_input_identity",
        "source_result_hash", "source_plan_sha256", "source_closure_sha256",
        "dependency_status", "fallback_used",
    ),
    "formal_dominance_checks.csv": COMMON + (
        "check_id", "taskset_skeleton_id", "taskset_id", "exact_e0",
        "carry_policy", "left_method", "right_method", "task_id",
        "left_candidate", "right_candidate", "left_certified",
        "right_certified", "check_status", "failure_severity",
    ),
    "formal_monotonicity_checks.csv": COMMON + (
        "check_id", "taskset_skeleton_id", "method", "axis",
        "weaker_value", "stronger_value", "weaker_analysis_id",
        "stronger_analysis_id", "candidate_status", "certification_status",
        "check_status", "failure_severity",
    ),
    "formal_simulation_runs.csv": COMMON + (
        "plan_record_id", "plan_simulation_id", "simulation_id",
        "execution_run_id", "taskset_skeleton_slot_id", "taskset_slot_id",
        "taskset_skeleton_id", "taskset_id",
        "taskset_hash", "release_projection_id", "release_vector_hash",
        "release_mode", "exact_offsets_json", "release_horizon",
        "observation_horizon", "scheduler", "battery_model", "battery_capacity",
        "physical_initial_energy", "offered_harvest", "required_margin",
        "service_harvest_identity", "trace_contract",
        "trace_path", "trace_sha256", "simulation_status", "deadline_miss_count",
        "max_observed_response", "task_result_vector_hash",
        "job_result_vector_hash", "release_audit_id", "no_overflow_evidence_id",
        "validated_simulation_evidence_id", "applicability_track",
    ),
    "formal_simulation_task_results.csv": COMMON + (
        "simulation_task_result_id", "simulation_id", "taskset_id",
        "task_id", "priority_rank",
        "released_job_count", "completed_job_count", "deadline_miss_count",
        "max_observed_response", "simulation_status",
    ),
    "formal_simulation_job_results.csv": COMMON + (
        "simulation_job_result_id", "simulation_id", "taskset_id",
        "task_id", "job_index", "release_time",
        "completion_time", "absolute_deadline", "observed_response_time",
        "deadline_missed", "within_release_horizon", "observation_status",
    ),
    "formal_applicability.csv": COMMON + (
        "plan_comparison_id", "comparison_id", "analysis_id", "simulation_id",
        "taskset_id", "method",
        "exact_e0", "release_audit_id", "e0_evaluation_id",
        "no_overflow_evidence_id", "validated_simulation_evidence_id",
        "applicability_track", "e0_condition_status", "theorem_applicability",
        "theorem_comparison_eligible", "rta_outcome", "simulation_outcome",
        "comparison_status", "candidate_response_time", "observed_response_time",
        "soundness_counterexample", "empirical_difference",
    ),
    "formal_worker_consistency.csv": COMMON + (
        "check_id", "mathematical_request_id", "reference_execution_id",
        "compared_execution_id", "reference_worker_count", "compared_worker_count",
        "solver_status_match", "candidate_match", "witness_match",
        "certification_match", "failure_reason_match", "math_hash_match",
        "reference_math_result_hash", "compared_math_result_hash", "check_status",
        "failure_severity",
    ),
    "formal_failures.csv": COMMON + (
        "failure_id", "severity", "stage", "code", "detail", "core",
        "analysis_id", "simulation_id", "taskset_skeleton_id", "taskset_id",
        "recoverable", "created_at_utc",
    ),
}

FORMAL_TABLES: Mapping[str, Tuple[str, ...]] = MappingProxyType(_TABLES)


def formal_schema_material() -> Dict[str, Any]:
    return {
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
        "ordered_tables": [
            {"filename": name, "ordered_columns": list(columns)}
            for name, columns in FORMAL_TABLES.items()
        ],
        "status_dimensions": [
            "generation_status", "solver_status", "task_certification_status",
            "taskset_certification_status", "simulation_status",
            "e0_condition_status", "theorem_applicability",
            "comparison_status", "failure_severity",
        ],
        "missing_telemetry_encoding": "NA_WITH_TELEMETRY_STATUS",
    }


def formal_schema_hash() -> str:
    return domain_hash(RTA4_FORMAL_SCHEMA_DOMAIN, formal_schema_material())


def formal_schema_manifest() -> Dict[str, Any]:
    material = formal_schema_material()
    return {**material, "schema_sha256": formal_schema_hash()}


def legacy_table_overlap() -> frozenset[str]:
    """The independent namespace must never claim a legacy table filename."""

    from .result_writer import TABLES as LEGACY_TABLES
    return frozenset(FORMAL_TABLES).intersection(LEGACY_TABLES)


if legacy_table_overlap():
    raise RuntimeError("RTA4 formal schema overlaps the legacy ResultWriter namespace")


__all__ = [
    "FORMAL_TABLES", "RTA4_FORMAL_SCHEMA_DOMAIN",
    "RTA4_FORMAL_SCHEMA_MANIFEST", "formal_schema_hash",
    "formal_schema_manifest", "formal_schema_material", "legacy_table_overlap",
]
