"""ASAP-BLOCK simulator adapter for frozen v9.3 tasksets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, Mapping, Optional, Sequence

import yaml

import asap_block_rta as legacy_rta

from .censoring import next_horizon
from .config import canonical_json, domain_hash, fraction_text
from .result_writer import atomic_write_json, atomic_write_text
from .simulation_result import (
    JobObservation,
    SimulationResult,
    SimulationStatus,
    SimulationTraceError,
    TaskObservation,
    parse_simulation_trace,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_TRACE_SCHEMA_VERSION = 2


class SimulationConfigurationError(RuntimeError):
    """Raised before execution when RTA/simulation inputs cannot be paired."""


@dataclass(frozen=True)
class SimulationExecution:
    simulation_id: str
    result: SimulationResult
    runtime_seconds: float
    attempt_count: int
    horizons_attempted: tuple[int, ...]
    system_config_path: Path
    taskset_path: Path
    retained_trace_path: Optional[Path]
    stdout_tail: str = ""
    stderr_tail: str = ""


def simulation_identity(
    cell_id: str,
    taskset_hash: str,
    exact_e0: Fraction,
    simulation_config: Mapping[str, Any],
) -> str:
    return domain_hash(
        "ASAP_BLOCK:V9.3:CORE3_SIMULATION:v1",
        {
            "cell_id": cell_id,
            "taskset_hash": taskset_hash,
            "exact_e0": fraction_text(exact_e0),
            "simulation": simulation_config,
        },
    )


def _taskset_document(task_payload: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    tasks = []
    expected_ranks = list(range(len(task_payload)))
    ranks = [int(row["priority_rank"]) for row in task_payload]
    if ranks != expected_ranks:
        raise SimulationConfigurationError(
            "frozen task payload is not in contiguous priority order"
        )
    for row in task_payload:
        task_id = str(row["task_id"])
        c_value, d_value, t_value = int(row["C"]), int(row["D"]), int(row["T"])
        if not 0 < c_value <= d_value <= t_value:
            raise SimulationConfigurationError("frozen task violates 0 < C <= D <= T")
        workload = str(row["workload"])
        offset = int(row.get("arrival_offset", 0))
        tasks.append({
            "name": f"v93_task_{task_id}",
            "iat": t_value,
            "deadline": d_value,
            "runtime": c_value,
            "startcpu": 0,
            "ph": offset,
            "code": [f"fixed({c_value}, {workload})"],
            "params": (
                f"period={t_value},wcet={c_value},"
                f"arrival_offset={offset},workload={workload}"
            ),
        })
    return {"taskset": tasks, "resources": []}


def _render_taskset_yaml(task_payload: Sequence[Mapping[str, Any]]) -> str:
    """Render the conservative YAML subset consumed by RTSim's C++ parser."""

    document = _taskset_document(task_payload)
    lines = ["taskset:"]
    for task in document["taskset"]:
        lines.extend([
            f"  - name: {task['name']}",
            f"    iat: {task['iat']}",
            f"    runtime: {task['runtime']}",
            f"    startcpu: {task['startcpu']}",
            f"    deadline: {task['deadline']}",
            f"    ph: {task['ph']}",
            f"    params: \"{task['params']}\"",
            "    code:",
            *[f"      - {instruction}" for instruction in task["code"]],
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def materialize_simulation_inputs(
    base_system_path: Path,
    destination: Path,
    task_payload: Sequence[Mapping[str, Any]],
    *,
    processors: int,
    initial_battery: Fraction,
    battery_capacity: Fraction,
    scheduler_id: str = "gpfp_asap_block",
) -> tuple[Path, Path]:
    """Write a scheduler-only projection without changing frozen semantics."""

    try:
        source_text = base_system_path.read_text(encoding="utf-8")
        system = yaml.safe_load(source_text)
    except (OSError, yaml.YAMLError) as exc:
        raise SimulationConfigurationError(f"cannot load base system: {exc}") from exc
    if not isinstance(system, dict) or not isinstance(system.get("cpu_islands"), list):
        raise SimulationConfigurationError("base system has no CPU island")
    replacements = {
        "numcpus": str(processors),
        "scheduler": scheduler_id,
        "initial_energy": format(float(initial_battery), ".17g"),
        "max_energy": format(float(battery_capacity), ".17g"),
    }
    seen = {key: 0 for key in replacements}
    speed_parameter_count = 0
    rendered_lines = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("speed_params:"):
            indent = line[:len(line) - len(line.lstrip())]
            rendered_lines.append(f"{indent}speed_params: [1, 0, 0, 0]")
            speed_parameter_count += 1
            continue
        matched = None
        for key in replacements:
            if stripped.startswith(key + ":"):
                matched = key
                break
        if matched is None:
            rendered_lines.append(line)
            continue
        # These four keys occur exactly once in the audited system template.
        indent = line[:len(line) - len(line.lstrip())]
        comment = ""
        if "#" in line:
            comment = "  #" + line.split("#", 1)[1]
        rendered_lines.append(
            f"{indent}{matched}: {replacements[matched]}{comment}"
        )
        seen[matched] += 1
    if any(count != 1 for count in seen.values()) or speed_parameter_count == 0:
        raise SimulationConfigurationError(
            "system template replacement counts are invalid: "
            f"{seen}, speed_params={speed_parameter_count}"
        )

    destination.mkdir(parents=True, exist_ok=True)
    system_path = destination / "system_config.yaml"
    taskset_path = destination / "taskset.yaml"
    atomic_write_text(
        system_path,
        "\n".join(rendered_lines) + "\n",
    )
    atomic_write_text(
        taskset_path,
        _render_taskset_yaml(task_payload),
    )
    return system_path, taskset_path


def validate_no_overflow_guard(
    system_path: Path,
    maximum_horizon: int,
    *,
    initial_battery: Fraction,
    battery_capacity: Fraction,
) -> Fraction:
    """Return offered harvest after proving capacity cannot clip it."""

    try:
        system = legacy_rta.load_system_config(str(system_path))
        harvest = legacy_rta._harvest_trace_from_config(system, maximum_horizon)
    except Exception as exc:
        raise SimulationConfigurationError(
            f"cannot construct paired simulation harvest trace: {exc}"
        ) from exc
    exact_harvest = sum((Fraction(str(value)) for value in harvest), Fraction(0))
    required = initial_battery + exact_harvest
    if battery_capacity < required:
        raise SimulationConfigurationError(
            "finite battery can clip configured harvest through maximum_horizon: "
            f"capacity={fraction_text(battery_capacity)} required_at_least="
            f"{fraction_text(required)}"
        )
    return exact_harvest


def _failure_result(
    status: SimulationStatus,
    reason: str,
    horizon: int,
    scheduler_id: str = "gpfp_asap_block",
) -> SimulationResult:
    return SimulationResult(
        status, reason, horizon, (), (), False, None, {}, 2,
        scheduler_id, False, reason,
    )


def simulation_result_to_dict(result: SimulationResult) -> Dict[str, Any]:
    trace_schema_version = _required_trace_schema_version({
        "trace_schema_version": result.trace_schema_version,
    })
    return {
        "status": result.status.value,
        "reason": result.reason,
        "horizon": result.horizon,
        "jobs": [job.row() for job in result.jobs],
        "tasks": [task.row() for task in result.tasks],
        "release_e0_valid": result.release_e0_valid,
        "minimum_release_energy_j": result.minimum_release_energy_j,
        "observed_task_power_j_per_tick": dict(result.observed_task_power_j_per_tick),
        "trace_schema_version": trace_schema_version,
        "configured_scheduler": result.configured_scheduler,
        "simulation_completed": result.simulation_completed,
        "completion_reason": result.completion_reason,
        "metrics": dict(result.metrics),
    }


def _required_trace_schema_version(value: Mapping[str, Any]) -> int:
    actual = (
        value["trace_schema_version"]
        if "trace_schema_version" in value else "<missing>"
    )
    if (
        type(actual) is not int
        or actual != SUPPORTED_TRACE_SCHEMA_VERSION
    ):
        raise SimulationConfigurationError(
            "trace_schema_version must be the integer "
            f"{SUPPORTED_TRACE_SCHEMA_VERSION}; actual={actual!r}"
        )
    return actual


def simulation_result_from_dict(value: Mapping[str, Any]) -> SimulationResult:
    trace_schema_version = _required_trace_schema_version(value)
    return SimulationResult(
        SimulationStatus(str(value["status"])), str(value["reason"]),
        int(value["horizon"]),
        tuple(JobObservation(**row) for row in value.get("jobs", [])),
        tuple(TaskObservation(**row) for row in value.get("tasks", [])),
        bool(value["release_e0_valid"]), value.get("minimum_release_energy_j"),
        dict(value.get("observed_task_power_j_per_tick", {})),
        trace_schema_version,
        str(value.get("configured_scheduler", "")),
        bool(value.get("simulation_completed", False)),
        str(value.get("completion_reason", "")),
        dict(value.get("metrics", {})),
    )


def write_simulation_terminal(path: Path, execution: SimulationExecution) -> None:
    payload = {
        "simulation_id": execution.simulation_id,
        "result": simulation_result_to_dict(execution.result),
        "runtime_seconds": execution.runtime_seconds,
        "attempt_count": execution.attempt_count,
        "horizons_attempted": list(execution.horizons_attempted),
        "system_config_path": str(execution.system_config_path),
        "taskset_path": str(execution.taskset_path),
        "retained_trace_path": (
            str(execution.retained_trace_path) if execution.retained_trace_path else None
        ),
        "stdout_tail": execution.stdout_tail,
        "stderr_tail": execution.stderr_tail,
    }
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if canonical_json(existing) != canonical_json(payload):
            raise SimulationConfigurationError(
                f"conflicting duplicate simulation terminal: {execution.simulation_id}"
            )
        return
    atomic_write_json(path, payload)


def load_simulation_terminal(path: Path) -> SimulationExecution:
    value = json.loads(path.read_text(encoding="utf-8"))
    return SimulationExecution(
        str(value["simulation_id"]), simulation_result_from_dict(value["result"]),
        float(value["runtime_seconds"]), int(value["attempt_count"]),
        tuple(int(item) for item in value["horizons_attempted"]),
        Path(value["system_config_path"]), Path(value["taskset_path"]),
        Path(value["retained_trace_path"]) if value.get("retained_trace_path") else None,
        str(value.get("stdout_tail", "")), str(value.get("stderr_tail", "")),
    )


def run_paired_simulation(
    *,
    simulation_id_value: str,
    base_system_path: Path,
    run_root: Path,
    task_payload: Sequence[Mapping[str, Any]],
    taskset_hash: str,
    processors: int,
    exact_e0: Fraction,
    energy_config: Mapping[str, Any],
    simulation_config: Mapping[str, Any],
    scheduler_id: str = "gpfp_asap_block",
) -> SimulationExecution:
    initial = Fraction(str(energy_config["simulation_initial_battery"]))
    capacity = Fraction(str(energy_config["battery_capacity"]))
    input_root = run_root / "simulation_inputs" / simulation_id_value
    system_path, taskset_path = materialize_simulation_inputs(
        base_system_path, input_root, task_payload,
        processors=processors, initial_battery=initial,
        battery_capacity=capacity, scheduler_id=scheduler_id,
    )
    validate_no_overflow_guard(
        system_path, int(simulation_config["maximum_horizon"]),
        initial_battery=initial, battery_capacity=capacity,
    )

    simulator = Path(str(simulation_config["simulator_bin"]))
    if not simulator.is_absolute():
        simulator = PROJECT_ROOT / simulator
    if not simulator.is_file():
        raise SimulationConfigurationError(f"simulator binary not found: {simulator}")
    trace_work = run_root / "simulation_trace_work"
    trace_work.mkdir(parents=True, exist_ok=True)
    failure_traces = run_root / "failure_traces"
    failure_traces.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    library = simulator.parent.parent / "librtsim"
    environment["LD_LIBRARY_PATH"] = str(library) + ":" + environment.get("LD_LIBRARY_PATH", "")

    horizon = int(simulation_config["horizon"])
    maximum = int(simulation_config["maximum_horizon"])
    policy = str(simulation_config["horizon_extension_policy"])
    horizons: list[int] = []
    total_runtime = 0.0
    stdout_tail = ""
    stderr_tail = ""
    retained: Optional[Path] = None
    result: Optional[SimulationResult] = None

    while True:
        horizons.append(horizon)
        trace_path = trace_work / f"{simulation_id_value}.{horizon}.json"
        trace_path.unlink(missing_ok=True)
        command = [
            str(simulator), str(system_path), str(taskset_path), str(horizon),
            "-t", str(trace_path), "--run-id",
            f"v93-{simulation_id_value[:16]}-h{horizon}",
            "--taskset-semantic-hash", taskset_hash,
        ]
        if simulation_config["trace_mode"] == "semantic":
            command.append("--semantic-traces")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command, cwd=str(PROJECT_ROOT), env=environment,
                capture_output=True, text=True,
                timeout=float(simulation_config["timeout_seconds"]), check=False,
            )
            total_runtime += time.perf_counter() - started
            stdout_tail = (completed.stdout or "")[-6000:]
            stderr_tail = (completed.stderr or "")[-6000:]
            if completed.returncode:
                result = _failure_result(
                    SimulationStatus.INTERNAL_ERROR,
                    f"simulator_exit_{completed.returncode}", horizon,
                    scheduler_id,
                )
            else:
                try:
                    result = parse_simulation_trace(
                        trace_path, task_payload,
                        expected_taskset_hash=taskset_hash, horizon=horizon,
                        warmup=int(simulation_config["warmup"]),
                        minimum_jobs_per_task=int(simulation_config["minimum_jobs_per_task"]),
                        release_e0=exact_e0,
                        expected_scheduler=scheduler_id,
                        expected_processors=processors,
                    )
                    for task_id, observed in result.observed_task_power_j_per_tick.items():
                        expected = float(Fraction(str(task_payload[int(task_id)]["P"])))
                        if not math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-12):
                            raise SimulationTraceError(
                                f"task {task_id} RTA/simulation power mismatch"
                            )
                except SimulationTraceError as exc:
                    result = _failure_result(
                        SimulationStatus.INTERNAL_ERROR,
                        f"trace_semantic_error:{exc}", horizon,
                        scheduler_id,
                    )
        except subprocess.TimeoutExpired as exc:
            total_runtime += time.perf_counter() - started
            stdout_tail = str(exc.stdout or "")[-6000:]
            stderr_tail = str(exc.stderr or "")[-6000:]
            result = _failure_result(
                SimulationStatus.RUNTIME_TIMEOUT, "simulation_timeout", horizon,
                scheduler_id,
            )

        assert result is not None
        retain_always = bool(simulation_config.get("retain_trace", False))
        should_retain = bool(
            trace_path.is_file()
            and (
                retain_always
                or (
                    simulation_config["trace_on_failure"]
                    and (
                        result.status in {
                            SimulationStatus.DEADLINE_MISS,
                            SimulationStatus.INTERNAL_ERROR,
                        }
                        or not result.release_e0_valid
                    )
                )
            )
        )
        if should_retain:
            destination_root = (
                run_root / "retained_traces" if retain_always else failure_traces
            )
            destination_root.mkdir(parents=True, exist_ok=True)
            retained = destination_root / f"{simulation_id_value}.json"
            shutil.copy2(trace_path, retained)
        trace_path.unlink(missing_ok=True)

        if result.status is not SimulationStatus.HORIZON_INSUFFICIENT:
            break
        extended = next_horizon(horizon, maximum, policy)
        if extended is None:
            break
        horizon = extended

    return SimulationExecution(
        simulation_id_value, result, total_runtime, len(horizons), tuple(horizons),
        system_path, taskset_path, retained, stdout_tail, stderr_tail,
    )
