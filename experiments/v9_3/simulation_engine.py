"""ASAP-BLOCK simulator adapter for frozen v9.3 tasksets."""

from __future__ import annotations

from dataclasses import dataclass
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
from . import exact_energy
from .result_writer import atomic_write_json, atomic_write_text
from .solar_parse_proof import (
    ImmutableSolarReplaySnapshot,
    SolarParseProofError,
    build_live_solar_stod_parse_proof_from_snapshot,
    freeze_material,
    solar_parser_build_binding,
    thaw_material,
    validate_expected_solar_stod_parse_proof,
)
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
SHARED_SOLAR_INPUT_SCHEMA = "ASAP_BLOCK_V9_3_SHARED_SOLAR_INPUT_V3"
SHARED_SOLAR_INPUT_CLASSIFICATION = "DETERMINISTIC_CANONICAL_REPLAY_INPUT"
SHARED_SOLAR_SAMPLING_RULE = (
    "PRODUCTION_IRRADIANCE_SAMPLE_THEN_BINARY64_TICK_ENERGY_V1"
)
SHARED_SOLAR_INDEXING_POLICY = (
    "CXX_PHYSICAL_DATA_ROW_EQUALS_TOTAL_CALENDAR_MINUTE_V1"
)
SHARED_SOLAR_INVALID_ROW_POLICY = (
    "FAIL_CLOSED_FROM_FIRST_DATA_ROW_THROUGH_LAST_ACCESSED_ROW_V1"
)
SHARED_SOLAR_NEGATIVE_VALUE_POLICY = (
    "ALLOW_BEFORE_WINDOW_FAIL_CLOSED_IF_ACCESSED_V1"
)
SHARED_SOLAR_OPERATION_ORDER_VERSION = (
    "ASAP_BLOCK_REAL_SOLAR_BINARY64_TICK_ORDER_V1"
)
class SimulationConfigurationError(RuntimeError):
    """Raised before execution when RTA/simulation inputs cannot be paired."""


@dataclass(frozen=True)
class SharedSolarInput:
    """Side-effect-free replay of production per-tick solar input."""

    harvest_j_per_tick: tuple[Fraction, ...]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "harvest_j_per_tick", tuple(self.harvest_j_per_tick),
        )
        object.__setattr__(
            self, "provenance", freeze_material(self.provenance),
        )

    @property
    def offered_harvest_j(self) -> Fraction:
        return sum(self.harvest_j_per_tick, Fraction(0))

    def beta(
        self,
        max_length: int,
        *,
        valid_start_range: range | None = None,
    ) -> tuple[Fraction, ...]:
        return exact_energy.service_curve_lower_bound(
            self.harvest_j_per_tick,
            max_length,
            valid_start_range=valid_start_range,
        )


VerifiedSolarServiceMaterial = SharedSolarInput


