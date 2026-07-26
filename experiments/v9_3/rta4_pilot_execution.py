"""Reproducible, engineering-only execution for the RTA4 pilot selection.

This namespace deliberately persists no mathematical result.  Workers may
evaluate the existing public RTA/simulator entry points in memory, but the
parent records only timing, resource, timeout, retry, I/O, and provenance
evidence.
"""

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import shutil
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

import yaml

from .constrained_taskset_identity import TasksetIdentityCertificate
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import (
    RTA4_CORES, canonical_json, default_rta4_formal_config, domain_hash,
    validate_rta4_formal_config,
)
from .rta4_formal_environment import (
    RTA4_DEPENDENCY_DOMAIN, RTA4_DEPENDENCY_MANIFEST_VERSION,
    RTA4_ENVIRONMENT_DOMAIN, RTA4_ENVIRONMENT_MANIFEST_VERSION,
    RTA4_HARDWARE_DOMAIN, RTA4_HARDWARE_MANIFEST_VERSION,
    RTA4_SIMULATOR_DOMAIN, RTA4_SIMULATOR_MANIFEST_VERSION,
    RTA4_SOURCE_DOMAIN, RTA4_SOURCE_MANIFEST_VERSION,
    build_dependency_manifest, build_environment_manifest,
    build_hardware_manifest, validate_bound_source_file,
    validate_identity_manifest, validate_source_manifest,
)
from .rta4_formal_pilot import (
    RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_OBSERVATIONS,
    RTA4_PILOT_OUTPUT_MARKER, RTA4_PILOT_REPORT, build_pilot_observations,
    build_pilot_report, validate_pilot_manifest, validate_pilot_observations,
    validate_pilot_report,
)
from .rta4_formal_plan import FormalPlanRecord, iter_formal_plan
from .rta4_formal_pipeline import RTA4FormalRunner
from .rta4_formal_store import (
    FORMAL_TASKSET_STORE_MANIFEST, RTA4FormalTasksetStore,
    _store_manifest as _formal_store_manifest,
)


RTA4_PILOT_EXECUTION_CONFIG_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_EXECUTION_CONFIG_V2"
)
RTA4_PILOT_EXECUTION_MANIFEST_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_EXECUTION_MANIFEST_V2"
)
RTA4_PILOT_CHECKPOINT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_CHECKPOINT_V2"
)
RTA4_PILOT_RAW_TERMINAL_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_RAW_TERMINAL_V1"
)
RTA4_PILOT_FINAL_TERMINAL_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_FINAL_TERMINAL_V2"
)
RTA4_PILOT_TERMINAL_VERSION = RTA4_PILOT_FINAL_TERMINAL_VERSION
RTA4_PILOT_CHECKPOINT_EVENT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_CHECKPOINT_EVENT_V1"
)
RTA4_PILOT_RESUME_EVENT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_RESUME_EVENT_V1"
)
RTA4_PILOT_STORE_MANIFEST_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_TASKSET_STORE_V1"
)
RTA4_PILOT_AUDIT_VERSION = "ASAP_BLOCK_V9_3_RTA4_PILOT_AUDIT_V2"
RTA4_PILOT_RUNTIME_CI_RULE_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_RUNTIME_CI_RULE_V1"
)

RTA4_PILOT_EXECUTION_CONFIG_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_EXECUTION_CONFIG:v2"
)
RTA4_PILOT_EXECUTION_MANIFEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_EXECUTION_MANIFEST:v2"
)
RTA4_PILOT_CHECKPOINT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_CHECKPOINT:v2"
)
RTA4_PILOT_RAW_TERMINAL_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_RAW_TERMINAL:v1"
)
RTA4_PILOT_FINAL_TERMINAL_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_FINAL_TERMINAL:v2"
)
RTA4_PILOT_TERMINAL_DOMAIN = RTA4_PILOT_FINAL_TERMINAL_DOMAIN
RTA4_PILOT_CHECKPOINT_EVENT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_CHECKPOINT_EVENT:v1"
)
RTA4_PILOT_RESUME_EVENT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_RESUME_EVENT:v1"
)
RTA4_PILOT_STORE_MANIFEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_TASKSET_STORE:v1"
)
RTA4_PILOT_AUDIT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_AUDIT:v2"

RTA4_PILOT_TEST_EXECUTION_CLASS = "ENGINEERING_PILOT_TEST"
RTA4_PILOT_EXECUTION_CONFIG = "rta4_pilot_execution_config.json"
RTA4_PILOT_EXECUTION_MANIFEST = "rta4_pilot_execution_manifest.json"
RTA4_PILOT_CHECKPOINT = "rta4_pilot_checkpoint.json"
RTA4_PILOT_AUDIT = "rta4_pilot_audit.json"
RTA4_PILOT_RAW_TERMINAL_DIRECTORY = "rta4_pilot_raw_terminals"
RTA4_PILOT_FINAL_TERMINAL_DIRECTORY = "rta4_pilot_final_terminals"
RTA4_PILOT_TERMINAL_DIRECTORY = RTA4_PILOT_FINAL_TERMINAL_DIRECTORY
RTA4_PILOT_TRACE_DIRECTORY = "rta4_pilot_traces"
RTA4_PILOT_CHECKPOINT_DIRECTORY = "rta4_pilot_checkpoints"
RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY = "rta4_pilot_checkpoint_events"
RTA4_PILOT_RESUME_EVENT_DIRECTORY = "rta4_pilot_resume_events"
RTA4_PILOT_WORKER_TRACE_DIRECTORY = "rta4_pilot_worker_tmp"
RTA4_PILOT_STORE_MANIFEST = "rta4_pilot_taskset_store_manifest.json"

PILOT_RESUME_POLICY = "TRANSACTIONAL_RAW_EVIDENCE_RESUME_V2"
PILOT_THROUGHPUT_DEFINITION = (
    "floor(1000*completed_batch_records/batch_wall_seconds);"
    "zero_if_nonpositive_elapsed"
)
PILOT_OUTPUT_IO_DEFINITION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_OUTPUT_IO_DEFINITION_V2:"
    "canonical_json(final_terminal_preimage)_utf8_bytes_plus_trace_bytes;"
    "output_io_bytes_and_final_terminal_sha256_excluded"
)

_CONFIG_FIELDS = frozenset({
    "execution_config_version", "execution_class", "pilot_manifest",
    "source_manifest", "output_root", "taskset_store",
    "simulator_manifest", "simulation_support",
    "default_worker_count", "max_in_flight",
    "provisional_rta_attempt_timeout_seconds",
    "provisional_simulation_timeout_seconds", "memory_soft_limit_bytes",
    "checkpoint_interval_records", "maximum_attempts",
    "runtime_ci_rule_version", "resume_policy",
    "throughput_definition", "output_io_definition",
    "dependency_manifest", "environment_manifest", "hardware_manifest",
    "execution_config_id",
})

_RAW_METRIC_FIELDS = frozenset({
    "runtime_wall_milliseconds", "runtime_cpu_milliseconds",
    "peak_rss_bytes", "timed_out", "attempt_count",
    "worker_throughput_milli_records_per_second",
    "simulation_wall_milliseconds", "trace_size_bytes",
    "engineering_error",
})
_FINAL_METRIC_FIELDS = frozenset({
    *_RAW_METRIC_FIELDS,
    "checkpoint_overhead_milliseconds", "resume_overhead_milliseconds",
    "output_io_bytes",
    "ci_width_engineering_warning",
})
_METRIC_FIELDS = _FINAL_METRIC_FIELDS

_TERMINAL_IDENTITY_FIELDS = frozenset({
    "execution_class", "pilot_manifest_id",
    "execution_config_id", "core", "ordinal", "kind", "plan_record_id",
    "mathematical_request_id", "execution_id", "method",
    "taskset_skeleton_slot_id", "taskset_slot_id", "worker_count",
    "selection_key", "generation_request_id", "taskset_skeleton_id",
    "taskset_id", "taskset_hash", "power_vector_hash",
})


class RTA4PilotExecutionError(RuntimeError):
    """Raised when pilot engineering evidence is incomplete or ambiguous."""


class RTA4PilotExecutionInterrupted(RuntimeError):
    """Deterministic test-only interruption after parent persistence."""


