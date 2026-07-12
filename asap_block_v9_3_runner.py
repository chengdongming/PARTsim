#!/usr/bin/env python3
"""Explicit v9.3 runner adapter and v1.3.12 analysis serializer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import asap_block_rta as v20
import asap_block_rta_v21_local_window as v21
import asap_block_rta_v9_3 as v93_core
import asap_block_rta_v9_3_taskset as v93
from asap_block_v1_3_12_schema_binding import (
    ContractBindingError,
    V1312SchemaBinding,
)


DEFAULT_RTA_VERSION = "v20.4"
V21_DISPATCH_VERSION = "v21"
V93_DISPATCH_VERSION = "v9.3"

VARIANT_ORDER = (
    v93.AnalysisVariant.CW_D,
    v93.AnalysisVariant.LOC_D,
    v93.AnalysisVariant.CW_THETA_CW,
    v93.AnalysisVariant.LOC_THETA_CW,
    v93.AnalysisVariant.LOC_THETA_LOC,
)

WINDOW_BY_VARIANT = {
    v93.AnalysisVariant.CW_D: "complete",
    v93.AnalysisVariant.LOC_D: "local",
    v93.AnalysisVariant.CW_THETA_CW: "complete",
    v93.AnalysisVariant.LOC_THETA_CW: "local",
    v93.AnalysisVariant.LOC_THETA_LOC: "local",
}

CARRY_BY_VARIANT = {
    v93.AnalysisVariant.CW_D: "deadline",
    v93.AnalysisVariant.LOC_D: "deadline",
    v93.AnalysisVariant.CW_THETA_CW: "cw_candidate",
    v93.AnalysisVariant.LOC_THETA_CW: "cw_candidate",
    v93.AnalysisVariant.LOC_THETA_LOC: "loc_candidate",
}

FAILURE_DETAIL_BY_CODE = {
    "NONE": None,
    "NO_CANDIDATE": "closure exhausted through task deadline",
    "SOLVER_TIMEOUT": None,
    "NUMERIC_ERROR": "numeric guard rejected analysis",
    "UPSTREAM_PREFIX_FAILURE": None,
    "DEPENDENCY_NOT_APPLICABLE": None,
    "DOMINANCE_INVARIANT_VIOLATION": (
        "local result violated frozen carry-in dominance"
    ),
    "UNKNOWN_CORE_STATUS": "unrecognized core solver status",
    "INTERNAL_CONFORMANCE_FAILURE": (
        "internal analyzer conformance failure"
    ),
}


class RunnerConformanceError(ValueError):
    """Raised rather than repairing or falling back from an invalid result."""


@dataclass(frozen=True)
class V93DispatchRequest:
    analysis_id: str
    variant: v93.AnalysisVariant
    analysis_input: v93.TasksetAnalysisInput
    source: Optional[v93.TasksetAnalysisResult] = None
    dependency_check_status: v93.DependencyVectorCheckStatus = (
        v93.DependencyVectorCheckStatus.NOT_CHECKED
    )
    diagnostic_mode: bool = False


@dataclass(frozen=True)
class FiveConfigurationRun:
    entries: Tuple[Tuple[v93.AnalysisVariant, v93.TasksetAnalysisResult], ...]

    def by_variant(self) -> Mapping[v93.AnalysisVariant, v93.TasksetAnalysisResult]:
        return MappingProxyType(dict(self.entries))


@dataclass(frozen=True)
class StructuredTaskFailure:
    code: str
    detail: Optional[str]


@dataclass(frozen=True)
class SerializedAnalysis:
    analysis_result: v93.TasksetAnalysisResult
    taskset_row: Mapping[str, Any]
    task_rows: Tuple[Mapping[str, Any], ...]
    dependency_rows: Tuple[Mapping[str, Any], ...]


def dispatch_rta_version(
    version: Optional[str] = None,
    *,
    v20_kwargs: Optional[Mapping[str, Any]] = None,
    v21_kwargs: Optional[Mapping[str, Any]] = None,
    v93_request: Optional[V93DispatchRequest] = None
) -> Any:
    """Dispatch only an explicitly supported version; never fall back."""

    selected = DEFAULT_RTA_VERSION if version is None else version
    if selected == DEFAULT_RTA_VERSION:
        if v20_kwargs is None:
            raise RunnerConformanceError("v20.4 dispatch requires v20_kwargs")
        return v20.analyze_taskset(**dict(v20_kwargs))
    if selected == V21_DISPATCH_VERSION:
        if v21_kwargs is None:
            raise RunnerConformanceError("v21 dispatch requires v21_kwargs")
        return v21.analyze_taskset_v21(**dict(v21_kwargs))
    if selected == V93_DISPATCH_VERSION:
        if v93_request is None:
            raise RunnerConformanceError("v9.3 dispatch requires V93DispatchRequest")
        validated_beta = v93_core.validate_service_curve_v9_3(
            v93_request.analysis_input.beta,
            max(
                task.deadline
                for task in v93_request.analysis_input.tasks
            ) - 1,
        )
        validated_request = replace(
            v93_request,
            analysis_input=replace(
                v93_request.analysis_input, beta=validated_beta
            ),
        )
        # No exception handler here: a v9.3 failure must not invoke an older RTA.
        return v93.analyze_taskset_v9_3(
            validated_request.analysis_id,
            validated_request.variant,
            validated_request.analysis_input,
            source=validated_request.source,
            dependency_check_status=validated_request.dependency_check_status,
            diagnostic_mode=validated_request.diagnostic_mode,
        )
    raise RunnerConformanceError("unknown RTA version: {!r}".format(selected))


def _source_is_jointly_certified(source: v93.TasksetAnalysisResult) -> bool:
    return bool(
        source.analysis_variant is v93.AnalysisVariant.CW_THETA_CW
        and source.solver_status is v93.AnalysisSolverStatus.COMPLETED
        and source.certification_status
        is v93.AnalysisCertificationStatus.CERTIFIED_TASKSET
        and source.taskset_proven
        and all(
            record.solver_status is v93.TaskSolverStatus.CANDIDATE_FOUND
            and record.certification_status
            is v93.TaskCertificationStatus.CERTIFIED
            for record in source.task_records
        )
    )


def run_five_configurations_v9_3(
    analysis_input: v93.TasksetAnalysisInput,
    analysis_ids: Mapping[v93.AnalysisVariant, str],
) -> FiveConfigurationRun:
    """Run five configurations, handing LOC-Theta-cw an immutable CW result."""

    if set(analysis_ids) != set(VARIANT_ORDER):
        raise RunnerConformanceError("analysis_ids must cover exactly five variants")
    results: Dict[v93.AnalysisVariant, v93.TasksetAnalysisResult] = {}
    # Source is completed first and stored only as its frozen result object.
    source_variant = v93.AnalysisVariant.CW_THETA_CW
    results[source_variant] = dispatch_rta_version(
        V93_DISPATCH_VERSION,
        v93_request=V93DispatchRequest(
            analysis_ids[source_variant], source_variant, analysis_input
        ),
    )
    for variant in (
        v93.AnalysisVariant.CW_D,
        v93.AnalysisVariant.LOC_D,
        v93.AnalysisVariant.LOC_THETA_LOC,
    ):
        results[variant] = dispatch_rta_version(
            V93_DISPATCH_VERSION,
            v93_request=V93DispatchRequest(
                analysis_ids[variant], variant, analysis_input
            ),
        )
    source = results[source_variant]
    dependency_status = (
        v93.DependencyVectorCheckStatus.VALID
        if _source_is_jointly_certified(source)
        else v93.DependencyVectorCheckStatus.INVALID
    )
    target_variant = v93.AnalysisVariant.LOC_THETA_CW
    results[target_variant] = dispatch_rta_version(
        V93_DISPATCH_VERSION,
        v93_request=V93DispatchRequest(
            analysis_ids[target_variant],
            target_variant,
            analysis_input,
            source=source,
            dependency_check_status=dependency_status,
        ),
    )
    return FiveConfigurationRun(tuple((variant, results[variant]) for variant in VARIANT_ORDER))


def map_task_failure_provenance(
    task_record: v93.TaskAnalysisRecord,
    analysis_result: v93.TasksetAnalysisResult,
    *,
    solver_origin: str = "DEFAULT_V9_3_CORE"
) -> StructuredTaskFailure:
    """Map audited analyzer outcomes to the frozen v1.3.12 formal language."""

    status = task_record.solver_status
    raw = task_record.failure_reason
    dominance = bool(
        analysis_result.dominance_counterexample
        and analysis_result.dominance_counterexample.task_id == task_record.task_id
    )
    if dominance:
        if (
            status
            not in {
                v93.TaskSolverStatus.CANDIDATE_FOUND,
                v93.TaskSolverStatus.NO_CANDIDATE,
            }
            or task_record.certification_status
            is not v93.TaskCertificationStatus.NOT_CERTIFIED
            or raw
            not in {None, "no v9.3 closure candidate by the task deadline"}
        ):
            raise RunnerConformanceError("invalid dominance failure provenance")
        code = "DOMINANCE_INVARIANT_VIOLATION"
    elif status is v93.TaskSolverStatus.CANDIDATE_FOUND:
        if raw is not None:
            raise RunnerConformanceError("candidate has raw failure reason")
        code = "NONE"
    elif status is v93.TaskSolverStatus.NO_CANDIDATE:
        if raw != "no v9.3 closure candidate by the task deadline":
            raise RunnerConformanceError("unknown no-candidate raw failure reason")
        code = "NO_CANDIDATE"
    elif status is v93.TaskSolverStatus.TIMEOUT:
        if solver_origin != "DEFAULT_V9_3_CORE" or not isinstance(raw, str) or not raw:
            raise RunnerConformanceError("unclassified timeout failure origin")
        code = "SOLVER_TIMEOUT"
    elif status is v93.TaskSolverStatus.NUMERIC_ERROR:
        if solver_origin != "DEFAULT_V9_3_CORE" or not isinstance(raw, str) or not raw:
            raise RunnerConformanceError("unclassified numeric failure origin")
        code = "NUMERIC_ERROR"
    elif status is v93.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE:
        if raw != "not evaluated after prefix failure":
            raise RunnerConformanceError("unknown prefix failure reason")
        code = "UPSTREAM_PREFIX_FAILURE"
    elif status is v93.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY:
        if (
            raw != "fixed carry-in dependency is not applicable"
            or analysis_result.dependency_check_status
            is not v93.DependencyVectorCheckStatus.INVALID
        ):
            raise RunnerConformanceError("invalid dependency failure provenance")
        code = "DEPENDENCY_NOT_APPLICABLE"
    elif status is v93.TaskSolverStatus.INTERNAL_CONFORMANCE_FAILURE:
        if solver_origin == "ADAPTER_UNKNOWN_CORE_STATUS" and raw in {
            None,
            "unknown core status",
        }:
            code = "UNKNOWN_CORE_STATUS"
        elif (
            solver_origin == "ANALYZER_INTERNAL_CONFORMANCE"
            and raw == "internal analyzer conformance failure"
        ):
            code = "INTERNAL_CONFORMANCE_FAILURE"
        else:
            raise RunnerConformanceError("unknown internal failure provenance")
    else:
        raise RunnerConformanceError("unsupported task solver status")
    return StructuredTaskFailure(code, FAILURE_DETAIL_BY_CODE[code])


def _copy_analyzer_field(row: Dict[str, Any], field: str, value: Any) -> None:
    existing = row.get(field)
    if existing is not None and existing != value:
        raise RunnerConformanceError(
            "serializer base attempted to override analyzer field {}".format(field)
        )
    row[field] = value


def _dependency_mask(
    source_row: Mapping[str, Any],
    target_row: Mapping[str, Any],
    source_task_row: Mapping[str, Any],
) -> Tuple[str, ...]:
    checks = (
        ("taskset_semantic_hash", "TASKSET_HASH_MISMATCH"),
        ("priority_rank_hash", "PRIORITY_HASH_MISMATCH"),
        ("analysis_E0_canonical_hash", "E0_HASH_MISMATCH"),
        ("analysis_service_curve_canonical_hash", "SERVICE_CURVE_HASH_MISMATCH"),
        ("analysis_power_vector_canonical_hash", "POWER_VECTOR_HASH_MISMATCH"),
        ("analysis_energy_unit_hash", "ENERGY_UNIT_HASH_MISMATCH"),
        ("energy_numeric_mode", "NUMERIC_MODE_MISMATCH"),
        ("energy_numeric_scale", "NUMERIC_SCALE_MISMATCH"),
        ("theory_document_sha256", "THEORY_HASH_MISMATCH"),
        ("fixed_carry_in_corollary_hash", "FIXED_CARRY_IN_INTERFACE_HASH_MISMATCH"),
        ("formal_contract_hash", "FORMAL_CONTRACT_HASH_MISMATCH"),
    )
    failures = {
        code for field, code in checks if source_row.get(field) != target_row.get(field)
    }
    if target_row.get("fixed_carry_in_corollary_status") != "ACTIVE":
        failures.add("COROLLARY_INACTIVE")
    if (
        source_row.get("analysis_certification_status") != "CERTIFIED_TASKSET"
        or source_task_row.get("task_certification_status") != "CERTIFIED"
    ):
        failures.add("DEPENDENCY_CERTIFICATION_MISMATCH")
    return tuple(sorted(failures))


def _task_dominance_status(
    result: v93.TasksetAnalysisResult, task_id: str
) -> str:
    if result.dominance_counterexample:
        return (
            "DOMINANCE_INVARIANT_VIOLATION"
            if result.dominance_counterexample.task_id == task_id
            else "NOT_CHECKED"
        )
    return result.dominance_invariant_status.value


def serialize_taskset_analysis_v1_3_12(
    result: v93.TasksetAnalysisResult,
    binding: V1312SchemaBinding,
    taskset_result_base: Mapping[str, Any],
    task_definitions: Mapping[str, Mapping[str, Any]],
    *,
    source: Optional[SerializedAnalysis] = None,
    solver_origin: str = "DEFAULT_V9_3_CORE"
) -> SerializedAnalysis:
    """Serialize analyzer-owned state without changing or completing it."""

    if result.taskset_proven != (
        result.certification_status
        is v93.AnalysisCertificationStatus.CERTIFIED_TASKSET
    ):
        raise RunnerConformanceError("analyzer taskset_proven invariant failed")
    if set(task_definitions) != {record.task_id for record in result.task_records}:
        raise RunnerConformanceError("task definitions do not match analyzer records")
    row = dict(taskset_result_base)
    expected_columns = set(binding.canonical_columns("per_taskset_results.csv"))
    if set(row) != expected_columns:
        raise RunnerConformanceError("per-taskset base does not have canonical shape")
    variant = result.analysis_variant
    analyzer_values = {
        "analysis_run_id": result.analysis_id,
        "analysis_method_role": result.method_role.value,
        "variant": variant.value,
        "window_mode": WINDOW_BY_VARIANT[variant],
        "carry_in_mode": CARRY_BY_VARIANT[variant],
        "n_tasks_total": result.n_tasks_total,
        "n_tasks_evaluated": result.n_tasks_evaluated,
        "n_tasks_candidate_found": result.n_tasks_candidate_found,
        "n_tasks_certified": result.n_tasks_certified,
        "analysis_solver_status": result.solver_status.value,
        "analysis_certification_status": result.certification_status.value,
        "fixed_carry_in_corollary_status": (
            result.fixed_carry_in_interface_status.value
        ),
        "taskset_proven": result.taskset_proven,
        "dominance_invariant_status": result.dominance_invariant_status.value,
        "dominance_violation_count": 1 if result.dominance_counterexample else 0,
        "envelope_call_count_total": str(
            sum(record.envelope_call_count for record in result.task_records)
        ),
        "first_non_candidate_priority": result.first_failed_priority,
    }
    for field, value in analyzer_values.items():
        _copy_analyzer_field(row, field, value)
    binding.encode_row("per_taskset_results.csv", row)

    source_task_rows = (
        {str(task["task_id"]): task for task in source.task_rows} if source else {}
    )
    source_analysis_row = source.taskset_row if source else None
    if variant is v93.AnalysisVariant.LOC_THETA_CW and source is None:
        raise RunnerConformanceError("LOC-Theta-cw serialization requires source")
    if variant is not v93.AnalysisVariant.LOC_THETA_CW and source is not None:
        raise RunnerConformanceError("source supplied for non-LOC-Theta-cw analysis")

    task_rows = []
    carry_entries_by_target: Dict[str, Tuple[Dict[str, str], ...]] = {}
    for record in result.task_records:
        definition = task_definitions[record.task_id]
        task_row = binding.empty_row("per_task_results.csv")
        failure = map_task_failure_provenance(
            record, result, solver_origin=solver_origin
        )
        task_row.update(
            {
                "analysis_run_id": result.analysis_id,
                "taskset_id": row["taskset_id"],
                "task_id": int(record.task_id),
                "analysis_method_role": result.method_role.value,
                "variant": variant.value,
                "window_mode": WINDOW_BY_VARIANT[variant],
                "carry_in_mode": CARRY_BY_VARIANT[variant],
                "priority_rank": record.priority_rank,
                "C_i": definition["C_i"],
                "T_i": definition["T_i"],
                "D_i": definition["D_i"],
                "P_hat_i_raw": definition["P_analysis"],
                "task_solver_status": record.solver_status.value,
                "task_certification_status": record.certification_status.value,
                "task_failure_reason_code": failure.code,
                "w_values_checked": record.checked_w_count,
                "h_values_checked": record.checked_h_count,
                "q_values_checked": record.checked_q_count,
                "full_w_scan_conformance": True,
                "full_h_scan_conformance": True,
                "full_q_scan_conformance": True,
                "envelope_call_count": record.envelope_call_count,
                "energy_numeric_mode": row["energy_numeric_mode"],
                "dominance_invariant_status": _task_dominance_status(
                    result, record.task_id
                ),
                "task_failure_detail": failure.detail,
                "candidate_response_time": record.candidate_response_time,
                "closing_w": record.closing_w,
                "witness_h": record.witness_h,
            }
        )
        if variant is v93.AnalysisVariant.LOC_THETA_CW:
            assert source_analysis_row is not None
            entries = []
            for source_record in sorted(
                source.task_rows, key=lambda candidate: int(candidate["priority_rank"])
            ):
                if int(source_record["priority_rank"]) >= record.priority_rank:
                    continue
                candidate = source_record.get("candidate_response_time")
                if candidate is None:
                    continue
                entries.append(
                    {
                        "hp_task_id": str(source_record["task_id"]),
                        "theta_value": str(candidate),
                        "source_analysis_run_id": str(
                            source_analysis_row["analysis_run_id"]
                        ),
                        "source_task_id": str(source_record["task_id"]),
                        "source_task_certification_status": str(
                            source_record["task_certification_status"]
                        ),
                    }
                )
            carry_entries_by_target[record.task_id] = tuple(entries)
            carry_hash = binding.carry_in_vector_hash(
                result.analysis_id, record.task_id, entries
            )
            aggregate_failures = set()
            for entry in entries:
                source_task = source_task_rows[entry["source_task_id"]]
                aggregate_failures.update(
                    _dependency_mask(source_analysis_row, row, source_task)
                )
            if not entries and source_analysis_row.get(
                "analysis_certification_status"
            ) != "CERTIFIED_TASKSET":
                aggregate_failures.add("DEPENDENCY_CERTIFICATION_MISMATCH")
            task_row.update(
                {
                    "source_analysis_run_id": source_analysis_row[
                        "analysis_run_id"
                    ],
                    "carry_in_vector_hash": carry_hash,
                    "carry_in_source_variant": source_analysis_row["variant"],
                    "carry_in_source_certification_status": source_analysis_row[
                        "analysis_certification_status"
                    ],
                    "fixed_carry_in_corollary_status": row[
                        "fixed_carry_in_corollary_status"
                    ],
                    "dependency_vector_check_status": (
                        result.dependency_check_status.value
                    ),
                    "dependency_input_failure_mask": binding.common.format_mask(
                        aggregate_failures
                    ),
                }
            )
        task_row["task_result_hash"] = binding.task_result_hash(task_row)
        binding.encode_row("per_task_results.csv", task_row)
        task_rows.append(task_row)

    dependency_rows = []
    if variant is v93.AnalysisVariant.LOC_THETA_CW:
        assert source is not None and source_analysis_row is not None
        for target_record in result.task_records:
            target_task_row = next(
                task for task in task_rows if str(task["task_id"]) == target_record.task_id
            )
            entries = carry_entries_by_target[target_record.task_id]
            for entry in entries:
                source_task_row = source_task_rows[entry["source_task_id"]]
                failures = _dependency_mask(
                    source_analysis_row, row, source_task_row
                )
                dependency_row = binding.empty_row("rta_dependency_records.csv")
                dependency_row.update(
                    {
                        "analysis_run_id": result.analysis_id,
                        "taskset_id": row["taskset_id"],
                        "target_task_id": target_record.task_id,
                        "hp_task_id": entry["hp_task_id"],
                        "theta_value": entry["theta_value"],
                        "theta_source_mode": "CW_CANDIDATE",
                        "source_analysis_run_id": source_analysis_row[
                            "analysis_run_id"
                        ],
                        "source_task_id": entry["source_task_id"],
                        "source_task_solver_status": source_task_row[
                            "task_solver_status"
                        ],
                        "source_task_certification_status": source_task_row[
                            "task_certification_status"
                        ],
                        "source_analysis_solver_status": source_analysis_row[
                            "analysis_solver_status"
                        ],
                        "source_analysis_certification_status": source_analysis_row[
                            "analysis_certification_status"
                        ],
                        "source_variant": source_analysis_row["variant"],
                        "source_theory_document_sha256": source_analysis_row[
                            "theory_document_sha256"
                        ],
                        "target_theory_document_sha256": row[
                            "theory_document_sha256"
                        ],
                        "source_taskset_semantic_hash": source_analysis_row[
                            "taskset_semantic_hash"
                        ],
                        "target_taskset_semantic_hash": row[
                            "taskset_semantic_hash"
                        ],
                        "source_priority_rank_hash": source_analysis_row[
                            "priority_rank_hash"
                        ],
                        "target_priority_rank_hash": row["priority_rank_hash"],
                        "source_analysis_E0_canonical_hash": source_analysis_row[
                            "analysis_E0_canonical_hash"
                        ],
                        "target_analysis_E0_canonical_hash": row[
                            "analysis_E0_canonical_hash"
                        ],
                        "source_analysis_service_curve_canonical_hash": source_analysis_row[
                            "analysis_service_curve_canonical_hash"
                        ],
                        "target_analysis_service_curve_canonical_hash": row[
                            "analysis_service_curve_canonical_hash"
                        ],
                        "source_analysis_power_vector_canonical_hash": source_analysis_row[
                            "analysis_power_vector_canonical_hash"
                        ],
                        "target_analysis_power_vector_canonical_hash": row[
                            "analysis_power_vector_canonical_hash"
                        ],
                        "source_analysis_energy_unit_hash": source_analysis_row[
                            "analysis_energy_unit_hash"
                        ],
                        "target_analysis_energy_unit_hash": row[
                            "analysis_energy_unit_hash"
                        ],
                        "source_energy_numeric_mode": source_analysis_row[
                            "energy_numeric_mode"
                        ],
                        "target_energy_numeric_mode": row["energy_numeric_mode"],
                        "fixed_carry_in_corollary_status": row[
                            "fixed_carry_in_corollary_status"
                        ],
                        "carry_in_vector_hash": target_task_row[
                            "carry_in_vector_hash"
                        ],
                        "dependency_vector_check_status": (
                            "VALID" if not failures else "INVALID"
                        ),
                        "dependency_input_failure_mask": binding.common.format_mask(
                            failures
                        ),
                        "source_plan_context_hash": source_analysis_row[
                            "plan_context_hash"
                        ],
                        "target_plan_context_hash": row["plan_context_hash"],
                        "source_fixed_carry_in_corollary_hash": source_analysis_row[
                            "fixed_carry_in_corollary_hash"
                        ],
                        "target_fixed_carry_in_corollary_hash": row[
                            "fixed_carry_in_corollary_hash"
                        ],
                        "source_formal_contract_hash": source_analysis_row.get(
                            "formal_contract_hash"
                        ),
                        "target_formal_contract_hash": row.get(
                            "formal_contract_hash"
                        ),
                    }
                )
                dependency_row["dependency_record_hash"] = (
                    binding.dependency_record_hash(dependency_row)
                )
                binding.encode_row(
                    "rta_dependency_records.csv", dependency_row
                )
                dependency_rows.append(dependency_row)

    return SerializedAnalysis(
        result,
        MappingProxyType(dict(row)),
        tuple(MappingProxyType(dict(task)) for task in task_rows),
        tuple(MappingProxyType(dict(dep)) for dep in dependency_rows),
    )


__all__ = [
    "DEFAULT_RTA_VERSION",
    "FAILURE_DETAIL_BY_CODE",
    "FiveConfigurationRun",
    "RunnerConformanceError",
    "SerializedAnalysis",
    "StructuredTaskFailure",
    "V21_DISPATCH_VERSION",
    "V93DispatchRequest",
    "V93_DISPATCH_VERSION",
    "dispatch_rta_version",
    "map_task_failure_provenance",
    "run_five_configurations_v9_3",
    "serialize_taskset_analysis_v1_3_12",
]
