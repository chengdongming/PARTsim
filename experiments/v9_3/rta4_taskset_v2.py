"""W-free canonical task-set identities and provider for formal RTA4 V2.

This module deliberately does not import or subclass the V1 formal task-set
provider.  V2 task-set provenance contains scheduling/workload facts only;
energy demand is materialized separately by :mod:`rta4_shared_energy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from typing import Any, Callable, Dict, Mapping, Sequence

from .constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE,
    FIXED_SLACK_FRACTION_VARIANT,
    IMPLICIT_DEADLINE_MODE,
    UINT64_MAX,
    GenerationRequest,
    deadline_from_slack_fraction,
    derive_deadline_lambda_numerator,
    fixed_slack_deadline,
)
from .rta4_formal_config import canonical_json, domain_hash
RTA4_TASKSET_CERTIFICATE_SCHEMA_V2 = (
    "ASAP_BLOCK_V9_3_RTA4_W_FREE_TASKSET_CERTIFICATE_V2"
)
RTA4_TASKSET_CERTIFICATE_DOMAIN_V2 = (
    "ASAP_BLOCK:V9.3:RTA4_W_FREE_TASKSET_CERTIFICATE:v2"
)
RTA4_TASKSET_SOURCE_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_TASKSET_SOURCE:v2"
RTA4_TASKSET_SKELETON_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_TASKSET_SKELETON:v2"
RTA4_GENERATION_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_PRODUCTION_GENERATION:v2"
RTA4_TASKSET_GENERATOR_CONTRACT_V2 = (
    "ASAP_BLOCK_V9_3_RTA4_W_FREE_GENERATOR_PROJECTION_V2"
)
RTA4_FORMAL_PROFILE_V2 = "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY"


class RTA4TasksetV2Error(ValueError):
    """Raised when W-free task-set provenance is incomplete or drifts."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _require_sha(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RTA4TasksetV2Error(f"{label} must be a lowercase SHA-256")
    return value


def _plain_material(value: Any) -> Any:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _plain_material(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_material(item) for item in value]
    return value


@dataclass(frozen=True)
class FormalTaskV2:
    task_id: str
    priority_rank: int
    wcet: int
    relative_deadline: int
    period: int
    workload: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise RTA4TasksetV2Error("V2 task_id must be non-empty")
        if type(self.priority_rank) is not int or self.priority_rank < 0:
            raise RTA4TasksetV2Error("V2 priority rank must be non-negative")
        if (
            type(self.wcet) is not int
            or type(self.relative_deadline) is not int
            or type(self.period) is not int
            or not (0 < self.wcet <= self.relative_deadline <= self.period)
        ):
            raise RTA4TasksetV2Error("V2 task must satisfy 0 < C <= D <= T")
        if not isinstance(self.workload, str) or not self.workload:
            raise RTA4TasksetV2Error("V2 task workload must be non-empty")

    @property
    def deadline_to_period_ratio(self) -> Fraction:
        return Fraction(self.relative_deadline, self.period)

    @property
    def deadline_slack_fraction(self) -> Fraction:
        if self.period == self.wcet:
            return Fraction(1)
        return Fraction(
            self.relative_deadline - self.wcet,
            self.period - self.wcet,
        )

    def material(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority_rank": self.priority_rank,
            "C": self.wcet,
            "D": self.relative_deadline,
            "T": self.period,
            "workload": self.workload,
        }


