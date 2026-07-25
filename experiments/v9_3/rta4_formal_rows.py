"""Canonical semantic row contracts for all RTA4 formal CSV tables."""

from __future__ import annotations

from fractions import Fraction
import math
import re
from typing import Any, Dict, Mapping
import unicodedata

from .rta4_formal_schema import FORMAL_TABLES


NA = "NA"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")


class RTA4FormalRowError(ValueError):
    """Raised before a non-canonical or semantically invalid row is written."""


EXACT_FIELDS = frozenset({
    "normalized_utilization", "target_normalized_utilization",
    "actual_normalized_utilization", "normalized_density", "exact_e0",
    "service_scale", "power_scale", "P_exact", "D_over_T",
    "deadline_slack_fraction", "physical_initial_energy", "battery_capacity",
    "offered_harvest", "required_margin",
    "weaker_value", "stronger_value",
    "axis_value",
})

INTEGER_FIELDS = frozenset({
    "formal_seed", "processor_count", "task_count", "priority_rank",
    "C", "D", "T", "attempt_number", "worker_count",
    "timeout_budget_seconds", "peak_rss_bytes", "first_failed_priority",
    "checked_w_count", "checked_q_count", "checked_h_count",
    "impossible_prefix_count", "empty_phase_set_count",
    "strict_ph_lt_loc_checkpoints", "flow_call_count", "flow_node_count",
    "flow_edge_count", "z_branch_count", "flow_optimal_count",
    "flow_infeasible_count", "flow_timeout_count", "flow_internal_count",
    "sequence_length", "distinct_h_count", "last_h", "safety_predicate_calls",
    "cache_hits", "cache_misses", "release_horizon", "observation_horizon",
    "deadline_miss_count", "released_job_count", "completed_job_count",
    "job_index", "release_time", "completion_time", "absolute_deadline",
    "candidate_response_time", "observed_response_time", "max_observed_response",
    "left_candidate", "right_candidate",
})

MEASUREMENT_FIELDS = frozenset({
    "runtime_wall_seconds", "runtime_cpu_seconds", "cache_hit_rate",
})

BOOLEAN_FIELDS = frozenset({
    "taskset_proven", "timeout", "fallback_used", "left_certified",
    "right_certified", "ph_no_common_h_but_seq_exists", "strict_seq_lt_ph",
    "deadline_missed", "within_release_horizon", "theorem_comparison_eligible",
    "soundness_counterexample", "empirical_difference", "solver_status_match",
    "candidate_match", "witness_match", "certification_match",
    "failure_reason_match", "math_hash_match", "recoverable",
})

SHA_FIELDS = frozenset({
    "schema_sha256", "plan_sha256", "config_semantic_hash", "plan_record_id",
    "generation_request_id", "taskset_skeleton_id", "taskset_id", "taskset_hash",
    "taskset_skeleton_slot_id", "taskset_slot_id",
    "power_vector_hash", "priority_identity", "base_power_vector_identity",
    "certificate_sha256", "analysis_id", "request_id", "execution_run_id",
    "service_identity",
    "theory_document_sha256", "numeric_contract_sha256", "exact_input_identity",
    "source_analysis_id", "attempt_id", "parent_attempt_id", "exact_result_hash",
    "candidate_vector_hash", "witness_vector_hash", "certification_vector_hash",
    "failure_reason_vector_hash", "task_result_id", "exact_task_result_hash", "witness_hash",
    "plan_relation_id", "source_request_id", "source_result_hash",
    "source_plan_sha256", "source_closure_sha256", "check_id",
    "source_exact_input_identity", "target_exact_input_identity",
    "weaker_analysis_id", "stronger_analysis_id", "plan_simulation_id",
    "simulation_id", "simulation_task_result_id", "simulation_job_result_id",
    "task_result_vector_hash", "job_result_vector_hash",
    "release_projection_id", "release_vector_hash",
    "service_harvest_identity", "trace_sha256", "release_audit_id",
    "no_overflow_evidence_id", "validated_simulation_evidence_id",
    "plan_comparison_id", "comparison_id", "e0_evaluation_id",
    "mathematical_request_id", "reference_execution_id", "compared_execution_id",
    "reference_math_result_hash", "compared_math_result_hash", "failure_id",
})

NULLABLE = frozenset({
    "source_analysis_id", "parent_attempt_id", "failure_origin",
    "runtime_cpu_seconds", "first_failed_priority", "failure_reason",
    "candidate_response_time", "witness_hash", "left_candidate", "right_candidate",
    "trace_path", "completion_time", "observed_response_time",
    "analysis_id", "simulation_id", "taskset_skeleton_id", "taskset_id",
    "battery_capacity", "physical_initial_energy", "no_overflow_evidence_id",
    "failure_severity",
})

