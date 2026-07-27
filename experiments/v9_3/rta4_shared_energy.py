"""Frozen task J/tick and verified solar-service materials for RTA4 V2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

import yaml

import asap_block_rta as legacy_rta

from . import exact_energy
from .constrained_taskset_identity import TasksetIdentityCertificate
from .config import canonical_json, fraction_text
from .release_applicability import RELEASE_HORIZON
from .rta4_formal_config import domain_hash
from .rta4_production_build_manifest import (
    PRODUCTION_BUILD_MANIFEST_SCHEMA,
    PRODUCTION_BUILD_PROFILE,
    load_and_validate_production_build_manifest,
)
from .simulation_engine import SharedSolarInput, construct_shared_solar_input


TASK_ENERGY_MATERIAL_SCHEMA = "ASAP_BLOCK_V9_3_RTA4_TASK_ENERGY_MATERIAL_V2"
TASK_ENERGY_MATERIAL_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_TASK_ENERGY_MATERIAL:v2"
TASK_ENERGY_ENTRY_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_TASK_ENERGY_SOURCE:v2"
SERVICE_MATERIAL_SCHEMA = "ASAP_BLOCK_V9_3_RTA4_VERIFIED_SOLAR_SERVICE_MATERIAL_V2"
SERVICE_MATERIAL_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_VERIFIED_SOLAR_SERVICE_MATERIAL:v2"
SERVICE_SPEC_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SERVICE_SPEC:v2"
SERVICE_CACHE_KEY_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_SERVICE_CACHE_KEY:v2"
CORE3_SHARED_ENERGY_PROJECTION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_CORE3_SHARED_ENERGY_PROJECTION:v2"
)
BETA_CONTRACT_VERSION = "ARBITRARY_WINDOW_BINARY64_MINIMUM_V2"
HORIZON_CONTRACT_VERSION = "ASAP_BLOCK_V9_3_RTA4_SERVICE_HORIZON_V2"


class SharedEnergyMaterialError(ValueError):
    """Raised when V2 shared task/service inputs cannot be frozen exactly."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_material(path: Path | str) -> Dict[str, Any]:
    source = Path(path).resolve(strict=True)
    if not source.is_file():
        raise SharedEnergyMaterialError(f"energy source is not a file: {source}")
    payload = source.read_bytes()
    return {
        "absolute_path": str(source),
        "sha256": _sha256(payload),
        "size_bytes": len(payload),
    }


def _binary64_scale(scale: Fraction) -> float:
    if type(scale) is not Fraction or scale <= 0:
        raise SharedEnergyMaterialError("energy scale must be a positive Fraction")
    value = float(scale)
    if Fraction.from_float(value) != scale:
        raise SharedEnergyMaterialError("energy scale is not exactly binary64")
    return value


