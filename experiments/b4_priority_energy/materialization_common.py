#!/usr/bin/env python3
"""Deterministic, fail-closed B4-PE input materialization."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import yaml

import manifest_common as manifest


B4_DIR = Path(__file__).resolve().parent
REPO_ROOT = B4_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import acceptance_ratio_test as acceptance


MATERIALIZATION_PROTOCOL_PATH = B4_DIR / "materialization_protocol_v1.json"
ADMISSION_PROTOCOL_PATH = B4_DIR / "base_pool_admission_protocol_v1.json"
TASK_GENERATOR_PATH = REPO_ROOT / "global_task_generator.py"
SYSTEM_TEMPLATE_PATH = REPO_ROOT / "v9_3_b4_priority_energy_system_template.yml"
SEMANTIC_HASH_PLACEHOLDER = "__B4PE_MATERIALIZED_TASKSET_SEMANTIC_HASH__"

PROCESSORS = 4
TASK_COUNT = 10
HORIZON_MS = 30000
PERIOD_MIN_MS = 40
PERIOD_MAX_MS = 200
MIN_TASK_UTILIZATION = Fraction(1, 100)
MAX_TASK_UTILIZATION = Fraction(45, 100)
TOTAL_UTILIZATION_TOLERANCE = Fraction(1, 100)
RHO_REFERENCE = Fraction(2, 1)
SOURCE_INTEGRAL_SECONDS = Fraction(22, 1)

BASE_PARAM_KEYS = ("period", "wcet", "arrival_offset", "workload")
EXECUTION_PARAM_KEYS = BASE_PARAM_KEYS + ("task_energy_factor",)
TASK_NAME = re.compile(r"task_([0-9]+)")

PRIORITY_ENERGY_PLACEHOLDER = """priority_energy:
  enabled: false
  profile_id: b4_pe_three_stage_v1
  alpha_w: 0.0
  horizon_ms: 30000
  tick_ms: 1
"""
ENERGY_BOUNDS_PLACEHOLDER = """  initial_energy: 100.0
  max_energy: 1000.0
"""
LEGACY_SOURCE_PLACEHOLDER = """  day_of_year: 187
  time_of_day_ms: 21900000
  base_harvesting_rate: 0.054
  harvesting_scale: 1.0

  use_real_solar_data: true
  solar_data_file: "data/processed/shenyang_solar_minute.csv"
  pv_efficiency: 0.18
  pv_area_m2: 1.0

"""
SCHEDULER_PLACEHOLDER = "      scheduler: gpfp_asap_block\n"


class MaterializationError(RuntimeError):
    """A materialized B4-PE input is missing, ambiguous, or inconsistent."""


def _require(condition, message):
    if not condition:
        raise MaterializationError(message)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_materialization_protocol(path=MATERIALIZATION_PROTOCOL_PATH):
    try:
        protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(
            "materialization protocol is not readable JSON"
        ) from exc
    required = {
        "artifact_rules", "base_pool_admission_protocol_ref",
        "base_pool_admission_protocol_sha256", "governance",
        "identity_protocol_ref",
        "identity_protocol_sha256", "manifest_protocol_ref",
        "manifest_protocol_sha256", "protocol_name", "schema_version",
        "semantic_hash", "status", "system_template_ref",
        "system_template_sha256", "task_generator_ref",
        "task_generator_sha256",
    }
    _require(set(protocol) == required, "materialization protocol fields mismatch")
    _require(
        protocol["schema_version"] == 1
        and protocol["protocol_name"] == "B4-PE-materialization-v1"
        and protocol["status"] == "pilot_authorized",
        "materialization protocol identity mismatch",
    )
    _require(
        protocol["base_pool_admission_protocol_ref"]
        == ADMISSION_PROTOCOL_PATH.name
        and protocol["base_pool_admission_protocol_sha256"]
        == file_sha256(ADMISSION_PROTOCOL_PATH),
        "materialization admission protocol identity mismatch",
    )
    _require(
        protocol["manifest_protocol_ref"]
        == manifest.MANIFEST_PROTOCOL_V4_PATH.name
        and protocol["manifest_protocol_sha256"]
        == file_sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
        "materialization manifest protocol identity mismatch",
    )
    _require(
        protocol["identity_protocol_ref"] == manifest.IDENTITY_PROTOCOL_PATH.name
        and protocol["identity_protocol_sha256"]
        == file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
        "materialization identity protocol mismatch",
    )
    _require(
        protocol["system_template_sha256"] == file_sha256(SYSTEM_TEMPLATE_PATH)
        and protocol["task_generator_sha256"] == file_sha256(TASK_GENERATOR_PATH),
        "materialization input implementation identity mismatch",
    )
    _require(
        protocol["governance"]
        == {
            "formal_runs_authorized": False,
            "negative_control_runs_authorized": False,
            "paper_result_authorized": False,
            "pilot_runs_authorized": True,
        },
        "materialization pilot governance mismatch",
    )
    return protocol


PROTOCOL = load_materialization_protocol()


def bytes_sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


class _IndentedSafeDumper(yaml.SafeDumper):
    """Emit block sequences at the indentation expected by rtsim's YAML reader."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, indentless=False)