ENUMS = {
    "core": {"CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B"},
    "generation_status": {"GENERATED_AND_CERTIFIED"},
    "method": {
        "CW_D", "LOC_D", "PH_D", "SEQ_D", "CW_THETA_CW", "LOC_THETA_LOC",
        "PH_THETA_PH", "SEQ_THETA_SEQ",
    },
    "method_role": {"MAIN_METHOD", "SOURCE_REUSE", "WORKER_CONSISTENCY"},
    "carry_policy": {"FIXED_D", "SELF_RECURSIVE"},
    "request_status": {"PLANNED", "COMPLETED", "TIMEOUT", "FAILED"},
    "solver_status": {"COMPLETED", "NO_CANDIDATE", "TIMEOUT", "NUMERIC_ERROR", "INTERNAL_ERROR"},
    "taskset_certification_status": {"CERTIFIED_TASKSET", "NOT_CERTIFIED", "TIMEOUT", "ERROR"},
    "task_solver_status": {"CANDIDATE_FOUND", "NO_CANDIDATE", "TIMEOUT", "NUMERIC_ERROR", "INTERNAL_ERROR"},
    "task_certification_status": {"CERTIFIED", "NOT_CERTIFIED", "TIMEOUT", "ERROR"},
    "severity": {"P0", "P1", "P2", "P3"},
    "telemetry_status": {"AVAILABLE", "NOT_APPLICABLE_OR_UNAVAILABLE"},
    "dependency_status": {"VALIDATED", "VALIDATED_EXTERNAL_SOURCE"},
    "release_mode": {"SYNC_V1", "ASYNC_HASH_PHASE_V1"},
    "applicability_track": {"THEOREM_ALIGNED", "FINITE_BATTERY_EMPIRICAL"},
    "e0_condition_status": {"E0_CONDITION_SATISFIED", "E0_CONDITION_NOT_SATISFIED"},
    "rta_outcome": {"RTA_PASS", "RTA_FAIL"},
    "simulation_outcome": {"SIM_DEADLINE_MISS", "SIM_NO_DEADLINE_MISS"},
    "battery_model": {"FINITE_CAPACITY_EXACT", "THEOREM_NO_OVERFLOW_EXACT"},
    "simulation_status": {"COMPLETED"},
    "failure_severity": {"P0", "P1", "P2", "P3"},
    "theorem_applicability": {
        "THEOREM_ALIGNED", "FINITE_BATTERY_EMPIRICAL",
        "E0_CONDITION_NOT_SATISFIED",
    },
    "comparison_status": {
        "RTA_PASS_SIM_FAIL", "RTA_PASS_SIM_PASS", "RTA_FAIL_SIM_FAIL",
        "RTA_FAIL_SIM_PASS", "E0_CONDITION_NOT_SATISFIED",
    },
}

TABLE_ENUMS = {
    "formal_dependencies.csv": {
        "relation": {"CORE2_REUSE", "CORE3_APPLICABILITY_SOURCE"},
    },
    "formal_dominance_checks.csv": {
        "check_status": {"PASS", "P0_VIOLATION", "NOT_COMPARABLE"},
    },
    "formal_monotonicity_checks.csv": {
        "candidate_status": {"PASS", "P0_VIOLATION", "NOT_COMPARABLE"},
        "certification_status": {"PASS", "P0_VIOLATION", "NOT_COMPARABLE"},
        "check_status": {"PASS", "P0_VIOLATION", "NOT_COMPARABLE"},
    },
    "formal_worker_consistency.csv": {
        "check_status": {"PASS", "P0_MISMATCH"},
    },
    "formal_simulation_job_results.csv": {
        "observation_status": {"COMPLETED", "DEADLINE_MISSED", "INCOMPLETE"},
    },
}


def _fraction_text(value: Any, field: str, *, nullable: bool) -> str:
    if value is None or value == "" or value == NA:
        if nullable:
            return NA
        raise RTA4FormalRowError(f"{field} is required")
    if isinstance(value, bool) or isinstance(value, float):
        raise RTA4FormalRowError(f"{field} forbids binary floating-point data")
    if isinstance(value, Fraction):
        exact = value
    elif type(value) in {str, int}:
        try:
            exact = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4FormalRowError(f"{field} is not exact rational data") from exc
    else:
        raise RTA4FormalRowError(f"{field} is not exact rational data")
    text = str(exact.numerator) if exact.denominator == 1 else f"{exact.numerator}/{exact.denominator}"
    if isinstance(value, str) and value != text:
        raise RTA4FormalRowError(f"{field} is not canonical rational text")
    if exact < 0:
        raise RTA4FormalRowError(f"{field} must be non-negative")
    return text


