"""Strict simulation parsing and job-level response extraction.

The historical entry point remains schema-2-only by default.  CORE-3's V6
caller opts into the existing schema-3 observability contract explicitly; the
opt-in is deliberately fail-closed so a legacy trace can never be mistaken for
release-energy or overflow evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from fractions import Fraction
from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX import compatibility
    fcntl = None

from .rta4_core3_contracts_v6 import (
    RTA4Core3ContractV6Error,
    core3_energy_conservation_close_v1,
    require_normalized_core3_energy_conservation_rule_v1,
)
from . import implicit_trace_stream


class SimulationStatus(str, Enum):
    PASS_OBSERVED = "SIM_PASS_OBSERVED"
    DEADLINE_MISS = "SIM_DEADLINE_MISS"
    HORIZON_INSUFFICIENT = "SIM_HORIZON_INSUFFICIENT"
    RUNTIME_TIMEOUT = "SIM_RUNTIME_TIMEOUT"
    INTERNAL_ERROR = "SIM_INTERNAL_ERROR"


class SimulationTraceError(RuntimeError):
    """Raised when a simulator trace is not an admissible observation."""


_TRACE_PARSE_CONCURRENCY_ENV = "PARTSIM_TRACE_PARSE_CONCURRENCY"
_TRACE_PARSE_SLOT_DIR_ENV = "PARTSIM_TRACE_PARSE_SLOT_DIR"
_DEFAULT_TRACE_PARSE_SLOT_DIR = Path("/tmp/partsim_trace_parse_slots")


def _trace_parse_concurrency() -> Optional[int]:
    raw = os.environ.get(_TRACE_PARSE_CONCURRENCY_ENV)
    if raw is None:
        return None
    try:
        value = int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise SimulationTraceError(
            f"invalid {_TRACE_PARSE_CONCURRENCY_ENV}: {raw!r}"
        ) from exc
    if value < 1:
        raise SimulationTraceError(
            f"invalid {_TRACE_PARSE_CONCURRENCY_ENV}: must be positive"
        )
    return value


@contextmanager
def _trace_parse_slot():
    """Hold one shared POSIX slot for the complete JSON parse."""
    concurrency = _trace_parse_concurrency()
    if concurrency is None:
        yield
        return
    if fcntl is None:
        raise SimulationTraceError("trace parse slot gate requires POSIX flock")

    slot_dir_raw = os.environ.get(
        _TRACE_PARSE_SLOT_DIR_ENV, str(_DEFAULT_TRACE_PARSE_SLOT_DIR)
    )
    if not slot_dir_raw:
        raise SimulationTraceError(
            f"invalid {_TRACE_PARSE_SLOT_DIR_ENV}: must not be empty"
        )
    slot_dir = Path(slot_dir_raw)
    try:
        slot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not slot_dir.is_dir():
            raise OSError(f"not a directory: {slot_dir}")
    except OSError as exc:
        raise SimulationTraceError(
            f"cannot create trace parse slot directory: {slot_dir}"
        ) from exc

    descriptor: Optional[int] = None
    try:
        while descriptor is None:
            for index in range(concurrency):
                slot_path = slot_dir / f"slot-{index}.lock"
                try:
                    candidate = os.open(
                        slot_path,
                        os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                    )
                except OSError as exc:
                    raise SimulationTraceError(
                        f"cannot open trace parse slot: {slot_path}"
                    ) from exc
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    os.close(candidate)
                    if exc.errno in (errno.EACCES, errno.EAGAIN):
                        continue
                    raise SimulationTraceError(
                        f"cannot lock trace parse slot: {slot_path}"
                    ) from exc
                descriptor = candidate
                break
            if descriptor is None:
                time.sleep(0.01)
        yield
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_JOB_OBSERVATIONS_V6"
)
CORE3_RELEASE_ENERGY_SAMPLING_STAGE = "post_harvest_pre_consumption"
CORE3_ENERGY_TOLERANCE_J = 1e-8


@dataclass(frozen=True)
class JobObservation:
    task_id: str
    job_index: int
    release: int
    completion: Optional[int]
    absolute_deadline: int
    response_time: Optional[int]
    deadline_miss: bool
    first_execution: Optional[int]
    preemption_count: int
    energy_blocked_ticks: int
    processor_wait_ticks: Optional[int]
    executed_ticks: int
    eligible_after_warmup: bool
    censored: bool
    censoring_reason: Optional[str]
    task_name: Optional[str] = None
    release_energy_j: Optional[float] = None
    release_energy_sampling_stage: Optional[str] = None

    def row(self) -> Dict[str, Any]:
        value = asdict(self)
        if (
            self.task_name is None
            and self.release_energy_j is None
            and self.release_energy_sampling_stage is None
        ):
            for field_name in (
                "task_name", "release_energy_j",
                "release_energy_sampling_stage",
            ):
                value.pop(field_name)
        return value


@dataclass(frozen=True)
class TaskObservation:
    task_id: str
    observed_jobs: int
    completed_jobs: int
    missed_jobs: int
    censored_jobs: int
    r_sim_max: Optional[int]
    horizon_coverage: float
    minimum_jobs_satisfied: bool

    def row(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationResult:
    status: SimulationStatus
    reason: str
    horizon: int
    jobs: Tuple[JobObservation, ...]
    tasks: Tuple[TaskObservation, ...]
    release_e0_valid: bool
    minimum_release_energy_j: Optional[float]
    observed_task_power_j_per_tick: Mapping[str, float]
    trace_schema_version: int
    configured_scheduler: str
    simulation_completed: bool
    completion_reason: str
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def comparison_eligible(self) -> bool:
        return self.release_e0_valid and self.status in {
            SimulationStatus.PASS_OBSERVED,
            SimulationStatus.DEADLINE_MISS,
        }


def _strict_json(path: Path) -> Mapping[str, Any]:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SimulationTraceError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        with _trace_parse_slot():
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle, object_pairs_hook=no_duplicates)
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulationTraceError(f"cannot read simulation trace: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
        raise SimulationTraceError("trace must be an object containing an event list")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise SimulationTraceError(f"{label} must be an integer tick")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            parsed = Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise SimulationTraceError(f"{label} must be an integer tick") from exc
        if parsed.denominator == 1:
            return parsed.numerator
    raise SimulationTraceError(f"{label} must be an integer tick")


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise SimulationTraceError(f"{label} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulationTraceError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise SimulationTraceError(f"{label} must be finite")
    return result


def conditional_release_coverage_v6(
    release_energies_j: Sequence[float],
    exact_e0_values: Sequence[str],
) -> list[dict[str, Any]]:
    """Return integer conditional-coverage fractions for every exact E0."""

    energies = [
        _finite(value, f"release energy {index}")
        for index, value in enumerate(release_energies_j)
    ]
    if any(value < 0 for value in energies):
        raise SimulationTraceError("release energy must be nonnegative")
    exact_axes: list[tuple[str, Fraction]] = []
    for index, value in enumerate(exact_e0_values):
        if type(value) is not str:
            raise SimulationTraceError(
                f"conditional E0 {index} is not a rational string"
            )
        try:
            exact = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise SimulationTraceError(
                f"conditional E0 {index} is not rational"
            ) from exc
        if exact < 0 or str(exact) != value:
            raise SimulationTraceError(
                f"conditional E0 {index} is not canonical and nonnegative"
            )
        exact_axes.append((value, exact))
    if len({value for value, _ in exact_axes}) != len(exact_axes):
        raise SimulationTraceError("conditional E0 values are not unique")

    coverage = []
    previous_covered: Optional[int] = None
    for exact_text, exact_value in exact_axes:
        covered = sum(
            Fraction(str(energy)) >= exact_value
            for energy in energies
        )
        if previous_covered is not None and covered > previous_covered:
            raise SimulationTraceError(
                "conditional release-energy coverage is not monotone"
            )
        previous_covered = covered
        denominator = len(energies)
        coverage.append({
            "exact_e0": exact_text,
            "eligible_job_count": denominator,
            "covered_job_count": covered,
            "uncovered_job_count": denominator - covered,
            "coverage_rate_numerator": covered,
            "coverage_rate_denominator": denominator,
        })
    return coverage


def conditional_release_coverage_v7(
    release_energies_j: Sequence[float],
    projection_e0_model_units: Sequence[str],
    model_energy_unit_joules: str,
) -> list[dict[str, Any]]:
    """Evaluate CORE-3 V7 release coverage in the physical-joule domain."""

    if type(model_energy_unit_joules) is not str:
        raise SimulationTraceError(
            "model_energy_unit_joules must be a rational string"
        )
    try:
        scale = Fraction(model_energy_unit_joules)
    except (ValueError, ZeroDivisionError) as exc:
        raise SimulationTraceError(
            "model_energy_unit_joules is not rational"
        ) from exc
    if scale <= 0 or str(scale) != model_energy_unit_joules:
        raise SimulationTraceError(
            "model_energy_unit_joules must be canonical and positive"
        )
    physical_axes = []
    for index, value in enumerate(projection_e0_model_units):
        if type(value) is not str:
            raise SimulationTraceError(
                f"projection E0 {index} is not a rational string"
            )
        try:
            model_e0 = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise SimulationTraceError(
                f"projection E0 {index} is not rational"
            ) from exc
        if model_e0 < 0 or str(model_e0) != value:
            raise SimulationTraceError(
                f"projection E0 {index} is not canonical and nonnegative"
            )
        physical_axes.append((value, model_e0 * scale))
    legacy_shape = conditional_release_coverage_v6(
        release_energies_j,
        [str(physical) for _, physical in physical_axes],
    )
    return [{
        "projection_e0_model_units": model,
        "projection_e0_j": str(physical),
        **{
            key: item
            for key, item in row.items()
            if key != "exact_e0"
        },
    } for (model, physical), row in zip(physical_axes, legacy_shape)]


def parse_simulation_trace(
    trace_path: Path,
    task_payload: Sequence[Mapping[str, Any]],
    *,
    expected_taskset_hash: str,
    horizon: int,
    warmup: int,
    minimum_jobs_per_task: int,
    release_e0: Fraction,
    expected_scheduler: str = "gpfp_asap_block",
    expected_processors: Optional[int] = None,
    require_core3_observability: bool = False,
    release_horizon: Optional[int] = None,
    physical_initial_energy: Optional[Fraction] = None,
    battery_capacity: Optional[Fraction] = None,
    conditional_e0: Sequence[str] = (),
    theorem_alignment_track: bool = False,
    energy_tolerance_j: float = CORE3_ENERGY_TOLERANCE_J,
    energy_conservation_rule: Optional[Mapping[str, Any]] = None,
    model_energy_unit_joules: Optional[str] = None,
    expected_task_energy_j_per_tick: Optional[
        Mapping[str, Fraction]
    ] = None,
    task_energy_factor_provenance: Optional[
        Mapping[str, Mapping[str, Any]]
    ] = None,
    stream_events: bool = False,
) -> SimulationResult:
    """Parse one complete audited scheduler trace into job/task observations."""

    if stream_events:
        try:
            data, events = implicit_trace_stream.open_strict_stream(trace_path)
        except (OSError, ValueError) as exc:
            raise SimulationTraceError(str(exc)) from exc
    else:
        data = _strict_json(trace_path)
        events = data["events"]
    trace_schema = data.get("trace_schema_version")
    expected_schema = 3 if require_core3_observability else 2
    if type(trace_schema) is not int or trace_schema != expected_schema:
        raise SimulationTraceError(
            f"CORE-3 requires trace schema version {expected_schema}"
        )
    if type(require_core3_observability) is not bool:
        raise SimulationTraceError("CORE-3 observability selection must be boolean")
    v7_task_energy_selected = (
        model_energy_unit_joules is not None
        or expected_task_energy_j_per_tick is not None
        or task_energy_factor_provenance is not None
    )
    if v7_task_energy_selected and (
        not require_core3_observability
        or model_energy_unit_joules is None
        or expected_task_energy_j_per_tick is None
        or task_energy_factor_provenance is None
    ):
        raise SimulationTraceError(
            "CORE-3 V7 task energy validation inputs must be supplied together"
        )
    if data.get("taskset_semantic_hash") != expected_taskset_hash:
        raise SimulationTraceError("RTA/simulation taskset hash mismatch")
    if data.get("configured_scheduler") != expected_scheduler:
        raise SimulationTraceError(
            "trace scheduler mismatch: expected "
            f"{expected_scheduler}, got {data.get('configured_scheduler')!r}"
        )
    if data.get("simulation_completed") is not True:
        raise SimulationTraceError("simulation did not report complete horizon")
    if data.get("simulation_completion_reason") != "reached_horizon":
        raise SimulationTraceError("simulation completion reason is not reached_horizon")
    if _integer(data.get("expected_simulation_horizon_ms"), "expected horizon") != horizon:
        raise SimulationTraceError("trace expected horizon mismatch")
    if _integer(data.get("observed_simulation_end_ms"), "observed horizon") != horizon:
        raise SimulationTraceError("trace observed horizon mismatch")

    if require_core3_observability:
        if (
            type(release_horizon) is not int
            or release_horizon <= 0
            or horizon <= release_horizon
        ):
            raise SimulationTraceError("CORE-3 release/observation horizon is invalid")
        if data.get("release_cutoff_enabled") is not True:
            raise SimulationTraceError("CORE-3 trace did not enable release cutoff")
        if _integer(data.get("release_horizon_ms"), "release horizon") != release_horizon:
            raise SimulationTraceError("trace release horizon mismatch")
        if _integer(data.get("observation_horizon_ms"), "observation horizon") != horizon:
            raise SimulationTraceError("trace observation horizon mismatch")
        if data.get("observation_horizon_reached") is not True:
            raise SimulationTraceError("trace did not reach the observation horizon")
        if data.get("observability_summary_contract_version") != 2:
            raise SimulationTraceError("CORE-3 requires observability contract version 2")
        if _integer(
            data.get("observability_summary_horizon_ms"),
            "observability summary horizon",
        ) != horizon:
            raise SimulationTraceError("observability summary horizon mismatch")
        if physical_initial_energy is None or battery_capacity is None:
            raise SimulationTraceError("CORE-3 energy bounds were not supplied")
        if (
            physical_initial_energy < 0
            or physical_initial_energy > battery_capacity
        ):
            raise SimulationTraceError("CORE-3 initial energy bounds are invalid")
        if (
            not math.isfinite(energy_tolerance_j)
            or energy_tolerance_j < 0
        ):
            raise SimulationTraceError("CORE-3 energy tolerance is invalid")

    definitions = {str(row["task_id"]): row for row in task_payload}
    names = {f"v93_task_{task_id}": task_id for task_id in definitions}
    if len(names) != len(task_payload):
        raise SimulationTraceError("duplicate task ID in frozen taskset")
    if require_core3_observability and len(definitions) != 10:
        raise SimulationTraceError("CORE-3 schema-3 preflight requires exactly 10 tasks")
    if require_core3_observability:
        dmax = max(int(row["D"]) for row in task_payload)
        if horizon != release_horizon + dmax:
            raise SimulationTraceError("observation horizon must equal release horizon + Dmax")

    raw_jobs: Dict[tuple[str, int], Dict[str, Any]] = {}
    release_energies: list[float] = []
    arrival_jobs: set[tuple[str, int]] = set()
    release_energy_snapshots: Dict[tuple[str, int], float] = {}
    observed_power: Dict[str, float] = {}
    running_since: Dict[tuple[str, int], int] = {}
    bypass_count = 0
    sync_wait_ticks: set[int] = set()
    st_charge_begin_count = 0
    st_charge_hold_ticks: set[int] = set()
    st_charge_release_count = 0
    st_charge_release_reasons: list[str] = []
    idle_ready_ticks: set[int] = set()
    battery_samples: list[tuple[int, float]] = []
    harvested_samples: list[float] = []
    consumed_samples: list[float] = []

    scheduler_parts = expected_scheduler.split("_")
    if len(scheduler_parts) != 3:
        raise SimulationTraceError("invalid expected scheduler identity")
    mechanism_display = {
        "block": "Block", "nonblock": "NonBlock", "sync": "Sync",
    }.get(scheduler_parts[2])
    if mechanism_display is None:
        raise SimulationTraceError("invalid expected scheduler mechanism")
    expected_display_scheduler = f"{scheduler_parts[1].upper()}-{mechanism_display}"

    def validate_mechanism_scheduler(event: Mapping[str, Any], allowed: bool) -> None:
        if not allowed or event.get("scheduler") != expected_display_scheduler:
            raise SimulationTraceError("mechanism event scheduler/applicability mismatch")

    def validate_named_task(value: Any, label: str) -> None:
        if value not in names:
            raise SimulationTraceError(f"{label} has unknown task name")

    def job_for(name: Any, release_value: Any) -> Dict[str, Any]:
        if name not in names:
            raise SimulationTraceError(f"unknown trace task name: {name!r}")
        release = _integer(release_value, "job release")
        key = (str(name), release)
        if key not in raw_jobs:
            task_id = names[str(name)]
            deadline = release + int(definitions[task_id]["D"])
            raw_jobs[key] = {
                "task_id": task_id,
                "task_name": str(name),
                "release": release,
                "absolute_deadline": deadline,
                "completion": None,
                "first_execution": None,
                "preemptions": 0,
                "energy_blocked": set(),
                "executing": set(),
                "miss": False,
            }
        return raw_jobs[key]

    def close_running_interval(
        name: Any, job: Dict[str, Any], end: int,
    ) -> Optional[int]:
        key = (str(name), job["release"])
        start = running_since.pop(key, None)
        if start is None:
            return None
        if end < start:
            raise SimulationTraceError(
                "negative execution interval: "
                f"trace={trace_path}; request={trace_path.stem}; "
                f"task={name!r}; release={job['release']}; "
                f"start={start}; end={end}"
            )
        job["executing"].update(range(start, end))
        return start

    for position, event in enumerate(events):
        if not isinstance(event, dict):
            raise SimulationTraceError(f"event {position} is not an object")
        event_type = event.get("event_type")
        event_time = _integer(event.get("time"), f"event {position} time")
        if event_time < 0 or event_time > horizon:
            raise SimulationTraceError("event time lies outside simulation horizon")
        if "current_energy_mJ" in event:
            battery = _finite(event["current_energy_mJ"], "current energy") / 1000.0
            harvested = _finite(event.get("total_harvested_mJ", 0), "harvested energy") / 1000.0
            consumed = _finite(event.get("total_consumed_mJ", 0), "consumed energy") / 1000.0
            if min(battery, harvested, consumed) < -1e-12:
                raise SimulationTraceError("negative cumulative energy observation")
            battery_samples.append((event_time, battery))
            harvested_samples.append(harvested)
            consumed_samples.append(consumed)
        if event_type == "arrival":
            job = job_for(event.get("task_name"), event.get("arrival_time"))
            if job["release"] != event_time:
                raise SimulationTraceError("arrival event time/release mismatch")
            key = (str(event.get("task_name")), job["release"])
            if key in arrival_jobs:
                raise SimulationTraceError("duplicate arrival event for one job")
            if (
                require_core3_observability
                and release_horizon is not None
                and job["release"] >= release_horizon
            ):
                raise SimulationTraceError("job release is not before H_rel")
            arrival_jobs.add(key)
            energy_j = _finite(event.get("current_energy_mJ"), "arrival energy") / 1000.0
            if energy_j < -1e-12:
                raise SimulationTraceError("negative arrival energy")
            if not require_core3_observability:
                release_energies.append(energy_j)
        elif event_type == "release_energy_snapshot":
            if not require_core3_observability:
                continue
            name = event.get("task_name")
            if name not in names:
                raise SimulationTraceError("release snapshot has unknown task")
            arrival = _integer(
                event.get("arrival_time"), "release snapshot arrival",
            )
            if arrival != event_time:
                raise SimulationTraceError("release snapshot time/release mismatch")
            if release_horizon is not None and arrival >= release_horizon:
                raise SimulationTraceError("release snapshot is not before H_rel")
            if event.get("sampling_stage") != CORE3_RELEASE_ENERGY_SAMPLING_STAGE:
                raise SimulationTraceError("release snapshot sampling stage mismatch")
            if event.get("scheduler") != expected_scheduler:
                raise SimulationTraceError("release snapshot scheduler mismatch")
            key = (str(name), arrival)
            if key in release_energy_snapshots:
                raise SimulationTraceError("duplicate release energy snapshot")
            energy_j = _finite(
                event.get("available_energy_mJ"), "release snapshot energy",
            ) / 1000.0
            if energy_j < 0:
                raise SimulationTraceError("negative release snapshot energy")
            release_energy_snapshots[key] = energy_j
        elif event_type == "scheduled":
            name = event.get("task_name")
            release = event.get("arrival_time")
            job = job_for(name, release)
            key = (str(name), job["release"])
            if job["first_execution"] is None:
                job["first_execution"] = event_time
            running_since.setdefault(key, event_time)
            power = _finite(event.get("task_unit_energy_mJ"), "task unit energy") / 1000.0
            prior = observed_power.get(job["task_id"])
            if prior is not None and not math.isclose(prior, power, rel_tol=1e-9, abs_tol=1e-12):
                raise SimulationTraceError("task power changed within one trace")
            observed_power[job["task_id"]] = power
        elif event_type in {"descheduled", "end_instance"}:
            name = event.get("task_name")
            job = job_for(name, event.get("arrival_time"))
            close_running_interval(name, job, event_time)
            if event_type == "descheduled" and event.get("reason") == "preemption":
                job["preemptions"] += 1
            if event_type == "end_instance":
                if job["completion"] is not None:
                    raise SimulationTraceError("duplicate job completion")
                job["completion"] = event_time
        elif event_type == "dline_miss":
            name = event.get("task_name")
            job = job_for(name, event.get("arrival_time"))
            reported_deadline = _integer(event.get("deadline"), "miss deadline")
            if reported_deadline != job["absolute_deadline"] or event_time < reported_deadline:
                raise SimulationTraceError("deadline-miss payload mismatch")
            running_start = close_running_interval(name, job, event_time)
            remaining = _integer(
                event.get("remaining_execution_ms"),
                "deadline-miss remaining execution",
            )
            wcet = _integer(
                definitions[job["task_id"]].get("C"),
                "task WCET",
            )
            executed = len(job["executing"])
            if remaining <= 0 or executed + remaining != wcet:
                interval = (
                    "none" if running_start is None
                    else f"[{running_start},{event_time})"
                )
                job_id = event.get(
                    "job_id", f"{name}@{job['release']}"
                )
                raise SimulationTraceError(
                    "deadline-miss execution invariant failed: "
                    f"trace={trace_path}; request={trace_path.stem}; "
                    f"run_id={data.get('run_id')!r}; job={job_id!r}; "
                    f"task={name!r}; release={job['release']}; "
                    f"wcet={wcet}; executed={executed}; "
                    f"remaining={remaining}; miss_time={event_time}; "
                    f"running_interval={interval}"
                )
            job["miss"] = True
        elif event_type == "scheduler_decision":
            ready = event.get("ready_jobs")
            selected = event.get("selected_jobs")
            if not isinstance(ready, list) or not isinstance(selected, list):
                raise SimulationTraceError("scheduler decision has invalid job arrays")
            selected_keys = set()
            for nested in selected:
                if not isinstance(nested, dict):
                    raise SimulationTraceError("selected job is not an object")
                selected_job = job_for(nested.get("task_name"), nested.get("arrival_time"))
                selected_keys.add((str(nested.get("task_name")), selected_job["release"]))
            reason = event.get("decision_reason")
            if expected_processors is not None and ready:
                expected_selected = min(expected_processors, len(ready))
                if len(selected) < expected_selected:
                    idle_ready_ticks.add(event_time)
            stopped_by_energy = reason in {
                "highest_priority_energy_insufficient", "prefix_energy_insufficient"
            }
            for nested in ready:
                if not isinstance(nested, dict):
                    raise SimulationTraceError("ready job is not an object")
                ready_job = job_for(nested.get("task_name"), nested.get("arrival_time"))
                key = (str(nested.get("task_name")), ready_job["release"])
                if key in selected_keys:
                    ready_job["executing"].add(event_time)
                elif stopped_by_energy:
                    ready_job["energy_blocked"].add(event_time)
        elif event_type == "nonblock_bypass":
            validate_mechanism_scheduler(
                event, expected_scheduler == "gpfp_asap_nonblock",
            )
            validate_named_task(
                event.get("blocked_higher_priority_task"), "nonblock blocked task",
            )
            validate_named_task(event.get("bypassed_task"), "nonblock bypassed task")
            for field in (
                "blocked_task_unit_energy_mJ", "bypassed_task_unit_energy_mJ",
                "available_energy_mJ",
            ):
                _finite(event.get(field), field)
            if event.get("reason") != "lower_priority_bypass_due_to_energy":
                raise SimulationTraceError("invalid nonblock bypass reason")
            bypass_count += 1
        elif event_type == "sync_batch_block":
            validate_mechanism_scheduler(
                event, expected_scheduler == "gpfp_asap_sync",
            )
            batch = event.get("batch_tasks")
            if not isinstance(batch, list) or not batch:
                raise SimulationTraceError("sync batch event has no batch tasks")
            for nested in batch:
                if not isinstance(nested, dict):
                    raise SimulationTraceError("sync batch task is not an object")
                validate_named_task(nested.get("task_name"), "sync batch task")
            _finite(event.get("batch_required_energy_mJ"), "sync batch required energy")
            _finite(event.get("available_energy_mJ"), "sync batch available energy")
            if not isinstance(event.get("feasible_subset_exists"), bool):
                raise SimulationTraceError("sync feasible-subset flag must be boolean")
            if event.get("reason") != "sync_batch_energy_insufficient":
                raise SimulationTraceError("invalid sync batch block reason")
            sync_wait_ticks.add(event_time)
        elif event_type in {"st_charge_begin", "st_charge_hold", "st_charge_release"}:
            validate_mechanism_scheduler(event, expected_scheduler.startswith("gpfp_st_"))
            blocked_task = event.get("blocked_task")
            blocked_group = event.get("blocked_group")
            if blocked_task is not None:
                validate_named_task(blocked_task, "ST blocked task")
            elif isinstance(blocked_group, list) and blocked_group:
                for nested in blocked_group:
                    if not isinstance(nested, dict):
                        raise SimulationTraceError("ST blocked-group task is not an object")
                    validate_named_task(nested.get("task_name"), "ST blocked-group task")
            else:
                raise SimulationTraceError("ST event has no blocked task/group")
            for field in ("available_energy_mJ", "required_energy_mJ", "slack_at_begin"):
                _finite(event.get(field), field)
            if event_type == "st_charge_begin":
                st_charge_begin_count += 1
            elif event_type == "st_charge_hold":
                st_charge_hold_ticks.add(event_time)
            else:
                reason_value = event.get("release_reason")
                if reason_value not in {
                    "battery_full", "slack_exhausted",
                    "battery_full_and_slack_exhausted",
                }:
                    raise SimulationTraceError("invalid ST charge release reason")
                st_charge_release_count += 1
                st_charge_release_reasons.append(str(reason_value))

    if require_core3_observability:
        observed_jobs = set(raw_jobs)
        if observed_jobs != arrival_jobs:
            raise SimulationTraceError(
                "arrival events and observed lifecycle jobs do not match"
            )
        if arrival_jobs != set(release_energy_snapshots):
            missing = sorted(arrival_jobs - set(release_energy_snapshots))
            unknown = sorted(set(release_energy_snapshots) - arrival_jobs)
            raise SimulationTraceError(
                "arrival/release snapshot job sets differ: "
                f"missing={missing[:3]}, unknown={unknown[:3]}"
            )
        if not arrival_jobs:
            raise SimulationTraceError(
                "CORE-3 trace contains no released jobs before H_rel"
            )
        if any(
            raw["absolute_deadline"] > horizon for raw in raw_jobs.values()
        ):
            raise SimulationTraceError(
                "a released job deadline exceeds the observation horizon"
            )
        release_energies = [
            release_energy_snapshots[key] for key in sorted(arrival_jobs)
        ]

    ordered_by_task: Dict[str, list[Dict[str, Any]]] = {
        task_id: [] for task_id in definitions
    }
    for raw in raw_jobs.values():
        ordered_by_task[raw["task_id"]].append(raw)
    for values in ordered_by_task.values():
        values.sort(key=lambda row: row["release"])

    observations: list[JobObservation] = []
    task_order = sorted(
        definitions,
        key=lambda value: (int(definitions[value]["priority_rank"]), value),
    )
    if [int(definitions[value]["priority_rank"]) for value in task_order] != list(
        range(len(task_order))
    ):
        raise SimulationTraceError("task priority order is not canonical")
    for task_id in task_order:
        definition = definitions[task_id]
        for job_index, raw in enumerate(ordered_by_task[task_id]):
            completion = raw["completion"]
            response = None if completion is None else completion - raw["release"]
            if response is not None and response < 0:
                raise SimulationTraceError("negative job response time")
            if completion is not None and completion > raw["absolute_deadline"]:
                raw["miss"] = True
            if raw["miss"] and completion is not None and completion <= raw["absolute_deadline"]:
                raise SimulationTraceError("trace marks an on-time completion as missed")
            eligible = raw["release"] >= warmup
            censored = completion is None and not raw["miss"]
            censor_reason = "UNFINISHED_AT_HORIZON" if censored else None
            executed = len({tick for tick in raw["executing"] if tick < (completion or horizon)})
            blocked = len({tick for tick in raw["energy_blocked"] if tick < (completion or horizon)})
            processor_wait = None
            if response is not None:
                processor_wait = response - executed - blocked
                if processor_wait < 0:
                    raise SimulationTraceError("negative derived processor-wait time")
                if executed < int(definition["C"]):
                    raise SimulationTraceError("completed job has fewer execution ticks than C")
            observations.append(JobObservation(
                task_id, job_index, raw["release"], completion,
                raw["absolute_deadline"], response, bool(raw["miss"]),
                raw["first_execution"], int(raw["preemptions"]), blocked,
                processor_wait, executed, eligible, censored, censor_reason,
                raw["task_name"],
                release_energy_snapshots.get(
                    (raw["task_name"], raw["release"]),
                ),
                (
                    CORE3_RELEASE_ENERGY_SAMPLING_STAGE
                    if require_core3_observability else None
                ),
            ))

    task_observations = []
    for task_id in task_order:
        eligible_jobs = [
            job for job in observations
            if job.task_id == task_id and job.eligible_after_warmup
        ]
        completed = [job for job in eligible_jobs if job.completion is not None]
        missed = [job for job in eligible_jobs if job.deadline_miss]
        censored = [job for job in eligible_jobs if job.censored]
        observed = len(eligible_jobs)
        task_observations.append(TaskObservation(
            task_id, observed, len(completed), len(missed), len(censored),
            max((job.response_time for job in completed if job.response_time is not None), default=None),
            (len(completed) / observed) if observed else 0.0,
            len(completed) >= minimum_jobs_per_task,
        ))

    task_energy_validation: list[dict[str, Any]] | None = None
    if expected_task_energy_j_per_tick is not None:
        if not require_core3_observability:
            raise SimulationTraceError(
                "task energy validation is only valid for CORE-3 observability"
            )
        if (
            not isinstance(expected_task_energy_j_per_tick, Mapping)
            or set(expected_task_energy_j_per_tick) != set(definitions)
            or not isinstance(task_energy_factor_provenance, Mapping)
            or set(task_energy_factor_provenance) != set(definitions)
        ):
            raise SimulationTraceError(
                "expected task energy/provenance task IDs do not match payload"
            )
        unknown = set(observed_power) - set(definitions)
        if unknown:
            raise SimulationTraceError(
                f"observed task energy contains unknown tasks: {sorted(unknown)}"
            )
        executed_by_task = {
            task_id: sum(
                job.executed_ticks for job in observations
                if job.task_id == task_id
            )
            for task_id in definitions
        }
        task_energy_validation = []
        for task_id in task_order:
            expected = expected_task_energy_j_per_tick[task_id]
            if type(expected) is not Fraction or expected <= 0:
                raise SimulationTraceError(
                    f"task {task_id} expected physical energy is invalid"
                )
            observed = observed_power.get(task_id)
            if executed_by_task[task_id] > 0 and observed is None:
                raise SimulationTraceError(
                    f"task {task_id} executed without a unit-energy observation; "
                    f"factor_provenance={task_energy_factor_provenance[task_id]}"
                )
            matches = observed is None or math.isclose(
                observed, float(expected), rel_tol=0.0,
                abs_tol=energy_tolerance_j,
            )
            if not matches:
                raise SimulationTraceError(
                    f"task {task_id} physical energy mismatch: "
                    f"expected={expected}, observed={observed}, "
                    f"factor_provenance={task_energy_factor_provenance[task_id]}"
                )
            task_energy_validation.append({
                "task_id": task_id,
                "expected_physical_energy_j_per_tick": str(expected),
                "observed_physical_energy_j_per_tick": observed,
                "executed_ticks": executed_by_task[task_id],
                "validated": observed is not None,
                "within_tolerance": matches,
            })

    has_miss = any(job.deadline_miss for job in observations)
    unfinished_without_miss = sum(
        job.completion is None and not job.deadline_miss
        for job in observations
    )
    enough_jobs = all(task.minimum_jobs_satisfied for task in task_observations)
    if require_core3_observability and unfinished_without_miss:
        status, reason = (
            SimulationStatus.HORIZON_INSUFFICIENT,
            "unfinished_without_miss_at_observation_horizon",
        )
    elif has_miss:
        status, reason = SimulationStatus.DEADLINE_MISS, "deadline_miss"
    elif enough_jobs:
        status, reason = SimulationStatus.PASS_OBSERVED, "minimum_jobs_observed"
    else:
        status, reason = SimulationStatus.HORIZON_INSUFFICIENT, "minimum_jobs_not_observed"
    minimum_energy = min(release_energies) if release_energies else None
    e0_float = float(release_e0)
    release_valid = bool(
        minimum_energy is not None and minimum_energy + 1e-12 >= e0_float
    )
    completed_responses = [
        job.response_time for job in observations if job.response_time is not None
    ]
    first_miss = min(
        (job.absolute_deadline for job in observations if job.deadline_miss),
        default=None,
    )
    completed_job_count = sum(
        job.completion is not None for job in observations
    )
    unfinished_job_count = sum(
        job.completion is None for job in observations
    )
    deadline_miss_job_count = sum(
        job.deadline_miss for job in observations
    )
    classified_job_count = sum(
        job.completion is not None or job.deadline_miss
        for job in observations
    )
    metrics: Dict[str, Any] = {
        "missed_jobs": sum(job.deadline_miss for job in observations),
        "first_miss_time": first_miss,
        "maximum_observed_response_time": max(completed_responses, default=None),
        "mean_response_time": (
            sum(completed_responses) / len(completed_responses)
            if completed_responses else None
        ),
        "completed_jobs": len(completed_responses),
        "preemptions": sum(job.preemption_count for job in observations),
        "processor_wait_ticks": sum(
            job.processor_wait_ticks for job in observations
            if job.processor_wait_ticks is not None
        ),
        "energy_blocked_ticks": sum(job.energy_blocked_ticks for job in observations),
        "bypass_count": bypass_count,
        "synchronization_wait_ticks": len(sync_wait_ticks),
        "st_charge_begin_count": st_charge_begin_count,
        "st_charge_hold_ticks": len(st_charge_hold_ticks),
        "st_charge_release_count": st_charge_release_count,
        "st_charge_release_reasons": st_charge_release_reasons,
        "idle_cores_while_ready_jobs_exist_ticks": (
            len(idle_ready_ticks) if expected_processors is not None else None
        ),
        "harvested_energy_j": max(harvested_samples, default=None),
        "consumed_energy_j": max(consumed_samples, default=None),
        "battery_minimum_j": (
            min((value for _, value in battery_samples), default=None)
        ),
        "battery_maximum_j": (
            max((value for _, value in battery_samples), default=None)
        ),
        "battery_trajectory": [
            {"time": tick, "energy_j": value} for tick, value in battery_samples
        ],
        "released_job_count": len(observations),
        "completed_job_count": completed_job_count,
        "deadline_miss_job_count": deadline_miss_job_count,
        "unfinished_job_count": unfinished_job_count,
        "unfinished_without_miss_count": unfinished_without_miss,
        "classified_job_count": classified_job_count,
    }
    if require_core3_observability:
        try:
            conservation_rule = (
                require_normalized_core3_energy_conservation_rule_v1(
                    energy_conservation_rule
                )
            )
        except RTA4Core3ContractV6Error as exc:
            raise SimulationTraceError(str(exc)) from exc
        release_energy_values = [
            job.release_energy_j
            for job in observations
            if job.release_energy_j is not None
        ]
        conditional_coverage = (
            conditional_release_coverage_v7(
                release_energy_values,
                conditional_e0,
                model_energy_unit_joules,
            )
            if model_energy_unit_joules is not None
            else conditional_release_coverage_v6(
                release_energy_values, conditional_e0,
            )
        )

        energy = data.get("energy_summary")
        if not isinstance(energy, Mapping):
            raise SimulationTraceError("CORE-3 schema 3 is missing energy_summary")
        scalar_fields = (
            "offered_energy_j", "credited_energy_j", "clipped_energy_j",
            "consumed_energy_j", "battery_min_j", "battery_max_j",
            "battery_final_j",
        )
        counter_fields = (
            "battery_empty_ticks", "battery_full_ticks",
            "observed_energy_intervals",
        )
        energy_values = {
            field: _finite(energy.get(field), f"energy_summary.{field}")
            for field in scalar_fields
        }
        energy_counts = {
            field: _integer(energy.get(field), f"energy_summary.{field}")
            for field in counter_fields
        }
        if any(
            value < 0
            for value in (*energy_values.values(), *energy_counts.values())
        ):
            raise SimulationTraceError("energy_summary contains a negative value")
        if energy_counts["observed_energy_intervals"] != horizon:
            raise SimulationTraceError("energy observation interval count mismatch")
        if any(
            energy_counts[field] > energy_counts["observed_energy_intervals"]
            for field in ("battery_empty_ticks", "battery_full_ticks")
        ):
            raise SimulationTraceError("battery boundary tick count exceeds horizon")

        if not core3_energy_conservation_close_v1(
            energy_values["offered_energy_j"],
            energy_values["credited_energy_j"]
            + energy_values["clipped_energy_j"],
            conservation_rule,
        ):
            raise SimulationTraceError("offered energy does not close")
        initial = float(physical_initial_energy)
        capacity = float(battery_capacity)
        if not core3_energy_conservation_close_v1(
            initial + energy_values["credited_energy_j"],
            energy_values["consumed_energy_j"]
            + energy_values["battery_final_j"],
            conservation_rule,
        ):
            raise SimulationTraceError("battery energy balance does not close")
        if not (
            -energy_tolerance_j <= energy_values["battery_min_j"]
            and energy_values["battery_min_j"] - energy_tolerance_j
            <= energy_values["battery_final_j"]
            and energy_values["battery_final_j"] - energy_tolerance_j
            <= energy_values["battery_max_j"]
            and energy_values["battery_max_j"]
            <= capacity + energy_tolerance_j
        ):
            raise SimulationTraceError("battery bounds are inconsistent")

        offered = energy_values["offered_energy_j"]
        clipped = energy_values["clipped_energy_j"]
        theorem_valid = not theorem_alignment_track or (
            clipped <= energy_tolerance_j
        )
        metrics.update({
            "conditional_coverage": conditional_coverage,
            "minimum_release_energy_j": min(release_energies),
            "maximum_release_energy_j": max(release_energies),
            "mean_release_energy_j": (
                sum(release_energies) / len(release_energies)
            ),
            **energy_values,
            **energy_counts,
            "overflow_energy_j": clipped,
            "overflow_ratio_numerator": clipped,
            "overflow_ratio_denominator": offered,
            "overflow_ratio": clipped / offered if offered else 0.0,
            "battery_full_tick_ratio": (
                energy_counts["battery_full_ticks"]
                / energy_counts["observed_energy_intervals"]
            ),
            "battery_empty_tick_ratio": (
                energy_counts["battery_empty_ticks"]
                / energy_counts["observed_energy_intervals"]
            ),
            "theorem_alignment_valid": theorem_valid,
            "theorem_alignment_failure_reason": (
                None if theorem_valid else "ENERGY_OVERFLOW_OBSERVED"
            ),
            "energy_tolerance_j": energy_tolerance_j,
            "energy_conservation_rule": conservation_rule,
            **({
                "task_energy_validation": task_energy_validation,
            } if task_energy_validation is not None else {}),
        })
    return SimulationResult(
        status, reason, horizon, tuple(observations), tuple(task_observations),
        release_valid, minimum_energy, observed_power, trace_schema,
        str(data["configured_scheduler"]), True,
        str(data["simulation_completion_reason"]),
        metrics,
    )