@dataclass(frozen=True)
class TasksetIdentityCertificateV2:
    processors: int
    formal_master_seed: int
    generator_seed: int
    generator_contract_version: str
    generation_request_id: str
    taskset_skeleton_id: str
    taskset_source_sha256: str
    deadline_variant: str
    energy_coefficient: Fraction
    tasks: tuple[FormalTaskV2, ...]
    taskset_hash: str
    taskset_id: str
    schema: str = RTA4_TASKSET_CERTIFICATE_SCHEMA_V2

    def __post_init__(self) -> None:
        self.validate()

    @property
    def source_canonical_sha256(self) -> str:
        return self.taskset_source_sha256

    def material(self, *, include_identity: bool = True) -> Dict[str, Any]:
        value: Dict[str, Any] = {
            "schema": self.schema,
            "profile": RTA4_FORMAL_PROFILE_V2,
            "processor_count": self.processors,
            "formal_master_seed": self.formal_master_seed,
            "generator_seed": self.generator_seed,
            "generator_contract_version": self.generator_contract_version,
            "generation_request_id": self.generation_request_id,
            "taskset_skeleton_id": self.taskset_skeleton_id,
            "taskset_source_sha256": self.taskset_source_sha256,
            "deadline_variant": self.deadline_variant,
            "energy_coefficient": _fraction_text(self.energy_coefficient),
            "tasks": [task.material() for task in self.tasks],
            "taskset_hash": self.taskset_hash,
        }
        if include_identity:
            value["taskset_id"] = self.taskset_id
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.material()).encode("utf-8")

    def validate(self) -> None:
        if self.schema != RTA4_TASKSET_CERTIFICATE_SCHEMA_V2:
            raise RTA4TasksetV2Error("V2 taskset certificate schema mismatch")
        if type(self.processors) is not int or self.processors <= 0:
            raise RTA4TasksetV2Error("V2 processor count must be positive")
        if type(self.formal_master_seed) is not int or self.formal_master_seed < 0:
            raise RTA4TasksetV2Error("V2 formal seed must be non-negative")
        if type(self.generator_seed) is not int or self.generator_seed < 0:
            raise RTA4TasksetV2Error("V2 generator seed must be non-negative")
        if not isinstance(self.generator_contract_version, str) or not self.generator_contract_version:
            raise RTA4TasksetV2Error("V2 generator contract is missing")
        for value, label in (
            (self.generation_request_id, "generation request identity"),
            (self.taskset_skeleton_id, "taskset skeleton identity"),
            (self.taskset_source_sha256, "taskset source SHA"),
            (self.taskset_hash, "taskset hash"),
            (self.taskset_id, "taskset identity"),
        ):
            _require_sha(value, label)
        if type(self.energy_coefficient) is not Fraction or self.energy_coefficient <= 0:
            raise RTA4TasksetV2Error("V2 energy coefficient must be a positive Fraction")
        if not self.tasks or any(type(task) is not FormalTaskV2 for task in self.tasks):
            raise RTA4TasksetV2Error("V2 tasks must be a non-empty immutable tuple")
        if tuple(task.priority_rank for task in self.tasks) != tuple(range(len(self.tasks))):
            raise RTA4TasksetV2Error("V2 tasks are not in canonical priority order")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise RTA4TasksetV2Error("V2 task IDs are not unique")
        expected_hash = domain_hash(
            RTA4_TASKSET_SOURCE_DOMAIN_V2,
            {
                "processor_count": self.processors,
                "deadline_variant": self.deadline_variant,
                "tasks": [task.material() for task in self.tasks],
            },
        )
        if self.taskset_hash != expected_hash:
            raise RTA4TasksetV2Error("V2 taskset content hash mismatch")
        expected_id = domain_hash(
            RTA4_TASKSET_CERTIFICATE_DOMAIN_V2,
            self.material(include_identity=False),
        )
        if self.taskset_id != expected_id:
            raise RTA4TasksetV2Error("V2 taskset identity mismatch")
        forbidden = {"actual_power", "P_exact", "watts", "power_w"}
        if forbidden.intersection(self.material()):
            raise RTA4TasksetV2Error("V2 certificate contains a legacy W field")

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "TasksetIdentityCertificateV2":
        def unique(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise RTA4TasksetV2Error(f"duplicate V2 certificate key: {key}")
                result[key] = value
            return result

        try:
            value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RTA4TasksetV2Error("invalid V2 taskset certificate JSON") from exc
        if canonical_json(value).encode("utf-8") != payload:
            raise RTA4TasksetV2Error("V2 certificate JSON is not canonical")
        if type(value) is not dict:
            raise RTA4TasksetV2Error("V2 certificate must be a mapping")
        expected = {
            "schema", "profile", "processor_count", "formal_master_seed",
            "generator_seed", "generator_contract_version",
            "generation_request_id", "taskset_skeleton_id",
            "taskset_source_sha256", "deadline_variant",
            "energy_coefficient", "tasks", "taskset_hash", "taskset_id",
        }
        if set(value) != expected or value.get("profile") != RTA4_FORMAL_PROFILE_V2:
            raise RTA4TasksetV2Error("V2 certificate field/profile mismatch")
        raw_tasks = value["tasks"]
        if not isinstance(raw_tasks, list):
            raise RTA4TasksetV2Error("V2 task list is invalid")
        tasks = tuple(
            FormalTaskV2(
                row["task_id"], row["priority_rank"], row["C"], row["D"],
                row["T"], row["workload"],
            )
            for row in raw_tasks
        )
        return cls(
            value["processor_count"], value["formal_master_seed"],
            value["generator_seed"], value["generator_contract_version"],
            value["generation_request_id"], value["taskset_skeleton_id"],
            value["taskset_source_sha256"], value["deadline_variant"],
            Fraction(value["energy_coefficient"]), tasks,
            value["taskset_hash"], value["taskset_id"], value["schema"],
        )


class ProductionTasksetProviderV2:
    """Generate canonical V2 scheduling/workload facts without reading energy/W."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        generator_factory: Callable[..., Any] | None = None,
    ) -> None:
        from .rta4_formal_config_v2 import validate_rta4_formal_config_v2

        self.config = validate_rta4_formal_config_v2(config)
        self._generator_factory = generator_factory
        self._tasksets: Dict[str, TasksetIdentityCertificateV2] = {}

    def _generation_request(self, record: Any) -> GenerationRequest:
        generation = self.config["generation"]
        material = record.material
        try:
            from global_task_generator import (
                EnergyAwareTaskGenerator, _task_workload_candidate_identity,
            )
        except Exception as exc:
            raise RTA4TasksetV2Error("public task generator is unavailable") from exc
        factory = self._generator_factory or EnergyAwareTaskGenerator
        probe = factory(seed=0, energy_manager=None)
        power_contract = domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_GENERATOR_POWER_CONTRACT:v1",
            _plain_material(probe.scheduler_energy_model),
        )
        workload_identity = _task_workload_candidate_identity(
            probe.task_workload_candidates
        )
        generation_id = domain_hash(RTA4_GENERATION_DOMAIN_V2, {
            "profile": RTA4_FORMAL_PROFILE_V2,
            "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
        })
        return GenerationRequest(
            formal_master_seed=generation["formal_master_seed"],
            formal_generation_id=generation_id,
            processors=int(material.get("processor_count", 4)),
            task_count=int(material.get("task_count", 10)),
            target_normalized_utilization=Fraction(
                str(material.get("normalized_utilization", "1/2"))
            ),
            replicate_index=int(material.get("replicate_index", 0)),
            period_min=generation["period_min"],
            period_max=generation["period_max"],
            utilization_allocation_mode=generation["utilization_allocation_mode"],
            min_task_utilization=Fraction(generation["minimum_task_utilization"]),
            max_task_utilization=Fraction(generation["maximum_task_utilization"]),
            utilization_tolerance=Fraction(generation["utilization_tolerance"]),
            wcet_rounding_mode=generation["wcet_rounding"],
            generator_version=generation["generator_version"],
            power_generation_mode=generation["power_generation_mode"],
            power_generation_contract_identity=power_contract,
            workload_candidate_identity=workload_identity,
            priority_policy=generation["priority_policy"],
            dag_generation_mode="disabled",
            energy_aware_generation=False,
        )

    def __call__(self, record: Any) -> TasksetIdentityCertificateV2:
        slot = str(record.taskset_slot_id)
        if slot in self._tasksets:
            return self._tasksets[slot]
        request = self._generation_request(record)
        try:
            from global_task_generator import EnergyAwareTaskGenerator
            factory = self._generator_factory or EnergyAwareTaskGenerator
            generator = factory(seed=request.generator_seed, energy_manager=None)
            generated, resources, dag, _historical_energy = generator.generate_taskset(
                n=request.task_count,
                total_utilization=float(
                    request.target_normalized_utilization * request.processors
                ),
                min_period=request.period_min,
                max_period=request.period_max,
                num_cpus=request.processors,
                implicit_deadline=True,
                dag_enabled=False,
                energy_aware=False,
                arrival_offset=False,
                min_task_util=float(request.min_task_utilization),
                max_task_util=float(request.max_task_utilization),
                wcet_rounding=request.wcet_rounding_mode,
                actual_utilization_tolerance_total=float(
                    request.utilization_tolerance * request.processors
                ),
            )
        except Exception as exc:
            raise RTA4TasksetV2Error("public task generator failed") from exc
        if resources or dag or len(generated) != request.task_count:
            raise RTA4TasksetV2Error("generator returned a non-sequential V2 taskset")
        ordered = sorted(
            enumerate(generated),
            key=lambda item: (int(item[1]["iat"]), item[0]),
        )
        time_scale = (
            int(record.material["axis_value"])
            if record.material.get("axis") == "integer_time_scale"
            else 1
        )
        source_rows = []
        base_rows = []
        for priority_rank, (source_index, task) in enumerate(ordered):
            try:
                wcet = int(task["execution_time"]) * time_scale
                period = int(task["iat"]) * time_scale
                workload = task["workload"]
            except Exception as exc:
                raise RTA4TasksetV2Error(
                    "generator lacks canonical V2 scheduling/workload fields"
                ) from exc
            if not isinstance(workload, str) or not workload:
                raise RTA4TasksetV2Error("generator returned no canonical workload")
            task_id = f"tau-{source_index:02d}"
            source_rows.append({
                "source_index": source_index,
                "task_id": task_id,
                "priority_rank": priority_rank,
                "execution_time": wcet,
                "period": period,
                "workload": workload,
            })
            base_rows.append((task_id, priority_rank, wcet, period, workload))
        source_bytes = canonical_json({
            "contract": RTA4_TASKSET_GENERATOR_CONTRACT_V2,
            "generator_seed": request.generator_seed,
            "tasks": source_rows,
        }).encode("utf-8")
        source_sha = _sha256(source_bytes)
        skeleton_id = domain_hash(RTA4_TASKSET_SKELETON_DOMAIN_V2, {
            "generation_request_id": request.formal_generation_id,
            "processor_count": request.processors,
            "taskset_source_sha256": source_sha,
            "tasks": source_rows,
        })
        variant = str(record.material.get(
            "deadline_variant", CONSTRAINED_UNIFORM_SLACK_MODE,
        ))
        mode = variant
        fixed: Fraction | None = None
        if variant.startswith(f"{FIXED_SLACK_FRACTION_VARIANT}:"):
            mode = FIXED_SLACK_FRACTION_VARIANT
            fixed = Fraction(variant.split(":", 1)[1])
        if mode == CONSTRAINED_UNIFORM_SLACK_MODE:
            draws = tuple(
                derive_deadline_lambda_numerator(
                    formal_master_seed=request.formal_master_seed,
                    generation_id=request.formal_generation_id,
                    task_id=row[0],
                    priority_rank=row[1],
                )
                for row in base_rows
            )
        else:
            draws = ()
        tasks = []
        for index, (task_id, rank, wcet, period, workload) in enumerate(base_rows):
            if mode == IMPLICIT_DEADLINE_MODE:
                deadline = period
            elif mode == CONSTRAINED_UNIFORM_SLACK_MODE:
                deadline = deadline_from_slack_fraction(
                    wcet, period, draws[index], UINT64_MAX,
                )
            elif mode == FIXED_SLACK_FRACTION_VARIANT and fixed is not None:
                deadline = fixed_slack_deadline(wcet, period, fixed)
            else:
                raise RTA4TasksetV2Error("unknown V2 deadline variant")
            tasks.append(FormalTaskV2(
                task_id, rank, wcet, deadline, period, workload,
            ))
        task_tuple = tuple(tasks)
        taskset_hash = domain_hash(RTA4_TASKSET_SOURCE_DOMAIN_V2, {
            "processor_count": request.processors,
            "deadline_variant": variant,
            "tasks": [task.material() for task in task_tuple],
        })
        coefficient = Fraction(str(record.material.get("power_scale", "1")))
        if coefficient <= 0 or Fraction.from_float(float(coefficient)) != coefficient:
            raise RTA4TasksetV2Error("V2 energy coefficient must be positive binary64")
        base = {
            "schema": RTA4_TASKSET_CERTIFICATE_SCHEMA_V2,
            "profile": RTA4_FORMAL_PROFILE_V2,
            "processor_count": request.processors,
            "formal_master_seed": request.formal_master_seed,
            "generator_seed": request.generator_seed,
            "generator_contract_version": RTA4_TASKSET_GENERATOR_CONTRACT_V2,
            "generation_request_id": request.formal_generation_id,
            "taskset_skeleton_id": skeleton_id,
            "taskset_source_sha256": source_sha,
            "deadline_variant": variant,
            "energy_coefficient": _fraction_text(coefficient),
            "tasks": [task.material() for task in task_tuple],
            "taskset_hash": taskset_hash,
        }
        identity = domain_hash(RTA4_TASKSET_CERTIFICATE_DOMAIN_V2, base)
        certificate = TasksetIdentityCertificateV2(
            request.processors,
            request.formal_master_seed,
            request.generator_seed,
            RTA4_TASKSET_GENERATOR_CONTRACT_V2,
            request.formal_generation_id,
            skeleton_id,
            source_sha,
            variant,
            coefficient,
            task_tuple,
            taskset_hash,
            identity,
        )
        self._tasksets[slot] = certificate
        return certificate

    def workloads_for(
        self, _record: Any, certificate: TasksetIdentityCertificateV2,
    ) -> tuple[str, ...]:
        if type(certificate) is not TasksetIdentityCertificateV2:
            raise RTA4TasksetV2Error("V2 provider requires a V2 certificate")
        return tuple(task.workload for task in certificate.tasks)


__all__ = [
    "FormalTaskV2", "ProductionTasksetProviderV2",
    "RTA4_TASKSET_CERTIFICATE_DOMAIN_V2",
    "RTA4_TASKSET_CERTIFICATE_SCHEMA_V2",
    "RTA4_TASKSET_GENERATOR_CONTRACT_V2", "RTA4TasksetV2Error",
    "TasksetIdentityCertificateV2",
]
