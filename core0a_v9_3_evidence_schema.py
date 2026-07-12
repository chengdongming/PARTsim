"""Versioned schemas for independently replayable CORE-0A raw evidence."""

from __future__ import annotations


SCHEMA_VERSION = "CORE0A-RAW-2.0"


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
    "search_trace_events.csv": _schema(
        ["task_case_id", "sequence_number"],
        ["task_case_id", "sequence_number", "input_hash", "build_identity_hash", "variant", "task_json", "hp_json", "lp_json", "theta_json", "M", "E0", "service_curve_json", "result_status", "w", "A", "h", "q", "event_type", "envelope_value", "service_value", "service_index", "coverage_index", "q_result", "h_result", "w_result"],
    ),
    "service_curve_cases.csv": _schema(
        ["curve_case_id"],
        ["curve_case_id", "input_hash", "build_identity_hash", "curve_kind", "curve_spec", "required_horizon", "expected_valid", "production_accepted", "validation_status", "analysis_attempted", "candidate_returned", "certification_returned", "match"],
    ),
    "scheduler_event_order_traces.csv": _schema(
        ["microcase_id", "tick", "assertion_id"],
        ["microcase_id", "tick", "assertion_id", "input_hash", "build_identity_hash", "initial_tasks_json", "initial_energy", "boundary_events_json", "completion_events_json", "harvest_credit", "release_events_json", "boundary_energy", "eligible_hol_json", "scan_order_json", "execution_set_json", "energy_consumed", "post_tick_energy", "expected_event", "actual_event", "assertion_passed"],
    ),
    "joint_certification_cases.csv": _schema(
        ["state_case_id"],
        ["state_case_id", "input_hash", "build_identity_hash", "case_kind", "variant", "expected_solver_status", "actual_solver_status", "expected_certification_status", "actual_certification_status", "expected_taskset_proven", "actual_taskset_proven", "production_api_used", "passed"],
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
        ["mutation_id", "input_hash", "build_identity_hash", "target_file", "target_symbol", "original_file_hash", "mutated_file_hash", "mutation_applied", "expected_test", "test_exit_code", "failure_matches_target", "restored_file_hash", "detected"],
    ),
    "lineage_checks.csv": _schema(
        ["check_id"],
        ["check_id", "input_hash", "build_identity_hash", "check_type", "source_file", "source_key", "target_file", "target_key", "expected", "actual", "passed"],
    ),
}


RAW_TABLES = tuple(TABLE_SCHEMAS)
