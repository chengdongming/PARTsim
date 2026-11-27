"""Native simulator system/task configuration and harvesting input model."""

from __future__ import annotations


from bisect import bisect_left


import csv


import math


import os


import re


import warnings


from dataclasses import asdict, dataclass, field


from fractions import Fraction


from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union


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


class RTAError(Exception):
    """Base error for the offline analyzer."""


class InputValidationError(RTAError):
    """Raised when an input is outside the supported first-version model."""


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


def materialize_runtime_start_offset_ms(
    day_of_year: int, time_of_day_ms: int,
) -> int:
    """Mirror EnergyConfig's production whole-minute start-time phase.

    ``time_of_day_ms`` is floored to its containing minute before the
    simulator starts.  Subsequent scheduler ticks advance from that
    materialized boundary; the discarded millisecond remainder never
    reappears.
    """

    return ((day_of_year - 1) * 1440 + time_of_day_ms // 60000) * 60000


_SYNTHETIC_PERIOD_MS = 60000


def _synthetic_normalized_linear_integral(
    start_ms: int, end_ms: int, intercept: float, slope: float,
) -> float:
    if end_ms <= start_ms:
        return 0.0
    start_factor = intercept + slope * float(start_ms)
    end_factor = intercept + slope * float(end_ms)
    return float(end_ms - start_ms) * (start_factor + end_factor) * 0.5


def _synthetic_normalized_primitive(time_ms: int) -> float:
    if time_ms <= 0:
        return 0.0
    if time_ms <= 10000:
        return _synthetic_normalized_linear_integral(
            0, time_ms, 1.0, -1.0 / 50000.0,
        )

    area = _synthetic_normalized_linear_integral(
        0, 10000, 1.0, -1.0 / 50000.0,
    )
    if time_ms <= 20000:
        return area + float(time_ms - 10000) * (4.0 / 5.0)

    area += float(10000) * (4.0 / 5.0)
    if time_ms <= 40000:
        return area + _synthetic_normalized_linear_integral(
            20000, time_ms, 0.4, 1.0 / 50000.0,
        )

    area += _synthetic_normalized_linear_integral(
        20000, 40000, 0.4, 1.0 / 50000.0,
    )
    if time_ms <= 50000:
        return area + float(time_ms - 40000) * (6.0 / 5.0)

    area += float(10000) * (6.0 / 5.0)
    return area + _synthetic_normalized_linear_integral(
        50000, time_ms, 2.2, -1.0 / 50000.0,
    )


def _synthetic_normalized_cumulative(time_ms: int) -> float:
    if time_ms < 0:
        raise InputValidationError(
            "synthetic harvesting time must be non-negative"
        )
    periods, remainder = divmod(time_ms, _SYNTHETIC_PERIOD_MS)
    return (
        float(periods) * float(_SYNTHETIC_PERIOD_MS)
        + _synthetic_normalized_primitive(remainder)
    )


def _harvest_trace_from_config(
    config: RTASystemConfig, horizon_ms: int
) -> List[float]:
    start_offset = materialize_runtime_start_offset_ms(
        config.day_of_year, config.time_of_day_ms,
    )
    if config.use_real_solar_data:
        irradiance = _load_irradiance_values(_resolve_solar_path(config))
        trace = []
        for tick in range(1, horizon_ms + 1):
            minute = (start_offset + tick) // 60000
            value = irradiance[minute] if minute < len(irradiance) else 0.0
            trace.append(
                value
                * config.pv_area_m2
                * config.pv_efficiency
                * (1.0 * TICK_SECONDS)
            )
        return trace

    # Mirror LegacySolarSource's binary64 analytic interval integration for
    # the synthetic one-ms tick. The synthetic clock starts at the beginning
    # of this simulation; calendar and photovoltaic parameters are
    # intentionally not consulted on this path.
    trace = []
    for tick in range(horizon_ms):
        normalized_area_ms = (
            _synthetic_normalized_cumulative(tick + 1)
            - _synthetic_normalized_cumulative(tick)
        )
        trace.append(
            config.base_harvesting_rate * normalized_area_ms * TICK_SECONDS
        )
    return trace
