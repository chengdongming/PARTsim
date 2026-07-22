"""Frozen synchronous taskset store dedicated to v9.3 B4."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import yaml

from .config import (
    canonical_json, domain_hash, fraction_text, task_workload_contract_material,
)
from .performance_config import CONTRACT_VERSION, GENERATION_CONTRACT_VERSION
from .performance_energy import burst_energy, taskset_demand
from .performance_identity import taskset_store_identity
from .result_writer import atomic_write_json, atomic_write_text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASKSET_SCHEMA = "ASAP_BLOCK_V9_3_B4_FROZEN_TASKSET_V1"
MANIFEST_SCHEMA = "ASAP_BLOCK_V9_3_B4_TASKSET_MANIFEST_V1"
TASKSET_DOMAIN = "ASAP_BLOCK:V9.3:B4:TASKSET:v1"
PRIORITY_DOMAIN = "ASAP_BLOCK:V9.3:B4:PRIORITY:v1"
POWER_DOMAIN = "ASAP_BLOCK:V9.3:B4:POWER:v1"
RELEASE_DOMAIN = "ASAP_BLOCK:V9.3:B4:RELEASE:v1"


class PerformanceTasksetStoreError(RuntimeError):
    pass


def expected_store_contract(config: Mapping[str, Any]) -> Dict[str, Any]:
    template = Path(config["generation"]["system_template"])
    if not template.is_absolute():
        template = PROJECT_ROOT / template
    workload_contract = task_workload_contract_material(
        config["generation"]["workload_candidates"], template,
    )
    generation = config["generation"]
    count = int(config["grid"]["tasksets_per_utilization"])
    return {
        "schema": MANIFEST_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "generation_contract_version": GENERATION_CONTRACT_VERSION,
        "seed_space": config["seed_space"],
        "base_seed": int(config["grid"]["base_seed"]),
        "utilization_points": list(config["grid"]["utilization_points"]),
        "configured_tasksets_per_utilization": count,
        "taskset_index_range": [0, count - 1],
        "generation_contract": {
            "deadline_mode": generation["deadline_mode"],
            "period_min": int(generation["period_min"]),
            "period_max": int(generation["period_max"]),
            "wcet_rounding": generation["wcet_rounding"],
            "utilization_tolerance_total": generation["utilization_tolerance_total"],
            "min_task_util": generation["min_task_util"],
            "max_task_util": generation["max_task_util"],
            "dag_enabled": generation["dag_enabled"],
            "arrival_offset": generation["arrival_offset"],
            "release_pattern": "SYNCHRONOUS",
            "generation_semantic_flags": ["--constrained-deadlines", "--no-arrival-offset"],
            "workload_candidates": list(generation["workload_candidates"]),
        },
        "system_template_hash": _sha256(template),
        "workload_contract": workload_contract,
    }


def _manifest_identity_material(document: Mapping[str, Any]) -> Dict[str, Any]:
    material = {
        key: value for key, value in document.items()
        if key not in {"attempt_history", "store_identity"}
    }
    material["entries"] = [
        {key: value for key, value in entry.items() if key != "path"}
        for entry in material.get("entries", [])
    ]
    return material


@dataclass(frozen=True)
class PerformanceTaskset:
    taskset_id: str
    taskset_semantic_hash: str
    priority_hash: str
    power_hash: str
    release_hash: str
    utilization: str
    taskset_index: int
    seed: int
    actual_total_utilization: str
    p_dem: str
    e_burst: str
    tasks: Tuple[Mapping[str, Any], ...]
    path: Path


def generation_seed(base_seed: int, utilization_index: int, tasksets_per_utilization: int, taskset_index: int, attempt: int = 0) -> int:
    if min(base_seed, utilization_index, tasksets_per_utilization, taskset_index, attempt) < 0:
        raise ValueError("generation seed dimensions must be non-negative")
    return base_seed + utilization_index * tasksets_per_utilization + taskset_index + attempt * 1000000


def generation_command(
    config: Mapping[str, Any], *, utilization: Fraction, seed: int, output: Path,
) -> list:
    generation = config["generation"]
    command = [
        sys.executable, str(PROJECT_ROOT / "global_task_generator.py"),
        "--num-tasks", str(config["platform"]["task_count"]),
        "--utilization", format(float(utilization * config["platform"]["cores"]), ".17g"),
        "--min-period", str(generation["period_min"]),
        "--max-period", str(generation["period_max"]),
        "--cpus", str(config["platform"]["cores"]),
        "--constrained-deadlines", "--no-arrival-offset",
        "--system-config", str(Path(generation["system_template"])),
        "--seed", str(seed), "--min-task-util",
        format(float(Fraction(generation["min_task_util"])), ".17g"),
        "--max-task-util",
        format(float(Fraction(generation["max_task_util"])), ".17g"),
        "--wcet-rounding", generation["wcet_rounding"],
        "--actual-utilization-tolerance-total",
        format(float(Fraction(generation["utilization_tolerance_total"])), ".17g"),
        "--output", str(output),
    ]
    for workload in generation["workload_candidates"]:
        command.extend(["--task-workload-candidate", workload])
    return command


def _workload(raw: Mapping[str, Any]) -> str:
    for part in str(raw.get("params", "")).split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip() == "workload":
                return value.strip().strip('"')
    raise PerformanceTasksetStoreError("generated task has no workload")


def _offset(raw: Mapping[str, Any]) -> int:
    if "ph" in raw and int(raw["ph"]) != 0:
        return int(raw["ph"])
    for part in str(raw.get("params", "")).split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip() == "arrival_offset":
                return int(value)
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_generation_command(command: Sequence[str]) -> list:
    """Remove the materialization path from task semantics, retaining flags."""

    output = []
    skip = False
    for value in command[2:]:
        if skip:
            skip = False
            continue
        if value == "--output":
            skip = True
            continue
        output.append(value)
    return output


def _task_document(
    raw_document: Mapping[str, Any], config: Mapping[str, Any], utilization: str,
    taskset_index: int, seed: int, generation_attempt: int,
    command: Sequence[str], workload_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    raw_tasks = raw_document.get("taskset")
    if not isinstance(raw_tasks, list) or len(raw_tasks) != config["platform"]["task_count"]:
        raise PerformanceTasksetStoreError("generator returned the wrong task count")
    power_by_workload = {
        row["workload"]: Fraction(row["energy_per_tick"])
        for row in workload_contract["power_model"]
    }
    sortable = []
    for source_index, raw in enumerate(raw_tasks):
        name = str(raw.get("name", ""))
        if not name.startswith("task_"):
            raise PerformanceTasksetStoreError("DAG/non-periodic task leaked into B4")
        c_value = int(raw.get("runtime", 0))
        d_value = int(raw.get("deadline", 0))
        t_value = int(raw.get("iat", 0))
        offset = _offset(raw)
        workload = _workload(raw)
        if not 0 < c_value <= d_value <= t_value:
            raise PerformanceTasksetStoreError("generated task violates C <= D <= T")
        if offset != 0:
            raise PerformanceTasksetStoreError("B4 generated a nonzero arrival offset")
        if workload == "idle" or workload not in power_by_workload:
            raise PerformanceTasksetStoreError("generated workload violates the frozen contract")
        sortable.append((t_value, source_index, name, c_value, d_value, workload, offset))
    sortable.sort(key=lambda item: (item[0], item[1], item[2]))
    tasks = []
    for rank, (t_value, _source_index, name, c_value, d_value, workload, offset) in enumerate(sortable):
        tasks.append({
            "task_id": str(rank), "source_name": name, "priority_rank": rank,
            "C": c_value, "D": d_value, "T": t_value,
            "P": fraction_text(power_by_workload[workload]),
            "D_over_T": fraction_text(Fraction(d_value, t_value)),
            "workload": workload, "arrival_offset": offset,
        })
    actual = sum((Fraction(task["C"], task["T"]) for task in tasks), Fraction(0))
    target = Fraction(utilization) * int(config["platform"]["cores"])
    if abs(actual - target) > Fraction(config["generation"]["utilization_tolerance_total"]):
        raise PerformanceTasksetStoreError("actual utilization is outside tolerance")
    semantic_material = {
        "schema": TASKSET_SCHEMA, "contract_version": CONTRACT_VERSION,
        "generation_contract_version": GENERATION_CONTRACT_VERSION,
        "seed_space": config["seed_space"], "generation_seed": seed,
        "generation_attempt": generation_attempt,
        "generation_parameters": _semantic_generation_command(command),
        "utilization": utilization,
        "taskset_index": taskset_index,
        "target_total_utilization": fraction_text(target),
        "actual_total_utilization": fraction_text(actual),
        "priority_policy": "RM", "release_pattern": "SYNCHRONOUS",
        "tasks": tasks, "workload_contract": workload_contract,
    }
    semantic_hash = domain_hash(TASKSET_DOMAIN, semantic_material)
    priority_hash = domain_hash(PRIORITY_DOMAIN, [
        {"task_id": task["task_id"], "priority_rank": task["priority_rank"], "T": task["T"]}
        for task in tasks
    ])
    power_hash = domain_hash(POWER_DOMAIN, [
        {"task_id": task["task_id"], "P": task["P"], "workload": task["workload"]}
        for task in tasks
    ])
    release_hash = domain_hash(RELEASE_DOMAIN, [
        {"task_id": task["task_id"], "arrival_offset": task["arrival_offset"]}
        for task in tasks
    ])
    return {
        **semantic_material,
        "generation_command": list(command),
        "taskset_id": f"b4-u{utilization.replace('/', '_')}-i{taskset_index:03d}-{semantic_hash[:12]}",
        "taskset_semantic_hash": semantic_hash,
        "priority_hash": priority_hash, "power_hash": power_hash,
        "release_hash": release_hash,
        "workload_vector": [task["workload"] for task in tasks],
        "p_dem": fraction_text(taskset_demand(tasks)),
        "e_burst": fraction_text(burst_energy(tasks, int(config["platform"]["cores"]))),
    }


class PerformanceTasksetStore:
    def __init__(self, root: Path, config: Mapping[str, Any]) -> None:
        self.root = Path(root)
        self.config = config
        template = Path(config["generation"]["system_template"])
        if not template.is_absolute():
            template = PROJECT_ROOT / template
        self.system_template = template
        self.workload_contract = task_workload_contract_material(
            config["generation"]["workload_candidates"], template,
        )
        self.manifest_path = self.root / "taskset_manifest.json"

    def _path(self, utilization: str, index: int) -> Path:
        return self.root / "tasksets" / f"u_{utilization.replace('/', '_')}" / f"taskset_{index:03d}.json"

    def freeze(self, *, max_tasksets_per_utilization: Optional[int] = None) -> Dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        entries = []
        attempts = []
        points = self.config["grid"]["utilization_points"]
        configured_count = int(self.config["grid"]["tasksets_per_utilization"])
        count = configured_count if max_tasksets_per_utilization is None else min(configured_count, max_tasksets_per_utilization)
        for u_index, utilization in enumerate(points):
            for index in range(count):
                path = self._path(utilization, index)
                if path.is_file():
                    document = self._read_and_verify(path)
                    entries.append(self._manifest_entry(document, path))
                    continue
                last_error = ""
                for attempt in range(int(self.config["generation"]["structural_retry_limit"]) + 1):
                    seed = generation_seed(
                        int(self.config["grid"]["base_seed"]), u_index,
                        configured_count, index, attempt,
                    )
                    generated = self.root / "attempts" / f"u{u_index}_i{index}_a{attempt}.yml"
                    generated.parent.mkdir(parents=True, exist_ok=True)
                    command = generation_command(
                        self.config, utilization=Fraction(utilization), seed=seed, output=generated,
                    )
                    started = time.perf_counter()
                    completed = subprocess.run(
                        command, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                        timeout=int(self.config["generation"]["generator_timeout_seconds"]), check=False,
                    )
                    attempt_row = {
                        "utilization": utilization, "taskset_index": index,
                        "attempt": attempt, "seed": seed, "command": command,
                        "returncode": completed.returncode,
                        "runtime_seconds": format(time.perf_counter() - started, ".9f"),
                        "stdout_tail": (completed.stdout or "")[-2000:],
                        "stderr_tail": (completed.stderr or "")[-2000:],
                    }
                    try:
                        if completed.returncode != 0:
                            raise PerformanceTasksetStoreError("generator exited nonzero")
                        raw_document = yaml.safe_load(generated.read_text(encoding="utf-8"))
                        document = _task_document(
                            raw_document, self.config, utilization, index, seed, attempt,
                            command, self.workload_contract,
                        )
                        path.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_json(path, document)
                        attempt_row["accepted"] = True
                        attempts.append(attempt_row)
                        entries.append(self._manifest_entry(document, path))
                        break
                    except (OSError, yaml.YAMLError, PerformanceTasksetStoreError) as exc:
                        last_error = str(exc)
                        attempt_row.update({"accepted": False, "structural_error": last_error})
                        attempts.append(attempt_row)
                else:
                    atomic_write_json(
                        self.root / "failed_attempt_history.json",
                        {"attempt_history": attempts},
                    )
                    raise PerformanceTasksetStoreError(
                        f"structural retry limit exhausted for u={utilization}, index={index}: {last_error}"
                    )
        contract = {
            **expected_store_contract(self.config),
            "frozen_tasksets_per_utilization": count,
            "entries": entries,
        }
        manifest = {
            **contract, "attempt_history": attempts,
        }
        manifest["store_identity"] = taskset_store_identity(_manifest_identity_material(manifest))
        atomic_write_json(self.manifest_path, manifest)
        if self.config["stage"] == "CALIBRATION":
            atomic_write_json(self.root / "calibration_taskset_manifest.json", manifest)
        return manifest

    def _manifest_entry(self, document: Mapping[str, Any], path: Path) -> Dict[str, Any]:
        entry = {
            key: document[key] for key in (
                "taskset_id", "taskset_semantic_hash", "priority_hash", "power_hash",
                "release_hash", "utilization", "taskset_index", "generation_seed",
                "generation_attempt", "actual_total_utilization", "p_dem", "e_burst",
            )
        }
        entry["path"] = str(path.resolve())
        return entry

    def _read_and_verify(self, path: Path) -> Dict[str, Any]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PerformanceTasksetStoreError(f"cannot read taskset: {path}") from exc
        if document.get("schema") != TASKSET_SCHEMA:
            raise PerformanceTasksetStoreError("taskset schema mismatch")
        if any(int(task.get("arrival_offset", -1)) != 0 for task in document.get("tasks", [])):
            raise PerformanceTasksetStoreError("stored taskset has nonzero arrival offset")
        semantic_keys = (
            "schema", "contract_version", "generation_contract_version", "seed_space",
            "generation_seed", "generation_attempt", "generation_parameters", "utilization", "taskset_index",
            "target_total_utilization", "actual_total_utilization", "priority_policy",
            "release_pattern", "tasks", "workload_contract",
        )
        material = {key: document[key] for key in semantic_keys}
        if domain_hash(TASKSET_DOMAIN, material) != document.get("taskset_semantic_hash"):
            raise PerformanceTasksetStoreError("taskset semantic hash mismatch")
        utilization = str(document.get("utilization", ""))
        if utilization not in self.config["grid"]["utilization_points"]:
            raise PerformanceTasksetStoreError("taskset utilization is outside the current config")
        utilization_index = self.config["grid"]["utilization_points"].index(utilization)
        index = int(document.get("taskset_index", -1))
        configured_count = int(self.config["grid"]["tasksets_per_utilization"])
        if not 0 <= index < configured_count:
            raise PerformanceTasksetStoreError("taskset index is outside the current config")
        attempt = int(document.get("generation_attempt", -1))
        if not 0 <= attempt <= int(self.config["generation"]["structural_retry_limit"]):
            raise PerformanceTasksetStoreError("taskset generation attempt is invalid")
        expected_seed = generation_seed(
            int(self.config["grid"]["base_seed"]), utilization_index,
            configured_count, index, attempt,
        )
        if int(document.get("generation_seed", -1)) != expected_seed:
            raise PerformanceTasksetStoreError("taskset generation seed/config mismatch")
        if document.get("workload_contract") != self.workload_contract:
            raise PerformanceTasksetStoreError("taskset workload contract differs from current config")
        expected_parameters = _semantic_generation_command(generation_command(
            self.config, utilization=Fraction(utilization), seed=expected_seed,
            output=Path("/unused"),
        ))
        if document.get("generation_parameters") != expected_parameters:
            raise PerformanceTasksetStoreError("taskset generation semantic flags/config mismatch")
        expected_priority = domain_hash(PRIORITY_DOMAIN, [
            {"task_id": task["task_id"], "priority_rank": task["priority_rank"], "T": task["T"]}
            for task in document["tasks"]
        ])
        expected_power = domain_hash(POWER_DOMAIN, [
            {"task_id": task["task_id"], "P": task["P"], "workload": task["workload"]}
            for task in document["tasks"]
        ])
        expected_release = domain_hash(RELEASE_DOMAIN, [
            {"task_id": task["task_id"], "arrival_offset": task["arrival_offset"]}
            for task in document["tasks"]
        ])
        if document.get("priority_hash") != expected_priority:
            raise PerformanceTasksetStoreError("priority hash mismatch")
        if document.get("power_hash") != expected_power:
            raise PerformanceTasksetStoreError("power hash mismatch")
        if document.get("release_hash") != expected_release:
            raise PerformanceTasksetStoreError("release hash mismatch")
        return document

    def load(self, utilization: str, index: int) -> PerformanceTaskset:
        path = self._path(utilization, index)
        document = self._read_and_verify(path)
        return PerformanceTaskset(
            str(document["taskset_id"]), str(document["taskset_semantic_hash"]),
            str(document["priority_hash"]), str(document["power_hash"]),
            str(document["release_hash"]), str(document["utilization"]),
            int(document["taskset_index"]), int(document["generation_seed"]),
            str(document["actual_total_utilization"]), str(document["p_dem"]),
            str(document["e_burst"]), tuple(document["tasks"]), path,
        )

    def verify_manifest(self) -> Dict[str, Any]:
        if not self.manifest_path.is_file():
            raise PerformanceTasksetStoreError("taskset store is not frozen")
        document = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if taskset_store_identity(_manifest_identity_material(document)) != document.get("store_identity"):
            raise PerformanceTasksetStoreError("taskset store identity mismatch")
        expected = expected_store_contract(self.config)
        mismatches = [key for key, value in expected.items() if document.get(key) != value]
        if mismatches:
            raise PerformanceTasksetStoreError(f"taskset store/current config mismatch: {mismatches}")
        configured_count = int(self.config["grid"]["tasksets_per_utilization"])
        if document.get("frozen_tasksets_per_utilization") != configured_count:
            raise PerformanceTasksetStoreError("taskset store is only partially frozen")
        expected_pairs = [
            (utilization, index)
            for utilization in self.config["grid"]["utilization_points"]
            for index in range(configured_count)
        ]
        observed_pairs = [
            (str(entry.get("utilization")), int(entry.get("taskset_index", -1)))
            for entry in document.get("entries", [])
        ]
        if observed_pairs != expected_pairs:
            raise PerformanceTasksetStoreError("taskset manifest utilization/index grid mismatch")
        for entry in document["entries"]:
            observed = self._read_and_verify(Path(entry["path"]))
            for key, value in entry.items():
                if key != "path" and observed.get(key) != value:
                    raise PerformanceTasksetStoreError(f"manifest/taskset field mismatch: {key}")
        return document
