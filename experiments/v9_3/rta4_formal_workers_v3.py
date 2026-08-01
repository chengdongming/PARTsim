"""Pickle-safe V3 worker protocol; workers never receive persistence objects."""

from __future__ import annotations

from dataclasses import dataclass
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
from .rta4_process_isolation_v3 import (
    ISOLATED_RESULT,
    ISOLATED_TIMEOUT,
    execute_isolated_call_v3,
)


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


def _isolated_adapter_attempt_v3(
    record: Any,
    certificate: Any,
    config: Mapping[str, Any],
    timeout_seconds: int,
    task_energy: Any,
    service: Any,
    identity_contract: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Any]:
    """Run one adapter attempt in a killable spawn child.

    A hard wall timeout is projected through the unchanged adapter with a zero
    budget.  This preserves its canonical TIMEOUT response shape and identity;
    transport, serialization, and crash failures remain INTERNAL_ERROR paths.
    """

    arguments = (
        record,
        certificate,
        config,
        timeout_seconds,
        task_energy,
        service,
        identity_contract,
    )
    isolated = execute_isolated_call_v3(
        _adapter_result_v2,
        arguments,
        timeout_seconds,
        start_method="spawn",
    )
    if isolated.status == ISOLATED_RESULT:
        value = isolated.value
        if not isinstance(value, tuple) or len(value) != 2:
            raise RTA4WorkerInfrastructureV3Error(
                "INVALID_ISOLATED_ADAPTER_PAYLOAD"
            )
        return value
    if isolated.status == ISOLATED_TIMEOUT:
        mapped, raw = _adapter_result_v2(
            record,
            certificate,
            config,
            0,
            task_energy,
            service,
            identity_contract,
        )
        if mapped.get("solver_status") != "TIMEOUT":
            raise RTA4WorkerInfrastructureV3Error(
                "hard timeout could not be projected as adapter TIMEOUT"
            )
        return mapped, raw
    raise RTA4WorkerInfrastructureV3Error(
        isolated.error_classification
    )


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
            adapter_attempt_runner=_isolated_adapter_attempt_v3,
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
    "V3WorkerRequest",
    "V3WorkerResponse",
    "execute_worker_request_v3",
]
