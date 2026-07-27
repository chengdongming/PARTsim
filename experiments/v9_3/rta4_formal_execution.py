"""Authorized, bounded, parent-persisted execution for the RTA4 formal plan."""

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import hashlib
from pathlib import Path
import resource
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

import asap_block_rta_v9_3 as rta_core
import asap_block_rta_v9_3_taskset as rta_adapter

from . import exact_energy
from .constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE, FIXED_SLACK_FRACTION_VARIANT,
    GenerationRequest, SkeletonTask, TasksetIdentityCertificate,
    build_taskset_identity_certificate,
)
from .result_writer import atomic_write_json
from .rta4_formal_authorization import (
    RTA4_TEST_AUTHORIZATION_SCHEMA, validate_authorization_document,
    verify_live_authorization,
)
from .rta4_formal_config import canonical_json, domain_hash
from .rta4_formal_freeze import (
    prepared_scientific_config, validate_prepared_config,
)
from .rta4_formal_environment import (
    load_strict_json, validate_bound_source_file, validate_command_invocation,
)
from .rta4_formal_manifest import (
    FORMAL_AUTHORIZED, RTA4_CONFIG_CHECKPOINT, RTA4_PLAN_MANIFEST,
    SYNTHETIC_AUTHORIZED,
)
from .rta4_formal_pipeline import (
    RTA4FormalRunner, dispatch_formal_rta,
    formal_analysis_identity, mechanism_telemetry_rows,
)
from .rta4_formal_plan import (
    FormalPlanRecord, formal_service_identity, iter_formal_plan,
)
from .rta4_formal_store import RTA4FormalTasksetStore
from .rta4_formal_schema import FORMAL_TABLES, RTA4_FORMAL_SCHEMA_MANIFEST
from .rta4_formal_store import (
    FORMAL_TASKSET_STORE_MANIFEST, formal_taskset_store_identity,
)
from .rta4_shared_energy import (
    TaskEnergyMaterial, VerifiedSolarServiceMaterialV2,
)
from .rta4_formal_validation import (
    RTA4_CHECKPOINT_DOMAIN, RTA4_CHECKPOINT_FILENAME,
    RTA4_CHECKPOINT_VERSION,
    ValidatedFormalClosure, refresh_validated_closure,
    validate_formal_checkpoint, validate_formal_run_closure,
)
from .rta4_formal_writer import (
    FORMAL_AUTHORIZATION_EVIDENCE, FORMAL_RUN_METADATA,
    FORMAL_TERMINAL_DIRECTORY, RTA4FormalResultWriter,
)


RTA4_GENERATION_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_GENERATION:v1"


class RTA4ExecutionError(RuntimeError):
    """Raised before mutation or on fail-closed execution evidence."""


class RTA4ExecutionInterrupted(RuntimeError):
    """Raised by tests after a deterministic committed checkpoint."""


def _fraction_text(value: Fraction) -> str:
    return (
        str(value.numerator)
        if value.denominator == 1
        else f"{value.numerator}/{value.denominator}"
    )


def _plain_material(value: Any) -> Any:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _plain_material(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_material(item) for item in value]
    return value


def _certificate_from_closure(
    closure: ValidatedFormalClosure, taskset_id: str,
) -> TasksetIdentityCertificate:
    rows = {
        row["taskset_id"]: row for row in closure.table("formal_tasksets.csv")
    }
    row = rows.get(taskset_id)
    if row is None:
        raise RTA4ExecutionError("source closure taskset certificate is missing")
    path = closure.root / row["certificate_path"]
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != row["certificate_sha256"]:
        raise RTA4ExecutionError("source taskset certificate byte hash mismatch")
    certificate = TasksetIdentityCertificate.from_canonical_bytes(payload)
    if certificate.taskset_id != taskset_id:
        raise RTA4ExecutionError("source taskset certificate identity mismatch")
    return certificate