def construct_shared_solar_input(
    base_system_path: Path | str,
    energy_support: Path | str,
    *,
    horizon: int,
    solar_parse_proof: Path | str | None = None,
    solar_parse_compiler: Path | str = "c++",
    source_root: Path | str | None = None,
) -> SharedSolarInput:
    """Build one immutable, internally verified production solar material."""

    base_path = Path(base_system_path).resolve(strict=True)
    if not base_path.is_file():
        raise SimulationConfigurationError(
            f"base simulator system is not a file: {base_path}"
        )
    root = (
        base_path.parent
        if source_root is None
        else Path(source_root).resolve(strict=True)
    )
    if not root.is_dir():
        raise SimulationConfigurationError(
            f"shared energy source root is not a directory: {root}"
        )
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise SimulationConfigurationError(
            "shared solar horizon must be a positive integer"
        )
    if isinstance(energy_support, Mapping):
        raise SimulationConfigurationError(
            "formal-safe shared solar input requires a versioned "
            "energy-support file path"
        )
    try:
        with ImmutableSolarReplaySnapshot(
            source_root=root,
            base_system_path=base_path,
            energy_support_path=energy_support,
            expected_proof_path=solar_parse_proof,
        ) as snapshot:
            try:
                base_system = legacy_rta.load_system_config(
                    str(snapshot.system_path)
                )
            except Exception as exc:
                raise SimulationConfigurationError(
                    f"cannot load snapshotted simulator system: {exc}"
                ) from exc
            live_proof = build_live_solar_stod_parse_proof_from_snapshot(
                snapshot,
                day_of_year=base_system.day_of_year,
                time_of_day_ms=base_system.time_of_day_ms,
                horizon=horizon,
                compiler=solar_parse_compiler,
            )
            validate_expected_solar_stod_parse_proof(
                snapshot, live_proof,
            )

            support_document = yaml.safe_load(snapshot.support.payload)
            if not isinstance(support_document, Mapping):
                raise SimulationConfigurationError(
                    "snapshotted energy support must be a mapping"
                )
            energy_value = support_document.get(
                "energy", support_document,
            )
            if not isinstance(energy_value, Mapping):
                raise SimulationConfigurationError(
                    "snapshotted energy support has no energy mapping"
                )
            energy = dict(energy_value)
            service = energy.get("service_curve")
            if not isinstance(service, Mapping):
                raise SimulationConfigurationError(
                    "snapshotted energy support service_curve "
                    "must be a mapping"
                )
            initial = _system_fraction(
                energy.get(
                    "simulation_initial_battery",
                    energy.get("initial_energy"),
                ),
                "shared solar initial battery",
            )
            capacity = _system_fraction(
                energy.get("battery_capacity"),
                "shared solar battery capacity",
                positive=True,
            )
            scale = _system_fraction(
                service.get("solar_scale", "1"),
                "shared solar scale",
                positive=True,
            )
            projected_bytes = render_system_projection(
                snapshot.system_path,
                processors=base_system.num_cores,
                initial_battery=initial,
                battery_capacity=capacity,
                service_curve=service,
            ).encode("utf-8")
            projected_path = snapshot.write_private_file(
                "projected-system.yml", projected_bytes,
            )
            try:
                projected = legacy_rta.load_system_config(
                    str(projected_path)
                )
            except Exception as exc:
                raise SimulationConfigurationError(
                    f"cannot load projected snapshotted system: {exc}"
                ) from exc
            if not projected.use_real_solar_data:
                raise SimulationConfigurationError(
                    "shared production solar input requires real solar data"
                )
            projected_solar = Path(
                legacy_rta._resolve_solar_path(projected)
            ).resolve(strict=True)
            if projected_solar != snapshot.solar_csv_path.resolve(strict=True):
                raise SimulationConfigurationError(
                    "production replay is not bound to the immutable CSV snapshot"
                )

            trace = construct_paired_harvest_trace(
                projected_path, horizon,
            )
            if (
                len(trace) != horizon
                or any(
                    type(value) is not Fraction or value < 0
                    for value in trace
                )
            ):
                raise SimulationConfigurationError(
                    "shared solar replay returned an invalid supply trace"
                )

            tick = exact_energy.materialize_supply_lower_bound(
                legacy_rta.TICK_SECONDS,
                "production tick duration seconds",
            )
            trace_payload = canonical_json(
                [fraction_text(value) for value in trace]
            ).encode("utf-8")
            semantic = live_proof["semantic_service_source"]
            expected_material = (
                None
                if snapshot.expected_proof is None
                else thaw_material(snapshot.expected_proof.material)
            )
            provenance: Dict[str, Any] = {
                "schema": SHARED_SOLAR_INPUT_SCHEMA,
                "classification": SHARED_SOLAR_INPUT_CLASSIFICATION,
                "system_template": thaw_material(
                    snapshot.system.material
                ),
                "energy_support": thaw_material(
                    snapshot.support.material
                ),
                "solar_csv": thaw_material(
                    snapshot.solar_csv.material
                ),
                "expected_solar_stod_parse_proof": expected_material,
                "live_solar_stod_parse_proof": thaw_material(live_proof),
                "solar_stod_parser_binding": solar_parser_build_binding(
                    live_proof
                ),
                "use_real_solar_data": True,
                "day_of_year": projected.day_of_year,
                "time_of_day_ms": projected.time_of_day_ms,
                "materialized_start_offset_ms": (
                    legacy_rta.materialize_runtime_start_offset_ms(
                        projected.day_of_year,
                        projected.time_of_day_ms,
                    )
                ),
                "solar_scale": fraction_text(scale),
                "raw_reference_pv_area_m2": fraction_text(
                    exact_energy.materialize_supply_lower_bound(
                        base_system.pv_area_m2,
                        "shared solar raw reference pv area",
                    ).exact_value
                ),
                "effective_pv_area_m2": fraction_text(
                    exact_energy.materialize_supply_lower_bound(
                        projected.pv_area_m2,
                        "shared solar projected pv area",
                    ).exact_value
                ),
                "pv_efficiency": fraction_text(
                    exact_energy.materialize_supply_lower_bound(
                        projected.pv_efficiency,
                        "shared solar pv efficiency",
                    ).exact_value
                ),
                "tick_duration_seconds": fraction_text(tick.exact_value),
                "tick_duration_binary64": tick.binary64_hex,
                "sampling_rule": SHARED_SOLAR_SAMPLING_RULE,
                "physical_data_row_count": (
                    semantic["physical_data_row_count"]
                ),
                "first_accessed_data_row": (
                    semantic["first_accessed_data_row"]
                ),
                "last_accessed_data_row": (
                    semantic["last_accessed_data_row"]
                ),
                "first_calendar_minute_index": (
                    semantic["first_accessed_data_row"]
                ),
                "last_calendar_minute_index": (
                    semantic["last_accessed_data_row"]
                ),
                "accessed_sample_count": (
                    semantic["accessed_sample_count"]
                ),
                "invalid_row_policy": SHARED_SOLAR_INVALID_ROW_POLICY,
                "negative_value_policy": SHARED_SOLAR_NEGATIVE_VALUE_POLICY,
                "indexing_policy": SHARED_SOLAR_INDEXING_POLICY,
                "operation_order_version": (
                    SHARED_SOLAR_OPERATION_ORDER_VERSION
                ),
                "horizon": horizon,
                "harvest_trace_sha256": hashlib.sha256(
                    trace_payload
                ).hexdigest(),
            }
            provenance["replay_input_sha256"] = hashlib.sha256(
                canonical_json(provenance).encode("utf-8")
            ).hexdigest()
            return SharedSolarInput(trace, provenance)
    except SolarParseProofError as exc:
        raise SimulationConfigurationError(
            f"shared solar snapshot/proof rejected: {exc}"
        ) from exc


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


