"""Direct persistent execution for selectable-service RTA V5.

This module is deliberately outside the frozen V3/V4 profiles.  It adapts V5
plan records to the established V2 executor and V3 worker request protocol,
and reuses the V3 checkpoint throttle.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

import yaml

from experiments.common.exact_service_curve import normalize_exact_service_curve

from . import exact_energy
from .rta4_core3_artifacts_v6 import (
    RTA4Core3ArtifactV6Error,
    artifact_binding_from_row_v1,
    artifact_sha256_size_v1,
    fsync_directory_v1,
    load_bound_gzip_json_v1,
    load_legacy_bound_json_v1,
    prefixed_artifact_binding_v1,
    publish_deterministic_gzip_json_v1,
    strict_json_file_v6,
)
from .rta4_core3_contracts_v6 import (
    RTA4Core3ContractV6Error,
    require_normalized_core3_artifact_storage_v1,
)
from .rta4_core3_contracts_v7 import (
    CORE3_RESULT_DOMAIN_V7,
    CORE3_RESULT_SCHEMA_V7,
    CORE3_SIMULATION_CONTRACT_V7,
    CORE3_TASK_WORKLOAD_V7,
    RTA4Core3ContractV7Error,
    core3_physical_execution_identity_v7,
    core3_task_physical_projection_v7,
    require_core3_task_physical_projection_v7,
)
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_config_v2 import default_rta4_formal_config_v2
from .rta4_formal_config_v5 import (
    CORE3_RESULT_DOMAIN_V6,
    CORE3_RESULT_SCHEMA_V6,
    LoadedCampaignV5,
    RTA4_FORMAL_PROFILE_V5,
    formal_taskset_store_identity_v5,
    load_rta4_campaign_v5,
    task_source_material_identity_v5,
)
from .rta4_energy_service_v5 import (
    core3_simulation_projection_v5,
    core3_simulation_projection_v6,
)
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
from .checkpoint_throttle import CheckpointThrottle
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
from .simulation_result import (
    CORE3_ENERGY_TOLERANCE_J,
    CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6,
    SimulationStatus,
)


RTA4_LOCAL_RUN_MANIFEST_V5 = "local_run_manifest_v5.json"
RTA4_LOCAL_CHECKPOINT_V5 = "local_checkpoint_v5.json"
RTA4_LOCAL_TERMINALS_V5 = "local_terminal_results_v5"
RTA4_LOCAL_RUN_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RUN:v5"
RTA4_LOCAL_RESULT_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RESULT:v5"
RTA4_LOCAL_RESULT_DOMAIN_V6 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RESULT:v6"
RTA4_LOCAL_RESULT_DOMAIN_V7 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RESULT:v7"
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


@dataclass(frozen=True)
class PreparedRecordResultV5:
    ordinal: int
    execution_id: str
    worker_record: "LocalWorkerRecordV5"
    certificate: Any
    context: SharedEnergyRunContext
    material_identity: str
    worker_pid: int


@dataclass(frozen=True)
class TerminalValidationRequestV5:
    terminal_path: Path
    output_root: Path
    planned_execution_id: str | None
    run_identity: str
    artifact_storage: Mapping[str, Any] | None


@dataclass(frozen=True)
class ValidatedTerminalV5:
    execution_identity: str
    row: Mapping[str, Any]
    worker_pid: int


_PREPARATION_CAMPAIGN_V5: LoadedCampaignV5 | None = None


def _root_exception_type_v5(exc: BaseException) -> str:
    root = exc
    seen = set()
    while root.__cause__ is not None and id(root) not in seen:
        if type(root.__cause__).__name__ == "_RemoteTraceback":
            break
        seen.add(id(root))
        root = root.__cause__
    return type(root).__name__


@dataclass(frozen=True)
class _Core3ObservationWindowV6:
    release_horizon: int
    maximum_relative_deadline: int
    observation_horizon: int

    def __post_init__(self) -> None:
        if (
            type(self.release_horizon) is not int
            or type(self.maximum_relative_deadline) is not int
            or type(self.observation_horizon) is not int
            or self.release_horizon <= 0
            or self.maximum_relative_deadline <= 0
            or self.observation_horizon
            != self.release_horizon + self.maximum_relative_deadline
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 V6 observation window is inconsistent"
            )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _exact_numeric_text_v6(value: Any) -> str:
    """Encode a validated trace scalar without leaking a binary float.

    Schema-3 is JSON and therefore supplies decimal numeric tokens.  The
    parser deliberately uses floats for tolerance checks, but V6 terminal
    material participates in the repository's exact, float-free identity
    domain.  Converting through the displayed decimal preserves that input
    precision and yields the canonical rational representation used by the
    rest of RTA4.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V6 numeric summary is not a scalar"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V6 numeric summary is not finite"
        )
    return str(Fraction(str(value)))


