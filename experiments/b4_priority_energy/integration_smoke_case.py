#!/usr/bin/env python3
"""Materialize and validate one real B4-PE integration-smoke case."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import yaml


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import acceptance_ratio_test as acceptance
import execution_common as execution
import inspect_execution
import integration_smoke_common as smoke
import manifest_common as manifest


TASK_GENERATOR_PATH = REPO_ROOT / "global_task_generator.py"
SYSTEM_TEMPLATE_PATH = REPO_ROOT / "v9_3_b4_priority_energy_system_template.yml"
SIMULATOR_DEFAULT = Path(
    "/home/devcontainers/builds/partsim-b4-release/rtsim/rtsim"
)

PROCESSORS = 4
TASK_COUNT = 10
TARGET_NORMALIZED_UTILIZATION = Fraction(3, 10)
TARGET_TOTAL_UTILIZATION = TARGET_NORMALIZED_UTILIZATION * PROCESSORS
UTILIZATION_TOLERANCE = Fraction(1, 100)
MIN_TASK_UTILIZATION = Fraction(1, 100)
MAX_TASK_UTILIZATION = Fraction(45, 100)
PERIOD_MIN_MS = 40
PERIOD_MAX_MS = 200
GENERATOR_SEED = 424242
FROZEN_HORIZON_MS = 30000
SMOKE_HORIZON_MS = 1000
RHO_E = Fraction(2, 1)
LAMBDA_E = Fraction(85, 100)
SOURCE_INTEGRAL_SECONDS = Fraction(22, 1)

RAW_TASKSET_RELPATH = "integration-smoke/artifacts/taskset.raw.yml"
TASKSET_RELPATH = "integration-smoke/artifacts/taskset.yml"
SYSTEM_RELPATH = "integration-smoke/artifacts/system.yml"
SOURCE_RELPATH = "integration-smoke/artifacts/source.json"
RESULT_PREFIX = "integration-smoke/results"

SMOKE_ALGORITHMS = tuple(
    manifest.IDENTITY.RESOLUTION["phase_algorithms"]["formal_main"]
)
ALGORITHM_CLI_MAPPING = dict(manifest.PROTOCOL["algorithm_cli_mapping"])
DEFAULT_ALGORITHM = "ASAP-BLOCK"

LEGACY_SOURCE_FIELDS = {
    "base_harvesting_rate",
    "day_of_year",
    "time_of_day_ms",
    "start_offset_minutes",
    "harvesting_scale",
    "use_real_solar_data",
    "solar_data_file",
    "pv_efficiency",
    "pv_area_m2",
    "harvesting_sources",
}

SYSTEM_PRIORITY_ENERGY_PLACEHOLDER = """priority_energy:
  enabled: false
  profile_id: b4_pe_three_stage_v1
  alpha_w: 0.0
  horizon_ms: 30000
  tick_ms: 1
"""

SYSTEM_ENERGY_BOUNDS_PLACEHOLDER = """  initial_energy: 100.0
  max_energy: 1000.0
"""

SYSTEM_LEGACY_SOURCE_PLACEHOLDER = """  day_of_year: 187
  time_of_day_ms: 21900000
  base_harvesting_rate: 0.054
  harvesting_scale: 1.0

  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: 0.18
  pv_area_m2: 1.0

