"""Versioned schemas for independently replayable CORE-0A raw evidence."""

from __future__ import annotations


SCHEMA_VERSION = "CORE0A-RAW-3.0"


def _schema(primary_key, fields):
    return {"primary_key": tuple(primary_key), "fields": tuple(fields)}


TABLE_SCHEMAS = {
    "workload_cases.csv": _schema(
        ["case_id"],
        ["case_id", "input_hash", "build_identity_hash", "C", "T", "D", "theta", "L", "production_value", "oracle_value", "match"],
    ),
    "workload_monotonicity_checks.csv": _schema(
        ["check_id"],
        ["check_id", "input_hash", "build_identity_hash", "axis", "left_case_id", "right_case_id", "left_value", "right_value", "passed"],
    ),
    "processor_cases.csv": _schema(
        ["case_id"],
        ["case_id", "input_hash", "build_identity_hash", "domain", "M", "target_json", "hp_json", "theta_json", "w", "production_value", "oracle_value", "match"],
    ),
    "envelope_cases.csv": _schema(
        ["case_id", "kind"],
        ["case_id", "kind", "input_hash", "build_identity_hash", "domain", "target_json", "hp_json", "lp_json", "theta_json", "w", "q", "h", "M", "production_value", "oracle_value", "match"],
    ),
    "search_closure_specifications.csv": _schema(
        ["specification_id"],
        ["specification_id", "input_hash", "build_identity_hash", "case_kind", "variant", "task_json", "hp_json", "lp_json", "theta_json", "M", "E0", "w_domain_json", "A_lookup_json", "closure_point_json", "expected_result_status", "canonical_specification_hash"],
    ),
    "search_closure_lookup.csv": _schema(
        ["specification_id", "w", "h", "q"],
        ["specification_id", "w", "h", "q", "input_hash", "build_identity_hash", "envelope_value", "service_value", "expected_predicate"],
    ),
    "search_trace_events.csv": _schema(
        ["specification_id", "sequence_number"],
        ["specification_id", "sequence_number", "input_hash", "build_identity_hash", "event_type", "w", "A", "h", "q", "envelope_value", "service_value", "service_index", "coverage_index", "q_result", "h_result", "w_result", "result_status"],
    ),
    "service_curve_cases.csv": _schema(
        ["curve_case_id"],
        ["curve_case_id", "input_hash", "build_identity_hash", "curve_kind", "curve_spec", "required_horizon", "expected_valid", "production_accepted", "validation_status", "analysis_attempted", "candidate_returned", "certification_returned", "match"],
    ),
    "scheduler_event_order_cases.csv": _schema(
        ["microcase_id"],
        ["microcase_id", "input_hash", "build_identity_hash", "initial_tasks_json", "initial_energy", "service_curve_json", "M", "event_order_specification_id"],
    ),
    "scheduler_event_order_ticks.csv": _schema(
        ["microcase_id", "tick"],
        ["microcase_id", "tick", "input_hash", "build_identity_hash", "energy_before", "completed_jobs_json", "harvested_energy_committed", "released_jobs_json", "ready_hol_order_json", "eligible_jobs_json", "scheduler_scan_order_json", "selected_jobs_json", "consumed_energy", "energy_after", "job_remaining_before_json", "job_remaining_after_json", "processor_blocked_jobs_json", "energy_blocked_jobs_json"],
    ),
    "scheduler_event_order_assertions.csv": _schema(
        ["microcase_id", "tick", "assertion_id"],
        ["microcase_id", "tick", "assertion_id", "input_hash", "build_identity_hash", "expected_event", "actual_event", "assertion_passed"],
    ),
    "joint_certification_cases.jsonl": _schema(
        ["case_id"],
        ["case_id", "input_hash", "build_identity_hash", "case_kind", "analysis_variant", "priority_order_json", "source_analysis_id", "dependency_context_json", "production_api_used", "reported_passed"],
    ),
    "joint_certification_task_inputs.csv": _schema(
        ["case_id", "task_id"],
        ["case_id", "task_id", "input_hash", "build_identity_hash", "priority_rank", "C", "D", "T", "P", "fixed_carry_in", "source_candidate"],
    ),
    "joint_certification_solver_script.csv": _schema(
        ["case_id", "call_sequence"],
        ["case_id", "call_sequence", "input_hash", "build_identity_hash", "task_id", "solver_outcome", "candidate", "expected_called"],
    ),
    "joint_certification_actual_tasks.csv": _schema(
        ["case_id", "task_id"],
        ["case_id", "task_id", "input_hash", "build_identity_hash", "solver_status", "certification_status", "candidate", "failure_reason", "evaluation_order"],
    ),
    "joint_certification_expected_tasks.csv": _schema(
        ["case_id", "task_id"],
        ["case_id", "task_id", "input_hash", "build_identity_hash", "solver_status", "certification_status", "candidate", "failure_reason", "evaluation_order"],
    ),
    "joint_certification_assertions.csv": _schema(
        ["case_id", "assertion_id"],
        ["case_id", "assertion_id", "input_hash", "build_identity_hash", "expected", "actual", "producer_passed"],
    ),
    "joint_certification_results.csv": _schema(
        ["case_id"],
        ["case_id", "input_hash", "build_identity_hash", "expected_solver_status", "actual_solver_status", "expected_certification_status", "actual_certification_status", "expected_taskset_proven", "actual_taskset_proven", "pre_finalizer_status", "post_finalizer_status", "source_hash_before", "source_hash_after"],
    ),
    "dominance_tasksets.csv": _schema(
        ["taskset_hash"],
        ["taskset_hash", "input_hash", "build_identity_hash", "tasks_json", "priority_order_json", "processors", "E0", "service_curve_json", "source_analysis_id", "source_solver_status", "source_certification_status", "source_vector_json", "source_vector_hash", "local_analysis_id", "local_solver_status", "local_certification_status", "local_frozen_vector_json", "local_vector_hash", "joint_certified"],
    ),
    "dominance_task_results.csv": _schema(
        ["taskset_hash", "task_id"],
        ["taskset_hash", "task_id", "input_hash", "build_identity_hash", "source_candidate", "local_candidate", "candidate_compared", "dominance_violation"],
    ),
    "finite_state_tasksets.csv": _schema(
        ["taskset_id"],
        ["taskset_id", "input_hash", "build_identity_hash", "domain_id", "tasks_json", "processors", "E0", "service_curve_json", "generation_horizon", "observation_horizon", "enumeration_complete", "analysis_variant", "analysis_solver_status", "analysis_certification_status", "taskset_proven", "inconclusive_reason", "internal_error"],
    ),
    "finite_state_jobs.csv": _schema(
        ["taskset_id", "job_id"],
        ["taskset_id", "job_id", "input_hash", "build_identity_hash", "task_id", "priority_rank", "release", "wcet", "candidate", "completion", "response_time", "release_energy", "E0", "certificate_satisfied", "processor_blocking_ticks", "energy_blocking_ticks"],
    ),
    "finite_state_ticks.csv": _schema(
        ["taskset_id", "tick"],
        ["taskset_id", "tick", "input_hash", "build_identity_hash", "start_energy", "completion_events_json", "previous_harvest_credit", "release_events_json", "eligible_hol_json", "scan_order_json", "execution_set_json", "energy_consumed", "post_tick_energy", "processor_blocked_jobs_json", "energy_blocked_jobs_json"],
    ),
    "release_energy_certificates.csv": _schema(
        ["taskset_id", "job_id"],
        ["taskset_id", "job_id", "input_hash", "build_identity_hash", "release", "release_energy", "E0", "positive_E0", "candidate_jointly_certified", "bound_check_executed", "certificate_status"],
    ),
    "bound_checks.csv": _schema(
        ["taskset_id", "job_id"],
        ["taskset_id", "job_id", "input_hash", "build_identity_hash", "variant", "release_boundary", "candidate", "actual_completion_boundary", "response_time", "release_energy", "E0", "certificate_satisfied", "processor_blocking_count", "energy_blocking_count", "violation", "inconclusive_reason"],
    ),
    "mutation_runs.csv": _schema(
        ["mutation_id"],
        ["mutation_id", "input_hash", "build_identity_hash", "target_file", "target_symbol", "argv_json", "cwd_policy", "environment_overrides_json", "stdout_member_path", "stderr_member_path", "stdout_sha256", "stderr_sha256", "exit_code", "expected_failing_assertion_id", "observed_failing_assertion_id", "failure_matches_target", "syntax_import_failure", "original_source_hash", "mutated_source_hash", "restored_source_hash", "mutation_applied", "detected"],
    ),
    "lineage_checks.csv": _schema(
        ["check_id"],
        ["check_id", "input_hash", "build_identity_hash", "check_type", "source_table", "source_row_id", "target_table", "target_row_id", "expected", "actual", "violation", "evidence_hash"],
    ),
}


RAW_TABLES = tuple(TABLE_SCHEMAS)

LINEAGE_REQUIRED_CHECK_TYPES = (
    "PRIMARY_KEY_UNIQUE", "FOREIGN_KEY_VALID", "INPUT_HASH_MATCH",
    "BUILD_HASH_MATCH", "THEORY_HASH_MATCH", "CONTRACT_HASH_MATCH",
    "REQUEST_ACCOUNTED", "EXECUTION_STATE_VALID", "SEMANTIC_STATUS_VALID",
    "GENERATION_FAILURE_PROPAGATION", "DEPENDENCY_SOURCE_VALID",
    "DEPENDENCY_VECTOR_HASH_MATCH", "DEPENDENCY_DAG_EDGE_VALID",
    "DEPENDENCY_DAG_ACYCLIC", "TASKSET_PROVEN_CONSISTENT",
    "TASK_CERTIFICATION_CONSISTENT", "FAILURE_PROVENANCE_CONSISTENT",
    "CANONICAL_COLUMN_ORDER",
)
