#!/usr/bin/env python3
"""Offline sufficient response-time analysis for ASAP-BLOCK.

The implementation follows docs/asap_block_rta_final_discrete_coupled.md.
It intentionally supports only the restricted task model documented by the
CLI help and uses a one millisecond discrete tick.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import warnings
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import yaml


TICK_SECONDS = 0.001

DEFAULT_WORKLOAD_COEFFICIENTS = {
    "bzip2": 1.2,
    "hash": 0.8,
    "encrypt": 1.5,
    "decrypt": 1.5,
    "control": 0.1,
    "idle": 0.1,
}

DEFAULT_FREQUENCY_POWER_RATIOS = {
    7000: 0.85,
    7500: 0.88,
    8000: 0.92,
    8100: 0.93,
    8200: 0.94,
    8300: 0.95,
    8400: 0.96,
    8500: 0.97,
    9000: 1.00,
    9500: 1.05,
    10000: 1.10,
    10500: 1.15,
}

FIXED_RE = re.compile(
    r"^\s*fixed\(\s*(\d+)\s*,\s*([A-Za-z0-9_.+\-]+)\s*\)\s*;?\s*$"
)

DAG_FIELDS = {
    "dag",
    "dependencies",
    "edges",
    "nodes",
    "predecessors",
    "successors",
}
RESOURCE_FIELDS = {"critical_sections", "locks", "resources"}
TASK_FREQUENCY_FIELDS = {
    "freq",
    "frequency",
    "frequency_mhz",
    "frequency_ratio",
}
RESOURCE_INSTRUCTION_RE = re.compile(
    r"\b(?:critical_section|get_resource|lock|resource|unlock|wait)\s*\(",
    re.IGNORECASE,
)
ENERGY_DP_STATE_LIMIT = 200000


class RTAError(Exception):
    """Base error for the offline analyzer."""


class InputValidationError(RTAError):
    """Raised when an input is outside the supported first-version model."""


def _exact_fraction(value: Union[int, float, Fraction]) -> Fraction:
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


@dataclass
class RTATask:
    name: str
    period: int
    wcet: int
    deadline: int
    workload: str
    yaml_index: int
    energy_per_tick: float = 0.0
    _taskset: Tuple["RTATask", ...] = field(
        default_factory=tuple, repr=False, compare=False
    )
    _num_cores: Optional[int] = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class RTASystemConfig:
    num_cores: int
    base_frequency: float
    base_power: float
    workload_coefficients: Mapping[str, float]
    frequency_power_ratios: Mapping[int, float]
    initial_energy: float
    max_energy: float
    use_real_solar_data: bool
    solar_data_file: str
    pv_efficiency: float
    pv_area_m2: float
    day_of_year: int
    time_of_day_ms: int
    base_harvesting_rate: float
    source_path: str

    def workload_coefficient(self, workload: str) -> float:
        if workload in self.workload_coefficients:
            return float(self.workload_coefficients[workload])
        warnings.warn(
            "unknown workload {!r}; using scheduler fallback coefficient 1.0".format(
                workload
            ),
            RuntimeWarning,
        )
        return 1.0

    def frequency_ratio(self, frequency: Optional[float] = None) -> float:
        if not self.frequency_power_ratios:
            return 1.0
        requested = self.base_frequency if frequency is None else frequency
        closest = min(
            self.frequency_power_ratios,
            key=lambda value: (abs(value - requested), value),
        )
        return float(self.frequency_power_ratios[closest])

    def task_energy_per_tick(self, workload: str) -> float:
        power = (
            self.base_power
            * self.workload_coefficient(workload)
            * self.frequency_ratio()
        )
        return power * TICK_SECONDS


@dataclass
class TaskAnalysisResult:
    task_name: str
    period: int
    wcet: int
    deadline: int
    workload: str
    energy_per_tick: float
    response_time_bound: Optional[int]
    proven: bool
    failure_reason: Optional[str]
    iterations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result.update(
            {
                "conditional": True,
                "proven_under_assumptions": self.proven,
                "absolute_schedulability_claim": False,
            }
        )
        return result


@dataclass(frozen=True)
class _EnergyBlockingResult:
    blocking: Optional[int]
    failure_reason: Optional[str] = None


@dataclass
class TasksetAnalysis:
    system_file: str
    tasks_file: str
    horizon_ms: int
    assume_no_overflow: bool
    e0: float
    tasks: List[TaskAnalysisResult]

    @property
    def proven(self) -> bool:
        return bool(self.tasks) and all(result.proven for result in self.tasks)

    @property
    def assumptions(self) -> List[str]:
        return [
            "battery does not overflow during the analyzed interval",
            "harvesting service curve is valid only within the {} ms horizon".format(
                self.horizon_ms
            ),
            "E0 is 0 J at each analyzed job release",
            "scheduler tick duration is 1 ms",
            "tasks use the restricted periodic single-segment fixed model",
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_file": self.system_file,
            "tasks_file": self.tasks_file,
            "horizon_ms": self.horizon_ms,
            "assume_no_overflow": self.assume_no_overflow,
            "E0": self.e0,
            "proven": self.proven,
            "conditional": True,
            "assumptions": self.assumptions,
            "proven_under_assumptions": self.proven,
            "absolute_schedulability_claim": False,
            "tasks": [result.to_dict() for result in self.tasks],
        }


class EnergyServiceCurve(Sequence[float]):
    """Lazy lower service curve for a finite non-negative tick trace.

    beta(delta) is the minimum energy in any contiguous interval of length
    delta contained in the supplied horizon.
    """

    def __init__(self, harvest_trace: Sequence[float], horizon_ms: int):
        self.horizon_ms = horizon_ms
        self.trace = tuple(float(value) for value in harvest_trace[:horizon_ms])
        self._exact_trace = tuple(_exact_fraction(value) for value in self.trace)
        self._exact_prefix = [Fraction(0)]
        for value in self._exact_trace:
            self._exact_prefix.append(self._exact_prefix[-1] + value)
        self._beta_cache: Dict[int, Fraction] = {0: Fraction(0)}
        self._inverse_cache: Dict[
            Tuple[Fraction, Optional[int]], Optional[int]
        ] = {}
        self._constant_rate = self._detect_constant_rate()

    def _detect_constant_rate(self) -> Optional[Fraction]:
        if not self._exact_trace:
            return Fraction(0)
        first = self._exact_trace[0]
        if all(value == first for value in self._exact_trace):
            return first
        return None

    def __len__(self) -> int:
        return self.horizon_ms + 1

    def __getitem__(self, delta: Union[int, slice]) -> Union[float, List[float]]:
        if isinstance(delta, slice):
            start, stop, step = delta.indices(len(self))
            return [self[index] for index in range(start, stop, step)]
        if delta < 0:
            delta += len(self)
        if delta < 0 or delta > self.horizon_ms:
            raise IndexError("service-curve delta outside the configured horizon")
        if delta not in self._beta_cache:
            if self._constant_rate is not None:
                value = self._constant_rate * delta
            else:
                prefix = self._exact_prefix
                value = min(
                    prefix[start + delta] - prefix[start]
                    for start in range(0, self.horizon_ms - delta + 1)
                )
            self._beta_cache[delta] = max(Fraction(0), value)
        return float(self._beta_cache[delta])

    def inverse(
        self,
        energy: Union[int, float, Fraction],
        max_delta: Optional[int] = None,
    ) -> Optional[int]:
        exact_energy = _exact_fraction(energy)
        if exact_energy <= 0:
            return 0
        limit = self.horizon_ms if max_delta is None else min(
            self.horizon_ms, max_delta
        )
        key = (exact_energy, limit)
        if key in self._inverse_cache:
            return self._inverse_cache[key]

        if self._constant_rate is not None:
            if self._constant_rate <= 0:
                result = None
            else:
                ratio = exact_energy / self._constant_rate
                result = -(-ratio.numerator // ratio.denominator)
                if result > limit:
                    result = None
            self._inverse_cache[key] = result
            return result

        # For a non-negative trace, beta^-1(E) is one plus the longest
        # contiguous interval whose energy is strictly below E.
        left = 0
        current = Fraction(0)
        longest_below = 0
        for right, value in enumerate(self._exact_trace):
            current += value
            while left <= right and current >= exact_energy:
                current -= self._exact_trace[left]
                left += 1
            longest_below = max(longest_below, right - left + 1)

        result = longest_below + 1
        if result > limit or result > self.horizon_ms:
            result = None
        self._inverse_cache[key] = result
        return result

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EnergyServiceCurve):
            return self.trace == other.trace
        if isinstance(other, Sequence):
            return list(self) == list(other)
        return False


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InputValidationError("{} must be a YAML mapping".format(description))
    return value


def _positive_int(value: Any, description: str) -> int:
    if isinstance(value, bool):
        raise InputValidationError("{} must be a positive integer".format(description))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise InputValidationError(
            "{} must be a positive integer".format(description)
        )
    if parsed <= 0 or str(parsed) != str(value).strip():
        raise InputValidationError(
            "{} must be a positive integer".format(description)
        )
    return parsed


def _parse_params(params: Any, task_name: str) -> Dict[str, str]:
    if not isinstance(params, str) or not params.strip():
        raise InputValidationError(
            "task {} must provide params with period, wcet, and workload".format(
                task_name
            )
        )
    result: Dict[str, str] = {}
    for item in params.split(","):
        if "=" not in item:
            raise InputValidationError(
                "task {} has malformed params entry {!r}".format(task_name, item)
            )
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        if not key or not value or key in result:
            raise InputValidationError(
                "task {} has malformed or duplicate params key {!r}".format(
                    task_name, key
                )
            )
        result[key] = value
    return result


def load_tasks(tasks_yml: str) -> List[RTATask]:
    """Load and strictly validate the supported periodic task model."""

    try:
        with open(tasks_yml, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except OSError as exc:
        raise InputValidationError(
            "cannot read task YAML {}: {}".format(tasks_yml, exc)
        )
    except yaml.YAMLError as exc:
        raise InputValidationError(
            "cannot parse task YAML {}: {}".format(tasks_yml, exc)
        )

    root = _require_mapping(document, "task YAML root")
    root_dag_fields = sorted(DAG_FIELDS.intersection(root))
    if root_dag_fields:
        raise InputValidationError(
            "DAG tasksets are unsupported; found root field(s): {}".format(
                ", ".join(root_dag_fields)
            )
        )
    root_resource_fields = sorted(RESOURCE_FIELDS.intersection(root))
    if root_resource_fields:
        raise InputValidationError(
            "resource/lock tasksets are unsupported; found root field(s): {}".format(
                ", ".join(root_resource_fields)
            )
        )
    task_specs = root.get("taskset")
    if not isinstance(task_specs, list) or not task_specs:
        raise InputValidationError("task YAML must contain a non-empty taskset list")

    tasks: List[RTATask] = []
    seen_names = set()
    for index, raw_spec in enumerate(task_specs):
        spec = _require_mapping(raw_spec, "taskset[{}]".format(index))
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InputValidationError("taskset[{}] has no valid name".format(index))
        name = name.strip()
        if name in seen_names:
            raise InputValidationError("duplicate task name {!r}".format(name))
        seen_names.add(name)

        task_type = spec.get("type")
        if task_type is not None and str(task_type).strip():
            if not isinstance(task_type, str) or task_type.strip().lower() != "periodic":
                raise InputValidationError(
                    "task {} has unsupported non-periodic type {!r}".format(
                        name, task_type
                    )
                )

        if name.lower().startswith("dag_"):
            raise InputValidationError(
                "task {} appears to be a DAG control task; DAGs are unsupported".format(
                    name
                )
            )
        dag_fields = sorted(DAG_FIELDS.intersection(spec))
        if dag_fields:
            raise InputValidationError(
                "task {} uses unsupported DAG field(s): {}".format(
                    name, ", ".join(dag_fields)
                )
            )
        resource_fields = sorted(RESOURCE_FIELDS.intersection(spec))
        if resource_fields:
            raise InputValidationError(
                "task {} uses unsupported resource/lock field(s): {}".format(
                    name, ", ".join(resource_fields)
                )
            )
        frequency_fields = sorted(TASK_FREQUENCY_FIELDS.intersection(spec))
        if frequency_fields:
            raise InputValidationError(
                "task-level frequency is unsupported by the first RTA version; "
                "task {} contains: {}".format(name, ", ".join(frequency_fields))
            )

        period = _positive_int(spec.get("iat"), "{}.iat".format(name))
        runtime = _positive_int(spec.get("runtime"), "{}.runtime".format(name))
        deadline = _positive_int(
            spec.get("deadline"), "{}.deadline".format(name)
        )
        if deadline > period:
            raise InputValidationError(
                "task {} has deadline {} greater than period {}".format(
                    name, deadline, period
                )
            )
        if runtime > deadline:
            raise InputValidationError(
                "task {} has wcet {} greater than deadline {}; "
                "deadline-parameterized RTA requires C_i <= D_i <= T_i".format(
                    name, runtime, deadline
                )
            )

        params = _parse_params(spec.get("params"), name)
        params_dag_fields = sorted(DAG_FIELDS.intersection(params))
        if params_dag_fields:
            raise InputValidationError(
                "task {} params uses unsupported DAG field(s): {}".format(
                    name, ", ".join(params_dag_fields)
                )
            )
        params_resource_fields = sorted(RESOURCE_FIELDS.intersection(params))
        if params_resource_fields:
            raise InputValidationError(
                "task {} params uses unsupported resource/lock field(s): {}".format(
                    name, ", ".join(params_resource_fields)
                )
            )
        params_frequency_fields = sorted(
            TASK_FREQUENCY_FIELDS.intersection(params)
        )
        if params_frequency_fields:
            raise InputValidationError(
                "task-level frequency is unsupported by the first RTA version; "
                "task {} params contains: {}".format(
                    name, ", ".join(params_frequency_fields)
                )
            )
        for required in ("period", "wcet", "workload"):
            if required not in params:
                raise InputValidationError(
                    "task {} params is missing {}".format(name, required)
                )
        params_period = _positive_int(
            params["period"], "{}.params.period".format(name)
        )
        params_wcet = _positive_int(
            params["wcet"], "{}.params.wcet".format(name)
        )
        workload = params["workload"].strip()
        if not workload:
            raise InputValidationError("task {} has no workload".format(name))

        code = spec.get("code")
        if not isinstance(code, list) or len(code) != 1:
            raise InputValidationError(
                "task {} must contain exactly one fixed(C, workload) code segment".format(
                    name
                )
            )
        instruction = code[0]
        if not isinstance(instruction, str):
            raise InputValidationError(
                "task {} code segment must be a string".format(name)
            )
        if RESOURCE_INSTRUCTION_RE.search(instruction):
            raise InputValidationError(
                "task {} uses a resource/lock instruction, which is unsupported".format(
                    name
                )
            )
        fixed_match = FIXED_RE.match(instruction)
        if not fixed_match:
            raise InputValidationError(
                "task {} must use exactly fixed(C, workload)".format(name)
            )
        fixed_wcet = int(fixed_match.group(1))
        fixed_workload = fixed_match.group(2)

        if period != params_period:
            raise InputValidationError(
                "task {} has inconsistent iat={} and params.period={}".format(
                    name, period, params_period
                )
            )
        if not (runtime == params_wcet == fixed_wcet):
            raise InputValidationError(
                "task {} has inconsistent runtime={}, params.wcet={}, fixed={}".format(
                    name, runtime, params_wcet, fixed_wcet
                )
            )
        if workload != fixed_workload:
            raise InputValidationError(
                "task {} has inconsistent params workload {!r} and fixed workload {!r}".format(
                    name, workload, fixed_workload
                )
            )

        tasks.append(
            RTATask(
                name=name,
                period=period,
                wcet=runtime,
                deadline=deadline,
                workload=workload,
                yaml_index=index,
            )
        )

    taskset = tuple(tasks)
    for task in tasks:
        task._taskset = taskset
    return tasks


def _normalise_energy_model(model: Any) -> Dict[str, Any]:
    if not isinstance(model, Mapping):
        return {}
    frequency_values = model.get(
        "frequency_power_ratios", model.get("frequency_scaling", {})
    )
    return {
        "base_power": (
            float(model["base_power"]) if "base_power" in model else None
        ),
        "workload_coefficients": {
            str(key): float(value)
            for key, value in model.get("workload_coefficients", {}).items()
        },
        "frequency_power_ratios": {
            int(key): float(value)
            for key, value in frequency_values.items()
        },
    }


def _select_energy_model(energy: Mapping[str, Any]) -> Mapping[str, Any]:
    canonical = energy.get("scheduler_energy_model")
    legacy = energy.get("consumption_model")
    if canonical is not None and legacy is not None:
        if _normalise_energy_model(canonical) != _normalise_energy_model(legacy):
            warnings.warn(
                "scheduler_energy_model and consumption_model differ; "
                "using scheduler_energy_model",
                RuntimeWarning,
            )
        return _require_mapping(canonical, "scheduler_energy_model")
    if canonical is not None:
        return _require_mapping(canonical, "scheduler_energy_model")
    if legacy is not None:
        return _require_mapping(legacy, "consumption_model")
    return {}


def _select_frequency_ratios(model: Mapping[str, Any]) -> Mapping[Any, Any]:
    canonical = model.get("frequency_power_ratios")
    legacy = model.get("frequency_scaling")
    if canonical is not None and legacy is not None:
        canonical_map = {int(key): float(value) for key, value in canonical.items()}
        legacy_map = {int(key): float(value) for key, value in legacy.items()}
        if canonical_map != legacy_map:
            warnings.warn(
                "frequency_power_ratios and frequency_scaling differ; "
                "using frequency_power_ratios",
                RuntimeWarning,
            )
        return canonical
    if canonical is not None:
        return canonical
    if legacy is not None:
        return legacy
    return {}


def load_system_config(system_yml: str) -> RTASystemConfig:
    """Load scheduler, battery, harvesting, and unified energy-model values."""

    try:
        with open(system_yml, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except OSError as exc:
        raise InputValidationError(
            "cannot read system YAML {}: {}".format(system_yml, exc)
        )
    except yaml.YAMLError as exc:
        raise InputValidationError(
            "cannot parse system YAML {}: {}".format(system_yml, exc)
        )

    root = _require_mapping(document, "system YAML root")
    islands = root.get("cpu_islands")
    if not isinstance(islands, list) or not islands:
        raise InputValidationError(
            "system YAML must contain at least one CPU island"
        )
    island = _require_mapping(islands[0], "cpu_islands[0]")
    num_cores = _positive_int(island.get("numcpus"), "cpu_islands[0].numcpus")
    try:
        base_frequency = float(island.get("base_freq", 8100.0))
    except (TypeError, ValueError):
        raise InputValidationError("cpu_islands[0].base_freq must be numeric")

    energy = _require_mapping(
        root.get("energy_management", {}), "energy_management"
    )
    model = _select_energy_model(energy)
    base_power = float(model.get("base_power", 0.5))
    workload_coefficients = dict(DEFAULT_WORKLOAD_COEFFICIENTS)
    raw_coefficients = model.get("workload_coefficients", {})
    if raw_coefficients:
        if not isinstance(raw_coefficients, Mapping):
            raise InputValidationError("workload_coefficients must be a mapping")
        workload_coefficients.update(
            {str(key): float(value) for key, value in raw_coefficients.items()}
        )

    frequency_ratios = dict(DEFAULT_FREQUENCY_POWER_RATIOS)
    raw_ratios = _select_frequency_ratios(model)
    if raw_ratios:
        if not isinstance(raw_ratios, Mapping):
            raise InputValidationError(
                "frequency power ratios must be a mapping"
            )
        frequency_ratios.update(
            {int(key): float(value) for key, value in raw_ratios.items()}
        )

    try:
        initial_energy = float(energy.get("initial_energy", 200.0))
        max_energy = float(energy.get("max_energy", 800.0))
        pv_efficiency = float(energy.get("pv_efficiency", 0.18))
        pv_area_m2 = float(energy.get("pv_area_m2", 1.0))
        day_of_year = int(energy.get("day_of_year", 187))
        time_of_day_ms = int(energy.get("time_of_day_ms", 0))
        base_harvesting_rate = float(
            energy.get(
                "base_harvesting_rate",
                energy.get("base_harvest_rate", 0.054),
            )
        )
    except (TypeError, ValueError) as exc:
        raise InputValidationError(
            "system energy configuration contains a non-numeric value: {}".format(
                exc
            )
        )
    if max_energy <= 0:
        raise InputValidationError("max_energy must be positive")
    if pv_efficiency <= 0 or pv_area_m2 <= 0:
        raise InputValidationError("pv_efficiency and pv_area_m2 must be positive")
    if day_of_year <= 0:
        raise InputValidationError("day_of_year must be positive")

    return RTASystemConfig(
        num_cores=num_cores,
        base_frequency=base_frequency,
        base_power=base_power,
        workload_coefficients=workload_coefficients,
        frequency_power_ratios=frequency_ratios,
        initial_energy=initial_energy,
        max_energy=max_energy,
        use_real_solar_data=bool(energy.get("use_real_solar_data", False)),
        solar_data_file=str(
            energy.get(
                "solar_data_file", "data/processed/shenyang_solar_minute.csv"
            )
        ),
        pv_efficiency=pv_efficiency,
        pv_area_m2=pv_area_m2,
        day_of_year=day_of_year,
        time_of_day_ms=time_of_day_ms,
        base_harvesting_rate=base_harvesting_rate,
        source_path=os.path.abspath(system_yml),
    )


def rm_order(tasks: Sequence[RTATask]) -> List[RTATask]:
    """Return RM order with YAML position as the stable tie-break."""

    return sorted(tasks, key=lambda task: (task.period, task.yaml_index))


Target = Union[RTATask, int, str]


def _resolve_target(tasks: Sequence[RTATask], k: Target) -> RTATask:
    if isinstance(k, RTATask):
        for task in tasks:
            if task is k:
                return task
        for task in tasks:
            if task.name == k.name and task.yaml_index == k.yaml_index:
                return task
        raise ValueError("target task is not part of the supplied taskset")
    if isinstance(k, int):
        try:
            return tasks[k]
        except IndexError:
            raise ValueError("target task index {} is out of range".format(k))
    if isinstance(k, str):
        matches = [task for task in tasks if task.name == k]
        if len(matches) != 1:
            raise ValueError("target task name {!r} is not unique".format(k))
        return matches[0]
    raise TypeError("target task must be RTATask, index, or name")


def _taskset_for(k: Target, tasks: Optional[Sequence[RTATask]]) -> Sequence[RTATask]:
    if tasks is not None:
        return tasks
    if isinstance(k, RTATask) and k._taskset:
        return k._taskset
    raise ValueError("the complete taskset is required for this calculation")


def hp(tasks: Sequence[RTATask], k: Target) -> List[RTATask]:
    """Return tasks with higher RM priority than k."""

    target = _resolve_target(tasks, k)
    ordered = rm_order(tasks)
    position = ordered.index(target)
    return ordered[:position]


def _lp(tasks: Sequence[RTATask], k: Target) -> List[RTATask]:
    target = _resolve_target(tasks, k)
    ordered = rm_order(tasks)
    position = ordered.index(target)
    return ordered[position + 1 :]


def workload_bound(task: RTATask, w: int) -> int:
    """Deadline-parameterized W_i^D(w), not the old carry-in W_i^H(w)."""

    if w < 0:
        raise ValueError("response window w must be non-negative")
    window = w + task.deadline - task.wcet
    if window <= 0:
        return 0
    jobs = window // task.period
    residual = window - jobs * task.period
    return jobs * task.wcet + min(task.wcet, residual)


def _processor_workloads(
    target: RTATask, w: int, tasks: Sequence[RTATask]
) -> Dict[str, int]:
    interference_cap = max(w - target.wcet + 1, 0)
    return {
        task.name: min(workload_bound(task, w), interference_cap)
        for task in hp(tasks, target)
    }


def processor_delay(
    k: Target,
    w: int,
    M: int,
    tasks: Optional[Sequence[RTATask]] = None,
) -> int:
    """Compute the deadline-parameterized CPU-only delay D_k^{P,D}(w)."""

    if M <= 0:
        raise ValueError("M must be positive")
    taskset = _taskset_for(k, tasks)
    target = _resolve_target(taskset, k)
    bars = list(_processor_workloads(target, w, taskset).values())
    if not bars:
        return 0
    maximum_candidate = sum(bars) // M
    maximum_delay = 0
    for delay in range(maximum_candidate + 1):
        if sum(min(value, delay) for value in bars) >= M * delay:
            maximum_delay = delay
    return maximum_delay


def build_energy_service_curve(
    harvest_trace: Sequence[float], horizon_ms: int
) -> EnergyServiceCurve:
    """Build beta_l from per-tick harvested energy over a finite horizon."""

    if horizon_ms is None or horizon_ms <= 0:
        raise InputValidationError("horizon_ms must be explicitly positive")
    if len(harvest_trace) < horizon_ms:
        raise InputValidationError(
            "harvest trace has {} ticks but horizon_ms is {}".format(
                len(harvest_trace), horizon_ms
            )
        )
    cleaned = []
    for index, raw_value in enumerate(harvest_trace[:horizon_ms]):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise InputValidationError(
                "harvest trace value {} is not numeric".format(index)
            )
        if not math.isfinite(value) or value < 0:
            raise InputValidationError(
                "harvest trace value {} must be finite and non-negative".format(
                    index
                )
            )
        cleaned.append(value)
    return EnergyServiceCurve(cleaned, horizon_ms)


def beta_inverse(
    beta_values: Sequence[float], energy: Union[int, float, Fraction]
) -> Optional[int]:
    """Return min integer delta with beta(delta) >= energy, or None."""

    exact_energy = _exact_fraction(energy)
    if exact_energy <= 0:
        return 0
    if isinstance(beta_values, EnergyServiceCurve):
        return beta_values.inverse(energy)
    for delta, value in enumerate(beta_values):
        if _exact_fraction(value) >= exact_energy:
            return delta
    return None


def _energy_fraction(task: RTATask) -> Fraction:
    return Fraction(str(task.energy_per_tick))


_DPState = Tuple[int, int, int, Fraction]


class _DPStateLimitExceeded(Exception):
    pass


def _state_dominates(left: _DPState, right: _DPState) -> bool:
    """Return true if left is no better for capacity and no smaller in U/E."""

    left_a, left_b, left_u, left_e = left
    right_a, right_b, right_u, right_e = right
    return (
        left_a <= right_a
        and left_b <= right_b
        and left_u >= right_u
        and left_e >= right_e
        and (
            left_a < right_a
            or left_b < right_b
            or left_u > right_u
            or left_e > right_e
        )
    )


def _prune_pareto_states(
    states: Iterable[_DPState],
    state_limit: int = ENERGY_DP_STATE_LIMIT,
) -> List[_DPState]:
    """Keep the Pareto frontier for capacity use and the maximized U/E pair."""

    frontier: List[_DPState] = []
    for state in states:
        if any(_state_dominates(existing, state) for existing in frontier):
            continue
        frontier = [
            existing
            for existing in frontier
            if not _state_dominates(state, existing)
        ]
        frontier.append(state)
        if len(frontier) > state_limit:
            raise _DPStateLimitExceeded(
                "energy DP state limit {} exceeded".format(state_limit)
            )
    return frontier


def _high_priority_options(
    task: RTATask,
    w: int,
    a_window: int,
    z: int,
    processor_workload: int,
    state_limit: int,
) -> List[_DPState]:
    workload = workload_bound(task, w)
    energy = _energy_fraction(task)
    _ = (a_window, z, processor_workload, state_limit)
    # For fixed a_i and b_i, taking all remaining W_i^D(w) as u_i is the
    # worst-case relaxation used by the RTA upper bound: both U and E are
    # monotone nondecreasing in u_i. Because a_i and b_i only consume capacity,
    # (a_i,b_i,u_i)=(0,0,W_i^D) dominates every other high-priority option for
    # this maximization. This is not intended to reconstruct one exact
    # simulator trace.
    return [(0, 0, workload, workload * energy)]


def _low_priority_options(
    task: RTATask,
    w: int,
    z: int,
    state_limit: int,
) -> List[_DPState]:
    workload = workload_bound(task, w)
    energy = _energy_fraction(task)
    options = [
        (0, c_value, 0, c_value * energy)
        for c_value in range(min(workload, z) + 1)
    ]
    return _prune_pareto_states(options, state_limit)


def _deadline_energy_states_for_z(
    target: RTATask,
    taskset: Sequence[RTATask],
    w: int,
    x: int,
    z: int,
    M: int,
    processor_workloads: Mapping[str, int],
) -> List[Tuple[int, Fraction]]:
    a_capacity = M * (x - z)
    b_capacity = (M - 1) * z
    target_energy = z * _energy_fraction(target)

    states: List[_DPState] = [(0, 0, 0, Fraction(0))]
    option_sets: List[List[_DPState]] = []
    for task in hp(taskset, target):
        option_sets.append(
            _high_priority_options(
                task,
                w,
                x - z,
                z,
                processor_workloads[task.name],
                ENERGY_DP_STATE_LIMIT,
            )
        )
    for task in _lp(taskset, target):
        option_sets.append(
            _low_priority_options(task, w, z, ENERGY_DP_STATE_LIMIT)
        )

    for options in option_sets:
        next_states: List[_DPState] = []
        for a_used, b_used, u_total, energy_total in states:
            for a_value, b_value, u_value, energy_value in options:
                new_a = a_used + a_value
                new_b = b_used + b_value
                if new_a > a_capacity or new_b > b_capacity:
                    continue
                next_states.append(
                    (
                        new_a,
                        new_b,
                        u_total + u_value,
                        energy_total + energy_value,
                    )
                )
        states = _prune_pareto_states(next_states, ENERGY_DP_STATE_LIMIT)

    return [
        (u_total, energy_total + target_energy)
        for _a_used, _b_used, u_total, energy_total in states
    ]


def _prefix_energy_upper_bound_exact(
    k: Target,
    w: int,
    x: int,
    tasks: Optional[Sequence[RTATask]] = None,
    M: Optional[int] = None,
) -> Fraction:
    taskset = _taskset_for(k, tasks)
    target = _resolve_target(taskset, k)
    processors = M if M is not None else target._num_cores
    if processors is None or processors <= 0:
        raise ValueError("M is required for prefix energy analysis")
    delay = processor_delay(target, w, processors, taskset)
    reference_length = target.wcet + delay
    if x < 0 or x > reference_length:
        raise ValueError(
            "x={} is outside reference prefix [0, {}]".format(
                x, reference_length
            )
        )
    z_min = max(0, x - delay)
    z_max = min(target.wcet, x)
    processor_workloads = _processor_workloads(target, w, taskset)
    best = Fraction(0)
    for z in range(z_min, z_max + 1):
        for _u_total, energy in _deadline_energy_states_for_z(
            target,
            taskset,
            w,
            x,
            z,
            processors,
            processor_workloads,
        ):
            if energy > best:
                best = energy
    return best


def prefix_energy_upper_bound(
    k: Target,
    w: int,
    x: int,
    tasks: Optional[Sequence[RTATask]] = None,
    M: Optional[int] = None,
) -> float:
    """Return the largest deadline-parameterized E_k^D demand for x."""

    return float(_prefix_energy_upper_bound_exact(k, w, x, tasks, M))


def _energy_blocking_bound_result(
    k: Target,
    w: int,
    beta: Sequence[float],
    E0: float = 0,
    tasks: Optional[Sequence[RTATask]] = None,
    M: Optional[int] = None,
) -> Optional[int]:
    """Compute B_k^E(w); return None if the finite service is insufficient."""

    taskset = _taskset_for(k, tasks)
    target = _resolve_target(taskset, k)
    processors = M if M is not None else target._num_cores
    if processors is None or processors <= 0:
        raise ValueError("M is required for energy blocking analysis")
    delay = processor_delay(target, w, processors, taskset)
    reference_length = target.wcet + delay
    if reference_length <= 0:
        return _EnergyBlockingResult(0)

    blocking = 0
    exact_e0 = _exact_fraction(E0)
    processor_workloads = _processor_workloads(target, w, taskset)
    for x in range(1, reference_length + 1):
        z_min = max(0, x - delay)
        z_max = min(target.wcet, x)
        for z in range(z_min, z_max + 1):
            try:
                states = _deadline_energy_states_for_z(
                    target,
                    taskset,
                    w,
                    x,
                    z,
                    processors,
                    processor_workloads,
                )
            except _DPStateLimitExceeded:
                return _EnergyBlockingResult(
                    None, "energy DP state limit exceeded"
                )
            for u_total, energy in states:
                demand = max(energy - exact_e0, Fraction(0))
                delta = beta_inverse(beta, demand)
                if delta is None:
                    return _EnergyBlockingResult(
                        None, "finite energy service is insufficient"
                    )
                blocking = max(blocking, max(u_total, max(delta - x, 0)))
    return _EnergyBlockingResult(blocking)


def energy_blocking_bound(
    k: Target,
    w: int,
    beta: Sequence[float],
    E0: float = 0,
    tasks: Optional[Sequence[RTATask]] = None,
    M: Optional[int] = None,
) -> Optional[int]:
    """Compute B_k^{E,D}(w); return None when finite service is insufficient."""

    return _energy_blocking_bound_result(k, w, beta, E0, tasks, M).blocking


def response_time_bound(
    k: Target,
    tasks: Optional[Sequence[RTATask]] = None,
    M: Optional[int] = None,
    beta: Optional[Sequence[float]] = None,
    E0: float = 0,
    max_iterations: int = 1000,
    assume_no_overflow: bool = False,
) -> TaskAnalysisResult:
    """Iterate the sufficient ASAP-BLOCK response-time upper bound."""

    taskset = _taskset_for(k, tasks)
    target = _resolve_target(taskset, k)
    processors = M if M is not None else target._num_cores
    if processors is None or processors <= 0:
        raise ValueError("M is required for response-time analysis")
    if beta is None:
        raise ValueError("beta is required for response-time analysis")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    harvesting_horizon = len(beta) - 1
    if harvesting_horizon < 0:
        raise ValueError("beta must include at least beta(0)")
    if target.wcet > harvesting_horizon:
        return TaskAnalysisResult(
            target.name,
            target.period,
            target.wcet,
            target.deadline,
            target.workload,
            target.energy_per_tick,
            target.wcet,
            False,
            "response bound exceeds harvesting horizon",
            0,
        )

    current = target.wcet
    for iteration in range(1, max_iterations + 1):
        cpu_delay = processor_delay(target, current, processors, taskset)
        energy_result = _energy_blocking_bound_result(
            target,
            current,
            beta,
            E0=E0,
            tasks=taskset,
            M=processors,
        )
        energy_delay = energy_result.blocking
        if energy_delay is None:
            return TaskAnalysisResult(
                target.name,
                target.period,
                target.wcet,
                target.deadline,
                target.workload,
                target.energy_per_tick,
                None,
                False,
                energy_result.failure_reason
                or "energy service is insufficient within the configured horizon",
                iteration,
            )

        next_value = target.wcet + cpu_delay + energy_delay
        if next_value > harvesting_horizon:
            return TaskAnalysisResult(
                target.name,
                target.period,
                target.wcet,
                target.deadline,
                target.workload,
                target.energy_per_tick,
                next_value,
                False,
                "response bound exceeds harvesting horizon",
                iteration,
            )
        if next_value > target.deadline:
            return TaskAnalysisResult(
                target.name,
                target.period,
                target.wcet,
                target.deadline,
                target.workload,
                target.energy_per_tick,
                next_value,
                False,
                "response-time bound exceeds the task deadline",
                iteration,
            )
        if next_value == current:
            if not assume_no_overflow:
                return TaskAnalysisResult(
                    target.name,
                    target.period,
                    target.wcet,
                    target.deadline,
                    target.workload,
                    target.energy_per_tick,
                    next_value,
                    False,
                    "no-overflow assumption was not acknowledged",
                    iteration,
                )
            return TaskAnalysisResult(
                target.name,
                target.period,
                target.wcet,
                target.deadline,
                target.workload,
                target.energy_per_tick,
                next_value,
                True,
                None,
                iteration,
            )
        if next_value < current:
            return TaskAnalysisResult(
                target.name,
                target.period,
                target.wcet,
                target.deadline,
                target.workload,
                target.energy_per_tick,
                next_value,
                False,
                "fixed-point iteration became non-monotonic",
                iteration,
            )
        current = next_value

    return TaskAnalysisResult(
        target.name,
        target.period,
        target.wcet,
        target.deadline,
        target.workload,
        target.energy_per_tick,
        current,
        False,
        "fixed-point iteration limit {} exceeded".format(max_iterations),
        max_iterations,
    )


def _resolve_solar_path(config: RTASystemConfig) -> str:
    if os.path.isabs(config.solar_data_file):
        return config.solar_data_file
    relative_to_config = os.path.join(
        os.path.dirname(config.source_path), config.solar_data_file
    )
    if os.path.exists(relative_to_config):
        return relative_to_config
    return os.path.abspath(config.solar_data_file)


def _load_irradiance_values(path: str) -> List[float]:
    values: List[float] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if not row:
                    continue
                try:
                    value = float(row[0])
                except ValueError:
                    continue
                # The repository trace uses negative sentinels for missing data.
                values.append(max(value, 0.0))
    except OSError as exc:
        raise InputValidationError(
            "cannot read solar data file {}: {}".format(path, exc)
        )
    if not values:
        raise InputValidationError(
            "solar data file {} contains no numeric irradiance values".format(path)
        )
    return values


def _time_factor(absolute_time_ms: int) -> float:
    hour = (absolute_time_ms % 86400000) / 3600000.0
    if hour < 6.0:
        return 0.0
    if hour < 11.0:
        return (hour - 6.0) / 5.0
    if hour < 13.0:
        return 1.0
    if hour < 18.0:
        return (18.0 - hour) / 5.0
    return 0.0


def _harvest_trace_from_config(
    config: RTASystemConfig, horizon_ms: int
) -> List[float]:
    start_offset = (
        (config.day_of_year - 1) * 86400000 + config.time_of_day_ms
    )
    if config.use_real_solar_data:
        irradiance = _load_irradiance_values(_resolve_solar_path(config))
        trace = []
        for tick in range(1, horizon_ms + 1):
            minute = (start_offset + tick) // 60000
            value = irradiance[minute] if minute < len(irradiance) else 0.0
            trace.append(
                value
                * config.pv_efficiency
                * config.pv_area_m2
                * TICK_SECONDS
            )
        return trace

    # This mirrors ASAPBlockScheduler::collectSolarEnergy for one-ms ticks.
    return [
        config.base_harvesting_rate
        * _time_factor(start_offset + tick)
        * TICK_SECONDS
        for tick in range(1, horizon_ms + 1)
    ]


def _check_single_tick_capacity(
    tasks: Sequence[RTATask], config: RTASystemConfig
) -> None:
    maximum_tick_demand = sum(
        sorted(
            (task.energy_per_tick for task in tasks),
            reverse=True,
        )[: config.num_cores]
    )
    if config.max_energy < maximum_tick_demand:
        raise InputValidationError(
            "max_energy={} J violates the single-tick capacity assumption; "
            "at least {} J is required".format(
                config.max_energy, maximum_tick_demand
            )
        )


def analyze_taskset(
    system_yml: str,
    tasks_yml: str,
    horizon_ms: Optional[int],
    assume_no_overflow: bool = False,
    harvest_trace: Optional[Sequence[float]] = None,
    max_iterations: int = 1000,
) -> TasksetAnalysis:
    """Analyze every task and return a serializable report."""

    if horizon_ms is None:
        raise InputValidationError("--horizon-ms is required")
    if horizon_ms <= 0:
        raise InputValidationError("--horizon-ms must be positive")

    config = load_system_config(system_yml)
    tasks = load_tasks(tasks_yml)
    taskset = tuple(tasks)
    for task in tasks:
        task.energy_per_tick = config.task_energy_per_tick(task.workload)
        task._taskset = taskset
        task._num_cores = config.num_cores

    _check_single_tick_capacity(tasks, config)
    trace = (
        list(harvest_trace)
        if harvest_trace is not None
        else _harvest_trace_from_config(config, horizon_ms)
    )
    beta = build_energy_service_curve(trace, horizon_ms)

    results = [
        response_time_bound(
            task,
            tasks=tasks,
            M=config.num_cores,
            beta=beta,
            E0=0,
            max_iterations=max_iterations,
            assume_no_overflow=assume_no_overflow,
        )
        for task in rm_order(tasks)
    ]
    return TasksetAnalysis(
        system_file=os.path.abspath(system_yml),
        tasks_file=os.path.abspath(tasks_yml),
        horizon_ms=horizon_ms,
        assume_no_overflow=assume_no_overflow,
        e0=0.0,
        tasks=results,
    )


def _format_report(report: TasksetAnalysis) -> str:
    lines = [
        "ASAP-BLOCK offline response-time upper-bound analysis",
        "CONDITIONAL ANALYSIS ONLY: no absolute schedulability claim is made.",
        "horizon_ms={}  E0=0  assume_no_overflow={}".format(
            report.horizon_ms, str(report.assume_no_overflow).lower()
        ),
        "Assumptions:",
    ]
    lines.extend("  - {}".format(assumption) for assumption in report.assumptions)
    lines.append("")
    for result in report.tasks:
        bound = (
            str(result.response_time_bound)
            if result.response_time_bound is not None
            else "unavailable"
        )
        lines.append(
            "{name}: T={period} C={wcet} D={deadline} workload={workload} "
            "energy/tick={energy:.12g}J R_UB={bound} "
            "proven_under_assumptions={proven}".format(
                name=result.task_name,
                period=result.period,
                wcet=result.wcet,
                deadline=result.deadline,
                workload=result.workload,
                energy=result.energy_per_tick,
                bound=bound,
                proven=str(result.proven).lower(),
            )
        )
        if result.failure_reason:
            lines.append("  reason: {}".format(result.failure_reason))
    lines.append("")
    lines.append(
        "taskset proven_under_assumptions={}".format(
            str(report.proven).lower()
        )
    )
    return "\n".join(lines)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline sufficient RTA checker for ASAP-BLOCK"
    )
    parser.add_argument("--system", required=True, help="system YAML file")
    parser.add_argument("--tasks", required=True, help="taskset YAML file")
    parser.add_argument(
        "--horizon-ms",
        required=True,
        type=int,
        help="finite harvesting-analysis horizon in milliseconds",
    )
    parser.add_argument(
        "--assume-no-overflow",
        action="store_true",
        help="acknowledge the theorem's no-battery-overflow assumption",
    )
    parser.add_argument(
        "--json", action="store_true", help="write the report as JSON"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    try:
        report = analyze_taskset(
            args.system,
            args.tasks,
            args.horizon_ms,
            assume_no_overflow=args.assume_no_overflow,
        )
    except RTAError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print("error: {}".format(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(_format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
