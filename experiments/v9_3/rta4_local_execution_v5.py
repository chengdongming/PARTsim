"""Explicit local, non-paper execution for selectable-service RTA V5.

This module is deliberately outside the frozen V3/V4 profiles.  It adapts V5
plan records to the established V2 executor and V3 worker request protocol,
and reuses the V3 checkpoint throttle.  It does not create a formal
authorization and never labels its outputs as paper evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

import yaml

from experiments.common.exact_service_curve import normalize_exact_service_curve

from . import exact_energy
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v2 import default_rta4_formal_config_v2
from .rta4_formal_config_v5 import (
    LoadedCampaignV5,
    RTA4_FORMAL_PROFILE_V5,
    formal_taskset_store_identity_v5,
    load_rta4_campaign_v5,
    source_closure_identity_v5,
)
from .rta4_energy_service_v5 import core3_simulation_projection_v5
from .rta4_formal_execution import (
    ProductionRTAExecutorV2,
    build_formal_release_projection_v2,
)
from .rta4_formal_plan_v5 import (
    FormalPlanRecordV5,
    describe_formal_plan_v5,
    iter_formal_plan_v5,
)
from .rta4_formal_rows import ENUMS as RTA4_FORMAL_ENUMS
from .rta4_formal_runner_v3 import _CheckpointThrottleV3
from .rta4_formal_workers_v3 import (
    V3WorkerBootstrap,
    V3WorkerRequest,
    V3WorkerResponse,
    execute_worker_request_v3,
)
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_shared_energy import (
    FrozenMapping,
    SharedEnergyRunContext,
    project_core3_shared_energy_payload,
)
from .rta4_physical_core_slots_v3 import (
    PHYSICAL_CORE_EXECUTION_BACKEND_V3,
    PhysicalCoreSlotPoolV3,
    PhysicalCoreSlotV3Error,
    discover_cpu_topology_v3,
)
from .rta4_physical_execution_v5 import (
    PreparedPhysicalRecordV5,
    RTA4PhysicalExecutionV5Error,
    execute_physical_group_v5,
)
from .rta4_unified_adapter_v5 import prepare_execution_material_v5
from .simulation_result import SimulationStatus


RTA4_LOCAL_RUN_MANIFEST_V5 = "local_run_manifest_v5.json"
RTA4_LOCAL_CHECKPOINT_V5 = "local_checkpoint_v5.json"
RTA4_LOCAL_TERMINALS_V5 = "local_terminal_results_v5"
RTA4_LOCAL_RUN_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RUN:v5"
RTA4_LOCAL_RESULT_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RESULT:v5"
RTA4_LOCAL_CHECKPOINT_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_CHECKPOINT:v5"
RTA4_LOCAL_BUILD_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_MATERIAL:v5"
RTA4_TEST_EXECUTION_BACKEND_V5 = "TEST_ONLY_EXPLICIT_DISPATCHERS"
_VALID_SOLVER_STATUSES_V5 = frozenset(RTA4_FORMAL_ENUMS["solver_status"])
_VALID_SIMULATION_OBSERVED_STATUSES_V5 = frozenset({
    SimulationStatus.PASS_OBSERVED.value,
    SimulationStatus.DEADLINE_MISS.value,
})


class RTA4LocalExecutionV5Error(RuntimeError):
    """Raised when a local V5 run cannot remain fail-closed."""


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class LocalWorkerRecordV5:
    kind: str
    core: str
    ordinal: int
    mathematical_request_id: str
    execution_id: str
    record_id: str
    material: Mapping[str, Any]


def _local_material_identity(campaign: LoadedCampaignV5) -> str:
    return domain_hash(RTA4_LOCAL_BUILD_DOMAIN_V5, {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "source_closure_identity": source_closure_identity_v5(
            campaign.normalized_scientific_config
        ),
        "classification": "LOCAL_NOT_FOR_PAPER",
    })


def _worker_record(record: FormalPlanRecordV5) -> LocalWorkerRecordV5:
    grid = dict(record.material["v3_grid_material"])
    return LocalWorkerRecordV5(
        record.kind,
        record.core,
        record.ordinal,
        record.mathematical_request_id,
        record.execution_id,
        record.record_id,
        FrozenMapping(grid),
    )


def _binding_for_record(
    campaign: LoadedCampaignV5, record: FormalPlanRecordV5,
) -> tuple[Any, Any]:
    selector = record.material["task_source_selector"]
    matches = [
        binding for binding in campaign.task_sources
        if binding.axis == selector["axis"]
        and binding.axis_value == selector["axis_value"]
    ]
    if len(matches) != 1:
        raise RTA4LocalExecutionV5Error(
            "V5 execution record has no unique task source"
        )
    binding = matches[0]
    index = int(record.material["taskset_source_index"])
    try:
        taskset = binding.source.taskset(index)
    except Exception as exc:
        raise RTA4LocalExecutionV5Error(
            "V5 execution task source index drift"
        ) from exc
    if taskset.identity != record.taskset_identity:
        raise RTA4LocalExecutionV5Error(
            "V5 execution taskset identity drift"
        )
    return binding, taskset


def _prepared_record_material(
    campaign: LoadedCampaignV5,
    record: FormalPlanRecordV5,
) -> tuple[LocalWorkerRecordV5, Any, SharedEnergyRunContext, str]:
    binding, taskset = _binding_for_record(campaign, record)
    effective_config = record.material["effective_service_curve"]
    curve_input = {
        key: effective_config[key]
        for key in ("model", "rate", "latency", "time_unit")
        if key in effective_config
    }
    curve = normalize_exact_service_curve(curve_input)
    if curve.identity != record.effective_service_identity:
        raise RTA4LocalExecutionV5Error(
            "effective service identity drift before execution"
        )
    scientific = campaign.normalized_scientific_config
    local_material_identity = _local_material_identity(campaign)
    horizon = None
    service_material = record.material.get("service_material")
    if isinstance(service_material, Mapping):
        horizon = int(service_material["maximum_length"])
    simulation_tick_ms = (
        scientific["simulation_tick_ms"] if record.core == "CORE-3" else None
    )
    prepared = prepare_execution_material_v5(
        taskset=taskset,
        processors=binding.source.processors,
        task_source_identity=binding.source.identity,
        taskset_store_identity=formal_taskset_store_identity_v5(scientific),
        production_build_manifest_identity=local_material_identity,
        service_curve=curve,
        core=record.core,
        grid_material=record.material["v3_grid_material"],
        service_material_horizon=horizon,
        simulation_tick_ms=simulation_tick_ms,
    )
    worker_record = _worker_record(record)
    task_identity = prepared.task_energy.task_energy_material_identity
    service_identity = prepared.service.service_material_identity
    binding_material: dict[str, Any] = {
        "task_energy_material_identity": task_identity,
        "service_material_identity": service_identity,
    }
    if record.kind == "simulation":
        if not isinstance(service_material, Mapping):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 record has no exact service projection"
            )
        planned_projection = service_material["simulation_projection"]
        provenance = json.loads(prepared.service.immutable_provenance_json)
        prepared_projection = provenance.get("core3_simulation_projection")
        if prepared_projection != planned_projection:
            raise RTA4LocalExecutionV5Error(
                "CORE-3 runtime service projection identity drift"
            )
        binding_material.update({
            "simulation_tick_ms": simulation_tick_ms,
            "simulation_projection_identity": planned_projection[
                "simulation_projection_identity"
            ],
        })
    context = SharedEnergyRunContext(
        local_material_identity,
        FrozenMapping({task_identity: prepared.task_energy}),
        FrozenMapping({service_identity: prepared.service}),
        FrozenMapping({worker_record.record_id: FrozenMapping(binding_material)}),
        FrozenMapping({}),
        True,
    )
    return worker_record, prepared.certificate, context, local_material_identity


def _exact_piecewise_system_v5(
    base_system_path: Path,
    destination: Path,
    *,
    processors: int,
    scheduler: str,
    initial_energy: Fraction,
    max_energy: Fraction,
    simulation_projection: Mapping[str, Any],
) -> Path:
    try:
        document = yaml.safe_load(base_system_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 base simulator system is unreadable"
        ) from exc
    document.pop("priority_energy", None)
    energy = document.get("energy_management")
    if not isinstance(energy, dict):
        raise RTA4LocalExecutionV5Error(
            "CORE-3 base simulator system has no energy management"
        )
    for field in (
        "day_of_year", "time_of_day_ms", "base_harvesting_rate",
        "harvesting_scale", "use_real_solar_data", "solar_data_file",
        "pv_efficiency", "pv_area_m2", "start_offset_minutes",
        "start_offset_ms",
    ):
        energy.pop(field, None)
    energy["initial_energy"] = float(initial_energy)
    energy["max_energy"] = float(max_energy)
    document["cpu_islands"][0]["numcpus"] = processors
    document["cpu_islands"][0]["kernel"]["scheduler"] = scheduler
    segments = simulation_projection.get("segments")
    if not isinstance(segments, list):
        raise RTA4LocalExecutionV5Error(
            "CORE-3 simulation projection has no exact segments"
        )
    runs: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 simulation projection segment is invalid"
            )
        runs.append({
            "start_ms": int(segment["start_ms"]),
            "end_ms": int(segment["end_ms"]),
            "multiplier": float(Fraction(str(segment["power_w"]))),
        })
    document["harvesting"] = {
        "source": "scaled_piecewise",
        "scaled_piecewise": {"scale_w": 1.0, "segments": runs},
    }
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "system_config_v5.yaml"
    atomic_write_json(
        destination / "service_projection_v5.json", simulation_projection,
    )
    atomic_write_text(path, yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True,
    ))
    return path


class ExactServiceSimulationExecutorV5:
    """CORE-3 adapter around the existing simulator binary and trace parser."""

    def __init__(
        self,
        _config: Mapping[str, Any],
        *,
        run_context: SharedEnergyRunContext,
        production_manifest: Mapping[str, Any],
        system_config_path: Path | str,
        energy_support_path: Path | str,
        output_root: Path | str,
        simulation_timeout_seconds: int,
    ) -> None:
        del energy_support_path
        self.context = run_context
        self.simulator = Path(str(production_manifest["simulator_path"]))
        self.system = Path(system_config_path)
        self.output = Path(output_root)
        self.timeout = simulation_timeout_seconds

    def __call__(self, record: Any, certificate: Any) -> Mapping[str, Any]:
        from .simulation_engine import _render_taskset_yaml
        from .simulation_result import SimulationStatus, parse_simulation_trace

        binding = self.context.binding_for(record.record_id)
        task_energy = self.context.task_energy_materials[
            binding["task_energy_material_identity"]
        ]
        service = self.context.service_materials[
            binding["service_material_identity"]
        ]
        simulation_tick_ms = binding.get("simulation_tick_ms")
        projection = core3_simulation_projection_v5(
            exact_service_material_identity=service.beta_material_identity,
            harvest_trace=service.harvest_j_per_tick,
            simulation_tick_ms=simulation_tick_ms,
        )
        if projection["simulation_projection_identity"] != binding.get(
            "simulation_projection_identity"
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 execution projection identity drift"
            )
        release, window, base_payload = build_formal_release_projection_v2(
            certificate, str(record.material["release_mode"]),
        )
        payload = project_core3_shared_energy_payload(
            certificate, base_payload, task_energy,
        )
        run_root = self.output / "bounded_core3_simulations_v5" / record.execution_id
        system_path = _exact_piecewise_system_v5(
            self.system,
            run_root,
            processors=certificate.processors,
            scheduler=str(record.material["scheduler"]),
            initial_energy=Fraction(record.material["physical_initial_energy"]),
            max_energy=Fraction(record.material["battery_capacity"]),
            simulation_projection=projection,
        )
        taskset_path = run_root / "taskset_v5.yaml"
        atomic_write_text(taskset_path, _render_taskset_yaml(
            payload, release_horizon=int(record.material["release_horizon"]),
        ))
        trace_path = run_root / "trace_v5.json"
        command = [
            str(self.simulator), str(system_path), str(taskset_path),
            str(window.observation_horizon), "-t", str(trace_path),
            "--run-id", f"rta4-v5-{record.execution_id[:16]}",
            "--taskset-semantic-hash", certificate.taskset_hash,
            "--semantic-traces",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RTA4LocalExecutionV5Error(
                "CORE-3 simulator timeout"
            ) from exc
        if completed.returncode != 0:
            raise RTA4LocalExecutionV5Error(
                f"CORE-3 simulator exited {completed.returncode}"
            )
        result = parse_simulation_trace(
            trace_path,
            payload,
            expected_taskset_hash=certificate.taskset_hash,
            horizon=window.observation_horizon,
            warmup=0,
            minimum_jobs_per_task=0,
            release_e0=Fraction(record.material["physical_initial_energy"]),
            expected_scheduler=str(record.material["scheduler"]),
            expected_processors=certificate.processors,
        )
        if result.status not in {
            SimulationStatus.PASS_OBSERVED,
            SimulationStatus.DEADLINE_MISS,
        }:
            raise RTA4LocalExecutionV5Error(
                f"CORE-3 simulator result is incomplete: {result.status.value}"
            )
        material = {
            "simulation_status": "COMPLETED",
            "observed_status": result.status.value,
            "release_projection_identity": release.release_projection_id,
            "deadline_miss_count": sum(
                bool(job.deadline_miss) for job in result.jobs
            ),
            "task_energy_material_identity": (
                task_energy.task_energy_material_identity
            ),
            "service_material_identity": service.service_material_identity,
            "beta_material_identity": service.beta_material_identity,
            "simulation_tick_ms": simulation_tick_ms,
            "simulation_projection_identity": projection[
                "simulation_projection_identity"
            ],
            "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        }
        return FrozenMapping({
            **material,
            "simulation_result_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:LOCAL_SIMULATION_RESULT:v5", material,
            ),
        })


def _dispatch_existing_worker_v5(
    campaign: LoadedCampaignV5,
    record: FormalPlanRecordV5,
    operation: Mapping[str, Any],
) -> Mapping[str, Any]:
    worker_record, certificate, context, local_identity = (
        _prepared_record_material(campaign, record)
    )
    timeout = int(operation["timeout_seconds"])
    methods = (
        [str(worker_record.material["method"])]
        if record.kind != "simulation" else []
    )
    timeout_contract = {
        method: {
            "initial_timeout_seconds": timeout,
            "retry_timeout_seconds": timeout * 2,
            "maximum_attempts": 2,
        }
        for method in methods
    }
    identity_contract = {
        "formal_profile": RTA4_FORMAL_PROFILE_V5,
        "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:LOCAL_ANALYSIS:v5",
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "timeout_contract": timeout_contract,
    }
    repository = Path(__file__).resolve().parents[2]
    request = V3WorkerRequest(
        record=worker_record,
        certificate=certificate,
        v2_config=default_rta4_formal_config_v2(record.core),
        run_context=context,
        timeout_contract=timeout_contract,
        identity_contract=identity_contract,
        production_manifest={
            "manifest_id": local_identity,
            "simulator_path": (
                operation.get("simulator_path")
                or repository / "build/rtsim/rtsim"
            ),
        },
        system_config_path=str(repository / "system_config_unified_template.yml"),
        energy_support_path=str(
            repository / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
        ),
        output_root=str(operation["output_root"]),
        simulation_timeout_seconds=timeout,
        rta_executor_factory=ProductionRTAExecutorV2,
        simulation_executor_factory=ExactServiceSimulationExecutorV5,
    )
    response = execute_worker_request_v3(request)
    if (
        type(response) is not V3WorkerResponse
        or response.plan_record_identity != record.record_id
        or response.execution_identity != record.execution_id
    ):
        raise RTA4LocalExecutionV5Error("V3 worker response identity drift")
    return response.result


def dispatch_core1_v5(campaign, record, operation):
    return _dispatch_existing_worker_v5(campaign, record, operation)


def dispatch_core2_v5(campaign, record, operation):
    return _dispatch_existing_worker_v5(campaign, record, operation)


def dispatch_core3_v5(campaign, record, operation):
    return _dispatch_existing_worker_v5(campaign, record, operation)


def dispatch_core4_v5(campaign, record, operation):
    return _dispatch_existing_worker_v5(campaign, record, operation)


def dispatch_core5a_v5(campaign, record, operation):
    return _dispatch_existing_worker_v5(campaign, record, operation)


def dispatch_core5b_v5(campaign, record, operation):
    return _dispatch_existing_worker_v5(campaign, record, operation)


CORE_EXECUTION_DISPATCH_V5: Mapping[str, Callable[..., Mapping[str, Any]]] = {
    "CORE-1": dispatch_core1_v5,
    "CORE-2": dispatch_core2_v5,
    "CORE-3": dispatch_core3_v5,
    "CORE-4": dispatch_core4_v5,
    "CORE-5A": dispatch_core5a_v5,
    "CORE-5B": dispatch_core5b_v5,
}


def _strict_positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise RTA4LocalExecutionV5Error(f"{label} must be a positive integer")
    return value


def _timeout_contract_v5(
    records: Sequence[FormalPlanRecordV5], timeout_seconds: int,
) -> dict[str, dict[str, int]]:
    methods = sorted({
        str(record.material["v3_grid_material"]["method"])
        for record in records if record.kind != "simulation"
    })
    return {
        method: {
            "initial_timeout_seconds": timeout_seconds,
            "retry_timeout_seconds": timeout_seconds * 2,
            "maximum_attempts": 2,
        }
        for method in methods
    }


def _worker_bootstrap_v5(
    campaign: LoadedCampaignV5,
    records: Sequence[FormalPlanRecordV5],
    operation: Mapping[str, Any],
) -> V3WorkerBootstrap:
    repository = Path(__file__).resolve().parents[2]
    timeout_contract = _timeout_contract_v5(
        records, int(operation["timeout_seconds"]),
    )
    return V3WorkerBootstrap(
        v2_config=default_rta4_formal_config_v2(
            campaign.normalized_scientific_config["core"]
        ),
        timeout_contract=timeout_contract,
        identity_contract={
            "formal_profile": RTA4_FORMAL_PROFILE_V5,
            "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:LOCAL_ANALYSIS:v5",
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "timeout_contract": timeout_contract,
        },
        production_manifest={
            "manifest_id": _local_material_identity(campaign),
            "simulator_path": (
                operation.get("simulator_path")
                or repository / "build/rtsim/rtsim"
            ),
        },
        system_config_path=str(repository / "system_config_unified_template.yml"),
        energy_support_path=str(
            repository / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
        ),
        output_root=str(operation["output_root"]),
        simulation_timeout_seconds=int(operation["timeout_seconds"]),
        rta_executor_factory=ProductionRTAExecutorV2,
        simulation_executor_factory=ExactServiceSimulationExecutorV5,
    )


def _core5b_groups_v5(
    records: Sequence[FormalPlanRecordV5],
    worker_counts: Sequence[int],
) -> dict[int, tuple[FormalPlanRecordV5, ...]]:
    if (
        not isinstance(worker_counts, (tuple, list))
        or not worker_counts
        or any(type(value) is not int or value < 1 for value in worker_counts)
        or list(worker_counts) != sorted(set(worker_counts))
    ):
        raise RTA4LocalExecutionV5Error(
            "CORE-5B workers must be unique ascending positive integers"
        )
    grouped = {
        worker_count: tuple(
            record for record in records
            if record.material.get("worker_count") == worker_count
        )
        for worker_count in worker_counts
    }
    if any(not group for group in grouped.values()):
        raise RTA4LocalExecutionV5Error(
            "CORE-5B plan is missing a worker-count execution group"
        )
    reference_count = worker_counts[0]
    reference = grouped[reference_count]
    reference_math = [record.mathematical_request_id for record in reference]
    reference_material = [
        {
            "kind": record.kind,
            "core": record.core,
            "taskset_identity": record.taskset_identity,
            "configured_service_identity": record.configured_service_identity,
            "effective_service_identity": record.effective_service_identity,
            "material": {
                key: value for key, value in record.material.items()
                if key != "worker_count"
            },
        }
        for record in reference
    ]
    for worker_count in worker_counts[1:]:
        group = grouped[worker_count]
        if (
            len(group) != len(reference)
            or [record.mathematical_request_id for record in group]
            != reference_math
            or [
                {
                    "kind": record.kind,
                    "core": record.core,
                    "taskset_identity": record.taskset_identity,
                    "configured_service_identity": (
                        record.configured_service_identity
                    ),
                    "effective_service_identity": record.effective_service_identity,
                    "material": {
                        key: value for key, value in record.material.items()
                        if key != "worker_count"
                    },
                }
                for record in group
            ] != reference_material
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-5B worker groups do not share one stable math stream"
            )
    return grouped


def _physical_operation_v5(
    campaign: LoadedCampaignV5,
    records: Sequence[FormalPlanRecordV5],
    root: Path,
    *,
    topology_discoverer: Callable[[], Any],
) -> tuple[dict[str, Any], Mapping[str, Any], dict[int, tuple[Any, ...]]]:
    runtime = dict(campaign.runtime)
    timeout = _strict_positive_int(
        runtime.get("timeout_seconds", 60), "runtime.timeout_seconds"
    )
    scientific = campaign.normalized_scientific_config
    core = scientific["core"]
    if core == "CORE-5B":
        worker_counts = tuple(scientific["v3_plan_grid"]["workers"])
        grouped = _core5b_groups_v5(records, worker_counts)
        required_max = max(worker_counts)
        runtime_cap = _strict_positive_int(
            runtime.get("worker_count", required_max),
            "runtime.worker_count",
        )
        if runtime_cap < required_max:
            raise RTA4LocalExecutionV5Error(
                "CORE-5B runtime.worker_count is below the scientific maximum"
            )
    else:
        runtime_cap = _strict_positive_int(
            runtime.get("worker_count", 1), "runtime.worker_count"
        )
        worker_counts = (runtime_cap,)
        grouped = {runtime_cap: tuple(records)}
    topology = topology_discoverer()
    selected = {
        worker_count: topology.select(worker_count)
        for worker_count in worker_counts
    }
    max_in_flight = _strict_positive_int(
        runtime.get("max_in_flight", 2 * runtime_cap),
        "runtime.max_in_flight",
    )
    if max_in_flight < max(worker_counts):
        raise RTA4LocalExecutionV5Error(
            "runtime.max_in_flight must cover the largest physical group"
        )
    operation = {
        "output_root": root,
        "timeout_seconds": timeout,
        "worker_count": runtime_cap,
        "max_in_flight": max_in_flight,
        "simulator_path": runtime.get("simulator_path"),
    }
    groups = tuple({
        "worker_count": worker_count,
        "mathematical_request_count": len(grouped[worker_count]),
        "mathematical_request_identities": [
            record.mathematical_request_id for record in grouped[worker_count]
        ],
        "selected_physical_cores": [
            row.as_dict() for row in selected[worker_count]
        ],
    } for worker_count in worker_counts)
    environment = {
        "execution_backend": PHYSICAL_CORE_EXECUTION_BACKEND_V3,
        "physical_core_binding_required": True,
        "topology_fingerprint": topology.topology_fingerprint,
        "available_physical_core_count": topology.physical_core_count,
        "allowed_logical_cpus": list(topology.allowed_logical_cpus),
        "topology_selection_policy": topology.selection_policy,
        "physical_execution_groups": groups,
    }
    return operation, environment, selected


def _test_operation_v5(
    campaign: LoadedCampaignV5, root: Path,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    runtime = dict(campaign.runtime)
    timeout = _strict_positive_int(
        runtime.get("timeout_seconds", 60), "runtime.timeout_seconds"
    )
    operation = {
        "output_root": root,
        "timeout_seconds": timeout,
        "worker_count": _strict_positive_int(
            runtime.get("worker_count", 1), "runtime.worker_count"
        ),
        "max_in_flight": _strict_positive_int(
            runtime.get("max_in_flight", 1), "runtime.max_in_flight"
        ),
        "simulator_path": runtime.get("simulator_path"),
    }
    environment = {
        "execution_backend": RTA4_TEST_EXECUTION_BACKEND_V5,
        "physical_core_binding_required": False,
        "topology_fingerprint": None,
        "available_physical_core_count": 0,
        "allowed_logical_cpus": [],
        "topology_selection_policy": "TEST_ONLY_EXPLICIT_DISPATCHERS",
        "physical_execution_groups": [],
    }
    return operation, environment


class LocalResultWriterV5:
    """Atomic terminal/checkpoint namespace for non-paper V5 execution."""

    def __init__(
        self,
        root: Path,
        *,
        campaign: LoadedCampaignV5,
        plan: Mapping[str, Any],
        records: Sequence[FormalPlanRecordV5],
        execution_environment: Mapping[str, Any],
        resume: bool,
    ) -> None:
        self.root = root
        self.terminals = root / RTA4_LOCAL_TERMINALS_V5
        self.marker = root / RTA4_LOCAL_RUN_MANIFEST_V5
        rows = [{
            "ordinal": record.ordinal,
            "core": record.core,
            "kind": record.kind,
            "plan_record_identity": record.record_id,
            "mathematical_request_identity": record.mathematical_request_id,
            "execution_identity": record.execution_id,
            "taskset_identity": record.taskset_identity,
            "taskset_content_sha256": record.material[
                "taskset_content_sha256"
            ],
            "task_order_sha256": record.material["task_order_sha256"],
            "configured_service_identity": record.configured_service_identity,
            "effective_service_identity": record.effective_service_identity,
            **({
                "simulation_tick_ms": record.material["simulation_tick_ms"],
                "simulation_projection_identity": record.material[
                    "service_material"
                ]["simulation_projection"]["simulation_projection_identity"],
            } if record.core == "CORE-3" else {}),
        } for record in records]
        material = {
            "schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_RUN_V5",
            "profile": RTA4_FORMAL_PROFILE_V5,
            "campaign_id": campaign.normalized_scientific_config["campaign_id"],
            "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
            "normalized_scientific_config_sha256": (
                campaign.normalized_scientific_config_sha256
            ),
            "plan_sha256": plan["plan_sha256"],
            "ordered_stream_digest": plan["ordered_stream_digest"],
            "output_root": str(root.resolve()),
            "execution_class": "LOCAL_NOT_FOR_PAPER",
            "formal_campaign_started": False,
            "paper_result_authorized": False,
            "not_for_paper": True,
            "execution_backend": execution_environment["execution_backend"],
            "physical_core_binding_required": execution_environment[
                "physical_core_binding_required"
            ],
            "topology_fingerprint": execution_environment[
                "topology_fingerprint"
            ],
            "available_physical_core_count": execution_environment[
                "available_physical_core_count"
            ],
            "allowed_logical_cpus": list(execution_environment[
                "allowed_logical_cpus"
            ]),
            "topology_selection_policy": execution_environment[
                "topology_selection_policy"
            ],
            "physical_execution_groups": _plain(execution_environment[
                "physical_execution_groups"
            ]),
            "plan_records": rows,
        }
        self.run_manifest = {
            **material,
            "run_identity": domain_hash(RTA4_LOCAL_RUN_DOMAIN_V5, material),
        }
        if root.exists() and any(root.iterdir()) and not self.marker.is_file():
            raise RTA4LocalExecutionV5Error(
                "local output root belongs to another namespace"
            )
        if resume and (
            not self.marker.is_file() or not self.terminals.is_dir()
        ):
            raise RTA4LocalExecutionV5Error(
                "resume requires a complete V5 local namespace"
            )
        if self.marker.is_file() and not resume:
            raise RTA4LocalExecutionV5Error(
                "existing V5 local namespace requires resume=true"
            )
        if self.marker.is_file():
            try:
                existing = json.loads(self.marker.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RTA4LocalExecutionV5Error(
                    "local run manifest is unreadable"
                ) from exc
            if existing != self.run_manifest:
                raise RTA4LocalExecutionV5Error(
                    "local output root belongs to another V5 run"
                )
        self.terminals.mkdir(parents=True, exist_ok=True)
        if not self.marker.is_file():
            atomic_write_json(self.marker, self.run_manifest)
        self._plan = {record.execution_id: record for record in records}

    def completed_rows(self) -> dict[str, Mapping[str, Any]]:
        completed: dict[str, Mapping[str, Any]] = {}
        for path in sorted(self.terminals.glob("*.json")):
            row = json.loads(path.read_text(encoding="utf-8"))
            unsigned = dict(row)
            observed = unsigned.pop("result_identity", None)
            execution = row.get("execution_identity")
            if (
                path.stem != execution
                or execution not in self._plan
                or observed != domain_hash(RTA4_LOCAL_RESULT_DOMAIN_V5, unsigned)
                or row.get("run_identity") != self.run_manifest["run_identity"]
                or row.get("not_for_paper") is not True
            ):
                raise RTA4LocalExecutionV5Error(
                    "local terminal identity or classification drift"
                )
            completed[str(execution)] = row
        checkpoint = self.root / RTA4_LOCAL_CHECKPOINT_V5
        if checkpoint.is_file():
            value = json.loads(checkpoint.read_text(encoding="utf-8"))
            unsigned = dict(value)
            observed = unsigned.pop("checkpoint_identity", None)
            if (
                observed != domain_hash(
                    RTA4_LOCAL_CHECKPOINT_DOMAIN_V5, unsigned
                )
                or value.get("run_identity") != self.run_manifest["run_identity"]
                or not set(value.get("completed_execution_ids", [])).issubset(
                    completed
                )
            ):
                raise RTA4LocalExecutionV5Error("local checkpoint drift")
        return completed

    def write_result(self, row: Mapping[str, Any]) -> None:
        execution = str(row.get("execution_identity", ""))
        if execution not in self._plan:
            raise RTA4LocalExecutionV5Error("result lies outside V5 plan")
        path = self.terminals / f"{execution}.json"
        payload = canonical_json(row) + "\n"
        if path.is_file():
            if path.read_text(encoding="utf-8") != payload:
                raise RTA4LocalExecutionV5Error("terminal content conflict")
            return
        atomic_write_text(path, payload)

    def write_checkpoint(
        self, completed_execution_ids: Sequence[str],
    ) -> Mapping[str, Any]:
        completed = sorted(set(completed_execution_ids))
        if not set(completed).issubset(self._plan):
            raise RTA4LocalExecutionV5Error(
                "checkpoint contains an unknown execution"
            )
        material = {
            "checkpoint_schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_CHECKPOINT_V5",
            "run_identity": self.run_manifest["run_identity"],
            "plan_sha256": self.run_manifest["plan_sha256"],
            "ordered_stream_count": len(self._plan),
            "completed_execution_ids": completed,
            "complete": len(completed) == len(self._plan),
            "formal_campaign_started": False,
            "paper_result_authorized": False,
            "not_for_paper": True,
        }
        value = {
            **material,
            "checkpoint_identity": domain_hash(
                RTA4_LOCAL_CHECKPOINT_DOMAIN_V5, material
            ),
        }
        atomic_write_json(self.root / RTA4_LOCAL_CHECKPOINT_V5, value)
        return value


def _terminal_row(
    writer: LocalResultWriterV5,
    record: FormalPlanRecordV5,
    result: Mapping[str, Any],
    *,
    worker_backend: str,
    physical_core_binding_required: bool,
) -> dict[str, Any]:
    payload = {
        "row_schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V5",
        "profile": RTA4_FORMAL_PROFILE_V5,
        "execution_class": "LOCAL_NOT_FOR_PAPER",
        "run_identity": writer.run_manifest["run_identity"],
        "plan_sha256": writer.run_manifest["plan_sha256"],
        "core": record.core,
        "kind": record.kind,
        "plan_record_identity": record.record_id,
        "mathematical_request_identity": record.mathematical_request_id,
        "execution_identity": record.execution_id,
        "taskset_identity": record.taskset_identity,
        "taskset_content_sha256": record.material["taskset_content_sha256"],
        "task_order_sha256": record.material["task_order_sha256"],
        "configured_service_identity": record.configured_service_identity,
        "effective_service_identity": record.effective_service_identity,
        **({
            "simulation_tick_ms": record.material["simulation_tick_ms"],
            "simulation_projection_identity": record.material[
                "service_material"
            ]["simulation_projection"]["simulation_projection_identity"],
        } if record.core == "CORE-3" else {}),
        "worker_backend": worker_backend,
        "physical_core_binding_required": physical_core_binding_required,
        "result": _plain(result),
        "formal_campaign_started": False,
        "paper_result_authorized": False,
        "not_for_paper": True,
    }
    return {
        **payload,
        "result_identity": domain_hash(RTA4_LOCAL_RESULT_DOMAIN_V5, payload),
    }


def _test_failure_result_v5(
    record: FormalPlanRecordV5,
    detail: str,
    *,
    malformed: bool,
) -> Mapping[str, Any]:
    classification = detail[:500]
    common = {
        "solver_status": "INTERNAL_ERROR",
        "attempts": [{
            "attempt_index": 0,
            "status": "INTERNAL_ERROR",
            "error_classification": classification,
        }],
        "failure_reason": classification,
        "failure_closed": True,
        "protocol_malformed_result": malformed,
    }
    if record.kind == "simulation":
        return {
            **common,
            "status": "INTERNAL_ERROR",
            "error_classification": classification,
            "result": {"failure_reason": classification},
        }
    return {**common, "taskset_proven": False}


def _valid_physical_diagnostics_v5(
    writer: LocalResultWriterV5,
    record: FormalPlanRecordV5 | None,
    result: Mapping[str, Any],
    attempts: Any,
    diagnostics: Any,
) -> bool:
    if (
        result.get("worker_backend")
        != writer.run_manifest["execution_backend"]
        or result.get("physical_core_binding_required") is not True
        or not isinstance(diagnostics, (tuple, list))
        or not diagnostics
    ):
        return False
    selected = {
        (
            row.get("selected_logical_cpu_id"),
            row.get("physical_package_id"),
            row.get("physical_core_id"),
        )
        for group in writer.run_manifest["physical_execution_groups"]
        for row in group.get("selected_physical_cores", [])
        if isinstance(row, Mapping)
    }
    required = {
        "attempt_index",
        "worker_pid",
        "slot_id",
        "worker_generation",
        "logical_cpu_id",
        "physical_package_id",
        "physical_core_id",
        "affinity_mask",
        "started_monotonic_ns",
        "finished_monotonic_ns",
        "timed_out",
        "worker_exit",
        "error_classification",
    }
    observed_indices = []
    for item in diagnostics:
        if not isinstance(item, Mapping) or not required.issubset(item):
            return False
        integer_fields = (
            "attempt_index",
            "worker_pid",
            "slot_id",
            "worker_generation",
            "logical_cpu_id",
            "physical_package_id",
            "physical_core_id",
            "started_monotonic_ns",
            "finished_monotonic_ns",
        )
        if any(type(item[field]) is not int for field in integer_fields):
            return False
        if (
            item["attempt_index"] not in {0, 1}
            or item["worker_pid"] < 1
            or item["slot_id"] < 0
            or item["worker_generation"] < 0
            or item["logical_cpu_id"] < 0
            or item["physical_package_id"] < 0
            or item["physical_core_id"] < 0
            or item["finished_monotonic_ns"]
            < item["started_monotonic_ns"]
            or type(item["timed_out"]) is not bool
        ):
            return False
        affinity = item["affinity_mask"]
        if (
            not isinstance(affinity, (tuple, list))
            or list(affinity) != [item["logical_cpu_id"]]
            or (
                item["logical_cpu_id"],
                item["physical_package_id"],
                item["physical_core_id"],
            ) not in selected
        ):
            return False
        if item["worker_exit"] is not None and type(
            item["worker_exit"]
        ) is not int:
            return False
        if item["error_classification"] is not None and not isinstance(
            item["error_classification"], str,
        ):
            return False
        observed_indices.append(item["attempt_index"])
    if record is not None and record.kind == "simulation":
        return observed_indices == [0]
    if not isinstance(attempts, (tuple, list)):
        return False
    return observed_indices == [attempt.get("attempt_index") for attempt in attempts]


def _terminal_counts_v5(
    writer: LocalResultWriterV5,
    rows: Mapping[str, Mapping[str, Any]],
    *,
    execution_ids: Sequence[str] | None = None,
) -> dict[str, int]:
    selected = set(rows) if execution_ids is None else set(execution_ids)
    counts = {
        "terminal_count": 0,
        "internal_error_count": 0,
        "terminal_timeout_count": 0,
        "attempt_timeout_count": 0,
        "simulation_failure_count": 0,
        "malformed_result_count": 0,
    }
    for execution in sorted(selected):
        row = rows.get(execution)
        if row is None:
            continue
        counts["terminal_count"] += 1
        malformed = False
        record = writer._plan.get(execution)
        if record is None or any(
            row.get(key) != expected for key, expected in (
                ("core", record.core),
                ("kind", record.kind),
                ("plan_record_identity", record.record_id),
                ("mathematical_request_identity", record.mathematical_request_id),
                ("taskset_identity", record.taskset_identity),
                ("configured_service_identity", record.configured_service_identity),
                ("effective_service_identity", record.effective_service_identity),
                ("worker_backend", writer.run_manifest["execution_backend"]),
                (
                    "physical_core_binding_required",
                    writer.run_manifest["physical_core_binding_required"],
                ),
            )
        ):
            malformed = True
        result = row.get("result")
        if not isinstance(result, Mapping):
            counts["malformed_result_count"] += 1
            continue
        if result.get("protocol_malformed_result") is True:
            malformed = True
        attempts = result.get("attempts")
        if attempts is not None:
            if not isinstance(attempts, (tuple, list)) or not attempts:
                malformed = True
            else:
                for attempt in attempts:
                    if not isinstance(attempt, Mapping):
                        malformed = True
                        continue
                    status = attempt.get("status")
                    if status not in _VALID_SOLVER_STATUSES_V5:
                        malformed = True
                    elif status == "TIMEOUT":
                        counts["attempt_timeout_count"] += 1
        diagnostics = result.get("execution_attempt_diagnostics")
        if row.get("physical_core_binding_required") is True:
            if not _valid_physical_diagnostics_v5(
                writer, record, result, attempts, diagnostics,
            ):
                malformed = True
        if record is not None and record.kind == "simulation":
            status = result.get("status")
            nested = result.get("result")
            if (
                status != "COMPLETED"
                or not isinstance(nested, Mapping)
                or nested.get("simulation_status") != "COMPLETED"
                or nested.get("observed_status")
                not in _VALID_SIMULATION_OBSERVED_STATUSES_V5
            ):
                counts["simulation_failure_count"] += 1
            if status == "INTERNAL_ERROR" or result.get(
                "solver_status"
            ) == "INTERNAL_ERROR":
                counts["internal_error_count"] += 1
            if status == "TIMEOUT":
                counts["terminal_timeout_count"] += 1
            if status not in {"COMPLETED", "INTERNAL_ERROR", "TIMEOUT"}:
                malformed = True
        else:
            solver_status = result.get("solver_status")
            if solver_status not in _VALID_SOLVER_STATUSES_V5:
                malformed = True
            else:
                if solver_status == "INTERNAL_ERROR":
                    counts["internal_error_count"] += 1
                if solver_status == "TIMEOUT":
                    counts["terminal_timeout_count"] += 1
            if not isinstance(attempts, (tuple, list)) or not attempts:
                malformed = True
        if malformed:
            counts["malformed_result_count"] += 1
    return counts


def execute_loaded_campaign_v5(
    campaign: LoadedCampaignV5,
    *,
    acknowledge_not_for_paper: bool,
    output_root: Path | str | None = None,
    resume: bool | None = None,
    max_records: int | None = None,
    dispatchers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
    _topology_discoverer: Callable[[], Any] = discover_cpu_topology_v3,
    _pool_factory: Callable[..., Any] = PhysicalCoreSlotPoolV3,
    _physical_group_executor: Callable[..., Mapping[str, Any]] = (
        execute_physical_group_v5
    ),
) -> dict[str, Any]:
    if acknowledge_not_for_paper is not True:
        raise RTA4LocalExecutionV5Error(
            "local execution requires acknowledge_not_for_paper=true"
        )
    runtime = dict(campaign.runtime)
    root_value = output_root if output_root is not None else runtime.get(
        "output_root"
    )
    if root_value is None:
        raise RTA4LocalExecutionV5Error(
            "local execution requires runtime.output_root"
        )
    root = Path(root_value).expanduser().resolve(strict=False)
    use_resume = bool(runtime.get("resume", False)) if resume is None else resume
    if type(use_resume) is not bool:
        raise RTA4LocalExecutionV5Error("resume must be a strict boolean")
    configured_limit = runtime.get("max_records")
    limit = configured_limit if max_records is None else max_records
    if limit is not None and (type(limit) is not int or limit < 0):
        raise RTA4LocalExecutionV5Error("max_records must be non-negative")
    scientific = campaign.normalized_scientific_config
    plan = describe_formal_plan_v5(
        scientific, campaign.task_sources, campaign.service_curve,
    )
    all_records = tuple(iter_formal_plan_v5(
        scientific, campaign.task_sources, campaign.service_curve,
    ))
    production_execution = dispatchers is None
    try:
        if production_execution:
            operation, execution_environment, selected_cores = (
                _physical_operation_v5(
                    campaign,
                    all_records,
                    root,
                    topology_discoverer=_topology_discoverer,
                )
            )
            selected_dispatch = None
        else:
            operation, execution_environment = _test_operation_v5(
                campaign, root,
            )
            selected_cores = {}
            if set(dispatchers) != set(CORE_EXECUTION_DISPATCH_V5):
                raise RTA4LocalExecutionV5Error(
                    "local execution dispatcher set must cover all six cores"
                )
            selected_dispatch = dispatchers
    except (PhysicalCoreSlotV3Error, RTA4PhysicalExecutionV5Error) as exc:
        raise RTA4LocalExecutionV5Error(
            f"V5 physical execution setup failed: {exc}"
        ) from exc
    writer = LocalResultWriterV5(
        operation["output_root"],
        campaign=campaign,
        plan=plan,
        records=all_records,
        execution_environment=execution_environment,
        resume=use_resume,
    )
    completed = writer.completed_rows()
    if use_resume:
        writer.write_checkpoint(tuple(completed))
    pending_records = [
        record for record in all_records
        if record.execution_id not in completed
    ]
    target_records = (
        pending_records if limit is None else pending_records[:limit]
    )
    invocation_execution_ids = tuple(
        record.execution_id for record in target_records
    )
    throttle = _CheckpointThrottleV3(
        writer, completed, every_records=1, every_seconds=30,
    )
    processed = 0
    physical_execution_groups: list[Mapping[str, Any]] = []

    def persist(record: FormalPlanRecordV5, result: Mapping[str, Any]) -> None:
        nonlocal processed
        row = _terminal_row(
            writer,
            record,
            result,
            worker_backend=str(execution_environment["execution_backend"]),
            physical_core_binding_required=bool(
                execution_environment["physical_core_binding_required"]
            ),
        )
        writer.write_result(row)
        completed[record.execution_id] = row
        processed += 1
        throttle.terminal_committed()
        throttle.write_if_due()

    try:
        if selected_dispatch is not None:
            group_started = time.monotonic_ns()
            for record in target_records:
                dispatcher = selected_dispatch[record.core]
                try:
                    result = dispatcher(campaign, record, operation)
                    if not isinstance(result, Mapping):
                        result = _test_failure_result_v5(
                            record,
                            "MALFORMED_TEST_DISPATCH_RESULT:not a mapping",
                            malformed=True,
                        )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    result = _test_failure_result_v5(
                        record,
                        f"TEST_DISPATCH_FAILURE:{type(exc).__name__}:{exc}",
                        malformed=False,
                    )
                persist(record, result)
            group_finished = time.monotonic_ns()
            physical_execution_groups.append({
                "execution_backend": RTA4_TEST_EXECUTION_BACKEND_V5,
                "worker_count": 0,
                "requested_record_count": len(target_records),
                "completed_record_count": processed,
                "group_started_monotonic_ns": group_started,
                "group_finished_monotonic_ns": group_finished,
                "elapsed_wall_seconds": (
                    group_finished - group_started
                ) / 1_000_000_000,
            })
        else:
            bootstrap = _worker_bootstrap_v5(
                campaign, all_records, operation,
            )
            prepared_by_execution: dict[str, PreparedPhysicalRecordV5] = {}
            expected_material_identity = _local_material_identity(campaign)
            for record in target_records:
                worker_record, certificate, context, material_identity = (
                    _prepared_record_material(campaign, record)
                )
                if material_identity != expected_material_identity:
                    raise RTA4LocalExecutionV5Error(
                        "V5 prepared material identity drift"
                    )
                prepared_by_execution[record.execution_id] = (
                    PreparedPhysicalRecordV5(
                        record,
                        worker_record,
                        certificate,
                        context,
                    )
                )
            if scientific["core"] == "CORE-5B":
                group_counts = tuple(scientific["v3_plan_grid"]["workers"])
                grouped_targets = {
                    worker_count: tuple(
                        prepared_by_execution[record.execution_id]
                        for record in target_records
                        if record.material.get("worker_count") == worker_count
                    )
                    for worker_count in group_counts
                }
            else:
                group_counts = (int(operation["worker_count"]),)
                grouped_targets = {
                    group_counts[0]: tuple(
                        prepared_by_execution[record.execution_id]
                        for record in target_records
                    ),
                }
            for worker_count in group_counts:
                group = grouped_targets[worker_count]
                if not group:
                    continue
                try:
                    evidence = _physical_group_executor(
                        worker_count=worker_count,
                        selected_cores=selected_cores[worker_count],
                        prepared_records=group,
                        bootstrap=bootstrap,
                        max_in_flight=int(operation["max_in_flight"]),
                        terminal_callback=persist,
                        pool_factory=_pool_factory,
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    raise RTA4LocalExecutionV5Error(
                        "V5 physical execution group failed: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                if not isinstance(evidence, Mapping):
                    raise RTA4LocalExecutionV5Error(
                        "V5 physical group returned no evidence mapping"
                    )
                physical_execution_groups.append(_plain(evidence))
    finally:
        checkpoint = throttle.write_if_due(force=True)
    assert checkpoint is not None
    completed = writer.completed_rows()
    total_counts = _terminal_counts_v5(writer, completed)
    invocation_counts = _terminal_counts_v5(
        writer,
        completed,
        execution_ids=invocation_execution_ids,
    )
    expected_count = len(all_records)
    terminal_complete = total_counts["terminal_count"] == expected_count
    total_failure_count = sum(
        total_counts[key] for key in (
            "internal_error_count",
            "terminal_timeout_count",
            "simulation_failure_count",
            "malformed_result_count",
        )
    )
    invocation_failure_count = sum(
        invocation_counts[key] for key in (
            "internal_error_count",
            "terminal_timeout_count",
            "simulation_failure_count",
            "malformed_result_count",
        )
    )
    invocation_clean = (
        invocation_counts["terminal_count"] == len(invocation_execution_ids)
        and invocation_failure_count == 0
    )
    return {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "campaign_id": scientific["campaign_id"],
        "core": scientific["core"],
        "plan_sha256": plan["plan_sha256"],
        "expected_count": expected_count,
        "terminal_count": total_counts["terminal_count"],
        "processed_records": processed,
        "pending_records": expected_count - total_counts["terminal_count"],
        "terminal_complete": terminal_complete,
        "complete": terminal_complete,
        "internal_error_count": total_counts["internal_error_count"],
        "terminal_timeout_count": total_counts["terminal_timeout_count"],
        "attempt_timeout_count": total_counts["attempt_timeout_count"],
        "simulation_failure_count": total_counts[
            "simulation_failure_count"
        ],
        "malformed_result_count": total_counts["malformed_result_count"],
        "clean_complete": terminal_complete and total_failure_count == 0,
        "invocation_target_count": len(invocation_execution_ids),
        "invocation_terminal_count": invocation_counts["terminal_count"],
        "invocation_internal_error_count": invocation_counts[
            "internal_error_count"
        ],
        "invocation_terminal_timeout_count": invocation_counts[
            "terminal_timeout_count"
        ],
        "invocation_attempt_timeout_count": invocation_counts[
            "attempt_timeout_count"
        ],
        "invocation_simulation_failure_count": invocation_counts[
            "simulation_failure_count"
        ],
        "invocation_malformed_result_count": invocation_counts[
            "malformed_result_count"
        ],
        "invocation_clean": invocation_clean,
        "bounded_smoke": limit is not None,
        "execution_backend": execution_environment["execution_backend"],
        "physical_core_binding_required": execution_environment[
            "physical_core_binding_required"
        ],
        "topology_fingerprint": execution_environment[
            "topology_fingerprint"
        ],
        "available_physical_core_count": execution_environment[
            "available_physical_core_count"
        ],
        "allowed_logical_cpus": list(execution_environment[
            "allowed_logical_cpus"
        ]),
        "topology_selection_policy": execution_environment[
            "topology_selection_policy"
        ],
        "physical_execution_groups": physical_execution_groups,
        "checkpoint_path": str(
            operation["output_root"] / RTA4_LOCAL_CHECKPOINT_V5
        ),
        "execution_started": True,
        "formal_campaign_started": False,
        "paper_result_authorized": False,
        "not_for_paper": True,
    }


def execute_local_campaign_v5(
    path: Path | str,
    *,
    acknowledge_not_for_paper: bool,
    output_root: Path | str | None = None,
    resume: bool | None = None,
    max_records: int | None = None,
    dispatchers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    campaign = load_rta4_campaign_v5(path)
    return execute_loaded_campaign_v5(
        campaign,
        acknowledge_not_for_paper=acknowledge_not_for_paper,
        output_root=output_root,
        resume=resume,
        max_records=max_records,
        dispatchers=dispatchers,
    )


__all__ = [
    "CORE_EXECUTION_DISPATCH_V5",
    "ExactServiceSimulationExecutorV5",
    "LocalResultWriterV5",
    "RTA4LocalExecutionV5Error",
    "dispatch_core1_v5",
    "dispatch_core2_v5",
    "dispatch_core3_v5",
    "dispatch_core4_v5",
    "dispatch_core5a_v5",
    "dispatch_core5b_v5",
    "execute_loaded_campaign_v5",
    "execute_local_campaign_v5",
]
