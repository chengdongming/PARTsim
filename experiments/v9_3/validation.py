"""Fail-closed conformance checks around production analyzer results."""

from __future__ import annotations

from typing import Mapping, Optional

import asap_block_rta_v9_3_taskset as taskset

from .taskset_store import StoredTaskset


class ConformanceFailure(RuntimeError):
    pass


def validate_analysis_result(
    result: taskset.TasksetAnalysisResult,
    stored: StoredTaskset,
    *,
    expected_analysis_id: str,
    expected_variant: taskset.AnalysisVariant,
    source: Optional[taskset.TasksetAnalysisResult] = None,
) -> None:
    if result.analysis_id != expected_analysis_id:
        raise ConformanceFailure("analysis ID mismatch")
    if result.analysis_variant is not expected_variant:
        raise ConformanceFailure("analysis variant mismatch")
    if result.taskset_proven != (
        result.certification_status
        is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
    ):
        raise ConformanceFailure("taskset_proven/certification mismatch")
    if result.n_tasks_total != len(stored.tasks):
        raise ConformanceFailure("task count mismatch")
    ids = tuple(record.task_id for record in result.task_records)
    expected_ids = tuple(item.name for item in stored.tasks)
    if ids != expected_ids or len(ids) != len(set(ids)):
        raise ConformanceFailure("missing, reordered, or duplicate task rows")
    definitions = {item.name: item for item in stored.tasks}
    for record in result.task_records:
        definition = definitions[record.task_id]
        if record.certification_status is taskset.TaskCertificationStatus.CERTIFIED:
            if record.solver_status is not taskset.TaskSolverStatus.CANDIDATE_FOUND:
                raise ConformanceFailure("certified task is not CANDIDATE_FOUND")
            candidate = record.candidate_response_time
            if candidate is None or not definition.wcet <= candidate <= definition.deadline:
                raise ConformanceFailure("certified candidate violates C <= R <= D")
    if result.dominance_invariant_status is taskset.DominanceInvariantStatus.DOMINANCE_INVARIANT_VIOLATION:
        raise ConformanceFailure("P0 dominance invariant violation")
    if expected_variant is taskset.AnalysisVariant.LOC_THETA_CW:
        if source is None:
            if result.solver_status is not taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY:
                raise ConformanceFailure("LOC_THETA_CW ran without a source")
            return
        source_certified = bool(
            source.taskset_proven
            and source.certification_status is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
            and source.solver_status is taskset.AnalysisSolverStatus.COMPLETED
        )
        if not source_certified:
            if result.solver_status is not taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY:
                raise ConformanceFailure("uncertified source did not produce dependency N/A")
            if result.certification_status is not taskset.AnalysisCertificationStatus.NOT_APPLICABLE:
                raise ConformanceFailure("dependency failure has non-N/A certification")
            return
        source_vector = tuple(sorted(
            (record.task_id, record.candidate_response_time)
            for record in source.task_records
        ))
        if result.source_candidate_vector != source_vector:
            raise ConformanceFailure("source/target carry-in vector mismatch")
        if result.dependency_check_status is not taskset.DependencyVectorCheckStatus.VALID:
            raise ConformanceFailure("certified source dependency is not VALID")


def assert_unique(rows: list[Mapping[str, object]], *keys: str) -> None:
    seen = set()
    for row in rows:
        identity = tuple(row[key] for key in keys)
        if identity in seen:
            raise ConformanceFailure(f"duplicate row {identity}")
        seen.add(identity)
