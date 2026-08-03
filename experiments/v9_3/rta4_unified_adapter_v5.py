"""Thin exact-service adapter into the unchanged RTA mathematical kernels."""

from __future__ import annotations

from fractions import Fraction
import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

from experiments.common.exact_service_curve import (
    ExactServiceCurve,
    fraction_text,
    materialize_exact_service_curve,
)

from . import exact_energy
from .constrained_taskset_identity import fixed_slack_deadline
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v5 import RTA4_FORMAL_PROFILE_V5
from .rta4_energy_service_v5 import core3_simulation_projection_v5
from .rta4_formal_execution import _adapter_result_v2
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_shared_energy import (
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    ServiceHorizonContract,
    VerifiedSolarServiceMaterialV2,
)
from .rta4_task_source_v4 import TaskV4, TasksetV4
from .rta4_unified_adapter_v4 import _certificate, _projection, _task_energy


RTA4_V5_ADAPTER_RESULT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:ADAPTER_RESULT:v5"
RTA4_V5_SERVICE_MATERIAL_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:EXACT_SERVICE_ADAPTER_MATERIAL:v5"
)
RTA4_V5_TASK_VARIANT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:TASK_VARIANT:v5"


class RTA4UnifiedAdapterV5Error(ValueError):
    """Raised before exact V5 material can reach the legacy adapter."""


@dataclass(frozen=True)
class PreparedExecutionMaterialV5:
    effective_taskset: TasksetV4
    certificate: Any
    task_energy: Any
    service: VerifiedSolarServiceMaterialV2


def prepare_execution_material_v5(
    *,
    taskset: TasksetV4,
    processors: int,
    task_source_identity: str,
    taskset_store_identity: str,
    production_build_manifest_identity: str,
    service_curve: ExactServiceCurve,
    core: str,
    grid_material: Mapping[str, Any] | None = None,
    service_material_horizon: int | None = None,
    simulation_tick_ms: int | None = None,
) -> PreparedExecutionMaterialV5:
    """Prepare one V5 record for the established V2/V3 worker executors."""

    if type(taskset) is not TasksetV4:
        raise RTA4UnifiedAdapterV5Error(
            "adapter requires a normalized TasksetV4"
        )
    if type(processors) is not int or processors < 1:
        raise RTA4UnifiedAdapterV5Error("processors must be positive")
    if type(service_curve) is not ExactServiceCurve:
        raise RTA4UnifiedAdapterV5Error("service curve was not normalized")
    if core == "CORE-3":
        if type(simulation_tick_ms) is not int or simulation_tick_ms <= 0:
            raise RTA4UnifiedAdapterV5Error(
                "CORE-3 requires a positive simulation_tick_ms"
            )
    elif simulation_tick_ms is not None:
        raise RTA4UnifiedAdapterV5Error(
            "simulation_tick_ms is only valid for CORE-3"
        )
    effective_taskset = (
        taskset
        if grid_material is None
        else materialize_v3_task_variant_v5(
            taskset, core=core, grid_material=grid_material,
        )
    )
    certificate = _certificate(
        effective_taskset,
        processors=processors,
        task_source_identity=task_source_identity,
    )
    task_energy = _task_energy(
        effective_taskset,
        certificate,
        taskset_store_identity=taskset_store_identity,
        production_build_manifest_identity=production_build_manifest_identity,
    )
    service = exact_runtime_service_material_v5(
        effective_taskset,
        service_curve,
        production_build_manifest_identity=production_build_manifest_identity,
        service_material_horizon=service_material_horizon,
        simulation_tick_ms=simulation_tick_ms,
    )
    return PreparedExecutionMaterialV5(
        effective_taskset, certificate, task_energy, service,
    )


def _sha(value: Any, label: str) -> str:
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4UnifiedAdapterV5Error(f"{label} must be a lowercase SHA-256")
    return value


def exact_runtime_service_material_v5(
    taskset: TasksetV4,
    service_curve: ExactServiceCurve,
    *,
    production_build_manifest_identity: str,
    service_material_horizon: int | None = None,
    simulation_tick_ms: int | None = None,
) -> VerifiedSolarServiceMaterialV2:
    """Build exact beta/trace material with no binary64 scientific input."""

    if type(taskset) is not TasksetV4 or type(service_curve) is not ExactServiceCurve:
        raise RTA4UnifiedAdapterV5Error("runtime service inputs are not normalized")
    build = _sha(
        production_build_manifest_identity, "production build manifest identity"
    )
    maximum_deadline = max(task.D for task in taskset.tasks)
    analysis_horizon = maximum_deadline - 1
    if service_material_horizon is None:
        service_material_horizon = maximum_deadline
    if (
        type(service_material_horizon) is not int
        or service_material_horizon < analysis_horizon
    ):
        raise RTA4UnifiedAdapterV5Error(
            "service material horizon does not cover RTA analysis"
        )
    material = materialize_exact_service_curve(
        service_curve, service_material_horizon,
    )
    beta = tuple(material.beta_prefix[:analysis_horizon + 1])
    trace = tuple(material.harvest_trace[:service_material_horizon])
    horizon = ServiceHorizonContract(
        analysis_horizon,
        0,
        service_material_horizon,
        0,
        0,
        HORIZON_CONTRACT_VERSION,
    )
    base = {
        "schema": SERVICE_MATERIAL_SCHEMA,
        "profile": RTA4_FORMAL_PROFILE_V5,
        "production_build_manifest_identity": build,
        "configured_service_identity": service_curve.identity,
        "exact_service_material_identity": material.identity,
        "service_curve": dict(service_curve.normalized_config),
        "beta_prefix": [fraction_text(value) for value in beta],
        "harvest_trace": [fraction_text(value) for value in trace],
        "trace_sha256": material.trace_sha256,
        "horizon": horizon.material(),
        **({
            "core3_simulation_projection": core3_simulation_projection_v5(
                exact_service_material_identity=material.identity,
                harvest_trace=trace,
                simulation_tick_ms=simulation_tick_ms,
            ),
        } if simulation_tick_ms is not None else {}),
    }
    service_identity = domain_hash(RTA4_V5_SERVICE_MATERIAL_DOMAIN, base)
    return VerifiedSolarServiceMaterialV2(
        cache_key=service_identity,
        semantic_service_source_identity=service_curve.identity,
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
        beta_prefix_j=beta,
        trace_sha256=material.trace_sha256,
        beta_material_sha256=material.identity,
        service_material_identity=service_identity,
        immutable_provenance_json=canonical_json(base),
    )