def _integer_text(value: Any, field: str, *, nullable: bool) -> str:
    if value is None or value == "" or value == NA:
        if nullable:
            return NA
        raise RTA4FormalRowError(f"{field} is required")
    if isinstance(value, bool) or isinstance(value, float):
        raise RTA4FormalRowError(f"{field} must be an integer")
    text = str(value)
    if _INTEGER.fullmatch(text) is None:
        raise RTA4FormalRowError(f"{field} must be a canonical non-negative integer")
    if field in {"C", "D", "T", "processor_count", "task_count", "worker_count", "release_horizon", "observation_horizon"} and int(text) < 1:
        raise RTA4FormalRowError(f"{field} must be positive")
    return text


def _measurement_text(value: Any, field: str, *, nullable: bool) -> str:
    if value is None or value == "" or value == NA:
        if nullable:
            return NA
        raise RTA4FormalRowError(f"{field} is required")
    if isinstance(value, bool):
        raise RTA4FormalRowError(f"{field} must be a finite non-negative measurement")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RTA4FormalRowError(f"{field} must be a finite non-negative measurement") from exc
    if not math.isfinite(number) or number < 0:
        raise RTA4FormalRowError(f"{field} must be a finite non-negative measurement")
    return format(number, ".17g")


def _boolean_text(value: Any, field: str) -> str:
    if value is True or value in {"true", "True", "1", 1}:
        return "true"
    if value is False or value in {"false", "False", "0", 0}:
        return "false"
    raise RTA4FormalRowError(f"{field} must be a strict boolean")


def _string(value: Any, field: str, *, nullable: bool) -> str:
    if value is None or value == "" or value == NA:
        if nullable:
            return NA
        raise RTA4FormalRowError(f"{field} is required")
    if type(value) not in {str, int}:
        raise RTA4FormalRowError(f"{field} must be canonical scalar text")
    text = str(value)
    if unicodedata.normalize("NFC", text) != text:
        raise RTA4FormalRowError(f"{field} must be canonical NFC text")
    return text


def normalize_formal_row(
    table: str, row: Mapping[str, Any], common: Mapping[str, Any],
) -> Dict[str, str]:
    if table not in FORMAL_TABLES:
        raise RTA4FormalRowError(f"unknown formal table: {table}")
    expected = set(FORMAL_TABLES[table])
    supplied = {**dict(common), **dict(row)}
    extra = set(supplied) - expected
    missing = expected - set(supplied)
    if extra:
        raise RTA4FormalRowError(f"unexpected columns for {table}: {sorted(extra)}")
    if missing:
        raise RTA4FormalRowError(f"missing required columns for {table}: {sorted(missing)}")
    result: Dict[str, str] = {}
    for field in FORMAL_TABLES[table]:
        value = supplied[field]
        nullable = field in NULLABLE
        if field in EXACT_FIELDS and not (field == "axis_value" and str(value) in {"baseline", "ALL", "NA"}):
            text = _fraction_text(value, field, nullable=nullable)
        elif field in INTEGER_FIELDS:
            text = _integer_text(value, field, nullable=nullable)
        elif field in MEASUREMENT_FIELDS:
            text = _measurement_text(value, field, nullable=nullable)
        elif field in BOOLEAN_FIELDS:
            text = _boolean_text(value, field)
        else:
            text = _string(value, field, nullable=nullable)
        if field in SHA_FIELDS and text != NA and _SHA256.fullmatch(text) is None:
            raise RTA4FormalRowError(f"{field} must be canonical lowercase SHA-256")
        allowed = TABLE_ENUMS.get(table, {}).get(field, ENUMS.get(field))
        if allowed is not None and text != NA and text not in allowed:
            raise RTA4FormalRowError(f"unknown {field}: {text!r}")
        result[field] = text
    if table == "formal_tasks.csv":
        c, d, t = (int(result[name]) for name in ("C", "D", "T"))
        if not c <= d <= t:
            raise RTA4FormalRowError("formal task must satisfy 1 <= C <= D <= T")
    if table == "formal_rta_task_results.csv":
        candidate = result["candidate_response_time"]
        status = result["task_solver_status"]
        if status == "CANDIDATE_FOUND" and candidate == NA:
            raise RTA4FormalRowError("candidate-found task requires a candidate")
        if status != "CANDIDATE_FOUND" and candidate != NA:
            raise RTA4FormalRowError("non-candidate task must encode candidate as NA")
        if result["task_certification_status"] == "CERTIFIED":
            if status != "CANDIDATE_FOUND" or candidate == NA:
                raise RTA4FormalRowError("certified task requires a found candidate")
            if int(candidate) > int(result["D"]):
                raise RTA4FormalRowError("certified candidate must not exceed D")
    return result


__all__ = ["NA", "RTA4FormalRowError", "normalize_formal_row"]
