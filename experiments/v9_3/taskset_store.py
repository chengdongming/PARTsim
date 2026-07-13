"""Persistent canonical tasksets shared by v9.3 CORE-1 and CORE-2."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
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

from .cell_model import Cell, derive_seed, generation_dimensions, taskset_id
from .config import canonical_json, domain_hash, fraction_text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK_GENERATOR = PROJECT_ROOT / "global_task_generator.py"


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
    with template.open("r", encoding="utf-8") as handle:
        system_document = yaml.safe_load(handle)
    system_document["cpu_islands"][0]["numcpus"] = max(config["platform"]["cores"])
    energy = system_document.setdefault("energy_management", {})
    energy["max_energy"] = float(Fraction(config["energy"]["battery_capacity"]))
    # Generator energy state is a generation parameter, not theorem E0. Keep
    # it independent of the analysis E0 grid so paired cells freeze one taskset.
    energy["initial_energy"] = energy["max_energy"]
    system_path = run_root / "system_config.yaml"
    _atomic_write(
        system_path,
        yaml.safe_dump(system_document, allow_unicode=True, sort_keys=False),
    )
    system = legacy_rta.load_system_config(str(system_path))
    horizon = int(spec["horizon"])
    trace = legacy_rta._harvest_trace_from_config(system, horizon)
    curve = legacy_rta.build_energy_service_curve(trace, horizon)
    required = max(config["generation"]["period_max"] - 1, 0)
    if required >= len(curve):
        raise TasksetStoreError("service-curve horizon is shorter than required deadline horizon")
    values = tuple(Fraction(str(curve[index])) for index in range(required + 1))
    values = rta_core.validate_service_curve_v9_3(values, required)
    raw = {
        "id": spec["id"],
        "horizon": horizon,
        "system_template": str(spec["system_template"]),
        "validated_prefix": [fraction_text(item) for item in values],
    }
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
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, generation_id: str, taskset_index: int) -> Path:
        return self.root / generation_id / f"taskset_{taskset_index:05d}.json"

    def get_or_create(self, cell: Cell, taskset_index: int) -> StoredTaskset:
        path = self.path_for(cell.generation_id, taskset_index)
        if path.is_file():
            return self._load(path, cell, taskset_index)
        return self._generate(path, cell, taskset_index)

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
            power = Fraction(str(system.task_energy_per_tick(legacy_task.workload)))
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
                "workload": _workload(raw),
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
            "schema": "ASAP_BLOCK_V9_3_FROZEN_TASKSET_V1",
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
        }
        semantic_hash = domain_hash("ASAP_BLOCK:V9.3:TASKSET_SEMANTIC:v1", canonical_payload)
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
        if document.get("generation_id") != cell.generation_id:
            raise TasksetStoreError("frozen taskset generation identity mismatch")
        if document.get("taskset_index") != taskset_index:
            raise TasksetStoreError("frozen taskset index mismatch")
        if document.get("service_curve_reference") != self.service.identity:
            raise TasksetStoreError("frozen taskset service-curve identity mismatch")
        preimage = {
            key: document[key]
            for key in (
                "schema", "generation_id", "taskset_index", "seed",
                "generation_parameters", "target_total_utilization",
                "actual_total_utilization", "priority_policy", "power_mode",
                "deadline_mode", "service_curve_reference", "tasks",
            )
        }
        observed = domain_hash("ASAP_BLOCK:V9.3:TASKSET_SEMANTIC:v1", preimage)
        if observed != document.get("taskset_hash"):
            raise TasksetStoreError("frozen taskset semantic hash mismatch")
        return self._from_document(document, path)

    def _from_document(self, document: Mapping[str, Any], path: Path) -> StoredTaskset:
        payload = tuple(document["tasks"])
        tasks = tuple(rta_core.V93Task(
            str(item["task_id"]), int(item["C"]), int(item["D"]),
            int(item["T"]), Fraction(str(item["P"])),
        ) for item in payload)
        if any(task.deadline > task.period for task in tasks):
            raise TasksetStoreError("stored taskset violates D <= T")
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
