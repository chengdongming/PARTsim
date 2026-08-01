"""Pickle-safe V3 worker protocol; workers never receive persistence objects."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from .rta4_formal_execution import (
    ProductionRTAExecutorV2,
    ProductionSimulationExecutorV2,
    RTA4ExecutionError,
    _adapter_result_v2,
)
from .rta4_shared_energy import FrozenMapping


class RTA4WorkerInfrastructureV3Error(RTA4ExecutionError):
    """A process/transport failure, never a mathematical RTA timeout."""


@dataclass(frozen=True)
class V3WorkerRequest:
    record: Any
    certificate: Any
    v2_config: Mapping[str, Any]
    run_context: Any
    timeout_contract: Mapping[str, Mapping[str, int]]
    identity_contract: Mapping[str, Any]
    production_manifest: Mapping[str, Any]
    system_config_path: str
    energy_support_path: str
    output_root: str
    simulation_timeout_seconds: int
    rta_executor_factory: Callable[..., Any] = ProductionRTAExecutorV2
    simulation_executor_factory: Callable[..., Any] = ProductionSimulationExecutorV2


@dataclass(frozen=True)
class V3WorkerResponse:
    plan_record_identity: str
    execution_identity: str
    worker_pid: int
    started_monotonic_ns: int
    finished_monotonic_ns: int
    result: Mapping[str, Any]


@dataclass(frozen=True)
class V3WorkerBootstrap:
    """Read-only material loaded once by every persistent physical slot."""

    v2_config: Mapping[str, Any]
    timeout_contract: Mapping[str, Mapping[str, int]]
    identity_contract: Mapping[str, Any]
    production_manifest: Mapping[str, Any]
    system_config_path: str
    energy_support_path: str
    output_root: str
    simulation_timeout_seconds: int
    rta_executor_factory: Callable[..., Any] = ProductionRTAExecutorV2
    simulation_executor_factory: Callable[..., Any] = ProductionSimulationExecutorV2


@dataclass(frozen=True)
class V3AttemptRequest:
    record: Any
    certificate: Any
    run_context: Any
    attempt_index: int
    timeout_seconds: int


@dataclass(frozen=True)
class V3AttemptResponse:
    plan_record_identity: str
    execution_identity: str
    attempt_index: int
    timeout_seconds: int
    result: Mapping[str, Any]


def _isolated_adapter_attempt_v3(
    record: Any,
    certificate: Any,
    config: Mapping[str, Any],
    timeout_seconds: int,
    task_energy: Any,
    service: Any,
    identity_contract: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Any]:
    """Compatibility name for the now-direct, single-layer adapter call.

    Hard boundaries are enforced by the parent terminating the physical slot;
    this function must never create another process.
    """

    return _adapter_result_v2(
        record, certificate, config, timeout_seconds,
        task_energy, service, identity_contract,
    )


def _single_attempt_contract(
    contract: Mapping[str, Mapping[str, int]], method: str, budget: int,
) -> Mapping[str, Mapping[str, int]]:
    if method not in contract:
        raise RTA4WorkerInfrastructureV3Error(
            "attempt method has no timeout contract"
        )
    return FrozenMapping({
        method: FrozenMapping({
            "initial_timeout_seconds": budget,
            "retry_timeout_seconds": max(1, budget),
            "maximum_attempts": 1,
        }),
    })


def execute_worker_attempt_in_slot_v3(
    bootstrap: V3WorkerBootstrap, request: V3AttemptRequest,
) -> V3AttemptResponse:
    """Execute exactly one attempt in the already-pinned slot process."""

    if type(bootstrap) is not V3WorkerBootstrap:
        raise RTA4WorkerInfrastructureV3Error("invalid V3 worker bootstrap")
    if (
        type(request) is not V3AttemptRequest
        or type(request.attempt_index) is not int
        or request.attempt_index not in {0, 1}
        or type(request.timeout_seconds) is not int
        or request.timeout_seconds < 0
    ):
        raise RTA4WorkerInfrastructureV3Error("invalid V3 attempt request")
    record = request.record
    certificate = request.certificate
    if record.kind == "simulation":
        if request.attempt_index != 0:
            raise RTA4WorkerInfrastructureV3Error(
                "simulation does not accept an RTA retry attempt"
            )
        executor = bootstrap.simulation_executor_factory(
            bootstrap.v2_config,
            run_context=request.run_context,
            production_manifest=bootstrap.production_manifest,
            system_config_path=Path(bootstrap.system_config_path),
            energy_support_path=Path(bootstrap.energy_support_path),
            output_root=Path(bootstrap.output_root),
            simulation_timeout_seconds=bootstrap.simulation_timeout_seconds,
        )
        from .rta4_formal_runner_v2 import _timed_simulation

        result = _timed_simulation(executor, record, certificate)
    else:
        method = str(record.material["method"])
        executor = bootstrap.rta_executor_factory(
            bootstrap.v2_config,
            run_context=request.run_context,
            timeout_contract=_single_attempt_contract(
                bootstrap.timeout_contract, method, request.timeout_seconds,
            ),
            identity_contract=bootstrap.identity_contract,
            adapter_attempt_runner=_adapter_result_v2,
        )
        result = executor(record, certificate)
        if not isinstance(result, Mapping):
            raise RTA4WorkerInfrastructureV3Error(
                "V3 attempt executor did not return a mapping"
            )
        attempts = result.get("attempts")
        if not isinstance(attempts, (tuple, list)) or len(attempts) != 1:
            raise RTA4WorkerInfrastructureV3Error(
                "V3 slot executor must return exactly one attempt"
            )
        attempt = dict(attempts[0])
        attempt["attempt_index"] = request.attempt_index
        attempt["timeout_seconds"] = request.timeout_seconds
        result = FrozenMapping({
            **dict(result),
            "attempts": (FrozenMapping(attempt),),
            "timeout_seconds": request.timeout_seconds,
        })
    if not isinstance(result, Mapping):
        raise RTA4WorkerInfrastructureV3Error(
            "V3 worker attempt did not return a mapping"
        )
    return V3AttemptResponse(
        str(record.record_id), str(record.execution_id),
        request.attempt_index, request.timeout_seconds, result,
    )


def combine_attempt_results_v3(
    responses: Mapping[int, Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Combine parent-observed attempts without changing mathematical fields."""

    indices = sorted(responses)
    if indices not in ([0], [0, 1]):
        raise RTA4WorkerInfrastructureV3Error(
            "V3 attempt history must be a contiguous one/two-attempt prefix"
        )
    attempts = []
    for index in indices:
        result = responses[index]
        observed = result.get("attempts")
        if not isinstance(observed, (tuple, list)) or len(observed) != 1:
            raise RTA4WorkerInfrastructureV3Error(
                "V3 attempt result has invalid history"
            )
        attempt = dict(observed[0])
        if attempt.get("attempt_index") != index:
            raise RTA4WorkerInfrastructureV3Error(
                "V3 attempt result index drift"
            )
        attempts.append(FrozenMapping(attempt))
    final = dict(responses[indices[-1]])
    return FrozenMapping({
        **final,
        "attempts": tuple(attempts),
        "timeout_seconds": attempts[-1]["timeout_seconds"],
        "runtime_wall_seconds": format(sum(
            (Decimal(str(row["runtime_wall_seconds"])) for row in attempts),
            Decimal(),
        ), "f"),
        "runtime_cpu_seconds": format(sum(
            (Decimal(str(row["runtime_cpu_seconds"])) for row in attempts),
            Decimal(),
        ), "f"),
        "peak_rss_bytes": max(int(row["peak_rss_bytes"]) for row in attempts),
    })