"""

SYSTEM_INLINE_VOLTS_LINE = (
    "    volts: [0.92, 0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06, "
    "1.08, 1.10, 1.12, 1.14]"
)
SYSTEM_SCHEDULER_PLACEHOLDER = "      scheduler: gpfp_asap_block\n"


class RealSmokeCaseError(RuntimeError):
    """The real integration-smoke artifact/result contract is not satisfied."""


def _require(condition, message):
    if not condition:
        raise RealSmokeCaseError(message)


def scheduler_cli_name(algorithm):
    _require(
        algorithm in SMOKE_ALGORITHMS
        and algorithm in ALGORITHM_CLI_MAPPING,
        f"unknown formal scheduler algorithm: {algorithm}",
    )
    return ALGORITHM_CLI_MAPPING[algorithm]


def _normalised_utilization(value):
    try:
        exact = value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise RealSmokeCaseError("normalized utilization is not exact") from exc
    _require(
        0 < exact <= 1,
        "normalized utilization must be in (0,1]",
    )
    return exact


def _fraction_cli_text(value):
    return format(float(value), ".17g")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_nonempty(path, label):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise RealSmokeCaseError(f"{label} is missing: {path}") from exc
    _require(
        stat.S_ISREG(info.st_mode) and info.st_size > 0,
        f"{label} must be a regular non-empty file: {path}",
    )
    return path


def compact_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _artifact_path(output_root, relative):
    return Path(output_root).joinpath(*Path(relative).parts)


def generator_argv(
    raw_taskset_path,
    python_executable=None,
    seed=GENERATOR_SEED,
    normalized_utilization=TARGET_NORMALIZED_UTILIZATION,
):
    """Return the exact public task-generator CLI for this smoke case."""
    _require(type(seed) is int and seed >= 0, "generator seed must be a nonnegative integer")
    normalized = _normalised_utilization(normalized_utilization)
    executable = Path(python_executable or sys.executable)
    return [
        str(executable),
        str(TASK_GENERATOR_PATH),
        "--num-tasks",
        str(TASK_COUNT),
        "--utilization",
        _fraction_cli_text(normalized * PROCESSORS),
        "--min-period",
        str(PERIOD_MIN_MS),
        "--max-period",
        str(PERIOD_MAX_MS),
        "--cpus",
        str(PROCESSORS),
        "--constrained-deadlines",
        "--no-arrival-offset",
        "--system-config",
        str(SYSTEM_TEMPLATE_PATH),
        "--seed",
        str(seed),
        "--min-task-util",
        "0.01",
        "--max-task-util",
        "0.45",
        "--wcet-rounding",
        "compensated",
        "--actual-utilization-tolerance-total",
        "0.01",
        "--output",
        str(Path(raw_taskset_path).resolve()),
        "--task-workload-candidate",
        "hash",
    ]


def run_generator(
    raw_taskset_path,
    seed=GENERATOR_SEED,
    normalized_utilization=TARGET_NORMALIZED_UTILIZATION,
):
    command = generator_argv(
        raw_taskset_path,
        seed=seed,
        normalized_utilization=normalized_utilization,
    )
    log_directory = Path(raw_taskset_path).parent / "generator-logs"
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            **os.environ,
            "PARTSIM_LOG_DIR": str(log_directory),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    _require(
        completed.returncode == 0,
        "public task generator exited {}: {}".format(
            completed.returncode, (completed.stderr or "")[-2000:]
        ),
    )
    _require(
        Path(raw_taskset_path).is_file()
        and Path(raw_taskset_path).stat().st_size > 0,
        "public task generator produced no taskset",
    )
    return command


def _load_yaml_mapping(path, label):
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RealSmokeCaseError(f"{label} is not readable YAML") from exc
    _require(isinstance(document, dict), f"{label} root must be a mapping")
    return document


def _params_mapping(value):
    result = {}
    for token in str(value).split(","):
        if "=" not in token:
            continue
        key, item = token.split("=", 1)
        key = key.strip()
        _require(key not in result, f"duplicate task parameter: {key}")
        result[key] = item.strip().strip('"')
    return result


def validate_generated_taskset(
    path,
    normalized_utilization=TARGET_NORMALIZED_UTILIZATION,
):
    """Validate the real generator output before bridge materialization."""
    target_total_utilization = (
        _normalised_utilization(normalized_utilization) * PROCESSORS
    )
    document = _load_yaml_mapping(path, "generated taskset")
    tasks = document.get("taskset")
    _require(
        isinstance(tasks, list) and len(tasks) == TASK_COUNT,
        "generated taskset must contain exactly ten tasks",
    )
    actual = Fraction(0, 1)
    names = set()
    for index, task in enumerate(tasks):
        _require(isinstance(task, dict), f"task {index} must be a mapping")
        name = task.get("name")
        _require(
            isinstance(name, str)
            and name.startswith("task_")
            and name not in names,
            f"task {index} has invalid identity",
        )
        names.add(name)
        c_value = task.get("runtime")
        d_value = task.get("deadline")
        t_value = task.get("iat")
        _require(
            all(type(value) is int for value in (c_value, d_value, t_value))
            and 1 <= c_value <= d_value <= t_value,
            f"task {name} violates constrained-deadline bounds",
        )
        _require(
            PERIOD_MIN_MS <= t_value <= PERIOD_MAX_MS,
            f"task {name} period is outside the frozen range",
        )
        utilization = Fraction(c_value, t_value)
        _require(
            MIN_TASK_UTILIZATION <= utilization <= MAX_TASK_UTILIZATION,
            f"task {name} utilization is outside the frozen bounds",
        )
        params = _params_mapping(task.get("params", ""))
        _require(params.get("period") == str(t_value), f"task {name} period parameter mismatch")
        _require(params.get("wcet") == str(c_value), f"task {name} WCET parameter mismatch")
        _require(params.get("arrival_offset") == "0", f"task {name} is not synchronous")
        _require(params.get("workload") == "hash", f"task {name} workload is not hash")
        actual += utilization
    _require(
        abs(actual - target_total_utilization) <= UTILIZATION_TOLERANCE,
        "actual total utilization is outside the frozen tolerance",
    )
    return document, actual


def _template_power_contract(template):
    islands = template.get("cpu_islands")
    _require(
        isinstance(islands, list) and len(islands) == 1,
        "system template must define one CPU island",
    )
    island = islands[0]
    _require(island.get("numcpus") == PROCESSORS, "system template processor mismatch")
    _require(
        island.get("kernel", {}).get("scheduler") == "gpfp_asap_block",
        "system template scheduler is not ASAP-BLOCK",
    )
    base_frequency = int(island.get("base_freq"))
    energy = template.get("energy_management", {})
    model = energy.get("scheduler_energy_model", {})
    base_power = Fraction(str(model.get("base_power")))
    coefficient = Fraction(str(model.get("workload_coefficients", {}).get("hash")))
    ratios = model.get("frequency_power_ratios", {})
    ratio = ratios.get(base_frequency, ratios.get(str(base_frequency)))
    _require(ratio is not None, "system template lacks the base-frequency power ratio")
    power_w = base_power * coefficient * Fraction(str(ratio))
    _require(power_w > 0, "system template hash power must be positive")
    return power_w


def _release_count(period, horizon=FROZEN_HORIZON_MS):
    return (horizon - 1) // period + 1


def materialize_taskset(
    raw_path,
    destination,
    normalized_utilization=TARGET_NORMALIZED_UTILIZATION,
):
    """Copy the public generator taskset byte-for-byte into the smoke artifact."""
    raw_path = _require_regular_nonempty(raw_path, "raw taskset")
    raw, actual = validate_generated_taskset(
        raw_path,
        normalized_utilization=normalized_utilization,
    )
    tasks = raw["taskset"]
    ranked = sorted(
        enumerate(tasks),
        key=lambda item: (item[1]["iat"], item[0], item[1]["name"]),
    )
    template = _load_yaml_mapping(SYSTEM_TEMPLATE_PATH, "system template")
    q0_j_per_ms = _template_power_contract(template) / 1000
    high_base = sum(
        (
            _release_count(task["iat"]) * task["runtime"] * q0_j_per_ms
            for _index, task in ranked[:PROCESSORS]
        ),
        Fraction(0, 1),
    )
    low_base = sum(
        (
            _release_count(task["iat"]) * task["runtime"] * q0_j_per_ms
            for _index, task in ranked[PROCESSORS:]
        ),
        Fraction(0, 1),
    )
    _require(high_base > 0 and low_base > 0, "taskset energy groups must be non-empty")
    low_factor = (high_base + low_base) / (RHO_E * high_base + low_base)
    high_factor = RHO_E * low_factor
    for task in tasks:
        params = str(task.get("params", ""))
        _require(
            "task_energy_factor" not in _params_mapping(params),
            "generator unexpectedly emitted task_energy_factor",
        )

    burst = sum(
        (
            task["runtime"] * q0_j_per_ms * high_factor
            for _index, task in ranked[:PROCESSORS]
        ),
        Fraction(0, 1),
    )
    e0 = burst
    emax = 2 * burst
    demand = high_base + low_base
    alpha = (LAMBDA_E * demand - e0) / SOURCE_INTEGRAL_SECONDS
    _require(alpha >= 0, "frozen alpha is negative for generated taskset")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(raw_path, destination)
    destination = _require_regular_nonempty(
        destination, "materialized taskset"
    )
    raw_sha = file_sha256(raw_path)
    materialized_sha = file_sha256(destination)
    _require(
        raw_sha == materialized_sha,
        "raw/materialized taskset SHA mismatch",
    )
    raw_semantic_hash = formal_semantic_hash(raw_path)
    materialized_semantic_hash = formal_semantic_hash(destination)
    _require(
        raw_semantic_hash == materialized_semantic_hash,
        "raw/materialized taskset semantic hash mismatch",
    )
    return {
        "actual_total_utilization": actual,
        "power_w": _template_power_contract(template),
        "q0_j_per_ms": q0_j_per_ms,
        "high_factor": high_factor,
        "low_factor": low_factor,
        "E0_j": e0,
        "Emax_j": emax,
        "nominal_demand_j": demand,
        "alpha_w": alpha,
        "raw_taskset_sha256": raw_sha,
        "materialized_taskset_sha256": materialized_sha,
        "raw_taskset_semantic_hash": raw_semantic_hash,
        "materialized_taskset_semantic_hash": materialized_semantic_hash,
    }


def formal_semantic_hash(taskset_path):
    """Use the repository's formal semantic-hash function directly."""
    return acceptance.taskset_semantic_hash(Path(taskset_path))