def materialize_v3_task_variant_v5(
    taskset: TasksetV4, *, core: str, grid_material: Mapping[str, Any],
) -> TasksetV4:
    """Apply only the already-declared V3 CORE-4/5A task variants.

    CORE-5A task sources already contain exact C/D/T values for each axis; its
    time axis is therefore never reconstructed or silently rescaled here.
    """

    if type(taskset) is not TasksetV4 or not isinstance(grid_material, Mapping):
        raise RTA4UnifiedAdapterV5Error("task variant inputs are invalid")
    power_scale = Fraction(str(grid_material.get("power_scale", "1")))
    if power_scale <= 0:
        raise RTA4UnifiedAdapterV5Error("power_scale must be positive")
    deadline_variant = str(grid_material.get("deadline_variant", ""))
    if core not in {"CORE-4", "CORE-5A"}:
        if power_scale != 1 or deadline_variant:
            raise RTA4UnifiedAdapterV5Error(
                f"{core} unexpectedly requested a task variant"
            )
        return taskset
    slack: Fraction | None = None
    if core == "CORE-4":
        prefix = "fixed_slack_fraction_v1:"
        if not deadline_variant.startswith(prefix):
            raise RTA4UnifiedAdapterV5Error("CORE-4 deadline variant is not fixed")
        slack = Fraction(deadline_variant[len(prefix):])
    tasks = []
    for task in taskset.tasks:
        deadline = (
            task.D
            if slack is None
            else fixed_slack_deadline(task.C, task.T, slack)
        )
        tasks.append(TaskV4(
            task.name,
            task.C,
            deadline,
            task.T,
            fraction_text(Fraction(task.power) * power_scale),
        ))
    material = {
        "base_taskset_identity": taskset.identity,
        "core": core,
        "power_scale": fraction_text(power_scale),
        "deadline_variant": deadline_variant,
        "tasks": [task.material() for task in tasks],
    }
    content = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    identity = domain_hash(RTA4_V5_TASK_VARIANT_DOMAIN, material)
    return TasksetV4(
        taskset.taskset_id,
        taskset.source_seed,
        tuple(tasks),
        taskset.task_order,
        taskset.task_order_sha256,
        content,
        identity,
    )


def execute_normalized_taskset_v5(
    *,
    taskset: TasksetV4,
    processors: int,
    task_source_identity: str,
    taskset_store_identity: str,
    production_build_manifest_identity: str,
    service_curve: ExactServiceCurve,
    e0: str,
    method: str,
    timeout_seconds: int,
    core: str = "CORE-1",
    grid_material: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one non-formal replay through the unchanged V2 math adapter."""

    try:
        exact_e0 = Fraction(e0)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise RTA4UnifiedAdapterV5Error("E0 must be exact rational text") from exc
    if type(e0) is not str or exact_e0 < 0 or e0 != fraction_text(exact_e0):
        raise RTA4UnifiedAdapterV5Error("E0 must be canonical exact rational text")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise RTA4UnifiedAdapterV5Error("timeout must be positive")
    prepared = prepare_execution_material_v5(
        taskset=taskset,
        processors=processors,
        task_source_identity=task_source_identity,
        taskset_store_identity=taskset_store_identity,
        production_build_manifest_identity=production_build_manifest_identity,
        service_curve=service_curve,
        core=core,
        grid_material=grid_material,
        service_material_horizon=None,
        simulation_tick_ms=None,
    )
    effective_taskset = prepared.effective_taskset
    certificate = prepared.certificate
    task_energy = prepared.task_energy
    service = prepared.service
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
        "experiment_contract": {"profile": RTA4_FORMAL_PROFILE_V5},
        "execution": {"timeout_contract": timeout},
    }
    record = SimpleNamespace(material={"method": method, "exact_e0": e0})
    _mapped, raw = _adapter_result_v2(
        record,
        certificate,
        config,
        timeout_seconds,
        task_energy,
        service,
        {
            "formal_profile": RTA4_FORMAL_PROFILE_V5,
            "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:FORMAL_ANALYSIS:v5",
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "timeout_contract": timeout,
        },
    )
    result = _projection(raw)
    material = {
        "source_taskset_identity": taskset.identity,
        "effective_taskset_identity": effective_taskset.identity,
        "task_source_identity": task_source_identity,
        "taskset_store_identity": taskset_store_identity,
        "service_curve_identity": service_curve.identity,
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
            "ASAP_BLOCK:V9.3:RTA4:KERNEL_RESULT:v5",
            {"E0": e0, "method": method, "result": result},
        ),
        "mathematical_result_hash": domain_hash(
            RTA4_V5_ADAPTER_RESULT_DOMAIN, material,
        ),
    }


__all__ = [
    "PreparedExecutionMaterialV5",
    "RTA4UnifiedAdapterV5Error",
    "exact_runtime_service_material_v5",
    "execute_normalized_taskset_v5",
    "materialize_v3_task_variant_v5",
    "prepare_execution_material_v5",
]
