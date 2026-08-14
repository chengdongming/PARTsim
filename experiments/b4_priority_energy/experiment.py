"""Direct, reproducible B4 priority-energy experiment.

This module contains the scientific grid and the input materialization needed
by the simulator.  It deliberately has no authorization, freeze-manifest,
publication-status, or protocol-lifecycle concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[2]
B4_DIR = Path(__file__).resolve().parent
SYSTEM_TEMPLATE = ROOT / "v9_3_b4_priority_energy_system_template.yml"
TASK_GENERATOR = ROOT / "global_task_generator.py"
SIMULATOR = Path(os.environ.get("PARTSIM_RTSIM_BIN", str(ROOT / "rtsim" / "rtsim")))

PROCESSORS = 4
TASK_COUNT = 10
HORIZON_MS = 30_000
TICK_MS = 1
PERIOD_MIN_MS = 40
PERIOD_MAX_MS = 200
MIN_TASK_UTILIZATION = Fraction(1, 100)
MAX_TASK_UTILIZATION = Fraction(45, 100)
UTILIZATION_TOLERANCE = Fraction(1, 100)
RHO_REFERENCE = "2"
SOURCE_INTEGRAL_SECONDS = Fraction(22)
TIMEOUT_SECONDS = 300
RETRY_TIMEOUT_SECONDS = 600

ALGORITHMS = (
    "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC",
    "ALAP-BLOCK", "ALAP-NONBLOCK", "ALAP-SYNC",
    "ST-BLOCK", "ST-NONBLOCK", "ST-SYNC",
)
PILOT_ALGORITHMS = (
    "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK",
)
ALGORITHM_CLI = {
    "ASAP-BLOCK": "gpfp_asap_block", "ASAP-NONBLOCK": "gpfp_asap_nonblock",
    "ASAP-SYNC": "gpfp_asap_sync", "ALAP-BLOCK": "gpfp_alap_block",
    "ALAP-NONBLOCK": "gpfp_alap_nonblock", "ALAP-SYNC": "gpfp_alap_sync",
    "ST-BLOCK": "gpfp_st_block", "ST-NONBLOCK": "gpfp_st_nonblock",
    "ST-SYNC": "gpfp_st_sync",
}
GRID = {
    "pilot": {"utilization": ("0.3", "0.4", "0.5"), "lambda_E": ("0.70", "0.85", "1.00", "1.15"), "rho_E": ("1", "2"), "replicates": 20, "algorithms": PILOT_ALGORITHMS},
    "formal_main": {"utilization": ("0.2", "0.3", "0.4", "0.5", "0.6"), "lambda_E": ("0.70", "0.85", "1.00", "1.15"), "rho_E": ("2",), "replicates": 100, "algorithms": ALGORITHMS},
    "negative_control": {"utilization": ("0.3", "0.4", "0.5"), "lambda_E": ("0.85", "1.00"), "rho_E": ("1",), "replicates": 100, "algorithms": ALGORITHMS},
}
PHASE_COUNTS = {"pilot": 2400, "formal_main": 18000, "negative_control": 5400}
POOL = {"pilot": "pilot", "formal_main": "formal", "negative_control": "formal"}

TASKSET_KEY_FIELDS = ("identity_protocol", "taskset_pool", "utilization", "replicate_index")
SOURCE_KEY_FIELDS = ("identity_protocol", "taskset_id", "lambda_E", "source_profile", "horizon_ms", "rho_reference", "E0_rule", "Emax_rule", "alpha_rule")
CASE_KEY_FIELDS = ("identity_protocol", "phase", "taskset_id", "source_id", "rho_E", "algorithm")
IDENTITY_NAMESPACE = "B4-PE-v5.2/I4A-0-v1"
TASKSET_SEED_DOMAIN = "B4-PE/TASKSET-SEED/v1\n"
SOURCE_ID_DOMAIN = "B4-PE/SOURCE-ID/v1\n"
TASKSET_ID_DOMAIN = "B4-PE/TASKSET-ID/v1\n"
CASE_ID_DOMAIN = "B4-PE/CASE-ID/v1\n"

_TASK_NAME = re.compile(r"task_([0-9]+)")


@dataclass(frozen=True)
class B4Request:
    phase: str
    utilization: str
    replicate_index: int
    lambda_E: str
    rho_E: str
    algorithm: str
    taskset_seed: int
    taskset_id: str
    source_id: str
    case_id: str
    source_seed: None = None

    def row(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "algorithm_cli": ALGORITHM_CLI[self.algorithm],
            "taskset_pool": POOL[self.phase],
            "processors": PROCESSORS,
            "task_count": TASK_COUNT,
            "horizon_ms": HORIZON_MS,
            "scheduler": self.algorithm,
        }


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_yaml(value: Any) -> bytes:
    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, indentless=False)
    return (yaml.dump(value, Dumper=Dumper, sort_keys=False, allow_unicode=True) or "").encode("utf-8")


def _hash(domain: str, value: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(domain.encode("utf-8") + canonical_json(value)).digest()


def _id(domain: str, value: Mapping[str, Any], prefix: str) -> str:
    return prefix + hashlib.sha256(domain.encode("utf-8") + canonical_json(value)).hexdigest()


def _seed(domain: str, value: Mapping[str, Any]) -> int:
    return int.from_bytes(_hash(domain, value)[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _semantic(phase: str, utilization: str, replicate_index: int, lambda_E: str, rho_E: str, algorithm: str) -> dict[str, Any]:
    return {
        "identity_protocol": IDENTITY_NAMESPACE,
        "taskset_pool": POOL[phase], "utilization": utilization,
        "replicate_index": replicate_index, "phase": phase,
        "lambda_E": lambda_E, "rho_E": rho_E, "algorithm": algorithm,
        "source_profile": "three-stage-offered-harvest-v1",
        "horizon_ms": HORIZON_MS, "rho_reference": RHO_REFERENCE,
        "E0_rule": "E_burst_ref", "Emax_rule": "2*E_burst_ref",
        "alpha_rule": "(lambda_E*E_dem_nom(H)-E0)/22s",
    }


def _identity_parts(phase: str, utilization: str, replicate_index: int, lambda_E: str, rho_E: str, algorithm: str) -> dict[str, Any]:
    semantic = _semantic(phase, utilization, replicate_index, lambda_E, rho_E, algorithm)
    task_key = {name: semantic[name] for name in TASKSET_KEY_FIELDS}
    task_seed = _seed(TASKSET_SEED_DOMAIN, task_key)
    task_id = _id(TASKSET_ID_DOMAIN, task_key, "ts-")
    source_material = {**semantic, "taskset_id": task_id}
    source_key = {name: source_material[name] for name in SOURCE_KEY_FIELDS}
    source_id = _id(SOURCE_ID_DOMAIN, source_key, "src-")
    case_material = {**source_material, "source_id": source_id}
    case_key = {name: case_material[name] for name in CASE_KEY_FIELDS}
    case_id = _id(CASE_ID_DOMAIN, case_key, "case-")
    return {"taskset_seed": task_seed, "taskset_id": task_id, "source_seed": None, "source_id": source_id, "case_id": case_id}


def iter_requests(phases: Iterable[str] = ("pilot", "formal_main", "negative_control")) -> Iterable[B4Request]:
    for phase in phases:
        config = GRID[phase]
        for utilization in config["utilization"]:
            for lambda_E in config["lambda_E"]:
                for rho_E in config["rho_E"]:
                    for replicate in range(1, config["replicates"] + 1):
                        for algorithm in config["algorithms"]:
                            parts = _identity_parts(phase, utilization, replicate, lambda_E, rho_E, algorithm)
                            yield B4Request(phase, utilization, replicate, lambda_E, rho_E, algorithm, **parts)


def request_plan(phases: Iterable[str] = ("pilot", "formal_main", "negative_control")) -> list[dict[str, Any]]:
    rows = [request.row() for request in iter_requests(phases)]
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate B4 case ID")
    return rows


def _task_id(task: Mapping[str, Any]) -> int:
    match = _TASK_NAME.fullmatch(str(task.get("name", "")))
    if not match:
        raise ValueError("task name is not task_<integer>")
    return int(match.group(1))


def _params(task: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for item in str(task.get("params", "")).split(","):
        key, value = item.split("=", 1)
        result[key] = value
    return result


def validate_base_taskset(document: Mapping[str, Any], utilization: str | None = None) -> Mapping[str, Any]:
    tasks = document.get("taskset") if isinstance(document, Mapping) else None
    if not isinstance(tasks, list) or len(tasks) != TASK_COUNT:
        raise ValueError("B4 taskset must contain exactly ten tasks")
    if {_task_id(task) for task in tasks} != set(range(TASK_COUNT)):
        raise ValueError("B4 task IDs must be exactly 0..9")
    total = Fraction(0)
    for task in tasks:
        if not isinstance(task, Mapping) or not all(type(task.get(key)) is int for key in ("iat", "runtime", "deadline")):
            raise ValueError("B4 task computation fields are invalid")
        period, runtime, deadline = task["iat"], task["runtime"], task["deadline"]
        if not (PERIOD_MIN_MS <= period <= PERIOD_MAX_MS and 1 <= runtime <= deadline <= period):
            raise ValueError("B4 task violates C/D/T bounds")
        params = _params(task)
        if params.get("period") != str(period) or params.get("wcet") != str(runtime) or not (0 <= int(params.get("arrival_offset", -1)) < period):
            raise ValueError("B4 task params do not match C/T")
        total += Fraction(runtime, period)
    if utilization is not None and abs(total - Fraction(utilization) * PROCESSORS) > UTILIZATION_TOLERANCE:
        raise ValueError("B4 taskset utilization is outside frozen tolerance")
    return document


def _release_count(period: int, offset: int) -> int:
    return 0 if offset >= HORIZON_MS else (HORIZON_MS - 1 - offset) // period + 1


def _energy_groups(document: Mapping[str, Any]):
    ranked = sorted(document["taskset"], key=lambda task: (task["iat"], _task_id(task)))
    q0 = Fraction(1, 2) * Fraction(8, 10) / 1000
    def group(rows):
        return sum((_release_count(task["iat"], int(_params(task)["arrival_offset"])) * task["runtime"] * q0 for task in rows), Fraction(0))
    return ranked, q0, group(ranked[:PROCESSORS]), group(ranked[PROCESSORS:])


def _factors(high: Fraction, low: Fraction, rho_E: str):
    rho = Fraction(rho_E)
    low_factor = (high + low) / (rho * high + low)
    return rho * low_factor, low_factor


def derive_execution_taskset(document: Mapping[str, Any], rho_E: str) -> dict[str, Any]:
    ranked, _q0_value, high_energy, low_energy = _energy_groups(document)
    high_factor, low_factor = _factors(high_energy, low_energy, rho_E)
    high_ids = {_task_id(task) for task in ranked[:PROCESSORS]}
    derived = json.loads(json.dumps(document))
    for task in derived["taskset"]:
        params = _params(task)
        params["task_energy_factor"] = str(high_factor if _task_id(task) in high_ids else low_factor)
        task["params"] = ",".join(f"{key}={params[key]}" for key in ("period", "wcet", "arrival_offset", "workload", "task_energy_factor"))
    return derived


def source_energy(document: Mapping[str, Any], lambda_E: str) -> dict[str, Fraction]:
    ranked, q0, high_energy, low_energy = _energy_groups(document)
    reference_high, _reference_low = _factors(high_energy, low_energy, RHO_REFERENCE)
    burst = sum((task["runtime"] * q0 * reference_high for task in ranked[:PROCESSORS]), Fraction(0))
    demand = high_energy + low_energy
    alpha = (Fraction(lambda_E) * demand - burst) / SOURCE_INTEGRAL_SECONDS
    if alpha < 0:
        raise ValueError("B4 source alpha is negative")
    return {"E0_j": burst, "Emax_j": 2 * burst, "alpha_w": alpha, "nominal_demand_j": demand, "W_H_j": high_energy, "W_L_j": low_energy}


def generate_base_taskset(request: B4Request) -> tuple[dict[str, Any], bytes]:
    with tempfile.TemporaryDirectory(prefix="b4-direct-generator-") as temp:
        output = Path(temp) / "taskset.raw.yml"
        target_total = float(Fraction(request.utilization) * PROCESSORS)
        argv = [sys.executable, str(TASK_GENERATOR), "--num-tasks", str(TASK_COUNT), "--utilization", str(target_total), "--min-period", str(PERIOD_MIN_MS), "--max-period", str(PERIOD_MAX_MS), "--cpus", str(PROCESSORS), "--constrained-deadlines", "--arrival-offset", "--system-config", str(SYSTEM_TEMPLATE), "--seed", str(request.taskset_seed), "--min-task-util", "0.01", "--max-task-util", "0.45", "--wcet-rounding", "compensated", "--actual-utilization-tolerance-total", "0.01", "--task-workload-candidate", "hash", "--output", str(output)]
        completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"})
        if completed.returncode or not output.is_file():
            raise RuntimeError(f"task generator failed: {completed.stderr[-2000:]}")
        document = yaml.safe_load(output.read_text(encoding="utf-8"))
    validate_base_taskset(document, request.utilization)
    payload = canonical_yaml(document)
    return yaml.safe_load(payload.decode("utf-8")), payload


def render_system_config(energy: Mapping[str, Fraction], algorithm: str) -> bytes:
    document = yaml.safe_load(SYSTEM_TEMPLATE.read_text(encoding="utf-8"))
    document.pop("priority_energy", None)
    management = document["energy_management"]
    for field in ("day_of_year", "time_of_day_ms", "base_harvesting_rate", "harvesting_scale", "use_real_solar_data", "solar_data_file", "pv_efficiency", "pv_area_m2", "start_offset_minutes"):
        management.pop(field, None)
    management["initial_energy"] = float(energy["E0_j"])
    management["max_energy"] = float(energy["Emax_j"])
    document["cpu_islands"][0]["kernel"]["scheduler"] = ALGORITHM_CLI[algorithm]
    document["harvesting"] = {
        "source": "scaled_piecewise",
        "scaled_piecewise": {"scale_w": float(energy["alpha_w"]), "segments": [
            {"start_ms": 0, "end_ms": 5000, "multiplier": 1.0},
            {"start_ms": 5000, "end_ms": 15000, "multiplier": 0.2},
            {"start_ms": 15000, "end_ms": 30000, "multiplier": 1.0},
        ]},
    }
    return canonical_yaml(document)


def source_descriptor(request: B4Request, energy: Mapping[str, Fraction]) -> bytes:
    document = {
        "schema": "b4-direct-source-v1", "source_id": request.source_id,
        "taskset_id": request.taskset_id, "lambda_E": request.lambda_E,
        "rho_reference": RHO_REFERENCE, "E0_j": str(energy["E0_j"]),
        "Emax_j": str(energy["Emax_j"]), "alpha_w": str(energy["alpha_w"]),
        "segments": [[0, 5000, "1"], [5000, 15000, "1/5"], [15000, 30000, "1"]],
    }
    return canonical_json(document) + b"\n"


def materialize_request(request: B4Request, root: Path, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if request.taskset_id not in cache:
        base, base_payload = generate_base_taskset(request)
        cache[request.taskset_id] = {"base": base, "base_sha256": hashlib.sha256(base_payload).hexdigest()}
    base = cache[request.taskset_id]["base"]
    execution = derive_execution_taskset(base, request.rho_E)
    energy = source_energy(base, request.lambda_E)
    taskset_rel = Path("inputs/tasksets") / request.taskset_id / f"rho-{request.rho_E}.yml"
    source_rel = Path("inputs/sources") / f"{request.source_id}.json"
    config_rel = Path("inputs/configs") / ALGORITHM_CLI[request.algorithm] / f"{request.case_id}.yml"
    result_rel = Path("results") / request.phase / f"{request.case_id}.json"
    files = {
        taskset_rel: canonical_yaml(execution),
        source_rel: source_descriptor(request, energy),
        config_rel: render_system_config(energy, request.algorithm),
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f"input conflict: {relative}")
        if not path.exists():
            path.write_bytes(payload)
    return {
        **request.row(),
        "taskset_artifact": str(taskset_rel),
        "source_artifact": str(source_rel),
        "system_config_artifact": str(config_rel),
        "result_relpath": str(result_rel),
        "taskset_sha256": hashlib.sha256(files[taskset_rel]).hexdigest(),
        "taskset_semantic_hash": hashlib.sha256(files[taskset_rel]).hexdigest(),
        "source_sha256": hashlib.sha256(files[source_rel]).hexdigest(),
        "system_sha256": hashlib.sha256(files[config_rel]).hexdigest(),
        "timeout_seconds": TIMEOUT_SECONDS,
        "retry_timeout_seconds": RETRY_TIMEOUT_SECONDS,
        "command": [str(SIMULATOR), str(config_rel), str(taskset_rel), str(HORIZON_MS), "-t", str(result_rel), "--run-id", request.case_id, "--taskset-semantic-hash", hashlib.sha256(files[taskset_rel]).hexdigest(), "--b4-observability-summary", "--b4-summary-horizon", str(HORIZON_MS), "--b4-observability-contract-version", "2"],
    }


__all__ = [
    "ALGORITHMS", "ALGORITHM_CLI", "B4Request", "GRID", "HORIZON_MS",
    "PHASE_COUNTS", "PROCESSORS", "TASK_COUNT", "generate_base_taskset",
    "iter_requests", "materialize_request", "request_plan", "source_energy",
    "validate_base_taskset",
]
