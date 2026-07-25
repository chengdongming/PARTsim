"""Execution bridge for the pre-pilot RTA4 profile.

PR-D deliberately authorizes descriptions and bounded synthetic fixtures only.
The production callbacks below reuse the unified adapter and PR-C projection;
they do not reinterpret either mathematical contract.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, MutableMapping, Sequence

import asap_block_rta_v9_3_methods as method_registry
from asap_block_rta_v9_3_taskset import (
    TasksetAnalysisInput, V93MethodTasksetAnalysisResult,
    analyze_method_taskset_v9_3,
)

from .constrained_taskset_identity import TasksetIdentityCertificate
from .release_applicability import (
    FINITE_BATTERY_EMPIRICAL, RTA_FAIL, RTA_PASS,
    SIMULATOR_TRACE_CONTRACT_VERSION, TARGET_SCHEDULER,
    ReleaseObservationWindow, ReleaseProjection, assess_applicability,
    build_no_overflow_evidence, build_release_projection,
    evaluate_e0_condition, parse_release_trace,
    project_certificate_for_simulation, simulation_applicability_identity,
    validate_simulation_evidence,
)
from .rta4_formal_config import (
    RTA4_FORMAL_PARAMETER_STATUS, RTA4_FORMAL_PROFILE, canonical_json,
    domain_hash, exact_fraction, fraction_text, validate_rta4_formal_config,
)
from .rta4_formal_plan import FormalPlanRecord, describe_formal_plan
from .rta4_formal_manifest import (
    NONFORMAL_TEST_FIXTURE, build_trusted_plan_manifest,
)
from .rta4_formal_plan import formal_service_identity
from .rta4_formal_store import RTA4FormalTasksetStore


RTA4_MATH_RESULT_DOMAIN = b"ASAP_BLOCK:V9.3:RTA4_MATH_RESULT:v1\0"
RTA4_EXACT_INPUT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_EXACT_INPUT:v1"
RTA4_ANALYSIS_ID_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_ANALYSIS_ID:v1"
RTA4_EXECUTION_ID_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_ANALYSIS_EXECUTION:v1"
RTA4_TEST_FIXTURE_LIMIT = 100
RTA4_APPLICABILITY_ROW_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_APPLICABILITY_ROW:v1"


class RTA4FormalAuthorizationError(RuntimeError):
    """Raised when PR-D is asked to perform a PR-E formal execution."""


class RTA4FormalPipelineError(RuntimeError):
    """Raised when a runner bridge would violate an upstream contract."""


class RTA4FixtureInterruption(RuntimeError):
    """Deterministic interruption hook used to verify the real resume path."""


def _canonical_task_result_material(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the persisted raw mathematical task summary used for hashing."""

    return {
        "task_id": str(row["task_id"]),
        "priority_rank": int(row["priority_rank"]),
        "task_solver_status": str(row["task_solver_status"]),
        "task_certification_status": str(row["task_certification_status"]),
        "candidate_response_time": str(row["candidate_response_time"]),
        "D": int(row["D"]),
        "checked_w_count": int(row["checked_w_count"]),
        "checked_q_count": int(row["checked_q_count"]),
        "checked_h_count": int(row["checked_h_count"]),
        "failure_reason": str(row["failure_reason"]),
        "witness_hash": str(row["witness_hash"]),
    }


