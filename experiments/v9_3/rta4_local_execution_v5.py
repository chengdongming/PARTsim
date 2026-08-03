"""Explicit local, non-paper execution for selectable-service RTA V5.

This module is deliberately outside the frozen V3/V4 profiles.  It adapts V5
plan records to the established V2 executor and V3 worker request protocol,
and reuses the V3 checkpoint throttle.  It does not create a formal
authorization and never labels its outputs as paper evidence.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
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
from .rta4_formal_runner_v3 import _CheckpointThrottleV3
from .rta4_formal_workers_v3 import (
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
from .rta4_unified_adapter_v5 import prepare_execution_material_v5


RTA4_LOCAL_RUN_MANIFEST_V5 = "local_run_manifest_v5.json"
RTA4_LOCAL_CHECKPOINT_V5 = "local_checkpoint_v5.json"
RTA4_LOCAL_TERMINALS_V5 = "local_terminal_results_v5"
RTA4_LOCAL_RUN_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RUN:v5"
RTA4_LOCAL_RESULT_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_RESULT:v5"
RTA4_LOCAL_CHECKPOINT_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_CHECKPOINT:v5"
RTA4_LOCAL_BUILD_DOMAIN_V5 = "ASAP_BLOCK:V9.3:RTA4:LOCAL_MATERIAL:v5"


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
    local_material_identity = domain_hash(RTA4_LOCAL_BUILD_DOMAIN_V5, {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "source_closure_identity": source_closure_identity_v5(scientific),
        "classification": "LOCAL_NOT_FOR_PAPER",
    })
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


class LocalResultWriterV5:
    """Atomic terminal/checkpoint namespace for non-paper V5 execution."""

    def __init__(
        self,
        root: Path,
        *,
        campaign: LoadedCampaignV5,
        plan: Mapping[str, Any],
        records: Sequence[FormalPlanRecordV5],
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
        "worker_backend": "V3_WORKER_REQUEST_THREAD_LOCAL_NOT_FOR_PAPER",
        "result": _plain(result),
        "formal_campaign_started": False,
        "paper_result_authorized": False,
        "not_for_paper": True,
    }
    return {
        **payload,
        "result_identity": domain_hash(RTA4_LOCAL_RESULT_DOMAIN_V5, payload),
    }


def execute_loaded_campaign_v5(
    campaign: LoadedCampaignV5,
    *,
    acknowledge_not_for_paper: bool,
    output_root: Path | str | None = None,
    resume: bool | None = None,
    max_records: int | None = None,
    dispatchers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
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
    operation = {
        "output_root": Path(root_value).expanduser().resolve(strict=False),
        "timeout_seconds": int(runtime.get("timeout_seconds", 60)),
        "worker_count": int(runtime.get("worker_count", 1)),
        "max_in_flight": int(runtime.get(
            "max_in_flight", 2 * int(runtime.get("worker_count", 1))
        )),
        "simulator_path": runtime.get("simulator_path"),
    }
    if operation["max_in_flight"] < operation["worker_count"]:
        raise RTA4LocalExecutionV5Error(
            "max_in_flight must cover worker_count"
        )
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
    writer = LocalResultWriterV5(
        operation["output_root"],
        campaign=campaign,
        plan=plan,
        records=all_records,
        resume=use_resume,
    )
    completed = writer.completed_rows()
    if use_resume:
        writer.write_checkpoint(tuple(completed))
    remaining = [
        record for record in all_records
        if record.execution_id not in completed
    ]
    if limit is not None:
        remaining = remaining[:limit]
    selected_dispatch = (
        CORE_EXECUTION_DISPATCH_V5 if dispatchers is None else dispatchers
    )
    if set(selected_dispatch) != set(CORE_EXECUTION_DISPATCH_V5):
        raise RTA4LocalExecutionV5Error(
            "local execution dispatcher set must cover all six cores"
        )
    throttle = _CheckpointThrottleV3(
        writer, completed, every_records=1, every_seconds=30,
    )
    processed = 0

    def persist(record: FormalPlanRecordV5, result: Mapping[str, Any]) -> None:
        nonlocal processed
        row = _terminal_row(writer, record, result)
        writer.write_result(row)
        completed[record.execution_id] = row
        processed += 1
        throttle.terminal_committed()
        throttle.write_if_due()

    futures: dict[Future[Any], FormalPlanRecordV5] = {}
    try:
        with ThreadPoolExecutor(
            max_workers=operation["worker_count"]
        ) as pool:
            pending = list(remaining)
            while pending or futures:
                while pending and len(futures) < operation["max_in_flight"]:
                    record = pending.pop(0)
                    dispatcher = selected_dispatch[record.core]
                    futures[pool.submit(
                        dispatcher, campaign, record, operation,
                    )] = record
                future = next(as_completed(tuple(futures)))
                record = futures.pop(future)
                try:
                    result = future.result()
                    if not isinstance(result, Mapping):
                        raise RTA4LocalExecutionV5Error(
                            "execution dispatcher returned no result mapping"
                        )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    result = {
                        "solver_status": "INTERNAL_ERROR",
                        "taskset_proven": False,
                        "failure_reason": f"{type(exc).__name__}: {exc}"[:500],
                        "failure_closed": True,
                    }
                persist(record, result)
    finally:
        checkpoint = throttle.write_if_due(force=True)
    assert checkpoint is not None
    return {
        "profile": RTA4_FORMAL_PROFILE_V5,
        "campaign_id": scientific["campaign_id"],
        "core": scientific["core"],
        "plan_sha256": plan["plan_sha256"],
        "processed_records": processed,
        "pending_records": len(all_records) - len(completed),
        "complete": len(completed) == len(all_records),
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
