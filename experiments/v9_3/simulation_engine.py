"""ASAP-BLOCK simulator adapter for frozen v9.3 tasksets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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
from .task_identity import runtime_task_name_for_source_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_TRACE_SCHEMA_VERSION = 2
CORE3_ENERGY_PREFLIGHT_SCHEMA = "ASAP_BLOCK_V9_3_CORE3_ENERGY_PREFLIGHT_V1"


class SimulationConfigurationError(RuntimeError):
    """Raised before execution when RTA/simulation inputs cannot be paired."""


def trace_retention_statuses(
    simulation_config: Mapping[str, Any],
) -> set[str]:
    """Return the optional retention set while preserving legacy defaults."""

    configured = simulation_config.get("retain_trace_statuses")
    if configured is None:
        return {
            SimulationStatus.DEADLINE_MISS.value,
            SimulationStatus.INTERNAL_ERROR.value,
        }
    if (
        not isinstance(configured, (list, tuple))
        or any(not isinstance(value, str) for value in configured)
    ):
        raise SimulationConfigurationError(
            "retain_trace_statuses must be a list of status strings"
        )
    return set(configured)


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


def shared_e0_simulation_identity(
    generation_id: str,
    taskset_hash: str,
    simulation_config: Mapping[str, Any],
) -> str:
    """Identify one simulation whose trace is projected onto several RTA E0s."""

    return domain_hash(
        "ASAP_BLOCK:V9.3:CORE3_SHARED_E0_SIMULATION:v1",
        {
            "generation_id": generation_id,
            "taskset_hash": taskset_hash,
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
            "name": runtime_task_name_for_source_id(task_id),
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


def _system_fraction(
    value: Any,
    label: str,
    *,
    positive: bool = False,
) -> Fraction:
    if isinstance(value, bool):
        raise SimulationConfigurationError(f"{label} must be finite and numeric")
    try:
        exact = Fraction(str(value))
        numeric = float(exact)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise SimulationConfigurationError(
            f"{label} must be finite and numeric"
        ) from exc
    if not math.isfinite(numeric) or exact < 0 or (positive and exact <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise SimulationConfigurationError(
            f"{label} must be finite and {qualifier}"
        )
    return exact


def configured_solar_scale(energy_config: Mapping[str, Any]) -> Fraction:
    service = energy_config.get("service_curve")
    if not isinstance(service, Mapping):
        raise SimulationConfigurationError("energy.service_curve must be a mapping")
    return _system_fraction(
        service.get("solar_scale", "1"),
        "energy.service_curve.solar_scale",
        positive=True,
    )


def render_system_projection(
    base_system_path: Path,
    *,
    processors: int,
    initial_battery: Fraction,
    battery_capacity: Fraction,
    scheduler_id: str = "gpfp_asap_block",
    service_curve: Optional[Mapping[str, Any]] = None,
) -> str:
    """Return the single side-effect-free system projection used by CORE-3."""

    try:
        source_text = base_system_path.read_text(encoding="utf-8")
        system = yaml.safe_load(source_text)
    except (OSError, yaml.YAMLError) as exc:
        raise SimulationConfigurationError(f"cannot load base system: {exc}") from exc
    if not isinstance(system, dict) or not isinstance(system.get("cpu_islands"), list):
        raise SimulationConfigurationError("base system has no CPU island")
    energy = system.get("energy_management")
    if not isinstance(energy, dict):
        raise SimulationConfigurationError("base system has no energy_management mapping")
    if isinstance(processors, bool) or not isinstance(processors, int) or processors <= 0:
        raise SimulationConfigurationError("processors must be a positive integer")
    initial = _system_fraction(initial_battery, "initial battery")
    capacity = _system_fraction(
        battery_capacity, "battery capacity", positive=True
    )
    if initial > capacity:
        raise SimulationConfigurationError("initial battery exceeds capacity")

    service = dict(service_curve or {})
    scale = _system_fraction(
        service.get("solar_scale", "1"), "solar scale", positive=True
    )
    reference_area = _system_fraction(
        energy.get("pv_area_m2"), "template pv_area_m2", positive=True
    )
    expected_reference = service.get("raw_reference_pv_area_m2")
    if expected_reference is not None:
        expected = _system_fraction(
            expected_reference, "raw reference pv_area_m2", positive=True
        )
        if expected != reference_area:
            raise SimulationConfigurationError(
                "template pv_area_m2 does not match frozen raw reference: "
                f"template={fraction_text(reference_area)} expected="
                f"{fraction_text(expected)}"
            )
    effective_area = reference_area * scale
    effective_float = float(effective_area)
    if not math.isfinite(effective_float) or effective_float <= 0:
        raise SimulationConfigurationError(
            "effective pv_area_m2 is not a finite positive runtime value"
        )

    replacements = {
        "numcpus": str(processors),
        "scheduler": scheduler_id,
        "initial_energy": format(float(initial), ".17g"),
        "max_energy": format(float(capacity), ".17g"),
        "pv_area_m2": format(effective_float, ".17g"),
    }
    if bool(energy.get("use_real_solar_data", False)):
        raw_solar_path = energy.get("solar_data_file")
        if not isinstance(raw_solar_path, str) or not raw_solar_path:
            raise SimulationConfigurationError(
                "real-solar system requires solar_data_file"
            )
        solar_path = Path(raw_solar_path)
        if not solar_path.is_absolute():
            solar_path = (base_system_path.parent / solar_path).resolve()
        if not solar_path.is_file():
            raise SimulationConfigurationError(
                f"solar data file not found: {solar_path}"
            )
        replacements["solar_data_file"] = json.dumps(str(solar_path))

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
    return "\n".join(rendered_lines) + "\n"


def materialize_simulation_inputs(
    base_system_path: Path,
    destination: Path,
    task_payload: Sequence[Mapping[str, Any]],
    *,
    processors: int,
    initial_battery: Fraction,
    battery_capacity: Fraction,
    scheduler_id: str = "gpfp_asap_block",
    service_curve: Optional[Mapping[str, Any]] = None,
) -> tuple[Path, Path]:
    """Write a scheduler-only projection without changing frozen semantics."""

    rendered_system = render_system_projection(
        base_system_path,
        processors=processors,
        initial_battery=initial_battery,
        battery_capacity=battery_capacity,
        scheduler_id=scheduler_id,
        service_curve=service_curve,
    )

    destination.mkdir(parents=True, exist_ok=True)
    system_path = destination / "system_config.yaml"
    taskset_path = destination / "taskset.yaml"
    atomic_write_text(
        system_path,
        rendered_system,
    )
    atomic_write_text(
        taskset_path,
        _render_taskset_yaml(task_payload),
    )
    return system_path, taskset_path


def construct_paired_harvest_trace(
    system_path: Path,
    horizon_ms: int,
) -> tuple[Fraction, ...]:
    """Construct the exact audit view of the production per-tick trace."""

    if isinstance(horizon_ms, bool) or not isinstance(horizon_ms, int) or horizon_ms <= 0:
        raise SimulationConfigurationError("harvest horizon must be a positive integer")
    try:
        system = legacy_rta.load_system_config(str(system_path))
        raw_trace = legacy_rta._harvest_trace_from_config(system, horizon_ms)
    except Exception as exc:
        raise SimulationConfigurationError(
            f"cannot construct paired simulation harvest trace: {exc}"
        ) from exc
    trace = []
    for index, value in enumerate(raw_trace):
        try:
            numeric = float(value)
            exact = Fraction(str(value))
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise SimulationConfigurationError(
                f"harvest trace value {index} is not finite numeric data"
            ) from exc
        if not math.isfinite(numeric) or exact < 0:
            raise SimulationConfigurationError(
                f"harvest trace value {index} must be finite and non-negative"
            )
        trace.append(exact)
    if len(trace) != horizon_ms:
        raise SimulationConfigurationError(
            "paired harvest trace length does not match maximum_horizon"
        )
    return tuple(trace)


def no_overflow_contract(
    *,
    initial_battery: Fraction,
    battery_capacity: Fraction,
    offered_harvest: Fraction,
    required_safety_margin: Fraction = Fraction(0),
) -> tuple[Fraction, Fraction, bool]:
    """Return required capacity, remaining headroom, and strict gate result."""

    initial = _system_fraction(initial_battery, "initial battery")
    capacity = _system_fraction(
        battery_capacity, "battery capacity", positive=True
    )
    harvest = _system_fraction(offered_harvest, "offered harvest")
    margin = _system_fraction(required_safety_margin, "required safety margin")
    required = initial + harvest
    available = capacity - required
    return required, available, available >= margin


def select_largest_dyadic_solar_scale(
    *,
    raw_offered_harvest: Fraction,
    initial_battery: Fraction,
    battery_capacity: Fraction,
    required_safety_margin: Fraction,
) -> Fraction:
    """Apply the frozen result-independent CORE-3 dyadic feasibility rule."""

    raw = _system_fraction(raw_offered_harvest, "raw offered harvest")
    initial = _system_fraction(initial_battery, "initial battery")
    capacity = _system_fraction(
        battery_capacity, "battery capacity", positive=True
    )
    margin = _system_fraction(required_safety_margin, "required safety margin")
    budget = capacity - margin - initial
    if budget < 0 or (budget == 0 and raw > 0):
        raise SimulationConfigurationError(
            "no positive dyadic solar scale can satisfy the frozen headroom rule"
        )
    scale = Fraction(1)
    while scale * raw > budget:
        scale /= 2
    return scale


def validate_no_overflow_guard(
    system_path: Path,
    maximum_horizon: int,
    *,
    initial_battery: Fraction,
    battery_capacity: Fraction,
    required_safety_margin: Fraction = Fraction(0),
) -> Fraction:
    """Return offered harvest after proving capacity cannot clip it."""

    exact_harvest = sum(
        construct_paired_harvest_trace(system_path, maximum_horizon),
        Fraction(0),
    )
    required, available, valid = no_overflow_contract(
        initial_battery=initial_battery,
        battery_capacity=battery_capacity,
        offered_harvest=exact_harvest,
        required_safety_margin=required_safety_margin,
    )
    if not valid:
        raise SimulationConfigurationError(
            "finite battery can clip configured harvest through maximum_horizon: "
            f"initial={fraction_text(initial_battery)} "
            f"capacity={fraction_text(battery_capacity)} "
            f"offered_harvest={fraction_text(exact_harvest)} "
            f"required_capacity={fraction_text(required)} "
            f"available_headroom={fraction_text(available)} "
            f"required_safety_margin={fraction_text(required_safety_margin)}"
        )
    return exact_harvest


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SimulationConfigurationError(f"cannot hash audit input {path}: {exc}") from exc


def core3_energy_preflight(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Audit CORE-3 energy headroom without creating experiment artifacts."""

    energy_config = config.get("energy")
    simulation_config = config.get("simulation")
    platform_config = config.get("platform")
    if not all(isinstance(item, Mapping) for item in (
        energy_config, simulation_config, platform_config,
    )):
        raise SimulationConfigurationError("CORE-3 preflight configuration is incomplete")
    assert isinstance(energy_config, Mapping)
    assert isinstance(simulation_config, Mapping)
    assert isinstance(platform_config, Mapping)
    service = energy_config.get("service_curve")
    if not isinstance(service, Mapping):
        raise SimulationConfigurationError("energy.service_curve must be a mapping")
    template_value = service.get("system_template")
    if not isinstance(template_value, str) or not template_value:
        raise SimulationConfigurationError("service curve has no system template")
    template_path = Path(template_value)
    if not template_path.is_absolute():
        template_path = PROJECT_ROOT / template_path
    if not template_path.is_file():
        raise SimulationConfigurationError(
            f"service-curve system template not found: {template_path}"
        )
    cores = platform_config.get("cores")
    if not isinstance(cores, list) or not cores:
        raise SimulationConfigurationError("CORE-3 preflight requires platform cores")
    processors = max(cores)
    horizon = simulation_config.get("maximum_horizon")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise SimulationConfigurationError("maximum_horizon must be a positive integer")
    initial = _system_fraction(
        energy_config.get("simulation_initial_battery"),
        "simulation initial battery",
    )
    capacity = _system_fraction(
        energy_config.get("battery_capacity"), "battery capacity", positive=True
    )
    margin = _system_fraction(
        energy_config.get("required_safety_margin", "0"),
        "required safety margin",
    )
    scale = configured_solar_scale(energy_config)

    raw_service = dict(service)
    raw_service["solar_scale"] = "1"
    with tempfile.TemporaryDirectory(prefix="v9_3_core3_energy_preflight_") as temp:
        audit_root = Path(temp)
        raw_system_path, _ = materialize_simulation_inputs(
            template_path,
            audit_root / "raw_reference",
            (),
            processors=processors,
            initial_battery=initial,
            battery_capacity=capacity,
            service_curve=raw_service,
        )
        scaled_system_path, _ = materialize_simulation_inputs(
            template_path,
            audit_root / "scaled_runtime",
            (),
            processors=processors,
            initial_battery=initial,
            battery_capacity=capacity,
            service_curve=service,
        )
        raw_system = legacy_rta.load_system_config(str(raw_system_path))
        scaled_system = legacy_rta.load_system_config(str(scaled_system_path))
        raw_trace = construct_paired_harvest_trace(raw_system_path, horizon)
        scaled_trace = construct_paired_harvest_trace(scaled_system_path, horizon)
        raw_harvest = sum(raw_trace, Fraction(0))
        scaled_harvest = sum(scaled_trace, Fraction(0))
        solar_path = Path(scaled_system.solar_data_file)
        if not solar_path.is_absolute():
            solar_path = Path(legacy_rta._resolve_solar_path(scaled_system))
        if scaled_system.use_real_solar_data and not solar_path.is_file():
            raise SimulationConfigurationError(
                f"solar data file not found: {solar_path}"
            )
        solar_sha256 = _sha256(solar_path) if scaled_system.use_real_solar_data else ""

    if (
        raw_system.use_real_solar_data != scaled_system.use_real_solar_data
        or raw_system.day_of_year != scaled_system.day_of_year
        or raw_system.time_of_day_ms != scaled_system.time_of_day_ms
        or raw_system.pv_efficiency != scaled_system.pv_efficiency
    ):
        raise SimulationConfigurationError(
            "raw/scaled preflight projections changed real-solar semantics"
        )
    if bool(service.get("require_real_solar_data", False)) and not bool(
        scaled_system.use_real_solar_data
    ):
        raise SimulationConfigurationError(
            "service curve requires real-solar data but the system disables it"
        )
    required, available, valid = no_overflow_contract(
        initial_battery=initial,
        battery_capacity=capacity,
        offered_harvest=scaled_harvest,
        required_safety_margin=margin,
    )
    selection = service.get("dyadic_scale_selection")
    selected_scale: Optional[Fraction] = None
    selection_rule = ""
    if selection is not None:
        if not isinstance(selection, Mapping):
            raise SimulationConfigurationError(
                "dyadic_scale_selection must be a mapping"
            )
        selection_rule = str(selection.get("rule", ""))
        if selection_rule != "largest_feasible_dyadic_v1":
            raise SimulationConfigurationError(
                "unsupported dyadic solar scale selection rule"
            )
        selected_scale = select_largest_dyadic_solar_scale(
            raw_offered_harvest=raw_harvest,
            initial_battery=_system_fraction(
                selection.get("reference_initial_battery"),
                "dyadic reference initial battery",
            ),
            battery_capacity=_system_fraction(
                selection.get("reference_battery_capacity"),
                "dyadic reference battery capacity",
                positive=True,
            ),
            required_safety_margin=_system_fraction(
                selection.get("required_safety_margin"),
                "dyadic required safety margin",
            ),
        )
        if selected_scale != scale:
            raise SimulationConfigurationError(
                "configured solar scale is not the largest feasible dyadic: "
                f"configured={fraction_text(scale)} selected="
                f"{fraction_text(selected_scale)}"
            )
    try:
        solar_data_display = str(solar_path.relative_to(PROJECT_ROOT))
    except ValueError:
        solar_data_display = str(solar_path)
    report: Dict[str, Any] = {
        "schema": CORE3_ENERGY_PREFLIGHT_SCHEMA,
        "service_curve_id": str(service.get("id", "")),
        "system_template_path": str(template_value),
        "system_template_sha256": _sha256(template_path),
        "solar_data_path": solar_data_display,
        "solar_data_sha256": solar_sha256,
        "use_real_solar_data": bool(scaled_system.use_real_solar_data),
        "day_of_year": scaled_system.day_of_year,
        "time_of_day_ms": scaled_system.time_of_day_ms,
        "horizon_ms": horizon,
        "pv_efficiency": fraction_text(Fraction(str(scaled_system.pv_efficiency))),
        "pv_area_m2": fraction_text(Fraction(str(scaled_system.pv_area_m2))),
        "raw_reference_pv_area_m2": fraction_text(
            Fraction(str(raw_system.pv_area_m2))
        ),
        "raw_offered_harvest_j": fraction_text(raw_harvest),
        "applied_solar_scale": fraction_text(scale),
        "scaled_offered_harvest_j": fraction_text(scaled_harvest),
        "simulation_initial_battery_j": fraction_text(initial),
        "battery_capacity_j": fraction_text(capacity),
        "required_capacity_j": fraction_text(required),
        "available_headroom_j": fraction_text(available),
        "required_safety_margin_j": fraction_text(margin),
        "no_overflow_preflight_valid": valid,
    }
    if selected_scale is not None:
        report.update({
            "dyadic_scale_selection_rule": selection_rule,
            "largest_feasible_dyadic_scale": fraction_text(selected_scale),
        })
    report["preflight_identity"] = domain_hash(
        "ASAP_BLOCK:V9.3:CORE3_ENERGY_PREFLIGHT:v1", report
    )
    return report


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
        service_curve=energy_config.get("service_curve"),
    )
    # CORE-3's proof-oriented runs forbid harvest clipping.  EXT-1B's
    # SLACK_LIMITED_CHARGING micro-mechanism intentionally observes the ST
    # scheduler's documented "battery full or slack exhausted" release gate,
    # so that one explicitly validated experiment path may use a finite,
    # clipping battery.  The default remains fail-closed and unchanged.
    if not bool(energy_config.get("allow_harvest_clipping", False)):
        validate_no_overflow_guard(
            system_path, int(simulation_config["maximum_horizon"]),
            initial_battery=initial, battery_capacity=capacity,
            required_safety_margin=Fraction(
                str(energy_config.get("required_safety_margin", "0"))
            ),
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
        retain_statuses = trace_retention_statuses(simulation_config)
        should_retain = bool(
            trace_path.is_file()
            and (
                retain_always
                or (
                    simulation_config["trace_on_failure"]
                    and (
                        result.status.value in retain_statuses
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