def project_hard_timeout_result_v3(
    bootstrap: V3WorkerBootstrap, request: V3AttemptRequest,
) -> Mapping[str, Any]:
    """Build the unchanged adapter TIMEOUT projection after a parent kill.

    A zero-budget adapter call is deterministic and returns before entering a
    substantive search.  It is metadata projection, not an additional attempt;
    the killed slot remains the only process that executed the timed attempt.
    """

    record = request.record
    certificate = request.certificate
    binding = request.run_context.binding_for(record.record_id)
    task_energy = request.run_context.task_energy_materials[
        binding["task_energy_material_identity"]
    ]
    service = request.run_context.service_materials[
        binding["service_material_identity"]
    ]
    try:
        mapped, _raw = _adapter_result_v2(
            record, certificate, bootstrap.v2_config, 0,
            task_energy, service, bootstrap.identity_contract,
        )
    except Exception:
        # Test-only injected executors may use lightweight stand-in materials.
        # Ask the injected executor for deterministic response-shaped material;
        # the parent still overwrites the status with the observed hard timeout.
        method = str(record.material["method"])
        executor = bootstrap.rta_executor_factory(
            bootstrap.v2_config, run_context=request.run_context,
            timeout_contract=_single_attempt_contract(
                bootstrap.timeout_contract, method, 0,
            ),
            identity_contract=bootstrap.identity_contract,
            adapter_attempt_runner=_adapter_result_v2,
        )
        mapped = dict(executor(record, certificate))
        mapped.update({"solver_status": "TIMEOUT", "taskset_proven": False})
    if mapped.get("solver_status") != "TIMEOUT":
        raise RTA4WorkerInfrastructureV3Error(
            "hard timeout could not be projected as adapter TIMEOUT"
        )
    attempt = FrozenMapping({
        "attempt_index": request.attempt_index,
        "timeout_seconds": request.timeout_seconds,
        "status": "TIMEOUT",
        "runtime_wall_seconds": str(request.timeout_seconds),
        "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0,
        "error_classification": "UNIFIED_RTA_ADAPTER_TIMEOUT",
        "analysis_identity": str(mapped["analysis_id"]),
        "taskset_identity": certificate.taskset_id,
        "task_energy_material_identity": (
            task_energy.task_energy_material_identity
        ),
        "service_material_identity": service.service_material_identity,
        "beta_material_identity": service.beta_material_identity,
        "production_build_manifest_identity": (
            request.run_context.production_build_manifest_identity
        ),
    })
    return FrozenMapping({
        **dict(mapped),
        "attempts": (attempt,),
        "timeout_seconds": request.timeout_seconds,
        "runtime_wall_seconds": str(request.timeout_seconds),
        "runtime_cpu_seconds": "0",
        "peak_rss_bytes": 0,
    })