class ProductionTasksetProvider:
    """Generate each local slot once or reuse the exact authorized parent DAG."""

    def __init__(
        self, prepared_config: Mapping[str, Any], *,
        source_closures: Mapping[str, ValidatedFormalClosure] | None = None,
        generator_factory: Callable[..., Any] | None = None,
        source_task_workloads: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.prepared = validate_prepared_config(prepared_config)
        self.config = prepared_scientific_config(self.prepared)
        self.core = self.prepared["core"]
        self.sources = dict(source_closures or {})
        self._generator_factory = generator_factory
        self._tasksets: Dict[str, TasksetIdentityCertificate] = {}
        self._skeletons: Dict[str, tuple[GenerationRequest, tuple[SkeletonTask, ...]]] = {}
        self._source_slot_index: Dict[str, Dict[str, str]] = {}
        self._skeleton_workloads: Dict[str, tuple[str, ...]] = {}
        self._task_workloads: Dict[str, tuple[str, ...]] = {
            str(key): tuple(value)
            for key, value in (source_task_workloads or {}).items()
        }
        for source_core, closure in self.sources.items():
            index: Dict[str, str] = {}
            for table in ("formal_rta_requests.csv", "formal_simulation_runs.csv"):
                for row in closure.table(table):
                    slot = row.get("taskset_slot_id")
                    taskset_id = row.get("taskset_id")
                    if not slot or not taskset_id:
                        continue
                    if slot in index and index[slot] != taskset_id:
                        raise RTA4ExecutionError(
                            "source closure maps one slot to multiple tasksets"
                        )
                    index[slot] = taskset_id
            self._source_slot_index[source_core] = index

    def _source_certificate(
        self, record: FormalPlanRecord,
    ) -> TasksetIdentityCertificate | None:
        source_core = {
            "CORE-2": "CORE-1", "CORE-3": "CORE-1", "CORE-5B": "CORE-4",
        }.get(self.core)
        if source_core is None:
            return None
        closure = self.sources.get(source_core)
        if closure is None:
            raise RTA4ExecutionError(
                f"{self.core} cannot fall back without {source_core}"
            )
        taskset_id = self._source_slot_index[source_core].get(
            str(record.taskset_slot_id)
        )
        if taskset_id is None:
            raise RTA4ExecutionError(
                "authorized source closure lacks the requested taskset slot"
            )
        return _certificate_from_closure(closure, taskset_id)

    def _generation_request(self, record: FormalPlanRecord) -> GenerationRequest:
        generation = self.config["generation"]
        material = record.material
        try:
            from global_task_generator import (
                EnergyAwareTaskGenerator, _task_workload_candidate_identity,
            )
        except Exception as exc:
            raise RTA4ExecutionError("public task generator is unavailable") from exc
        factory = self._generator_factory or EnergyAwareTaskGenerator
        probe = factory(seed=0, energy_manager=None)
        power_contract = domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_GENERATOR_POWER_CONTRACT:v1",
            _plain_material(probe.scheduler_energy_model),
        )
        workload_identity = _task_workload_candidate_identity(
            probe.task_workload_candidates
        )
        formal_generation_id = domain_hash(RTA4_GENERATION_DOMAIN, {
            "profile": self.config["experiment_contract"]["profile"],
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
        })
        return GenerationRequest(
            formal_master_seed=generation["formal_master_seed"],
            formal_generation_id=formal_generation_id,
            processors=int(material.get("processor_count", 4)),
            task_count=int(material.get("task_count", 10)),
            target_normalized_utilization=Fraction(
                str(material.get("normalized_utilization", "1/2"))
            ),
            replicate_index=int(material.get("replicate_index", 0)),
            period_min=generation["period_min"],
            period_max=generation["period_max"],
            utilization_allocation_mode=generation[
                "utilization_allocation_mode"
            ],
            min_task_utilization=Fraction(
                generation["minimum_task_utilization"]
            ),
            max_task_utilization=Fraction(
                generation["maximum_task_utilization"]
            ),
            utilization_tolerance=Fraction(
                generation["utilization_tolerance"]
            ),
            wcet_rounding_mode=generation["wcet_rounding"],
            generator_version=generation["generator_version"],
            power_generation_mode=generation["power_generation_mode"],
            power_generation_contract_identity=power_contract,
            workload_candidate_identity=workload_identity,
            priority_policy=generation["priority_policy"],
            dag_generation_mode="disabled",
            energy_aware_generation=False,
        )

    def _generate_skeleton(
        self, record: FormalPlanRecord,
    ) -> tuple[GenerationRequest, tuple[SkeletonTask, ...]]:
        slot = str(record.taskset_skeleton_slot_id)
        if slot in self._skeletons:
            return self._skeletons[slot]
        request = self._generation_request(record)
        try:
            from global_task_generator import EnergyAwareTaskGenerator
            factory = self._generator_factory or EnergyAwareTaskGenerator
            generator = factory(seed=request.generator_seed, energy_manager=None)
            generated, resources, dag, _energy = generator.generate_taskset(
                n=request.task_count,
                total_utilization=float(
                    request.target_normalized_utilization * request.processors
                ),
                min_period=request.period_min, max_period=request.period_max,
                num_cpus=request.processors, implicit_deadline=True,
                dag_enabled=False, energy_aware=False, arrival_offset=False,
                min_task_util=float(request.min_task_utilization),
                max_task_util=float(request.max_task_utilization),
                wcet_rounding=request.wcet_rounding_mode,
                actual_utilization_tolerance_total=float(
                    request.utilization_tolerance * request.processors
                ),
            )
        except Exception as exc:
            raise RTA4ExecutionError("public task generator failed") from exc
        if resources or dag or len(generated) != request.task_count:
            raise RTA4ExecutionError("generator returned a non-sequential taskset")
        ordered = sorted(
            enumerate(generated),
            key=lambda item: (int(item[1]["iat"]), item[0]),
        )
        time_scale = (
            int(record.material["axis_value"])
            if record.material.get("axis") == "integer_time_scale"
            else 1
        )
        skeleton = []
        workloads = []
        for priority_rank, (source_index, task) in enumerate(ordered):
            wcet = int(task["execution_time"]) * time_scale
            period = int(task["iat"]) * time_scale
            duration = Fraction(int(task["execution_time"]), 1000)
            if duration <= 0:
                raise RTA4ExecutionError("generator returned zero task duration")
            power = Fraction.from_float(float(task["energy"])) / duration
            skeleton.append(SkeletonTask(
                f"tau-{source_index:02d}", priority_rank,
                wcet, period, power,
            ))
            workload = task.get("workload")
            if not isinstance(workload, str) or not workload:
                raise RTA4ExecutionError(
                    "generator returned no canonical task workload"
                )
            workloads.append(workload)
        result = (request, tuple(skeleton))
        self._skeletons[slot] = result
        if not hasattr(self, "_skeleton_workloads"):
            # Historical pilot wrappers intentionally bypassed this class's
            # initializer; keep that V1 subclass compatible while V2 records
            # the workload vector.
            self._skeleton_workloads = {}
        self._skeleton_workloads[slot] = tuple(workloads)
        return result

    def __call__(self, record: FormalPlanRecord) -> TasksetIdentityCertificate:
        slot = str(record.taskset_slot_id)
        if slot in self._tasksets:
            return self._tasksets[slot]
        source = self._source_certificate(record)
        if source is not None:
            self._tasksets[slot] = source
            return source
        request, skeleton = self._generate_skeleton(record)
        deadline = str(record.material.get(
            "deadline_variant", CONSTRAINED_UNIFORM_SLACK_MODE,
        ))
        mode = deadline
        fixed = None
        if deadline.startswith(f"{FIXED_SLACK_FRACTION_VARIANT}:"):
            mode = FIXED_SLACK_FRACTION_VARIANT
            fixed = Fraction(deadline.split(":", 1)[1])
        certificate = build_taskset_identity_certificate(
            request, skeleton, deadline_mode=mode,
            fixed_slack_fraction=fixed,
            power_scale=Fraction(str(record.material.get("power_scale", "1"))),
        )
        certificate.validate()
        if not hasattr(self, "_task_workloads"):
            self._task_workloads = {}
        self._task_workloads[certificate.taskset_id] = (
            self._skeleton_workloads[str(record.taskset_skeleton_slot_id)]
        )
        self._tasksets[slot] = certificate
        return certificate

    def workloads_for(
        self, record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate | None = None,
    ) -> tuple[str, ...]:
        """Return generator-frozen workloads; never infer them from power/W."""

        bound = self(record) if certificate is None else certificate
        workloads = getattr(self, "_task_workloads", {}).get(bound.taskset_id)
        if workloads is None or len(workloads) != len(bound.tasks):
            raise RTA4ExecutionError(
                "taskset has no complete frozen workload vector"
            )
        return workloads


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    return int(value if __import__("sys").platform == "darwin" else value * 1024)


def _bounded_execution_batches(
    records: Sequence[FormalPlanRecord], *, max_in_flight: int,
    default_workers: int, config: Mapping[str, Any],
) -> Iterable[tuple[int, Sequence[FormalPlanRecord]]]:
    """Yield deterministic bounded batches, including real CORE-5B conditions."""

    if config["core"] != "CORE-5B":
        for start in range(0, len(records), max_in_flight):
            yield default_workers, records[start:start + max_in_flight]
        return
    worker_order = tuple(config["plan"]["workers"])
    if worker_order != (1, 2, 4, 8):
        raise RTA4ExecutionError("CORE-5B worker condition order drift")
    if any(record.material.get("worker_count") not in worker_order for record in records):
        raise RTA4ExecutionError("CORE-5B record has an unknown worker condition")
    for workers in worker_order:
        condition = tuple(
            record for record in records
            if record.material["worker_count"] == workers
        )
        for start in range(0, len(condition), max_in_flight):
            yield workers, condition[start:start + max_in_flight]