@dataclass(frozen=True)
class PilotExecutionSummary:
    execution_class: str
    execution_config_id: str
    processed_count: int
    remaining_count: int
    complete: bool
    checkpoint_path: Path
    audit: Mapping[str, Any] | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plain_positive_int(value: Any, label: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise RTA4PilotExecutionError(f"{label} must be a positive integer")
    return value


def _absolute(value: Any, label: str, *, require_exists: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise RTA4PilotExecutionError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        raise RTA4PilotExecutionError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=require_exists)
    except OSError as exc:
        raise RTA4PilotExecutionError(f"{label} does not exist") from exc
    return str(resolved)


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def build_simulation_support(
    *, base_system_path: Path | str,
    energy_config_path: Path | str,
) -> Dict[str, Any]:
    """Bind the existing simulator wrapper support files without copying them."""

    system = Path(base_system_path).resolve(strict=True)
    energy_path = Path(energy_config_path).resolve(strict=True)
    if not system.is_file() or not energy_path.is_file():
        raise RTA4PilotExecutionError(
            "simulation support paths must identify files"
        )
    try:
        with energy_path.open("r", encoding="utf-8") as handle:
            energy = yaml.safe_load(handle)
    except Exception as exc:
        raise RTA4PilotExecutionError(
            "cannot parse simulator energy configuration"
        ) from exc
    if not isinstance(energy, Mapping):
        raise RTA4PilotExecutionError(
            "simulator energy configuration must be a mapping"
        )
    return {
        "base_system_path": str(system),
        "base_system_sha256": _sha256(system),
        "energy_config_path": str(energy_path),
        "energy_config_sha256": _sha256(energy_path),
        "energy_config": dict(energy),
    }


def _validate_simulation_support(
    support: Any, *, execution_class: str,
) -> Mapping[str, Any] | None:
    if support is None:
        if execution_class == RTA4_PILOT_EXECUTION_CLASS:
            raise RTA4PilotExecutionError(
                "real pilot CORE-3 requires bound simulation support"
            )
        return None
    exact = {
        "base_system_path", "base_system_sha256",
        "energy_config_path", "energy_config_sha256", "energy_config",
    }
    if not isinstance(support, Mapping) or set(support) != exact:
        raise RTA4PilotExecutionError(
            "simulation support has an unexpected field set"
        )
    for prefix in ("base_system", "energy_config"):
        path = Path(_absolute(
            support[f"{prefix}_path"], f"{prefix}_path",
            require_exists=True,
        ))
        if not path.is_file() or _sha256(path) != support[f"{prefix}_sha256"]:
            raise RTA4PilotExecutionError(
                f"{prefix} byte identity drift"
            )
    if not isinstance(support["energy_config"], Mapping):
        raise RTA4PilotExecutionError("energy_config must be a mapping")
    return dict(support)


def build_pilot_execution_config(
    manifest_path: Path | str,
    manifest: Mapping[str, Any], *,
    source_manifest: Mapping[str, Any],
    output_root: Path | str,
    taskset_store: Path | str,
    simulator_manifest: Mapping[str, Any],
    default_worker_count: int,
    max_in_flight: int,
    provisional_rta_attempt_timeout_seconds: int,
    provisional_simulation_timeout_seconds: int,
    memory_soft_limit_bytes: int,
    checkpoint_interval_records: int,
    maximum_attempts: int,
    dependency_manifest: Mapping[str, Any] | None = None,
    environment_manifest: Mapping[str, Any] | None = None,
    hardware_manifest: Mapping[str, Any] | None = None,
    simulation_support: Mapping[str, Any] | None = None,
    execution_class: str = RTA4_PILOT_EXECUTION_CLASS,
) -> Dict[str, Any]:
    """Build an explicit pilot-only operational contract with no defaults."""

    if execution_class not in {
        RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_TEST_EXECUTION_CLASS,
    }:
        raise RTA4PilotExecutionError("unknown pilot execution class")
    path = Path(manifest_path).resolve(strict=True)
    if not path.is_file():
        raise RTA4PilotExecutionError("pilot manifest path must be a file")
    output = Path(output_root).resolve()
    store = Path(taskset_store).resolve()
    if (
        str(output) != manifest.get("output_root")
        or str(store) != manifest.get("taskset_store")
        or output == store
    ):
        raise RTA4PilotExecutionError(
            "execution paths differ from pilot manifest"
        )
    workers = _plain_positive_int(
        default_worker_count, "default_worker_count",
    )
    in_flight = _plain_positive_int(max_in_flight, "max_in_flight")
    if in_flight < workers:
        raise RTA4PilotExecutionError(
            "max_in_flight must cover default_worker_count"
        )
    _plain_positive_int(
        provisional_rta_attempt_timeout_seconds,
        "provisional_rta_attempt_timeout_seconds",
    )
    _plain_positive_int(
        provisional_simulation_timeout_seconds,
        "provisional_simulation_timeout_seconds",
    )
    _plain_positive_int(memory_soft_limit_bytes, "memory_soft_limit_bytes")
    _plain_positive_int(
        checkpoint_interval_records, "checkpoint_interval_records",
    )
    attempts = _plain_positive_int(maximum_attempts, "maximum_attempts")
    if attempts > 2:
        raise RTA4PilotExecutionError(
            "pilot maximum_attempts must not exceed two"
        )
    dependency = dict(
        build_dependency_manifest()
        if dependency_manifest is None else dependency_manifest
    )
    environment = dict(
        build_environment_manifest(dependency)
        if environment_manifest is None else environment_manifest
    )
    hardware = dict(
        build_hardware_manifest()
        if hardware_manifest is None else hardware_manifest
    )
    material = {
        "execution_config_version": RTA4_PILOT_EXECUTION_CONFIG_VERSION,
        "execution_class": execution_class,
        "pilot_manifest": {
            "absolute_path": str(path),
            "file_sha256": _sha256(path),
            "pilot_manifest_id": manifest.get("pilot_manifest_id"),
        },
        "source_manifest": dict(source_manifest),
        "output_root": str(output),
        "taskset_store": str(store),
        "simulator_manifest": dict(simulator_manifest),
        "simulation_support": (
            None if simulation_support is None else dict(simulation_support)
        ),
        "default_worker_count": workers,
        "max_in_flight": in_flight,
        "provisional_rta_attempt_timeout_seconds": (
            provisional_rta_attempt_timeout_seconds
        ),
        "provisional_simulation_timeout_seconds": (
            provisional_simulation_timeout_seconds
        ),
        "memory_soft_limit_bytes": memory_soft_limit_bytes,
        "checkpoint_interval_records": checkpoint_interval_records,
        "maximum_attempts": attempts,
        "runtime_ci_rule_version": RTA4_PILOT_RUNTIME_CI_RULE_VERSION,
        "resume_policy": PILOT_RESUME_POLICY,
        "throughput_definition": PILOT_THROUGHPUT_DEFINITION,
        "output_io_definition": PILOT_OUTPUT_IO_DEFINITION,
        "dependency_manifest": dependency,
        "environment_manifest": environment,
        "hardware_manifest": hardware,
    }
    document = {
        **material,
        "execution_config_id": domain_hash(
            RTA4_PILOT_EXECUTION_CONFIG_DOMAIN, material,
        ),
    }
    return validate_pilot_execution_config(
        document, manifest, validate_live_source=False,
    )


def validate_pilot_execution_config(
    document: Mapping[str, Any], manifest: Mapping[str, Any], *,
    validate_live_source: bool = True,
) -> Dict[str, Any]:
    if not isinstance(document, Mapping) or set(document) != _CONFIG_FIELDS:
        raise RTA4PilotExecutionError(
            "pilot execution config has an unexpected field set"
        )
    if (
        document["execution_config_version"]
        != RTA4_PILOT_EXECUTION_CONFIG_VERSION
        or document["execution_class"] not in {
            RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_TEST_EXECUTION_CLASS,
        }
        or document["runtime_ci_rule_version"]
        != RTA4_PILOT_RUNTIME_CI_RULE_VERSION
        or document["resume_policy"] != PILOT_RESUME_POLICY
        or document["throughput_definition"] != PILOT_THROUGHPUT_DEFINITION
        or document["output_io_definition"] != PILOT_OUTPUT_IO_DEFINITION
    ):
        raise RTA4PilotExecutionError("pilot execution contract mismatch")
    pilot_file = document["pilot_manifest"]
    if not isinstance(pilot_file, Mapping) or set(pilot_file) != {
        "absolute_path", "file_sha256", "pilot_manifest_id",
    }:
        raise RTA4PilotExecutionError("pilot manifest binding is incomplete")
    path = Path(_absolute(
        pilot_file["absolute_path"], "pilot manifest path",
        require_exists=True,
    ))
    if path != (
        Path(document["output_root"]) / RTA4_PILOT_OUTPUT_MARKER
    ).resolve():
        raise RTA4PilotExecutionError(
            "pilot manifest must be the canonical output-root marker"
        )
    if (
        _sha256(path) != pilot_file["file_sha256"]
        or pilot_file["pilot_manifest_id"] != manifest.get(
            "pilot_manifest_id"
        )
    ):
        raise RTA4PilotExecutionError("pilot manifest binding drift")
    try:
        observed_manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RTA4PilotExecutionError("cannot read bound pilot manifest") from exc
    if observed_manifest != dict(manifest):
        raise RTA4PilotExecutionError("pilot manifest bytes/material mismatch")
    if (
        document["output_root"] != manifest.get("output_root")
        or document["taskset_store"] != manifest.get("taskset_store")
        or document["output_root"] == document["taskset_store"]
    ):
        raise RTA4PilotExecutionError(
            "pilot execution paths do not match selection manifest"
        )
    for field in (
        "default_worker_count", "max_in_flight",
        "provisional_rta_attempt_timeout_seconds",
        "provisional_simulation_timeout_seconds", "memory_soft_limit_bytes",
        "checkpoint_interval_records", "maximum_attempts",
    ):
        _plain_positive_int(document[field], field)
    if (
        document["max_in_flight"] < document["default_worker_count"]
        or document["maximum_attempts"] > 2
    ):
        raise RTA4PilotExecutionError("pilot execution bounds are invalid")
    source = validate_identity_manifest(
        document["source_manifest"],
        version=RTA4_SOURCE_MANIFEST_VERSION,
        domain=RTA4_SOURCE_DOMAIN,
    )
    if validate_live_source:
        require_clean = (
            document["execution_class"] == RTA4_PILOT_EXECUTION_CLASS
        )
        try:
            validate_source_manifest(source, require_clean=require_clean)
        except Exception as exc:
            raise RTA4PilotExecutionError(
                "pilot source commit/tree/file binding drift"
            ) from exc
    dependency = validate_identity_manifest(
        document["dependency_manifest"],
        version=RTA4_DEPENDENCY_MANIFEST_VERSION,
        domain=RTA4_DEPENDENCY_DOMAIN,
    )
    environment = validate_identity_manifest(
        document["environment_manifest"],
        version=RTA4_ENVIRONMENT_MANIFEST_VERSION,
        domain=RTA4_ENVIRONMENT_DOMAIN,
    )
    hardware = validate_identity_manifest(
        document["hardware_manifest"],
        version=RTA4_HARDWARE_MANIFEST_VERSION,
        domain=RTA4_HARDWARE_DOMAIN,
    )
    if environment.get("dependency_manifest_id") != dependency["manifest_id"]:
        raise RTA4PilotExecutionError(
            "pilot environment/dependency binding mismatch"
        )
    if validate_live_source:
        try:
            live_dependency = build_dependency_manifest(tuple(
                row["distribution"]
                for row in dependency["dependencies"]
            ))
            live_environment = build_environment_manifest(live_dependency)
            live_hardware = build_hardware_manifest()
        except Exception as exc:
            raise RTA4PilotExecutionError(
                "cannot reconstruct live pilot runtime identity"
            ) from exc
        if live_dependency != dependency:
            raise RTA4PilotExecutionError(
                "pilot dependency environment drift"
            )
        if live_environment != environment:
            raise RTA4PilotExecutionError(
                "pilot runtime environment drift"
            )
        if live_hardware != hardware:
            raise RTA4PilotExecutionError(
                "pilot hardware environment drift"
            )
    simulator = validate_identity_manifest(
        document["simulator_manifest"],
        version=RTA4_SIMULATOR_MANIFEST_VERSION,
        domain=RTA4_SIMULATOR_DOMAIN,
    )
    if (
        not simulator.get("required")
        or not simulator.get("executable")
        or not isinstance(simulator.get("absolute_path"), str)
        or not isinstance(simulator.get("sha256"), str)
    ):
        raise RTA4PilotExecutionError(
            "CORE-3 pilot selection requires an executable simulator binding"
        )
    simulator_path = Path(_absolute(
        simulator["absolute_path"], "simulator binary",
        require_exists=True,
    ))
    if (
        not simulator_path.is_file()
        or _sha256(simulator_path) != simulator["sha256"]
        or simulator_path.stat().st_size != simulator["size_bytes"]
        or not os.access(simulator_path, os.X_OK)
    ):
        raise RTA4PilotExecutionError("simulator binary identity drift")
    support = _validate_simulation_support(
        document["simulation_support"],
        execution_class=document["execution_class"],
    )
    if support is not None:
        for field in ("base_system_path", "energy_config_path"):
            try:
                validate_bound_source_file(source, support[field])
            except Exception as exc:
                raise RTA4PilotExecutionError(
                    "simulation support is outside the source closure"
                ) from exc
    material = dict(document)
    observed = material.pop("execution_config_id")
    if observed != domain_hash(RTA4_PILOT_EXECUTION_CONFIG_DOMAIN, material):
        raise RTA4PilotExecutionError("pilot execution config ID mismatch")
    return dict(document)


def _selected_rows(manifest: Mapping[str, Any]) -> tuple[Dict[str, Any], ...]:
    rows = []
    for core in RTA4_CORES:
        for row in manifest["selected_records"][core]:
            rows.append({**dict(row), "core": core})
    return tuple(rows)


@lru_cache(maxsize=96)
def _trusted_records_at_ordinals(
    core: str, ordinals: tuple[int, ...],
) -> tuple[FormalPlanRecord, ...]:
    pending = set(ordinals)
    records = []
    for record in iter_formal_plan(default_rta4_formal_config(core)):
        if record.ordinal in pending:
            records.append(record)
            pending.remove(record.ordinal)
            if not pending:
                break
    if pending:
        raise RTA4PilotExecutionError(
            "selected ordinal is absent from trusted plan"
        )
    return tuple(records)


def reconstruct_selected_records(
    configs: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> tuple[FormalPlanRecord, ...]:
    """Reconstruct exact selected plan rows without sampling or adaptation."""

    if set(configs) != set(RTA4_CORES):
        raise RTA4PilotExecutionError("all six configs are required")
    records = []
    for core in RTA4_CORES:
        validate_rta4_formal_config(configs[core], expected_core=core)
        selected = manifest["selected_records"][core]
        by_ordinal = {int(row["ordinal"]): row for row in selected}
        if len(by_ordinal) != len(selected):
            raise RTA4PilotExecutionError(
                "pilot selection contains duplicate ordinals"
            )
        pending = set(by_ordinal)
        for record in _trusted_records_at_ordinals(
            core, tuple(sorted(pending)),
        ):
            row = by_ordinal[record.ordinal]
            expected = {
                "ordinal": record.ordinal,
                "plan_record_id": record.record_id,
                "kind": record.kind,
                "mathematical_request_id": record.mathematical_request_id,
                "execution_id": record.execution_id,
                "method": str(record.material.get("method", "NA")),
                "taskset_skeleton_slot_id": (
                    record.taskset_skeleton_slot_id
                ),
                "taskset_slot_id": record.taskset_slot_id,
                "worker_count": int(record.material.get("worker_count", 1)),
                "selection_key": row["selection_key"],
            }
            if row != expected:
                raise RTA4PilotExecutionError(
                    "selected record differs from trusted plan"
                )
            records.append(record)
            pending.remove(record.ordinal)
        if pending:
            raise RTA4PilotExecutionError(
                "selected ordinal is absent from trusted plan"
            )
    return tuple(records)


def build_pilot_execution_manifest(
    manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
) -> Dict[str, Any]:
    rows = _selected_rows(manifest)
    material = {
        "execution_manifest_version": (
            RTA4_PILOT_EXECUTION_MANIFEST_VERSION
        ),
        "execution_class": execution_config["execution_class"],
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "execution_config_id": execution_config["execution_config_id"],
        "planned_record_count": len(rows),
        "ordered_plan_record_ids": [
            row["plan_record_id"] for row in rows
        ],
        "ordered_execution_ids": [row["execution_id"] for row in rows],
        "selection_identity_sha256": hashlib.sha256(
            canonical_json(rows).encode("utf-8")
        ).hexdigest(),
        "parent_persistence": True,
        "deterministic_submission_order": "RTA4_CORES_THEN_MANIFEST_ORDER",
        "deterministic_persistence_order": "SUBMISSION_ORDER",
    }
    return {
        **material,
        "execution_manifest_id": domain_hash(
            RTA4_PILOT_EXECUTION_MANIFEST_DOMAIN, material,
        ),
    }


def validate_pilot_execution_manifest(
    document: Mapping[str, Any], manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = build_pilot_execution_manifest(manifest, execution_config)
    if dict(document) != expected:
        raise RTA4PilotExecutionError(
            "pilot execution manifest cannot be reconstructed"
        )
    return expected


class PilotTasksetProvider:
    """Strict pilot wrapper around the production provider generation logic."""

    def __init__(
        self, configs: Mapping[str, Mapping[str, Any]], *,
        generator_factory: Callable[..., Any] | None = None,
    ) -> None:
        from .rta4_formal_execution import ProductionTasksetProvider

        class SharedProvider(ProductionTasksetProvider):
            def __init__(
                shared_self,
                normalized: Mapping[str, Mapping[str, Any]],
                factory: Callable[..., Any] | None,
            ) -> None:
                shared_self.prepared = None
                shared_self.config = normalized["CORE-1"]
                shared_self.core = "PILOT_SHARED"
                shared_self.sources = {}
                shared_self._generator_factory = factory
                shared_self._tasksets = {}
                shared_self._skeletons = {}
                shared_self._source_slot_index = {}
                shared_self._pilot_configs = normalized

            def _source_certificate(
                shared_self, record: FormalPlanRecord,
            ) -> None:
                return None

            def _generation_request(
                shared_self, record: FormalPlanRecord,
            ) -> Any:
                shared_self.config = shared_self._pilot_configs[record.core]
                return super(SharedProvider, shared_self)._generation_request(
                    record
                )

        normalized = {
            core: validate_rta4_formal_config(
                configs[core], expected_core=core,
            )
            for core in RTA4_CORES
        }
        self._provider = SharedProvider(normalized, generator_factory)

    def __call__(
        self, record: FormalPlanRecord,
    ) -> TasksetIdentityCertificate:
        return self._provider(record)

    def hydrate(
        self, taskset_slot_id: str,
        certificate: TasksetIdentityCertificate,
    ) -> None:
        if type(certificate) is not TasksetIdentityCertificate:
            raise RTA4PilotExecutionError(
                "pilot store hydration requires a PR-B certificate"
            )
        certificate.validate()
        slot = str(taskset_slot_id)
        existing = self._provider._tasksets.get(slot)
        if existing is not None and existing.canonical_bytes() != (
            certificate.canonical_bytes()
        ):
            raise RTA4PilotExecutionError(
                "pilot store hydration conflicts with cached slot"
            )
        self._provider._tasksets[slot] = certificate


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if os.uname().sysname == "Darwin" else value * 1024)


def _nonnegative_milliseconds(elapsed_ns: int) -> int:
    if type(elapsed_ns) is not int or elapsed_ns < 0:
        raise RTA4PilotExecutionError(
            "clock duration must be a non-negative integer"
        )
    return elapsed_ns // 1_000_000


def _worker_execute(
    record: FormalPlanRecord,
    certificate: TasksetIdentityCertificate,
    config: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    callback: Callable[..., Mapping[str, Any]] | None,
    worker_temp_root: str | None,
) -> Dict[str, Any]:
    """Worker-only computation; no terminal/certificate/checkpoint writes."""

    wall_started = time.monotonic_ns()
    cpu_started = time.process_time_ns()
    peak_before = _rss_bytes()
    attempts = 0
    timed_out = False
    engineering_error = False
    trace_payload: bytes | None = None
    simulation_wall = 0
    simulation_id: str | None = None
    overrides: Mapping[str, Any] = {}
    worker_trace_root: Path | None = None
    try:
        if record.kind == "simulation":
            attempts = 1
            from .rta4_formal_pipeline import build_formal_release_projection
            from .release_applicability import (
                TARGET_SCHEDULER, simulation_applicability_identity,
            )
            from .rta4_formal_pipeline import formal_service_identity

            projection, window, payload = build_formal_release_projection(
                certificate, record.material["release_mode"],
            )
            simulation_id = simulation_applicability_identity(
                taskset_id=certificate.taskset_id,
                release_projection_id=projection.release_projection_id,
                scheduler=TARGET_SCHEDULER,
                service_identity=formal_service_identity(
                    record.material["service_scale"]
                ),
                initial_battery=record.material["physical_initial_energy"],
                battery_capacity=record.material["battery_capacity"],
                window=window,
                applicability_track=record.material["applicability_track"],
            )
            sim_started = time.monotonic_ns()
            if callback is not None:
                result = callback(
                    record, certificate, projection, window, payload,
                    simulation_id,
                )
            else:
                from .rta4_formal_execution import (
                    ProductionSimulationExecutor,
                )
                support = execution_config["simulation_support"]
                if worker_temp_root is None:
                    raise RTA4PilotExecutionError(
                        "real simulation worker lacks a parent-owned temp root"
                    )
                worker_trace_root = Path(worker_temp_root)
                result = ProductionSimulationExecutor.execute_bound(
                    simulator_binary=execution_config[
                        "simulator_manifest"
                    ]["absolute_path"],
                    simulation_timeout_seconds=execution_config[
                        "provisional_simulation_timeout_seconds"
                    ],
                    output_root=worker_trace_root,
                    base_system_path=support["base_system_path"],
                    energy_config_path=support["energy_config_path"],
                    energy_config=support["energy_config"],
                    record=record, certificate=certificate,
                    projection=projection, window=window, payload=payload,
                    simulation_id=simulation_id,
                )
            simulation_wall = _nonnegative_milliseconds(
                time.monotonic_ns() - sim_started
            )
            trace = result.get("trace_path") if isinstance(result, Mapping) else None
            if trace is None:
                engineering_error = True
            else:
                trace_path = Path(trace).resolve(strict=True)
                trace_payload = trace_path.read_bytes()
                try:
                    json.loads(
                        trace_payload.decode("utf-8"),
                        parse_constant=lambda value: (
                            (_ for _ in ()).throw(
                                ValueError(f"non-finite JSON: {value}")
                            )
                        ),
                    )
                except Exception as exc:
                    raise RTA4PilotExecutionError(
                        "simulator trace is not strict UTF-8 JSON"
                    ) from exc
            if isinstance(result, Mapping):
                overrides = result.get("__pilot_metric_overrides__", {})
        else:
            last_status = "INTERNAL_ERROR"
            for _ in range(execution_config["maximum_attempts"]):
                attempts += 1
                if callback is not None:
                    result = callback(record, certificate)
                    if not isinstance(result, Mapping):
                        raise RTA4PilotExecutionError(
                            "test RTA callback must return a mapping"
                        )
                    last_status = str(result.get(
                        "solver_status", "COMPLETED",
                    ))
                    overrides = result.get(
                        "__pilot_metric_overrides__", {},
                    )
                else:
                    from .rta4_formal_execution import _adapter_result
                    mapped, _raw = _adapter_result(
                        record, certificate, config,
                        execution_config[
                            "provisional_rta_attempt_timeout_seconds"
                        ],
                    )
                    last_status = str(mapped["solver_status"])
                if last_status != "TIMEOUT":
                    break
            timed_out = last_status == "TIMEOUT"
            engineering_error = last_status in {
                "NUMERIC_ERROR", "INTERNAL_ERROR",
                "INTERNAL_CONFORMANCE_FAILURE",
            }
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        engineering_error = True
        trace_payload = None
    runtime_wall = _nonnegative_milliseconds(
        time.monotonic_ns() - wall_started
    )
    runtime_cpu = _nonnegative_milliseconds(
        time.process_time_ns() - cpu_started
    )
    peak = max(peak_before, _rss_bytes())
    if peak > execution_config["memory_soft_limit_bytes"]:
        engineering_error = True
    metrics = {
        "runtime_wall_milliseconds": runtime_wall,
        "runtime_cpu_milliseconds": runtime_cpu,
        "peak_rss_bytes": peak,
        "timed_out": timed_out,
        "attempt_count": attempts,
        "worker_throughput_milli_records_per_second": 0,
        "simulation_wall_milliseconds": simulation_wall,
        "trace_size_bytes": 0,
        "engineering_error": engineering_error,
    }
    if (
        execution_config["execution_class"]
        == RTA4_PILOT_TEST_EXECUTION_CLASS
        and isinstance(overrides, Mapping)
    ):
        for name, value in overrides.items():
            if name not in _RAW_METRIC_FIELDS:
                raise RTA4PilotExecutionError(
                    "test metric override contains an unknown field"
                )
            metrics[name] = value
    return {
        "plan_record_id": record.record_id,
        "execution_id": record.execution_id,
        "taskset_id": certificate.taskset_id,
        "simulation_id": simulation_id,
        "trace_payload": trace_payload,
        "metrics": metrics,
    }


def _execution_batches(
    records: Sequence[FormalPlanRecord], *, max_in_flight: int,
    default_workers: int,
) -> Iterable[tuple[int, Sequence[FormalPlanRecord]]]:
    """Preserve manifest order while honoring CORE-5B worker conditions."""

    start = 0
    while start < len(records):
        first = records[start]
        if first.core == "CORE-5B":
            workers = int(first.material["worker_count"])
            end = start
            while (
                end < len(records)
                and end - start < max_in_flight
                and records[end].core == "CORE-5B"
                and int(records[end].material["worker_count"]) == workers
            ):
                end += 1
        else:
            workers = default_workers
            end = start
            while (
                end < len(records)
                and end - start < max_in_flight
                and records[end].core == first.core
            ):
                end += 1
        yield workers, records[start:end]
        start = end


def _parent_worker_failure(
    record: FormalPlanRecord,
    certificate: TasksetIdentityCertificate,
    elapsed_ns: int,
) -> Dict[str, Any]:
    """Canonicalize process transport/crash failures as engineering evidence."""

    return {
        "plan_record_id": record.record_id,
        "execution_id": record.execution_id,
        "taskset_id": certificate.taskset_id,
        "trace_payload": None,
        "metrics": {
            "runtime_wall_milliseconds": _nonnegative_milliseconds(
                elapsed_ns
            ),
            "runtime_cpu_milliseconds": 0,
            "peak_rss_bytes": 0,
            "timed_out": False,
            "attempt_count": 0,
            "worker_throughput_milli_records_per_second": 0,
            "simulation_wall_milliseconds": 0,
            "trace_size_bytes": 0,
            "engineering_error": True,
        },
        "simulation_id": None,
    }


def _validate_metrics(
    metrics: Mapping[str, Any], *,
    final: bool = True,
) -> Dict[str, Any]:
    fields = _FINAL_METRIC_FIELDS if final else _RAW_METRIC_FIELDS
    if not isinstance(metrics, Mapping) or set(metrics) != fields:
        raise RTA4PilotExecutionError(
            "pilot worker metrics have an unexpected field set"
        )
    for field in (
        "runtime_wall_milliseconds", "runtime_cpu_milliseconds",
        "peak_rss_bytes", "attempt_count",
        "worker_throughput_milli_records_per_second",
        "simulation_wall_milliseconds", "trace_size_bytes",
        *(
            (
                "checkpoint_overhead_milliseconds",
                "resume_overhead_milliseconds", "output_io_bytes",
            )
            if final else ()
        ),
    ):
        if type(metrics[field]) is not int or metrics[field] < 0:
            raise RTA4PilotExecutionError(
                "pilot metrics must be non-negative plain integers"
            )
    for field in (
        "timed_out", "engineering_error",
        *(("ci_width_engineering_warning",) if final else ()),
    ):
        if type(metrics[field]) is not bool:
            raise RTA4PilotExecutionError(
                "pilot flags must be strict booleans"
            )
    return dict(metrics)


PILOT_TRACE_PARSER_IDENTITY = domain_hash(
    "ASAP_BLOCK:V9.3:RTA4_PILOT_TRACE_PARSER:v1", {
        "parser": "release_applicability.parse_release_trace",
        "trace_schema_version": 2,
        "simulator_trace_contract_version": (
            "ASAP_BLOCK_V9_3_RELEASE_CUTOFF_TRACE_V2"
        ),
    },
)
PILOT_TRACE_COMPLETENESS_IDENTITY = domain_hash(
    "ASAP_BLOCK:V9.3:RTA4_PILOT_TRACE_COMPLETENESS:v1", {
        "simulation_completed": True,
        "completion_reason": "reached_horizon",
        "release_cutoff": True,
        "observation_horizon_reached": True,
    },
)


def _terminal_identity(
    selected: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    certificate: TasksetIdentityCertificate,
) -> Dict[str, Any]:
    return {
        "execution_class": execution_config["execution_class"],
        "pilot_manifest_id": execution_config["pilot_manifest"][
            "pilot_manifest_id"
        ],
        "execution_config_id": execution_config["execution_config_id"],
        "core": selected["core"],
        "ordinal": selected["ordinal"],
        "kind": selected["kind"],
        "plan_record_id": selected["plan_record_id"],
        "mathematical_request_id": selected["mathematical_request_id"],
        "execution_id": selected["execution_id"],
        "method": selected["method"],
        "taskset_skeleton_slot_id": selected[
            "taskset_skeleton_slot_id"
        ],
        "taskset_slot_id": selected["taskset_slot_id"],
        "worker_count": selected["worker_count"],
        "selection_key": selected["selection_key"],
        "generation_request_id": certificate.generation_request_id,
        "taskset_skeleton_id": certificate.taskset_skeleton_id,
        "taskset_id": certificate.taskset_id,
        "taskset_hash": certificate.taskset_hash,
        "power_vector_hash": certificate.power_vector_hash,
    }


def _trace_binding(
    *, execution_id: str, kind: str, trace_size_bytes: int,
    trace_sha256: str | None, simulation_id: str | None,
) -> Dict[str, Any]:
    if kind == "simulation":
        if (
            not isinstance(simulation_id, str)
            or len(simulation_id) != 64
        ):
            raise RTA4PilotExecutionError(
                "simulation raw evidence lacks a complete trace binding"
            )
        if trace_size_bytes == 0 and trace_sha256 is None:
            filename = None
        elif (
            trace_size_bytes > 0
            and isinstance(trace_sha256, str)
            and len(trace_sha256) == 64
        ):
            filename = f"{execution_id}.json"
        else:
            raise RTA4PilotExecutionError(
                "simulation trace size/hash binding is inconsistent"
            )
        return {
            "trace_filename": filename,
            "trace_sha256": trace_sha256,
            "trace_schema_version": 2,
            "simulation_id": simulation_id,
            "trace_parser_identity": PILOT_TRACE_PARSER_IDENTITY,
            "trace_completeness_identity": (
                PILOT_TRACE_COMPLETENESS_IDENTITY
            ),
        }
    if (
        trace_size_bytes != 0
        or trace_sha256 is not None
        or simulation_id is not None
    ):
        raise RTA4PilotExecutionError(
            "RTA raw evidence must not bind a simulator trace"
        )
    return {
        "trace_filename": None,
        "trace_sha256": None,
        "trace_schema_version": None,
        "simulation_id": None,
        "trace_parser_identity": None,
        "trace_completeness_identity": None,
    }


def build_pilot_raw_terminal(
    selected: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    certificate: TasksetIdentityCertificate,
    metrics: Mapping[str, Any], *,
    trace_sha256: str | None = None,
    simulation_id: str | None = None,
) -> Dict[str, Any]:
    normalized = _validate_metrics(metrics, final=False)
    material = {
        "raw_terminal_version": RTA4_PILOT_RAW_TERMINAL_VERSION,
        **_terminal_identity(selected, execution_config, certificate),
        **_trace_binding(
            execution_id=str(selected["execution_id"]),
            kind=str(selected["kind"]),
            trace_size_bytes=normalized["trace_size_bytes"],
            trace_sha256=trace_sha256,
            simulation_id=simulation_id,
        ),
        **normalized,
    }
    return {
        **material,
        "raw_terminal_sha256": domain_hash(
            RTA4_PILOT_RAW_TERMINAL_DOMAIN, material,
        ),
    }


def validate_pilot_raw_terminal(
    document: Mapping[str, Any], selected: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    certificate: TasksetIdentityCertificate,
) -> Dict[str, Any]:
    expected_identity = _terminal_identity(
        selected, execution_config, certificate,
    )
    if not isinstance(document, Mapping):
        raise RTA4PilotExecutionError("pilot raw terminal must be a mapping")
    for field, value in expected_identity.items():
        if document.get(field) != value:
            raise RTA4PilotExecutionError(
                "pilot raw terminal identity mismatch"
            )
    if document.get("raw_terminal_version") != (
        RTA4_PILOT_RAW_TERMINAL_VERSION
    ):
        raise RTA4PilotExecutionError("pilot raw terminal version mismatch")
    metrics = {
        field: document.get(field) for field in _RAW_METRIC_FIELDS
    }
    _validate_metrics(metrics, final=False)
    trace = _trace_binding(
        execution_id=str(selected["execution_id"]),
        kind=str(selected["kind"]),
        trace_size_bytes=metrics["trace_size_bytes"],
        trace_sha256=document.get("trace_sha256"),
        simulation_id=document.get("simulation_id"),
    )
    if any(document.get(field) != value for field, value in trace.items()):
        raise RTA4PilotExecutionError(
            "pilot raw terminal trace binding mismatch"
        )
    expected_fields = {
        "raw_terminal_version", "raw_terminal_sha256",
        *_TERMINAL_IDENTITY_FIELDS, *_RAW_METRIC_FIELDS, *trace,
    }
    if set(document) != expected_fields:
        raise RTA4PilotExecutionError(
            "pilot raw terminal has an unexpected field set"
        )
    material = dict(document)
    observed = material.pop("raw_terminal_sha256")
    if observed != domain_hash(RTA4_PILOT_RAW_TERMINAL_DOMAIN, material):
        raise RTA4PilotExecutionError("pilot raw terminal hash mismatch")
    return dict(document)


def pilot_final_terminal_preimage(
    raw_terminal: Mapping[str, Any], *,
    checkpoint_overhead_milliseconds: int,
    resume_overhead_milliseconds: int,
    ci_width_engineering_warning: bool,
) -> Dict[str, Any]:
    for value, label in (
        (checkpoint_overhead_milliseconds, "checkpoint overhead"),
        (resume_overhead_milliseconds, "resume overhead"),
    ):
        if type(value) is not int or value < 0:
            raise RTA4PilotExecutionError(
                f"{label} must be a non-negative plain integer"
            )
    if type(ci_width_engineering_warning) is not bool:
        raise RTA4PilotExecutionError(
            "runtime CI warning must be a strict boolean"
        )
    return {
        "final_terminal_version": RTA4_PILOT_FINAL_TERMINAL_VERSION,
        "raw_terminal_sha256": raw_terminal["raw_terminal_sha256"],
        **{
            field: raw_terminal[field]
            for field in (
                *_TERMINAL_IDENTITY_FIELDS,
                "trace_filename", "trace_sha256", "trace_schema_version",
                "simulation_id", "trace_parser_identity",
                "trace_completeness_identity", *_RAW_METRIC_FIELDS,
            )
        },
        "checkpoint_overhead_milliseconds": (
            checkpoint_overhead_milliseconds
        ),
        "resume_overhead_milliseconds": resume_overhead_milliseconds,
        "ci_width_engineering_warning": ci_width_engineering_warning,
        "output_io_definition": PILOT_OUTPUT_IO_DEFINITION,
    }


def compute_pilot_output_io_bytes(
    final_terminal_preimage: Mapping[str, Any],
    trace_size_bytes: int,
) -> int:
    if type(trace_size_bytes) is not int or trace_size_bytes < 0:
        raise RTA4PilotExecutionError(
            "trace size must be a non-negative plain integer"
        )
    if (
        "output_io_bytes" in final_terminal_preimage
        or "final_terminal_sha256" in final_terminal_preimage
    ):
        raise RTA4PilotExecutionError(
            "final terminal preimage contains a self-referential field"
        )
    return len(
        canonical_json(dict(final_terminal_preimage)).encode("utf-8")
    ) + trace_size_bytes


def build_pilot_final_terminal(
    raw_terminal: Mapping[str, Any], *,
    checkpoint_overhead_milliseconds: int,
    resume_overhead_milliseconds: int,
    ci_width_engineering_warning: bool,
) -> Dict[str, Any]:
    preimage = pilot_final_terminal_preimage(
        raw_terminal,
        checkpoint_overhead_milliseconds=(
            checkpoint_overhead_milliseconds
        ),
        resume_overhead_milliseconds=resume_overhead_milliseconds,
        ci_width_engineering_warning=ci_width_engineering_warning,
    )
    material = {
        **preimage,
        "output_io_bytes": compute_pilot_output_io_bytes(
            preimage, int(raw_terminal["trace_size_bytes"]),
        ),
    }
    return {
        **material,
        "final_terminal_sha256": domain_hash(
            RTA4_PILOT_FINAL_TERMINAL_DOMAIN, material,
        ),
    }


def validate_pilot_final_terminal(
    document: Mapping[str, Any],
    raw_terminal: Mapping[str, Any], *,
    checkpoint_overhead_milliseconds: int,
    resume_overhead_milliseconds: int,
    ci_width_engineering_warning: bool,
) -> Dict[str, Any]:
    expected = build_pilot_final_terminal(
        raw_terminal,
        checkpoint_overhead_milliseconds=(
            checkpoint_overhead_milliseconds
        ),
        resume_overhead_milliseconds=resume_overhead_milliseconds,
        ci_width_engineering_warning=ci_width_engineering_warning,
    )
    if dict(document) != expected:
        raise RTA4PilotExecutionError(
            "pilot final terminal cannot be reconstructed"
        )
    return expected


def build_pilot_terminal(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Compatibility name for the immutable V2 final-terminal constructor."""

    return build_pilot_final_terminal(*args, **kwargs)


def validate_pilot_terminal(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Compatibility name for the immutable V2 final-terminal validator."""

    return validate_pilot_final_terminal(*args, **kwargs)


def _digest_map(
    documents: Sequence[Mapping[str, Any]], *,
    id_field: str, digest_field: str,
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for document in documents:
        identity = str(document[id_field])
        digest = str(document[digest_field])
        if identity in result or len(identity) != 64 or len(digest) != 64:
            raise RTA4PilotExecutionError(
                "checkpoint inventory identity is invalid or duplicated"
            )
        result[identity] = digest
    return {key: result[key] for key in sorted(result)}


def build_pilot_checkpoint(
    manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    execution_manifest: Mapping[str, Any], *,
    store_manifest: Mapping[str, Any],
    raw_terminals: Sequence[Mapping[str, Any]],
    checkpoint_events: Sequence[Mapping[str, Any]],
    resume_events: Sequence[Mapping[str, Any]],
    final_terminals: Sequence[Mapping[str, Any]],
    trace_digests: Mapping[str, str],
    phase: str,
    generation: int,
) -> Dict[str, Any]:
    if phase not in {
        "PREPARING_STORE", "EXECUTING", "FINALIZING", "PILOT_COMPLETE",
    }:
        raise RTA4PilotExecutionError("unknown pilot checkpoint phase")
    _plain_positive_int(generation, "checkpoint generation")
    raw_map = _digest_map(
        raw_terminals, id_field="execution_id",
        digest_field="raw_terminal_sha256",
    )
    final_map = _digest_map(
        final_terminals, id_field="execution_id",
        digest_field="final_terminal_sha256",
    )
    event_map = _digest_map(
        checkpoint_events, id_field="checkpoint_event_id",
        digest_field="checkpoint_event_sha256",
    )
    resume_map = _digest_map(
        resume_events, id_field="resume_event_id",
        digest_field="resume_event_sha256",
    )
    traces = {str(key): str(value) for key, value in trace_digests.items()}
    planned = execution_manifest["planned_record_count"]
    if (
        len(raw_map) > planned
        or not set(final_map).issubset(raw_map)
        or not set(traces).issubset(raw_map)
    ):
        raise RTA4PilotExecutionError("invalid checkpoint evidence inventory")
    if phase == "PILOT_COMPLETE" and (
        len(raw_map) != planned or len(final_map) != planned
    ):
        raise RTA4PilotExecutionError(
            "complete checkpoint lacks complete raw/final evidence"
        )
    material = {
        "checkpoint_version": RTA4_PILOT_CHECKPOINT_VERSION,
        "checkpoint_generation": generation,
        "phase": phase,
        "state": (
            "PILOT_COMPLETE"
            if phase == "PILOT_COMPLETE" else "INCOMPLETE_PILOT"
        ),
        "execution_class": execution_config["execution_class"],
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "execution_config_id": execution_config["execution_config_id"],
        "execution_manifest_id": execution_manifest[
            "execution_manifest_id"
        ],
        "store_manifest_id": store_manifest["store_manifest_id"],
        "planned_record_count": planned,
        "completed_raw_count": len(raw_map),
        "completed_raw_terminal_digests": raw_map,
        "completed_raw_ordered_digest": hashlib.sha256(
            canonical_json(raw_map).encode("utf-8")
        ).hexdigest(),
        "checkpoint_event_digests": event_map,
        "checkpoint_event_ordered_digest": hashlib.sha256(
            canonical_json(event_map).encode("utf-8")
        ).hexdigest(),
        "resume_event_digests": resume_map,
        "resume_event_ordered_digest": hashlib.sha256(
            canonical_json(resume_map).encode("utf-8")
        ).hexdigest(),
        "final_terminal_digests": final_map,
        "final_terminal_ordered_digest": hashlib.sha256(
            canonical_json(final_map).encode("utf-8")
        ).hexdigest(),
        "trace_digests": {
            key: traces[key] for key in sorted(traces)
        },
        "trace_ordered_digest": hashlib.sha256(
            canonical_json({
                key: traces[key] for key in sorted(traces)
            }).encode("utf-8")
        ).hexdigest(),
    }
    return {
        **material,
        "checkpoint_id": domain_hash(
            RTA4_PILOT_CHECKPOINT_DOMAIN, material,
        ),
    }


def validate_pilot_checkpoint(
    document: Mapping[str, Any], manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    execution_manifest: Mapping[str, Any], **inventory: Any,
) -> Dict[str, Any]:
    expected = build_pilot_checkpoint(
        manifest, execution_config, execution_manifest, **inventory,
    )
    if dict(document) != expected:
        raise RTA4PilotExecutionError(
            "pilot checkpoint evidence inventory mismatch"
        )
    return expected


def _median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[(len(ordered) - 1) // 2]


def _quantile(values: Sequence[int], numerator: int, denominator: int) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = ((len(ordered) - 1) * numerator + denominator - 1) // denominator
    return ordered[index]


def runtime_ci_engineering_warnings(
    pilot_manifest_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> Dict[str, bool]:
    """Apply the frozen runtime-only deterministic bootstrap warning rule."""

    groups: Dict[tuple[str, str, int], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["core"]), str(row["method"]), int(row["worker_count"]),
        )
        groups.setdefault(key, []).append(row)
    result: Dict[str, bool] = {}
    for key in sorted(groups):
        group = groups[key]
        runtimes = sorted(
            int(row["runtime_wall_milliseconds"]) for row in group
        )
        if len(runtimes) < 8:
            warning = True
        else:
            seed = int(domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_PILOT_RUNTIME_CI_SEED:v1",
                {
                    "pilot_manifest_id": pilot_manifest_id,
                    "core": key[0], "method": key[1],
                    "worker_count": key[2],
                },
            ), 16)
            generator = random.Random(seed)
            medians = []
            for _ in range(1000):
                sample = [
                    runtimes[generator.randrange(len(runtimes))]
                    for _ in runtimes
                ]
                medians.append(_median(sample))
            lower = _quantile(medians, 1, 40)
            upper = _quantile(medians, 39, 40)
            center = max(1, _median(runtimes))
            warning = (upper - lower) * 4 > center
        for row in group:
            result[str(row["plan_record_id"])] = warning
    return result


def _terminal_observation_input(
    terminal: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "plan_record_id": terminal["plan_record_id"],
        "mathematical_request_id": terminal["mathematical_request_id"],
        "execution_id": terminal["execution_id"],
        "worker_count": terminal["worker_count"],
        **{field: terminal[field] for field in _METRIC_FIELDS},
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON: {value}")
            ),
        )
    except Exception as exc:
        raise RTA4PilotExecutionError(f"cannot read {path.name}") from exc


def _evidence_paths(
    root: Path, directory: str, *,
    stem_length: int = 64,
) -> tuple[Path, ...]:
    evidence_root = root / directory
    if not evidence_root.is_dir():
        return ()
    entries = tuple(sorted(evidence_root.iterdir()))
    if any(
        not path.is_file()
        or path.is_symlink()
        or path.suffix != ".json"
        or len(path.stem) != stem_length
        for path in entries
    ):
        raise RTA4PilotExecutionError(
            f"{directory} contains an unexpected entry"
        )
    return entries


def _terminal_paths(root: Path) -> tuple[Path, ...]:
    return _evidence_paths(root, RTA4_PILOT_FINAL_TERMINAL_DIRECTORY)


def _write_json_once(path: Path, document: Mapping[str, Any]) -> None:
    expected = _canonical_json_bytes(document)
    if path.exists():
        if not path.is_file() or path.read_bytes() != expected:
            raise RTA4PilotExecutionError(
                f"immutable evidence conflict: {path.name}"
            )
        return
    atomic_write_json(path, document)
    if path.read_bytes() != expected:
        raise RTA4PilotExecutionError(
            f"immutable evidence write verification failed: {path.name}"
        )


def _write_text_once(path: Path, text: str) -> None:
    expected = text.encode("utf-8")
    if path.exists():
        if not path.is_file() or path.read_bytes() != expected:
            raise RTA4PilotExecutionError(
                f"immutable evidence conflict: {path.name}"
            )
        return
    atomic_write_text(path, text)
    if path.read_bytes() != expected:
        raise RTA4PilotExecutionError(
            f"immutable evidence write verification failed: {path.name}"
        )


def _certificate_for_record(
    record: FormalPlanRecord,
    certificates_by_slot: Mapping[str, TasksetIdentityCertificate],
) -> TasksetIdentityCertificate:
    try:
        certificate = certificates_by_slot[str(record.taskset_slot_id)]
    except KeyError as exc:
        raise RTA4PilotExecutionError(
            "pilot store lacks a selected taskset slot"
        ) from exc
    certificate.validate()
    return certificate


def build_pilot_store_manifest(
    records: Sequence[FormalPlanRecord],
    certificates_by_slot: Mapping[str, TasksetIdentityCertificate],
    manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
) -> Dict[str, Any]:
    references: Dict[str, list[FormalPlanRecord]] = {}
    for record in records:
        references.setdefault(str(record.taskset_slot_id), []).append(record)
    rows = []
    for slot in sorted(
        references,
        key=lambda value: min(row.ordinal for row in references[value]),
    ):
        certificate = certificates_by_slot[slot]
        payload = certificate.canonical_bytes()
        for record in references[slot]:
            certificate.validate()
        rows.append({
            "taskset_slot_id": slot,
            "taskset_skeleton_slot_id": str(
                references[slot][0].taskset_skeleton_slot_id
            ),
            "taskset_id": certificate.taskset_id,
            "generation_request_id": certificate.generation_request_id,
            "taskset_skeleton_id": certificate.taskset_skeleton_id,
            "certificate_filename": (
                f"certificates/{certificate.taskset_id}.json"
            ),
            "certificate_sha256": hashlib.sha256(payload).hexdigest(),
            "source_provenance": "TRUSTED_FORMAL_PLAN_SLOT_RECONSTRUCTION",
            "referenced_cores": sorted({
                record.core for record in references[slot]
            }),
            "referenced_execution_ids": sorted(
                str(record.execution_id) for record in references[slot]
            ),
        })
    material = {
        "store_manifest_version": RTA4_PILOT_STORE_MANIFEST_VERSION,
        "execution_class": execution_config["execution_class"],
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "execution_config_id": execution_config["execution_config_id"],
        "slot_count": len(rows),
        "slots": rows,
    }
    return {
        **material,
        "store_manifest_id": domain_hash(
            RTA4_PILOT_STORE_MANIFEST_DOMAIN, material,
        ),
    }


def _load_pilot_store(
    store_root: Path,
    records: Sequence[FormalPlanRecord],
    manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any], *,
    configs: Mapping[str, Mapping[str, Any]],
    reconstruct_expected: bool,
) -> tuple[Dict[str, Any], Dict[str, TasksetIdentityCertificate]]:
    if not store_root.is_dir() or store_root.is_symlink():
        raise RTA4PilotExecutionError("pilot taskset store is missing")
    if {path.name for path in store_root.iterdir()} != {
        FORMAL_TASKSET_STORE_MANIFEST, RTA4_PILOT_STORE_MANIFEST,
        "certificates",
    }:
        raise RTA4PilotExecutionError(
            "pilot taskset store inventory is not exact"
        )
    if _load_json(store_root / FORMAL_TASKSET_STORE_MANIFEST) != (
        _formal_store_manifest()
    ):
        raise RTA4PilotExecutionError(
            "pilot taskset store marker is damaged"
        )
    observed = _load_json(store_root / RTA4_PILOT_STORE_MANIFEST)
    if (
        not isinstance(observed, Mapping)
        or observed.get("store_manifest_version")
        != RTA4_PILOT_STORE_MANIFEST_VERSION
        or observed.get("execution_class")
        != execution_config["execution_class"]
        or observed.get("pilot_manifest_id") != manifest["pilot_manifest_id"]
        or observed.get("execution_config_id")
        != execution_config["execution_config_id"]
    ):
        raise RTA4PilotExecutionError(
            "pilot taskset store manifest binding mismatch"
        )
    certificate_root = store_root / "certificates"
    entries = tuple(sorted(certificate_root.iterdir()))
    if any(
        not path.is_file() or path.is_symlink()
        or path.suffix != ".json" or len(path.stem) != 64
        for path in entries
    ):
        raise RTA4PilotExecutionError(
            "pilot certificate directory contains an unexpected entry"
        )
    rows = observed.get("slots")
    if not isinstance(rows, list):
        raise RTA4PilotExecutionError("pilot store slot index is invalid")
    by_slot: Dict[str, TasksetIdentityCertificate] = {}
    expected_files: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise RTA4PilotExecutionError("pilot store slot row is invalid")
        slot = str(row.get("taskset_slot_id"))
        if slot in by_slot:
            raise RTA4PilotExecutionError("duplicate pilot store slot")
        path = store_root / str(row.get("certificate_filename"))
        try:
            if (
                path.parent != certificate_root
                or path.name != f"{row['taskset_id']}.json"
            ):
                raise ValueError("certificate path escape")
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != (
                row["certificate_sha256"]
            ):
                raise ValueError("certificate SHA drift")
            certificate = (
                TasksetIdentityCertificate.from_canonical_bytes(payload)
            )
            certificate.validate()
        except Exception as exc:
            raise RTA4PilotExecutionError(
                "pilot store certificate bytes are missing or damaged"
            ) from exc
        by_slot[slot] = certificate
        expected_files.add(path.name)
    if {path.name for path in entries} != expected_files:
        raise RTA4PilotExecutionError(
            "pilot certificate inventory differs from the slot manifest"
        )
    expected = build_pilot_store_manifest(
        records, by_slot, manifest, execution_config,
    )
    if dict(observed) != expected:
        raise RTA4PilotExecutionError(
            "pilot store manifest cannot be reconstructed"
        )
    for record in records:
        certificate = _certificate_for_record(record, by_slot)
        RTA4FormalRunner(
            configs[record.core]
        )._validate_plan_certificate(record, certificate)
    if (
        reconstruct_expected
        and execution_config["execution_class"]
        == RTA4_PILOT_EXECUTION_CLASS
    ):
        provider = PilotTasksetProvider(configs)
        reconstructed: Dict[str, TasksetIdentityCertificate] = {}
        for record in records:
            slot = str(record.taskset_slot_id)
            if slot not in reconstructed:
                reconstructed[slot] = provider(record)
            if reconstructed[slot].canonical_bytes() != (
                by_slot[slot].canonical_bytes()
            ):
                raise RTA4PilotExecutionError(
                    "pilot certificate differs from trusted slot generation"
                )
    return dict(observed), by_slot


def _simulation_identity(
    record: FormalPlanRecord,
    certificate: TasksetIdentityCertificate,
) -> tuple[Any, Any, Sequence[Mapping[str, Any]], str]:
    from .rta4_formal_pipeline import (
        build_formal_release_projection, formal_service_identity,
    )
    from .release_applicability import (
        TARGET_SCHEDULER, simulation_applicability_identity,
    )

    projection, window, payload = build_formal_release_projection(
        certificate, record.material["release_mode"],
    )
    simulation_id = simulation_applicability_identity(
        taskset_id=certificate.taskset_id,
        release_projection_id=projection.release_projection_id,
        scheduler=TARGET_SCHEDULER,
        service_identity=formal_service_identity(
            record.material["service_scale"]
        ),
        initial_battery=record.material["physical_initial_energy"],
        battery_capacity=record.material["battery_capacity"],
        window=window,
        applicability_track=record.material["applicability_track"],
    )
    return projection, window, payload, simulation_id


def _validate_trace(
    path: Path, record: FormalPlanRecord,
    certificate: TasksetIdentityCertificate,
    expected_simulation_id: str,
) -> None:
    from .release_applicability import parse_release_trace

    projection, window, payload, simulation_id = _simulation_identity(
        record, certificate,
    )
    if simulation_id != expected_simulation_id:
        raise RTA4PilotExecutionError(
            "simulation trace identity differs from trusted plan"
        )
    try:
        parse_release_trace(
            path, payload, expected_simulation_id=simulation_id,
            expected_taskset_hash=certificate.taskset_hash,
            expected_certificate=certificate,
            expected_projection=projection, window=window,
        )
    except Exception as exc:
        raise RTA4PilotExecutionError(
            "pilot trace parser/completeness audit failed"
        ) from exc


def _build_checkpoint_event(
    checkpoint: Mapping[str, Any], checkpoint_path: Path, *,
    triggering_execution_id: str | None,
    write_milliseconds: int,
) -> Dict[str, Any]:
    material = {
        "checkpoint_event_version": RTA4_PILOT_CHECKPOINT_EVENT_VERSION,
        "checkpoint_generation": checkpoint["checkpoint_generation"],
        "triggering_execution_id": triggering_execution_id,
        "completed_raw_id_set_sha256": hashlib.sha256(
            canonical_json(sorted(
                checkpoint["completed_raw_terminal_digests"]
            )).encode("utf-8")
        ).hexdigest(),
        "checkpoint_filename": checkpoint_path.name,
        "checkpoint_payload_sha256": _sha256(checkpoint_path),
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_write_milliseconds": write_milliseconds,
    }
    identity = domain_hash(RTA4_PILOT_CHECKPOINT_EVENT_DOMAIN, material)
    document = {**material, "checkpoint_event_id": identity}
    return {
        **document,
        "checkpoint_event_sha256": hashlib.sha256(
            canonical_json(document).encode("utf-8")
        ).hexdigest(),
    }


def _validate_checkpoint_event(
    document: Mapping[str, Any], checkpoint_path: Path,
    checkpoint: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = _build_checkpoint_event(
        checkpoint, checkpoint_path,
        triggering_execution_id=document.get("triggering_execution_id"),
        write_milliseconds=document.get("checkpoint_write_milliseconds"),
    )
    if dict(document) != expected:
        raise RTA4PilotExecutionError(
            "checkpoint event cannot be reconstructed"
        )
    return expected


def _build_resume_event(
    execution_config: Mapping[str, Any], *,
    generation: int, preflight_started_ns: int,
    preflight_finished_ns: int, initialization_milliseconds: int,
    first_pending_execution_id: str,
) -> Dict[str, Any]:
    material = {
        "resume_event_version": RTA4_PILOT_RESUME_EVENT_VERSION,
        "execution_config_id": execution_config["execution_config_id"],
        "resume_generation": generation,
        "preflight_started_monotonic_ns": preflight_started_ns,
        "preflight_finished_monotonic_ns": preflight_finished_ns,
        "resume_initialization_milliseconds": initialization_milliseconds,
        "first_pending_execution_id": first_pending_execution_id,
    }
    identity = domain_hash(RTA4_PILOT_RESUME_EVENT_DOMAIN, material)
    document = {**material, "resume_event_id": identity}
    return {
        **document,
        "resume_event_sha256": hashlib.sha256(
            canonical_json(document).encode("utf-8")
        ).hexdigest(),
    }


def _validate_resume_event(
    document: Mapping[str, Any],
    execution_config: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = _build_resume_event(
        execution_config,
        generation=document.get("resume_generation"),
        preflight_started_ns=document.get("preflight_started_monotonic_ns"),
        preflight_finished_ns=document.get(
            "preflight_finished_monotonic_ns"
        ),
        initialization_milliseconds=document.get(
            "resume_initialization_milliseconds"
        ),
        first_pending_execution_id=document.get(
            "first_pending_execution_id"
        ),
    )
    if dict(document) != expected:
        raise RTA4PilotExecutionError(
            "resume event cannot be reconstructed"
        )
    return expected


def _checkpoint_pointer(
    checkpoint: Mapping[str, Any], checkpoint_path: Path,
    event: Mapping[str, Any], event_path: Path,
) -> Dict[str, Any]:
    material = {
        "checkpoint_pointer_version": (
            "ASAP_BLOCK_V9_3_RTA4_PILOT_CHECKPOINT_POINTER_V1"
        ),
        "checkpoint_generation": checkpoint["checkpoint_generation"],
        "phase": checkpoint["phase"],
        "state": checkpoint["state"],
        "checkpoint_filename": checkpoint_path.name,
        "checkpoint_payload_sha256": _sha256(checkpoint_path),
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_event_filename": event_path.name,
        "checkpoint_event_sha256": event["checkpoint_event_sha256"],
        "checkpoint_event_id": event["checkpoint_event_id"],
    }
    return {
        **material,
        "checkpoint_pointer_sha256": domain_hash(
            "ASAP_BLOCK:V9.3:RTA4_PILOT_CHECKPOINT_POINTER:v1",
            material,
        ),
    }


def _load_checkpoint_transaction(
    root: Path,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any],
           tuple[Dict[str, Any], ...], tuple[Path, ...]]:
    pointer = _load_json(root / RTA4_PILOT_CHECKPOINT)
    if not isinstance(pointer, Mapping):
        raise RTA4PilotExecutionError("checkpoint pointer is invalid")
    generation_root = root / RTA4_PILOT_CHECKPOINT_DIRECTORY
    event_root = root / RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY
    generations = _evidence_paths(
        root, RTA4_PILOT_CHECKPOINT_DIRECTORY, stem_length=8,
    )
    events = _evidence_paths(
        root, RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY, stem_length=8,
    )
    checkpoint_path = generation_root / str(pointer.get(
        "checkpoint_filename"
    ))
    event_path = event_root / str(pointer.get(
        "checkpoint_event_filename"
    ))
    if checkpoint_path not in generations or event_path not in events:
        raise RTA4PilotExecutionError(
            "checkpoint pointer references absent transaction evidence"
        )
    checkpoint = _load_json(checkpoint_path)
    material = dict(checkpoint)
    observed_id = material.pop("checkpoint_id", None)
    if (
        checkpoint.get("checkpoint_version")
        != RTA4_PILOT_CHECKPOINT_VERSION
        or observed_id != domain_hash(
            RTA4_PILOT_CHECKPOINT_DOMAIN, material,
        )
    ):
        raise RTA4PilotExecutionError(
            "checkpoint generation identity mismatch"
        )
    event = _validate_checkpoint_event(
        _load_json(event_path), checkpoint_path, checkpoint,
    )
    if dict(pointer) != _checkpoint_pointer(
        checkpoint, checkpoint_path, event, event_path,
    ):
        raise RTA4PilotExecutionError(
            "canonical checkpoint pointer mismatch"
        )
    by_id: Dict[str, Dict[str, Any]] = {}
    committed_paths = {checkpoint_path, event_path}
    for path in events:
        document = _load_json(path)
        event_id = document.get("checkpoint_event_id")
        event_sha = document.get("checkpoint_event_sha256")
        if (
            event_id in checkpoint["checkpoint_event_digests"]
            and checkpoint["checkpoint_event_digests"][event_id]
            == event_sha
        ):
            referenced_generation = generation_root / str(
                document.get("checkpoint_filename")
            )
            if not referenced_generation.is_file():
                raise RTA4PilotExecutionError(
                    "committed checkpoint event lacks its generation"
                )
            referenced_checkpoint = _load_json(referenced_generation)
            _validate_checkpoint_event(
                document, referenced_generation, referenced_checkpoint,
            )
            by_id[event_id] = document
            committed_paths.update({path, referenced_generation})
    if set(by_id) != set(checkpoint["checkpoint_event_digests"]):
        raise RTA4PilotExecutionError(
            "checkpoint generation event inventory is incomplete"
        )
    by_id[event["checkpoint_event_id"]] = event
    orphans = tuple(sorted(
        (set(generations) | set(events)) - committed_paths
    ))
    return (
        dict(pointer), dict(checkpoint), dict(event),
        tuple(by_id[key] for key in sorted(by_id)), orphans,
    )


def _audit_material(
    *, root: Path, manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    execution_manifest: Mapping[str, Any],
    store_manifest: Mapping[str, Any],
    pointer: Mapping[str, Any], checkpoint: Mapping[str, Any],
    raw_terminals: Sequence[Mapping[str, Any]],
    final_terminals: Sequence[Mapping[str, Any]],
    checkpoint_events: Sequence[Mapping[str, Any]],
    resume_events: Sequence[Mapping[str, Any]],
    trace_digests: Mapping[str, str],
    observations: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    recovery_orphan_count: int,
) -> Dict[str, Any]:
    complete = checkpoint["phase"] == "PILOT_COMPLETE"
    eligible = (
        complete
        and recovery_orphan_count == 0
        and execution_config["execution_class"]
        == RTA4_PILOT_EXECUTION_CLASS
        and observations is not None and report is not None
    )
    raw_map = _digest_map(
        raw_terminals, id_field="execution_id",
        digest_field="raw_terminal_sha256",
    )
    final_map = _digest_map(
        final_terminals, id_field="execution_id",
        digest_field="final_terminal_sha256",
    )
    event_map = _digest_map(
        checkpoint_events, id_field="checkpoint_event_id",
        digest_field="checkpoint_event_sha256",
    )
    resume_map = _digest_map(
        resume_events, id_field="resume_event_id",
        digest_field="resume_event_sha256",
    )
    return {
        "audit_version": RTA4_PILOT_AUDIT_VERSION,
        "audit_status": (
            "ENGINEERING_PILOT_AUDIT_COMPLETE"
            if complete else "ENGINEERING_PILOT_AUDIT_PARTIAL"
        ),
        "execution_class": execution_config["execution_class"],
        "freeze_eligible": eligible,
        "pilot_root": str(root.resolve()),
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "execution_config_id": execution_config["execution_config_id"],
        "execution_manifest_id": execution_manifest[
            "execution_manifest_id"
        ],
        "store_manifest_id": store_manifest["store_manifest_id"],
        "store_marker_sha256": hashlib.sha256(
            _canonical_json_bytes(_formal_store_manifest())
        ).hexdigest(),
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_pointer_sha256": pointer[
            "checkpoint_pointer_sha256"
        ],
        "checkpoint_phase": checkpoint["phase"],
        "checkpoint_state": checkpoint["state"],
        "raw_terminal_count": len(raw_map),
        "raw_terminal_set_sha256": hashlib.sha256(
            canonical_json(raw_map).encode("utf-8")
        ).hexdigest(),
        "terminal_count": len(final_map),
        "terminal_set_sha256": hashlib.sha256(
            canonical_json(final_map).encode("utf-8")
        ).hexdigest(),
        "taskset_certificate_set_sha256": hashlib.sha256(
            canonical_json([
                {
                    "slot": row["taskset_slot_id"],
                    "sha256": row["certificate_sha256"],
                }
                for row in store_manifest["slots"]
            ]).encode("utf-8")
        ).hexdigest(),
        "checkpoint_event_set_sha256": hashlib.sha256(
            canonical_json(event_map).encode("utf-8")
        ).hexdigest(),
        "resume_event_set_sha256": hashlib.sha256(
            canonical_json(resume_map).encode("utf-8")
        ).hexdigest(),
        "trace_set_sha256": hashlib.sha256(
            canonical_json({
                key: trace_digests[key] for key in sorted(trace_digests)
            }).encode("utf-8")
        ).hexdigest(),
        "trace_parser_identity": PILOT_TRACE_PARSER_IDENTITY,
        "trace_completeness_identity": (
            PILOT_TRACE_COMPLETENESS_IDENTITY
        ),
        "source_manifest_id": execution_config["source_manifest"][
            "manifest_id"
        ],
        "dependency_manifest_id": execution_config[
            "dependency_manifest"
        ]["manifest_id"],
        "environment_manifest_id": execution_config[
            "environment_manifest"
        ]["manifest_id"],
        "hardware_manifest_id": execution_config["hardware_manifest"][
            "manifest_id"
        ],
        "simulator_manifest_id": execution_config["simulator_manifest"][
            "manifest_id"
        ],
        "pilot_observations_id": (
            None if observations is None
            else observations["pilot_observations_id"]
        ),
        "pilot_report_id": (
            None if report is None else report["pilot_report_id"]
        ),
        "pilot_closure_id": (
            None if report is None else report["pilot_closure_id"]
        ),
        "recovery_orphan_count": recovery_orphan_count,
        "scientific_results_included": False,
    }


def validate_pilot_audit_document(
    document: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(document, Mapping):
        raise RTA4PilotExecutionError("pilot audit must be a mapping")
    material = dict(document)
    observed = material.pop("audit_id", None)
    if (
        material.get("audit_version") != RTA4_PILOT_AUDIT_VERSION
        or material.get("scientific_results_included") is not False
        or material.get("execution_class") not in {
            RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_TEST_EXECUTION_CLASS,
        }
        or observed != domain_hash(RTA4_PILOT_AUDIT_DOMAIN, material)
    ):
        raise RTA4PilotExecutionError("pilot audit contract mismatch")
    complete = material.get("checkpoint_phase") == "PILOT_COMPLETE"
    if (
        material.get("checkpoint_state")
        != ("PILOT_COMPLETE" if complete else "INCOMPLETE_PILOT")
        or material.get("audit_status")
        != (
            "ENGINEERING_PILOT_AUDIT_COMPLETE"
            if complete else "ENGINEERING_PILOT_AUDIT_PARTIAL"
        )
        or type(material.get("freeze_eligible")) is not bool
        or type(material.get("recovery_orphan_count")) is not int
        or material["recovery_orphan_count"] < 0
    ):
        raise RTA4PilotExecutionError("pilot audit state mismatch")
    final_fields = (
        "pilot_observations_id", "pilot_report_id", "pilot_closure_id",
    )
    final_present = all(
        material.get(field) is not None for field in final_fields
    )
    if (
        complete and not final_present
        or final_present
        and material.get("checkpoint_phase") not in {
            "FINALIZING", "PILOT_COMPLETE",
        }
    ):
        raise RTA4PilotExecutionError(
            "pilot audit final evidence/state mismatch"
        )
    expected_eligible = (
        complete
        and material["execution_class"] == RTA4_PILOT_EXECUTION_CLASS
        and material["recovery_orphan_count"] == 0
    )
    if material["freeze_eligible"] is not expected_eligible:
        raise RTA4PilotExecutionError(
            "pilot audit freeze eligibility mismatch"
        )
    for field, value in material.items():
        if (
            field.endswith(("_id", "_sha256"))
            and value is not None
            and (
                not isinstance(value, str)
                or len(value) != 64
            )
        ):
            raise RTA4PilotExecutionError(
                "pilot audit contains a malformed identity"
            )
    return dict(document)


def audit_pilot_namespace(
    root: Path | str,
    configs: Mapping[str, Mapping[str, Any]], *,
    require_complete: bool = True,
    reconstruct_store: bool = True,
    allow_recovery_artifacts: bool = False,
) -> Dict[str, Any]:
    """Independently reconstruct every file-backed pilot evidence layer."""

    output = Path(root).resolve(strict=True)
    allowed_root_entries = {
        RTA4_PILOT_OUTPUT_MARKER, RTA4_PILOT_EXECUTION_CONFIG,
        RTA4_PILOT_EXECUTION_MANIFEST, RTA4_PILOT_CHECKPOINT,
        RTA4_PILOT_RAW_TERMINAL_DIRECTORY,
        RTA4_PILOT_FINAL_TERMINAL_DIRECTORY,
        RTA4_PILOT_TRACE_DIRECTORY, RTA4_PILOT_CHECKPOINT_DIRECTORY,
        RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY,
        RTA4_PILOT_RESUME_EVENT_DIRECTORY,
        RTA4_PILOT_WORKER_TRACE_DIRECTORY,
        RTA4_PILOT_OBSERVATIONS, RTA4_PILOT_REPORT, RTA4_PILOT_AUDIT,
    }
    if {path.name for path in output.iterdir()} - allowed_root_entries:
        raise RTA4PilotExecutionError(
            "pilot output root contains an unexpected entry"
        )
    manifest = validate_pilot_manifest(
        _load_json(output / RTA4_PILOT_OUTPUT_MARKER), configs,
    )
    execution_config = validate_pilot_execution_config(
        _load_json(output / RTA4_PILOT_EXECUTION_CONFIG),
        manifest, validate_live_source=True,
    )
    execution_manifest = validate_pilot_execution_manifest(
        _load_json(output / RTA4_PILOT_EXECUTION_MANIFEST),
        manifest, execution_config,
    )
    records = reconstruct_selected_records(configs, manifest)
    selected = {
        row["execution_id"]: row for row in _selected_rows(manifest)
    }
    records_by_execution = {
        str(record.execution_id): record for record in records
    }
    store_manifest, certificates = _load_pilot_store(
        Path(execution_config["taskset_store"]), records,
        manifest, execution_config, configs=configs,
        reconstruct_expected=reconstruct_store,
    )
    raw_paths = _evidence_paths(
        output, RTA4_PILOT_RAW_TERMINAL_DIRECTORY,
    )
    if any(path.stem not in selected for path in raw_paths):
        raise RTA4PilotExecutionError(
            "raw terminal filename is outside the selected plan"
        )
    raw_terminals = []
    raw_by_execution: Dict[str, Dict[str, Any]] = {}
    for path in raw_paths:
        record = records_by_execution[path.stem]
        raw = validate_pilot_raw_terminal(
            _load_json(path), selected[path.stem], execution_config,
            _certificate_for_record(record, certificates),
        )
        if raw["execution_id"] != path.stem:
            raise RTA4PilotExecutionError(
                "raw terminal filename/payload mismatch"
            )
        raw_by_execution[path.stem] = raw
        raw_terminals.append(raw)
    trace_paths = {
        path.stem: path for path in _evidence_paths(
            output, RTA4_PILOT_TRACE_DIRECTORY,
        )
    }
    expected_trace_ids = {
        execution_id for execution_id, raw in raw_by_execution.items()
        if raw["trace_filename"] is not None
    }
    if set(trace_paths) != expected_trace_ids:
        raise RTA4PilotExecutionError(
            "pilot trace inventory differs from raw terminals"
        )
    trace_digests: Dict[str, str] = {}
    for execution_id, path in trace_paths.items():
        raw = raw_by_execution[execution_id]
        digest = _sha256(path)
        if (
            raw["trace_filename"] != path.name
            or raw["trace_size_bytes"] != path.stat().st_size
            or raw["trace_sha256"] != digest
            or raw["trace_schema_version"] != 2
            or raw["trace_parser_identity"] != PILOT_TRACE_PARSER_IDENTITY
            or raw["trace_completeness_identity"]
            != PILOT_TRACE_COMPLETENESS_IDENTITY
        ):
            raise RTA4PilotExecutionError(
                "pilot trace bytes/bindings differ from raw evidence"
            )
        _validate_trace(
            path, records_by_execution[execution_id],
            _certificate_for_record(
                records_by_execution[execution_id], certificates,
            ),
            raw["simulation_id"],
        )
        trace_digests[execution_id] = digest
    (
        pointer, checkpoint, _current_event,
        checkpoint_events, orphan_paths,
    ) = _load_checkpoint_transaction(output)
    if (
        checkpoint["pilot_manifest_id"] != manifest["pilot_manifest_id"]
        or checkpoint["execution_config_id"]
        != execution_config["execution_config_id"]
        or checkpoint["execution_manifest_id"]
        != execution_manifest["execution_manifest_id"]
        or checkpoint["store_manifest_id"]
        != store_manifest["store_manifest_id"]
        or checkpoint["planned_record_count"] != len(records)
    ):
        raise RTA4PilotExecutionError(
            "checkpoint generation binding mismatch"
        )
    raw_digest_map = {
        key: value["raw_terminal_sha256"]
        for key, value in raw_by_execution.items()
    }
    if any(
        raw_digest_map.get(key) != value
        for key, value in checkpoint[
            "completed_raw_terminal_digests"
        ].items()
    ):
        raise RTA4PilotExecutionError(
            "checkpoint binds absent or changed raw terminal"
        )
    resume_events = tuple(
        _validate_resume_event(_load_json(path), execution_config)
        for path in _evidence_paths(
            output, RTA4_PILOT_RESUME_EVENT_DIRECTORY, stem_length=8,
        )
    )
    if len({
        event["resume_generation"] for event in resume_events
    }) != len(resume_events):
        raise RTA4PilotExecutionError("duplicate resume event generation")
    final_paths = _evidence_paths(
        output, RTA4_PILOT_FINAL_TERMINAL_DIRECTORY,
    )
    if any(path.stem not in raw_by_execution for path in final_paths):
        raise RTA4PilotExecutionError(
            "final terminal lacks immutable raw evidence"
        )
    warnings = runtime_ci_engineering_warnings(
        manifest["pilot_manifest_id"], raw_terminals,
    ) if len(raw_terminals) == len(records) else {}
    checkpoint_overhead: Dict[str, int] = {}
    for event in checkpoint_events:
        trigger = event["triggering_execution_id"]
        if trigger is not None:
            checkpoint_overhead[trigger] = (
                checkpoint_overhead.get(trigger, 0)
                + event["checkpoint_write_milliseconds"]
            )
    resume_overhead: Dict[str, int] = {}
    for event in resume_events:
        trigger = event["first_pending_execution_id"]
        if trigger in resume_overhead:
            raise RTA4PilotExecutionError(
                "multiple resume events target one execution"
            )
        resume_overhead[trigger] = event[
            "resume_initialization_milliseconds"
        ]
    final_terminals = []
    for path in final_paths:
        execution_id = path.stem
        if not warnings:
            raise RTA4PilotExecutionError(
                "final evidence exists before all raw terminals"
            )
        final = validate_pilot_final_terminal(
            _load_json(path), raw_by_execution[execution_id],
            checkpoint_overhead_milliseconds=checkpoint_overhead.get(
                execution_id, 0,
            ),
            resume_overhead_milliseconds=resume_overhead.get(
                execution_id, 0,
            ),
            ci_width_engineering_warning=warnings[
                raw_by_execution[execution_id]["plan_record_id"]
            ],
        )
        final_terminals.append(final)
    final_digest_map = {
        row["execution_id"]: row["final_terminal_sha256"]
        for row in final_terminals
    }
    if any(
        final_digest_map.get(key) != value
        for key, value in checkpoint["final_terminal_digests"].items()
    ) or any(
        trace_digests.get(key) != value
        for key, value in checkpoint["trace_digests"].items()
    ):
        raise RTA4PilotExecutionError(
            "checkpoint binds absent or changed final/trace evidence"
        )
    worker_root = output / RTA4_PILOT_WORKER_TRACE_DIRECTORY
    stale_worker_entries = (
        tuple(worker_root.iterdir()) if worker_root.is_dir() else ()
    )
    if any(
        not path.is_dir() or path.is_symlink()
        or path.parent != worker_root
        for path in stale_worker_entries
    ):
        raise RTA4PilotExecutionError(
            "worker temporary namespace contains an unsafe entry"
        )
    recovery_count = len(orphan_paths) + len(stale_worker_entries)
    complete = checkpoint["phase"] == "PILOT_COMPLETE"
    if complete and (
        set(raw_by_execution) != set(selected)
        or set(final_digest_map) != set(selected)
        or checkpoint["completed_raw_terminal_digests"] != raw_digest_map
        or checkpoint["final_terminal_digests"] != final_digest_map
        or checkpoint["trace_digests"] != trace_digests
        or recovery_count
    ):
        raise RTA4PilotExecutionError(
            "complete checkpoint inventory is not exact"
        )
    if require_complete and not complete:
        raise RTA4PilotExecutionError("partial pilot cannot pass final audit")
    if require_complete and recovery_count:
        raise RTA4PilotExecutionError(
            "pilot has uncommitted recovery artifacts"
        )
    if recovery_count and not allow_recovery_artifacts:
        raise RTA4PilotExecutionError(
            "pilot has uncommitted recovery artifacts"
        )
    observations = None
    report = None
    observations_path = output / RTA4_PILOT_OBSERVATIONS
    report_path = output / RTA4_PILOT_REPORT
    final_documents_present = (
        observations_path.is_file() and report_path.is_file()
    )
    if (
        observations_path.exists() != report_path.exists()
        or final_documents_present and (
            checkpoint["phase"] not in {"FINALIZING", "PILOT_COMPLETE"}
            or set(final_digest_map) != set(selected)
        )
    ):
        raise RTA4PilotExecutionError(
            "pilot final document state is not transactional"
        )
    if final_documents_present:
        observations = validate_pilot_observations(
            _load_json(observations_path), manifest,
        )
        rebuilt = build_pilot_observations(
            manifest,
            [_terminal_observation_input(row) for row in final_terminals],
        )
        if observations != rebuilt:
            raise RTA4PilotExecutionError(
                "pilot observations differ from final terminals"
            )
        report = validate_pilot_report(
            _load_json(report_path),
            manifest, observations,
        )
    material = _audit_material(
        root=output, manifest=manifest,
        execution_config=execution_config,
        execution_manifest=execution_manifest,
        store_manifest=store_manifest, pointer=pointer,
        checkpoint=checkpoint, raw_terminals=raw_terminals,
        final_terminals=final_terminals,
        checkpoint_events=checkpoint_events,
        resume_events=resume_events, trace_digests=trace_digests,
        observations=observations, report=report,
        recovery_orphan_count=recovery_count,
    )
    audit = validate_pilot_audit_document({
        **material,
        "audit_id": domain_hash(RTA4_PILOT_AUDIT_DOMAIN, material),
    })
    audit_path = output / RTA4_PILOT_AUDIT
    if audit_path.is_file() and _load_json(audit_path) != audit:
        raise RTA4PilotExecutionError(
            "persisted pilot audit differs from fresh reconstruction"
        )
    return audit


class PilotExecutionRunner:
    """Parent-owned transactional engineering-pilot execution."""

    def __init__(
        self, configs: Mapping[str, Mapping[str, Any]],
        manifest: Mapping[str, Any],
        execution_config: Mapping[str, Any],
    ) -> None:
        self.configs = {
            core: validate_rta4_formal_config(
                configs[core], expected_core=core,
            )
            for core in RTA4_CORES
        }
        self.manifest = validate_pilot_manifest(manifest, self.configs)
        self.execution_config = validate_pilot_execution_config(
            execution_config, self.manifest, validate_live_source=True,
        )
        canonical_config_path = (
            Path(self.execution_config["output_root"])
            / RTA4_PILOT_EXECUTION_CONFIG
        )
        if (
            not canonical_config_path.is_file()
            or canonical_config_path.read_bytes()
            != _canonical_json_bytes(self.execution_config)
        ):
            raise RTA4PilotExecutionError(
                "runner execution config differs from the canonical root copy"
            )
        self.execution_manifest = build_pilot_execution_manifest(
            self.manifest, self.execution_config,
        )
        self.records = reconstruct_selected_records(
            self.configs, self.manifest,
        )
        self.selected = {
            row["execution_id"]: row for row in _selected_rows(self.manifest)
        }
        if [record.execution_id for record in self.records] != list(
            self.execution_manifest["ordered_execution_ids"]
        ):
            raise RTA4PilotExecutionError(
                "execution order differs from selection manifest"
            )

    def _write_initial_namespace(
        self,
        provider: Callable[
            [FormalPlanRecord], TasksetIdentityCertificate
        ],
        transaction_hook: Callable[[str], None] | None,
    ) -> tuple[Dict[str, Any], Dict[str, TasksetIdentityCertificate]]:
        root = Path(self.execution_config["output_root"])
        allowed = {
            RTA4_PILOT_OUTPUT_MARKER,
            RTA4_PILOT_EXECUTION_CONFIG,
        }
        extras = {path.name for path in root.iterdir()} - allowed
        if extras:
            raise RTA4PilotExecutionError(
                "fresh pilot execution requires a plan-only namespace"
            )
        manifest_path = root / RTA4_PILOT_OUTPUT_MARKER
        if not manifest_path.is_file() or _load_json(manifest_path) != (
            self.manifest
        ):
            raise RTA4PilotExecutionError(
                "fresh pilot namespace lacks the bound selection manifest"
            )
        _write_json_once(
            root / RTA4_PILOT_EXECUTION_MANIFEST,
            self.execution_manifest,
        )
        for directory in (
            RTA4_PILOT_RAW_TERMINAL_DIRECTORY,
            RTA4_PILOT_FINAL_TERMINAL_DIRECTORY,
            RTA4_PILOT_TRACE_DIRECTORY,
            RTA4_PILOT_CHECKPOINT_DIRECTORY,
            RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY,
            RTA4_PILOT_RESUME_EVENT_DIRECTORY,
            RTA4_PILOT_WORKER_TRACE_DIRECTORY,
        ):
            (root / directory).mkdir(parents=True, exist_ok=False)
        store = RTA4FormalTasksetStore(
            self.execution_config["taskset_store"]
        )
        certificates: Dict[str, TasksetIdentityCertificate] = {}
        for record in self.records:
            slot = str(record.taskset_slot_id)
            if slot in certificates:
                certificate = certificates[slot]
            else:
                certificate = provider(record)
                if type(certificate) is not TasksetIdentityCertificate:
                    raise RTA4PilotExecutionError(
                        "pilot provider must return a PR-B certificate"
                    )
                certificates[slot] = certificate
            RTA4FormalRunner(
                self.configs[record.core]
            )._validate_plan_certificate(
                record, certificate,
            )
            store.put(certificate)
        store_manifest = build_pilot_store_manifest(
            self.records, certificates, self.manifest,
            self.execution_config,
        )
        _write_json_once(
            Path(self.execution_config["taskset_store"])
            / RTA4_PILOT_STORE_MANIFEST,
            store_manifest,
        )
        self._commit_checkpoint(
            store_manifest, certificates, phase="EXECUTING",
            triggering_execution_id=None,
            transaction_hook=transaction_hook,
        )
        return store_manifest, certificates

    def _resume_preflight(
        self, *, allow_recovery_artifacts: bool,
    ) -> Mapping[str, Any]:
        return audit_pilot_namespace(
            self.execution_config["output_root"], self.configs,
            require_complete=False, reconstruct_store=False,
            allow_recovery_artifacts=allow_recovery_artifacts,
        )

    def _safe_cleanup_worker_root(self, path: Path) -> None:
        canonical = (
            Path(self.execution_config["output_root"])
            / RTA4_PILOT_WORKER_TRACE_DIRECTORY
        ).resolve(strict=True)
        if (
            path.is_symlink()
            or path.parent.resolve(strict=True) != canonical
            or path.resolve(strict=True).parent != canonical
        ):
            raise RTA4PilotExecutionError(
                "refusing unsafe worker temporary cleanup"
            )
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise RTA4PilotExecutionError(
                "parent could not clean worker temporary evidence"
            ) from exc

    def _cleanup_recovery_artifacts(self) -> None:
        root = Path(self.execution_config["output_root"])
        *_transaction, orphan_paths = _load_checkpoint_transaction(root)
        for path in orphan_paths:
            if (
                path.is_symlink() or not path.is_file()
                or path.parent not in {
                    root / RTA4_PILOT_CHECKPOINT_DIRECTORY,
                    root / RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY,
                }
            ):
                raise RTA4PilotExecutionError(
                    "refusing unsafe orphan transaction cleanup"
                )
            path.unlink()
        worker_root = root / RTA4_PILOT_WORKER_TRACE_DIRECTORY
        for path in tuple(worker_root.iterdir()):
            self._safe_cleanup_worker_root(path)

    def _load_store(
        self,
    ) -> tuple[Dict[str, Any], Dict[str, TasksetIdentityCertificate]]:
        return _load_pilot_store(
            Path(self.execution_config["taskset_store"]),
            self.records, self.manifest, self.execution_config,
            configs=self.configs, reconstruct_expected=False,
        )

    def _persist_trace(
        self, record: FormalPlanRecord,
        certificate: TasksetIdentityCertificate,
        payload: bytes | None, simulation_id: str,
        worker_temp_root: Path,
    ) -> tuple[int, str | None]:
        if payload is None:
            return 0, None
        if not isinstance(payload, bytes):
            raise RTA4PilotExecutionError(
                "worker trace payload must be bytes"
            )
        try:
            text = payload.decode("utf-8")
            json.loads(
                text,
                parse_constant=lambda value: (
                    (_ for _ in ()).throw(
                        ValueError(f"non-finite JSON: {value}")
                    )
                ),
            )
        except Exception as exc:
            raise RTA4PilotExecutionError(
                "worker trace payload is not strict UTF-8 JSON"
            ) from exc
        candidate = worker_temp_root / "trace_candidate.json"
        atomic_write_text(candidate, text)
        _validate_trace(candidate, record, certificate, simulation_id)
        target = (
            Path(self.execution_config["output_root"])
            / RTA4_PILOT_TRACE_DIRECTORY / f"{record.execution_id}.json"
        )
        _write_text_once(target, text)
        size = target.stat().st_size
        return size, _sha256(target)

    def _load_raw_terminals(
        self,
        certificates: Mapping[str, TasksetIdentityCertificate],
    ) -> list[Dict[str, Any]]:
        root = Path(self.execution_config["output_root"])
        records = {
            str(record.execution_id): record for record in self.records
        }
        rows = []
        for path in _evidence_paths(
            root, RTA4_PILOT_RAW_TERMINAL_DIRECTORY,
        ):
            record = records.get(path.stem)
            if record is None:
                raise RTA4PilotExecutionError(
                    "raw terminal is outside selected execution inventory"
                )
            rows.append(validate_pilot_raw_terminal(
                _load_json(path), self.selected[path.stem],
                self.execution_config,
                _certificate_for_record(record, certificates),
            ))
        return rows

    def _load_resume_events(self) -> list[Dict[str, Any]]:
        root = Path(self.execution_config["output_root"])
        return [
            _validate_resume_event(_load_json(path), self.execution_config)
            for path in _evidence_paths(
                root, RTA4_PILOT_RESUME_EVENT_DIRECTORY, stem_length=8,
            )
        ]

    def _load_final_terminals(self) -> list[Dict[str, Any]]:
        root = Path(self.execution_config["output_root"])
        return [
            _load_json(path) for path in _evidence_paths(
                root, RTA4_PILOT_FINAL_TERMINAL_DIRECTORY,
            )
        ]

    def _trace_digests(self) -> Dict[str, str]:
        root = Path(self.execution_config["output_root"])
        return {
            path.stem: _sha256(path)
            for path in _evidence_paths(
                root, RTA4_PILOT_TRACE_DIRECTORY,
            )
        }

    def _commit_checkpoint(
        self,
        store_manifest: Mapping[str, Any],
        certificates: Mapping[str, TasksetIdentityCertificate],
        *, phase: str, triggering_execution_id: str | None,
        transaction_hook: Callable[[str], None] | None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        root = Path(self.execution_config["output_root"])
        pointer_path = root / RTA4_PILOT_CHECKPOINT
        if pointer_path.is_file():
            (
                _pointer, _checkpoint, _event,
                committed_events, orphan_paths,
            ) = _load_checkpoint_transaction(root)
            if orphan_paths:
                raise RTA4PilotExecutionError(
                    "checkpoint transaction has unresolved orphan evidence"
                )
            generation = _checkpoint["checkpoint_generation"] + 1
        else:
            committed_events = ()
            generation = 1
        raw = self._load_raw_terminals(certificates)
        final = self._load_final_terminals()
        resume_events = self._load_resume_events()
        checkpoint = build_pilot_checkpoint(
            self.manifest, self.execution_config,
            self.execution_manifest, store_manifest=store_manifest,
            raw_terminals=raw, checkpoint_events=committed_events,
            resume_events=resume_events, final_terminals=final,
            trace_digests=self._trace_digests(), phase=phase,
            generation=generation,
        )
        filename = f"{generation:08d}.json"
        checkpoint_path = (
            root / RTA4_PILOT_CHECKPOINT_DIRECTORY / filename
        )
        started = time.monotonic_ns()
        _write_json_once(checkpoint_path, checkpoint)
        elapsed = _nonnegative_milliseconds(
            time.monotonic_ns() - started
        )
        if transaction_hook is not None:
            transaction_hook("after_checkpoint_generation")
        event = _build_checkpoint_event(
            checkpoint, checkpoint_path,
            triggering_execution_id=triggering_execution_id,
            write_milliseconds=elapsed,
        )
        event_path = (
            root / RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY / filename
        )
        _write_json_once(event_path, event)
        if transaction_hook is not None:
            transaction_hook("after_checkpoint_event")
        atomic_write_json(
            pointer_path,
            _checkpoint_pointer(
                checkpoint, checkpoint_path, event, event_path,
            ),
        )
        if transaction_hook is not None:
            transaction_hook("after_checkpoint_pointer")
        return checkpoint, event

    def _ensure_resume_event(
        self, first_pending_execution_id: str, *,
        preflight_started_ns: int, preflight_finished_ns: int,
    ) -> Dict[str, Any]:
        events = self._load_resume_events()
        matching = [
            event for event in events
            if event["first_pending_execution_id"]
            == first_pending_execution_id
        ]
        if len(matching) > 1:
            raise RTA4PilotExecutionError(
                "multiple resume events bind the same pending execution"
            )
        if matching:
            return matching[0]
        generation = 1 + max(
            (event["resume_generation"] for event in events),
            default=0,
        )
        event = _build_resume_event(
            self.execution_config, generation=generation,
            preflight_started_ns=preflight_started_ns,
            preflight_finished_ns=preflight_finished_ns,
            initialization_milliseconds=_nonnegative_milliseconds(
                preflight_finished_ns - preflight_started_ns
            ),
            first_pending_execution_id=first_pending_execution_id,
        )
        _write_json_once(
            Path(self.execution_config["output_root"])
            / RTA4_PILOT_RESUME_EVENT_DIRECTORY
            / f"{generation:08d}.json",
            event,
        )
        return event

    def _finalize(
        self,
        store_manifest: Mapping[str, Any],
        certificates: Mapping[str, TasksetIdentityCertificate],
        transaction_hook: Callable[[str], None] | None,
    ) -> Mapping[str, Any]:
        root = Path(self.execution_config["output_root"])
        raw = self._load_raw_terminals(certificates)
        if len(raw) != len(self.records):
            raise RTA4PilotExecutionError(
                "finalization requires every immutable raw terminal"
            )
        pointer, checkpoint, _event, checkpoint_events, _orphans = (
            _load_checkpoint_transaction(root)
        )
        if checkpoint["phase"] == "PILOT_COMPLETE":
            return audit_pilot_namespace(root, self.configs)
        if checkpoint["phase"] != "FINALIZING":
            self._commit_checkpoint(
                store_manifest, certificates, phase="FINALIZING",
                triggering_execution_id=raw[-1]["execution_id"],
                transaction_hook=transaction_hook,
            )
            pointer, checkpoint, _event, checkpoint_events, _orphans = (
                _load_checkpoint_transaction(root)
            )
        warnings = runtime_ci_engineering_warnings(
            self.manifest["pilot_manifest_id"], raw,
        )
        checkpoint_overhead: Dict[str, int] = {}
        for event in checkpoint_events:
            execution_id = event["triggering_execution_id"]
            if execution_id is not None:
                checkpoint_overhead[execution_id] = (
                    checkpoint_overhead.get(execution_id, 0)
                    + event["checkpoint_write_milliseconds"]
                )
        resume_overhead = {
            event["first_pending_execution_id"]: event[
                "resume_initialization_milliseconds"
            ]
            for event in self._load_resume_events()
        }
        by_execution = {
            row["execution_id"]: row for row in raw
        }
        finalized = []
        for record in self.records:
            execution_id = str(record.execution_id)
            final = build_pilot_final_terminal(
                by_execution[execution_id],
                checkpoint_overhead_milliseconds=(
                    checkpoint_overhead.get(execution_id, 0)
                ),
                resume_overhead_milliseconds=resume_overhead.get(
                    execution_id, 0,
                ),
                ci_width_engineering_warning=warnings[
                    record.record_id
                ],
            )
            _write_json_once(
                root / RTA4_PILOT_FINAL_TERMINAL_DIRECTORY
                / f"{execution_id}.json",
                final,
            )
            finalized.append(final)
            if transaction_hook is not None:
                transaction_hook("during_finalization")
        observations = build_pilot_observations(
            self.manifest,
            [_terminal_observation_input(row) for row in finalized],
        )
        report = build_pilot_report(self.manifest, observations)
        _write_json_once(root / RTA4_PILOT_OBSERVATIONS, observations)
        _write_json_once(root / RTA4_PILOT_REPORT, report)
        audit_pilot_namespace(
            root, self.configs, require_complete=False,
            reconstruct_store=False,
        )
        self._commit_checkpoint(
            store_manifest, certificates, phase="PILOT_COMPLETE",
            triggering_execution_id=None,
            transaction_hook=transaction_hook,
        )
        audit = audit_pilot_namespace(
            root, self.configs, require_complete=True,
            reconstruct_store=False,
        )
        _write_json_once(root / RTA4_PILOT_AUDIT, audit)
        return audit

    def run(
        self, *, resume: bool = False, validate_only: bool = False,
        max_records: int | None = None,
        certificate_provider: Callable[
            [FormalPlanRecord], TasksetIdentityCertificate
        ] | None = None,
        rta_callback: Callable[..., Mapping[str, Any]] | None = None,
        simulation_callback: Callable[..., Mapping[str, Any]] | None = None,
        use_processes: bool | None = None,
        interrupt_after: int | None = None,
        transaction_hook: Callable[[str], None] | None = None,
    ) -> PilotExecutionSummary:
        is_test = self.execution_config["execution_class"] == (
            RTA4_PILOT_TEST_EXECUTION_CLASS
        )
        if not is_test and any(
            value is not None for value in (
                certificate_provider, rta_callback, simulation_callback,
                interrupt_after, transaction_hook,
            )
        ):
            raise RTA4PilotExecutionError(
                "real pilot refuses injected test hooks"
            )
        if validate_only and not resume:
            resume = True
        resume_started = time.monotonic_ns()
        if resume:
            preflight_audit = self._resume_preflight(
                allow_recovery_artifacts=True,
            )
            self._cleanup_recovery_artifacts()
            preflight_audit = self._resume_preflight(
                allow_recovery_artifacts=False,
            )
            store_manifest, certificates = self._load_store()
        else:
            provider = certificate_provider or PilotTasksetProvider(
                self.configs,
            )
            store_manifest, certificates = self._write_initial_namespace(
                provider, transaction_hook,
            )
            preflight_audit = None
        preflight_finished = time.monotonic_ns()
        root = Path(self.execution_config["output_root"])
        raw_terminals = self._load_raw_terminals(certificates)
        completed = {
            row["execution_id"] for row in raw_terminals
        }
        if validate_only:
            remaining = len(self.records) - len(completed)
            return PilotExecutionSummary(
                self.execution_config["execution_class"],
                self.execution_config["execution_config_id"],
                0, remaining, remaining == 0,
                root / RTA4_PILOT_CHECKPOINT, preflight_audit,
            )
        if max_records is not None and (
            type(max_records) is not int or max_records < 0
        ):
            raise RTA4PilotExecutionError(
                "max_records must be a non-negative plain integer"
            )
        all_pending = [
            record for record in self.records
            if record.execution_id not in completed
        ]
        pending = list(all_pending)
        if max_records is not None:
            pending = pending[:max_records]
        if (
            resume and not all_pending
            and preflight_audit["checkpoint_phase"] == "PILOT_COMPLETE"
        ):
            audit_path = root / RTA4_PILOT_AUDIT
            if not audit_path.is_file():
                _write_json_once(audit_path, preflight_audit)
            return PilotExecutionSummary(
                self.execution_config["execution_class"],
                self.execution_config["execution_config_id"],
                0, 0, True, root / RTA4_PILOT_CHECKPOINT,
                preflight_audit,
            )
        if resume and pending:
            self._ensure_resume_event(
                str(pending[0].execution_id),
                preflight_started_ns=resume_started,
                preflight_finished_ns=preflight_finished,
            )
        if use_processes is None:
            use_processes = not is_test
        if type(use_processes) is not bool:
            raise RTA4PilotExecutionError("use_processes must be boolean")
        if not is_test and not use_processes:
            raise RTA4PilotExecutionError(
                "real pilot requires process workers"
            )
        processed = 0
        pool_type = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        max_in_flight = self.execution_config["max_in_flight"]
        for condition_workers, batch in _execution_batches(
            pending, max_in_flight=max_in_flight,
            default_workers=self.execution_config["default_worker_count"],
        ):
            batch_certificates = []
            worker_roots = []
            for record in batch:
                certificate = _certificate_for_record(
                    record, certificates,
                )
                batch_certificates.append(certificate)
                worker_root = Path(tempfile.mkdtemp(
                    prefix=f"{record.execution_id}.",
                    dir=(
                        root / RTA4_PILOT_WORKER_TRACE_DIRECTORY
                    ),
                ))
                if worker_root.is_symlink():
                    raise RTA4PilotExecutionError(
                        "worker temporary root is a symlink"
                    )
                worker_roots.append(worker_root)
            worker_count = condition_workers
            batch_started = time.monotonic_ns()
            futures: list[Future[Any]] = []
            try:
                with pool_type(max_workers=max(1, worker_count)) as pool:
                    for record, certificate, worker_root in zip(
                        batch, batch_certificates, worker_roots,
                    ):
                        callback = (
                            simulation_callback
                            if record.kind == "simulation" else rta_callback
                        )
                        futures.append(pool.submit(
                            _worker_execute, record, certificate,
                            self.configs[record.core],
                            self.execution_config, callback,
                            str(worker_root),
                        ))
                    results = []
                    for record, certificate, future in zip(
                        batch, batch_certificates, futures,
                    ):
                        try:
                            result = future.result()
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            result = _parent_worker_failure(
                                record, certificate,
                                time.monotonic_ns() - batch_started,
                            )
                        results.append(result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                results = [
                    _parent_worker_failure(
                        record, certificate,
                        time.monotonic_ns() - batch_started,
                    )
                    for record, certificate in zip(
                        batch, batch_certificates,
                    )
                ]
            elapsed_ns = time.monotonic_ns() - batch_started
            throughput = (
                0 if elapsed_ns <= 0 else
                (1000 * len(batch) * 1_000_000_000) // elapsed_ns
            )
            for record, certificate, result, worker_root in zip(
                batch, batch_certificates, results, worker_roots,
            ):
                try:
                    if (
                        result.get("plan_record_id") != record.record_id
                        or result.get("execution_id") != record.execution_id
                        or result.get("taskset_id") != certificate.taskset_id
                    ):
                        raise RTA4PilotExecutionError(
                            "worker result identity mismatch"
                        )
                    metrics = _validate_metrics(
                        result["metrics"], final=False,
                    )
                    metrics[
                        "worker_throughput_milli_records_per_second"
                    ] = throughput
                    simulation_id = result.get("simulation_id")
                    if record.kind == "simulation":
                        _projection, _window, _payload, expected_id = (
                            _simulation_identity(record, certificate)
                        )
                        if simulation_id not in {None, expected_id}:
                            raise RTA4PilotExecutionError(
                                "worker returned a foreign simulation ID"
                            )
                        simulation_id = expected_id
                    trace_size, trace_sha = self._persist_trace(
                        record, certificate,
                        result.get("trace_payload"),
                        simulation_id, worker_root,
                    ) if record.kind == "simulation" else (0, None)
                    metrics["trace_size_bytes"] = trace_size
                    raw = build_pilot_raw_terminal(
                        self.selected[str(record.execution_id)],
                        self.execution_config, certificate, metrics,
                        trace_sha256=trace_sha,
                        simulation_id=simulation_id,
                    )
                    _write_json_once(
                        root / RTA4_PILOT_RAW_TERMINAL_DIRECTORY
                        / f"{record.execution_id}.json",
                        raw,
                    )
                    completed.add(str(record.execution_id))
                    processed += 1
                    if transaction_hook is not None:
                        transaction_hook("after_raw_terminal")
                    should_checkpoint = (
                        len(completed)
                        % self.execution_config[
                            "checkpoint_interval_records"
                        ] == 0
                    )
                    if should_checkpoint:
                        self._commit_checkpoint(
                            store_manifest, certificates,
                            phase="EXECUTING",
                            triggering_execution_id=str(
                                record.execution_id
                            ),
                            transaction_hook=transaction_hook,
                        )
                    if (
                        interrupt_after is not None
                        and processed >= interrupt_after
                    ):
                        if not should_checkpoint:
                            self._commit_checkpoint(
                                store_manifest, certificates,
                                phase="EXECUTING",
                                triggering_execution_id=str(
                                    record.execution_id
                                ),
                                transaction_hook=transaction_hook,
                            )
                        raise RTA4PilotExecutionInterrupted(
                            "deterministic pilot interruption"
                        )
                finally:
                    if worker_root.exists():
                        self._safe_cleanup_worker_root(worker_root)
            for worker_root in worker_roots:
                if worker_root.exists():
                    self._safe_cleanup_worker_root(worker_root)
        if processed:
            _pointer, checkpoint, _event, _events, _orphans = (
                _load_checkpoint_transaction(root)
            )
            if checkpoint["completed_raw_count"] != len(completed):
                self._commit_checkpoint(
                    store_manifest, certificates, phase="EXECUTING",
                    triggering_execution_id=str(
                        pending[processed - 1].execution_id
                    ),
                    transaction_hook=transaction_hook,
                )
        remaining = len(self.records) - len(completed)
        audit = None
        if remaining == 0:
            audit = self._finalize(
                store_manifest, certificates, transaction_hook,
            )
        return PilotExecutionSummary(
            self.execution_config["execution_class"],
            self.execution_config["execution_config_id"],
            processed, remaining, remaining == 0,
            root / RTA4_PILOT_CHECKPOINT, audit,
        )


__all__ = [
    "PILOT_OUTPUT_IO_DEFINITION", "PILOT_RESUME_POLICY",
    "PILOT_THROUGHPUT_DEFINITION", "PilotExecutionRunner",
    "PilotExecutionSummary", "PilotTasksetProvider", "RTA4_PILOT_AUDIT",
    "RTA4_PILOT_AUDIT_DOMAIN", "RTA4_PILOT_AUDIT_VERSION",
    "RTA4_PILOT_CHECKPOINT", "RTA4_PILOT_CHECKPOINT_DOMAIN",
    "RTA4_PILOT_CHECKPOINT_DIRECTORY",
    "RTA4_PILOT_CHECKPOINT_EVENT_DIRECTORY",
    "RTA4_PILOT_CHECKPOINT_EVENT_VERSION",
    "RTA4_PILOT_CHECKPOINT_VERSION", "RTA4_PILOT_EXECUTION_CONFIG",
    "RTA4_PILOT_EXECUTION_CONFIG_DOMAIN",
    "RTA4_PILOT_EXECUTION_CONFIG_VERSION",
    "RTA4_PILOT_EXECUTION_MANIFEST",
    "RTA4_PILOT_EXECUTION_MANIFEST_DOMAIN",
    "RTA4_PILOT_EXECUTION_MANIFEST_VERSION",
    "RTA4_PILOT_FINAL_TERMINAL_DIRECTORY",
    "RTA4_PILOT_FINAL_TERMINAL_DOMAIN",
    "RTA4_PILOT_FINAL_TERMINAL_VERSION",
    "RTA4_PILOT_RAW_TERMINAL_DIRECTORY",
    "RTA4_PILOT_RAW_TERMINAL_DOMAIN",
    "RTA4_PILOT_RAW_TERMINAL_VERSION",
    "RTA4_PILOT_RESUME_EVENT_DIRECTORY",
    "RTA4_PILOT_RUNTIME_CI_RULE_VERSION",
    "RTA4_PILOT_STORE_MANIFEST", "RTA4_PILOT_STORE_MANIFEST_VERSION",
    "RTA4_PILOT_TERMINAL_DIRECTORY", "RTA4_PILOT_TERMINAL_DOMAIN",
    "RTA4_PILOT_TERMINAL_VERSION", "RTA4_PILOT_TEST_EXECUTION_CLASS",
    "RTA4PilotExecutionError", "RTA4PilotExecutionInterrupted",
    "audit_pilot_namespace", "build_pilot_checkpoint",
    "build_pilot_execution_config", "build_pilot_execution_manifest",
    "build_pilot_final_terminal", "build_pilot_raw_terminal",
    "build_pilot_store_manifest", "build_pilot_terminal",
    "build_simulation_support", "compute_pilot_output_io_bytes",
    "pilot_final_terminal_preimage",
    "reconstruct_selected_records", "runtime_ci_engineering_warnings",
    "validate_pilot_audit_document", "validate_pilot_checkpoint",
    "validate_pilot_execution_config", "validate_pilot_execution_manifest",
    "validate_pilot_final_terminal", "validate_pilot_raw_terminal",
    "validate_pilot_terminal",
]