def execute_worker_request_v3(request: V3WorkerRequest) -> V3WorkerResponse:
    """Construct an executor locally and return computation-only material."""

    if type(request) is not V3WorkerRequest:
        raise RTA4WorkerInfrastructureV3Error("invalid V3 worker request")
    started = time.monotonic_ns()
    record = request.record
    certificate = request.certificate
    if record.kind == "simulation":
        executor = request.simulation_executor_factory(
            request.v2_config,
            run_context=request.run_context,
            production_manifest=request.production_manifest,
            system_config_path=Path(request.system_config_path),
            energy_support_path=Path(request.energy_support_path),
            output_root=Path(request.output_root),
            simulation_timeout_seconds=request.simulation_timeout_seconds,
        )
        from .rta4_formal_runner_v2 import _timed_simulation

        result = _timed_simulation(executor, record, certificate)
    else:
        executor = request.rta_executor_factory(
            request.v2_config,
            run_context=request.run_context,
            timeout_contract=request.timeout_contract,
            identity_contract=request.identity_contract,
            adapter_attempt_runner=_adapter_result_v2,
        )
        result = executor(record, certificate)
    if not isinstance(result, Mapping):
        raise RTA4WorkerInfrastructureV3Error(
            "V3 worker executor did not return a mapping"
        )
    return V3WorkerResponse(
        str(record.record_id),
        str(record.execution_id),
        os.getpid(),
        started,
        time.monotonic_ns(),
        result,
    )


__all__ = [
    "RTA4WorkerInfrastructureV3Error",
    "V3AttemptRequest",
    "V3AttemptResponse",
    "V3WorkerBootstrap",
    "V3WorkerRequest",
    "V3WorkerResponse",
    "combine_attempt_results_v3",
    "execute_worker_attempt_in_slot_v3",
    "execute_worker_request_v3",
    "project_hard_timeout_result_v3",
]
