"""Family-independent exact taskset adapter for RTA4 V4.

The adapter accepts only normalized tasksets and an explicit energy service.
It never reads ``family_id`` and dispatches through the current formal V3
adapter/method registry into the unchanged mathematical kernels.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
import hashlib
import multiprocessing
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from . import exact_energy
from .rta4_energy_service_v4 import (
    EXACT_LINEAR_SERVICE_V1,
    VERIFIED_SHARED_ENERGY_MATERIAL_V1,
    EnergyServiceV4,
    exact_service_material_v4,
    validate_bound_shared_material_v4,
)
from .rta4_formal_config import canonical_json, domain_hash, fraction_text
from .rta4_formal_config_v4 import RTA4_FORMAL_PROFILE_V4
from .rta4_formal_execution import _adapter_result_v2
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_shared_energy import (
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    TASK_ENERGY_ENTRY_DOMAIN,
    TASK_ENERGY_MATERIAL_DOMAIN,
    TASK_ENERGY_MATERIAL_SCHEMA,
    ServiceHorizonContract,
    TaskEnergyEntry,
    TaskEnergyMaterial,
    VerifiedSolarServiceMaterialV2,
)
from .rta4_taskset_v2 import (
    RTA4_FORMAL_PROFILE_V2,
    RTA4_TASKSET_CERTIFICATE_DOMAIN_V2,
    RTA4_TASKSET_CERTIFICATE_SCHEMA_V2,
    RTA4_TASKSET_SOURCE_DOMAIN_V2,
    FormalTaskV2,
    TasksetIdentityCertificateV2,
)
from .rta4_task_source_v4 import TasksetV4


RTA4_V4_ADAPTER_RESULT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:ADAPTER_RESULT:v4"
RTA4_V4_TASK_ENERGY_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:EXACT_TASK_ENERGY:v4"
RTA4_V4_SERVICE_MATERIAL_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:EXACT_SERVICE_ADAPTER_MATERIAL:v4"
)


class RTA4UnifiedAdapterV4Error(ValueError):
    """Raised when normalized V4 inputs drift before mathematical dispatch."""


def _sha(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4UnifiedAdapterV4Error(f"{label} must be a lowercase SHA-256")
    return value


def _certificate(
    taskset: TasksetV4, *, processors: int, task_source_identity: str,
) -> TasksetIdentityCertificateV2:
    source_identity = _sha(task_source_identity, "task source identity")
    tasks = tuple(
        FormalTaskV2(
            task.name, index, task.C, task.D, task.T,
            "RTA4_V4_EXACT_RATIONAL_TASK",
        )
        for index, task in enumerate(taskset.tasks)
    )
    seed = 0 if taskset.source_seed is None else taskset.source_seed
    request_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:V4:GENERATION_REQUEST:v1", {
        "task_source_identity": source_identity,
        "taskset_identity": taskset.identity,
        "taskset_content_sha256": taskset.content_sha256,
    })
    skeleton_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:V4:SKELETON:v1", {
        "generation_request_id": request_id,
        "task_order_sha256": taskset.task_order_sha256,
        "processor_count": processors,
    })
    deadline_variant = "EXPLICIT_CONSTRAINED_DEADLINE_V4"
    taskset_hash = domain_hash(RTA4_TASKSET_SOURCE_DOMAIN_V2, {
        "processor_count": processors,
        "deadline_variant": deadline_variant,
        "tasks": [task.material() for task in tasks],
    })
    base = {
        "schema": RTA4_TASKSET_CERTIFICATE_SCHEMA_V2,
        "profile": RTA4_FORMAL_PROFILE_V2,
        "processor_count": processors,
        "formal_master_seed": seed,
        "generator_seed": seed,
        "generator_contract_version": "ASAP_BLOCK_RTA4_V4_NORMALIZED_TASKSET_V1",
        "generation_request_id": request_id,
        "taskset_skeleton_id": skeleton_id,
        "taskset_source_sha256": taskset.content_sha256,
        "deadline_variant": deadline_variant,
        "energy_coefficient": "1",
        "tasks": [task.material() for task in tasks],
        "taskset_hash": taskset_hash,
    }
    certificate_id = domain_hash(RTA4_TASKSET_CERTIFICATE_DOMAIN_V2, base)
    certificate = TasksetIdentityCertificateV2(
        processors, seed, seed, "ASAP_BLOCK_RTA4_V4_NORMALIZED_TASKSET_V1",
        request_id, skeleton_id, taskset.content_sha256, deadline_variant,
        Fraction(1), tasks, taskset_hash, certificate_id,
    )
    certificate.validate()
    return certificate


def _task_energy(
    taskset: TasksetV4, certificate: TasksetIdentityCertificateV2, *,
    taskset_store_identity: str, production_build_manifest_identity: str,
) -> TaskEnergyMaterial:
    store = _sha(taskset_store_identity, "taskset store identity")
    build = _sha(
        production_build_manifest_identity,
        "production build manifest identity",
    )
    entries = []
    for index, (task, formal_task) in enumerate(
        zip(taskset.tasks, certificate.tasks)
    ):
        power = Fraction(task.power)
        base = {
            "schema": TASK_ENERGY_MATERIAL_SCHEMA,
            "profile_id": RTA4_FORMAL_PROFILE_V4,
            "production_build_manifest_identity": build,
            "taskset_id": certificate.taskset_id,
            "task_index": index,
            "task_id": task.name,
            "C": task.C,
            "D": task.D,
            "T": task.T,
            "energy_j_per_tick": fraction_text(power),
            "numeric_source": "EXACT_RATIONAL_TASK_SOURCE_V4",
            "unit": "J/tick",
        }
        entries.append(TaskEnergyEntry(
            index, task.name, task.T, task.D, task.C, formal_task.workload,
            "NOT_APPLICABLE_EXACT_RATIONAL_V4",
            "NOT_APPLICABLE_EXACT_RATIONAL_V4",
            "NOT_APPLICABLE_EXACT_RATIONAL_V4",
            "NOT_APPLICABLE_EXACT_RATIONAL_V4",
            power,
            "NOT_APPLICABLE_EXACT_RATIONAL_V4",
            domain_hash(TASK_ENERGY_ENTRY_DOMAIN, base),
        ))
    certificate_sha = hashlib.sha256(certificate.canonical_bytes()).hexdigest()
    material = {
        "schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "profile_id": RTA4_FORMAL_PROFILE_V4,
        "production_build_manifest_identity": build,
        "taskset_id": certificate.taskset_id,
        "taskset_store_identity": store,
        "taskset_canonical_sha256": certificate_sha,
        "source_taskset_identity": taskset.identity,
        "source_taskset_content_sha256": taskset.content_sha256,
        "numeric_source": "EXACT_RATIONAL_TASK_SOURCE_V4",
        "entries": [entry.material() for entry in entries],
    }
    identity = domain_hash(RTA4_V4_TASK_ENERGY_DOMAIN, material)
    return TaskEnergyMaterial(
        RTA4_FORMAL_PROFILE_V4, build, certificate.taskset_id, store,
        certificate_sha, "0" * 64, "0" * 64,
        "ASAP_BLOCK_RTA4_V4_NORMALIZED_TASKSET_V1", tuple(entries), identity,
    )


def _service(
    taskset: TasksetV4, energy_service: EnergyServiceV4, *,
    production_build_manifest_identity: str,
    verified_shared_service: VerifiedSolarServiceMaterialV2 | None,
) -> VerifiedSolarServiceMaterialV2:
    build = _sha(
        production_build_manifest_identity,
        "production build manifest identity",
    )
    if energy_service.model == VERIFIED_SHARED_ENERGY_MATERIAL_V1:
        if type(verified_shared_service) is not VerifiedSolarServiceMaterialV2:
            raise RTA4UnifiedAdapterV4Error(
                "verified shared energy material must be explicitly supplied"
            )
        validate_bound_shared_material_v4(
            energy_service,
            service_material_identity=(
                verified_shared_service.service_material_identity
            ),
            beta_material_identity=(
                verified_shared_service.beta_material_identity
            ),
            production_build_manifest_identity=(
                verified_shared_service.production_build_manifest_identity
            ),
        )
        if verified_shared_service.production_build_manifest_identity != build:
            raise RTA4UnifiedAdapterV4Error(
                "shared energy material build identity differs from execution"
            )
        return verified_shared_service
    if energy_service.model != EXACT_LINEAR_SERVICE_V1:
        raise RTA4UnifiedAdapterV4Error(
            "unknown V4 energy service model"
        )
    if verified_shared_service is not None:
        raise RTA4UnifiedAdapterV4Error(
            "exact service does not accept external shared material"
        )
    maximum_deadline = max(task.D for task in taskset.tasks)
    exact = exact_service_material_v4(energy_service, maximum_deadline - 1)
    rate = Fraction(exact.rate)
    trace = tuple(rate for _ in range(maximum_deadline))
    horizon = ServiceHorizonContract(
        maximum_deadline - 1, 0, maximum_deadline, 0, 0,
        HORIZON_CONTRACT_VERSION,
    )
    base = {
        "schema": SERVICE_MATERIAL_SCHEMA,
        "profile": RTA4_FORMAL_PROFILE_V4,
        "production_build_manifest_identity": build,
        "configured_service_identity": energy_service.identity,
        "exact_service_material_identity": exact.material_identity,
        "service_model": EXACT_LINEAR_SERVICE_V1,
        "rate": exact.rate,
        "beta_prefix": [fraction_text(value) for value in exact.beta_prefix],
        "horizon": horizon.material(),
    }
    service_identity = domain_hash(RTA4_V4_SERVICE_MATERIAL_DOMAIN, base)
    return VerifiedSolarServiceMaterialV2(
        cache_key=service_identity,
        semantic_service_source_identity=energy_service.identity,
        parser_environment_identity="0" * 64,
        live_proof_identity="0" * 64,
        production_build_manifest_identity=build,
        system_sha256="0" * 64,
        support_sha256="0" * 64,
        solar_csv_sha256="0" * 64,
        day_of_year=0,
        time_of_day_ms=0,
        solar_scale=Fraction(1),
        horizon=horizon,
        harvest_j_per_tick=trace,
        beta_prefix_j=exact.beta_prefix,
        trace_sha256=domain_hash(
            "ASAP_BLOCK:V9.3:RTA4:V4:EXACT_TRACE:v1",
            [fraction_text(value) for value in trace],
        ),
        beta_material_sha256=exact.material_identity,
        service_material_identity=service_identity,
        immutable_provenance_json=canonical_json(base),
    )


def _projection(result: Any) -> dict[str, Any]:
    tasks = []
    for row in result.task_results:
        tasks.append({
            "task_id": row.task_id,
            "priority_rank": row.priority_rank,
            "solver_status": row.solver_status.value,
            "kernel_solver_status": row.kernel_solver_status,
            "certification_status": row.certification_status.value,
            "candidate_response_time": row.candidate_response_time,
            "closing_w": row.closing_w,
            "carry_in_values_used": [
                list(value) for value in row.carry_in_values_used
            ],
            "witness_h": row.witness_h,
            "processor_progress_a": row.processor_progress_a,
            "maximum_blocking_h": row.maximum_blocking_h,
            "witness_sequence": list(row.witness_sequence),
            "checked_w_count": row.checked_w_count,
            "checked_h_count": row.checked_h_count,
            "checked_q_count": row.checked_q_count,
            "envelope_call_count": row.envelope_call_count,
            "solver_call_count": row.solver_call_count,
            "failure_reason": row.failure_reason,
        })
    return {
        "method_id": result.method_id.value,
        "kernel": result.kernel.value,
        "carry_policy": result.carry_policy.value,
        "solver_status": result.solver_status.value,
        "certification_status": result.analysis_certification_status.value,
        "taskset_proven": bool(result.taskset_proven),
        "first_failed_task": result.first_failed_task,
        "failure_reason": result.failure_reason,
        "exact_input_identity": result.exact_input_identity,
        "response_vector": [
            row["candidate_response_time"] for row in tasks
            if row["candidate_response_time"] is not None
        ],
        "carry_trace": [
            {
                "task_id": entry.task_id,
                "priority_rank": entry.priority_rank,
                "theta_by_task": [list(value) for value in entry.theta_by_task],
            }
            for entry in result.carry_trace
        ],
        "task_results": tasks,
    }


def execute_normalized_taskset_v4(
    *, taskset: TasksetV4, processors: int,
    task_source_identity: str, taskset_store_identity: str,
    production_build_manifest_identity: str, energy_service: EnergyServiceV4,
    e0: str, method: str, timeout_seconds: int,
    verified_shared_service: VerifiedSolarServiceMaterialV2 | None = None,
) -> dict[str, Any]:
    if type(taskset) is not TasksetV4:
        raise RTA4UnifiedAdapterV4Error("adapter requires a normalized TasksetV4")
    if type(processors) is not int or processors < 1:
        raise RTA4UnifiedAdapterV4Error("processors must be positive")
    if type(e0) is not str or Fraction(e0) < 0 or e0 != fraction_text(Fraction(e0)):
        raise RTA4UnifiedAdapterV4Error("E0 must be canonical exact rational text")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise RTA4UnifiedAdapterV4Error("timeout must be positive")
    certificate = _certificate(
        taskset, processors=processors,
        task_source_identity=task_source_identity,
    )
    task_energy = _task_energy(
        taskset, certificate,
        taskset_store_identity=taskset_store_identity,
        production_build_manifest_identity=production_build_manifest_identity,
    )
    service = _service(
        taskset, energy_service,
        production_build_manifest_identity=production_build_manifest_identity,
        verified_shared_service=verified_shared_service,
    )
    timeout = {
        method: {
            "initial_timeout_seconds": timeout_seconds,
            "retry_timeout_seconds": timeout_seconds * 2,
            "maximum_attempts": 2,
        },
    }
    config = {
        "identity": {
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        },
        "experiment_contract": {"profile": RTA4_FORMAL_PROFILE_V4},
        "execution": {"timeout_contract": timeout},
    }
    record = SimpleNamespace(material={"method": method, "exact_e0": e0})
    _mapped, raw = _adapter_result_v2(
        record, certificate, config, timeout_seconds, task_energy, service,
        {
            "formal_profile": RTA4_FORMAL_PROFILE_V4,
            "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:FORMAL_ANALYSIS:v4",
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "timeout_contract": timeout,
        },
    )
    result = _projection(raw)
    material = {
        "taskset_identity": taskset.identity,
        "task_source_identity": task_source_identity,
        "taskset_store_identity": taskset_store_identity,
        "energy_service_identity": energy_service.identity,
        "service_material_identity": service.service_material_identity,
        "beta_material_identity": service.beta_material_identity,
        "task_energy_material_identity": task_energy.task_energy_material_identity,
        "E0": e0,
        "method": method,
        "result": result,
    }
    return {
        **material,
        "kernel_result_hash": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4:KERNEL_RESULT:v4", {
                "E0": e0,
                "method": method,
                "result": result,
            },
        ),
        "mathematical_result_hash": domain_hash(
            RTA4_V4_ADAPTER_RESULT_DOMAIN, material,
        ),
    }


def _execute_mapping(request: Mapping[str, Any]) -> dict[str, Any]:
    return execute_normalized_taskset_v4(**dict(request))


def execute_replay_requests_v4(
    requests: Sequence[Mapping[str, Any]], *, worker_count: int,
) -> tuple[dict[str, Any], ...]:
    """Non-formal deterministic replay used only for worker-count tests."""

    if type(worker_count) is not int or worker_count < 1:
        raise RTA4UnifiedAdapterV4Error("worker_count must be positive")
    material = list(requests)
    if worker_count == 1:
        return tuple(_execute_mapping(request) for request in material)
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=worker_count, mp_context=context,
    ) as pool:
        return tuple(pool.map(_execute_mapping, material))


__all__ = [
    "RTA4UnifiedAdapterV4Error", "execute_normalized_taskset_v4",
    "execute_replay_requests_v4",
]