def _adapter_result(
    record: FormalPlanRecord, certificate: TasksetIdentityCertificate,
    config: Mapping[str, Any], timeout_seconds: int,
) -> tuple[Mapping[str, Any], Any]:
    tasks = tuple(
        rta_core.V93Task(
            task.task_id, task.wcet, task.relative_deadline,
            task.period, task.actual_power,
        )
        for task in certificate.tasks
    )
    e0 = Fraction(str(record.material["exact_e0"]))
    scale = Fraction(str(record.material.get("service_scale", "1")))
    beta = lambda length: scale * length
    required = max(task.deadline for task in tasks) - 1
    service_prefix = rta_core.validate_service_curve_v9_3(beta, required)
    adapter_input_id = exact_energy.exact_input_identity(
        task_powers=((task.name, task.power) for task in tasks),
        e0=e0, service_prefix=service_prefix,
    )
    service_id = formal_service_identity(scale)
    analysis_id, _exact_input, _ = formal_analysis_identity(
        certificate=certificate, method=record.material["method"],
        exact_e0=e0, service_identity=service_id,
        numeric_contract_sha256=config["identity"]["numeric_contract_sha256"],
        theory_document_sha256=config["identity"]["theory_document_sha256"],
        timeout_contract=config["execution"]["timeout_contract"],
    )
    context = rta_adapter.DependencyContext(
        taskset_identity=certificate.taskset_id,
        task_definitions_identity=certificate.taskset_hash,
        priority_order_identity=certificate.taskset_skeleton_id,
        e0_canonical_identity=_fraction_text(e0),
        service_curve_identity=service_id,
        power_vector_identity=certificate.power_vector_hash,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=exact_energy.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=(
            rta_adapter.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity=config["experiment_contract"]["profile"],
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=adapter_input_id,
        float_decision_path=False,
    )
    result = dispatch_formal_rta(
        analysis_id=analysis_id, method=record.material["method"],
        analysis_input=rta_adapter.TasksetAnalysisInput(
            tasks=tasks, processors=certificate.processors,
            e0=e0, beta=beta, dependency_context=context,
            timeout_seconds=timeout_seconds,
        ),
    )
    task_rows = []
    for row in result.task_results:
        task_solver_status = row.solver_status.value
        if task_solver_status == "INTERNAL_CONFORMANCE_FAILURE":
            task_solver_status = "INTERNAL_ERROR"
        task_rows.append({
            "task_solver_status": task_solver_status,
            "task_certification_status": row.certification_status.value,
            "candidate_response_time": (
                "NA" if row.candidate_response_time is None
                else row.candidate_response_time
            ),
            "checked_w_count": row.checked_w_count,
            "checked_q_count": row.checked_q_count,
            "checked_h_count": row.checked_h_count,
            "failure_reason": row.failure_reason or "NA",
            "witness": list(row.witness_sequence),
        })
    solver_status = result.solver_status.value
    if solver_status == "INTERNAL_CONFORMANCE_FAILURE":
        solver_status = "INTERNAL_ERROR"
    mapped = {
        "solver_status": solver_status,
        "taskset_certification_status": (
            result.analysis_certification_status.value
        ),
        "taskset_proven": result.taskset_proven,
        "failure_reason": result.failure_reason or "NA",
        "fallback_used": False,
        "task_results": task_rows,
        "mechanism_rows": mechanism_telemetry_rows(result),
    }
    return mapped, result


def _adapter_result_v2(
    record: FormalPlanRecord,
    certificate: TasksetIdentityCertificate,
    config: Mapping[str, Any],
    timeout_seconds: int,
    task_energy: TaskEnergyMaterial,
    service: VerifiedSolarServiceMaterialV2,
) -> tuple[Mapping[str, Any], Any]:
    """V2 adapter: consume only frozen J/tick and verified beta materials."""

    if type(task_energy) is not TaskEnergyMaterial:
        raise RTA4ExecutionError("V2 requires a frozen task-energy material")
    if type(service) is not VerifiedSolarServiceMaterialV2:
        raise RTA4ExecutionError("V2 requires a verified service material")
    if (
        task_energy.taskset_id != certificate.taskset_id
        or task_energy.production_build_manifest_identity
        != service.production_build_manifest_identity
    ):
        raise RTA4ExecutionError("V2 shared-energy material binding mismatch")
    tasks = tuple(
        rta_core.V93Task(
            task.task_id, task.wcet, task.relative_deadline, task.period,
            task_energy.energy_for_task(index, task.task_id),
        )
        for index, task in enumerate(certificate.tasks)
    )
    required = max(task.deadline for task in tasks) - 1
    if required > service.horizon.analysis_service_horizon_ticks:
        raise RTA4ExecutionError("verified beta does not cover the RTA query horizon")
    beta = service.beta
    service_prefix = tuple(beta(length) for length in range(required + 1))
    e0 = Fraction(str(record.material["exact_e0"]))
    adapter_input_id = exact_energy.exact_input_identity(
        task_powers=((task.name, task.power) for task in tasks),
        e0=e0,
        service_prefix=service_prefix,
    )
    numeric_sha = str(config["identity"]["numeric_contract_sha256"])
    theory_sha = str(config["identity"]["theory_document_sha256"])
    analysis_id = domain_hash(
        "ASAP_BLOCK:V9.3:RTA4_FORMAL_ANALYSIS:v2",
        {
            "profile": config["experiment_contract"]["profile"],
            "taskset_id": certificate.taskset_id,
            "task_energy_material_identity": (
                task_energy.task_energy_material_identity
            ),
            "service_material_identity": service.service_material_identity,
            "beta_material_identity": service.beta_material_identity,
            "method": record.material["method"],
            "exact_e0": _fraction_text(e0),
            "numeric_contract_sha256": numeric_sha,
            "theory_document_sha256": theory_sha,
            "timeout_contract": config["execution"]["timeout_contract"],
            "exact_input_identity": adapter_input_id,
            "production_build_manifest_identity": (
                service.production_build_manifest_identity
            ),
        },
    )
    context = rta_adapter.DependencyContext(
        taskset_identity=certificate.taskset_id,
        task_definitions_identity=task_energy.task_energy_material_identity,
        priority_order_identity=certificate.taskset_skeleton_id,
        e0_canonical_identity=_fraction_text(e0),
        service_curve_identity=service.service_material_identity,
        power_vector_identity=task_energy.task_energy_material_identity,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=theory_sha,
        fixed_carry_in_interface_sha256=(
            rta_adapter.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity=config["experiment_contract"]["profile"],
        numeric_contract_sha256=numeric_sha,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=adapter_input_id,
        float_decision_path=False,
    )
    result = dispatch_formal_rta(
        analysis_id=analysis_id,
        method=record.material["method"],
        analysis_input=rta_adapter.TasksetAnalysisInput(
            tasks=tasks,
            processors=certificate.processors,
            e0=e0,
            beta=beta,
            dependency_context=context,
            timeout_seconds=timeout_seconds,
        ),
    )
    task_rows = []
    for row in result.task_results:
        solver = row.solver_status.value
        if solver == "INTERNAL_CONFORMANCE_FAILURE":
            solver = "INTERNAL_ERROR"
        task_rows.append({
            "task_solver_status": solver,
            "task_certification_status": row.certification_status.value,
            "candidate_response_time": (
                "NA" if row.candidate_response_time is None
                else row.candidate_response_time
            ),
            "checked_w_count": row.checked_w_count,
            "checked_q_count": row.checked_q_count,
            "checked_h_count": row.checked_h_count,
            "failure_reason": row.failure_reason or "NA",
            "witness": list(row.witness_sequence),
        })
    solver_status = result.solver_status.value
    if solver_status == "INTERNAL_CONFORMANCE_FAILURE":
        solver_status = "INTERNAL_ERROR"
    return {
        "solver_status": solver_status,
        "taskset_certification_status": result.analysis_certification_status.value,
        "taskset_proven": result.taskset_proven,
        "failure_reason": result.failure_reason or "NA",
        "fallback_used": False,
        "task_results": task_rows,
        "mechanism_rows": mechanism_telemetry_rows(result),
        "production_build_manifest_identity": (
            service.production_build_manifest_identity
        ),
        "task_energy_material_identity": task_energy.task_energy_material_identity,
        "service_material_identity": service.service_material_identity,
        "beta_material_identity": service.beta_material_identity,
        "analysis_id": analysis_id,
    }, result


class ProductionRTAExecutorV2:
    """Read-only worker adapter over parent-materialized shared inputs."""

    def __init__(
        self,
        prepared_config: Mapping[str, Any],
        *,
        task_energy_materials: Mapping[str, TaskEnergyMaterial],
        service_materials: Mapping[str, VerifiedSolarServiceMaterialV2],
        record_bindings: Mapping[str, Mapping[str, str]],
    ) -> None:
        self.prepared = validate_prepared_config(prepared_config)
        self.config = prepared_scientific_config(self.prepared)
        self.task_energy_materials = dict(task_energy_materials)
        self.service_materials = dict(service_materials)
        self.record_bindings = {
            str(key): dict(value) for key, value in record_bindings.items()
        }

    def __call__(
        self, record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate,
    ) -> Mapping[str, Any]:
        binding = self.record_bindings.get(record.record_id)
        if not isinstance(binding, Mapping) or set(binding) != {
            "task_energy_material_identity", "service_material_identity",
        }:
            raise RTA4ExecutionError("V2 record has no frozen shared-energy binding")
        task_energy = self.task_energy_materials.get(
            str(binding["task_energy_material_identity"])
        )
        service = self.service_materials.get(
            str(binding["service_material_identity"])
        )
        if task_energy is None or service is None:
            raise RTA4ExecutionError("V2 worker received an incomplete material registry")
        timeout = self.prepared["timeout_contract"]["methods"][
            record.material["method"]
        ]["initial_timeout_seconds"]
        mapped, _raw = _adapter_result_v2(
            record, certificate, self.config, timeout, task_energy, service,
        )
        return mapped


class ProductionRTAExecutor:
    """Invoke only the public unified adapter and retain every retry attempt."""

    def __init__(self, prepared_config: Mapping[str, Any]) -> None:
        self.prepared = validate_prepared_config(prepared_config)
        self.config = prepared_scientific_config(self.prepared)

    def __call__(
        self, record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate,
    ) -> Mapping[str, Any]:
        timeout = self.prepared["timeout_contract"]["methods"][
            record.material["method"]
        ]
        attempts = []
        mapped = None
        budgets = (
            timeout["initial_timeout_seconds"],
            timeout["retry_timeout_seconds"],
        )[:timeout["maximum_attempts"]]
        for budget in budgets:
            before_rss = _rss_bytes()
            wall_started = time.perf_counter()
            cpu_started = time.process_time()
            try:
                mapped, raw = _adapter_result(
                    record, certificate, self.config, budget,
                )
                runtime_wall = time.perf_counter() - wall_started
                runtime_cpu = time.process_time() - cpu_started
                peak_rss = max(before_rss, _rss_bytes())
                attempts.append({
                    "solver_status": mapped["solver_status"],
                    "failure_origin": (
                        "UNIFIED_RTA_ADAPTER"
                        if mapped["solver_status"] in {
                            "TIMEOUT", "NUMERIC_ERROR", "INTERNAL_ERROR",
                        } else "NA"
                    ),
                    "runtime_wall_seconds": format(runtime_wall, ".17g"),
                    "runtime_cpu_seconds": format(runtime_cpu, ".17g"),
                    "peak_rss_bytes": peak_rss,
                })
                if peak_rss > self.prepared["operational"][
                    "memory_limit_bytes"
                ]:
                    mapped = self._internal_result(
                        certificate,
                        RTA4ExecutionError(
                            "frozen worker memory budget exceeded"
                        ),
                    )
                    attempts[-1].update({
                        "solver_status": "INTERNAL_ERROR",
                        "failure_origin": "RTA_EXECUTOR_MEMORY_BUDGET",
                    })
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                mapped = self._internal_result(certificate, exc)
                attempts.append({
                    "solver_status": "INTERNAL_ERROR",
                    "failure_origin": "RTA_EXECUTOR_BOUNDARY",
                    "runtime_wall_seconds": format(
                        time.perf_counter() - wall_started, ".17g"
                    ),
                    "runtime_cpu_seconds": format(
                        time.process_time() - cpu_started, ".17g"
                    ),
                    "peak_rss_bytes": max(before_rss, _rss_bytes()),
                })
            if mapped["solver_status"] != "TIMEOUT":
                break
        assert mapped is not None
        return {
            **mapped,
            "attempts": attempts,
            "runtime_wall_seconds": format(
                sum(
                    (Decimal(row["runtime_wall_seconds"]) for row in attempts),
                    Decimal(),
                ),
                "f",
            ),
            "runtime_cpu_seconds": format(
                sum(
                    (Decimal(row["runtime_cpu_seconds"]) for row in attempts),
                    Decimal(),
                ),
                "f",
            ),
            "peak_rss_bytes": max(
                int(row["peak_rss_bytes"]) for row in attempts
            ),
        }

    @staticmethod
    def _internal_result(
        certificate: TasksetIdentityCertificate, exc: Exception,
    ) -> Dict[str, Any]:
        reason = f"{type(exc).__name__}: {exc}"[:500]
        rows = []
        for index, task in enumerate(certificate.tasks):
            rows.append({
                "task_solver_status": (
                    "INTERNAL_ERROR"
                    if index == 0 else "NOT_EVALUATED_AFTER_PREFIX_FAILURE"
                ),
                "task_certification_status": "NOT_CERTIFIED",
                "candidate_response_time": "NA",
                "checked_w_count": 0, "checked_q_count": 0,
                "checked_h_count": 0,
                "failure_reason": reason if index == 0 else "prefix failure",
                "witness": [],
            })
        return {
            "solver_status": "INTERNAL_ERROR",
            "taskset_certification_status": "NOT_CERTIFIED_TASKSET",
            "taskset_proven": False, "failure_reason": reason,
            "fallback_used": False, "task_results": rows,
            "mechanism_rows": (),
        }


class ProductionSimulationExecutor:
    """Invoke the public simulator wrapper and expose complete raw job evidence."""

    def __init__(
        self, prepared_config: Mapping[str, Any], *,
        base_system_path: Path | str,
        energy_config_path: Path | str,
        energy_config: Mapping[str, Any],
        source_manifest: Mapping[str, Any],
    ) -> None:
        self.prepared = validate_prepared_config(prepared_config)
        if self.prepared["core"] != "CORE-3":
            raise RTA4ExecutionError(
                "production simulation executor is CORE-3 only"
            )
        self.base_system_path = Path(base_system_path).resolve(strict=True)
        if not self.base_system_path.is_file():
            raise RTA4ExecutionError("base simulator system must be a file")
        self.energy_config_path = Path(energy_config_path).resolve(strict=True)
        try:
            validate_bound_source_file(source_manifest, self.base_system_path)
            validate_bound_source_file(source_manifest, self.energy_config_path)
        except Exception as exc:
            raise RTA4ExecutionError(
                "simulator support configuration is not source-authorized"
            ) from exc
        if not isinstance(energy_config, Mapping):
            raise RTA4ExecutionError("simulation energy config must be a mapping")
        self.energy_config = dict(energy_config)

    @classmethod
    def execute_bound(
        cls, *, simulator_binary: Path | str,
        simulation_timeout_seconds: int, output_root: Path | str,
        base_system_path: Path | str, energy_config_path: Path | str,
        energy_config: Mapping[str, Any], record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate, projection: Any,
        window: Any, payload: Sequence[Mapping[str, Any]],
        simulation_id: str,
    ) -> Mapping[str, Any]:
        """Reuse the production simulator path for a pre-freeze pilot binding.

        The caller is responsible for validating the pilot-specific source and
        binary manifests before invoking this method.  No formal prepared
        configuration or authorization is synthesized.
        """

        binary = Path(simulator_binary).resolve(strict=True)
        system = Path(base_system_path).resolve(strict=True)
        energy_path = Path(energy_config_path).resolve(strict=True)
        if (
            not binary.is_file() or not system.is_file()
            or not energy_path.is_file()
        ):
            raise RTA4ExecutionError(
                "pilot simulator binding contains a non-file path"
            )
        if (
            type(simulation_timeout_seconds) is not int
            or isinstance(simulation_timeout_seconds, bool)
            or simulation_timeout_seconds < 1
        ):
            raise RTA4ExecutionError(
                "pilot simulator timeout must be a positive integer"
            )
        if not isinstance(energy_config, Mapping):
            raise RTA4ExecutionError(
                "pilot simulator energy config must be a mapping"
            )
        instance = object.__new__(cls)
        instance.prepared = {
            "operational": {
                "simulator_binary": str(binary),
                "simulation_timeout_seconds": simulation_timeout_seconds,
                "output_root": str(Path(output_root).resolve()),
            },
        }
        instance.base_system_path = system
        instance.energy_config_path = energy_path
        instance.energy_config = dict(energy_config)
        return instance(
            record, certificate, projection, window, payload, simulation_id,
        )

    def __call__(
        self, record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate,
        projection: Any,
        window: Any,
        payload: Sequence[Mapping[str, Any]],
        simulation_id: str,
    ) -> Mapping[str, Any]:
        from .simulation_engine import (
            construct_paired_harvest_trace, run_paired_simulation,
        )
        from .simulation_result import SimulationStatus

        energy = dict(self.energy_config)
        energy.update({
            "simulation_initial_battery": record.material[
                "physical_initial_energy"
            ],
            "battery_capacity": record.material["battery_capacity"],
        })
        simulation = {
            "simulator_bin": self.prepared["operational"]["simulator_binary"],
            "horizon": window.observation_horizon,
            "maximum_horizon": window.observation_horizon,
            "horizon_extension_policy": "none",
            "trace_mode": "semantic",
            "timeout_seconds": self.prepared["operational"][
                "simulation_timeout_seconds"
            ],
            "warmup": 0,
            "minimum_jobs_per_task": 0,
            "trace_on_failure": True,
            "retain_trace": True,
        }
        execution = run_paired_simulation(
            simulation_id_value=simulation_id,
            base_system_path=self.base_system_path,
            run_root=Path(self.prepared["operational"]["output_root"]),
            task_payload=payload, taskset_hash=certificate.taskset_hash,
            processors=certificate.processors,
            exact_e0=Fraction(record.material["physical_initial_energy"]),
            energy_config=energy, simulation_config=simulation,
        )
        if execution.result.status not in {
            SimulationStatus.PASS_OBSERVED,
            SimulationStatus.DEADLINE_MISS,
        }:
            raise RTA4ExecutionError(
                "simulator did not produce a complete admissible observation: "
                f"{execution.result.status.value}:{execution.result.reason}"
            )
        if execution.retained_trace_path is None:
            raise RTA4ExecutionError("simulator did not retain its bound trace")
        task_ids = [task.task_id for task in certificate.tasks]
        jobs = []
        for job in execution.result.jobs:
            task_id = str(job.task_id)
            if task_id not in task_ids:
                try:
                    task_id = task_ids[int(task_id)]
                except (ValueError, IndexError) as exc:
                    raise RTA4ExecutionError(
                        "simulator job task identity is not projectable"
                    ) from exc
            jobs.append({
                "task_id": task_id,
                "release_time": job.release,
                "completion_time": job.completion,
                "deadline_missed": job.deadline_miss,
            })
        offered = sum(
            construct_paired_harvest_trace(
                execution.system_config_path, window.observation_horizon,
            ),
            Fraction(),
        )
        return {
            "trace_path": execution.retained_trace_path,
            "simulation_status": "COMPLETED",
            "deadline_miss_count": sum(
                bool(job.deadline_miss) for job in execution.result.jobs
            ),
            "max_observed_response": max(
                (
                    int(job.response_time)
                    for job in execution.result.jobs
                    if job.response_time is not None
                ),
                default=0,
            ),
            "offered_harvest": _fraction_text(offered),
            "required_margin": str(energy.get("required_safety_margin", "0")),
            "job_results": jobs,
        }


@dataclass(frozen=True)
class ExecutionSummary:
    core: str
    execution_class: str
    authorization_id: str
    processed_records: int
    pending_records: int
    complete: bool
    checkpoint_path: Path
    closure: ValidatedFormalClosure | None


def _resume_required_inventory(root: Path) -> None:
    """Reject an incomplete namespace without creating or repairing anything."""

    if not root.is_dir():
        raise RTA4ExecutionError("resume requires an existing output root")
    if not any(root.iterdir()):
        raise RTA4ExecutionError("resume refuses an empty output root")
    required_files = {
        RTA4_FORMAL_SCHEMA_MANIFEST, RTA4_CONFIG_CHECKPOINT,
        RTA4_PLAN_MANIFEST, FORMAL_RUN_METADATA,
        FORMAL_AUTHORIZATION_EVIDENCE, RTA4_CHECKPOINT_FILENAME,
        *FORMAL_TABLES,
    }
    missing = sorted(
        name for name in required_files if not (root / name).is_file()
    )
    if missing:
        raise RTA4ExecutionError(
            f"resume namespace is missing required files: {missing}"
        )
    terminal_root = root / FORMAL_TERMINAL_DIRECTORY
    if not terminal_root.is_dir():
        raise RTA4ExecutionError(
            "resume namespace is missing the terminal directory"
        )


def _preflight_taskset_store(
    root: Path, closure: ValidatedFormalClosure,
) -> None:
    """Validate the existing store and every completed run-local certificate."""

    marker = root / FORMAL_TASKSET_STORE_MANIFEST
    certificates = root / "certificates"
    if not root.is_dir() or not marker.is_file() or not certificates.is_dir():
        raise RTA4ExecutionError(
            "resume requires an existing canonical taskset store"
        )
    try:
        observed = load_strict_json(marker)
    except Exception as exc:
        raise RTA4ExecutionError(
            "cannot read resume taskset store manifest"
        ) from exc
    if (
        not isinstance(observed, Mapping)
        or observed.get("store_identity") != formal_taskset_store_identity()
    ):
        raise RTA4ExecutionError("resume taskset store identity mismatch")
    for row in closure.table("formal_tasksets.csv"):
        store_path = certificates / f"{row['taskset_id']}.json"
        run_path = closure.root / row["certificate_path"]
        if (
            not store_path.is_file()
            or not run_path.is_file()
            or store_path.read_bytes() != run_path.read_bytes()
        ):
            raise RTA4ExecutionError(
                "resume taskset store certificate inventory mismatch"
            )


class AuthorizedRTA4Runner:
    """Preflight all bindings, execute bounded work, and persist in the parent."""

    def __init__(
        self, prepared_config: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        source_closures: Mapping[str, Path | str | ValidatedFormalClosure] | None = None,
        live_argv: Sequence[str] | None = None,
        live_cwd: Path | str | None = None,
    ) -> None:
        self.prepared = validate_prepared_config(prepared_config)
        self.authorization = validate_authorization_document(
            authorization, allow_test=True,
        )
        if (
            self.authorization["prepared_config_id"]
            != self.prepared["prepared_config_id"]
        ):
            raise RTA4ExecutionError("authorization/prepared config mismatch")
        self.config = prepared_scientific_config(self.prepared)
        self.source_inputs = dict(source_closures or {})
        self.live_argv = None if live_argv is None else tuple(live_argv)
        self.live_cwd = Path.cwd() if live_cwd is None else Path(live_cwd)

    @property
    def is_test(self) -> bool:
        return self.authorization["authorization_schema"] == (
            RTA4_TEST_AUTHORIZATION_SCHEMA
        )

    def _preflight(self, operation: str) -> Dict[str, ValidatedFormalClosure]:
        verify_live_authorization(
            self.authorization, allow_test=self.is_test,
        )
        if not self.is_test:
            if self.live_argv is None:
                raise RTA4ExecutionError(
                    "production execution requires its live command invocation"
                )
            try:
                validate_command_invocation(
                    self.authorization["command_manifest"],
                    argv=self.live_argv, cwd=self.live_cwd,
                    operation=operation, core=self.prepared["core"],
                )
            except Exception as exc:
                raise RTA4ExecutionError(
                    "live production command differs from authorization"
                ) from exc
        if self.authorization["core"] != self.prepared["core"]:
            raise RTA4ExecutionError("authorization core mismatch")
        if self.authorization["output_root"] != str(
            Path(self.prepared["operational"]["output_root"]).resolve()
        ):
            raise RTA4ExecutionError("authorized output path drift")
        if self.authorization["taskset_store"] != str(
            Path(self.prepared["operational"]["taskset_store"]).resolve()
        ):
            raise RTA4ExecutionError("authorized taskset store path drift")
        required = set(self.prepared["operational"]["source_closures"])
        if set(self.source_inputs) != required:
            raise RTA4ExecutionError("live source closure DAG mismatch")
        sources = {}
        for core, source in self.source_inputs.items():
            closure = refresh_validated_closure(
                source, require_complete=True,
                allow_test_authorization=self.is_test,
            )
            binding = self.authorization["source_closure_bindings"][core]
            if (
                closure.metadata["core"] != core
                or str(closure.root.resolve()) != binding["absolute_root"]
                or closure.metadata["plan_sha256"] != binding["plan_sha256"]
                or closure.closure_sha256 != binding["closure_sha256"]
                or closure.metadata.get("authorization_id")
                != binding["authorization_id"]
            ):
                raise RTA4ExecutionError("authorized source closure drift")
            sources[core] = closure
        return sources

    @staticmethod
    def _checkpoint(
        writer: RTA4FormalResultWriter, *, authorization_id: str,
        completed_ids: Sequence[str], planned_count: int,
    ) -> Dict[str, Any]:
        material = {
            "checkpoint_version": RTA4_CHECKPOINT_VERSION,
            "authorization_id": authorization_id,
            "plan_sha256": writer.plan_sha256,
            "core": writer.core,
            "execution_class": writer.execution_class,
            "output_root": str(writer.root.resolve()),
            "checkpoint_status": (
                "COMPLETE"
                if len(completed_ids) == planned_count
                else "INCOMPLETE_CHECKPOINT"
            ),
            "planned_count": planned_count,
            "completed_count": len(completed_ids),
            "completed_execution_ids_sha256": hashlib.sha256(
                canonical_json(list(completed_ids)).encode("utf-8")
            ).hexdigest(),
        }
        return {
            **material,
            "checkpoint_id": domain_hash(RTA4_CHECKPOINT_DOMAIN, material),
        }

    def run(
        self, *, resume: bool = False, validate_only: bool = False,
        max_records: int | None = None,
        synthetic_ordinals: Sequence[int] | None = None,
        certificate_provider: Callable[[FormalPlanRecord], TasksetIdentityCertificate] | None = None,
        rta_executor: Callable[[FormalPlanRecord, TasksetIdentityCertificate], Mapping[str, Any]] | None = None,
        simulator_executor: Callable[..., Mapping[str, Any]] | None = None,
        use_processes: bool | None = None,
        interrupt_after: int | None = None,
    ) -> ExecutionSummary:
        if self.is_test:
            if synthetic_ordinals is None:
                raise RTA4ExecutionError(
                    "TEST authorization requires explicit synthetic ordinals"
                )
            execution_class = SYNTHETIC_AUTHORIZED
            ordinals = tuple(synthetic_ordinals)
        else:
            if synthetic_ordinals is not None:
                raise RTA4ExecutionError(
                    "production authorization refuses synthetic ordinals"
                )
            if certificate_provider is not None or rta_executor is not None:
                raise RTA4ExecutionError(
                    "production authorization refuses injected executors"
                )
            if interrupt_after is not None:
                raise RTA4ExecutionError(
                    "production authorization refuses test interruption hooks"
                )
            if (
                self.prepared["core"] == "CORE-3"
                and not validate_only
                and type(simulator_executor) is not ProductionSimulationExecutor
            ):
                raise RTA4ExecutionError(
                    "production CORE-3 requires the bound simulator executor"
                )
            if (
                self.prepared["core"] != "CORE-3"
                and simulator_executor is not None
            ):
                raise RTA4ExecutionError(
                    "only production CORE-3 may receive a simulator executor"
                )
            execution_class = FORMAL_AUTHORIZED
            ordinals = ()
        operation = (
            "validate-only" if validate_only
            else "resume" if resume else "execute"
        )
        sources = self._preflight(operation)
        output = Path(self.prepared["operational"]["output_root"])
        preflight_closure = None
        if resume or validate_only:
            _resume_required_inventory(output)
            try:
                checkpoint_document = load_strict_json(
                    output / RTA4_CHECKPOINT_FILENAME
                )
            except Exception as exc:
                raise RTA4ExecutionError(
                    "cannot read existing formal checkpoint"
                ) from exc
            claimed_complete = (
                isinstance(checkpoint_document, Mapping)
                and checkpoint_document.get("checkpoint_status") == "COMPLETE"
            )
            try:
                preflight_closure = validate_formal_run_closure(
                    output, require_complete=claimed_complete,
                    source_closures=sources,
                    allow_test_authorization=self.is_test,
                )
            except Exception as exc:
                raise RTA4ExecutionError(
                    "resume closure validation failed; terminal inventory "
                    "may be outside the plan"
                ) from exc
            try:
                validate_formal_checkpoint(
                    output, metadata=preflight_closure.metadata,
                    plan_manifest=preflight_closure.plan_manifest,
                    terminal_payloads=preflight_closure.terminal_payloads,
                    require_complete=False,
                )
            except Exception as exc:
                raise RTA4ExecutionError(
                    "resume checkpoint validation failed"
                ) from exc
            if (
                preflight_closure.metadata.get("authorization_id")
                != self.authorization["authorization_id"]
                or preflight_closure.metadata.get("prepared_config_id")
                != self.prepared["prepared_config_id"]
            ):
                raise RTA4ExecutionError(
                    "resume namespace belongs to another authorization"
                )
            _preflight_taskset_store(
                Path(self.prepared["operational"]["taskset_store"]),
                preflight_closure,
            )
        if validate_only:
            assert preflight_closure is not None
            closure = preflight_closure
            planned = len(closure.plan_manifest["plan_records"])
            remaining = planned - len(closure.terminal_payloads)
            return ExecutionSummary(
                closure.metadata["core"], execution_class,
                self.authorization["authorization_id"], 0,
                remaining, remaining == 0,
                output / RTA4_CHECKPOINT_FILENAME, closure,
            )
        if not resume and output.exists() and any(output.iterdir()):
            raise RTA4ExecutionError("non-resume execution refuses a non-empty root")
        writer = RTA4FormalResultWriter(
            output, config=self.config, fixture_ordinals=ordinals,
            execution_class=execution_class,
            authorization_document=self.authorization,
            prepared_config=self.prepared,
            allow_test_authorization=self.is_test,
            require_existing_namespace=resume,
        )
        records = (
            tuple(iter_formal_plan(self.config))
            if execution_class == FORMAL_AUTHORIZED
            else tuple(
                record for ordinal, record in enumerate(iter_formal_plan(self.config))
                if ordinal in set(ordinals)
            )
        )
        if [record.record_id for record in records] != [
            row["plan_record_id"] for row in writer.plan_manifest["plan_records"]
        ]:
            raise RTA4ExecutionError("execution records differ from trusted manifest")
        terminal_ids = {
            path.stem for path in writer.terminals.glob("*.json")
        }
        known_execution_ids = {
            str(record.execution_id) for record in records
        }
        if not terminal_ids.issubset(known_execution_ids):
            raise RTA4ExecutionError(
                "terminal inventory contains an execution outside the plan"
            )
        checkpoint_path = output / RTA4_CHECKPOINT_FILENAME
        if resume:
            assert preflight_closure is not None
            observed_checkpoint = load_strict_json(checkpoint_path)
            expected_checkpoint = self._checkpoint(
                writer,
                authorization_id=self.authorization["authorization_id"],
                completed_ids=sorted(terminal_ids),
                planned_count=len(records),
            )
            if observed_checkpoint != expected_checkpoint:
                raise RTA4ExecutionError(
                    "checkpoint/terminal inventory mismatch"
                )
        pending = [
            record for record in records if record.execution_id not in terminal_ids
        ]
        if max_records is not None:
            if type(max_records) is not int or max_records < 0:
                raise RTA4ExecutionError("max_records must be non-negative")
            pending = pending[:max_records]
        provider = certificate_provider or ProductionTasksetProvider(
            self.prepared, source_closures=sources,
        )
        rta = rta_executor or ProductionRTAExecutor(self.prepared)
        if any(record.kind == "simulation" for record in pending) and (
            simulator_executor is None
        ):
            raise RTA4ExecutionError(
                "CORE-3 requires an explicit real/fake simulation executor"
            )
        store = RTA4FormalTasksetStore(
            self.prepared["operational"]["taskset_store"]
        )
        max_in_flight = self.prepared["operational"]["max_in_flight"]
        pool_size = self.prepared["operational"]["worker_count"]
        if use_processes is None:
            use_processes = not self.is_test
        if type(use_processes) is not bool:
            raise RTA4ExecutionError("use_processes must be boolean or None")
        if not self.is_test and not use_processes:
            raise RTA4ExecutionError(
                "production execution requires isolated process workers"
            )
        pool_type = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        processed = 0
        for pool_size, batch in _bounded_execution_batches(
            pending, max_in_flight=max_in_flight,
            default_workers=pool_size, config=self.config,
        ):
            certificates = []
            for record in batch:
                certificate = provider(record)
                if type(certificate) is not TasksetIdentityCertificate:
                    raise RTA4ExecutionError(
                        "taskset provider did not return a PR-B certificate"
                    )
                RTA4FormalRunner(self.config)._validate_plan_certificate(
                    record, certificate,
                )
                certificates.append(certificate)
            futures: list[Future[Any]] = []
            with pool_type(max_workers=pool_size) as pool:
                for record, certificate in zip(batch, certificates):
                    callback = (
                        simulator_executor
                        if record.kind == "simulation" else rta
                    )
                    if record.kind == "simulation":
                        from .rta4_formal_pipeline import (
                            build_formal_release_projection,
                        )
                        projection, window, payload = (
                            build_formal_release_projection(
                                certificate, record.material["release_mode"],
                            )
                        )
                        from .release_applicability import (
                            TARGET_SCHEDULER,
                            simulation_applicability_identity,
                        )
                        simulation_id = simulation_applicability_identity(
                            taskset_id=certificate.taskset_id,
                            release_projection_id=projection.release_projection_id,
                            scheduler=TARGET_SCHEDULER,
                            service_identity=formal_service_identity(
                                record.material["service_scale"]
                            ),
                            initial_battery=record.material[
                                "physical_initial_energy"
                            ],
                            battery_capacity=record.material[
                                "battery_capacity"
                            ],
                            window=window,
                            applicability_track=record.material[
                                "applicability_track"
                            ],
                        )
                        futures.append(pool.submit(
                            callback, record, certificate, projection,
                            window, payload, simulation_id,
                        ))
                    else:
                        futures.append(pool.submit(callback, record, certificate))
                results = []
                for record, certificate, future in zip(
                    batch, certificates, futures,
                ):
                    try:
                        results.append(future.result())
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:
                        if record.kind == "simulation":
                            results.append(exc)
                        else:
                            results.append(
                                ProductionRTAExecutor._internal_result(
                                    certificate, exc,
                                )
                            )
            for record, certificate, result in zip(
                batch, certificates, results,
            ):
                if isinstance(result, Exception):
                    writer.append_unique(
                        "formal_failures.csv", "failure_id", {
                            "failure_id": domain_hash(
                                "ASAP_BLOCK:V9.3:RTA4_EXECUTION_FAILURE:v1",
                                {
                                    "plan_record_id": record.record_id,
                                    "type": type(result).__name__,
                                    "detail": str(result),
                                },
                            ),
                            "severity": "P1", "stage": "SIMULATION_EXECUTOR",
                            "code": "SIMULATION_INTERNAL_ERROR",
                            "detail": str(result)[:500], "core": writer.core,
                            "analysis_id": "NA", "simulation_id": (
                                record.execution_id
                            ),
                            "taskset_skeleton_id": (
                                certificate.taskset_skeleton_id
                            ),
                            "taskset_id": certificate.taskset_id,
                            "recoverable": True,
                            "created_at_utc": FORMAL_AUTHORIZED,
                        },
                    )
                    continue
                writer.persist_taskset(store, certificate)
                engine = RTA4FormalRunner(self.config)
                if record.kind == "simulation":
                    engine._execute_simulation(
                        writer, record, certificate,
                        lambda *_args, value=result: value,
                    )
                else:
                    engine._execute_rta_request(
                        writer, record, certificate,
                        lambda *_args, value=result: value,
                    )
                processed += 1
                terminal_ids.add(str(record.execution_id))
                checkpoint = self._checkpoint(
                    writer,
                    authorization_id=self.authorization[
                        "authorization_id"
                    ],
                    completed_ids=sorted(terminal_ids),
                    planned_count=len(records),
                )
                atomic_write_json(
                    output / RTA4_CHECKPOINT_FILENAME, checkpoint,
                )
                if interrupt_after is not None and processed >= interrupt_after:
                    checkpoint = self._checkpoint(
                        writer,
                        authorization_id=self.authorization[
                            "authorization_id"
                        ],
                        completed_ids=sorted(terminal_ids),
                        planned_count=len(records),
                    )
                    atomic_write_json(
                        output / RTA4_CHECKPOINT_FILENAME, checkpoint,
                    )
                    raise RTA4ExecutionInterrupted(
                        "deterministic authorized interruption"
                    )
        remaining = len(records) - len(terminal_ids)
        checkpoint = self._checkpoint(
            writer, authorization_id=self.authorization["authorization_id"],
            completed_ids=sorted(terminal_ids),
            planned_count=len(records),
        )
        atomic_write_json(checkpoint_path, checkpoint)
        closure = None
        if remaining == 0:
            engine = RTA4FormalRunner(self.config)
            engine._persist_source_dependencies(writer, sources)
            engine._persist_applicability(writer, sources)
            engine._persist_hard_checks(writer)
            engine._persist_worker_consistency(writer)
            closure = validate_formal_run_closure(
                output, require_complete=True,
                require_authorized_formal=not self.is_test,
                source_closures=sources,
                allow_test_authorization=self.is_test,
            )
        return ExecutionSummary(
            writer.core, execution_class,
            self.authorization["authorization_id"], processed,
            remaining, remaining == 0, checkpoint_path, closure,
        )


__all__ = [
    "AuthorizedRTA4Runner", "ExecutionSummary", "ProductionRTAExecutor",
    "ProductionRTAExecutorV2",
    "ProductionSimulationExecutor", "ProductionTasksetProvider",
    "RTA4_CHECKPOINT_FILENAME",
    "RTA4_CHECKPOINT_VERSION", "RTA4ExecutionError",
    "RTA4ExecutionInterrupted", "_adapter_result_v2",
]