def _source_descriptor(energy):
    return {
        "schema": "b4-pe-i4b2a-source-descriptor-v1",
        "phase": "integration_smoke",
        "not_for_paper": True,
        "campaign_started": False,
        "profile_id": "b4_pe_three_stage_v1",
        "rho_E": "2",
        "lambda_E": "0.85",
        "E0_j": float(energy["E0_j"]),
        "Emax_j": float(energy["Emax_j"]),
        "source": {
            "kind": "scaled_piecewise",
            "scale_w": float(energy["alpha_w"]),
            "segments": [
                {"start_time_ms": 0, "end_time_ms": 5000, "multiplier": 1.0},
                {"start_time_ms": 5000, "end_time_ms": 15000, "multiplier": 0.2},
                {"start_time_ms": 15000, "end_time_ms": 30000, "multiplier": 1.0},
            ],
        },
    }


def _replace_system_placeholder(source, placeholder, replacement, label):
    _require(
        source.count(placeholder) == 1,
        f"system template {label} placeholder count is not one",
    )
    return source.replace(placeholder, replacement, 1)


def render_system_and_source(
    system_path,
    source_path,
    energy,
    algorithm=DEFAULT_ALGORITHM,
):
    """Render only declared smoke placeholders in the frozen template text."""
    scheduler_cli = scheduler_cli_name(algorithm)
    _require(
        file_sha256(SYSTEM_TEMPLATE_PATH)
        == manifest.PROTOCOL["system_template_sha256"],
        "system template SHA does not match the frozen manifest protocol",
    )
    template_text = SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
    _require(
        template_text.count("    numcpus: 4") == 1,
        "system template processor placeholder is not the frozen value",
    )
    _require(
        template_text.count(SYSTEM_SCHEDULER_PLACEHOLDER) == 1,
        "system template scheduler placeholder is not ASAP-BLOCK",
    )
    _require(
        template_text.count(SYSTEM_INLINE_VOLTS_LINE) == 1,
        "system template inline volts representation changed",
    )
    priority_energy = """priority_energy:
  enabled: true
  profile_id: b4_pe_three_stage_v1
  alpha_w: {}
  horizon_ms: 30000
  tick_ms: 1
""".format(repr(float(energy["alpha_w"])))
    energy_bounds = """  initial_energy: {}
  max_energy: {}
""".format(
        repr(float(energy["E0_j"])),
        repr(float(energy["Emax_j"])),
    )
    rendered = _replace_system_placeholder(
        template_text,
        SYSTEM_PRIORITY_ENERGY_PLACEHOLDER,
        priority_energy,
        "priority-energy",
    )
    rendered = _replace_system_placeholder(
        rendered,
        SYSTEM_ENERGY_BOUNDS_PLACEHOLDER,
        energy_bounds,
        "energy-bounds",
    )
    rendered = _replace_system_placeholder(
        rendered,
        SYSTEM_LEGACY_SOURCE_PLACEHOLDER,
        "",
        "legacy-source",
    )
    rendered = _replace_system_placeholder(
        rendered,
        SYSTEM_SCHEDULER_PLACEHOLDER,
        f"      scheduler: {scheduler_cli}\n",
        "scheduler",
    )
    _require(
        SYSTEM_PRIORITY_ENERGY_PLACEHOLDER not in rendered
        and SYSTEM_ENERGY_BOUNDS_PLACEHOLDER not in rendered
        and SYSTEM_LEGACY_SOURCE_PLACEHOLDER not in rendered,
        "system artifact contains an unexpanded smoke placeholder",
    )
    _require(
        rendered.count(SYSTEM_INLINE_VOLTS_LINE) == 1
        and "\n    volts:\n" not in rendered,
        "system artifact changed inline volts representation",
    )

    system_path = Path(system_path)
    system_path.parent.mkdir(parents=True, exist_ok=True)
    system_path.write_text(rendered, encoding="utf-8")
    _require_regular_nonempty(system_path, "materialized system")
    system = _load_yaml_mapping(system_path, "materialized system")
    priority = system.get("priority_energy", {})
    _require(
        priority.get("enabled") is True
        and priority.get("profile_id") == "b4_pe_three_stage_v1"
        and float(priority.get("alpha_w")) == float(energy["alpha_w"])
        and priority.get("horizon_ms") == FROZEN_HORIZON_MS
        and priority.get("tick_ms") == 1,
        "materialized priority-energy block mismatch",
    )
    island = system.get("cpu_islands", [{}])[0]
    _require(
        island.get("numcpus") == PROCESSORS
        and island.get("kernel", {}).get("scheduler") == scheduler_cli,
        "materialized processor/scheduler mismatch",
    )
    energy_management = system.get("energy_management", {})
    _require(
        float(energy_management.get("initial_energy")) == float(energy["E0_j"])
        and float(energy_management.get("max_energy")) == float(energy["Emax_j"]),
        "materialized energy bounds mismatch",
    )
    _require(
        not LEGACY_SOURCE_FIELDS.intersection(energy_management),
        "materialized system retained a legacy source field",
    )
    _require("harvesting" not in system, "explicit harvesting would create a second source")
    descriptor = _source_descriptor(energy)
    source_path = Path(source_path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(compact_json(descriptor) + "\n", encoding="utf-8")
    return system, descriptor


def build_record(
    output_root,
    simulator_path,
    generator_command,
    raw_sha,
    semantic_hash,
    algorithm=DEFAULT_ALGORITHM,
):
    output_root = Path(output_root).resolve()
    simulator_path = Path(simulator_path).resolve(strict=True)
    scheduler_cli_name(algorithm)
    case_id = (
        "smoke-b4-pe-i4b2b-"
        + semantic_hash[:16]
        + "-"
        + algorithm.lower()
    )
    result_relpath = f"{RESULT_PREFIX}/{case_id}.json"
    record = {
        "schema_version": "b4-pe-integration-smoke-v1",
        "record_type": "integration_smoke",
        "phase": "integration_smoke",
        "execution_scope": "single-real-case",
        "selected_case_count": 1,
        "campaign_started": False,
        "campaign_result_count": 0,
        "not_for_paper": True,
        "case_id": case_id,
        "algorithm": algorithm,
        "simulator_path": str(simulator_path),
        "output_root": str(output_root),
        "system_config_path": SYSTEM_RELPATH,
        "taskset_path": TASKSET_RELPATH,
        "source_artifact_path": SOURCE_RELPATH,
        "result_relpath": result_relpath,
        "timeout_seconds": 300,
        "retry_policy": {
            "initial_timeout_seconds": 300,
            "max_attempts": 2,
            "on_final_failure": "fail_closed",
            "retry_on": ["timeout"],
            "retry_timeout_seconds": 600,
        },
        "provenance": {
            "generator_path": str(TASK_GENERATOR_PATH),
            "generator_sha256": file_sha256(TASK_GENERATOR_PATH),
            "generator_argv": list(generator_command),
            "taskset_raw_sha256": raw_sha,
            "taskset_semantic_hash": semantic_hash,
            "system_config_sha256": file_sha256(
                _artifact_path(output_root, SYSTEM_RELPATH)
            ),
            "source_artifact_sha256": file_sha256(
                _artifact_path(output_root, SOURCE_RELPATH)
            ),
            "simulator_sha256": file_sha256(simulator_path),
        },
    }
    record["command_argv"] = [
        record["simulator_path"],
        SYSTEM_RELPATH,
        TASKSET_RELPATH,
        str(SMOKE_HORIZON_MS),
        "-t",
        result_relpath,
        "--run-id",
        case_id,
        "--taskset-semantic-hash",
        semantic_hash,
    ]
    return record


def write_record(record_path, record):
    record_path = Path(record_path).resolve()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(compact_json(record) + "\n", encoding="utf-8")
    return record_path


def preflight(record_path):
    """Programmatically close the one-case gate immediately before execution."""
    envelope = smoke.validate_integration_smoke_record(record_path)
    _require(len(envelope["records"]) == 1, "smoke gateway selected more than one case")
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    output_root = Path(record["output_root"])
    provenance = record["provenance"]
    raw_path = _artifact_path(output_root, RAW_TASKSET_RELPATH)
    taskset_path = _artifact_path(output_root, TASKSET_RELPATH)
    system_path = _artifact_path(output_root, SYSTEM_RELPATH)
    source_path = _artifact_path(output_root, SOURCE_RELPATH)
    simulator_path = Path(record["simulator_path"])
    _require_regular_nonempty(raw_path, "raw taskset")
    _require_regular_nonempty(taskset_path, "materialized taskset")
    for path in (system_path, source_path, simulator_path):
        _require(path.is_file() and path.stat().st_size > 0, f"missing input: {path}")
    raw_sha = file_sha256(raw_path)
    materialized_sha = file_sha256(taskset_path)
    _require(
        raw_sha == provenance["taskset_raw_sha256"],
        "raw taskset SHA mismatch",
    )
    _require(
        raw_sha == materialized_sha,
        "raw/materialized taskset SHA mismatch",
    )
    _require(
        Path(provenance["generator_path"]).resolve()
        == TASK_GENERATOR_PATH.resolve()
        and file_sha256(provenance["generator_path"])
        == provenance["generator_sha256"],
        "generator provenance mismatch",
    )
    _require(
        file_sha256(system_path) == provenance["system_config_sha256"],
        "system SHA mismatch",
    )
    _require(
        file_sha256(source_path) == provenance["source_artifact_sha256"],
        "source SHA mismatch",
    )
    _require(
        file_sha256(simulator_path) == provenance["simulator_sha256"],
        "simulator SHA mismatch",
    )
    raw_first = formal_semantic_hash(raw_path)
    raw_second = formal_semantic_hash(raw_path)
    materialized_first = formal_semantic_hash(taskset_path)
    materialized_second = formal_semantic_hash(taskset_path)
    _require(
        raw_first
        == raw_second
        == materialized_first
        == materialized_second
        == provenance["taskset_semantic_hash"],
        "semantic hash mismatch",
    )
    argv = record["command_argv"]
    _require(
        argv[-2:] == ["--taskset-semantic-hash", materialized_first],
        "semantic hash is not in the smoke command",
    )
    _require(
        argv
        == [
            record["simulator_path"],
            SYSTEM_RELPATH,
            TASKSET_RELPATH,
            str(SMOKE_HORIZON_MS),
            "-t",
            record["result_relpath"],
            "--run-id",
            record["case_id"],
            "--taskset-semantic-hash",
            materialized_first,
        ],
        "smoke command does not have the exact rtsim argv shape",
    )
    return {
        "selected_case_count": 1,
        "parallel_workers": 1,
        "output_root": str(output_root),
        "raw_taskset_sha256": raw_sha,
        "materialized_taskset_sha256": materialized_sha,
        "raw_materialized_sha_match": raw_sha == materialized_sha,
        "raw_taskset_semantic_hash": raw_first,
        "materialized_taskset_semantic_hash": materialized_first,
        "semantic_hash": materialized_first,
        "record_validated": True,
        "command_argv": argv,
        "execute_manifest_appends_semantic_arguments": False,
    }


def prepare_case(
    output_root,
    record_path,
    simulator_path=SIMULATOR_DEFAULT,
    algorithm=DEFAULT_ALGORITHM,
    seed=GENERATOR_SEED,
    normalized_utilization=TARGET_NORMALIZED_UTILIZATION,
):
    output_root = Path(output_root).resolve()
    normalized = _normalised_utilization(normalized_utilization)
    scheduler_cli_name(algorithm)
    _require(
        not (output_root == REPO_ROOT or REPO_ROOT in output_root.parents),
        "output root must be outside the repository",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    raw_path = _artifact_path(output_root, RAW_TASKSET_RELPATH)
    taskset_path = _artifact_path(output_root, TASKSET_RELPATH)
    system_path = _artifact_path(output_root, SYSTEM_RELPATH)
    source_path = _artifact_path(output_root, SOURCE_RELPATH)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    command = run_generator(
        raw_path,
        seed=seed,
        normalized_utilization=normalized,
    )
    raw_sha = file_sha256(raw_path)
    energy = materialize_taskset(
        raw_path,
        taskset_path,
        normalized_utilization=normalized,
    )
    _require(
        raw_sha
        == energy["raw_taskset_sha256"]
        == energy["materialized_taskset_sha256"],
        "materialization changed public generator taskset bytes",
    )
    semantic_hash = energy["materialized_taskset_semantic_hash"]
    _require(
        semantic_hash
        == energy["raw_taskset_semantic_hash"]
        == formal_semantic_hash(raw_path)
        == formal_semantic_hash(taskset_path),
        "raw/materialized formal semantic hash mismatch",
    )
    render_system_and_source(
        system_path,
        source_path,
        energy,
        algorithm=algorithm,
    )
    record = build_record(
        output_root,
        simulator_path,
        command,
        raw_sha,
        semantic_hash,
        algorithm=algorithm,
    )
    record_path = write_record(record_path, record)
    gate = preflight(record_path)
    return {
        "record_path": str(record_path),
        "output_root": str(output_root),
        "generator_path": str(TASK_GENERATOR_PATH),
        "generator_sha256": file_sha256(TASK_GENERATOR_PATH),
        "generator_argv": command,
        "seed": seed,
        "normalized_utilization": float(normalized),
        "algorithm": algorithm,
        "scheduler": scheduler_cli_name(algorithm),
        "taskset_raw_sha256": raw_sha,
        "taskset_sha256": file_sha256(taskset_path),
        "raw_materialized_sha_match": (
            raw_sha == file_sha256(taskset_path)
        ),
        "raw_taskset_semantic_hash": formal_semantic_hash(raw_path),
        "materialized_taskset_semantic_hash": semantic_hash,
        "taskset_semantic_hash": semantic_hash,
        "actual_total_utilization": float(energy["actual_total_utilization"]),
        "system_template_sha256": file_sha256(SYSTEM_TEMPLATE_PATH),
        "system_sha256": file_sha256(system_path),
        "source_sha256": file_sha256(source_path),
        "E0_j": float(energy["E0_j"]),
        "Emax_j": float(energy["Emax_j"]),
        "alpha_w": float(energy["alpha_w"]),
        "case_id": record["case_id"],
        "command_argv": record["command_argv"],
        "preflight": gate,
    }


def _walk_numeric_energy(value, path="trace"):
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if (
                "energy" in key.lower()
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
            ):
                _require(
                    math.isfinite(float(item)) and float(item) >= 0.0,
                    f"negative/non-finite energy field: {item_path}",
                )
            yield from _walk_numeric_energy(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_numeric_energy(item, f"{path}[{index}]")
    yield value


def validate_result_document(
    document,
    case_id,
    semantic_hash,
    emax_j,
    expected_scheduler="gpfp_asap_block",
):
    _require(isinstance(document, dict), "result is not a JSON object")
    _require(document.get("run_id") == case_id, "result run-id mismatch")
    _require(
        document.get("taskset_semantic_hash") == semantic_hash,
        "result semantic hash mismatch",
    )
    _require(
        document.get("configured_scheduler") == expected_scheduler,
        "result scheduler does not match the smoke record",
    )
    _require(
        float(document.get("expected_simulation_horizon_ms")) == SMOKE_HORIZON_MS
        and float(document.get("observed_simulation_end_ms")) == SMOKE_HORIZON_MS,
        "result horizon mismatch",
    )
    _require(document.get("simulation_completed") is True, "result is incomplete")
    _require(
        document.get("simulation_completion_reason") == "reached_horizon",
        "result completion reason mismatch",
    )
    events = document.get("events")
    _require(isinstance(events, list) and events, "result has no events")
    task_names = {
        event.get("task_name")
        for event in events
        if isinstance(event, dict)
        and event.get("event_type") == "arrival"
        and isinstance(event.get("task_name"), str)
    }
    _require(len(task_names) == TASK_COUNT, "result does not contain ten tasks")
    for event in events:
        if not isinstance(event, dict):
            continue
        for key in (
            "current_energy_mJ",
            "available_energy_mJ",
            "available_energy_before_decision_mJ",
            "residual_energy_after_continuation_reservation_mJ",
        ):
            if key in event:
                value = float(event[key])
                _require(
                    smoke.legacy_trace_battery_mj_is_valid(value, emax_j),
                    f"battery field outside [0,Emax]: {key}",
                )
        lowered = {key.lower(): key for key in event}
        offered = next((event[key] for low, key in lowered.items() if "offered" in low and "harvest" in low), None)
        actual = next((event[key] for low, key in lowered.items() if "actual" in low and "harvest" in low), None)
        clipped = next((event[key] for low, key in lowered.items() if "clipped" in low and "harvest" in low), None)
        if offered is not None and actual is not None and clipped is not None:
            offered_value = float(offered)
            relation = float(actual) + float(clipped)
            _require(
                math.isclose(offered_value, relation, rel_tol=1e-12, abs_tol=1e-12),
                "offered/actual/clipped harvest relation mismatch",
            )
    list(_walk_numeric_energy(document))
    encoded = compact_json(document)
    _require(
        re.search(r"(?i)(invalid trace extension|trace_target_exists_with_different_content|"
                  r"taskset semantic hash (?:missing|mismatch)|\bfatal\b|\bnan\b|"
                  r"\binf\b|incomplete trace)", encoded)
        is None,
        "result contains a forbidden failure marker",
    )
    return {
        "run_id": case_id,
        "scheduler": expected_scheduler,
        "task_count": len(task_names),
        "horizon_ms": SMOKE_HORIZON_MS,
        "battery_bounds_valid": True,
        "energy_fields_nonnegative": True,
        "harvest_relation_valid_when_present": True,
    }


def directory_content_digest(root):
    """Hash relative names, entry kinds, and regular-file bytes."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            kind = "symlink"
            payload = os.readlink(path).encode("utf-8")
        elif path.is_dir():
            kind = "directory"
            payload = b""
        elif path.is_file():
            kind = "file"
            payload = path.read_bytes()
        else:
            kind = "other"
            payload = b""
        digest.update(kind.encode("ascii") + b"\0" + relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _normalised_executed_argv(record, state, attempt):
    replacements = {
        record["simulator_path"]: state["simulator_executed_proc_fd_path"],
        record["system_config_path"]: state["system_executed_proc_fd_path"],
        record["taskset_path"]: state["taskset_executed_proc_fd_path"],
        record["result_relpath"]: (
            "/proc/self/fd/<attempt-directory-fd>/"
            + attempt["staging_trace_basename"]
        ),
    }
    return [replacements.get(item, item) for item in record["command_argv"]]


def validate_execution(record_path):
    """Validate result, state, publication, snapshots, provenance, and inspect."""
    envelope = smoke.validate_integration_smoke_record(record_path)
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    normalised_record = envelope["records"][0]
    root = Path(record["output_root"])
    state_path = root / ".b4pe" / "state" / f"{record['case_id']}.json"
    _require(state_path.is_file(), "execution state is missing")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    _require(state.get("current_status") == "succeeded", "case did not succeed")
    _require(state.get("attempt_count") == 1, "case did not use exactly one attempt")
    attempts = state.get("attempts")
    _require(isinstance(attempts, list) and len(attempts) == 1, "attempt evidence mismatch")
    attempt = attempts[0]
    publication = attempt.get("publication", {})
    _require(attempt.get("exit_code") == 0, "rtsim exit code is nonzero")
    _require(attempt.get("termination_reason") == "succeeded", "attempt did not succeed")
    _require(publication.get("publication_status") == "committed", "publication is not committed")
    _require(publication.get("integrity_failure_reason") is None, "publication integrity failed")

    result_path = _artifact_path(root, record["result_relpath"])
    staging_path = _artifact_path(root, attempt["temporary_result_path"])
    _require(
        result_path.is_file() and result_path.stat().st_size > 0,
        "final result is missing/empty",
    )
    _require(
        staging_path.is_file() and staging_path.stat().st_size > 0,
        "staging trace is missing/empty",
    )
    staging_sha = file_sha256(staging_path)
    final_sha = file_sha256(result_path)
    sha_values = {
        staging_sha,
        attempt.get("staging_trace_sha256"),
        publication.get("expected_result_sha256"),
        publication.get("observed_final_result_sha256"),
        state.get("final_result_sha256"),
        final_sha,
    }
    _require(len(sha_values) == 1 and None not in sha_values, "staging/final SHA closure failed")

    descriptor = json.loads(
        _artifact_path(root, SOURCE_RELPATH).read_text(encoding="utf-8")
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result_validation = validate_result_document(
        result,
        record["case_id"],
        record["provenance"]["taskset_semantic_hash"],
        descriptor["Emax_j"],
        scheduler_cli_name(record["algorithm"]),
    )

    raw_path = _artifact_path(root, RAW_TASKSET_RELPATH)
    taskset_path = _artifact_path(root, TASKSET_RELPATH)
    system_path = _artifact_path(root, SYSTEM_RELPATH)
    source_path = _artifact_path(root, SOURCE_RELPATH)
    _require(
        file_sha256(TASK_GENERATOR_PATH) == record["provenance"]["generator_sha256"],
        "generator provenance mismatch",
    )
    _require(
        file_sha256(raw_path) == record["provenance"]["taskset_raw_sha256"],
        "raw taskset provenance mismatch",
    )
    _require_regular_nonempty(raw_path, "raw taskset")
    _require_regular_nonempty(taskset_path, "materialized taskset")
    _require(
        file_sha256(raw_path) == file_sha256(taskset_path),
        "raw/materialized taskset SHA mismatch",
    )
    _require(
        formal_semantic_hash(raw_path)
        == formal_semantic_hash(taskset_path)
        == record["provenance"]["taskset_semantic_hash"],
        "taskset semantic provenance mismatch",
    )
    original_expectations = {
        "simulator": record["provenance"]["simulator_sha256"],
        "taskset": file_sha256(taskset_path),
        "system": record["provenance"]["system_config_sha256"],
        "source": record["provenance"]["source_artifact_sha256"],
    }
    for role, expected in original_expectations.items():
        _require(
            state.get(f"{role}_snapshot_sha256") == expected
            and state.get(f"{role}_observed_original_sha256") == expected
            and state.get(f"{role}_executed_snapshot_sha256") == expected,
            f"{role} snapshot provenance mismatch",
        )
        snapshot_path = _artifact_path(root, state[f"{role}_snapshot_relpath"])
        _require(file_sha256(snapshot_path) == expected, f"{role} snapshot SHA mismatch")
    _require(
        formal_semantic_hash(
            _artifact_path(root, state["taskset_snapshot_relpath"])
        )
        == record["provenance"]["taskset_semantic_hash"],
        "taskset snapshot semantic hash mismatch",
    )
    _require(
        state.get("manifest_file_sha256") == file_sha256(record_path)
        and state.get("manifest_record_sha256")
        == execution.record_sha256(normalised_record),
        "record provenance mismatch",
    )

    stdout_path = root / ".b4pe" / "logs" / f"{record['case_id']}.stdout"
    stderr_path = root / ".b4pe" / "logs" / f"{record['case_id']}.stderr"
    _require(
        file_sha256(stdout_path) == state.get("stdout_sha256")
        == publication.get("expected_stdout_sha256"),
        "stdout provenance mismatch",
    )
    _require(
        file_sha256(stderr_path) == state.get("stderr_sha256")
        == publication.get("expected_stderr_sha256"),
        "stderr provenance mismatch",
    )
    diagnostic_text = (
        stdout_path.read_text(encoding="utf-8", errors="replace")
        + "\n"
        + stderr_path.read_text(encoding="utf-8", errors="replace")
    )
    _require(
        re.search(
            r"(?i)(invalid trace extension|trace_target_exists_with_different_content|"
            r"taskset semantic hash (?:missing|mismatch)|\bfatal\b|\bnan\b|"
            r"\binf\b|incomplete trace)",
            diagnostic_text,
        )
        is None,
        "rtsim logs contain a forbidden failure marker",
    )

    before = directory_content_digest(root)
    inspection = inspect_execution.inspect_output(
        root, simulator_binary=record["simulator_path"]
    )
    after = directory_content_digest(root)
    _require(before == after, "inspect_execution changed output contents")
    _require(
        not inspect_execution.inspection_has_integrity_errors(inspection),
        "inspect_execution reported integrity errors",
    )
    return {
        "case_id": record["case_id"],
        "exit_code": attempt["exit_code"],
        "case_status": state["current_status"],
        "attempt_status": attempt["termination_reason"],
        "attempt_count": state["attempt_count"],
        "timeout_count": 0,
        "retry_count": 0,
        "publication_status": publication["publication_status"],
        "staging_sha256": staging_sha,
        "final_result_sha256": final_sha,
        "result_validation": result_validation,
        "provenance": {
            "generator_path": record["provenance"]["generator_path"],
            "generator_sha256": record["provenance"]["generator_sha256"],
            "generator_argv": record["provenance"]["generator_argv"],
            "taskset_raw_sha256": record["provenance"]["taskset_raw_sha256"],
            "taskset_semantic_hash": record["provenance"]["taskset_semantic_hash"],
            "taskset_snapshot_sha256": state["taskset_snapshot_sha256"],
            "system_sha256": file_sha256(system_path),
            "system_snapshot_sha256": state["system_snapshot_sha256"],
            "source_sha256": file_sha256(source_path),
            "source_snapshot_sha256": state["source_snapshot_sha256"],
            "binary_path": record["simulator_path"],
            "binary_sha256": state["simulator_snapshot_sha256"],
            "command_argv": record["command_argv"],
            "executed_argv_normalized": _normalised_executed_argv(
                record, state, attempt
            ),
            "stdout_sha256": state["stdout_sha256"],
            "stderr_sha256": state["stderr_sha256"],
            "result_sha256": state["final_result_sha256"],
        },
        "inspect_exit_code": 0,
        "inspect": inspection,
        "inspect_content_unchanged": before == after,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--output-root", required=True)
    prepare.add_argument("--record", required=True)
    prepare.add_argument("--simulator", default=str(SIMULATOR_DEFAULT))
    prepare.add_argument(
        "--algorithm",
        choices=SMOKE_ALGORITHMS,
        default=DEFAULT_ALGORITHM,
    )
    prepare.add_argument("--seed", type=int, default=GENERATOR_SEED)
    prepare.add_argument(
        "--normalized-utilization",
        default=str(float(TARGET_NORMALIZED_UTILIZATION)),
    )
    validate = subparsers.add_parser("validate")
    validate.add_argument("--record", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.action == "prepare":
            report = prepare_case(
                args.output_root,
                args.record,
                args.simulator,
                algorithm=args.algorithm,
                seed=args.seed,
                normalized_utilization=args.normalized_utilization,
            )
        else:
            report = validate_execution(args.record)
    except (
        RealSmokeCaseError,
        smoke.IntegrationSmokeError,
        execution.ExecutionError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        print(f"real smoke case failed: {exc}", file=sys.stderr)
        return 1
    print(compact_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