def canonical_yaml_bytes(value):
    rendered = yaml.dump(
        value,
        Dumper=_IndentedSafeDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
    return rendered.encode("utf-8")


def walk_inventory_numbers(value):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        yield float(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_inventory_numbers(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from walk_inventory_numbers(item)


def parse_canonical_task_params(params, require_factor):
    _require(isinstance(params, str) and params, "task params must be a string")
    result = {}
    tokens = params.split(",")
    expected = EXECUTION_PARAM_KEYS if require_factor else BASE_PARAM_KEYS
    _require(len(tokens) == len(expected), "task params field count is not canonical")
    observed = []
    for token in tokens:
        _require(token.count("=") == 1, "task params token is not canonical")
        key, value = token.split("=", 1)
        _require(
            key and value and key == key.strip() and value == value.strip(),
            "task params whitespace is not canonical",
        )
        _require(key not in result, f"duplicate task parameter: {key}")
        observed.append(key)
        result[key] = value
    _require(tuple(observed) == expected, "task params sequence is not canonical")
    for key in ("period", "wcet", "arrival_offset"):
        _require(
            re.fullmatch(r"(0|[1-9][0-9]*)", result[key]) is not None,
            f"{key} is not a canonical nonnegative integer",
        )
    _require(result["workload"] == "hash", "task workload must equal hash")
    if require_factor:
        try:
            factor = Decimal(result["task_energy_factor"])
        except Exception as exc:
            raise MaterializationError("task_energy_factor is not decimal") from exc
        _require(
            factor.is_finite() and factor > 0,
            "task_energy_factor must be finite and positive",
        )
        _require(
            canonical_decimal(Fraction(result["task_energy_factor"]))
            == result["task_energy_factor"],
            "task_energy_factor text is not canonical",
        )
    return result


def _task_id(task):
    name = task.get("name")
    match = TASK_NAME.fullmatch(name) if isinstance(name, str) else None
    _require(match is not None, "task name must have form task_<id>")
    return int(match.group(1))


def canonical_decimal(value):
    value = value if isinstance(value, Fraction) else Fraction(value)
    if value == 0:
        return "0"
    with localcontext() as context:
        context.prec = 17
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _template_power_contract():
    try:
        template = yaml.safe_load(SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MaterializationError("system template is not readable YAML") from exc
    _require(isinstance(template, dict), "system template root is not a mapping")
    islands = template.get("cpu_islands")
    _require(
        isinstance(islands, list) and len(islands) == 1,
        "system template must define one CPU island",
    )
    island = islands[0]
    _require(island.get("numcpus") == PROCESSORS, "processor count drift")
    frequency = island.get("base_freq")
    _require(frequency == 9000, "base frequency drift")
    model = template.get("energy_management", {}).get(
        "scheduler_energy_model", {}
    )
    ratios = model.get("frequency_power_ratios", {})
    ratio = ratios.get(frequency, ratios.get(str(frequency)))
    _require(ratio is not None, "base frequency power ratio is missing")
    power = (
        Fraction(str(model.get("base_power")))
        * Fraction(str(model.get("workload_coefficients", {}).get("hash")))
        * Fraction(str(ratio))
    )
    _require(power > 0, "base hash power must be positive")
    return power


def _release_count(period, offset):
    if offset >= HORIZON_MS:
        return 0
    return (HORIZON_MS - 1 - offset) // period + 1


def validate_base_taskset(document, normalized_utilization=None):
    _require(isinstance(document, dict), "base taskset root must be a mapping")
    tasks = document.get("taskset")
    _require(
        isinstance(tasks, list) and len(tasks) == TASK_COUNT,
        "base taskset must contain exactly ten tasks",
    )
    identities = set()
    total_utilization = Fraction(0, 1)
    for task in tasks:
        _require(isinstance(task, dict), "base task entry must be a mapping")
        task_id = _task_id(task)
        _require(task_id not in identities, "duplicate task id")
        identities.add(task_id)
        _require(
            set(task) <= {
                "name", "iat", "runtime", "startcpu", "deadline", "params",
                "code",
            },
            f"task_{task_id} contains an unsupported task field",
        )
        period = task.get("iat")
        runtime = task.get("runtime")
        deadline = task.get("deadline")
        _require(
            all(type(value) is int for value in (period, runtime, deadline)),
            f"task_{task_id} C/T/D must be integers",
        )
        _require(
            PERIOD_MIN_MS <= period <= PERIOD_MAX_MS
            and 1 <= runtime <= deadline <= period,
            f"task_{task_id} violates frozen C/D/T bounds",
        )
        utilization = Fraction(runtime, period)
        _require(
            MIN_TASK_UTILIZATION <= utilization <= MAX_TASK_UTILIZATION,
            f"task_{task_id} utilization is outside frozen bounds",
        )
        params = parse_canonical_task_params(
            task.get("params"), require_factor=False
        )
        offset = int(params["arrival_offset"])
        _require(
            params["period"] == str(period)
            and params["wcet"] == str(runtime)
            and 0 <= offset < period,
            f"task_{task_id} params do not match computation fields",
        )
        code = task.get("code")
        _require(
            isinstance(code, list)
            and code
            and all(isinstance(item, str) for item in code),
            f"task_{task_id} code must be a non-empty string list",
        )
        total_utilization += utilization
    _require(identities == set(range(TASK_COUNT)), "task ids must be exactly 0..9")
    if normalized_utilization is not None:
        target = Fraction(str(normalized_utilization)) * PROCESSORS
        _require(
            abs(total_utilization - target) <= TOTAL_UTILIZATION_TOLERANCE,
            "base taskset total utilization is outside frozen tolerance",
        )
    return document


def _energy_groups(document):
    validate_base_taskset(document)
    tasks = document["taskset"]
    ranked = sorted(tasks, key=lambda task: (task["iat"], _task_id(task)))
    q0_j_per_ms = _template_power_contract() / 1000

    def group_energy(group):
        return sum(
            (
                _release_count(
                    task["iat"],
                    int(parse_canonical_task_params(
                        task["params"], require_factor=False
                    )["arrival_offset"]),
                )
                * task["runtime"]
                * q0_j_per_ms
                for task in group
            ),
            Fraction(0, 1),
        )

    high = ranked[:PROCESSORS]
    low = ranked[PROCESSORS:]
    W_H = group_energy(high)
    W_L = group_energy(low)
    _require(W_H > 0 and W_L > 0, "taskset energy groups must be positive")
    return ranked, q0_j_per_ms, W_H, W_L


def _factors(W_H, W_L, rho):
    rho = Fraction(str(rho))
    _require(rho in {Fraction(1), Fraction(2)}, "rho_E must equal 1 or 2")
    low = (W_H + W_L) / (rho * W_H + W_L)
    high = rho * low
    return high, low


def derive_execution_taskset(base_document, rho_E):
    validate_base_taskset(base_document)
    ranked, q0_j_per_ms, W_H, W_L = _energy_groups(base_document)
    high_factor, low_factor = _factors(W_H, W_L, rho_E)
    high_ids = {_task_id(task) for task in ranked[:PROCESSORS]}
    high_text = canonical_decimal(high_factor)
    low_text = canonical_decimal(low_factor)
    derived = copy.deepcopy(base_document)
    for task in derived["taskset"]:
        params = parse_canonical_task_params(
            task.get("params"), require_factor=False
        )
        factor = high_text if _task_id(task) in high_ids else low_text
        task["params"] = ",".join(
            f"{key}={params[key]}" for key in BASE_PARAM_KEYS
        ) + f",task_energy_factor={factor}"
    validate_execution_taskset(base_document, derived, rho_E)
    return derived, {
        "q0_j_per_ms": q0_j_per_ms,
        "W_H_j": W_H,
        "W_L_j": W_L,
        "high_factor": high_factor,
        "low_factor": low_factor,
        "high_factor_text": high_text,
        "low_factor_text": low_text,
        "top_task_ids": sorted(high_ids),
    }


def validate_execution_taskset(base_document, execution_document, rho_E):
    validate_base_taskset(base_document)
    _require(
        isinstance(execution_document, dict)
        and set(execution_document) == set(base_document),
        "execution taskset root fields drifted",
    )
    base_tasks = base_document["taskset"]
    execution_tasks = execution_document.get("taskset")
    _require(
        isinstance(execution_tasks, list)
        and len(execution_tasks) == len(base_tasks),
        "execution task count drifted",
    )
    ranked, _q0, W_H, W_L = _energy_groups(base_document)
    high, low = _factors(W_H, W_L, rho_E)
    high_text = canonical_decimal(high)
    low_text = canonical_decimal(low)
    high_ids = {_task_id(task) for task in ranked[:PROCESSORS]}
    for base, derived in zip(base_tasks, execution_tasks):
        _require(
            set(base) == set(derived),
            f"{base.get('name')} task fields drifted",
        )
        expected = copy.deepcopy(base)
        base_params = parse_canonical_task_params(
            base["params"], require_factor=False
        )
        derived_params = parse_canonical_task_params(
            derived["params"], require_factor=True
        )
        factor = derived_params.pop("task_energy_factor")
        expected["params"] = base_params
        observed = copy.deepcopy(derived)
        observed["params"] = derived_params
        _require(observed == expected, f"{base['name']} computation fields drifted")
        expected_factor = (
            high_text if _task_id(base) in high_ids else low_text
        )
        _require(
            factor == expected_factor,
            f"{base['name']} task_energy_factor group mismatch",
        )
    for key, value in base_document.items():
        if key != "taskset":
            _require(
                execution_document[key] == value,
                f"execution taskset non-task field drifted: {key}",
            )
    return execution_document


def source_energy_contract(base_document, lambda_E):
    ranked, q0_j_per_ms, W_H, W_L = _energy_groups(base_document)
    reference_high, reference_low = _factors(
        W_H, W_L, RHO_REFERENCE
    )
    burst = sum(
        (
            task["runtime"] * q0_j_per_ms * reference_high
            for task in ranked[:PROCESSORS]
        ),
        Fraction(0, 1),
    )
    demand = W_H + W_L
    alpha = (
        Fraction(str(lambda_E)) * demand - burst
    ) / SOURCE_INTEGRAL_SECONDS
    _require(alpha >= 0, "frozen alpha is negative for base taskset")
    return {
        "E0_j": burst,
        "Emax_j": 2 * burst,
        "alpha_w": alpha,
        "nominal_demand_j": demand,
        "W_H_j": W_H,
        "W_L_j": W_L,
        "reference_high_factor": reference_high,
        "reference_low_factor": reference_low,
    }


def offered_harvest_trace_sha256(alpha_w):
    alpha = alpha_w if isinstance(alpha_w, Fraction) else Fraction(alpha_w)
    digest = hashlib.sha256()
    stages = (
        (5000, Fraction(1)),
        (10000, Fraction(1, 5)),
        (15000, Fraction(1)),
    )
    for count, multiplier in stages:
        increment = alpha * multiplier / 1000
        token = (
            f"{increment.numerator}/{increment.denominator}\n"
        ).encode("ascii")
        for _ in range(count):
            digest.update(token)
    return digest.hexdigest()


def _generator_argv(destination, record):
    target_total = Fraction(record["utilization"]) * PROCESSORS
    return [
        sys.executable,
        str(TASK_GENERATOR_PATH),
        "--num-tasks", str(TASK_COUNT),
        "--utilization", canonical_decimal(target_total),
        "--min-period", str(PERIOD_MIN_MS),
        "--max-period", str(PERIOD_MAX_MS),
        "--cpus", str(PROCESSORS),
        "--constrained-deadlines",
        "--arrival-offset",
        "--system-config", str(SYSTEM_TEMPLATE_PATH),
        "--seed", str(record["taskset_seed"]),
        "--min-task-util", "0.01",
        "--max-task-util", "0.45",
        "--wcet-rounding", "compensated",
        "--actual-utilization-tolerance-total", "0.01",
        "--task-workload-candidate", "hash",
        "--output", str(destination),
    ]


def generate_base_taskset(record):
    with tempfile.TemporaryDirectory(prefix="b4pe-materialize-generator-") as temp:
        temporary_root = Path(temp)
        raw_path = temporary_root / "taskset.raw.yml"
        completed = subprocess.run(
            _generator_argv(raw_path, record),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={
                **os.environ,
                "PARTSIM_LOG_DIR": str(temporary_root / "logs"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
        )
        _require(
            completed.returncode == 0,
            "task generator exited {}: {}".format(
                completed.returncode, (completed.stderr or "")[-2000:]
            ),
        )
        _require(raw_path.is_file(), "task generator produced no taskset")
        try:
            document = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise MaterializationError(
                "generated base taskset is not readable YAML"
            ) from exc
    validate_base_taskset(document, record["utilization"])
    # Re-render the semantic document.  This deliberately excludes generator
    # comments containing wall-clock time and absolute invocation paths.
    canonical = canonical_yaml_bytes(document)
    normalized = yaml.safe_load(canonical.decode("utf-8"))
    validate_base_taskset(normalized, record["utilization"])
    return normalized, canonical


def _replace_once(source, placeholder, replacement, label):
    _require(
        source.count(placeholder) == 1,
        f"system template {label} placeholder count is not one",
    )
    return source.replace(placeholder, replacement, 1)


def render_system_config(energy, algorithm_cli):
    template = SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
    priority = """priority_energy:
  enabled: true
  profile_id: b4_pe_three_stage_v1
  alpha_w: {}
  horizon_ms: 30000
  tick_ms: 1
""".format(repr(float(energy["alpha_w"])))
    bounds = """  initial_energy: {}
  max_energy: {}
""".format(
        repr(float(energy["E0_j"])),
        repr(float(energy["Emax_j"])),
    )
    rendered = _replace_once(
        template, PRIORITY_ENERGY_PLACEHOLDER, priority, "priority-energy"
    )
    rendered = _replace_once(
        rendered, ENERGY_BOUNDS_PLACEHOLDER, bounds, "energy-bounds"
    )
    rendered = _replace_once(
        rendered, LEGACY_SOURCE_PLACEHOLDER, "", "legacy-source"
    )
    rendered = _replace_once(
        rendered,
        SCHEDULER_PLACEHOLDER,
        f"      scheduler: {algorithm_cli}\n",
        "scheduler",
    )
    document = yaml.safe_load(rendered)
    _require(
        document["cpu_islands"][0]["kernel"]["scheduler"] == algorithm_cli,
        "rendered scheduler mismatch",
    )
    _require(
        not {
            "day_of_year", "time_of_day_ms", "base_harvesting_rate",
            "harvesting_scale", "use_real_solar_data", "solar_data_file",
            "pv_efficiency", "pv_area_m2",
        }.intersection(document["energy_management"]),
        "rendered system retained a legacy harvest source",
    )
    return rendered.encode("utf-8")


def render_source_descriptor(record, energy):
    descriptor = {
        "schema": "b4-pe-materialized-source-v1",
        "source_id": record["source_id"],
        "base_taskset_id": record["taskset_id"],
        "lambda_E": record["lambda_E"],
        "profile_id": "b4_pe_three_stage_v1",
        "E0_j": float(energy["E0_j"]),
        "Emax_j": float(energy["Emax_j"]),
        "source": {
            "kind": "scaled_piecewise",
            "scale_w": float(energy["alpha_w"]),
            "segments": [
                {
                    "start_time_ms": 0,
                    "end_time_ms": 5000,
                    "multiplier": 1.0,
                },
                {
                    "start_time_ms": 5000,
                    "end_time_ms": 15000,
                    "multiplier": 0.2,
                },
                {
                    "start_time_ms": 15000,
                    "end_time_ms": 30000,
                    "multiplier": 1.0,
                },
            ],
        },
    }
    return descriptor, canonical_json_bytes(descriptor)


def _publish_identical_or_fail(root, relative, data):
    manifest.validate_relative_path(relative, "materialized artifact path")
    destination = Path(root).joinpath(*Path(relative).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        _require(
            destination.is_file()
            and not destination.is_symlink()
            and destination.read_bytes() == data,
            f"existing materialized artifact conflicts: {relative}",
        )
        return destination
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".materializing",
        dir=str(destination.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            _require(
                destination.is_file()
                and not destination.is_symlink()
                and destination.read_bytes() == data,
                f"concurrent materialized artifact conflicts: {relative}",
            )
        directory = os.open(
            destination.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def _entry(path, data, **fields):
    return {"path": path, "sha256": bytes_sha256(data), **fields}


def taskset_semantic_hash_bytes(payload):
    with tempfile.NamedTemporaryFile(
        prefix="b4pe-semantic-", suffix=".yml"
    ) as handle:
        handle.write(payload)
        handle.flush()
        return acceptance.taskset_semantic_hash(Path(handle.name))


def _exact_inventory_energy(energy):
    return {
        name: canonical_decimal(energy[name])
        for name in (
            "E0_j", "Emax_j", "alpha_w", "nominal_demand_j",
            "W_H_j", "W_L_j",
        )
    }


def _validate_records_for_materialization(records):
    _require(isinstance(records, list) and records, "materialization records missing")
    for record in records:
        _require(
            isinstance(record, dict)
            and record.get("schema_version") == 4
            and record.get("protocol_name")
            == manifest.PROTOCOL_V4["protocol_name"],
            "materialization requires manifest v4 records",
        )
        expected = manifest.build_case(
            record["phase"],
            record["utilization"],
            record["replicate_index"],
            record["lambda_E"],
            record["rho_E"],
            record["algorithm"],
            manifest.PROTOCOL_V4,
        )
        _require(record == expected, "materialization record does not match v4")
    inventory_paths = {
        record["materialization_inventory_relpath"] for record in records
    }
    _require(len(inventory_paths) == 1, "inventory path is inconsistent")
    admission_paths = {
        record["base_pool_admission_inventory_relpath"]
        for record in records
    }
    _require(len(admission_paths) == 1, "admission inventory path is inconsistent")
    return records


def manifest_record_sha256(record):
    return bytes_sha256(manifest.compact_json(record).encode("utf-8"))


def _load_admitted_bases(records, root, manifest_sha256):
    representatives = {}
    for record in records:
        owner = representatives.setdefault(record["taskset_id"], record)
        _require(
            owner["taskset_pool"] == record["taskset_pool"]
            and owner["utilization"] == record["utilization"]
            and owner["replicate_index"] == record["replicate_index"]
            and owner["taskset_seed"] == record["taskset_seed"]
            and owner["base_taskset_artifact_relpath"]
            == record["base_taskset_artifact_relpath"],
            "base taskset identity is inconsistent",
        )
    relative = records[0]["base_pool_admission_inventory_relpath"]
    manifest.validate_relative_path(relative, "base admission inventory path")
    path = root.joinpath(*Path(relative).parts)
    _require(
        path.is_file() and not path.is_symlink(),
        "base admission inventory is missing or not a regular file",
    )
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(
            "base admission inventory is not readable JSON"
        ) from exc
    _require(
        payload == canonical_json_bytes(document),
        "base admission inventory bytes are not canonical",
    )
    required = {
        "schema_version", "protocol_name", "admission_protocol_sha256",
        "manifest_file_sha256", "manifest_protocol_sha256",
        "identity_protocol_sha256", "task_generator_sha256",
        "cpu_only_system_config_path",
        "cpu_only_system_config_sha256",
        "cpu_only_simulator_sha256", "base_tasksets",
    }
    _require(
        isinstance(document, dict) and set(document) == required,
        "base admission inventory fields mismatch",
    )
    _require(
        document["schema_version"] == 1
        and document["protocol_name"]
        == "B4-PE-base-pool-admission-v1"
        and document["admission_protocol_sha256"]
        == file_sha256(ADMISSION_PROTOCOL_PATH)
        and document["manifest_file_sha256"] == manifest_sha256
        and document["manifest_protocol_sha256"]
        == file_sha256(manifest.MANIFEST_PROTOCOL_V4_PATH)
        and document["identity_protocol_sha256"]
        == file_sha256(manifest.IDENTITY_PROTOCOL_PATH)
        and document["task_generator_sha256"]
        == file_sha256(TASK_GENERATOR_PATH),
        "base admission inventory identity mismatch",
    )
    sha_pattern = re.compile(r"[0-9a-f]{64}")
    _require(
        sha_pattern.fullmatch(document["cpu_only_system_config_sha256"])
        is not None
        and sha_pattern.fullmatch(document["cpu_only_simulator_sha256"])
        is not None,
        "base admission runtime identity is invalid",
    )
    cpu_system_relative = document["cpu_only_system_config_path"]
    manifest.validate_relative_path(
        cpu_system_relative, "CPU-only admission system path"
    )
    cpu_system_path = root.joinpath(*Path(cpu_system_relative).parts)
    _require(
        cpu_system_path.is_file() and not cpu_system_path.is_symlink(),
        "CPU-only admission system is missing or not a regular file",
    )
    try:
        cpu_system_payload = cpu_system_path.read_bytes()
        cpu_system = yaml.safe_load(cpu_system_payload.decode("utf-8"))
        island = cpu_system["cpu_islands"][0]
        priority_energy = cpu_system["priority_energy"]
    except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise MaterializationError(
            "CPU-only admission system is unreadable"
        ) from exc
    _require(
        bytes_sha256(cpu_system_payload)
        == document["cpu_only_system_config_sha256"]
        and priority_energy.get("enabled") is False
        and island.get("numcpus") == PROCESSORS
        and island.get("kernel", {}).get("scheduler")
        == "gpfp_asap_block"
        and island.get("kernel", {}).get("task_placement") == "global",
        "CPU-only admission system identity or semantics mismatch",
    )
    entries = document["base_tasksets"]
    _require(isinstance(entries, list), "base admission entries missing")
    base_documents = {}
    base_payloads = {}
    base_entries = []
    entry_fields = {
        "taskset_pool", "utilization", "replicate_index", "taskset_seed",
        "taskset_id", "base_taskset_path", "base_taskset_sha256",
        "base_semantic_hash", "cpu_only_simulator_sha256",
        "cpu_only_system_config_sha256", "horizon_ms",
        "adjudicable_job_count", "deadline_miss_count",
        "admission_status",
    }
    for taskset_id, record in sorted(representatives.items()):
        matches = [
            item for item in entries
            if isinstance(item, dict)
            and item.get("taskset_id") == taskset_id
        ]
        _require(
            len(matches) == 1,
            f"base admission mapping mismatch: {taskset_id}",
        )
        entry = matches[0]
        _require(
            set(entry) == entry_fields,
            f"base admission entry fields mismatch: {taskset_id}",
        )
        _require(
            entry["taskset_pool"] == record["taskset_pool"]
            and entry["utilization"] == record["utilization"]
            and entry["replicate_index"] == record["replicate_index"]
            and entry["taskset_seed"] == record["taskset_seed"]
            and entry["base_taskset_path"]
            == record["base_taskset_artifact_relpath"]
            and entry["cpu_only_simulator_sha256"]
            == document["cpu_only_simulator_sha256"]
            and entry["cpu_only_system_config_sha256"]
            == document["cpu_only_system_config_sha256"],
            f"base admission identity mismatch: {taskset_id}",
        )
        _require(
            entry["admission_status"] == "accepted"
            and entry["horizon_ms"] == HORIZON_MS
            and type(entry["adjudicable_job_count"]) is int
            and entry["adjudicable_job_count"] > 0
            and entry["deadline_miss_count"] == 0,
            f"base taskset did not pass CPU-only admission: {taskset_id}",
        )
        base_relative = entry["base_taskset_path"]
        manifest.validate_relative_path(base_relative, "admitted base taskset path")
        base_path = root.joinpath(*Path(base_relative).parts)
        _require(
            base_path.is_file() and not base_path.is_symlink(),
            f"admitted base taskset is missing: {taskset_id}",
        )
        try:
            base_payload = base_path.read_bytes()
            base_document = yaml.safe_load(base_payload.decode("utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise MaterializationError(
                f"admitted base taskset is unreadable: {taskset_id}"
            ) from exc
        validate_base_taskset(base_document, record["utilization"])
        _require(
            canonical_yaml_bytes(base_document) == base_payload
            and bytes_sha256(base_payload) == entry["base_taskset_sha256"]
            and taskset_semantic_hash_bytes(base_payload)
            == entry["base_semantic_hash"],
            f"admitted base taskset identity mismatch: {taskset_id}",
        )
        base_documents[taskset_id] = base_document
        base_payloads[base_relative] = base_payload
        base_entries.append(
            _entry(
                base_relative,
                base_payload,
                taskset_id=taskset_id,
                taskset_seed=record["taskset_seed"],
                semantic_hash=entry["base_semantic_hash"],
            )
        )
    return (
        base_documents,
        base_payloads,
        base_entries,
        relative,
        bytes_sha256(payload),
    )


def materialize_records(records, output_root, manifest_sha256):
    records = _validate_records_for_materialization(records)
    root = Path(output_root)
    _require(root.is_absolute(), "output root must be absolute")
    resolved_root = root.resolve(strict=False)
    _require(
        not (
            resolved_root == REPO_ROOT
            or REPO_ROOT in resolved_root.parents
        ),
        "materialization output root must be outside the repository",
    )
    _require(
        isinstance(manifest_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is not None,
        "manifest SHA must be lowercase SHA-256",
    )
    root.mkdir(parents=True, exist_ok=True)

    (
        base_documents,
        artifact_payloads,
        base_entries,
        admission_inventory_relative,
        admission_inventory_sha,
    ) = _load_admitted_bases(records, root, manifest_sha256)

    execution_entries = []
    execution_by_key = {}
    execution_representatives = {}
    for record in records:
        key = (record["taskset_id"], record["rho_E"])
        owner = execution_representatives.setdefault(key, record)
        _require(
            owner["taskset_artifact_relpath"]
            == record["taskset_artifact_relpath"],
            "rho-specific taskset path is inconsistent",
        )
    for (taskset_id, rho_E), record in sorted(execution_representatives.items()):
        document, energy = derive_execution_taskset(
            base_documents[taskset_id], rho_E
        )
        payload = canonical_yaml_bytes(document)
        path = record["taskset_artifact_relpath"]
        artifact_payloads[path] = payload
        semantic = taskset_semantic_hash_bytes(payload)
        entry = _entry(
            path,
            payload,
            taskset_id=taskset_id,
            rho_E=rho_E,
            semantic_hash=semantic,
            high_factor=energy["high_factor_text"],
            low_factor=energy["low_factor_text"],
            W_H_j=canonical_decimal(energy["W_H_j"]),
            W_L_j=canonical_decimal(energy["W_L_j"]),
        )
        execution_entries.append(entry)
        execution_by_key[(taskset_id, rho_E)] = entry

    source_entries = []
    source_by_id = {}
    source_energy_by_id = {}
    source_representatives = {}
    for record in records:
        owner = source_representatives.setdefault(record["source_id"], record)
        _require(
            owner["taskset_id"] == record["taskset_id"]
            and owner["lambda_E"] == record["lambda_E"]
            and owner["source_artifact_relpath"]
            == record["source_artifact_relpath"],
            "source identity is inconsistent",
        )
    for source_id, record in sorted(source_representatives.items()):
        energy = source_energy_contract(
            base_documents[record["taskset_id"]], record["lambda_E"]
        )
        _descriptor, payload = render_source_descriptor(record, energy)
        trace_sha = offered_harvest_trace_sha256(energy["alpha_w"])
        path = record["source_artifact_relpath"]
        artifact_payloads[path] = payload
        entry = _entry(
            path,
            payload,
            source_id=source_id,
            taskset_id=record["taskset_id"],
            lambda_E=record["lambda_E"],
            offered_harvest_trace_sha256=trace_sha,
            **_exact_inventory_energy(energy),
        )
        source_entries.append(entry)
        source_by_id[source_id] = entry
        source_energy_by_id[source_id] = energy

    system_entries = []
    system_by_case = {}
    system_representatives = {}
    for record in records:
        path = record["system_config_artifact_relpath"]
        owner = system_representatives.setdefault(path, record)
        _require(owner["case_id"] == record["case_id"], "system path collision")
    for path, record in sorted(system_representatives.items()):
        payload = render_system_config(
            source_energy_by_id[record["source_id"]],
            record["algorithm_cli"],
        )
        artifact_payloads[path] = payload
        entry = _entry(
            path,
            payload,
            case_id=record["case_id"],
            algorithm=record["algorithm"],
            scheduler=record["algorithm_cli"],
        )
        system_entries.append(entry)
        system_by_case[record["case_id"]] = entry

    cases = []
    for record in sorted(records, key=lambda item: item["case_id"]):
        execution_entry = execution_by_key[
            (record["taskset_id"], record["rho_E"])
        ]
        source_entry = source_by_id[record["source_id"]]
        energy = source_energy_by_id[record["source_id"]]
        cases.append(
            {
                "case_id": record["case_id"],
                "manifest_record_sha256": manifest_record_sha256(record),
                "phase": record["phase"],
                "algorithm": record["algorithm"],
                "taskset_id": record["taskset_id"],
                "source_id": record["source_id"],
                "lambda_E": record["lambda_E"],
                "rho_E": record["rho_E"],
                "base_taskset_path": record[
                    "base_taskset_artifact_relpath"
                ],
                "execution_taskset_path": execution_entry["path"],
                "execution_taskset_sha256": execution_entry["sha256"],
                "execution_taskset_semantic_hash": execution_entry[
                    "semantic_hash"
                ],
                "source_artifact_path": source_entry["path"],
                "source_artifact_sha256": source_entry["sha256"],
                "system_config_path": record[
                    "system_config_artifact_relpath"
                ],
                "system_config_sha256": system_by_case[
                    record["case_id"]
                ]["sha256"],
                "result_path": record["result_relpath"],
                "offered_harvest_trace_sha256": source_entry[
                    "offered_harvest_trace_sha256"
                ],
                **{
                    name: canonical_decimal(energy[name])
                    for name in ("E0_j", "Emax_j", "alpha_w")
                },
            }
        )

    protocol_sha = file_sha256(MATERIALIZATION_PROTOCOL_PATH)
    inventory = {
        "schema_version": 1,
        "protocol_name": PROTOCOL["protocol_name"],
        "materialization_protocol_sha256": protocol_sha,
        "base_pool_admission_protocol_sha256":
            file_sha256(ADMISSION_PROTOCOL_PATH),
        "base_pool_admission_inventory_relpath":
            admission_inventory_relative,
        "base_pool_admission_inventory_sha256":
            admission_inventory_sha,
        "manifest_file_sha256": manifest_sha256,
        "manifest_protocol_sha256": file_sha256(
            manifest.MANIFEST_PROTOCOL_V4_PATH
        ),
        "identity_protocol_sha256": file_sha256(
            manifest.IDENTITY_PROTOCOL_PATH
        ),
        "task_generator_sha256": file_sha256(TASK_GENERATOR_PATH),
        "system_template_sha256": file_sha256(SYSTEM_TEMPLATE_PATH),
        "base_tasksets": base_entries,
        "execution_tasksets": execution_entries,
        "sources": source_entries,
        "system_configs": system_entries,
        "cases": cases,
    }
    for relative, payload in sorted(artifact_payloads.items()):
        _publish_identical_or_fail(root, relative, payload)
    inventory_path = records[0]["materialization_inventory_relpath"]
    _publish_identical_or_fail(
        root, inventory_path, canonical_json_bytes(inventory)
    )
    return inventory


def validate_inventory_for_record(document, record, taskset_sha, semantic_hash):
    _require(isinstance(document, dict), "materialization inventory is not an object")
    _require(
        document.get("schema_version") == 1
        and document.get("protocol_name") == PROTOCOL["protocol_name"]
        and document.get("materialization_protocol_sha256")
        == file_sha256(MATERIALIZATION_PROTOCOL_PATH),
        "materialization inventory protocol mismatch",
    )
    matches = [
        entry
        for entry in document.get("execution_tasksets", [])
        if isinstance(entry, dict)
        and entry.get("taskset_id") == record["taskset_id"]
        and entry.get("rho_E") == record["rho_E"]
        and entry.get("path") == record["taskset_artifact_relpath"]
    ]
    _require(len(matches) == 1, "materialization inventory taskset mapping mismatch")
    _require(
        matches[0].get("sha256") == taskset_sha
        and matches[0].get("semantic_hash") == semantic_hash,
        "materialization inventory taskset identity mismatch",
    )
    case_matches = [
        entry
        for entry in document.get("cases", [])
        if isinstance(entry, dict)
        and entry.get("case_id") == record["case_id"]
    ]
    _require(
        len(case_matches) == 1
        and case_matches[0].get("manifest_record_sha256")
        == manifest_record_sha256(record)
        and case_matches[0].get("taskset_id") == record["taskset_id"]
        and case_matches[0].get("rho_E") == record["rho_E"]
        and case_matches[0].get("execution_taskset_path")
        == record["taskset_artifact_relpath"]
        and case_matches[0].get("execution_taskset_sha256") == taskset_sha
        and case_matches[0].get("execution_taskset_semantic_hash")
        == semantic_hash,
        "materialization inventory manifest record mapping mismatch",
    )
    return matches[0]