def _taskset_document(
    task_payload: Sequence[Mapping[str, Any]],
    *,
    release_horizon: Optional[int] = None,
) -> Dict[str, Any]:
    if release_horizon is not None and (
        isinstance(release_horizon, bool)
        or not isinstance(release_horizon, int)
        or release_horizon <= 0
    ):
        raise SimulationConfigurationError(
            "release_horizon must be a positive integer"
        )
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
        if "ph" in row and (
            isinstance(row["ph"], bool)
            or not isinstance(row["ph"], int)
            or row["ph"] != offset
        ):
            raise SimulationConfigurationError(
                "simulation ph/arrival_offset projection mismatch"
            )
        if offset < 0 or offset >= t_value:
            raise SimulationConfigurationError(
                "simulation arrival_offset must satisfy 0 <= O_i < T_i"
            )
        if release_horizon is not None and offset >= release_horizon:
            raise SimulationConfigurationError(
                "simulation arrival_offset must precede release_horizon"
            )
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
    document: Dict[str, Any] = {"taskset": tasks, "resources": []}
    if release_horizon is not None:
        document["release_horizon"] = release_horizon
    return document


def _render_taskset_yaml(
    task_payload: Sequence[Mapping[str, Any]],
    *,
    release_horizon: Optional[int] = None,
) -> str:
    """Render the conservative YAML subset consumed by RTSim's C++ parser."""

    document = _taskset_document(
        task_payload, release_horizon=release_horizon
    )
    lines = []
    if release_horizon is not None:
        lines.append(f"release_horizon: {document['release_horizon']}")
    lines.append("taskset:")
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
    try:
        exact = (
            exact_energy.materialize_supply_lower_bound(value, label).exact_value
            if type(value) is float
            else exact_energy.exact_e0_lower_bound(value, label)
        )
    except exact_energy.ExactEnergyError as exc:
        raise SimulationConfigurationError(
            f"{label} must be finite and numeric"
        ) from exc
    if positive and exact <= 0:
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
    release_horizon: Optional[int] = None,
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
        _render_taskset_yaml(
            task_payload, release_horizon=release_horizon
        ),
    )
    return system_path, taskset_path


def construct_paired_harvest_trace(
    system_path: Path,
    horizon_ms: int,
) -> tuple[Fraction, ...]:
    """Construct the exact audit view of the production binary64 tick trace."""

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
            exact = exact_energy.materialize_supply_lower_bound(
                value, f"harvest trace value {index}",
            ).exact_value
        except exact_energy.ExactEnergyError as exc:
            raise SimulationConfigurationError(
                f"harvest trace value {index} is not finite numeric data"
            ) from exc
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
        "pv_efficiency": fraction_text(
            exact_energy.materialize_supply_lower_bound(
                scaled_system.pv_efficiency, "scaled pv efficiency",
            ).exact_value
        ),
        "pv_area_m2": fraction_text(
            exact_energy.materialize_supply_lower_bound(
                scaled_system.pv_area_m2, "scaled pv area",
            ).exact_value
        ),
        "raw_reference_pv_area_m2": fraction_text(
            exact_energy.materialize_supply_lower_bound(
                raw_system.pv_area_m2, "raw reference pv area",
            ).exact_value
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
    try:
        initial = exact_energy.exact_e0_lower_bound(
            energy_config["simulation_initial_battery"],
            "simulation initial battery",
        )
        capacity = exact_energy.exact_e0_lower_bound(
            energy_config["battery_capacity"], "simulation battery capacity",
        )
    except exact_energy.ExactEnergyError as exc:
        raise SimulationConfigurationError(str(exc)) from exc
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
                        expected = float(exact_energy.parse_persisted_fraction(
                            task_payload[int(task_id)]["P"],
                            f"simulation task {task_id} P",
                        ))
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