def recompute_rta_result_hashes(
    result: Mapping[str, Any], task_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Recompute all hard-check summaries from canonical persisted raw rows."""

    canonical_tasks = [_canonical_task_result_material(row) for row in task_rows]
    task_hashes = [
        domain_hash("ASAP_BLOCK:V9.3:RTA4_TASK_RESULT:v1", material)
        for material in canonical_tasks
    ]
    mathematical = {
        "solver_status": str(result["solver_status"]),
        "taskset_certification_status": str(result["taskset_certification_status"]),
        "taskset_proven": bool(
            result["taskset_proven"] is True
            or str(result["taskset_proven"]).lower() == "true"
        ),
        "failure_reason": str(result["failure_reason"]),
        "fallback_used": bool(
            result["fallback_used"] is True
            or str(result["fallback_used"]).lower() == "true"
        ),
        "task_results": canonical_tasks,
    }
    return {
        "exact_result_hash": mathematical_result_hash(mathematical),
        "candidate_vector_hash": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_CANDIDATE_VECTOR:v1",
            [row["candidate_response_time"] for row in canonical_tasks],
        ),
        "witness_vector_hash": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_WITNESS_VECTOR:v1",
            [row["witness_hash"] for row in canonical_tasks],
        ),
        "certification_vector_hash": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_CERTIFICATION_VECTOR:v1",
            [row["task_certification_status"] for row in canonical_tasks],
        ),
        "failure_reason_vector_hash": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_FAILURE_VECTOR:v1",
            [row["failure_reason"] for row in canonical_tasks],
        ),
        "task_hashes": task_hashes,
    }


def formal_comparison_status(
    e0_status: str, rta_outcome: str, simulation_outcome: str,
) -> str:
    from .release_applicability import E0_CONDITION_NOT_SATISFIED

    if e0_status == E0_CONDITION_NOT_SATISFIED:
        return E0_CONDITION_NOT_SATISFIED
    return {
        (RTA_PASS, "SIM_DEADLINE_MISS"): "RTA_PASS_SIM_FAIL",
        (RTA_PASS, "SIM_NO_DEADLINE_MISS"): "RTA_PASS_SIM_PASS",
        (RTA_FAIL, "SIM_DEADLINE_MISS"): "RTA_FAIL_SIM_FAIL",
        (RTA_FAIL, "SIM_NO_DEADLINE_MISS"): "RTA_FAIL_SIM_PASS",
    }[(rta_outcome, simulation_outcome)]


def formal_comparison_id(
    *, plan_comparison_id: str, analysis_id: str, simulation_id: str,
    release_audit_id: str, e0_evaluation_id: str,
    no_overflow_evidence_id: str, validated_simulation_evidence_id: str,
    rta_outcome: str, simulation_outcome: str,
) -> str:
    return domain_hash(RTA4_APPLICABILITY_ROW_DOMAIN, {
        "plan_comparison_id": plan_comparison_id,
        "analysis_id": analysis_id,
        "simulation_id": simulation_id,
        "release_audit_id": release_audit_id,
        "e0_evaluation_id": e0_evaluation_id,
        "no_overflow_evidence_id": no_overflow_evidence_id,
        "validated_simulation_evidence_id": validated_simulation_evidence_id,
        "rta_outcome": rta_outcome,
        "simulation_outcome": simulation_outcome,
    })


def recompute_simulation_result_hash(row: Mapping[str, Any]) -> str:
    excluded = {
        "schema_version", "schema_sha256", "plan_sha256",
        "config_semantic_hash", "trace_path",
    }
    return mathematical_result_hash({
        key: str(row[key]) for key in sorted(row) if key not in excluded
    })


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
    """Real bounded persistence/resume path; formal execution remains denied."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = validate_rta4_formal_config(config)

    def describe(self) -> Dict[str, Any]:
        require_rta4_execution_authorization(self.config, dry_run=True)
        return describe_formal_plan(self.config)

    def run(self) -> None:
        require_rta4_execution_authorization(self.config)
        raise AssertionError("authorization guard returned unexpectedly")

    def _validate_plan_certificate(
        self, record: FormalPlanRecord, certificate: TasksetIdentityCertificate,
    ) -> None:
        """Bind a PR-B certificate to the trusted plan generation dimensions."""

        certificate.validate()
        request = certificate.generation_request
        generation = self.config["generation"]
        material = record.material
        exact_pairs = {
            "formal_master_seed": generation["formal_master_seed"],
            "processors": material.get("processor_count", 4),
            "task_count": material.get("task_count", 10),
            "target_normalized_utilization": Fraction(
                str(material.get("normalized_utilization", "1/2"))
            ),
            "replicate_index": material.get("replicate_index", 0),
            "period_min": generation["period_min"],
            "period_max": generation["period_max"],
            "utilization_allocation_mode": generation["utilization_allocation_mode"],
            "min_task_utilization": Fraction(generation["minimum_task_utilization"]),
            "max_task_utilization": Fraction(generation["maximum_task_utilization"]),
            "utilization_tolerance": Fraction(generation["utilization_tolerance"]),
            "wcet_rounding_mode": generation["wcet_rounding"],
            "generator_version": generation["generator_version"],
            "power_generation_mode": generation["power_generation_mode"],
            "priority_policy": generation["priority_policy"],
        }
        if any(getattr(request, key) != value for key, value in exact_pairs.items()):
            raise RTA4FormalPipelineError(
                "taskset certificate generation provenance does not match trusted plan"
            )
        expected_power = Fraction(str(material.get("power_scale", "1")))
        if certificate.power_variant.scale != expected_power:
            raise RTA4FormalPipelineError("taskset certificate power variant/plan mismatch")
        expected_deadline = str(
            material.get("deadline_variant", "constrained_uniform_slack_v1")
        )
        if expected_deadline.startswith("fixed_slack_fraction_v1:"):
            expected_slack = Fraction(expected_deadline.split(":", 1)[1])
            if certificate.deadline_variant.fixed_slack_fraction != expected_slack:
                raise RTA4FormalPipelineError(
                    "taskset certificate deadline variant/plan mismatch"
                )
        elif certificate.deadline_variant.mode != expected_deadline:
            raise RTA4FormalPipelineError(
                "taskset certificate deadline variant/plan mismatch"
            )

    def run_nonformal_fixture(
        self, records: Sequence[FormalPlanRecord], *, root: Path | str,
        taskset_store: Path | str,
        certificate_provider: Callable[[FormalPlanRecord], TasksetIdentityCertificate],
        rta_executor: Callable[[FormalPlanRecord, TasksetIdentityCertificate], Mapping[str, Any]] | None = None,
        simulator_executor: Callable[..., Mapping[str, Any]] | None = None,
        source_closures: Mapping[str, Path | str | Any] | None = None,
        interrupt_after: int | None = None,
    ) -> Any:
        """Execute trusted plan records through the production persistence path.

        The caller supplies only bounded fake mathematical callbacks.  Plan
        membership, taskset persistence, attempts, terminals, simulation
        deduplication, resume, source joins and final closure validation are the
        same contracts intended for a future PR-E runner.
        """

        require_rta4_execution_authorization(
            self.config, nonformal_fixture_count=len(records),
        )
        if interrupt_after is not None and (
            type(interrupt_after) is not int or interrupt_after < 0
        ):
            raise RTA4FormalPipelineError("interrupt_after must be non-negative")
        ordinals = tuple(record.ordinal for record in records)
        manifest = build_trusted_plan_manifest(
            self.config, execution_class=NONFORMAL_TEST_FIXTURE,
            fixture_ordinals=ordinals,
        )
        if [record.record_id for record in records] != [
            row["plan_record_id"] for row in manifest["plan_records"]
        ]:
            raise RTA4FormalPipelineError("fixture records are not the trusted plan selection")
        from .rta4_formal_writer import RTA4FormalResultWriter
        writer = RTA4FormalResultWriter(
            root, config=self.config, fixture_ordinals=ordinals,
            execution_class=NONFORMAL_TEST_FIXTURE,
        )
        store = RTA4FormalTasksetStore(taskset_store)
        completed = 0
        if interrupt_after == 0:
            raise RTA4FixtureInterruption(
                "deterministic bounded-fixture interruption"
            )
        for record in records:
            certificate = certificate_provider(record)
            if type(certificate) is not TasksetIdentityCertificate:
                raise RTA4FormalPipelineError("certificate provider returned an invalid certificate")
            self._validate_plan_certificate(record, certificate)
            terminal = writer.terminals / f"{record.execution_id}.json"
            if terminal.is_file():
                from .result_writer import read_csv
                table = (
                    "formal_simulation_runs.csv"
                    if record.kind == "simulation" else "formal_rta_requests.csv"
                )
                persisted = next(
                    (
                        row for row in read_csv(writer.root / table)
                        if row["plan_record_id"] == record.record_id
                    ),
                    None,
                )
                if persisted is None or persisted["taskset_id"] != certificate.taskset_id:
                    raise RTA4FormalPipelineError(
                        "resume certificate differs from completed plan record"
                    )
                continue
            writer.persist_taskset(store, certificate)
            if record.kind == "simulation":
                if simulator_executor is None:
                    raise RTA4FormalPipelineError("simulation record requires simulator_executor")
                self._execute_simulation(writer, record, certificate, simulator_executor)
            else:
                if rta_executor is None:
                    raise RTA4FormalPipelineError("RTA record requires rta_executor")
                self._execute_rta_request(writer, record, certificate, rta_executor)
            completed += 1
            if interrupt_after is not None and completed >= interrupt_after:
                raise RTA4FixtureInterruption("deterministic bounded-fixture interruption")
        self._persist_source_dependencies(writer, source_closures or {})
        self._persist_applicability(writer, source_closures or {})
        self._persist_hard_checks(writer)
        self._persist_worker_consistency(writer)
        from .rta4_formal_validation import validate_formal_run_closure
        return validate_formal_run_closure(
            writer.root, require_complete=True,
            source_closures=source_closures or {},
        )

    def _request_material(
        self, record: FormalPlanRecord, certificate: TasksetIdentityCertificate,
    ) -> tuple[Dict[str, Any], str, str]:
        material = record.material
        method = str(material["method"])
        service_scale = str(material.get("service_scale", "1"))
        service_identity = formal_service_identity(service_scale)
        analysis_id, exact_input, _ = formal_analysis_identity(
            certificate=certificate, method=method,
            exact_e0=material["exact_e0"], service_identity=service_identity,
            numeric_contract_sha256=self.config["identity"]["numeric_contract_sha256"],
            theory_document_sha256=self.config["identity"]["theory_document_sha256"],
            timeout_contract=self.config["execution"]["timeout_contract"],
        )
        spec = method_registry.method_spec_v9_3(method)
        cell_material = {
            "core": record.core, "scenario": material.get("scenario", "MAIN"),
            "axis": material.get("axis", "baseline"),
            "axis_value": material.get("axis_value", "baseline"),
            "processor_count": material.get("processor_count", 4),
            "task_count": material.get("task_count", 10),
            "normalized_utilization": material.get("normalized_utilization", "1/2"),
            "exact_e0": material["exact_e0"],
            "service_scale": service_scale,
            "power_scale": material.get("power_scale", "1"),
            "deadline_variant": material.get(
                "deadline_variant", "constrained_uniform_slack_v1"
            ),
        }
        request = {
            "plan_record_id": record.record_id, "analysis_id": analysis_id,
            "request_id": record.mathematical_request_id,
            "execution_run_id": record.execution_id,
            "cell_id": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_FIXTURE_CELL:v1", cell_material
            ),
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            "taskset_slot_id": record.taskset_slot_id,
            "taskset_skeleton_id": certificate.taskset_skeleton_id,
            "taskset_id": certificate.taskset_id, "taskset_hash": certificate.taskset_hash,
            "method": method,
            "method_role": "WORKER_CONSISTENCY" if record.core == "CORE-5B" else "MAIN_METHOD",
            "carry_policy": spec.carry_policy.value, "exact_e0": str(material["exact_e0"]),
            "service_identity": service_identity, "power_vector_hash": certificate.power_vector_hash,
            "theory_document_sha256": self.config["identity"]["theory_document_sha256"],
            "numeric_contract_sha256": self.config["identity"]["numeric_contract_sha256"],
            "exact_input_identity": exact_input,
            "timeout_contract": self.config["execution"]["timeout_contract"],
            "source_analysis_id": "NA", "scenario": material.get("scenario", "MAIN"),
            "axis": material.get("axis", "baseline"), "axis_value": material.get("axis_value", "baseline"),
            "service_scale": service_scale, "power_scale": material.get("power_scale", "1"),
            "deadline_variant": material.get("deadline_variant", "constrained_uniform_slack_v1"),
            "worker_count": material.get("worker_count", 1), "request_status": "PLANNED",
        }
        return request, analysis_id, exact_input

    @staticmethod
    def _vector_hash(domain: str, value: Any) -> str:
        return domain_hash(f"ASAP_BLOCK:V9.3:RTA4_{domain}:v1", value)

    def _execute_rta_request(
        self, writer: Any, record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate,
        executor: Callable[[FormalPlanRecord, TasksetIdentityCertificate], Mapping[str, Any]],
    ) -> None:
        request, analysis_id, _ = self._request_material(record, certificate)
        material = record.material
        writer.append_unique("formal_cells.csv", "cell_id", {
            "cell_id": request["cell_id"], "core": record.core,
            "scenario": material.get("scenario", "MAIN"),
            "axis": material.get("axis", "baseline"),
            "axis_value": material.get("axis_value", "baseline"),
            "processor_count": material.get("processor_count", 4),
            "task_count": material.get("task_count", 10),
            "normalized_utilization": material.get("normalized_utilization", "1/2"),
            "exact_e0": material["exact_e0"],
            "service_scale": material.get("service_scale", "1"),
            "power_scale": material.get("power_scale", "1"),
            "deadline_variant": material.get(
                "deadline_variant", "constrained_uniform_slack_v1"
            ),
            "generation_status": "GENERATED_AND_CERTIFIED",
        })
        writer.append_unique("formal_rta_requests.csv", "plan_record_id", request)
        result = dict(executor(record, certificate))
        task_results = result.get("task_results")
        if not isinstance(task_results, Sequence) or isinstance(task_results, (str, bytes)):
            raise RTA4FormalPipelineError("RTA fixture result requires ordered task_results")
        if len(task_results) != len(certificate.tasks):
            raise RTA4FormalPipelineError("RTA fixture task result count mismatch")
        canonical_tasks = []
        persisted_tasks = []
        for task, raw in zip(certificate.tasks, task_results):
            if not isinstance(raw, Mapping):
                raise RTA4FormalPipelineError("RTA fixture task result must be a mapping")
            candidate = raw.get("candidate_response_time", "NA")
            witness = raw.get("witness", ())
            witness_hash = self._vector_hash("TASK_WITNESS", witness)
            mathematical = _canonical_task_result_material({
                "task_id": task.task_id, "priority_rank": task.priority_rank,
                "task_solver_status": raw.get("task_solver_status", "CANDIDATE_FOUND"),
                "task_certification_status": raw.get("task_certification_status", "CERTIFIED"),
                "candidate_response_time": candidate, "D": task.relative_deadline,
                "checked_w_count": raw.get("checked_w_count", 0),
                "checked_q_count": raw.get("checked_q_count", 0),
                "checked_h_count": raw.get("checked_h_count", 0),
                "failure_reason": raw.get("failure_reason", "NA"),
                "witness_hash": witness_hash,
            })
            exact_task_hash = self._vector_hash("TASK_RESULT", mathematical)
            canonical_tasks.append(mathematical)
            persisted_tasks.append({
                "task_result_id": domain_hash("ASAP_BLOCK:V9.3:RTA4_TASK_RESULT_ROW:v1", {
                    "execution_run_id": record.execution_id, "task_id": task.task_id,
                }),
                "plan_record_id": record.record_id, "analysis_id": analysis_id,
                "request_id": record.mathematical_request_id, "execution_run_id": record.execution_id,
                "taskset_skeleton_id": certificate.taskset_skeleton_id,
                "taskset_id": certificate.taskset_id, "method": record.material["method"],
                "exact_e0": record.material["exact_e0"], "task_id": task.task_id,
                "priority_rank": task.priority_rank,
                "task_solver_status": mathematical["task_solver_status"],
                "task_certification_status": mathematical["task_certification_status"],
                "candidate_response_time": candidate, "D": task.relative_deadline,
                "checked_w_count": mathematical["checked_w_count"],
                "checked_q_count": mathematical["checked_q_count"],
                "checked_h_count": mathematical["checked_h_count"],
                "failure_reason": mathematical["failure_reason"],
                "witness_hash": witness_hash, "exact_task_result_hash": exact_task_hash,
            })
        solver_status = str(result.get("solver_status", "COMPLETED"))
        mathematical = {
            "solver_status": solver_status,
            "taskset_certification_status": result.get("taskset_certification_status", "CERTIFIED_TASKSET"),
            "taskset_proven": bool(result.get("taskset_proven", True)),
            "failure_reason": result.get("failure_reason", "NA"),
            "fallback_used": bool(result.get("fallback_used", False)),
            "task_results": canonical_tasks,
        }
        hashes = recompute_rta_result_hashes(mathematical, canonical_tasks)
        exact_result_hash = hashes["exact_result_hash"]
        candidate_hash = hashes["candidate_vector_hash"]
        witness_hash = hashes["witness_vector_hash"]
        certification_hash = hashes["certification_vector_hash"]
        failure_hash = hashes["failure_reason_vector_hash"]
        attempt_number = sum(
            row.get("execution_run_id") == record.execution_id
            for row in __import__("experiments.v9_3.result_writer", fromlist=["read_csv"]).read_csv(
                writer.root / "formal_rta_attempts.csv"
            )
        ) + 1
        attempt_id = domain_hash("ASAP_BLOCK:V9.3:RTA4_ATTEMPT:v1", {
            "execution_run_id": record.execution_id, "attempt_number": attempt_number,
        })
        failure_origin = result.get("failure_origin")
        if failure_origin is None:
            failure_origin = (
                "RTA_EXECUTOR" if solver_status in {"NUMERIC_ERROR", "INTERNAL_ERROR"}
                else "NA"
            )
        writer.append_attempt({
            "attempt_id": attempt_id, "plan_record_id": record.record_id,
            "analysis_id": analysis_id, "request_id": record.mathematical_request_id,
            "execution_run_id": record.execution_id, "attempt_number": attempt_number,
            "parent_attempt_id": "NA", "worker_count": record.material.get("worker_count", 1),
            "timeout_budget_seconds": 0, "solver_status": solver_status,
            "failure_origin": failure_origin,
            "runtime_wall_seconds": result.get("runtime_wall_seconds", "0"),
            "runtime_cpu_seconds": result.get("runtime_cpu_seconds", "0"),
            "peak_rss_bytes": result.get("peak_rss_bytes", 0),
            "started_at_utc": "NONFORMAL_TEST_FIXTURE",
        })
        failed = next((task.priority_rank for task, raw in zip(certificate.tasks, canonical_tasks)
                       if raw["task_certification_status"] != "CERTIFIED"), None)
        utilization = sum((Fraction(task.wcet, task.period) for task in certificate.tasks), Fraction()) / certificate.processors
        result_row = {
            **{key: request[key] for key in (
                "plan_record_id", "analysis_id", "request_id", "execution_run_id", "cell_id",
                "taskset_skeleton_slot_id", "taskset_slot_id", "taskset_skeleton_id", "taskset_id",
                "taskset_hash", "method", "method_role", "carry_policy", "exact_e0",
                "service_identity", "power_vector_hash", "theory_document_sha256",
                "numeric_contract_sha256", "exact_input_identity", "timeout_contract",
                "source_analysis_id", "scenario", "axis", "axis_value", "service_scale",
                "power_scale", "deadline_variant",
            )},
            "solver_status": solver_status,
            "taskset_certification_status": mathematical["taskset_certification_status"],
            "taskset_proven": mathematical["taskset_proven"],
            "first_failed_priority": "NA" if failed is None else failed,
            "failure_reason": mathematical["failure_reason"],
            "timeout": solver_status == "TIMEOUT",
            "runtime_wall_seconds": result.get("runtime_wall_seconds", "0"),
            "runtime_cpu_seconds": result.get("runtime_cpu_seconds", "0"),
            "peak_rss_bytes": result.get("peak_rss_bytes", 0),
            "checked_w_count": sum(int(row.get("checked_w_count", 0)) for row in task_results),
            "checked_q_count": sum(int(row.get("checked_q_count", 0)) for row in task_results),
            "checked_h_count": sum(int(row.get("checked_h_count", 0)) for row in task_results),
            "exact_result_hash": exact_result_hash, "candidate_vector_hash": candidate_hash,
            "witness_vector_hash": witness_hash, "certification_vector_hash": certification_hash,
            "failure_reason_vector_hash": failure_hash,
            "fallback_used": mathematical["fallback_used"],
            "normalized_utilization": utilization,
        }
        writer.append_unique("formal_rta_taskset_results.csv", "execution_run_id", result_row)
        for row in persisted_tasks:
            writer.append_unique("formal_rta_task_results.csv", "task_result_id", row)
        writer.write_terminal(record.execution_id, {
            "plan_record_id": record.record_id, "analysis_id": analysis_id,
            "request_id": record.mathematical_request_id, "worker_count": record.material.get("worker_count", 1),
            "solver_status": solver_status, "exact_result_hash": exact_result_hash,
            "candidate_vector_hash": candidate_hash, "witness_vector_hash": witness_hash,
            "certification_vector_hash": certification_hash,
            "failure_reason_vector_hash": failure_hash,
        })

    def _execute_simulation(self, writer: Any, record: FormalPlanRecord,
                            certificate: TasksetIdentityCertificate,
                            executor: Callable[..., Mapping[str, Any]]) -> None:
        material = record.material
        projection, window, payload = build_formal_release_projection(certificate, material["release_mode"])
        service_identity = formal_service_identity(material["service_scale"])
        capacity = material["battery_capacity"]
        initial = material["physical_initial_energy"]
        simulation_id = simulation_applicability_identity(
            taskset_id=certificate.taskset_id, release_projection_id=projection.release_projection_id,
            scheduler=TARGET_SCHEDULER, service_identity=service_identity,
            initial_battery=initial, battery_capacity=capacity, window=window,
            applicability_track=material["applicability_track"],
        )
        observed = dict(executor(record, certificate, projection, window, payload, simulation_id))
        trace_source = Path(observed["trace_path"])
        trace_dir = writer.root / "formal_simulation_traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_target = trace_dir / f"{simulation_id}.json"
        if trace_target.is_file() and trace_target.read_bytes() != trace_source.read_bytes():
            raise RTA4FormalPipelineError("simulation cache/trace conflict")
        if not trace_target.is_file():
            from .result_writer import atomic_write_text
            atomic_write_text(trace_target, trace_source.read_text(encoding="utf-8"))
        audit = parse_release_trace(
            trace_target, payload, expected_simulation_id=simulation_id,
            expected_taskset_hash=certificate.taskset_hash,
            expected_certificate=certificate, expected_projection=projection,
            window=window,
        )
        no_overflow = build_no_overflow_evidence(
            initial_battery=initial, battery_capacity=capacity,
            offered_harvest=observed.get("offered_harvest", "0"),
            required_margin=observed.get("required_margin", "0"),
            service_identity=service_identity,
            observation_horizon=window.observation_horizon,
        )
        evidence = validate_simulation_evidence(
            audit, service_identity=service_identity, initial_battery=initial,
            battery_capacity=capacity,
            applicability_track=material["applicability_track"],
        )
        (
            deadline_miss_count, max_observed_response,
            task_result_vector_hash, job_result_vector_hash,
        ) = self._persist_simulation_jobs(
            writer, simulation_id, certificate, projection, window, observed
        )
        if "deadline_miss_count" in observed and int(
            observed["deadline_miss_count"]
        ) != deadline_miss_count:
            raise RTA4FormalPipelineError(
                "simulator deadline summary contradicts canonical job evidence"
            )
        if "max_observed_response" in observed and int(
            observed["max_observed_response"]
        ) != max_observed_response:
            raise RTA4FormalPipelineError(
                "simulator response summary contradicts canonical job evidence"
            )
        if (audit.simulation_outcome == "SIM_DEADLINE_MISS") != (
            deadline_miss_count > 0
        ):
            raise RTA4FormalPipelineError(
                "simulator deadline count contradicts validated trace outcome"
            )
        if observed.get("simulation_status", "COMPLETED") != "COMPLETED":
            raise RTA4FormalPipelineError(
                "bounded fixture requires a completed validated simulation"
            )
        sim_row = {
            "plan_record_id": record.record_id, "plan_simulation_id": record.execution_id,
            "simulation_id": simulation_id, "execution_run_id": record.execution_id,
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
            "taskset_slot_id": record.taskset_slot_id,
            "taskset_skeleton_id": certificate.taskset_skeleton_id,
            "taskset_id": certificate.taskset_id, "taskset_hash": certificate.taskset_hash,
            "release_projection_id": projection.release_projection_id,
            "release_vector_hash": projection.release_vector_hash,
            "release_mode": material["release_mode"],
            "exact_offsets_json": canonical_json([row.arrival_offset for row in projection.offsets]),
            "release_horizon": window.release_horizon,
            "observation_horizon": window.observation_horizon,
            "scheduler": TARGET_SCHEDULER,
            "battery_model": material["battery_model"],
            "battery_capacity": capacity, "physical_initial_energy": initial,
            "offered_harvest": observed.get("offered_harvest", "0"),
            "required_margin": observed.get("required_margin", "0"),
            "service_harvest_identity": service_identity,
            "trace_contract": SIMULATOR_TRACE_CONTRACT_VERSION,
            "trace_path": trace_target.relative_to(writer.root).as_posix(),
            "trace_sha256": hashlib.sha256(trace_target.read_bytes()).hexdigest(),
            "simulation_status": observed.get("simulation_status", "COMPLETED"),
            "deadline_miss_count": deadline_miss_count,
            "max_observed_response": max_observed_response,
            "task_result_vector_hash": task_result_vector_hash,
            "job_result_vector_hash": job_result_vector_hash,
            "release_audit_id": audit.release_trace_audit_id,
            "no_overflow_evidence_id": no_overflow.evidence_id,
            "validated_simulation_evidence_id": evidence.evidence_id,
            "applicability_track": material["applicability_track"],
        }
        writer.append_unique("formal_simulation_runs.csv", "plan_record_id", sim_row)
        persisted = next(
            row for row in __import__(
                "experiments.v9_3.result_writer", fromlist=["read_csv"]
            ).read_csv(writer.root / "formal_simulation_runs.csv")
            if row["plan_record_id"] == record.record_id
        )
        exact_hash = recompute_simulation_result_hash(persisted)
        writer.write_terminal(record.execution_id, {
            "plan_record_id": record.record_id, "analysis_id": "NA",
            "request_id": record.execution_id, "worker_count": 1,
            "solver_status": "COMPLETED", "exact_result_hash": exact_hash,
        })

    def _persist_simulation_jobs(
        self, writer: Any, simulation_id: str,
        certificate: TasksetIdentityCertificate, projection: ReleaseProjection,
        window: ReleaseObservationWindow, observed: Mapping[str, Any],
    ) -> tuple[int, int, str, str]:
        raw_jobs = observed.get("job_results")
        if not isinstance(raw_jobs, Sequence) or isinstance(raw_jobs, (str, bytes)):
            raise RTA4FormalPipelineError(
                "simulation fixture requires complete ordered raw job_results"
            )
        indexed: Dict[tuple[str, int], Mapping[str, Any]] = {}
        for raw in raw_jobs:
            if not isinstance(raw, Mapping):
                raise RTA4FormalPipelineError("simulation job result must be a mapping")
            task_id = raw.get("task_id")
            release = raw.get("release_time")
            if type(task_id) is not str or type(release) is not int or release < 0:
                raise RTA4FormalPipelineError("simulation job identity is invalid")
            key = (task_id, release)
            if key in indexed:
                raise RTA4FormalPipelineError("duplicate simulation job evidence")
            indexed[key] = raw
        expected_keys = {
            (task.task_id, release)
            for task, offset in zip(certificate.tasks, projection.offsets)
            for release in range(
                offset.arrival_offset, window.release_horizon, task.period
            )
        }
        if set(indexed) != expected_keys:
            raise RTA4FormalPipelineError(
                "simulation job evidence is not the complete pre-cutoff release set"
            )
        total_misses = 0
        global_max = 0
        for task in certificate.tasks:
            task_jobs = []
            releases = sorted(release for task_id, release in expected_keys if task_id == task.task_id)
            for job_index, release in enumerate(releases):
                raw = indexed[(task.task_id, release)]
                completion = raw.get("completion_time")
                if completion is not None and (
                    type(completion) is not int
                    or completion < release
                    or completion > window.observation_horizon
                ):
                    raise RTA4FormalPipelineError(
                        "simulation job completion is outside the observation window"
                    )
                deadline = release + task.relative_deadline
                missed = completion is None or completion > deadline
                if "deadline_missed" in raw and raw["deadline_missed"] is not missed:
                    raise RTA4FormalPipelineError(
                        "simulation job deadline flag contradicts exact timing"
                    )
                response = None if completion is None else completion - release
                total_misses += int(missed)
                if response is not None:
                    global_max = max(global_max, response)
                task_jobs.append((missed, response))
                writer.append_unique(
                    "formal_simulation_job_results.csv",
                    "simulation_job_result_id", {
                        "simulation_job_result_id": domain_hash(
                            "ASAP_BLOCK:V9.3:RTA4_SIMULATION_JOB_RESULT:v1", {
                                "simulation_id": simulation_id,
                                "task_id": task.task_id, "release_time": release,
                            }
                        ),
                        "simulation_id": simulation_id,
                        "taskset_id": certificate.taskset_id,
                        "task_id": task.task_id, "job_index": job_index,
                        "release_time": release,
                        "completion_time": "NA" if completion is None else completion,
                        "absolute_deadline": deadline,
                        "observed_response_time": "NA" if response is None else response,
                        "deadline_missed": missed,
                        "within_release_horizon": True,
                        "observation_status": (
                            "DEADLINE_MISSED" if missed else "COMPLETED"
                        ),
                    },
                )
            task_misses = sum(missed for missed, _ in task_jobs)
            responses = [response for _, response in task_jobs if response is not None]
            writer.append_unique(
                "formal_simulation_task_results.csv",
                "simulation_task_result_id", {
                    "simulation_task_result_id": domain_hash(
                        "ASAP_BLOCK:V9.3:RTA4_SIMULATION_TASK_RESULT:v1", {
                            "simulation_id": simulation_id, "task_id": task.task_id,
                        }
                    ),
                    "simulation_id": simulation_id,
                    "taskset_id": certificate.taskset_id,
                    "task_id": task.task_id, "priority_rank": task.priority_rank,
                    "released_job_count": len(task_jobs),
                    "completed_job_count": len(responses),
                    "deadline_miss_count": task_misses,
                    "max_observed_response": max(responses, default=0),
                    "simulation_status": "COMPLETED",
                },
            )
        from .result_writer import read_csv
        common = {
            "schema_version", "schema_sha256", "plan_sha256",
            "config_semantic_hash",
        }
        task_rows = [
            {key: value for key, value in row.items() if key not in common}
            for row in read_csv(
                writer.root / "formal_simulation_task_results.csv"
            )
            if row["simulation_id"] == simulation_id
        ]
        job_rows = [
            {key: value for key, value in row.items() if key not in common}
            for row in read_csv(
                writer.root / "formal_simulation_job_results.csv"
            )
            if row["simulation_id"] == simulation_id
        ]
        return (
            total_misses,
            global_max,
            domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_SIMULATION_TASK_VECTOR:v1", task_rows
            ),
            domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_SIMULATION_JOB_VECTOR:v1", job_rows
            ),
        )

    def _persist_source_dependencies(self, writer: Any, source_closures: Mapping[str, Any]) -> None:
        if not writer.plan_manifest["source_relations"]:
            return
        from .rta4_formal_validation import refresh_validated_closure
        source = source_closures.get("CORE-1")
        if source is None:
            raise RTA4FormalPipelineError("fixture requires validated CORE-1 source closure")
        closure = refresh_validated_closure(source, require_complete=True)
        source_requests = {row["request_id"]: row for row in closure.table("formal_rta_requests.csv")}
        source_results = {row["request_id"]: row for row in closure.table("formal_rta_taskset_results.csv")}
        local = __import__("experiments.v9_3.result_writer", fromlist=["read_csv"]).read_csv(writer.root / "formal_rta_requests.csv")
        local_by_slot_e0 = {(row["taskset_slot_id"], row["exact_e0"]): row for row in local}
        for plan in writer.plan_manifest["source_relations"]:
            source_request = source_requests.get(plan["source_analysis_id"])
            source_result = source_results.get(plan["source_analysis_id"])
            if source_request is None or source_result is None:
                raise RTA4FormalPipelineError("source closure lacks a trusted source request")
            target = local_by_slot_e0.get((plan["taskset_slot_id"], plan["exact_e0"]), source_request)
            writer.append_unique("formal_dependencies.csv", "plan_relation_id", {
                "plan_relation_id": plan["plan_relation_id"], "analysis_id": target["analysis_id"],
                "source_analysis_id": source_request["analysis_id"], "source_request_id": source_request["request_id"],
                "relation": "CORE2_REUSE" if writer.core == "CORE-2" else "CORE3_APPLICABILITY_SOURCE",
                "source_core": "CORE-1", "target_core": writer.core,
                "taskset_skeleton_id": source_request["taskset_skeleton_id"],
                "taskset_id": source_request["taskset_id"], "taskset_hash": source_request["taskset_hash"],
                "method": plan["method"], "exact_e0": plan["exact_e0"],
                "service_identity": source_request["service_identity"],
                "power_vector_hash": source_request["power_vector_hash"],
                "theory_document_sha256": source_request["theory_document_sha256"],
                "numeric_contract_sha256": source_request["numeric_contract_sha256"],
                "source_exact_input_identity": source_request["exact_input_identity"],
                "target_exact_input_identity": target["exact_input_identity"],
                "source_result_hash": source_result["exact_result_hash"],
                "source_plan_sha256": closure.metadata["plan_sha256"],
                "source_closure_sha256": closure.closure_sha256,
                "dependency_status": "VALIDATED_EXTERNAL_SOURCE", "fallback_used": False,
            })

    def _persist_applicability(
        self, writer: Any, source_closures: Mapping[str, Any],
    ) -> None:
        if not writer.plan_manifest["applicability_rows"]:
            return
        from .result_writer import read_csv
        from .rta4_formal_validation import refresh_validated_closure

        source = source_closures.get("CORE-1")
        if source is None:
            raise RTA4FormalPipelineError(
                "applicability fixture requires a validated CORE-1 closure"
            )
        closure = refresh_validated_closure(source, require_complete=True)
        source_requests = {
            row["request_id"]: row
            for row in closure.table("formal_rta_requests.csv")
        }
        source_results = {
            row["request_id"]: row
            for row in closure.table("formal_rta_taskset_results.csv")
        }
        source_tasks: Dict[str, list[Mapping[str, str]]] = {}
        for row in closure.table("formal_rta_task_results.csv"):
            source_tasks.setdefault(row["analysis_id"], []).append(row)
        simulations = {
            row["plan_simulation_id"]: row
            for row in read_csv(writer.root / "formal_simulation_runs.csv")
        }
        tasksets = {
            row["taskset_id"]: row
            for row in read_csv(writer.root / "formal_tasksets.csv")
        }
        certificates: Dict[str, TasksetIdentityCertificate] = {}
        for taskset_id, row in tasksets.items():
            certificates[taskset_id] = TasksetIdentityCertificate.from_canonical_bytes(
                (writer.root / row["certificate_path"]).read_bytes()
            )
        for plan in writer.plan_manifest["applicability_rows"]:
            simulation = simulations.get(plan["simulation_id"])
            source_request = source_requests.get(plan["source_analysis_id"])
            source_result = source_results.get(plan["source_analysis_id"])
            if simulation is None or source_request is None or source_result is None:
                raise RTA4FormalPipelineError(
                    "applicability fixture lacks its exact source/simulation join"
                )
            if (
                source_request["taskset_id"] != simulation["taskset_id"]
                or source_request["taskset_hash"] != simulation["taskset_hash"]
                or source_request["method"] != plan["method"]
                or source_request["exact_e0"] != plan["exact_e0"]
            ):
                raise RTA4FormalPipelineError(
                    "applicability source mathematical identity mismatch"
                )
            certificate = certificates[simulation["taskset_id"]]
            projection = build_release_projection(
                certificate, release_mode=simulation["release_mode"]
            )
            window = ReleaseObservationWindow.for_certificate(certificate)
            trace_path = writer.root / simulation["trace_path"]
            audit = parse_release_trace(
                trace_path, project_certificate_for_simulation(certificate, projection),
                expected_simulation_id=simulation["simulation_id"],
                expected_taskset_hash=certificate.taskset_hash,
                expected_certificate=certificate, expected_projection=projection,
                window=window,
            )
            no_overflow = build_no_overflow_evidence(
                initial_battery=simulation["physical_initial_energy"],
                battery_capacity=simulation["battery_capacity"],
                offered_harvest=simulation["offered_harvest"],
                required_margin=simulation["required_margin"],
                service_identity=simulation["service_harvest_identity"],
                observation_horizon=window.observation_horizon,
            )
            evidence = validate_simulation_evidence(
                audit, service_identity=simulation["service_harvest_identity"],
                initial_battery=simulation["physical_initial_energy"],
                battery_capacity=simulation["battery_capacity"],
                applicability_track=simulation["applicability_track"],
            )
            evaluation = evaluate_e0_condition(audit, plan["exact_e0"])
            rta_outcome = (
                RTA_PASS if source_result["taskset_proven"] == "true" else RTA_FAIL
            )
            assessment = assess_applicability(
                requested_track=simulation["applicability_track"],
                release_trace_audit=audit, requested_e0=plan["exact_e0"],
                e0_evaluation=evaluation, no_overflow_evidence=no_overflow,
                simulation_evidence=evidence,
                expected_taskset_id=simulation["taskset_id"],
                expected_taskset_hash=simulation["taskset_hash"],
                expected_release_projection_id=simulation["release_projection_id"],
                expected_simulation_id=simulation["simulation_id"],
                rta_outcome=rta_outcome,
                simulation_outcome=audit.simulation_outcome,
            )
            candidates = [
                int(row["candidate_response_time"])
                for row in source_tasks.get(source_request["analysis_id"], ())
                if row["candidate_response_time"] != "NA"
            ]
            comparison_id = formal_comparison_id(
                plan_comparison_id=plan["plan_comparison_id"],
                analysis_id=source_request["analysis_id"],
                simulation_id=simulation["simulation_id"],
                release_audit_id=audit.release_trace_audit_id,
                e0_evaluation_id=evaluation.evaluation_id,
                no_overflow_evidence_id=no_overflow.evidence_id,
                validated_simulation_evidence_id=evidence.evidence_id,
                rta_outcome=rta_outcome,
                simulation_outcome=audit.simulation_outcome,
            )
            writer.append_unique(
                "formal_applicability.csv", "plan_comparison_id", {
                    "plan_comparison_id": plan["plan_comparison_id"],
                    "comparison_id": comparison_id,
                    "analysis_id": source_request["analysis_id"],
                    "simulation_id": simulation["simulation_id"],
                    "taskset_id": simulation["taskset_id"],
                    "method": plan["method"], "exact_e0": plan["exact_e0"],
                    "release_audit_id": audit.release_trace_audit_id,
                    "e0_evaluation_id": evaluation.evaluation_id,
                    "no_overflow_evidence_id": no_overflow.evidence_id,
                    "validated_simulation_evidence_id": evidence.evidence_id,
                    "applicability_track": simulation["applicability_track"],
                    "e0_condition_status": evaluation.status,
                    "theorem_applicability": assessment.category,
                    "theorem_comparison_eligible": assessment.theorem_comparison_eligible,
                    "rta_outcome": rta_outcome,
                    "simulation_outcome": audit.simulation_outcome,
                    "comparison_status": formal_comparison_status(
                        evaluation.status, rta_outcome, audit.simulation_outcome
                    ),
                    "candidate_response_time": max(candidates) if candidates else "NA",
                    "observed_response_time": simulation["max_observed_response"],
                    "soundness_counterexample": (
                        assessment.theorem_applicable_soundness_counterexample
                    ),
                    "empirical_difference": assessment.empirical_difference,
                },
            )

    def _persist_worker_consistency(self, writer: Any) -> None:
        from .result_writer import read_csv
        from .rta4_formal_validation import recompute_worker_consistency_rows
        results = read_csv(writer.root / "formal_rta_taskset_results.csv")
        terminals = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in writer.terminals.glob("*.json")
        }
        for row in recompute_worker_consistency_rows(results, terminals):
            writer.append_unique("formal_worker_consistency.csv", "check_id", row)

    def _persist_hard_checks(self, writer: Any) -> None:
        from .result_writer import read_csv
        from .rta4_formal_validation import (
            recompute_dominance_rows, recompute_monotonicity_rows,
        )

        results = read_csv(writer.root / "formal_rta_taskset_results.csv")
        tasks = read_csv(writer.root / "formal_rta_task_results.csv")
        for row in recompute_dominance_rows(results, tasks):
            writer.append_unique("formal_dominance_checks.csv", "check_id", row)
        for row in recompute_monotonicity_rows(results, tasks):
            writer.append_unique("formal_monotonicity_checks.csv", "check_id", row)


__all__ = [
    "RTA4FixtureInterruption", "RTA4FormalAuthorizationError",
    "RTA4FormalPipelineError",
    "RTA4FormalRunner", "SimulationDeduplicator",
    "build_formal_release_projection", "dispatch_formal_rta",
    "formal_analysis_identity", "formal_comparison_id",
    "formal_comparison_status", "formal_execution_identity",
    "mathematical_result_hash", "mechanism_telemetry_rows",
    "recompute_rta_result_hashes", "recompute_simulation_result_hash",
    "require_rta4_execution_authorization",
]