@dataclass(frozen=True)
class TaskEnergyEntry:
    task_index: int
    task_id: str
    period: int
    deadline: int
    wcet: int
    workload: str
    base_power_binary64: str
    workload_coefficient_binary64: str
    frequency_ratio_binary64: str
    energy_coefficient_binary64: str
    energy_j_per_tick: Fraction
    energy_j_per_tick_binary64: str
    task_energy_source_identity: str
    unit: str = "J/tick"

    def material(self) -> Dict[str, Any]:
        return {
            "task_index": self.task_index,
            "task_id": self.task_id,
            "period": self.period,
            "deadline": self.deadline,
            "wcet": self.wcet,
            "workload": self.workload,
            "base_power_binary64": self.base_power_binary64,
            "workload_coefficient_binary64": self.workload_coefficient_binary64,
            "frequency_ratio_binary64": self.frequency_ratio_binary64,
            "energy_coefficient_binary64": self.energy_coefficient_binary64,
            "energy_j_per_tick": fraction_text(self.energy_j_per_tick),
            "energy_j_per_tick_binary64": self.energy_j_per_tick_binary64,
            "task_energy_source_identity": self.task_energy_source_identity,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class TaskEnergyMaterial:
    profile_id: str
    production_build_manifest_identity: str
    taskset_id: str
    taskset_store_identity: str
    taskset_canonical_sha256: str
    system_config_sha256: str
    workload_config_sha256: str
    generator_contract_version: str
    entries: tuple[TaskEnergyEntry, ...]
    task_energy_material_identity: str
    schema: str = TASK_ENERGY_MATERIAL_SCHEMA

    def __post_init__(self) -> None:
        if not self.entries:
            raise SharedEnergyMaterialError("task energy material must not be empty")
        if tuple(entry.task_index for entry in self.entries) != tuple(range(len(self.entries))):
            raise SharedEnergyMaterialError("task energy entries are not canonically ordered")
        if any(entry.unit != "J/tick" for entry in self.entries):
            raise SharedEnergyMaterialError("formal task energy unit must be J/tick")

    def material(self, *, include_identity: bool = True) -> Dict[str, Any]:
        value = {
            "schema": self.schema,
            "profile_id": self.profile_id,
            "production_build_manifest_identity": self.production_build_manifest_identity,
            "taskset_id": self.taskset_id,
            "taskset_store_identity": self.taskset_store_identity,
            "taskset_canonical_sha256": self.taskset_canonical_sha256,
            "system_config_sha256": self.system_config_sha256,
            "workload_config_sha256": self.workload_config_sha256,
            "generator_contract_version": self.generator_contract_version,
            "numeric_contract_version": exact_energy.NUMERIC_CONTRACT_VERSION,
            "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
            "energy_demand_unit": "J/tick",
            "entries": [entry.material() for entry in self.entries],
        }
        if include_identity:
            value["task_energy_material_identity"] = self.task_energy_material_identity
        return value

    def energy_for_task(self, task_index: int, task_id: str) -> Fraction:
        try:
            entry = self.entries[task_index]
        except (IndexError, TypeError) as exc:
            raise SharedEnergyMaterialError("task energy index is outside material") from exc
        if entry.task_id != task_id:
            raise SharedEnergyMaterialError("task energy index/identity mismatch")
        return entry.energy_j_per_tick


def construct_task_energy_material(
    certificate: TasksetIdentityCertificate,
    workloads: Sequence[str],
    *,
    system_config_path: Path | str,
    workload_config_path: Path | str | None = None,
    taskset_store_identity: str,
    production_build_manifest_identity: str,
    profile_id: str = PRODUCTION_BUILD_PROFILE,
) -> TaskEnergyMaterial:
    """Derive source identities from bytes and operands; callers supply no identity."""

    if type(certificate) is not TasksetIdentityCertificate:
        raise SharedEnergyMaterialError("task energy requires a taskset certificate")
    certificate.validate()
    if (
        not isinstance(workloads, (tuple, list))
        or len(workloads) != len(certificate.tasks)
        or any(not isinstance(value, str) or not value for value in workloads)
    ):
        raise SharedEnergyMaterialError("one canonical workload is required per task")
    if not isinstance(taskset_store_identity, str) or not taskset_store_identity:
        raise SharedEnergyMaterialError("taskset store identity is required")
    if (
        not isinstance(production_build_manifest_identity, str)
        or len(production_build_manifest_identity) != 64
    ):
        raise SharedEnergyMaterialError("production build manifest identity is invalid")

    system_path = Path(system_config_path).resolve(strict=True)
    workload_path = (
        system_path
        if workload_config_path is None
        else Path(workload_config_path).resolve(strict=True)
    )
    system_source = _file_material(system_path)
    workload_source = _file_material(workload_path)
    try:
        system = legacy_rta.load_system_config(str(system_path))
    except Exception as exc:
        raise SharedEnergyMaterialError("cannot load canonical task energy system") from exc
    taskset_bytes = certificate.canonical_bytes()
    taskset_sha = _sha256(taskset_bytes)
    coefficient = _binary64_scale(certificate.power_variant.scale)
    entries = []
    for index, (task, workload) in enumerate(zip(certificate.tasks, workloads)):
        base_power = system.base_power
        try:
            workload_coefficient = system.workload_coefficient(workload)
            frequency_ratio = system.frequency_ratio()
        except Exception as exc:
            raise SharedEnergyMaterialError(
                f"cannot resolve canonical operands for task {index}"
            ) from exc
        materialized = exact_energy.materialize_task_demand_upper_bound(
            base_power=base_power,
            workload_coefficient=workload_coefficient,
            frequency_ratio=frequency_ratio,
            wcet=task.wcet,
            energy_coefficient=coefficient,
            label=f"RTA4 V2 task {task.task_id} J/tick",
        )
        identity_material = {
            "schema": TASK_ENERGY_MATERIAL_SCHEMA,
            "profile_id": profile_id,
            "taskset_store_identity": taskset_store_identity,
            "taskset_id": certificate.taskset_id,
            "taskset_canonical_sha256": taskset_sha,
            "task_index": index,
            "task_id": task.task_id,
            "period": task.period,
            "deadline": task.relative_deadline,
            "wcet": task.wcet,
            "workload": workload,
            "base_power_binary64": base_power.hex(),
            "workload_coefficient_binary64": workload_coefficient.hex(),
            "frequency_ratio_binary64": frequency_ratio.hex(),
            "energy_coefficient_binary64": coefficient.hex(),
            "operation_order_version": exact_energy.NUMERIC_CONTRACT_VERSION,
            "energy_j_per_tick": fraction_text(materialized.exact_value),
            "energy_j_per_tick_binary64": materialized.binary64_hex,
            "system_config": system_source,
            "workload_config": workload_source,
            "generator_contract_version": certificate.generation_request.generator_version,
            "production_build_manifest_identity": production_build_manifest_identity,
        }
        source_identity = domain_hash(TASK_ENERGY_ENTRY_DOMAIN, identity_material)
        entries.append(TaskEnergyEntry(
            index, task.task_id, task.period, task.relative_deadline, task.wcet,
            workload, base_power.hex(), workload_coefficient.hex(),
            frequency_ratio.hex(), coefficient.hex(), materialized.exact_value,
            materialized.binary64_hex, source_identity,
        ))
    base = {
        "schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "profile_id": profile_id,
        "production_build_manifest_identity": production_build_manifest_identity,
        "taskset_id": certificate.taskset_id,
        "taskset_store_identity": taskset_store_identity,
        "taskset_canonical_sha256": taskset_sha,
        "system_config_sha256": system_source["sha256"],
        "workload_config_sha256": workload_source["sha256"],
        "generator_contract_version": certificate.generation_request.generator_version,
        "numeric_contract_version": exact_energy.NUMERIC_CONTRACT_VERSION,
        "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
        "energy_demand_unit": "J/tick",
        "entries": [entry.material() for entry in entries],
    }
    identity = domain_hash(TASK_ENERGY_MATERIAL_DOMAIN, base)
    return TaskEnergyMaterial(
        profile_id, production_build_manifest_identity, certificate.taskset_id,
        taskset_store_identity, taskset_sha, str(system_source["sha256"]),
        str(workload_source["sha256"]),
        certificate.generation_request.generator_version,
        tuple(entries), identity,
    )


@dataclass(frozen=True)
class ServiceHorizonContract:
    analysis_service_horizon_ticks: int
    simulation_observation_horizon_ticks: int
    service_material_horizon_ticks: int
    warmup_guard_ticks: int = 0
    pilot_observation_horizon_ticks: int = 0
    version: str = HORIZON_CONTRACT_VERSION

    def __post_init__(self) -> None:
        values = (
            self.analysis_service_horizon_ticks,
            self.simulation_observation_horizon_ticks,
            self.service_material_horizon_ticks,
            self.warmup_guard_ticks,
            self.pilot_observation_horizon_ticks,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise SharedEnergyMaterialError("service horizons must be non-negative integers")
        required = max(
            self.analysis_service_horizon_ticks + self.warmup_guard_ticks,
            self.simulation_observation_horizon_ticks,
            self.pilot_observation_horizon_ticks,
        )
        if self.service_material_horizon_ticks < required:
            raise SharedEnergyMaterialError("service material does not cover all horizons")

    def material(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "analysis_service_horizon_ticks": self.analysis_service_horizon_ticks,
            "simulation_observation_horizon_ticks": self.simulation_observation_horizon_ticks,
            "service_material_horizon_ticks": self.service_material_horizon_ticks,
            "warmup_guard_ticks": self.warmup_guard_ticks,
            "pilot_observation_horizon_ticks": self.pilot_observation_horizon_ticks,
            "unit": "ticks",
        }


def derive_service_horizon_contract(
    certificate: TasksetIdentityCertificate,
    *,
    include_core3_simulation: bool = False,
    warmup_guard_ticks: int = 0,
    pilot_observation_horizon_ticks: int = 0,
) -> ServiceHorizonContract:
    certificate.validate()
    maximum_deadline = max(task.relative_deadline for task in certificate.tasks)
    analysis = maximum_deadline - 1
    simulation = (
        RELEASE_HORIZON + maximum_deadline if include_core3_simulation else 0
    )
    service = max(
        analysis + warmup_guard_ticks,
        simulation,
        pilot_observation_horizon_ticks,
    )
    return ServiceHorizonContract(
        analysis, simulation, service, warmup_guard_ticks,
        pilot_observation_horizon_ticks,
    )


@dataclass(frozen=True)
class ServiceMaterialSpec:
    base_system_path: str
    energy_support_path: str
    source_root: str
    solar_scale: Fraction
    horizon: ServiceHorizonContract
    expected_parse_proof_path: str | None = None

    def material(self) -> Dict[str, Any]:
        return {
            "base_system": _file_material(self.base_system_path),
            "energy_support": _file_material(self.energy_support_path),
            "source_root": str(Path(self.source_root).resolve(strict=True)),
            "solar_scale": fraction_text(self.solar_scale),
            "horizon": self.horizon.material(),
            "expected_parse_proof": (
                None
                if self.expected_parse_proof_path is None
                else _file_material(self.expected_parse_proof_path)
            ),
            "beta_contract_version": BETA_CONTRACT_VERSION,
        }

    @property
    def spec_identity(self) -> str:
        return domain_hash(SERVICE_SPEC_DOMAIN, self.material())


@dataclass(frozen=True)
class VerifiedSolarServiceMaterialV2:
    cache_key: str
    semantic_service_source_identity: str
    parser_environment_identity: str
    live_proof_identity: str
    production_build_manifest_identity: str
    system_sha256: str
    support_sha256: str
    solar_csv_sha256: str
    day_of_year: int
    time_of_day_ms: int
    solar_scale: Fraction
    horizon: ServiceHorizonContract
    harvest_j_per_tick: tuple[Fraction, ...]
    beta_prefix_j: tuple[Fraction, ...]
    trace_sha256: str
    beta_material_sha256: str
    service_material_identity: str
    immutable_provenance_json: str
    schema: str = SERVICE_MATERIAL_SCHEMA

    def __post_init__(self) -> None:
        if len(self.harvest_j_per_tick) != self.horizon.service_material_horizon_ticks:
            raise SharedEnergyMaterialError("verified trace/horizon mismatch")
        if len(self.beta_prefix_j) != self.horizon.analysis_service_horizon_ticks + 1:
            raise SharedEnergyMaterialError("verified beta/analysis horizon mismatch")
        if self.beta_prefix_j[0] != 0:
            raise SharedEnergyMaterialError("verified beta must start at zero")

    @property
    def beta_material_identity(self) -> str:
        return self.beta_material_sha256

    def beta(self, length: int) -> Fraction:
        if (
            isinstance(length, bool) or not isinstance(length, int)
            or length < 0 or length > self.horizon.analysis_service_horizon_ticks
        ):
            raise SharedEnergyMaterialError("beta query exceeds analysis material horizon")
        return self.beta_prefix_j[length]


def _support_with_scale(source: Path, scale: Fraction, destination: Path) -> Path:
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SharedEnergyMaterialError("cannot parse service support source") from exc
    if not isinstance(document, Mapping):
        raise SharedEnergyMaterialError("service support source must be a mapping")
    material = deepcopy(dict(document))
    energy = material.get("energy", material)
    if not isinstance(energy, dict) or not isinstance(energy.get("service_curve"), dict):
        raise SharedEnergyMaterialError("service support has no mutable service_curve copy")
    energy["service_curve"]["solar_scale"] = fraction_text(scale)
    destination.write_text(
        yaml.safe_dump(material, sort_keys=True), encoding="utf-8",
    )
    return destination


class ServiceMaterialRegistry:
    """Parent-owned construct-once cache; workers receive only frozen values."""

    def __init__(
        self,
        production_build_manifest: Mapping[str, Any],
        *,
        constructor: Callable[..., SharedSolarInput] = construct_shared_solar_input,
    ) -> None:
        if (
            production_build_manifest.get("manifest_schema")
            != PRODUCTION_BUILD_MANIFEST_SCHEMA
            or not isinstance(production_build_manifest.get("manifest_id"), str)
            or len(production_build_manifest["manifest_id"]) != 64
        ):
            raise SharedEnergyMaterialError("registry requires a validated production manifest")
        try:
            compiler = production_build_manifest["cpp_toolchain"]["compiler"]["path"]
            compiler_sha = production_build_manifest["cpp_toolchain"]["compiler"]["sha256"]
            verifier_sha = production_build_manifest["solar_verifier"]["binary"]["sha256"]
        except Exception as exc:
            raise SharedEnergyMaterialError("production compiler binding is absent") from exc
        self._build_manifest_id = str(production_build_manifest["manifest_id"])
        self._compiler = str(compiler)
        self._compiler_sha = str(compiler_sha)
        self._verifier_sha = str(verifier_sha)
        self._constructor = constructor
        self._materials: Dict[str, VerifiedSolarServiceMaterialV2] = {}
        self._spec_to_cache_key: Dict[str, str] = {}
        self._construction_counts: Dict[str, int] = {}
        self._workspace = Path(tempfile.mkdtemp(prefix="v9_3_rta4_service_registry_"))

    @classmethod
    def from_manifest_path(
        cls, path: Path | str, *,
        constructor: Callable[..., SharedSolarInput] = construct_shared_solar_input,
    ) -> "ServiceMaterialRegistry":
        return cls(
            load_and_validate_production_build_manifest(path),
            constructor=constructor,
        )

    def close(self) -> None:
        if self._workspace.exists():
            shutil.rmtree(self._workspace)

    def __enter__(self) -> "ServiceMaterialRegistry":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def construction_counts(self) -> Mapping[str, int]:
        return dict(self._construction_counts)

    @property
    def cache_statistics(self) -> Dict[str, int]:
        return {
            "unique_service_materials": len(self._materials),
            "construction_count": sum(self._construction_counts.values()),
            "cache_hits": max(0, len(self._spec_to_cache_key) - len(self._materials)),
        }

    def _construct(self, spec: ServiceMaterialSpec) -> VerifiedSolarServiceMaterialV2:
        support = Path(spec.energy_support_path).resolve(strict=True)
        try:
            current = yaml.safe_load(support.read_text(encoding="utf-8"))
            energy = current.get("energy", current)
            configured_scale = Fraction(str(energy["service_curve"].get("solar_scale", "1")))
        except Exception as exc:
            raise SharedEnergyMaterialError("cannot inspect configured solar scale") from exc
        material_support = support
        if configured_scale != spec.solar_scale:
            material_support = _support_with_scale(
                support, spec.solar_scale,
                self._workspace / f"support-{spec.spec_identity}.yaml",
            )
        shared = self._constructor(
            spec.base_system_path,
            material_support,
            horizon=spec.horizon.service_material_horizon_ticks,
            solar_parse_proof=spec.expected_parse_proof_path,
            solar_parse_compiler=self._compiler,
            source_root=spec.source_root,
        )
        provenance = shared.provenance
        binding = provenance["solar_stod_parser_binding"]
        if (
            binding.get("compiler_sha256") != self._compiler_sha
            or binding.get("verifier_binary_sha256") != self._verifier_sha
        ):
            raise SharedEnergyMaterialError(
                "live service proof differs from frozen compiler/verifier"
            )
        trace = tuple(shared.harvest_j_per_tick)
        beta = shared.beta(spec.horizon.analysis_service_horizon_ticks)
        trace_payload = canonical_json([fraction_text(value) for value in trace]).encode("utf-8")
        beta_payload = canonical_json([fraction_text(value) for value in beta]).encode("utf-8")
        trace_sha = _sha256(trace_payload)
        beta_sha = _sha256(beta_payload)
        system_sha = str(provenance["system_template"]["sha256"])
        support_sha = str(provenance["energy_support"]["sha256"])
        solar_sha = str(provenance["solar_csv"]["sha256"])
        cache_material = {
            "semantic_service_source_identity": binding["semantic_service_source_identity"],
            "parser_environment_identity": binding["parser_environment_identity"],
            "production_build_manifest_identity": self._build_manifest_id,
            "day_of_year": provenance["day_of_year"],
            "time_of_day_ms": provenance["time_of_day_ms"],
            "horizon": spec.horizon.material(),
            "solar_scale": fraction_text(spec.solar_scale),
            "beta_contract_version": BETA_CONTRACT_VERSION,
        }
        cache_key = domain_hash(SERVICE_CACHE_KEY_DOMAIN, cache_material)
        identity_material = {
            "schema": SERVICE_MATERIAL_SCHEMA,
            **cache_material,
            "live_proof_identity": binding["live_proof_identity"],
            "system_sha256": system_sha,
            "support_sha256": support_sha,
            "solar_csv_sha256": solar_sha,
            "trace_sha256": trace_sha,
            "beta_material_sha256": beta_sha,
            "service_unit": "J",
            "horizon_unit": "ticks",
        }
        identity = domain_hash(SERVICE_MATERIAL_DOMAIN, identity_material)
        return VerifiedSolarServiceMaterialV2(
            cache_key,
            str(binding["semantic_service_source_identity"]),
            str(binding["parser_environment_identity"]),
            str(binding["live_proof_identity"]), self._build_manifest_id,
            system_sha, support_sha, solar_sha,
            int(provenance["day_of_year"]), int(provenance["time_of_day_ms"]),
            spec.solar_scale, spec.horizon, trace, beta, trace_sha, beta_sha,
            identity, canonical_json(identity_material),
        )

    def prepare(
        self, specs: Iterable[ServiceMaterialSpec],
    ) -> Mapping[str, VerifiedSolarServiceMaterialV2]:
        unique: Dict[str, ServiceMaterialSpec] = {}
        for spec in specs:
            if type(spec) is not ServiceMaterialSpec:
                raise SharedEnergyMaterialError("service registry received an invalid spec")
            unique.setdefault(spec.spec_identity, spec)
        for spec_id in sorted(unique):
            if spec_id in self._spec_to_cache_key:
                continue
            material = self._construct(unique[spec_id])
            existing = self._materials.get(material.cache_key)
            if existing is not None and existing != material:
                raise SharedEnergyMaterialError("service cache key collision")
            if existing is None:
                self._materials[material.cache_key] = material
                self._construction_counts[material.cache_key] = 1
            self._spec_to_cache_key[spec_id] = material.cache_key
        return {
            spec_id: self._materials[cache_key]
            for spec_id, cache_key in self._spec_to_cache_key.items()
        }

    def material_for(self, spec: ServiceMaterialSpec) -> VerifiedSolarServiceMaterialV2:
        spec_id = spec.spec_identity
        if spec_id not in self._spec_to_cache_key:
            self.prepare((spec,))
        return self._materials[self._spec_to_cache_key[spec_id]]


def project_core3_shared_energy_payload(
    certificate: TasksetIdentityCertificate,
    payload: Sequence[Mapping[str, Any]],
    task_energy: TaskEnergyMaterial,
) -> tuple[Mapping[str, Any], ...]:
    """Replace V1 W compatibility data with the canonical workload/J-tick pair."""

    if task_energy.taskset_id != certificate.taskset_id:
        raise SharedEnergyMaterialError("CORE-3 task-energy/taskset mismatch")
    if len(payload) != len(certificate.tasks):
        raise SharedEnergyMaterialError("CORE-3 payload task count mismatch")
    projected = []
    for index, (row, task, entry) in enumerate(
        zip(payload, certificate.tasks, task_energy.entries)
    ):
        if (
            str(row.get("task_id")) != task.task_id
            or row.get("C") != task.wcet
            or row.get("D") != task.relative_deadline
            or row.get("T") != task.period
            or entry.task_index != index
            or entry.task_id != task.task_id
        ):
            raise SharedEnergyMaterialError("CORE-3 payload source identity drift")
        projected.append({
            **dict(row),
            "workload": entry.workload,
            "P": fraction_text(entry.energy_j_per_tick),
            "energy_j_per_tick": fraction_text(entry.energy_j_per_tick),
            "task_energy_source_identity": entry.task_energy_source_identity,
        })
    return tuple(projected)


def core3_shared_energy_projection(
    *,
    task_energy: TaskEnergyMaterial,
    service: VerifiedSolarServiceMaterialV2,
) -> Dict[str, Any]:
    if (
        task_energy.production_build_manifest_identity
        != service.production_build_manifest_identity
    ):
        raise SharedEnergyMaterialError("CORE-3 production build identity drift")
    material = {
        "taskset_id": task_energy.taskset_id,
        "task_energy_material_identity": task_energy.task_energy_material_identity,
        "semantic_service_source_identity": service.semantic_service_source_identity,
        "service_material_identity": service.service_material_identity,
        "beta_material_identity": service.beta_material_identity,
        "production_build_manifest_identity": service.production_build_manifest_identity,
        "solar_scale": fraction_text(service.solar_scale),
        "day_of_year": service.day_of_year,
        "time_of_day_ms": service.time_of_day_ms,
        "horizon": service.horizon.material(),
    }
    return {
        **material,
        "core3_shared_energy_projection_identity": domain_hash(
            CORE3_SHARED_ENERGY_PROJECTION_DOMAIN, material,
        ),
    }


def validate_core3_shared_energy_projection(
    projection: Mapping[str, Any],
    *,
    task_energy: TaskEnergyMaterial,
    service: VerifiedSolarServiceMaterialV2,
) -> None:
    if dict(projection) != core3_shared_energy_projection(
        task_energy=task_energy, service=service,
    ):
        raise SharedEnergyMaterialError("CORE-3 shared energy projection drift")


@dataclass(frozen=True)
class SharedEnergyRunContext:
    production_build_manifest_identity: str
    task_energy_materials: Mapping[str, TaskEnergyMaterial]
    service_materials: Mapping[str, VerifiedSolarServiceMaterialV2]
    record_bindings: Mapping[str, Mapping[str, str]]
    cache_statistics: Mapping[str, int]

    def binding_for(self, record_id: str) -> Mapping[str, str]:
        binding = self.record_bindings.get(record_id)
        if binding is None:
            raise SharedEnergyMaterialError("record has no frozen shared-energy binding")
        return dict(binding)


def initialize_shared_energy_run(
    records: Sequence[Any],
    *,
    taskset_provider: Any,
    production_build_manifest: Mapping[str, Any],
    system_config_path: Path | str,
    energy_support_path: Path | str,
    source_root: Path | str,
    taskset_store_identity: str,
    service_constructor: Callable[..., SharedSolarInput] = construct_shared_solar_input,
) -> SharedEnergyRunContext:
    """Freeze every formal input before a worker pool or record loop exists."""

    if not records:
        raise SharedEnergyMaterialError("shared-energy run has no records")
    build_id = str(production_build_manifest.get("manifest_id", ""))
    if len(build_id) != 64:
        raise SharedEnergyMaterialError("run has no validated production manifest")
    try:
        support_document = yaml.safe_load(
            Path(energy_support_path).read_text(encoding="utf-8")
        )
        support_energy = support_document.get("energy", support_document)
        base_scale = Fraction(str(
            support_energy["service_curve"].get("solar_scale", "1")
        ))
    except Exception as exc:
        raise SharedEnergyMaterialError("cannot load run service support") from exc

    certificates: Dict[str, TasksetIdentityCertificate] = {}
    record_certificates: Dict[str, TasksetIdentityCertificate] = {}
    task_materials: Dict[str, TaskEnergyMaterial] = {}
    requirements: Dict[Fraction, ServiceHorizonContract] = {}
    record_scales: Dict[str, Fraction] = {}
    for record in records:
        certificate = taskset_provider(record)
        if type(certificate) is not TasksetIdentityCertificate:
            raise SharedEnergyMaterialError("provider returned no taskset certificate")
        certificates.setdefault(certificate.taskset_id, certificate)
        record_certificates[record.record_id] = certificate
        if certificate.taskset_id not in task_materials:
            if not hasattr(taskset_provider, "workloads_for"):
                raise SharedEnergyMaterialError("provider exposes no frozen workloads")
            workloads = taskset_provider.workloads_for(record, certificate)
            material = construct_task_energy_material(
                certificate,
                workloads,
                system_config_path=system_config_path,
                workload_config_path=system_config_path,
                taskset_store_identity=taskset_store_identity,
                production_build_manifest_identity=build_id,
            )
            task_materials[certificate.taskset_id] = material
        factor = Fraction(str(record.material.get("service_scale", "1")))
        effective_scale = base_scale * factor
        record_scales[record.record_id] = effective_scale
        horizon = derive_service_horizon_contract(
            certificate,
            include_core3_simulation=record.kind == "simulation",
        )
        previous = requirements.get(effective_scale)
        if previous is None:
            requirements[effective_scale] = horizon
        else:
            requirements[effective_scale] = ServiceHorizonContract(
                max(previous.analysis_service_horizon_ticks,
                    horizon.analysis_service_horizon_ticks),
                max(previous.simulation_observation_horizon_ticks,
                    horizon.simulation_observation_horizon_ticks),
                max(previous.service_material_horizon_ticks,
                    horizon.service_material_horizon_ticks),
                max(previous.warmup_guard_ticks, horizon.warmup_guard_ticks),
                max(previous.pilot_observation_horizon_ticks,
                    horizon.pilot_observation_horizon_ticks),
            )

    specs = {
        scale: ServiceMaterialSpec(
            str(Path(system_config_path).resolve(strict=True)),
            str(Path(energy_support_path).resolve(strict=True)),
            str(Path(source_root).resolve(strict=True)),
            scale,
            horizon,
        )
        for scale, horizon in requirements.items()
    }
    with ServiceMaterialRegistry(
        production_build_manifest, constructor=service_constructor,
    ) as registry:
        registry.prepare(specs.values())
        services = {
            scale: registry.material_for(spec) for scale, spec in specs.items()
        }
        service_materials = {
            value.service_material_identity: value for value in services.values()
        }
        statistics = registry.cache_statistics
    task_by_identity = {
        value.task_energy_material_identity: value for value in task_materials.values()
    }
    bindings = {}
    for record in records:
        certificate = record_certificates[record.record_id]
        task = task_materials[certificate.taskset_id]
        service = services[record_scales[record.record_id]]
        bindings[record.record_id] = {
            "task_energy_material_identity": task.task_energy_material_identity,
            "service_material_identity": service.service_material_identity,
        }
    return SharedEnergyRunContext(
        build_id, task_by_identity, service_materials, bindings, statistics,
    )


__all__ = [
    "BETA_CONTRACT_VERSION", "HORIZON_CONTRACT_VERSION",
    "SERVICE_MATERIAL_SCHEMA", "TASK_ENERGY_MATERIAL_SCHEMA",
    "ServiceHorizonContract", "ServiceMaterialRegistry",
    "ServiceMaterialSpec", "SharedEnergyMaterialError", "SharedEnergyRunContext",
    "TaskEnergyEntry",
    "TaskEnergyMaterial", "VerifiedSolarServiceMaterialV2",
    "construct_task_energy_material", "core3_shared_energy_projection",
    "derive_service_horizon_contract", "initialize_shared_energy_run",
    "project_core3_shared_energy_payload",
    "validate_core3_shared_energy_projection",
]