def write_core3_job_observations_v6(
    path: Path | str,
    *,
    execution_identity: str,
    jobs: Sequence[Any],
) -> dict[str, Any]:
    """Atomically persist the bounded terminal's unbounded job payload."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write('{"execution_identity":')
            handle.write(json.dumps(execution_identity, ensure_ascii=False))
            handle.write(',"job_observation_count":')
            handle.write(str(len(jobs)))
            handle.write(',"job_observations":[')
            for index, job in enumerate(jobs):
                if index:
                    handle.write(",")
                row = job.row() if hasattr(job, "row") else _plain(job)
                handle.write(json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ))
            handle.write('],"job_observations_schema_version":')
            handle.write(json.dumps(
                CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6,
                ensure_ascii=False,
            ))
            handle.write("}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        fsync_directory_v1(destination.parent)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    digest, _size = artifact_sha256_size_v1(destination)
    return {
        "job_observations_sha256": digest,
        "job_observation_count": len(jobs),
        "job_observations_schema_version": (
            CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6
        ),
    }


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
        "task_source_material_identity": task_source_material_identity_v5(
            campaign.normalized_scientific_config
        ),
        "classification": "DIRECT_LOCAL_EXECUTION",
    })


def _worker_record(record: FormalPlanRecordV5) -> LocalWorkerRecordV5:
    grid = dict(record.material.get(
        "effective_core3_simulation_material",
        record.material["v3_grid_material"],
    ))
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
    core3_contract = (
        scientific.get("core3_simulation_contract")
        if record.core == "CORE-3" else None
    )
    core3_v7 = (
        isinstance(core3_contract, Mapping)
        and core3_contract.get("contract_version")
        == CORE3_SIMULATION_CONTRACT_V7
    )
    model_energy_unit_joules = (
        scientific["model_energy_unit_joules"] if core3_v7 else None
    )
    prepared = prepare_execution_material_v5(
        taskset=taskset,
        processors=binding.source.processors,
        task_source_identity=binding.source.identity,
        taskset_store_identity=formal_taskset_store_identity_v5(scientific),
        runtime_material_identity=local_material_identity,
        service_curve=curve,
        core=record.core,
        grid_material=record.material.get(
            "effective_core3_simulation_material",
            record.material["v3_grid_material"],
        ),
        service_material_horizon=horizon,
        simulation_tick_ms=simulation_tick_ms,
        model_energy_unit_joules=model_energy_unit_joules,
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
            **({
                "model_energy_unit_joules": model_energy_unit_joules,
            } if model_energy_unit_joules is not None else {}),
        })
    context = SharedEnergyRunContext(
        local_material_identity,
        FrozenMapping({task_identity: prepared.task_energy}),
        FrozenMapping({service_identity: prepared.service}),
        FrozenMapping({worker_record.record_id: FrozenMapping(binding_material)}),
        FrozenMapping({}),
    )
    return worker_record, prepared.certificate, context, local_material_identity


def _initialize_preparation_worker_v5(campaign: LoadedCampaignV5) -> None:
    """Install one immutable campaign context per spawned preparation worker."""

    global _PREPARATION_CAMPAIGN_V5
    _PREPARATION_CAMPAIGN_V5 = campaign


def _prepare_record_worker_v5(
    record: FormalPlanRecordV5,
) -> PreparedRecordResultV5:
    campaign = _PREPARATION_CAMPAIGN_V5
    if campaign is None:
        raise RTA4LocalExecutionV5Error(
            "V5 preparation worker has no initialized campaign"
        )
    worker, certificate, context, material_identity = (
        _prepared_record_material(campaign, record)
    )
    return PreparedRecordResultV5(
        record.ordinal,
        record.execution_id,
        worker,
        certificate,
        context,
        material_identity,
        os.getpid(),
    )


def _run_spawn_tasks_ordered_v5(
    items: Sequence[Any],
    labels: Sequence[str],
    *,
    worker_count: int,
    worker: Callable[[Any], Any],
    initializer: Callable[..., None] | None = None,
    initargs: tuple[Any, ...] = (),
    phase: str,
) -> tuple[Any, ...]:
    """Run a bounded number of spawned tasks and restore input order."""

    values = tuple(items)
    task_labels = tuple(labels)
    if len(values) != len(task_labels) or len(values) < 2:
        raise RTA4LocalExecutionV5Error(
            f"{phase} parallel scheduler received an invalid task batch"
        )
    if type(worker_count) is not int or not 1 <= worker_count <= len(values):
        raise RTA4LocalExecutionV5Error(
            f"{phase} parallel worker count is invalid"
        )
    executor = ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=initializer,
        initargs=initargs,
    )
    pending: dict[Any, int] = {}
    results: list[Any | None] = [None] * len(values)
    next_index = 0

    try:
        while next_index < len(values) and len(pending) < worker_count:
            future = executor.submit(worker, values[next_index])
            pending[future] = next_index
            next_index += 1
        while pending:
            done, _not_done = wait(
                tuple(pending), return_when=FIRST_COMPLETED,
            )
            ordered_done = sorted(
                (pending[future], future) for future in done
            )
            for index, future in ordered_done:
                pending.pop(future)
                try:
                    results[index] = future.result()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    raise RTA4LocalExecutionV5Error(
                        f"{phase} failed for {task_labels[index]}: "
                        f"{_root_exception_type_v5(exc)}: {exc}"
                    ) from exc
            while next_index < len(values) and len(pending) < worker_count:
                future = executor.submit(worker, values[next_index])
                pending[future] = next_index
                next_index += 1
    except BaseException:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True)
        raise
    else:
        executor.shutdown(wait=True)
    if any(result is None for result in results):
        raise RTA4LocalExecutionV5Error(
            f"{phase} parallel scheduler lost a task result"
        )
    return tuple(results)


def _prepare_records_for_execution_v5(
    campaign: LoadedCampaignV5,
    records: Sequence[FormalPlanRecordV5],
    requested_worker_count: int,
) -> tuple[tuple[PreparedRecordResultV5, ...], Mapping[str, Any]]:
    """Prepare only invocation targets, with spawn concurrency when useful."""

    if type(requested_worker_count) is not int or requested_worker_count < 1:
        raise RTA4LocalExecutionV5Error(
            "V5 preparation requested worker count must be positive"
        )
    targets = tuple(records)
    used_worker_count = min(requested_worker_count, len(targets))
    if not targets:
        return (), FrozenMapping({
            "requested_worker_count": requested_worker_count,
            "used_worker_count": 0,
            "distinct_worker_pids": (),
            "wall_seconds": 0.0,
        })
    started = time.monotonic_ns()
    if len(targets) == 1:
        record = targets[0]
        try:
            worker, certificate, context, material_identity = (
                _prepared_record_material(campaign, record)
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise RTA4LocalExecutionV5Error(
                "V5 record preparation failed for "
                f"ordinal={record.ordinal},execution_id={record.execution_id}: "
                f"{_root_exception_type_v5(exc)}: {exc}"
            ) from exc
        results = (PreparedRecordResultV5(
            record.ordinal,
            record.execution_id,
            worker,
            certificate,
            context,
            material_identity,
            os.getpid(),
        ),)
    else:
        labels = tuple(
            f"ordinal={record.ordinal},execution_id={record.execution_id}"
            for record in targets
        )
        results = _run_spawn_tasks_ordered_v5(
            targets,
            labels,
            worker_count=used_worker_count,
            worker=_prepare_record_worker_v5,
            initializer=_initialize_preparation_worker_v5,
            initargs=(campaign,),
            phase="V5 record preparation",
        )
    finished = time.monotonic_ns()
    for record, result in zip(targets, results):
        if (
            result.ordinal != record.ordinal
            or result.execution_id != record.execution_id
        ):
            raise RTA4LocalExecutionV5Error(
                "V5 prepared record order/identity drift"
            )
    return results, FrozenMapping({
        "requested_worker_count": requested_worker_count,
        "used_worker_count": used_worker_count,
        "distinct_worker_pids": tuple(sorted({
            result.worker_pid for result in results
        })),
        "wall_seconds": (finished - started) / 1_000_000_000,
    })


def _exact_piecewise_system_v5(
    base_system_path: Path,
    destination: Path,
    *,
    processors: int,
    scheduler: str,
    initial_energy: Fraction,
    max_energy: Fraction,
    simulation_projection: Mapping[str, Any],
    simulator_compatible_lists: bool = False,
    core3_v7_fixed_workload: str | None = None,
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
    if core3_v7_fixed_workload is not None:
        scheduler_model = energy.get("scheduler_energy_model")
        coefficients = (
            scheduler_model.get("workload_coefficients")
            if isinstance(scheduler_model, dict) else None
        )
        if not isinstance(coefficients, dict):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 V7 system has no mutable workload coefficient map"
            )
        if core3_v7_fixed_workload in coefficients:
            try:
                existing = float(coefficients[core3_v7_fixed_workload])
            except (TypeError, ValueError, OverflowError) as exc:
                raise RTA4LocalExecutionV5Error(
                    "CORE-3 V7 fixed workload coefficient is invalid"
                ) from exc
            if not math.isfinite(existing) or existing != 1.0:
                raise RTA4LocalExecutionV5Error(
                    "CORE-3 V7 fixed workload coefficient conflicts with 1.0"
                )
        coefficients[core3_v7_fixed_workload] = 1.0
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
    core3_v7 = core3_v7_fixed_workload is not None
    path = destination / (
        "system_config_v7.yaml" if core3_v7 else "system_config_v5.yaml"
    )
    atomic_write_json(
        destination / (
            "service_projection_v6.json"
            if core3_v7 else "service_projection_v5.json"
        ),
        simulation_projection,
    )
    if simulator_compatible_lists:
        class SimulatorCompatibleDumper(yaml.SafeDumper):
            def increase_indent(self, flow=False, indentless=False):
                return super().increase_indent(flow, False)

        def scalar_sequence(dumper, values):
            flow = all(
                value is None or isinstance(value, (bool, int, float, str))
                for value in values
            )
            return dumper.represent_sequence(
                "tag:yaml.org,2002:seq", values, flow_style=flow,
            )

        SimulatorCompatibleDumper.add_representer(list, scalar_sequence)
        rendered = yaml.dump(
            document,
            Dumper=SimulatorCompatibleDumper,
            sort_keys=False,
            allow_unicode=True,
        )
    else:
        rendered = yaml.safe_dump(
            document, sort_keys=False, allow_unicode=True,
        )
    atomic_write_text(path, rendered)
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
        self.artifact_storage = production_manifest.get("artifact_storage")
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
        core3_contract = record.material.get("core3_simulation_contract")
        core3_v6 = isinstance(core3_contract, Mapping)
        core3_v7 = (
            core3_v6
            and core3_contract.get("contract_version")
            == CORE3_SIMULATION_CONTRACT_V7
        )
        model_energy_unit_joules = (
            binding.get("model_energy_unit_joules") if core3_v7 else None
        )
        if core3_v7 and (
            model_energy_unit_joules
            != core3_contract.get("model_energy_unit_joules")
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 V7 model-energy scale binding drift"
            )
        projection = (
            core3_simulation_projection_v6(
                exact_service_material_identity=service.beta_material_identity,
                harvest_trace=service.harvest_j_per_tick,
                simulation_tick_ms=simulation_tick_ms,
                model_energy_unit_joules=model_energy_unit_joules,
            )
            if core3_v7
            else core3_simulation_projection_v5(
                exact_service_material_identity=service.beta_material_identity,
                harvest_trace=service.harvest_j_per_tick,
                simulation_tick_ms=simulation_tick_ms,
            )
        )
        if projection["simulation_projection_identity"] != binding.get(
            "simulation_projection_identity"
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 execution projection identity drift"
            )
        release, legacy_window, base_payload = build_formal_release_projection_v2(
            certificate, str(record.material["release_mode"]),
        )
        storage_contract = None
        if core3_v6:
            try:
                storage_contract = (
                    require_normalized_core3_artifact_storage_v1(
                        self.artifact_storage
                    )
                )
            except RTA4Core3ContractV6Error as exc:
                raise RTA4LocalExecutionV5Error(str(exc)) from exc
        if core3_v6:
            release_horizon = int(record.material["release_horizon"])
            dmax = max(int(row["D"]) for row in base_payload)
            observation_horizon = release_horizon + dmax
            if (
                int(record.material.get("dmax", -1)) != dmax
                or int(record.material.get("observation_horizon", -1))
                != observation_horizon
                or any(
                    int(row["arrival_offset"]) >= release_horizon
                    for row in base_payload
                )
            ):
                raise RTA4LocalExecutionV5Error(
                    "CORE-3 effective H_rel/H_obs material drift"
                )
            window = _Core3ObservationWindowV6(
                release_horizon, dmax, observation_horizon,
            )
        else:
            window = legacy_window
        payload = project_core3_shared_energy_payload(
            certificate, base_payload, task_energy,
        )
        if core3_v6 and len(payload) != 10:
            raise RTA4LocalExecutionV5Error(
                "CORE-3 schema-3 execution requires exactly ten tasks"
            )
        physical_task_projection = None
        task_energy_factors = None
        if core3_v7:
            try:
                physical_task_projection = core3_task_physical_projection_v7(
                    base_system_path=self.system,
                    task_energy_material=task_energy,
                    model_energy_unit_joules=model_energy_unit_joules,
                    simulation_tick_ms=simulation_tick_ms,
                )
                physical_task_projection = (
                    require_core3_task_physical_projection_v7(
                        physical_task_projection
                    )
                )
            except RTA4Core3ContractV7Error as exc:
                raise RTA4LocalExecutionV5Error(str(exc)) from exc
            task_energy_factors = {
                row["task_id"]: row[
                    "emitted_task_energy_factor_decimal"
                ]
                for row in physical_task_projection["tasks"]
            }
        run_root = self.output / "bounded_core3_simulations_v5" / record.execution_id
        initial_energy_j = Fraction(record.material[
            "physical_initial_energy_j"
            if core3_v7 else "physical_initial_energy"
        ])
        battery_capacity_j = Fraction(record.material[
            "battery_capacity_j" if core3_v7 else "battery_capacity"
        ])
        system_path = _exact_piecewise_system_v5(
            self.system,
            run_root,
            processors=certificate.processors,
            scheduler=str(record.material["scheduler"]),
            initial_energy=initial_energy_j,
            max_energy=battery_capacity_j,
            simulation_projection=projection,
            simulator_compatible_lists=core3_v6,
            core3_v7_fixed_workload=(
                CORE3_TASK_WORKLOAD_V7 if core3_v7 else None
            ),
        )
        projected_system_sha256 = hashlib.sha256(
            system_path.read_bytes()
        ).hexdigest()
        physical_execution_identity = None
        if core3_v7:
            try:
                physical_execution_identity = (
                    core3_physical_execution_identity_v7(
                        base_execution_identity=record.execution_id,
                        physical_task_projection_identity=(
                            physical_task_projection[
                                "physical_task_projection_identity"
                            ]
                        ),
                        projected_system_sha256=projected_system_sha256,
                    )
                )
            except RTA4Core3ContractV7Error as exc:
                raise RTA4LocalExecutionV5Error(str(exc)) from exc
        taskset_path = run_root / (
            "taskset_v7.yaml" if core3_v7 else "taskset_v5.yaml"
        )
        atomic_write_text(taskset_path, _render_taskset_yaml(
            payload, release_horizon=int(record.material["release_horizon"]),
            task_energy_factors=task_energy_factors,
        ))
        trace_path = run_root / "trace_v5.json"
        command = [
            str(self.simulator), str(system_path), str(taskset_path),
            str(window.observation_horizon), "-t", str(trace_path),
            "--run-id", f"rta4-v5-{record.execution_id[:16]}",
            "--taskset-semantic-hash", certificate.taskset_hash,
            "--semantic-traces",
        ]
        if core3_v6:
            command.extend([
                "--b4-observability-summary",
                "--b4-summary-horizon", str(window.observation_horizon),
                "--b4-observability-contract-version", "2",
            ])
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RTA4LocalExecutionV5Error(
                "CORE-3 simulator timeout"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            raise RTA4LocalExecutionV5Error(
                f"CORE-3 simulator exited {completed.returncode}: "
                f"{detail[:400]}"
            )
        result = parse_simulation_trace(
            trace_path,
            payload,
            expected_taskset_hash=certificate.taskset_hash,
            horizon=window.observation_horizon,
            warmup=0,
            minimum_jobs_per_task=0,
            release_e0=initial_energy_j,
            expected_scheduler=str(record.material["scheduler"]),
            expected_processors=certificate.processors,
            require_core3_observability=core3_v6,
            release_horizon=(
                int(record.material["release_horizon"])
                if core3_v6 else None
            ),
            physical_initial_energy=(
                initial_energy_j
                if core3_v6 else None
            ),
            battery_capacity=(
                battery_capacity_j
                if core3_v6 else None
            ),
            conditional_e0=(
                tuple(str(value) for value in record.material[
                    "projection_e0_model_units"
                    if core3_v7 else "projection_e0"
                ])
                if core3_v6 else ()
            ),
            theorem_alignment_track=(
                core3_v6 and record.material["track"] == "THEOREM_ALIGNED"
            ),
            energy_tolerance_j=float(Fraction(
                core3_contract["energy_tolerance_j"]
            )) if core3_v6 else CORE3_ENERGY_TOLERANCE_J,
            energy_conservation_rule=(
                core3_contract["energy_conservation_rule"]
                if core3_v6 else None
            ),
            model_energy_unit_joules=(
                model_energy_unit_joules if core3_v7 else None
            ),
            expected_task_energy_j_per_tick=(
                {
                    row["task_id"]: Fraction(
                        row["expected_physical_energy_j_per_tick"]
                    )
                    for row in physical_task_projection["tasks"]
                }
                if core3_v7 else None
            ),
            task_energy_factor_provenance=(
                {
                    row["task_id"]: row
                    for row in physical_task_projection["tasks"]
                }
                if core3_v7 else None
            ),
        )
        accepted_statuses = {
            SimulationStatus.PASS_OBSERVED,
            SimulationStatus.DEADLINE_MISS,
        }
        if core3_v6:
            accepted_statuses.add(SimulationStatus.HORIZON_INSUFFICIENT)
        if result.status not in accepted_statuses:
            raise RTA4LocalExecutionV5Error(
                f"CORE-3 simulator result is incomplete: {result.status.value}"
            )
        if core3_v6:
            sidecar_path = run_root / "simulation_job_observations_v6.json"
            sidecar_binding = write_core3_job_observations_v6(
                sidecar_path,
                execution_identity=record.execution_id,
                jobs=result.jobs,
            )
            trace_gzip_path = run_root / storage_contract["trace"]["final_name"]
            sidecar_gzip_path = (
                run_root
                / storage_contract["job_observations"]["final_name"]
            )
            trace_storage = publish_deterministic_gzip_json_v1(
                trace_path, trace_gzip_path, storage_contract,
            )
            sidecar_storage = publish_deterministic_gzip_json_v1(
                sidecar_path, sidecar_gzip_path, storage_contract,
            )
            trace_relative = trace_gzip_path.relative_to(
                self.output
            ).as_posix()
            sidecar_relative = sidecar_gzip_path.relative_to(
                self.output
            ).as_posix()
            trace_artifact = prefixed_artifact_binding_v1(
                "trace", trace_relative, trace_storage,
            )
            sidecar_artifact = prefixed_artifact_binding_v1(
                "job_observations", sidecar_relative, sidecar_storage,
            )
            try:
                verified_trace = load_bound_gzip_json_v1(
                    self.output, artifact_binding_from_row_v1(
                        trace_artifact, "trace"
                    ), reject_unbound_raw=False,
                )
            except RTA4Core3ArtifactV6Error as exc:
                raise RTA4LocalExecutionV5Error(str(exc)) from exc
            if (
                not isinstance(verified_trace, Mapping)
                or verified_trace.get("trace_schema_version")
                != result.trace_schema_version
                or verified_trace.get("simulation_completed") is not True
                or verified_trace.get("simulation_completion_reason")
                != "reached_horizon"
            ):
                raise RTA4LocalExecutionV5Error(
                    "CORE-3 compressed trace semantic verification failed"
                )
            del verified_trace
            try:
                verified_sidecar = load_bound_gzip_json_v1(
                    self.output, artifact_binding_from_row_v1(
                        sidecar_artifact, "job_observations"
                    ), reject_unbound_raw=False,
                )
            except RTA4Core3ArtifactV6Error as exc:
                raise RTA4LocalExecutionV5Error(str(exc)) from exc
            if (
                not isinstance(verified_sidecar, Mapping)
                or verified_sidecar.get("execution_identity")
                != record.execution_id
                or verified_sidecar.get("job_observation_count")
                != len(result.jobs)
                or not isinstance(
                    verified_sidecar.get("job_observations"), list,
                )
                or len(verified_sidecar["job_observations"])
                != len(result.jobs)
            ):
                raise RTA4LocalExecutionV5Error(
                    "CORE-3 compressed sidecar semantic verification failed"
                )
            del verified_sidecar
            summary_fields = {
                key: result.metrics[key]
                for key in (
                    "released_job_count", "completed_job_count",
                    "deadline_miss_job_count", "unfinished_job_count",
                    "unfinished_without_miss_count", "classified_job_count",
                    "conditional_coverage", "minimum_release_energy_j",
                    "maximum_release_energy_j", "mean_release_energy_j",
                    "harvested_energy_j",
                    "offered_energy_j", "credited_energy_j",
                    "clipped_energy_j", "consumed_energy_j",
                    "overflow_energy_j", "overflow_ratio_numerator",
                    "overflow_ratio_denominator", "battery_min_j",
                    "battery_max_j", "battery_final_j",
                    "battery_empty_ticks", "battery_full_ticks",
                    "observed_energy_intervals", "theorem_alignment_valid",
                    "theorem_alignment_failure_reason",
                    "energy_conservation_rule",
                )
            }
            for key in (
                "minimum_release_energy_j", "maximum_release_energy_j",
                "mean_release_energy_j", "harvested_energy_j",
                "offered_energy_j",
                "credited_energy_j", "clipped_energy_j",
                "consumed_energy_j", "overflow_energy_j",
                "overflow_ratio_numerator", "overflow_ratio_denominator",
                "battery_min_j", "battery_max_j", "battery_final_j",
            ):
                summary_fields[key] = _exact_numeric_text_v6(
                    summary_fields[key]
                )
            task_projection_rows = None
            observed_task_energy = None
            if core3_v7:
                observed_task_energy = {
                    task_id: _exact_numeric_text_v6(value)
                    for task_id, value in sorted(
                        result.observed_task_power_j_per_tick.items()
                    )
                }
                validation_by_task = {
                    row["task_id"]: row
                    for row in result.metrics["task_energy_validation"]
                }
                task_projection_rows = []
                for source in physical_task_projection["tasks"]:
                    task_id = source["task_id"]
                    validation = validation_by_task[task_id]
                    task_projection_rows.append({
                        **source,
                        "observed_task_energy_j_per_tick": (
                            observed_task_energy.get(task_id)
                        ),
                        "observed_executed_ticks": validation[
                            "executed_ticks"
                        ],
                        "observed_energy_validated": validation["validated"],
                        "observed_energy_within_tolerance": validation[
                            "within_tolerance"
                        ],
                    })
            material = {
                "result_schema_version": (
                    CORE3_RESULT_SCHEMA_V7
                    if core3_v7 else CORE3_RESULT_SCHEMA_V6
                ),
                "simulation_status": "COMPLETED",
                "observed_status": result.status.value,
                "track": str(record.material["track"]),
                "release_mode": str(record.material["release_mode"]),
                "battery_model": str(record.material["battery_model"]),
                **({
                    "model_energy_unit_joules": model_energy_unit_joules,
                    "battery_capacity_model_units": str(record.material[
                        "battery_capacity_model_units"
                    ]),
                    "battery_capacity_j": str(record.material[
                        "battery_capacity_j"
                    ]),
                    "physical_initial_energy_model_units": str(
                        record.material[
                            "physical_initial_energy_model_units"
                        ]
                    ),
                    "physical_initial_energy_j": str(record.material[
                        "physical_initial_energy_j"
                    ]),
                    "projection_e0_model_units": list(record.material[
                        "projection_e0_model_units"
                    ]),
                    "projection_e0_j": list(record.material[
                        "projection_e0_j"
                    ]),
                    "per_task_energy_projection": task_projection_rows,
                    "observed_task_energy_j_per_tick": observed_task_energy,
                    "physical_task_projection": physical_task_projection,
                    "physical_task_projection_identity": (
                        physical_task_projection[
                            "physical_task_projection_identity"
                        ]
                    ),
                    "system_energy_model": physical_task_projection[
                        "system_energy_model"
                    ],
                    "projected_system_sha256": projected_system_sha256,
                    "physical_execution_identity": (
                        physical_execution_identity
                    ),
                } if core3_v7 else {
                    "battery_capacity": str(
                        record.material["battery_capacity"]
                    ),
                    "physical_initial_energy": str(
                        record.material["physical_initial_energy"]
                    ),
                }),
                "release_horizon": int(record.material["release_horizon"]),
                "dmax": int(record.material["dmax"]),
                "observation_horizon": int(
                    record.material["observation_horizon"]
                ),
                "release_cutoff_enabled": True,
                "observation_horizon_reached": True,
                **summary_fields,
                "job_observations_relative_path": sidecar_relative,
                "job_observations_sha256": sidecar_storage[
                    "uncompressed_sha256"
                ],
                "job_observation_count": sidecar_binding[
                    "job_observation_count"
                ],
                "job_observations_schema_version": sidecar_binding[
                    "job_observations_schema_version"
                ],
                **sidecar_artifact,
                "task_energy_material_identity": (
                    task_energy.task_energy_material_identity
                ),
                "service_material_identity": service.service_material_identity,
                "beta_material_identity": service.beta_material_identity,
                "simulation_tick_ms": simulation_tick_ms,
                "simulation_projection_identity": projection[
                    "simulation_projection_identity"
                ],
                "release_projection_identity": release.release_projection_id,
                "trace_schema_version": result.trace_schema_version,
                "trace_sha256": trace_storage["uncompressed_sha256"],
                **trace_artifact,
            }
            final_result = FrozenMapping({
                **material,
                "simulation_result_identity": domain_hash(
                    CORE3_RESULT_DOMAIN_V7
                    if core3_v7 else CORE3_RESULT_DOMAIN_V6,
                    material,
                ),
            })
            sidecar_path.unlink()
            trace_path.unlink()
            return final_result
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
            "artifact_storage": operation.get("artifact_storage"),
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
            "artifact_storage": operation.get("artifact_storage"),
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
        "artifact_storage": runtime.get("artifact_storage"),
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
        "artifact_storage": runtime.get("artifact_storage"),
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


def _verify_core3_sidecar_document_v6(
    row: Mapping[str, Any], sidecar: Any,
) -> None:
    if not isinstance(sidecar, Mapping):
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V6 sidecar is not an object"
        )
    jobs = sidecar.get("job_observations")
    if (
        sidecar.get("job_observations_schema_version")
        != CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6
        or sidecar.get("execution_identity") != row.get("execution_identity")
        or not isinstance(jobs, list)
        or len(jobs) != row.get("job_observation_count")
        or len(jobs) != sidecar.get("job_observation_count")
    ):
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V6 sidecar schema/identity/count drift"
        )


def _verify_legacy_core3_artifacts_v6(
    root: Path, row: Mapping[str, Any],
) -> None:
    relative = row.get("job_observations_relative_path")
    expected_sha = row.get("job_observations_sha256")
    if type(relative) is not str or type(expected_sha) is not str:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V6 terminal has no bound job sidecar"
        )
    try:
        sidecar = load_legacy_bound_json_v1(root, relative, expected_sha)
        execution = row.get("execution_identity")
        trace_sha = row.get("trace_sha256")
        if type(execution) is not str or type(trace_sha) is not str:
            raise RTA4Core3ArtifactV6Error(
                "legacy CORE-3 trace binding is incomplete"
            )
        trace = load_legacy_bound_json_v1(
            root,
            f"bounded_core3_simulations_v5/{execution}/trace_v5.json",
            trace_sha,
        )
    except RTA4Core3ArtifactV6Error as exc:
        raise RTA4LocalExecutionV5Error(
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if (
        not isinstance(trace, Mapping)
        or trace.get("trace_schema_version") != row.get("trace_schema_version")
        or trace.get("simulation_completed") is not True
        or trace.get("simulation_completion_reason") != "reached_horizon"
        or trace.get("expected_simulation_horizon_ms")
        != row.get("observation_horizon")
        or trace.get("observed_simulation_end_ms")
        != row.get("observation_horizon")
    ):
        raise RTA4LocalExecutionV5Error(
            "legacy CORE-3 trace semantic binding drift"
        )
    _verify_core3_sidecar_document_v6(row, sidecar)


def _verify_core3_artifacts_v6(
    root: Path,
    row: Mapping[str, Any],
    storage: Mapping[str, Any] | None,
) -> None:
    try:
        trace_binding = artifact_binding_from_row_v1(row, "trace")
        sidecar_binding = artifact_binding_from_row_v1(
            row, "job_observations"
        )
    except RTA4Core3ArtifactV6Error as exc:
        raise RTA4LocalExecutionV5Error(
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if storage is not None:
        try:
            contract = require_normalized_core3_artifact_storage_v1(storage)
        except RTA4Core3ContractV6Error as exc:
            raise RTA4LocalExecutionV5Error(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if trace_binding is None or sidecar_binding is None:
            raise RTA4LocalExecutionV5Error(
                "CORE-3 compressed run has a legacy artifact binding"
            )
        expected_identity = contract["storage_contract_identity"]
        if (
            trace_binding["artifact_storage_contract_identity"]
            != expected_identity
            or sidecar_binding["artifact_storage_contract_identity"]
            != expected_identity
            or trace_binding["storage_compresslevel"]
            != contract["compresslevel"]
            or sidecar_binding["storage_compresslevel"]
            != contract["compresslevel"]
            or trace_binding["storage_mtime"] != contract["mtime"]
            or sidecar_binding["storage_mtime"] != contract["mtime"]
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 artifact storage provenance drift"
            )
        try:
            trace = load_bound_gzip_json_v1(root, trace_binding)
            if (
                row.get("trace_sha256")
                != trace_binding["uncompressed_sha256"]
                or not isinstance(trace, Mapping)
                or trace.get("trace_schema_version")
                != row.get("trace_schema_version")
                or trace.get("simulation_completed") is not True
                or trace.get("simulation_completion_reason")
                != "reached_horizon"
                or trace.get("expected_simulation_horizon_ms")
                != row.get("observation_horizon")
                or trace.get("observed_simulation_end_ms")
                != row.get("observation_horizon")
            ):
                raise RTA4LocalExecutionV5Error(
                    "CORE-3 compressed trace binding drift"
                )
            del trace
            sidecar = load_bound_gzip_json_v1(root, sidecar_binding)
        except RTA4Core3ArtifactV6Error as exc:
            raise RTA4LocalExecutionV5Error(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if (
            row.get("job_observations_sha256")
            != sidecar_binding["uncompressed_sha256"]
        ):
            raise RTA4LocalExecutionV5Error(
                "CORE-3 compressed sidecar binding drift"
            )
        _verify_core3_sidecar_document_v6(row, sidecar)
        return
    if trace_binding is not None or sidecar_binding is not None:
        raise RTA4LocalExecutionV5Error(
            "legacy CORE-3 run contains an unbound storage contract"
        )
    _verify_legacy_core3_artifacts_v6(root, row)


def _verify_core3_execution_provenance_v7(
    root: Path, row: Mapping[str, Any],
) -> None:
    try:
        projection = require_core3_task_physical_projection_v7(
            row.get("physical_task_projection")
        )
    except RTA4Core3ContractV7Error as exc:
        raise RTA4LocalExecutionV5Error(str(exc)) from exc
    projection_identity = projection["physical_task_projection_identity"]
    if row.get("physical_task_projection_identity") != projection_identity:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V7 physical task projection binding drift"
        )
    execution = row.get("execution_identity")
    projected_sha = row.get("projected_system_sha256")
    if type(execution) is not str or type(projected_sha) is not str:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V7 physical execution binding is incomplete"
        )
    system_path = (
        root / "bounded_core3_simulations_v5" / execution
        / "system_config_v7.yaml"
    )
    try:
        observed_system_sha = hashlib.sha256(
            system_path.read_bytes()
        ).hexdigest()
    except OSError as exc:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V7 projected system is unavailable"
        ) from exc
    if observed_system_sha != projected_sha:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V7 projected system hash drift"
        )
    try:
        expected_execution = core3_physical_execution_identity_v7(
            base_execution_identity=execution,
            physical_task_projection_identity=projection_identity,
            projected_system_sha256=projected_sha,
        )
    except RTA4Core3ContractV7Error as exc:
        raise RTA4LocalExecutionV5Error(str(exc)) from exc
    if row.get("physical_execution_identity") != expected_execution:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 V7 physical execution identity drift"
        )


def _validate_terminal_file_v5(
    request: TerminalValidationRequestV5,
) -> ValidatedTerminalV5:
    path = request.terminal_path
    row = strict_json_file_v6(path)
    if not isinstance(row, Mapping):
        raise RTA4LocalExecutionV5Error("local terminal is not an object")
    unsigned = dict(row)
    observed = unsigned.pop("result_identity", None)
    execution = row.get("execution_identity")
    result_domain = (
        RTA4_LOCAL_RESULT_DOMAIN_V7
        if row.get("row_schema") == "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V7"
        else RTA4_LOCAL_RESULT_DOMAIN_V6
        if row.get("row_schema") == "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V6"
        else RTA4_LOCAL_RESULT_DOMAIN_V5
    )
    if (
        path.stem != execution
        or request.planned_execution_id != execution
        or observed != domain_hash(result_domain, unsigned)
        or row.get("run_identity") != request.run_identity
        or row.get("execution_class") != "DIRECT_LOCAL_EXECUTION"
    ):
        raise RTA4LocalExecutionV5Error(
            "local terminal identity or classification drift"
        )
    if (
        (
            result_domain == RTA4_LOCAL_RESULT_DOMAIN_V6
            and row.get("result_schema_version") == CORE3_RESULT_SCHEMA_V6
        )
        or (
            result_domain == RTA4_LOCAL_RESULT_DOMAIN_V7
            and row.get("result_schema_version") == CORE3_RESULT_SCHEMA_V7
        )
    ):
        _verify_core3_artifacts_v6(
            request.output_root, row, request.artifact_storage,
        )
        if result_domain == RTA4_LOCAL_RESULT_DOMAIN_V7:
            _verify_core3_execution_provenance_v7(
                request.output_root, row,
            )
    elif result_domain in {
        RTA4_LOCAL_RESULT_DOMAIN_V6, RTA4_LOCAL_RESULT_DOMAIN_V7,
    }:
        raise RTA4LocalExecutionV5Error(
            "CORE-3 terminal/result schema version mismatch"
        )
    return ValidatedTerminalV5(str(execution), dict(row), os.getpid())


def _validate_terminal_files_v5(
    requests: Sequence[TerminalValidationRequestV5],
    requested_worker_count: int,
) -> tuple[tuple[ValidatedTerminalV5, ...], Mapping[str, Any]]:
    if type(requested_worker_count) is not int or requested_worker_count < 1:
        raise RTA4LocalExecutionV5Error(
            "V5 terminal validation requested worker count must be positive"
        )
    tasks = tuple(requests)
    used_worker_count = min(requested_worker_count, len(tasks))
    if not tasks:
        return (), FrozenMapping({
            "requested_worker_count": requested_worker_count,
            "used_worker_count": 0,
            "distinct_worker_pids": (),
            "wall_seconds": 0.0,
        })
    started = time.monotonic_ns()
    if len(tasks) == 1:
        try:
            results = (_validate_terminal_file_v5(tasks[0]),)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise RTA4LocalExecutionV5Error(
                f"V5 terminal validation failed for {tasks[0].terminal_path}: "
                f"{_root_exception_type_v5(exc)}: {exc}"
            ) from exc
    else:
        results = _run_spawn_tasks_ordered_v5(
            tasks,
            tuple(str(request.terminal_path) for request in tasks),
            worker_count=used_worker_count,
            worker=_validate_terminal_file_v5,
            phase="V5 terminal validation",
        )
    finished = time.monotonic_ns()
    return results, FrozenMapping({
        "requested_worker_count": requested_worker_count,
        "used_worker_count": used_worker_count,
        "distinct_worker_pids": tuple(sorted({
            result.worker_pid for result in results
        })),
        "wall_seconds": (finished - started) / 1_000_000_000,
    })


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
        validation_worker_count: int = 1,
    ) -> None:
        if type(validation_worker_count) is not int or validation_worker_count < 1:
            raise RTA4LocalExecutionV5Error(
                "terminal validation worker count must be positive"
            )
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
            **({
                "effective_core3_simulation_material": _plain(
                    record.material["effective_core3_simulation_material"]
                ),
            } if "effective_core3_simulation_material" in record.material else {}),
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
            "execution_class": "DIRECT_LOCAL_EXECUTION",
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
            **({
                "artifact_storage": _plain(
                    campaign.runtime["artifact_storage"]
                ),
            } if "artifact_storage" in campaign.runtime else {}),
            **({
                "core3_simulation_contract": _plain(
                    campaign.normalized_scientific_config[
                        "core3_simulation_contract"
                    ]
                ),
            } if "core3_simulation_contract" in (
                campaign.normalized_scientific_config
            ) else {}),
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
        self.validation_worker_count = validation_worker_count
        self.last_terminal_validation_evidence: Mapping[str, Any] = (
            FrozenMapping({
                "requested_worker_count": validation_worker_count,
                "used_worker_count": 0,
                "distinct_worker_pids": (),
                "wall_seconds": 0.0,
            })
        )

    def completed_rows(self) -> dict[str, Mapping[str, Any]]:
        paths = tuple(sorted(self.terminals.glob("*.json")))
        requests = tuple(
            TerminalValidationRequestV5(
                path,
                self.root,
                path.stem if path.stem in self._plan else None,
                str(self.run_manifest["run_identity"]),
                self.run_manifest.get("artifact_storage"),
            )
            for path in paths
        )
        verified, evidence = _validate_terminal_files_v5(
            requests, self.validation_worker_count,
        )
        self.last_terminal_validation_evidence = evidence
        completed = {
            result.execution_identity: result.row for result in verified
        }
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

    def _verify_core3_artifacts_v6(self, row: Mapping[str, Any]) -> None:
        _verify_core3_artifacts_v6(
            self.root, row, self.run_manifest.get("artifact_storage"),
        )

    @staticmethod
    def _verify_core3_sidecar_document_v6(
        row: Mapping[str, Any], sidecar: Any,
    ) -> None:
        _verify_core3_sidecar_document_v6(row, sidecar)

    def _verify_legacy_core3_artifacts_v6(
        self, row: Mapping[str, Any],
    ) -> None:
        _verify_legacy_core3_artifacts_v6(self.root, row)

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
    core3_v6 = "effective_core3_simulation_material" in record.material
    core3_contract = (
        record.material.get("effective_core3_simulation_material", {})
        .get("core3_simulation_contract", {})
        if core3_v6 else {}
    )
    core3_v7 = (
        isinstance(core3_contract, Mapping)
        and core3_contract.get("contract_version")
        == CORE3_SIMULATION_CONTRACT_V7
    )
    nested_core3 = result.get("result") if core3_v6 else None
    if core3_v6 and not isinstance(nested_core3, Mapping):
        nested_core3 = {}
    payload = {
        "row_schema": (
            "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V7"
            if core3_v7
            else "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V6"
            if core3_v6 else "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V5"
        ),
        "profile": RTA4_FORMAL_PROFILE_V5,
        "execution_class": "DIRECT_LOCAL_EXECUTION",
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
        **(_plain(nested_core3) if core3_v6 else {}),
    }
    return {
        **payload,
        "result_identity": domain_hash(
            RTA4_LOCAL_RESULT_DOMAIN_V7
            if core3_v7
            else RTA4_LOCAL_RESULT_DOMAIN_V6
            if core3_v6 else RTA4_LOCAL_RESULT_DOMAIN_V5,
            payload,
        ),
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
        "horizon_insufficient_count": 0,
        "theorem_alignment_invalid_count": 0,
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
            valid_observed_statuses = set(
                _VALID_SIMULATION_OBSERVED_STATUSES_V5
            )
            if "effective_core3_simulation_material" in record.material:
                valid_observed_statuses.add(
                    SimulationStatus.HORIZON_INSUFFICIENT.value
                )
            if (
                status != "COMPLETED"
                or not isinstance(nested, Mapping)
                or nested.get("simulation_status") != "COMPLETED"
                or nested.get("observed_status")
                not in valid_observed_statuses
            ):
                counts["simulation_failure_count"] += 1
            if (
                isinstance(nested, Mapping)
                and nested.get("observed_status")
                == SimulationStatus.HORIZON_INSUFFICIENT.value
            ):
                counts["horizon_insufficient_count"] += 1
            if (
                isinstance(nested, Mapping)
                and nested.get("track") == "THEOREM_ALIGNED"
                and nested.get("theorem_alignment_valid") is not True
            ):
                counts["theorem_alignment_invalid_count"] += 1
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
        validation_worker_count=int(operation["worker_count"]),
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
    requested_worker_count = int(operation["worker_count"])
    preparation_evidence: Mapping[str, Any] = FrozenMapping({
        "requested_worker_count": requested_worker_count,
        "used_worker_count": 0,
        "distinct_worker_pids": (),
        "wall_seconds": 0.0,
    })
    prepared_by_execution: dict[str, PreparedPhysicalRecordV5] = {}
    if selected_dispatch is None:
        prepared_results, preparation_evidence = (
            _prepare_records_for_execution_v5(
                campaign, target_records, requested_worker_count,
            )
        )
        expected_material_identity = _local_material_identity(campaign)
        for record, prepared in zip(target_records, prepared_results):
            if prepared.material_identity != expected_material_identity:
                raise RTA4LocalExecutionV5Error(
                    "V5 prepared material identity drift"
                )
            prepared_by_execution[record.execution_id] = (
                PreparedPhysicalRecordV5(
                    record,
                    prepared.worker_record,
                    prepared.certificate,
                    prepared.context,
                )
            )
    throttle = CheckpointThrottle(
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
    terminal_validation_evidence = writer.last_terminal_validation_evidence
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
            "horizon_insufficient_count",
            "theorem_alignment_invalid_count",
        )
    )
    invocation_failure_keys = [
            "internal_error_count",
            "terminal_timeout_count",
            "simulation_failure_count",
            "malformed_result_count",
            "theorem_alignment_invalid_count",
    ]
    core3_contract = scientific.get("core3_simulation_contract")
    calibration = (
        isinstance(core3_contract, Mapping)
        and core3_contract.get("campaign_type") == "CALIBRATION"
    )
    if not calibration:
        invocation_failure_keys.append("horizon_insufficient_count")
    invocation_failure_count = sum(
        invocation_counts[key] for key in invocation_failure_keys
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
        "horizon_insufficient_count": total_counts[
            "horizon_insufficient_count"
        ],
        "theorem_alignment_invalid_count": total_counts[
            "theorem_alignment_invalid_count"
        ],
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
        "invocation_horizon_insufficient_count": invocation_counts[
            "horizon_insufficient_count"
        ],
        "invocation_theorem_alignment_invalid_count": invocation_counts[
            "theorem_alignment_invalid_count"
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
        "preparation_requested_worker_count": preparation_evidence[
            "requested_worker_count"
        ],
        "preparation_used_worker_count": preparation_evidence[
            "used_worker_count"
        ],
        "preparation_distinct_worker_pids": list(preparation_evidence[
            "distinct_worker_pids"
        ]),
        "preparation_actual_distinct_worker_count": len(
            preparation_evidence["distinct_worker_pids"]
        ),
        "preparation_wall_seconds": preparation_evidence["wall_seconds"],
        "terminal_validation_requested_worker_count": (
            terminal_validation_evidence["requested_worker_count"]
        ),
        "terminal_validation_used_worker_count": (
            terminal_validation_evidence["used_worker_count"]
        ),
        "terminal_validation_distinct_worker_pids": list(
            terminal_validation_evidence["distinct_worker_pids"]
        ),
        "terminal_validation_actual_distinct_worker_count": len(
            terminal_validation_evidence["distinct_worker_pids"]
        ),
        "terminal_validation_wall_seconds": terminal_validation_evidence[
            "wall_seconds"
        ],
        "checkpoint_path": str(
            operation["output_root"] / RTA4_LOCAL_CHECKPOINT_V5
        ),
        "execution_started": True,
    }


def execute_local_campaign_v5(
    path: Path | str,
    *,
    output_root: Path | str | None = None,
    resume: bool | None = None,
    max_records: int | None = None,
    dispatchers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    campaign = load_rta4_campaign_v5(path)
    return execute_loaded_campaign_v5(
        campaign,
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
    "write_core3_job_observations_v6",
]
