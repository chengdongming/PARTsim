"""Execution bridge for the pre-pilot RTA4 profile.

PR-D deliberately authorizes descriptions and bounded synthetic fixtures only.
The production callbacks below reuse the unified adapter and PR-C projection;
they do not reinterpret either mathematical contract.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, MutableMapping, Sequence

import asap_block_rta_v9_3_methods as method_registry
from asap_block_rta_v9_3_taskset import (
    TasksetAnalysisInput, V93MethodTasksetAnalysisResult,
    analyze_method_taskset_v9_3,
)

from .constrained_taskset_identity import TasksetIdentityCertificate
from .release_applicability import (
    ReleaseObservationWindow, ReleaseProjection, build_release_projection,
    project_certificate_for_simulation,
)
from .rta4_formal_config import (
    RTA4_FORMAL_PARAMETER_STATUS, RTA4_FORMAL_PROFILE, canonical_json,
    domain_hash, exact_fraction, fraction_text, validate_rta4_formal_config,
)
from .rta4_formal_plan import FormalPlanRecord, describe_formal_plan


RTA4_MATH_RESULT_DOMAIN = b"ASAP_BLOCK:V9.3:RTA4_MATH_RESULT:v1\0"
RTA4_EXACT_INPUT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_EXACT_INPUT:v1"
RTA4_ANALYSIS_ID_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_ANALYSIS_ID:v1"
RTA4_EXECUTION_ID_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_ANALYSIS_EXECUTION:v1"
RTA4_TEST_FIXTURE_LIMIT = 100


class RTA4FormalAuthorizationError(RuntimeError):
    """Raised when PR-D is asked to perform a PR-E formal execution."""


class RTA4FormalPipelineError(RuntimeError):
    """Raised when a runner bridge would violate an upstream contract."""


def require_rta4_execution_authorization(
    config: Mapping[str, Any], *, dry_run: bool = False,
    nonformal_fixture_count: int | None = None,
) -> None:
    normalized = validate_rta4_formal_config(config)
    contract = normalized["experiment_contract"]
    if contract["profile"] != RTA4_FORMAL_PROFILE:
        raise RTA4FormalAuthorizationError("unknown formal execution profile")
    if contract["parameter_status"] != RTA4_FORMAL_PARAMETER_STATUS:
        raise RTA4FormalAuthorizationError("PR-D cannot freeze formal parameters")
    if dry_run:
        return
    if nonformal_fixture_count is not None:
        if (
            type(nonformal_fixture_count) is not int
            or not 0 <= nonformal_fixture_count <= RTA4_TEST_FIXTURE_LIMIT
        ):
            raise RTA4FormalAuthorizationError("nonformal fixture exceeds the 100-request limit")
        return
    raise RTA4FormalAuthorizationError(
        "RTA4 formal execution is UNAUTHORIZED_PRE_PILOT; PR-E authorization is required"
    )


def dispatch_formal_rta(
    *, analysis_id: str, method: str,
    analysis_input: TasksetAnalysisInput,
) -> V93MethodTasksetAnalysisResult:
    """Dispatch through the unified eight-method adapter without aliases."""

    spec = method_registry.method_spec_v9_3(method)
    return analyze_method_taskset_v9_3(
        analysis_id=analysis_id, method_spec=spec,
        analysis_input=analysis_input,
    )


def formal_analysis_identity(
    *, certificate: TasksetIdentityCertificate, method: str, exact_e0: Any,
    service_identity: str, numeric_contract_sha256: str,
    theory_document_sha256: str, timeout_contract: str,
    source_analysis_id: str | None = None,
) -> tuple[str, str, Mapping[str, Any]]:
    """Bind every mathematical input while excluding worker/output axes."""

    if type(certificate) is not TasksetIdentityCertificate:
        raise RTA4FormalPipelineError("analysis identity requires a PR-B certificate")
    certificate.validate()
    spec = method_registry.method_spec_v9_3(method)
    e0 = exact_fraction(exact_e0, "exact_e0")
    if e0 < 0:
        raise RTA4FormalPipelineError("exact_e0 must be non-negative")
    for identity, label in (
        (service_identity, "service_identity"),
        (numeric_contract_sha256, "numeric_contract_sha256"),
        (theory_document_sha256, "theory_document_sha256"),
    ):
        if not isinstance(identity, str) or len(identity) != 64:
            raise RTA4FormalPipelineError(f"{label} must be SHA-256")
    if not isinstance(timeout_contract, str) or not timeout_contract:
        raise RTA4FormalPipelineError("timeout_contract must be non-empty")
    if source_analysis_id is not None and (
        not isinstance(source_analysis_id, str) or len(source_analysis_id) != 64
    ):
        raise RTA4FormalPipelineError("source_analysis_id must be SHA-256")
    material = {
        "profile": RTA4_FORMAL_PROFILE,
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id,
        "taskset_hash": certificate.taskset_hash,
        "power_vector_hash": certificate.power_vector_hash,
        "method": spec.method_id.value,
        "kernel": spec.kernel.value,
        "carry_policy": spec.carry_policy.value,
        "exact_e0": fraction_text(e0),
        "service_identity": service_identity,
        "numeric_contract_sha256": numeric_contract_sha256,
        "theory_document_sha256": theory_document_sha256,
        "timeout_contract": timeout_contract,
        "source_analysis_id": source_analysis_id,
    }
    exact_input_identity = domain_hash(RTA4_EXACT_INPUT_DOMAIN, material)
    analysis_id = domain_hash(RTA4_ANALYSIS_ID_DOMAIN, {
        "exact_input_identity": exact_input_identity,
        "method": spec.method_id.value,
    })
    return analysis_id, exact_input_identity, material


def formal_execution_identity(
    analysis_id: str, *, worker_count: int, batch_identity: str,
) -> str:
    if not isinstance(analysis_id, str) or len(analysis_id) != 64:
        raise RTA4FormalPipelineError("analysis_id must be SHA-256")
    if type(worker_count) is not int or isinstance(worker_count, bool) or worker_count < 1:
        raise RTA4FormalPipelineError("worker_count must be positive")
    if not isinstance(batch_identity, str) or len(batch_identity) != 64:
        raise RTA4FormalPipelineError("batch_identity must be SHA-256")
    return domain_hash(RTA4_EXECUTION_ID_DOMAIN, {
        "analysis_id": analysis_id,
        "worker_count": worker_count,
        "batch_identity": batch_identity,
    })


def build_formal_release_projection(
    certificate: TasksetIdentityCertificate, release_mode: str,
) -> tuple[ReleaseProjection, ReleaseObservationWindow, tuple[Mapping[str, Any], ...]]:
    """Use PR-C as the sole offset and dual-horizon implementation."""

    projection = build_release_projection(certificate, release_mode=release_mode)
    window = ReleaseObservationWindow.for_certificate(certificate)
    payload = project_certificate_for_simulation(certificate, projection)
    return projection, window, payload


def mathematical_result_hash(result: Any) -> str:
    """Hash mathematical output while excluding execution-only measurements."""

    if is_dataclass(result):
        material: Any = asdict(result)
    elif isinstance(result, Mapping):
        material = dict(result)
    else:
        raise RTA4FormalPipelineError("mathematical result must be a dataclass or mapping")

    execution_keys = {
        "runtime_wall", "runtime_wall_seconds", "runtime_cpu",
        "runtime_cpu_seconds", "peak_rss", "peak_rss_bytes", "worker_count",
        "execution_run_id", "started_at_utc", "output_path",
    }

    def strip(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): strip(item) for key, item in value.items()
                if str(key) not in execution_keys and not str(key).startswith("_")
            }
        if isinstance(value, (tuple, list)):
            return [strip(item) for item in value]
        if hasattr(value, "value") and type(value).__module__ == "enum":
            return value.value
        return value

    encoded = canonical_json(strip(material)).encode("utf-8")
    return hashlib.sha256(RTA4_MATH_RESULT_DOMAIN + encoded).hexdigest()


def mechanism_telemetry_rows(result: V93MethodTasksetAnalysisResult) -> tuple[Dict[str, Any], ...]:
    """Project only adapter-validated telemetry; absent fields remain ``NA``."""

    rows = []
    method = result.method_id.value
    for task_result in result.task_results:
        adapter_fields = {
            "impossible_prefix_count": "impossible_prefix_count",
            "flow_call_count": "flow_solver_calls",
            "flow_node_count": "flow_node_count",
            "flow_edge_count": "flow_edge_count",
            "z_branch_count": "z_branch_count",
            "flow_infeasible_count": "flow_infeasible_count",
            "safety_predicate_calls": "phase_safe_calls",
        }

        def value(name: str) -> Any:
            observed = getattr(task_result, adapter_fields.get(name, name), None)
            return "NA" if observed is None else observed

        sequence = task_result.witness_sequence
        sequence_kind = (
            "NA" if not sequence else
            "CONSTANT" if len(set(sequence)) == 1 else "NONCONSTANT"
        )
        available = any(value(name) != "NA" for name in adapter_fields)

        rows.append({
            "analysis_id": result.analysis_id,
            "method": method,
            "task_id": task_result.task_id,
            "priority_rank": task_result.priority_rank,
            "telemetry_status": "AVAILABLE" if available else "NOT_APPLICABLE_OR_UNAVAILABLE",
            "impossible_prefix_count": value("impossible_prefix_count"),
            "empty_phase_set_count": value("empty_phase_set_count"),
            "strict_ph_lt_loc_checkpoints": value("strict_ph_lt_loc_checkpoints"),
            "flow_call_count": value("flow_call_count"),
            "flow_node_count": value("flow_node_count"),
            "flow_edge_count": value("flow_edge_count"),
            "z_branch_count": value("z_branch_count"),
            "flow_optimal_count": "NA",
            "flow_infeasible_count": value("flow_infeasible_count"),
            "flow_timeout_count": value("flow_timeout_count"),
            "flow_internal_count": value("flow_internal_count"),
            "ph_no_common_h_but_seq_exists": value("ph_no_common_h_but_seq_exists"),
            "sequence_kind": sequence_kind,
            "sequence_length": len(sequence) if sequence else "NA",
            "distinct_h_count": len(set(sequence)) if sequence else "NA",
            "last_h": sequence[-1] if sequence else "NA",
            "strict_seq_lt_ph": value("strict_seq_lt_ph"),
            "safety_predicate_calls": value("safety_predicate_calls"),
            "cache_hits": value("cache_hits"),
            "cache_misses": value("cache_misses"),
            "cache_hit_rate": value("cache_hit_rate"),
        })
    return tuple(rows)


class SimulationDeduplicator:
    """Ensure method/E0 projections never rerun one mathematical simulation."""

    def __init__(self) -> None:
        self._results: MutableMapping[str, Any] = {}

    def execute_once(
        self, simulation_id: str, callback: Callable[[], Any],
    ) -> Any:
        if simulation_id not in self._results:
            self._results[simulation_id] = callback()
        return self._results[simulation_id]

    @property
    def unique_simulation_count(self) -> int:
        return len(self._results)


class RTA4FormalRunner:
    """Pre-pilot runner facade: descriptions are allowed, formal runs are not."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = validate_rta4_formal_config(config)

    def describe(self) -> Dict[str, Any]:
        require_rta4_execution_authorization(self.config, dry_run=True)
        return describe_formal_plan(self.config)

    def run(self) -> None:
        require_rta4_execution_authorization(self.config)
        raise AssertionError("authorization guard returned unexpectedly")

    def run_nonformal_fixture(
        self, records: Sequence[FormalPlanRecord],
        callback: Callable[[FormalPlanRecord], Any],
    ) -> tuple[Any, ...]:
        require_rta4_execution_authorization(
            self.config, nonformal_fixture_count=len(records),
        )
        return tuple(callback(record) for record in records)


__all__ = [
    "RTA4FormalAuthorizationError", "RTA4FormalPipelineError",
    "RTA4FormalRunner", "SimulationDeduplicator",
    "build_formal_release_projection", "dispatch_formal_rta",
    "formal_analysis_identity", "formal_execution_identity",
    "mathematical_result_hash", "mechanism_telemetry_rows",
    "require_rta4_execution_authorization",
]
