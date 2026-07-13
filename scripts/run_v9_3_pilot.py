#!/usr/bin/env python3
"""Run the frozen ASAP-BLOCK v9.3 five-variant paper-pipeline pilot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asap_block_rta as legacy_rta
import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner
from asap_block_v1_3_12_schema_binding import V1312SchemaBinding


THEORY_SHA256 = taskset.THEORY_DOCUMENT_SHA256
CONTRACT_ZIP = PROJECT_ROOT / "docs" / "ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包.zip"
TASK_GENERATOR = PROJECT_ROOT / "global_task_generator.py"
VARIANT_ORDER = production_runner.VARIANT_ORDER
PILOT_TASKSET_COLUMNS = (
    "generation_seed", "taskset_id", "U_norm", "actual_total_utilization",
    "E0", "analysis_variant", "method_role", "solver_status",
    "certification_status", "taskset_proven", "first_failed_priority",
    "task_count", "candidate_found_task_count", "certified_task_count",
    "wall_clock_runtime_seconds", "cpu_runtime_seconds", "timeout",
    "numeric_error", "internal_conformance_failure", "dependency_status",
    "dominance_status", "fixed_carry_in_interface_status",
    "source_analysis_id", "source_vector_hash", "target_carry_in_vector_hash",
    "analysis_id", "schema_serialized", "service_curve_status",
)
PILOT_TASK_COLUMNS = (
    "generation_seed", "taskset_id", "U_norm", "E0", "analysis_variant",
    "method_role", "analysis_id", "task_id", "priority_rank", "C", "D",
    "T", "P", "task_solver_status", "task_certification_status",
    "candidate_response_time", "closing_w", "witness_h", "checked_w_count",
    "checked_h_count", "checked_q_count", "envelope_calls",
    "failure_reason_code", "failure_detail", "dominance_status",
    "source_analysis_id", "carry_in_vector_hash",
)
GENERATED_COLUMNS = (
    "generation_seed", "taskset_id", "U_norm", "U_norm_index", "E0",
    "E0_index", "taskset_index", "target_total_utilization",
    "actual_total_utilization", "utilization_error_total", "task_count",
    "taskset_semantic_hash", "priority_rank_hash", "power_vector_hash",
    "generation_status", "generation_runtime_seconds", "task_input_json",
)
FAILURE_COLUMNS = (
    "severity", "stage", "taskset_id", "analysis_variant", "code", "detail",
    "traceback", "input_file",
)
DOMINANCE_COLUMNS = (
    "taskset_id", "U_norm", "E0", "relation", "task_id", "priority_rank",
    "complete_candidate", "local_candidate", "improvement", "status",
    "common_task_count", "tighter_count", "equal_count", "violation_count",
    "maximum_improvement", "mean_improvement",
)


class PilotError(RuntimeError):
    """A fail-closed pilot pipeline error."""


@dataclass(frozen=True)
class GeneratedTaskset:
    seed: int
    taskset_id: str
    u_norm: str
    u_norm_index: int
    e0: int
    e0_index: int
    taskset_index: int
    target_total_utilization: Fraction
    actual_total_utilization: Fraction
    semantic_hash: str
    priority_hash: str
    power_hash: str
    tasks: Tuple[core.V93Task, ...]
    task_payload: Tuple[Mapping[str, Any], ...]
    generation_runtime_seconds: float


@dataclass(frozen=True)
class AnalysisExecution:
    result: Optional[taskset.TasksetAnalysisResult]
    wall_seconds: float
    cpu_seconds: float
    outer_timeout: bool
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    traceback_text: Optional[str] = None


def _canonical(value: Any) -> Any:
    if isinstance(value, Fraction):
        return _fraction_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else "{}/{}".format(
        value.numerator, value.denominator
    )


def _decimal_runtime(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        raise PilotError("runtime must be finite and non-negative")
    return format(value, ".9f")


def _canonical_runtime(value: float) -> str:
    return _fraction_text(Fraction(_decimal_runtime(value)))


def derive_seed(base_seed: int, u_index: int, e0_index: int, taskset_index: int) -> int:
    """Derive a stable generator seed without Python's process-random hash."""

    material = "{}|{}|{}|{}".format(base_seed, u_index, e0_index, taskset_index)
    digest = hashlib.sha256(material.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % 2147483647


def load_pilot_config(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise PilotError("pilot config must be a YAML mapping")
    generation = config.get("task_generation")
    analysis = config.get("analysis")
    energy = config.get("energy_model")
    if not all(isinstance(item, dict) for item in (generation, analysis, energy)):
        raise PilotError("pilot config is missing task_generation/analysis/energy_model")
    expected_variants = [variant.name for variant in VARIANT_ORDER]
    if analysis.get("variants") != expected_variants:
        raise PilotError("analysis variants must equal {}".format(expected_variants))
    frozen = {
        "task_n": 10, "M": 4, "deadline_mode": "implicit",
        "task_p_min": 40, "task_p_max": 200, "wcet_rounding": "compensated",
        "actual_utilization_tolerance_total": 0.01,
        "normalized_utilizations": [0.2, 0.4, 0.6],
        "initial_energy_lower_bounds": [0, 1], "num_tasksets_per_cell": 10,
        "smoke_tasksets_per_cell": 2, "base_seed": 930012,
    }
    for key, expected in frozen.items():
        if generation.get(key) != expected:
            raise PilotError("frozen task_generation.{} must be {!r}".format(key, expected))
    if config.get("rta_version") != "v9.3" or str(config.get("contract_version")) != "1.3.12":
        raise PilotError("pilot requires rta_version v9.3 and contract v1.3.12")
    timeout = analysis.get("timeout_seconds_per_configuration")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise PilotError("configuration timeout must be positive")
    if analysis.get("max_workers") != 1:
        raise PilotError("paper runtime pilot requires max_workers=1")
    if energy.get("energy_numeric_mode") != "EXACT_RATIONAL":
        raise PilotError("v9.3 pilot requires EXACT_RATIONAL")
    return config


def _prepare_system_config(config: Mapping[str, Any], output_root: Path) -> Path:
    energy_config = config["energy_model"]
    template = PROJECT_ROOT / str(energy_config["system_template"])
    with template.open("r", encoding="utf-8") as handle:
        system = yaml.safe_load(handle)
    island = system["cpu_islands"][0]
    island["numcpus"] = int(config["task_generation"]["M"])
    island.setdefault("kernel", {})["scheduler"] = "asap_block"
    energy = system.setdefault("energy_management", {})
    capacity = energy_config["battery_capacity_j"]
    ratio = energy_config["simulation_initial_energy_ratio"]
    energy["initial_energy"] = capacity * ratio
    energy["max_energy"] = capacity
    energy["use_real_solar_data"] = bool(energy_config["use_real_solar_data"])
    energy["time_of_day_ms"] = int(energy_config["solar_time_ms"])
    scale = float(energy_config["harvesting_scale"])
    energy["harvesting_scale"] = scale
    energy["base_harvesting_rate"] = float(energy.get("base_harvesting_rate", 0.054)) * scale
    path = output_root / "pilot_system_config.yaml"
    path.write_text(
        yaml.safe_dump(system, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def _build_exact_service_curve(
    system_path: Path, config: Mapping[str, Any]
) -> Tuple[Tuple[Fraction, ...], str, str]:
    system = legacy_rta.load_system_config(str(system_path))
    horizon = int(config["energy_model"]["service_curve_horizon_ms"])
    trace = legacy_rta._harvest_trace_from_config(system, horizon)
    curve = legacy_rta.build_energy_service_curve(trace, horizon)
    required = int(config["task_generation"]["task_p_max"]) - 1
    beta = tuple(Fraction(str(curve[index])) for index in range(required + 1))
    frozen = core.validate_service_curve_v9_3(beta, required)
    raw_spec = {
        "profile": config["energy_model"]["service_curve_profile"],
        "horizon_ms": horizon,
        "solar_time_ms": config["energy_model"]["solar_time_ms"],
        "harvesting_scale": config["energy_model"]["harvesting_scale"],
        "validated_prefix": [_fraction_text(item) for item in frozen],
    }
    return frozen, _domain_hash("ASAP_BLOCK:PILOT:SERVICE_CURVE:v9.3", raw_spec), json.dumps(
        raw_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _task_workload(raw_task: Mapping[str, Any]) -> str:
    params = str(raw_task.get("params", ""))
    for part in params.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip() == "workload":
                return value.strip().strip('"')
    raise PilotError("generated task has no workload parameter")


def _generate_taskset(
    config: Mapping[str, Any], system_path: Path, beta_hash: str,
    u_norm: Any, u_index: int, e0: int, e0_index: int, taskset_index: int,
) -> GeneratedTaskset:
    generation = config["task_generation"]
    seed = derive_seed(generation["base_seed"], u_index, e0_index, taskset_index)
    target = Fraction(str(u_norm)) * int(generation["M"])
    with tempfile.TemporaryDirectory(prefix="v9_3_pilot_generation_") as temp:
        task_path = Path(temp) / "tasks.yaml"
        command = [
            sys.executable, str(TASK_GENERATOR), "-n", str(generation["task_n"]),
            "-u", format(float(target), ".15g"), "-p", str(generation["task_p_min"]),
            "-P", str(generation["task_p_max"]), "-c", str(generation["M"]),
            "--seed", str(seed), "-s", str(system_path), "-o", str(task_path),
            "--min-task-util", str(generation["min_task_util"]),
            "--max-task-util", str(generation["max_task_util"]),
            "--wcet-rounding", str(generation["wcet_rounding"]),
            "--actual-utilization-tolerance-total",
            str(generation["actual_utilization_tolerance_total"]),
        ]
        started = time.perf_counter()
        completed = subprocess.run(
            command, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=60, check=False,
        )
        elapsed = time.perf_counter() - started
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
            raise PilotError("task generation failed with code {}: {}".format(
                completed.returncode, detail
            ))
        legacy_tasks = legacy_rta.rm_order(legacy_rta.load_tasks(str(task_path)))
        with task_path.open("r", encoding="utf-8") as handle:
            raw_document = yaml.safe_load(handle)
    raw_by_name = {str(item["name"]): item for item in raw_document["taskset"]}
    system = legacy_rta.load_system_config(str(system_path))
    tasks: List[core.V93Task] = []
    payload: List[Mapping[str, Any]] = []
    for rank, legacy_task in enumerate(legacy_tasks):
        raw = raw_by_name[legacy_task.name]
        power = Fraction(str(system.task_energy_per_tick(legacy_task.workload)))
        task_id = str(rank)
        tasks.append(core.V93Task(
            task_id, legacy_task.wcet, legacy_task.deadline, legacy_task.period, power
        ))
        payload.append({
            "task_id": task_id, "source_name": legacy_task.name,
            "priority_rank": rank, "C": legacy_task.wcet,
            "D": legacy_task.deadline, "T": legacy_task.period,
            "P": _fraction_text(power), "workload": _task_workload(raw),
            "arrival_offset": int(
                next((part.split("=", 1)[1] for part in str(raw.get("params", "")).split(",")
                      if part.strip().startswith("arrival_offset=")), "0")
            ),
        })
    actual = sum(Fraction(item.wcet, item.period) for item in tasks)
    tolerance = Fraction(str(generation["actual_utilization_tolerance_total"]))
    if abs(actual - target) > tolerance:
        raise PilotError("generated utilization is outside the frozen tolerance")
    semantic_preimage = {
        "M": generation["M"], "tasks": payload,
        "service_curve_hash": beta_hash,
    }
    semantic_hash = _domain_hash("ASAP_BLOCK:PILOT:TASKSET_SEMANTIC:v9.3", semantic_preimage)
    priority_hash = _domain_hash(
        "ASAP_BLOCK:PILOT:PRIORITY:v9.3",
        [{"task_id": item["task_id"], "priority_rank": item["priority_rank"]} for item in payload],
    )
    power_hash = _domain_hash(
        "ASAP_BLOCK:PILOT:POWER_VECTOR:v9.3",
        [{"task_id": item["task_id"], "P": item["P"]} for item in payload],
    )
    taskset_id = "v93-u{}-e{}-t{:02d}-{}".format(
        u_index, e0_index, taskset_index, semantic_hash[:12]
    )
    return GeneratedTaskset(
        seed, taskset_id, format(float(u_norm), ".15g"), u_index, int(e0),
        e0_index, taskset_index, target, actual, semantic_hash, priority_hash,
        power_hash, tuple(tasks), tuple(payload), elapsed,
    )


def _dependency_context(
    generated: GeneratedTaskset, beta_hash: str, config_hash: str,
) -> taskset.DependencyContext:
    return taskset.DependencyContext(
        taskset_identity=generated.semantic_hash,
        task_definitions_identity=_domain_hash(
            "ASAP_BLOCK:PILOT:TASK_DEFINITIONS:v9.3", generated.task_payload
        ),
        priority_order_identity=generated.priority_hash,
        e0_canonical_identity=_domain_hash("ASAP_BLOCK:PILOT:E0:v9.3", generated.e0),
        service_curve_identity=beta_hash,
        power_vector_identity=generated.power_hash,
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=THEORY_SHA256,
        fixed_carry_in_interface_sha256=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        formal_contract_identity=None,
    )


def _analysis_worker(connection: Any, request: production_runner.V93DispatchRequest) -> None:
    cpu_started = time.process_time()
    try:
        result = production_runner.dispatch_rta_version(
            production_runner.V93_DISPATCH_VERSION, v93_request=request
        )
        connection.send(("ok", result, time.process_time() - cpu_started))
    except BaseException as exc:  # The parent records the traceback out of formal failure detail.
        connection.send((
            "error", type(exc).__name__, str(exc), traceback.format_exc(),
            time.process_time() - cpu_started,
        ))
    finally:
        connection.close()


def execute_analysis(
    request: production_runner.V93DispatchRequest, timeout_seconds: float,
) -> AnalysisExecution:
    """Run one production dispatch behind a hard per-configuration wall timeout."""

    context = multiprocessing.get_context("fork")
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(target=_analysis_worker, args=(sending, request))
    started = time.perf_counter()
    process.start()
    sending.close()
    try:
        if not receiving.poll(timeout_seconds):
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            return AnalysisExecution(
                None, time.perf_counter() - started, 0.0, True,
                "ConfigurationTimeout", "hard per-configuration timeout", None,
            )
        payload = receiving.recv()
    finally:
        receiving.close()
    process.join(5)
    elapsed = time.perf_counter() - started
    if process.is_alive():
        process.terminate()
        process.join(5)
        return AnalysisExecution(
            None, elapsed, 0.0, True, "WorkerDidNotExit",
            "analysis worker did not exit after returning a payload", None,
        )
    if payload[0] == "ok":
        return AnalysisExecution(payload[1], elapsed, payload[2], False)
    return AnalysisExecution(
        None, elapsed, payload[4], False, payload[1], payload[2], payload[3]
    )


def _analysis_input(
    generated: GeneratedTaskset, beta: Tuple[Fraction, ...], beta_hash: str,
    config_hash: str, timeout_seconds: float,
) -> taskset.TasksetAnalysisInput:
    return taskset.TasksetAnalysisInput(
        generated.tasks, int(len(generated.tasks) and 4), Fraction(generated.e0),
        beta, _dependency_context(generated, beta_hash, config_hash),
        timeout_seconds=timeout_seconds,
    )


def _analysis_ids(generated: GeneratedTaskset) -> Dict[taskset.AnalysisVariant, str]:
    return {
        variant: _domain_hash(
            "ASAP_BLOCK:PILOT:ANALYSIS_RUN:v9.3",
            {"taskset_id": generated.taskset_id, "variant": variant.value},
        )
        for variant in VARIANT_ORDER
    }


def _hash_vector(entries: Iterable[Tuple[str, int]]) -> Optional[str]:
    values = tuple(sorted((str(name), int(value)) for name, value in entries))
    if not values:
        return None
    return _domain_hash("ASAP_BLOCK:PILOT:CARRY_IN_VECTOR:v9.3", values)


def validate_analysis_result(
    result: taskset.TasksetAnalysisResult, generated: GeneratedTaskset,
    source: Optional[taskset.TasksetAnalysisResult] = None,
) -> None:
    if result.taskset_proven != (
        result.certification_status is taskset.AnalysisCertificationStatus.CERTIFIED_TASKSET
    ):
        raise PilotError("taskset_proven/certification contradiction")
    if result.n_tasks_total != len(generated.tasks):
        raise PilotError("analysis task count differs from generated taskset")
    ids = [record.task_id for record in result.task_records]
    if len(ids) != len(set(ids)) or set(ids) != {item.name for item in generated.tasks}:
        raise PilotError("missing or duplicate task result")
    definitions = {item.name: item for item in generated.tasks}
    for record in result.task_records:
        definition = definitions[record.task_id]
        if record.certification_status is taskset.TaskCertificationStatus.CERTIFIED and (
            record.solver_status is not taskset.TaskSolverStatus.CANDIDATE_FOUND
        ):
            raise PilotError("certified task lacks a candidate")
        candidate = record.candidate_response_time
        if candidate is not None:
            if isinstance(candidate, bool) or not isinstance(candidate, int):
                raise PilotError("candidate is not a finite integer")
            if candidate < definition.wcet:
                raise PilotError("candidate is below C")
            if record.certification_status is taskset.TaskCertificationStatus.CERTIFIED and candidate > definition.deadline:
                raise PilotError("certified candidate exceeds D")
    if result.analysis_variant is taskset.AnalysisVariant.LOC_THETA_CW:
        if source is None:
            raise PilotError("LOC_THETA_CW has no source")
        source_certified = production_runner._source_is_jointly_certified(source)
        if not source_certified:
            if result.solver_status is not taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY:
                raise PilotError("uncertified source did not produce dependency N/A")
            if result.certification_status is not taskset.AnalysisCertificationStatus.NOT_APPLICABLE:
                raise PilotError("uncertified source target has non-N/A certification")
        else:
            if result.dependency_check_status is not taskset.DependencyVectorCheckStatus.VALID:
                raise PilotError("certified source dependency is not VALID")
            source_vector = tuple(
                sorted((record.task_id, record.candidate_response_time) for record in source.task_records)
            )
            if result.source_candidate_vector != source_vector:
                raise PilotError("source/target frozen carry-in vector mismatch")


def _task_definitions(generated: GeneratedTaskset) -> Dict[str, Dict[str, Any]]:
    return {
        str(item["task_id"]): {
            "taskset_id": generated.taskset_id,
            "task_id": int(item["task_id"]),
            "C_i": item["C"], "T_i": item["T"], "D_i": item["D"],
            "P_raw": item["P"], "P_analysis": item["P"],
            "priority_rank": item["priority_rank"],
            "power_latent_value": item["P"],
            "P_analysis_scaled": None, "P_rounding_mode": None,
        }
        for item in generated.task_payload
    }


def _analysis_base(
    binding: V1312SchemaBinding, generated: GeneratedTaskset,
    result: taskset.TasksetAnalysisResult, wall: float, cpu: float,
    config_hash: str, beta_hash: str, beta_spec: str,
) -> Dict[str, Any]:
    row = binding.empty_row("per_taskset_results.csv")
    total = _fraction_text(generated.actual_total_utilization)
    target = _fraction_text(generated.target_total_utilization)
    e0 = str(generated.e0)
    total_power = sum(item.power for item in generated.tasks)
    generator_hash = _sha256_file(TASK_GENERATOR)
    row.update(
        run_phase="PILOT",
        request_id=_domain_hash("ASAP_BLOCK:PILOT:REQUEST:v9.3", result.analysis_id),
        build_identity_hash=_domain_hash(
            "ASAP_BLOCK:PILOT:BUILD:v9.3", {"head": _git_head(), "config": config_hash}
        ),
        rta_implementation_hash=_domain_hash(
            "ASAP_BLOCK:PILOT:RTA_IMPLEMENTATION:v9.3",
            [_sha256_file(PROJECT_ROOT / name) for name in (
                "asap_block_rta_v9_3.py", "asap_block_rta_v9_3_taskset.py",
                "asap_block_v9_3_runner.py",
            )],
        ),
        generation_request_id=_domain_hash(
            "ASAP_BLOCK:PILOT:GENERATION_RESULT:v9.3", generated.taskset_id
        ),
        taskset_id=generated.taskset_id,
        taskset_materialization_request_id=_domain_hash(
            "ASAP_BLOCK:PILOT:MATERIALIZATION:v9.3", generated.taskset_id
        ),
        generator_contract_hash=generator_hash,
        experiment_config_version="V9_3_PILOT_V1",
        experiment_config_hash=config_hash,
        M=4, n=len(generated.tasks),
        target_total_utilization=target, actual_total_utilization=total,
        target_rho_p=target, actual_rho_p=total,
        target_rho_e="0", actual_rho_e_raw="0", actual_rho_e_analysis="0",
        rho_e_tolerance="0", rho_e_tolerance_mode="EXACT",
        rho_e_parameterization_status="ACCEPTED", numeric_coverage_status="VALID",
        service_rate_reference="0", service_rate_r_raw="0",
        service_curve_integerization_mode="EXACT", power_scale_alpha="1",
        target_power_demand=_fraction_text(total_power),
        actual_power_demand_raw=_fraction_text(total_power),
        actual_power_demand_analysis=_fraction_text(total_power),
        target_service_latency_ratio="0", realized_service_latency_L=0,
        realized_service_latency_ratio="0", power_latent_seed=str(generated.seed),
        power_latent_vector_hash=generated.power_hash,
        power_latent_mapping_version="SCHEDULER_WORKLOAD_MODEL_V1",
        priority_reference_delta="RM", priority_rank_reference_hash=generated.priority_hash,
        E0_target_raw=e0, E0_analysis_effective=e0, E0_rounding_error="0",
        target_epsilon_0="0", realized_epsilon_0_analysis="0",
        e0_parameterization_policy="EXACT_GRID", e0_parameterization_status="ACCEPTED",
        theorem_conditioning_mode=(
            "UNCONDITIONAL_E0_ZERO" if generated.e0 == 0 else "CONDITIONAL_E0_POSITIVE"
        ),
        service_latency_L=0, service_curve_raw_spec=beta_spec,
        runtime_wall=_canonical_runtime(wall), runtime_cpu=_canonical_runtime(cpu),
        rta_formula_version="v9.3", theory_document_sha256=THEORY_SHA256,
        fixed_carry_in_corollary_hash=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        taskset_semantic_hash=generated.semantic_hash,
        priority_rank_hash=generated.priority_hash,
        power_vector_raw_hash=generated.power_hash,
        analysis_E0_canonical_hash=result.dependency_context.e0_canonical_identity,
        analysis_power_vector_canonical_hash=generated.power_hash,
        analysis_service_curve_canonical_hash=beta_hash,
        energy_numeric_mode="EXACT_RATIONAL", energy_demand_rounding="EXACT",
        energy_supply_rounding="EXACT", numeric_integer_type="ARBITRARY_PRECISION_INTEGER",
        numeric_range_check_status="VALID", service_curve_raw_hash=beta_hash,
        plan_context_hash=_domain_hash(
            "ASAP_BLOCK:PILOT:PLAN_CONTEXT:v9.3", {"config": config_hash, "theory": THEORY_SHA256}
        ),
        analysis_energy_unit_hash=_domain_hash(
            "ASAP_BLOCK:PILOT:ENERGY_UNIT:v9.3", "joule_per_1ms_tick"
        ),
        formal_contract_hash=None, formal_contract_version=None,
    )
    return row


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT), capture_output=True,
        text=True, check=True,
    )
    return completed.stdout.strip()


def _project_result(
    generated: GeneratedTaskset, execution: AnalysisExecution,
    serialized: production_runner.SerializedAnalysis,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    result = serialized.analysis_result
    source_hash = _hash_vector(result.source_candidate_vector)
    target_hash = source_hash if result.analysis_variant is taskset.AnalysisVariant.LOC_THETA_CW else None
    taskset_row = {
        "generation_seed": generated.seed, "taskset_id": generated.taskset_id,
        "U_norm": generated.u_norm,
        "actual_total_utilization": _fraction_text(generated.actual_total_utilization),
        "E0": generated.e0, "analysis_variant": result.analysis_variant.name,
        "method_role": result.method_role.value, "solver_status": result.solver_status.value,
        "certification_status": result.certification_status.value,
        "taskset_proven": result.taskset_proven,
        "first_failed_priority": result.first_failed_priority,
        "task_count": result.n_tasks_total,
        "candidate_found_task_count": result.n_tasks_candidate_found,
        "certified_task_count": result.n_tasks_certified,
        "wall_clock_runtime_seconds": _decimal_runtime(execution.wall_seconds),
        "cpu_runtime_seconds": _decimal_runtime(execution.cpu_seconds),
        "timeout": result.solver_status is taskset.AnalysisSolverStatus.TIMEOUT,
        "numeric_error": result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR,
        "internal_conformance_failure": (
            result.solver_status is taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE
        ),
        "dependency_status": result.dependency_check_status.value,
        "dominance_status": result.dominance_invariant_status.value,
        "fixed_carry_in_interface_status": result.fixed_carry_in_interface_status.value,
        "source_analysis_id": result.source_analysis_id,
        "source_vector_hash": source_hash,
        "target_carry_in_vector_hash": target_hash,
        "analysis_id": result.analysis_id, "schema_serialized": True,
        "service_curve_status": "VALID",
    }
    definition_by_id = {str(item["task_id"]): item for item in generated.task_payload}
    task_rows = []
    for canonical in serialized.task_rows:
        task_id = str(canonical["task_id"])
        definition = definition_by_id[task_id]
        task_rows.append({
            "generation_seed": generated.seed, "taskset_id": generated.taskset_id,
            "U_norm": generated.u_norm, "E0": generated.e0,
            "analysis_variant": result.analysis_variant.name,
            "method_role": result.method_role.value, "analysis_id": result.analysis_id,
            "task_id": task_id, "priority_rank": canonical["priority_rank"],
            "C": definition["C"], "D": definition["D"], "T": definition["T"],
            "P": definition["P"], "task_solver_status": canonical["task_solver_status"],
            "task_certification_status": canonical["task_certification_status"],
            "candidate_response_time": canonical["candidate_response_time"],
            "closing_w": canonical["closing_w"], "witness_h": canonical["witness_h"],
            "checked_w_count": canonical["w_values_checked"],
            "checked_h_count": canonical["h_values_checked"],
            "checked_q_count": canonical["q_values_checked"],
            "envelope_calls": canonical["envelope_call_count"],
            "failure_reason_code": canonical["task_failure_reason_code"],
            "failure_detail": canonical["task_failure_detail"],
            "dominance_status": canonical["dominance_invariant_status"],
            "source_analysis_id": canonical["source_analysis_run_id"],
            "carry_in_vector_hash": canonical["carry_in_vector_hash"],
        })
    return taskset_row, task_rows


def dominance_rows(
    generated: GeneratedTaskset,
    results: Mapping[taskset.AnalysisVariant, taskset.TasksetAnalysisResult],
) -> List[Dict[str, Any]]:
    relations = (
        ("DEADLINE_CARRY_IN", taskset.AnalysisVariant.CW_D, taskset.AnalysisVariant.LOC_D),
        ("FIXED_CW_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_CW),
        ("RECURSIVE_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_LOC),
    )
    rows: List[Dict[str, Any]] = []
    for relation, complete_variant, local_variant in relations:
        complete = {record.task_id: record for record in results[complete_variant].task_records}
        local = {record.task_id: record for record in results[local_variant].task_records}
        checks = []
        for task_id in sorted(complete, key=int):
            left = complete[task_id]
            right = local[task_id]
            if (
                left.solver_status is not taskset.TaskSolverStatus.CANDIDATE_FOUND
                or right.solver_status is not taskset.TaskSolverStatus.CANDIDATE_FOUND
            ):
                continue
            improvement = left.candidate_response_time - right.candidate_response_time
            status = "TIGHTER" if improvement > 0 else "EQUAL" if improvement == 0 else "VIOLATION"
            checks.append((task_id, left.priority_rank, left.candidate_response_time,
                           right.candidate_response_time, improvement, status))
        common = len(checks)
        tighter = sum(item[5] == "TIGHTER" for item in checks)
        equal = sum(item[5] == "EQUAL" for item in checks)
        violations = sum(item[5] == "VIOLATION" for item in checks)
        improvements = [item[4] for item in checks]
        maximum = max(improvements) if improvements else None
        mean = Fraction(sum(improvements), len(improvements)) if improvements else None
        for task_id, rank, left, right, improvement, status in checks:
            rows.append({
                "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
                "E0": generated.e0, "relation": relation, "task_id": task_id,
                "priority_rank": rank, "complete_candidate": left,
                "local_candidate": right, "improvement": improvement, "status": status,
                "common_task_count": common, "tighter_count": tighter,
                "equal_count": equal, "violation_count": violations,
                "maximum_improvement": maximum,
                "mean_improvement": _fraction_text(mean) if mean is not None else None,
            })
        if not checks:
            rows.append({
                "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
                "E0": generated.e0, "relation": relation, "task_id": None,
                "priority_rank": None, "complete_candidate": None,
                "local_candidate": None, "improvement": None, "status": "NOT_APPLICABLE",
                "common_task_count": 0, "tighter_count": 0, "equal_count": 0,
                "violation_count": 0, "maximum_improvement": None,
                "mean_improvement": None,
            })
        if violations:
            raise PilotError("P0 dominance violation in {} {}".format(
                generated.taskset_id, relation
            ))
    return rows


def _generated_row(generated: GeneratedTaskset) -> Dict[str, Any]:
    return {
        "generation_seed": generated.seed, "taskset_id": generated.taskset_id,
        "U_norm": generated.u_norm, "U_norm_index": generated.u_norm_index,
        "E0": generated.e0, "E0_index": generated.e0_index,
        "taskset_index": generated.taskset_index,
        "target_total_utilization": _fraction_text(generated.target_total_utilization),
        "actual_total_utilization": _fraction_text(generated.actual_total_utilization),
        "utilization_error_total": _fraction_text(
            generated.actual_total_utilization - generated.target_total_utilization
        ),
        "task_count": len(generated.tasks), "taskset_semantic_hash": generated.semantic_hash,
        "priority_rank_hash": generated.priority_hash,
        "power_vector_hash": generated.power_hash, "generation_status": "SUCCESS",
        "generation_runtime_seconds": _decimal_runtime(generated.generation_runtime_seconds),
        "task_input_json": json.dumps(
            list(generated.task_payload), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in columns})


def _write_canonical_tables(
    root: Path, binding: V1312SchemaBinding,
    canonical_tasksets: Sequence[Mapping[str, Any]],
    canonical_tasks: Sequence[Mapping[str, Any]],
    canonical_dependencies: Sequence[Mapping[str, Any]],
) -> None:
    canonical_root = root / "canonical_v1_3_12"
    canonical_root.mkdir(parents=True, exist_ok=True)
    for table_name, rows in (
        ("per_taskset_results.csv", canonical_tasksets),
        ("per_task_results.csv", canonical_tasks),
        ("rta_dependency_records.csv", canonical_dependencies),
    ):
        encoded = [binding.encode_row(table_name, row) for row in rows]
        _write_csv(canonical_root / table_name, binding.canonical_columns(table_name), encoded)


def _save_failure_input(root: Path, generated: Optional[GeneratedTaskset]) -> str:
    if generated is None:
        return ""
    path = root / "failure_input.json"
    path.write_text(
        json.dumps({
            "taskset_id": generated.taskset_id, "generation_seed": generated.seed,
            "U_norm": generated.u_norm, "E0": generated.e0,
            "tasks": list(generated.task_payload),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path.name


def run_pilot(
    config_path: Path, output_root: Path, mode: str,
) -> Dict[str, Any]:
    config = load_pilot_config(config_path)
    output_root = Path(output_root)
    if output_root.exists() and any(output_root.iterdir()):
        raise PilotError("output root already exists and is non-empty: {}".format(output_root))
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "pilot_config.yaml").write_bytes(Path(config_path).read_bytes())
    config_hash = _sha256_file(output_root / "pilot_config.yaml")
    system_path = _prepare_system_config(config, output_root)
    beta, beta_hash, beta_spec = _build_exact_service_curve(system_path, config)
    binding = V1312SchemaBinding()
    generated_rows: List[Dict[str, Any]] = []
    taskset_rows: List[Dict[str, Any]] = []
    task_rows: List[Dict[str, Any]] = []
    dominance: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    canonical_tasksets: List[Mapping[str, Any]] = []
    canonical_tasks: List[Mapping[str, Any]] = []
    canonical_dependencies: List[Mapping[str, Any]] = []
    seen_ids = set()
    hard_failure: Optional[BaseException] = None
    active_generated: Optional[GeneratedTaskset] = None
    active_variant: Optional[taskset.AnalysisVariant] = None
    generation = config["task_generation"]
    count = (
        generation["smoke_tasksets_per_cell"] if mode == "smoke"
        else generation["num_tasksets_per_cell"]
    )
    try:
        for u_index, u_norm in enumerate(generation["normalized_utilizations"]):
            for e0_index, e0 in enumerate(generation["initial_energy_lower_bounds"]):
                for taskset_index in range(count):
                    active_generated = _generate_taskset(
                        config, system_path, beta_hash, u_norm, u_index,
                        int(e0), e0_index, taskset_index,
                    )
                    if active_generated.taskset_id in seen_ids:
                        raise PilotError("duplicate taskset ID")
                    seen_ids.add(active_generated.taskset_id)
                    generated_rows.append(_generated_row(active_generated))
                    inp = _analysis_input(
                        active_generated, beta, beta_hash, config_hash,
                        float(config["analysis"]["timeout_seconds_per_configuration"]),
                    )
                    ids = _analysis_ids(active_generated)
                    results: Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult] = {}
                    serializations: Dict[taskset.AnalysisVariant, production_runner.SerializedAnalysis] = {}
                    for active_variant in VARIANT_ORDER:
                        source = results.get(taskset.AnalysisVariant.CW_THETA_CW)
                        dependency = taskset.DependencyVectorCheckStatus.NOT_CHECKED
                        request_source = None
                        if active_variant is taskset.AnalysisVariant.LOC_THETA_CW:
                            if source is None:
                                raise PilotError("dependent variant scheduled before its source")
                            dependency = (
                                taskset.DependencyVectorCheckStatus.VALID
                                if production_runner._source_is_jointly_certified(source)
                                else taskset.DependencyVectorCheckStatus.INVALID
                            )
                            request_source = source
                        request = production_runner.V93DispatchRequest(
                            ids[active_variant], active_variant, inp,
                            source=request_source, dependency_check_status=dependency,
                            configuration_timeout_seconds=float(
                                config["analysis"]["timeout_seconds_per_configuration"]
                            ),
                        )
                        execution = execute_analysis(
                            request,
                            float(config["analysis"]["timeout_seconds_per_configuration"]) + 2.0,
                        )
                        if execution.outer_timeout:
                            raise PilotError("hard per-configuration timeout")
                        if execution.result is None:
                            raise PilotError("analysis worker exception: {}: {}".format(
                                execution.exception_type, execution.exception_message
                            ))
                        result = execution.result
                        validate_analysis_result(result, active_generated, request_source)
                        source_serialized = (
                            serializations[taskset.AnalysisVariant.CW_THETA_CW]
                            if active_variant is taskset.AnalysisVariant.LOC_THETA_CW else None
                        )
                        serialized = production_runner.serialize_taskset_analysis_v1_3_12(
                            result, binding,
                            _analysis_base(
                                binding, active_generated, result,
                                execution.wall_seconds, execution.cpu_seconds,
                                config_hash, beta_hash, beta_spec,
                            ),
                            _task_definitions(active_generated),
                            source=source_serialized,
                        )
                        results[active_variant] = result
                        serializations[active_variant] = serialized
                        projected_taskset, projected_tasks = _project_result(
                            active_generated, execution, serialized
                        )
                        taskset_rows.append(projected_taskset)
                        task_rows.extend(projected_tasks)
                        canonical_tasksets.append(dict(serialized.taskset_row))
                        canonical_tasks.extend(dict(row) for row in serialized.task_rows)
                        canonical_dependencies.extend(dict(row) for row in serialized.dependency_rows)
                    if tuple(results) != VARIANT_ORDER:
                        raise PilotError("taskset does not have exactly five ordered variants")
                    dominance.extend(dominance_rows(active_generated, results))
    except BaseException as exc:
        hard_failure = exc
        input_file = _save_failure_input(output_root, active_generated)
        failures.append({
            "severity": "P0", "stage": "generation" if active_generated is None else "analysis",
            "taskset_id": active_generated.taskset_id if active_generated else None,
            "analysis_variant": active_variant.name if active_variant else None,
            "code": type(exc).__name__, "detail": str(exc),
            "traceback": traceback.format_exc(), "input_file": input_file,
        })
    _write_csv(output_root / "generated_tasksets.csv", GENERATED_COLUMNS, generated_rows)
    _write_csv(output_root / "per_taskset_results.csv", PILOT_TASKSET_COLUMNS, taskset_rows)
    _write_csv(output_root / "per_task_results.csv", PILOT_TASK_COLUMNS, task_rows)
    _write_csv(output_root / "dominance_checks.csv", DOMINANCE_COLUMNS, dominance)
    _write_csv(output_root / "failures.csv", FAILURE_COLUMNS, failures)
    _write_canonical_tables(
        output_root, binding, canonical_tasksets, canonical_tasks, canonical_dependencies
    )
    from scripts.analyze_v9_3_pilot import finalize_pilot_outputs

    summary = finalize_pilot_outputs(output_root, mode=mode, hard_failure=hard_failure is not None)
    if hard_failure is not None:
        raise PilotError("pilot stopped after fail-closed error: {}".format(hard_failure))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "v9_3_pilot.yaml")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("smoke", "full"), required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_pilot(args.config, args.output_root, args.mode)
    except Exception as exc:
        print("v9.3 pilot failed: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
