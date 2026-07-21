"""Persistent canonical tasksets shared by v9.3 CORE-1 and CORE-2."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Mapping, Sequence, Tuple

import yaml

import asap_block_rta as legacy_rta
import asap_block_rta_v9_3 as rta_core

from .cell_model import (
    Cell, derive_seed, expand_cells, generation_dimensions, taskset_id,
)
from .config import (
    ConfigError,
    TASK_WORKLOAD_CONTRACT_VERSION,
    canonical_json,
    domain_hash,
    fraction_text,
    prepare_task_workload_contract as prepare_config_workload_contract,
    task_workload_energy_model,
)
from .ext1b_capacity_contract import (
    capacity_contract_identity,
    capacity_contract_material,
)
from .ext1b_b3_target_trace import (
    B3_V2_STORE_CONTRACT_DOMAIN,
    is_b3_target_trace_v2,
    target_trace_contract_material,
)
from .simulation_engine import (
    SimulationConfigurationError,
    construct_paired_harvest_trace,
    render_system_projection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK_GENERATOR = PROJECT_ROOT / "global_task_generator.py"
FROZEN_TASKSET_SCHEMA = "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V3"
FROZEN_TASKSET_SEMANTIC_DOMAIN = "ASAP_BLOCK:V9.3:TASKSET_SEMANTIC:v3"
PAIRING_MANIFEST_SCHEMA = "ASAP_BLOCK_V9_3_CORE12_PAIRING_MANIFEST_V2"
PAIRING_CONTRACT_DOMAIN = "ASAP_BLOCK:V9.3:CORE12_PAIRING_CONTRACT:v2"
B3_V2_PAIRING_MANIFEST_SCHEMA = (
    "ASAP_BLOCK_V9_3_EXT1B_B3_TARGET_TRACE_PAIRING_MANIFEST_V4"
)


class TasksetStoreError(RuntimeError):
    """Raised when a frozen taskset cannot be created or verified."""


@dataclass(frozen=True)
class StoredTaskset:
    taskset_id: str
    generation_id: str
    taskset_index: int
    seed: int
    semantic_hash: str
    priority_hash: str
    power_hash: str
    target_utilization: Fraction
    actual_utilization: Fraction
    processors: int
    task_count: int
    deadline_mode: str
    tasks: Tuple[rta_core.V93Task, ...]
    task_payload: Tuple[Mapping[str, Any], ...]
    generation_seconds: float
    service_curve_reference: str
    canonical_path: Path

    def generated_row(self) -> Dict[str, Any]:
        ratios = [Fraction(item["D"], item["T"]) for item in self.task_payload]
        return {
            "generation_id": self.generation_id,
            "taskset_id": self.taskset_id,
            "taskset_index": self.taskset_index,
            "generation_seed": self.seed,
            "M": self.processors,
            "task_n": self.task_count,
            "target_total_utilization": fraction_text(self.target_utilization),
            "actual_total_utilization": fraction_text(self.actual_utilization),
            "utilization_error_total": fraction_text(self.actual_utilization - self.target_utilization),
            "deadline_mode": self.deadline_mode,
            "d_over_t_min_actual": fraction_text(min(ratios)),
            "d_over_t_max_actual": fraction_text(max(ratios)),
            "d_over_t_values_json": canonical_json([fraction_text(item) for item in ratios]),
            "taskset_hash": self.semantic_hash,
            "priority_hash": self.priority_hash,
            "power_hash": self.power_hash,
            "service_curve_reference": self.service_curve_reference,
            "generation_seconds": f"{self.generation_seconds:.9f}",
            "canonical_taskset_json": str(self.canonical_path),
            "task_input_json": canonical_json(self.task_payload),
        }


@dataclass(frozen=True)
class ServiceCurveMaterial:
    values: Tuple[Fraction, ...]
    identity: str
    raw_spec: str
    system_path: Path


@dataclass(frozen=True)
class TaskWorkloadContract:
    candidates: Tuple[str, ...]
    candidate_identity: str
    power_model: Tuple[Tuple[str, Fraction], ...]
    power_model_identity: str
    contract_identity: str

    def canonical_material(self) -> Dict[str, Any]:
        return {
            "version": TASK_WORKLOAD_CONTRACT_VERSION,
            "idle_system_state_reserved": True,
            "ordered_candidates": list(self.candidates),
            "candidate_identity": self.candidate_identity,
            "power_model": [
                {"workload": name, "energy_per_tick": fraction_text(energy)}
                for name, energy in self.power_model
            ],
            "power_model_identity": self.power_model_identity,
            "contract_identity": self.contract_identity,
        }

def prepare_task_workload_contract(
    config: Mapping[str, Any], system_path: Path,
) -> TaskWorkloadContract:
    try:
        material = prepare_config_workload_contract(config, system_path)
    except ConfigError as exc:
        raise TasksetStoreError(str(exc)) from exc
    return TaskWorkloadContract(
        tuple(material["ordered_candidates"]),
        str(material["candidate_identity"]),
        tuple(
            (str(row["workload"]), Fraction(str(row["energy_per_tick"])))
            for row in material["power_model"]
        ),
        str(material["power_model_identity"]),
        str(material["contract_identity"]),
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def prepare_service_curve(
    config: Mapping[str, Any], run_root: Path
) -> ServiceCurveMaterial:
    spec = config["energy"]["service_curve"]
    template = PROJECT_ROOT / str(spec["system_template"])
    if not template.is_file():
        raise TasksetStoreError(f"service-curve system template not found: {template}")
    capacity = Fraction(config["energy"]["battery_capacity"])
    if config["core"] == "CORE-3":
        initial = Fraction(config["energy"]["simulation_initial_battery"])
        try:
            rendered = render_system_projection(
                template,
                processors=max(config["platform"]["cores"]),
                initial_battery=initial,
                battery_capacity=capacity,
                service_curve=spec,
            )
        except SimulationConfigurationError as exc:
            raise TasksetStoreError(f"cannot render service system: {exc}") from exc
    else:
        # Keep every non-CORE-3 system projection byte-semantically aligned
        # with the pre-existing shared framework.
        with template.open("r", encoding="utf-8") as handle:
            system_document = yaml.safe_load(handle)
        if spec.get("synthetic_piecewise", False):
            system_document.setdefault("energy_management", {})[
                "use_real_solar_data"
            ] = False
        system_document["cpu_islands"][0]["numcpus"] = max(
            config["platform"]["cores"]
        )
        energy = system_document.setdefault("energy_management", {})
        energy["max_energy"] = float(capacity)
        energy["initial_energy"] = energy["max_energy"]
        rendered = yaml.safe_dump(
            system_document, allow_unicode=True, sort_keys=False
        )
    system_path = run_root / "system_config.yaml"
    _atomic_write(system_path, rendered)
    system = legacy_rta.load_system_config(str(system_path))
    horizon = int(spec["horizon"])
    try:
        trace = construct_paired_harvest_trace(system_path, horizon)
    except SimulationConfigurationError as exc:
        raise TasksetStoreError(f"cannot construct service trace: {exc}") from exc
    curve = legacy_rta.build_energy_service_curve(trace, horizon)
    required = max(config["generation"]["period_max"] - 1, 0)
    if required >= len(curve):
        raise TasksetStoreError("service-curve horizon is shorter than required deadline horizon")
    exact_scale = Fraction(str(spec.get("exact_scale", "1")))
    if exact_scale <= 0:
        raise TasksetStoreError("service-curve exact scale must be positive")
    material_horizon = horizon if "exact_scale" in spec else required
    values = tuple(
        Fraction(str(curve[index])) * exact_scale
        for index in range(material_horizon + 1)
    )
    values = rta_core.validate_service_curve_v9_3(values, material_horizon)
    raw = {
        "id": spec["id"],
        "horizon": horizon,
        "system_template": str(spec["system_template"]),
        "validated_prefix": [fraction_text(item) for item in values],
    }
    if "exact_scale" in spec:
        raw.update({
            "source_template_sha256": hashlib.sha256(
                template.read_bytes()
            ).hexdigest(),
            "exact_scale": fraction_text(exact_scale),
        })
    if "solar_scale" in spec:
        solar_path = Path(system.solar_data_file)
        if not solar_path.is_absolute():
            solar_path = Path(legacy_rta._resolve_solar_path(system))
        raw.update({
            "source_template_sha256": hashlib.sha256(
                template.read_bytes()
            ).hexdigest(),
            "solar_data_sha256": hashlib.sha256(
                solar_path.read_bytes()
            ).hexdigest(),
            "solar_scale": fraction_text(Fraction(str(spec["solar_scale"]))),
            "effective_pv_area_m2": fraction_text(
                Fraction(str(system.pv_area_m2))
            ),
        })
    identity = domain_hash("ASAP_BLOCK:V9.3:SERVICE_CURVE:v1", raw)
    return ServiceCurveMaterial(values, identity, canonical_json(raw), system_path)


def _workload(raw_task: Mapping[str, Any]) -> str:
    params = str(raw_task.get("params", ""))
    for part in params.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip() == "workload":
                return value.strip().strip('"')
    raise TasksetStoreError("generated task has no workload parameter")


def _offset(raw_task: Mapping[str, Any]) -> int:
    params = str(raw_task.get("params", ""))
    for part in params.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip() == "arrival_offset":
                return int(value)
    return 0


class TasksetStore:
    def __init__(
        self, root: Path, config: Mapping[str, Any], service: ServiceCurveMaterial
    ) -> None:
        self.root = Path(root)
        self.config = config
        self.service = service
        self.task_workload_contract = prepare_task_workload_contract(
            config, service.system_path
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "pairing_manifest.json"
        self._initialize_pairing_manifest()

    def _pairing_contract(self) -> Dict[str, Any]:
        dimensions: Dict[str, Dict[str, Any]] = {}
        for cell in expand_cells(self.config):
            dimensions[cell.generation_id] = {
                "generation_id": cell.generation_id,
                "dimensions": generation_dimensions(
                    self.config, cell.processors, cell.task_count,
                    cell.utilization,
                ),
            }
        start = int(self.config["grid"].get("taskset_index_start", 0))
        count = int(self.config["grid"]["tasksets_per_cell"])
        contract = {
            "generation_cells": [dimensions[key] for key in sorted(dimensions)],
            "base_seed": int(self.config["grid"]["base_seed"]),
            "seed_mode": self.config["grid"].get(
                "seed_mode", "generation_dimensions"
            ),
            "taskset_index_start": start,
            "taskset_index_end_exclusive": start + count,
            "tasksets_per_generation_cell": count,
            "exact_e0_grid": list(self.config["energy"]["initial_energy_values"]),
            "service_curve_identity": self.service.identity,
        }
        contract["task_workload_contract"] = (
            self.task_workload_contract.canonical_material()
        )
        if self.config.get("scenario", {}).get("kind") == "TIMING_STRESS":
            contract["scenario_capacity_feasibility_contract"] = {
                **capacity_contract_material(self.config),
                "contract_identity": capacity_contract_identity(self.config),
            }
        if is_b3_target_trace_v2(self.config):
            contract["scenario_target_trace_contract"] = (
                target_trace_contract_material()
            )
        return contract

    def _initialize_pairing_manifest(self) -> None:
        contract = self._pairing_contract()
        target_trace_v2 = is_b3_target_trace_v2(self.config)
        pairing_id = domain_hash(
            B3_V2_STORE_CONTRACT_DOMAIN
            if target_trace_v2
            else PAIRING_CONTRACT_DOMAIN,
            contract,
        )
        expected_schema = (
            B3_V2_PAIRING_MANIFEST_SCHEMA
            if target_trace_v2
            else PAIRING_MANIFEST_SCHEMA
        )
        if not self.manifest_path.is_file():
            self._write_manifest({
                "schema": expected_schema,
                "pairing_id": pairing_id,
                "contract": contract,
                "entries": [],
            })
            return
        manifest = self.manifest_document()
        observed_contract = manifest.get("contract")
        if (
            manifest.get("schema") == "ASAP_BLOCK_V9_3_CORE12_PAIRING_MANIFEST_V1"
            and isinstance(observed_contract, Mapping)
            and "task_workload_contract" not in observed_contract
        ):
            raise TasksetStoreError(
                "legacy taskset store lacks mandatory non-idle workload contract"
            )
        if (
            self.config.get("scenario", {}).get("kind") == "TIMING_STRESS"
            and isinstance(observed_contract, Mapping)
            and "scenario_capacity_feasibility_contract"
            not in observed_contract
        ):
            raise TasksetStoreError(
                "legacy B3 taskset store lacks the capacity feasibility "
                "contract; regenerate the B3 taskset store"
            )
        if (
            self.config.get("scenario", {}).get("kind") == "TIMING_STRESS"
            and isinstance(observed_contract, Mapping)
            and observed_contract.get(
                "scenario_capacity_feasibility_contract"
            ) != contract.get("scenario_capacity_feasibility_contract")
        ):
            raise TasksetStoreError(
                "B3 taskset store capacity feasibility contract mismatch; "
                "regenerate the B3 taskset store"
            )
        if (
            target_trace_v2
            and isinstance(observed_contract, Mapping)
            and observed_contract.get("scenario_target_trace_contract")
            != contract.get("scenario_target_trace_contract")
        ):
            raise TasksetStoreError(
                "B3-v2 taskset store target-trace contract mismatch; "
                "regenerate the isolated B3-v2 taskset store"
            )
        if (
            manifest.get("schema") != expected_schema
            or manifest.get("pairing_id") != pairing_id
            or manifest.get("contract") != contract
            or not isinstance(manifest.get("entries"), list)
        ):
            raise TasksetStoreError(
                "CORE-1/CORE-2 pairing manifest contract mismatch"
            )

    def _write_manifest(self, document: Mapping[str, Any]) -> None:
        _atomic_write(
            self.manifest_path,
            json.dumps(
                document, ensure_ascii=False, sort_keys=True, indent=2
            ) + "\n",
        )

    def manifest_document(self) -> Dict[str, Any]:
        try:
            document = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TasksetStoreError("cannot read pairing manifest") from exc
        if not isinstance(document, dict):
            raise TasksetStoreError("pairing manifest must be a mapping")
        return document

    @staticmethod
    def _manifest_entry(stored: StoredTaskset) -> Dict[str, Any]:
        return {
            "generation_id": stored.generation_id,
            "taskset_index": stored.taskset_index,
            "generation_seed": stored.seed,
            "taskset_id": stored.taskset_id,
            "taskset_semantic_hash": stored.semantic_hash,
            "priority_hash": stored.priority_hash,
            "power_hash": stored.power_hash,
            "task_payload": list(stored.task_payload),
            "service_curve_identity": stored.service_curve_reference,
        }

    def _register_pairing_entry(self, stored: StoredTaskset) -> None:
        manifest = self.manifest_document()
        entries = list(manifest["entries"])
        entry = self._manifest_entry(stored)
        key = (stored.generation_id, stored.taskset_index)
        matches = [
            row for row in entries
            if (row.get("generation_id"), row.get("taskset_index")) == key
        ]
        if len(matches) > 1:
            raise TasksetStoreError("duplicate taskset in pairing manifest")
        if matches:
            if matches[0] != entry:
                raise TasksetStoreError(
                    "pairing manifest taskset payload mismatch"
                )
            return
        entries.append(entry)
        manifest["entries"] = sorted(
            entries,
            key=lambda row: (row["generation_id"], row["taskset_index"]),
        )
        self._write_manifest(manifest)

    def verify_pairing_manifest(self, *, require_complete: bool) -> None:
        self._initialize_pairing_manifest()
        manifest = self.manifest_document()
        entries = manifest["entries"]
        index: Dict[tuple[str, int], Mapping[str, Any]] = {}
        for entry in entries:
            key = (str(entry.get("generation_id")), int(entry.get("taskset_index")))
            if key in index:
                raise TasksetStoreError("duplicate taskset in pairing manifest")
            index[key] = entry
        cells_by_generation = {
            cell.generation_id: cell for cell in expand_cells(self.config)
        }
        for key, entry in index.items():
            path = self.path_for(*key)
            if not path.is_file():
                raise TasksetStoreError("pairing manifest taskset file is missing")
            cell = cells_by_generation.get(key[0])
            if cell is None:
                raise TasksetStoreError(
                    "pairing manifest has unknown generation identity"
                )
            observed = self._manifest_entry(self._load(path, cell, key[1]))
            if observed != entry:
                raise TasksetStoreError(
                    "pairing manifest/taskset payload mismatch"
                )
        if not require_complete:
            return
        contract = manifest["contract"]
        start = int(contract["taskset_index_start"])
        end = int(contract["taskset_index_end_exclusive"])
        expected = {
            (row["generation_id"], taskset_index)
            for row in contract["generation_cells"]
            for taskset_index in range(start, end)
        }
        if set(index) != expected:
            raise TasksetStoreError(
                "formal pairing manifest is missing or has extra tasksets"
            )

    def path_for(self, generation_id: str, taskset_index: int) -> Path:
        return self.root / generation_id / f"taskset_{taskset_index:05d}.json"

    def get_or_create(self, cell: Cell, taskset_index: int) -> StoredTaskset:
        path = self.path_for(cell.generation_id, taskset_index)
        if path.is_file():
            stored = self._load(path, cell, taskset_index)
        else:
            stored = self._generate(path, cell, taskset_index)
        self._register_pairing_entry(stored)
        return stored

    def _generate(self, path: Path, cell: Cell, taskset_index: int) -> StoredTaskset:
        generation = self.config["generation"]
        seed = derive_seed(
            self.config["grid"]["base_seed"], cell.generation_id, taskset_index,
            seed_mode=self.config["grid"].get("seed_mode", "generation_dimensions"),
            utilization_index=cell.utilization_index,
        )
        target = cell.utilization * cell.processors
        with tempfile.TemporaryDirectory(prefix="v9_3_formal_generation_") as directory:
            generated_path = Path(directory) / "tasks.yaml"
            command = [
                sys.executable, str(TASK_GENERATOR),
                "-n", str(cell.task_count), "-u", format(float(target), ".15g"),
                "-p", str(generation["period_min"]), "-P", str(generation["period_max"]),
                "-c", str(cell.processors), "--seed", str(seed),
                "-s", str(self.service.system_path), "-o", str(generated_path),
                "--min-task-util", format(float(Fraction(generation["min_task_util"])), ".15g"),
                "--max-task-util", format(float(Fraction(generation["max_task_util"])), ".15g"),
                "--wcet-rounding", generation["wcet_rounding"],
                "--actual-utilization-tolerance-total", format(float(Fraction(generation["utilization_tolerance"])), ".15g"),
            ]
            for workload in self.task_workload_contract.candidates:
                command.extend(["--task-workload-candidate", workload])
            if generation["deadline_mode"] == "constrained":
                command.append("--constrained-deadlines")
            started = time.perf_counter()
            completed = subprocess.run(
                command, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                timeout=float(generation.get("generator_timeout_seconds", 120)), check=False,
            )
            elapsed = time.perf_counter() - started
            if completed.returncode:
                detail = (completed.stderr or completed.stdout or "")[-6000:]
                raise TasksetStoreError(
                    f"task generator failed with exit {completed.returncode}: {detail}"
                )
            with generated_path.open("r", encoding="utf-8") as handle:
                raw_document = yaml.safe_load(handle)
            legacy_tasks = legacy_rta.rm_order(legacy_rta.load_tasks(str(generated_path)))
        raw_by_name = {str(item["name"]): item for item in raw_document["taskset"]}
        system = legacy_rta.load_system_config(str(self.service.system_path))
        tasks = []
        payload = []
        for rank, legacy_task in enumerate(legacy_tasks):
            raw = raw_by_name[legacy_task.name]
            workload = _workload(raw)
            if workload == "idle":
                raise TasksetStoreError(
                    "generator produced reserved idle workload for a real-time task"
                )
            if workload not in self.task_workload_contract.candidates:
                raise TasksetStoreError(
                    f"generator produced workload outside task candidate contract: {workload}"
                )
            if str(legacy_task.workload) != workload:
                raise TasksetStoreError(
                    "generator workload representations disagree"
                )
            power = Fraction(str(system.task_energy_per_tick(workload)))
            expected_power = dict(self.task_workload_contract.power_model)[workload]
            if power != expected_power:
                raise TasksetStoreError(
                    "generator produced workload/P mismatch against actual power model"
                )
            task_id_value = str(rank)
            if legacy_task.deadline > legacy_task.period:
                raise TasksetStoreError("generator produced D > T")
            tasks.append(rta_core.V93Task(
                task_id_value, legacy_task.wcet, legacy_task.deadline,
                legacy_task.period, power,
            ))
            payload.append({
                "task_id": task_id_value,
                "source_name": legacy_task.name,
                "priority_rank": rank,
                "C": legacy_task.wcet,
                "D": legacy_task.deadline,
                "T": legacy_task.period,
                "P": fraction_text(power),
                "D_over_T": fraction_text(Fraction(legacy_task.deadline, legacy_task.period)),
                "workload": workload,
                "arrival_offset": _offset(raw),
            })
        actual = sum(Fraction(task.wcet, task.period) for task in tasks)
        tolerance = Fraction(generation["utilization_tolerance"])
        if abs(actual - target) > tolerance:
            raise TasksetStoreError("generated utilization lies outside configured tolerance")
        dimensions = generation_dimensions(
            self.config, cell.processors, cell.task_count, cell.utilization
        )
        canonical_payload = {
            "schema": FROZEN_TASKSET_SCHEMA,
            "generation_id": cell.generation_id,
            "taskset_index": taskset_index,
            "seed": seed,
            "generation_parameters": dimensions,
            "target_total_utilization": fraction_text(target),
            "actual_total_utilization": fraction_text(actual),
            "priority_policy": cell.priority_policy,
            "power_mode": cell.power_mode,
            "deadline_mode": cell.deadline_mode,
            "service_curve_reference": self.service.identity,
            "tasks": payload,
            "task_workload_contract": (
                self.task_workload_contract.canonical_material()
            ),
        }
        semantic_hash = domain_hash(
            FROZEN_TASKSET_SEMANTIC_DOMAIN, canonical_payload
        )
        priority_hash = domain_hash(
            "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
            [{"task_id": item["task_id"], "priority_rank": item["priority_rank"]} for item in payload],
        )
        power_hash = domain_hash(
            "ASAP_BLOCK:V9.3:POWER_VECTOR:v1",
            [{"task_id": item["task_id"], "P": item["P"]} for item in payload],
        )
        document = {
            **canonical_payload,
            "taskset_id": taskset_id(cell.generation_id, taskset_index, semantic_hash),
            "taskset_hash": semantic_hash,
            "priority_hash": priority_hash,
            "power_hash": power_hash,
            "generation_seconds": f"{elapsed:.9f}",
        }
        _atomic_write(path, json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return self._from_document(document, path)

    def _load(self, path: Path, cell: Cell, taskset_index: int) -> StoredTaskset:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TasksetStoreError(f"cannot read frozen taskset {path}: {exc}") from exc
        schema = document.get("schema")
        if (
            schema == "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V1"
            or "task_workload_contract" not in document
        ):
            raise TasksetStoreError(
                "legacy taskset store lacks mandatory non-idle workload contract"
            )
        if schema != FROZEN_TASKSET_SCHEMA:
            raise TasksetStoreError("frozen taskset workload contract schema mismatch")
        if document.get(
            "task_workload_contract"
        ) != self.task_workload_contract.canonical_material():
            raise TasksetStoreError("frozen taskset workload contract mismatch")
        if document.get("generation_id") != cell.generation_id:
            raise TasksetStoreError("frozen taskset generation identity mismatch")
        if document.get("taskset_index") != taskset_index:
            raise TasksetStoreError("frozen taskset index mismatch")
        if document.get("service_curve_reference") != self.service.identity:
            raise TasksetStoreError("frozen taskset service-curve identity mismatch")
        preimage_keys = [
            "schema", "generation_id", "taskset_index", "seed",
            "generation_parameters", "target_total_utilization",
            "actual_total_utilization", "priority_policy", "power_mode",
            "deadline_mode", "service_curve_reference", "tasks",
        ]
        preimage_keys.append("task_workload_contract")
        preimage = {
            key: document[key]
            for key in preimage_keys
        }
        observed = domain_hash(FROZEN_TASKSET_SEMANTIC_DOMAIN, preimage)
        if observed != document.get("taskset_hash"):
            raise TasksetStoreError("frozen taskset semantic hash mismatch")
        return self._from_document(document, path)

    def _from_document(self, document: Mapping[str, Any], path: Path) -> StoredTaskset:
        payload = tuple(document["tasks"])
        energy_by_workload = dict(self.task_workload_contract.power_model)
        for item in payload:
            workload = item.get("workload")
            if workload == "idle":
                raise TasksetStoreError(
                    "stored real-time task uses reserved idle workload"
                )
            if (
                not isinstance(workload, str)
                or not workload
                or workload.strip() != workload
                or workload not in energy_by_workload
            ):
                raise TasksetStoreError(
                    f"stored real-time task uses unknown workload: {workload}"
                )
            try:
                observed_power = Fraction(str(item["P"]))
            except (KeyError, ValueError, ZeroDivisionError) as exc:
                raise TasksetStoreError("stored real-time task has invalid P") from exc
            if observed_power != energy_by_workload[workload]:
                raise TasksetStoreError(
                    "stored real-time task P does not match actual power model"
                )
        tasks = tuple(rta_core.V93Task(
            str(item["task_id"]), int(item["C"]), int(item["D"]),
            int(item["T"]), Fraction(str(item["P"])),
        ) for item in payload)
        if any(task.deadline > task.period for task in tasks):
            raise TasksetStoreError("stored taskset violates D <= T")
        expected_priority_hash = domain_hash(
            "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
            [
                {"task_id": item["task_id"], "priority_rank": item["priority_rank"]}
                for item in payload
            ],
        )
        expected_power_hash = domain_hash(
            "ASAP_BLOCK:V9.3:POWER_VECTOR:v1",
            [{"task_id": item["task_id"], "P": item["P"]} for item in payload],
        )
        if document.get("priority_hash") != expected_priority_hash:
            raise TasksetStoreError("frozen taskset priority hash mismatch")
        if document.get("power_hash") != expected_power_hash:
            raise TasksetStoreError("frozen taskset power hash mismatch")
        return StoredTaskset(
            str(document["taskset_id"]), str(document["generation_id"]),
            int(document["taskset_index"]), int(document["seed"]),
            str(document["taskset_hash"]), str(document["priority_hash"]),
            str(document["power_hash"]), Fraction(document["target_total_utilization"]),
            Fraction(document["actual_total_utilization"]),
            int(document["generation_parameters"]["M"]), len(tasks),
            str(document["deadline_mode"]), tasks, payload,
            float(document.get("generation_seconds", 0)),
            str(document["service_curve_reference"]), path,
        )
