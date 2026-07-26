"""Reproducible, engineering-only execution for the RTA4 pilot selection.

This namespace deliberately persists no mathematical result.  Workers may
evaluate the existing public RTA/simulator entry points in memory, but the
parent records only timing, resource, timeout, retry, I/O, and provenance
evidence.
"""

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import shutil
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

import yaml

from .constrained_taskset_identity import TasksetIdentityCertificate
from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import (
    RTA4_CORES, canonical_json, domain_hash, validate_rta4_formal_config,
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
)


RTA4_PILOT_EXECUTION_CONFIG_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_EXECUTION_CONFIG_V1"
)
RTA4_PILOT_EXECUTION_MANIFEST_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_EXECUTION_MANIFEST_V1"
)
RTA4_PILOT_CHECKPOINT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_CHECKPOINT_V1"
)
RTA4_PILOT_TERMINAL_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_TERMINAL_V1"
)
RTA4_PILOT_AUDIT_VERSION = "ASAP_BLOCK_V9_3_RTA4_PILOT_AUDIT_V1"
RTA4_PILOT_RUNTIME_CI_RULE_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_RUNTIME_CI_RULE_V1"
)

RTA4_PILOT_EXECUTION_CONFIG_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_EXECUTION_CONFIG:v1"
)
RTA4_PILOT_EXECUTION_MANIFEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_EXECUTION_MANIFEST:v1"
)
RTA4_PILOT_CHECKPOINT_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_CHECKPOINT:v1"
)
RTA4_PILOT_TERMINAL_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_TERMINAL:v1"
)
RTA4_PILOT_AUDIT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_AUDIT:v1"

RTA4_PILOT_TEST_EXECUTION_CLASS = "ENGINEERING_PILOT_TEST"
RTA4_PILOT_EXECUTION_CONFIG = "rta4_pilot_execution_config.json"
RTA4_PILOT_EXECUTION_MANIFEST = "rta4_pilot_execution_manifest.json"
RTA4_PILOT_CHECKPOINT = "rta4_pilot_checkpoint.json"
RTA4_PILOT_AUDIT = "rta4_pilot_audit.json"
RTA4_PILOT_TERMINAL_DIRECTORY = "rta4_pilot_terminals"
RTA4_PILOT_TRACE_DIRECTORY = "rta4_pilot_traces"
RTA4_PILOT_WORKER_TRACE_DIRECTORY = ".rta4_pilot_worker_traces"

PILOT_RESUME_POLICY = "REVALIDATE_PILOT_BINDINGS_SKIP_TERMINALS_V1"
PILOT_THROUGHPUT_DEFINITION = (
    "floor(1000*completed_batch_records/batch_wall_seconds);"
    "zero_if_nonpositive_elapsed"
)
PILOT_OUTPUT_IO_DEFINITION = (
    "canonical_terminal_bytes_plus_record_trace_bytes;"
    "shared_certificates_checkpoints_observations_reports_excluded"
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

_METRIC_FIELDS = frozenset({
    "runtime_wall_milliseconds", "runtime_cpu_milliseconds",
    "peak_rss_bytes", "timed_out", "attempt_count",
    "worker_throughput_milli_records_per_second",
    "checkpoint_overhead_milliseconds", "resume_overhead_milliseconds",
    "simulation_wall_milliseconds", "trace_size_bytes",
    "output_io_bytes", "engineering_error",
    "ci_width_engineering_warning",
})

_TERMINAL_IDENTITY_FIELDS = frozenset({
    "terminal_version", "execution_class", "pilot_manifest_id",
    "execution_config_id", "core", "ordinal", "kind", "plan_record_id",
    "mathematical_request_id", "execution_id", "method",
    "taskset_skeleton_slot_id", "taskset_slot_id", "worker_count",
    "selection_key", "generation_request_id", "taskset_skeleton_id",
    "taskset_id", "taskset_hash", "power_vector_hash",
})

_AUDIT_FIELDS = frozenset({
    "audit_version", "audit_status", "execution_class", "freeze_eligible",
    "pilot_root", "pilot_manifest_id", "execution_config_id",
    "execution_manifest_id", "checkpoint_id", "checkpoint_state",
    "terminal_count", "terminal_set_sha256",
    "taskset_certificate_set_sha256", "source_manifest_id",
    "dependency_manifest_id", "environment_manifest_id",
    "hardware_manifest_id", "simulator_manifest_id",
    "pilot_observations_id", "pilot_report_id", "pilot_closure_id",
    "scientific_results_included", "audit_id",
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


def reconstruct_selected_records(
    configs: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> tuple[FormalPlanRecord, ...]:
    """Reconstruct exact selected plan rows without sampling or adaptation."""

    if set(configs) != set(RTA4_CORES):
        raise RTA4PilotExecutionError("all six configs are required")
    records = []
    for core in RTA4_CORES:
        config = validate_rta4_formal_config(
            configs[core], expected_core=core,
        )
        selected = manifest["selected_records"][core]
        by_ordinal = {int(row["ordinal"]): row for row in selected}
        if len(by_ordinal) != len(selected):
            raise RTA4PilotExecutionError(
                "pilot selection contains duplicate ordinals"
            )
        pending = set(by_ordinal)
        for record in iter_formal_plan(config):
            if record.ordinal not in pending:
                continue
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
            if not pending:
                break
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
                worker_trace_root = (
                    Path(execution_config["output_root"])
                    / RTA4_PILOT_WORKER_TRACE_DIRECTORY
                    / str(record.execution_id)
                )
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
    finally:
        if worker_trace_root is not None:
            shutil.rmtree(worker_trace_root, ignore_errors=True)
            worker_parent = worker_trace_root.parent
            try:
                worker_parent.rmdir()
            except OSError:
                pass
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
        "checkpoint_overhead_milliseconds": 0,
        "resume_overhead_milliseconds": 0,
        "simulation_wall_milliseconds": simulation_wall,
        "trace_size_bytes": 0,
        "output_io_bytes": 0,
        "engineering_error": engineering_error,
        "ci_width_engineering_warning": True,
    }
    if (
        execution_config["execution_class"]
        == RTA4_PILOT_TEST_EXECUTION_CLASS
        and isinstance(overrides, Mapping)
    ):
        for name, value in overrides.items():
            if name not in _METRIC_FIELDS:
                raise RTA4PilotExecutionError(
                    "test metric override contains an unknown field"
                )
            metrics[name] = value
    return {
        "plan_record_id": record.record_id,
        "execution_id": record.execution_id,
        "taskset_id": certificate.taskset_id,
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
            "checkpoint_overhead_milliseconds": 0,
            "resume_overhead_milliseconds": 0,
            "simulation_wall_milliseconds": 0,
            "trace_size_bytes": 0,
            "output_io_bytes": 0,
            "engineering_error": True,
            "ci_width_engineering_warning": True,
        },
    }


def _validate_metrics(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(metrics, Mapping) or set(metrics) != _METRIC_FIELDS:
        raise RTA4PilotExecutionError(
            "pilot worker metrics have an unexpected field set"
        )
    for field in (
        "runtime_wall_milliseconds", "runtime_cpu_milliseconds",
        "peak_rss_bytes", "attempt_count",
        "worker_throughput_milli_records_per_second",
        "checkpoint_overhead_milliseconds", "resume_overhead_milliseconds",
        "simulation_wall_milliseconds", "trace_size_bytes",
        "output_io_bytes",
    ):
        if type(metrics[field]) is not int or metrics[field] < 0:
            raise RTA4PilotExecutionError(
                "pilot metrics must be non-negative plain integers"
            )
    for field in (
        "timed_out", "engineering_error",
        "ci_width_engineering_warning",
    ):
        if type(metrics[field]) is not bool:
            raise RTA4PilotExecutionError(
                "pilot flags must be strict booleans"
            )
    return dict(metrics)


def build_pilot_terminal(
    selected: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    certificate: TasksetIdentityCertificate,
    metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    normalized = _validate_metrics(metrics)
    material = {
        "terminal_version": RTA4_PILOT_TERMINAL_VERSION,
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
        **normalized,
    }
    return {
        **material,
        "terminal_hash": domain_hash(RTA4_PILOT_TERMINAL_DOMAIN, material),
    }


def validate_pilot_terminal(
    document: Mapping[str, Any], selected: Mapping[str, Any],
    execution_config: Mapping[str, Any],
) -> Dict[str, Any]:
    if (
        not isinstance(document, Mapping)
        or set(document) != (
            _TERMINAL_IDENTITY_FIELDS | _METRIC_FIELDS | {"terminal_hash"}
        )
    ):
        raise RTA4PilotExecutionError(
            "pilot terminal has an unexpected field set"
        )
    for field in (
        "core", "ordinal", "kind", "plan_record_id",
        "mathematical_request_id", "execution_id", "method",
        "taskset_skeleton_slot_id", "taskset_slot_id", "worker_count",
        "selection_key",
    ):
        if document[field] != selected[field]:
            raise RTA4PilotExecutionError(
                "pilot terminal selection identity mismatch"
            )
    if (
        document["terminal_version"] != RTA4_PILOT_TERMINAL_VERSION
        or document["execution_class"] != execution_config["execution_class"]
        or document["pilot_manifest_id"]
        != execution_config["pilot_manifest"]["pilot_manifest_id"]
        or document["execution_config_id"]
        != execution_config["execution_config_id"]
    ):
        raise RTA4PilotExecutionError("pilot terminal binding mismatch")
    _validate_metrics({
        field: document[field] for field in _METRIC_FIELDS
    })
    for field in (
        "generation_request_id", "taskset_skeleton_id", "taskset_id",
        "taskset_hash", "power_vector_hash", "terminal_hash",
    ):
        if not isinstance(document[field], str) or len(document[field]) != 64:
            raise RTA4PilotExecutionError(
                "pilot terminal identity is not SHA-256 material"
            )
    material = dict(document)
    observed = material.pop("terminal_hash")
    if observed != domain_hash(RTA4_PILOT_TERMINAL_DOMAIN, material):
        raise RTA4PilotExecutionError("pilot terminal hash mismatch")
    return dict(document)


def _terminal_with_io_size(
    selected: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    certificate: TasksetIdentityCertificate,
    metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    adjusted = dict(metrics)
    trace_size = int(adjusted["trace_size_bytes"])
    for _ in range(16):
        terminal = build_pilot_terminal(
            selected, execution_config, certificate, adjusted,
        )
        size = len(_canonical_json_bytes(terminal)) + trace_size
        if adjusted["output_io_bytes"] == size:
            return terminal
        adjusted["output_io_bytes"] = size
    raise RTA4PilotExecutionError(
        "pilot terminal output byte definition did not converge"
    )


def build_pilot_checkpoint(
    manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    execution_manifest: Mapping[str, Any],
    completed_execution_ids: Iterable[str],
) -> Dict[str, Any]:
    completed = sorted(completed_execution_ids)
    planned = execution_manifest["planned_record_count"]
    if len(completed) > planned or len(set(completed)) != len(completed):
        raise RTA4PilotExecutionError("invalid completed execution ID set")
    material = {
        "checkpoint_version": RTA4_PILOT_CHECKPOINT_VERSION,
        "execution_class": execution_config["execution_class"],
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "execution_config_id": execution_config["execution_config_id"],
        "execution_manifest_id": execution_manifest[
            "execution_manifest_id"
        ],
        "output_root": execution_config["output_root"],
        "taskset_store": execution_config["taskset_store"],
        "source_commit": execution_config["source_manifest"]["git_commit"],
        "source_tree": execution_config["source_manifest"]["git_tree"],
        "simulator_manifest_id": execution_config["simulator_manifest"][
            "manifest_id"
        ],
        "planned_record_count": planned,
        "completed_record_count": len(completed),
        "completed_execution_ids": completed,
        "completed_set_sha256": hashlib.sha256(
            canonical_json(completed).encode("utf-8")
        ).hexdigest(),
        "state": (
            "PILOT_COMPLETE"
            if len(completed) == planned else "INCOMPLETE_PILOT"
        ),
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
    execution_manifest: Mapping[str, Any],
    completed_execution_ids: Iterable[str],
) -> Dict[str, Any]:
    expected = build_pilot_checkpoint(
        manifest, execution_config, execution_manifest,
        completed_execution_ids,
    )
    if dict(document) != expected:
        raise RTA4PilotExecutionError(
            "pilot checkpoint/terminal inventory mismatch"
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


def _terminal_paths(root: Path) -> tuple[Path, ...]:
    terminal_root = root / RTA4_PILOT_TERMINAL_DIRECTORY
    if not terminal_root.is_dir():
        return ()
    entries = tuple(sorted(terminal_root.iterdir()))
    if any(
        not path.is_file()
        or path.suffix != ".json"
        or len(path.stem) != 64
        for path in entries
    ):
        raise RTA4PilotExecutionError(
            "pilot terminal directory contains an unexpected entry"
        )
    return entries


def _audit_material(
    *, root: Path, manifest: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    execution_manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    terminals: Sequence[Mapping[str, Any]],
    observations: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    complete = checkpoint["state"] == "PILOT_COMPLETE"
    eligible = (
        complete
        and execution_config["execution_class"]
        == RTA4_PILOT_EXECUTION_CLASS
        and observations is not None and report is not None
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
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_state": checkpoint["state"],
        "terminal_count": len(terminals),
        "terminal_set_sha256": hashlib.sha256(
            canonical_json(sorted(
                terminal["terminal_hash"] for terminal in terminals
            )).encode("utf-8")
        ).hexdigest(),
        "taskset_certificate_set_sha256": hashlib.sha256(
            canonical_json(sorted({
                terminal["taskset_id"] for terminal in terminals
            })).encode("utf-8")
        ).hexdigest(),
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
        "scientific_results_included": False,
    }


def validate_pilot_audit_document(
    document: Mapping[str, Any],
) -> Dict[str, Any]:
    if (
        not isinstance(document, Mapping)
        or set(document) != _AUDIT_FIELDS
    ):
        raise RTA4PilotExecutionError(
            "pilot audit has an unexpected field set"
        )
    material = dict(document)
    observed = material.pop("audit_id", None)
    if (
        material.get("audit_version") != RTA4_PILOT_AUDIT_VERSION
        or material.get("scientific_results_included") is not False
        or material.get("execution_class") not in {
            RTA4_PILOT_EXECUTION_CLASS, RTA4_PILOT_TEST_EXECUTION_CLASS,
        }
    ):
        raise RTA4PilotExecutionError("pilot audit contract mismatch")
    if (
        type(material["freeze_eligible"]) is not bool
        or type(material["terminal_count"]) is not int
        or material["terminal_count"] < 0
        or material["checkpoint_state"] not in {
            "INCOMPLETE_PILOT", "PILOT_COMPLETE",
        }
        or material["audit_status"] not in {
            "ENGINEERING_PILOT_AUDIT_PARTIAL",
            "ENGINEERING_PILOT_AUDIT_COMPLETE",
        }
    ):
        raise RTA4PilotExecutionError("pilot audit state is invalid")
    expected_status = (
        "ENGINEERING_PILOT_AUDIT_COMPLETE"
        if material["checkpoint_state"] == "PILOT_COMPLETE"
        else "ENGINEERING_PILOT_AUDIT_PARTIAL"
    )
    if material["audit_status"] != expected_status:
        raise RTA4PilotExecutionError(
            "pilot audit status/checkpoint state mismatch"
        )
    try:
        pilot_root = Path(material["pilot_root"])
    except TypeError as exc:
        raise RTA4PilotExecutionError(
            "pilot audit root is invalid"
        ) from exc
    if not pilot_root.is_absolute():
        raise RTA4PilotExecutionError(
            "pilot audit root must be absolute"
        )
    required_ids = (
        "pilot_manifest_id", "execution_config_id",
        "execution_manifest_id", "checkpoint_id",
        "terminal_set_sha256", "taskset_certificate_set_sha256",
        "source_manifest_id", "dependency_manifest_id",
        "environment_manifest_id", "hardware_manifest_id",
        "simulator_manifest_id",
    )
    optional_ids = (
        "pilot_observations_id", "pilot_report_id", "pilot_closure_id",
    )
    if any(
        not isinstance(material[field], str)
        or len(material[field]) != 64
        for field in required_ids
    ) or any(
        value is not None
        and (not isinstance(value, str) or len(value) != 64)
        for value in (material[field] for field in optional_ids)
    ):
        raise RTA4PilotExecutionError(
            "pilot audit identity is not SHA-256 material"
        )
    if observed != domain_hash(RTA4_PILOT_AUDIT_DOMAIN, material):
        raise RTA4PilotExecutionError("pilot audit ID mismatch")
    complete = material["checkpoint_state"] == "PILOT_COMPLETE"
    final_ids_present = all(
        material[field] is not None for field in optional_ids
    )
    if complete != final_ids_present:
        raise RTA4PilotExecutionError(
            "pilot audit final evidence/checkpoint state mismatch"
        )
    expected_eligibility = (
        complete
        and material["execution_class"] == RTA4_PILOT_EXECUTION_CLASS
        and final_ids_present
    )
    if material["freeze_eligible"] is not expected_eligibility:
        raise RTA4PilotExecutionError(
            "pilot audit freeze eligibility is inconsistent"
        )
    return dict(document)


def audit_pilot_namespace(
    root: Path | str,
    configs: Mapping[str, Mapping[str, Any]], *,
    require_complete: bool = True,
) -> Dict[str, Any]:
    """Independently rebuild manifest, terminal, store, checkpoint and report."""

    output = Path(root).resolve(strict=True)
    allowed_root_entries = {
        RTA4_PILOT_OUTPUT_MARKER, RTA4_PILOT_EXECUTION_CONFIG,
        RTA4_PILOT_EXECUTION_MANIFEST, RTA4_PILOT_CHECKPOINT,
        RTA4_PILOT_TERMINAL_DIRECTORY, RTA4_PILOT_TRACE_DIRECTORY,
        RTA4_PILOT_OBSERVATIONS, RTA4_PILOT_REPORT, RTA4_PILOT_AUDIT,
    }
    extras = {
        path.name for path in output.iterdir()
    } - allowed_root_entries
    if extras:
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
    selected = {
        row["execution_id"]: row for row in _selected_rows(manifest)
    }
    paths = _terminal_paths(output)
    if any(path.stem not in selected for path in paths):
        raise RTA4PilotExecutionError(
            "pilot terminal filename is outside selection"
        )
    terminals = []
    seen: set[str] = set()
    store = Path(execution_config["taskset_store"])
    marker = store / FORMAL_TASKSET_STORE_MANIFEST
    if not marker.is_file():
        raise RTA4PilotExecutionError("pilot taskset store marker is missing")
    store_entries = {path.name for path in store.iterdir()}
    if store_entries != {
        FORMAL_TASKSET_STORE_MANIFEST, "certificates",
    }:
        raise RTA4PilotExecutionError(
            "pilot taskset store contains an unexpected entry"
        )
    for path in paths:
        if path.stem in seen:
            raise RTA4PilotExecutionError("duplicate pilot terminal")
        terminal = validate_pilot_terminal(
            _load_json(path), selected[path.stem], execution_config,
        )
        if terminal["execution_id"] != path.stem:
            raise RTA4PilotExecutionError(
                "pilot terminal filename/payload mismatch"
            )
        certificate_path = (
            store / "certificates" / f"{terminal['taskset_id']}.json"
        )
        try:
            certificate = TasksetIdentityCertificate.from_canonical_bytes(
                certificate_path.read_bytes()
            )
            certificate.validate()
        except Exception as exc:
            raise RTA4PilotExecutionError(
                "pilot taskset certificate is missing or damaged"
            ) from exc
        for field in (
            "generation_request_id", "taskset_skeleton_id", "taskset_id",
            "taskset_hash", "power_vector_hash",
        ):
            if getattr(certificate, field) != terminal[field]:
                raise RTA4PilotExecutionError(
                    "terminal/taskset certificate identity mismatch"
                )
        terminals.append(terminal)
        seen.add(path.stem)
    trace_root = output / RTA4_PILOT_TRACE_DIRECTORY
    worker_trace_root = output / RTA4_PILOT_WORKER_TRACE_DIRECTORY
    if worker_trace_root.exists():
        raise RTA4PilotExecutionError(
            "pilot namespace contains worker-side temporary evidence"
        )
    if trace_root.is_dir() and any(
        not path.is_file()
        or path.suffix != ".json"
        or len(path.stem) != 64
        for path in trace_root.iterdir()
    ):
        raise RTA4PilotExecutionError(
            "pilot trace directory contains an unexpected entry"
        )
    trace_paths = (
        {path.stem: path for path in trace_root.glob("*.json")}
        if trace_root.is_dir() else {}
    )
    expected_trace_ids = {
        terminal["execution_id"]
        for terminal in terminals if terminal["kind"] == "simulation"
    }
    if set(trace_paths) != expected_trace_ids:
        raise RTA4PilotExecutionError(
            "pilot trace inventory differs from simulation terminals"
        )
    for terminal in terminals:
        trace_size = (
            trace_paths[terminal["execution_id"]].stat().st_size
            if terminal["kind"] == "simulation" else 0
        )
        if terminal["trace_size_bytes"] != trace_size:
            raise RTA4PilotExecutionError(
                "pilot terminal trace byte count mismatch"
            )
        if terminal["output_io_bytes"] != (
            len(_canonical_json_bytes(terminal)) + trace_size
        ):
            raise RTA4PilotExecutionError(
                "pilot terminal output I/O byte definition mismatch"
            )
    checkpoint = validate_pilot_checkpoint(
        _load_json(output / RTA4_PILOT_CHECKPOINT),
        manifest, execution_config, execution_manifest, seen,
    )
    complete = checkpoint["state"] == "PILOT_COMPLETE"
    if require_complete and not complete:
        raise RTA4PilotExecutionError("partial pilot cannot pass final audit")
    observations = None
    report = None
    if complete:
        if set(seen) != set(selected):
            raise RTA4PilotExecutionError(
                "complete pilot terminal inventory is incomplete"
            )
        certificate_root = store / "certificates"
        certificate_entries = (
            tuple(sorted(certificate_root.iterdir()))
            if certificate_root.is_dir() else ()
        )
        if any(
            not path.is_file()
            or path.suffix != ".json"
            or len(path.stem) != 64
            for path in certificate_entries
        ) or {path.stem for path in certificate_entries} != {
            terminal["taskset_id"] for terminal in terminals
        }:
            raise RTA4PilotExecutionError(
                "complete pilot taskset certificate inventory differs"
            )
        warnings = runtime_ci_engineering_warnings(
            manifest["pilot_manifest_id"], terminals,
        )
        if any(
            terminal["ci_width_engineering_warning"]
            != warnings[terminal["plan_record_id"]]
            for terminal in terminals
        ):
            raise RTA4PilotExecutionError(
                "terminal runtime CI warning cannot be reconstructed"
            )
        observations = validate_pilot_observations(
            _load_json(output / RTA4_PILOT_OBSERVATIONS), manifest,
        )
        rebuilt = build_pilot_observations(
            manifest,
            [_terminal_observation_input(row) for row in terminals],
        )
        if observations != rebuilt:
            raise RTA4PilotExecutionError(
                "pilot observations differ from terminal reconstruction"
            )
        report = validate_pilot_report(
            _load_json(output / RTA4_PILOT_REPORT),
            manifest, observations,
        )
    else:
        if (
            (output / RTA4_PILOT_OBSERVATIONS).exists()
            or (output / RTA4_PILOT_REPORT).exists()
        ):
            raise RTA4PilotExecutionError(
                "partial pilot published final observations/report"
            )
    material = _audit_material(
        root=output, manifest=manifest,
        execution_config=execution_config,
        execution_manifest=execution_manifest, checkpoint=checkpoint,
        terminals=terminals, observations=observations, report=report,
    )
    audit = {
        **material,
        "audit_id": domain_hash(RTA4_PILOT_AUDIT_DOMAIN, material),
    }
    normalized_audit = validate_pilot_audit_document(audit)
    audit_path = output / RTA4_PILOT_AUDIT
    if audit_path.is_file() and _load_json(audit_path) != normalized_audit:
        raise RTA4PilotExecutionError(
            "persisted pilot audit differs from reconstruction"
        )
    return normalized_audit


class PilotExecutionRunner:
    """Bounded process execution with deterministic parent persistence."""

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

    def _write_initial_namespace(self) -> None:
        root = Path(self.execution_config["output_root"])
        root.mkdir(parents=True, exist_ok=True)
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
        config_path = root / RTA4_PILOT_EXECUTION_CONFIG
        if config_path.is_file():
            if _load_json(config_path) != self.execution_config:
                raise RTA4PilotExecutionError(
                    "pilot execution config copy differs"
                )
        else:
            atomic_write_json(config_path, self.execution_config)
        atomic_write_json(
            root / RTA4_PILOT_EXECUTION_MANIFEST,
            self.execution_manifest,
        )
        (root / RTA4_PILOT_TERMINAL_DIRECTORY).mkdir(
            parents=True, exist_ok=True,
        )
        checkpoint = build_pilot_checkpoint(
            self.manifest, self.execution_config,
            self.execution_manifest, (),
        )
        atomic_write_json(root / RTA4_PILOT_CHECKPOINT, checkpoint)

    def _resume_preflight(self) -> Mapping[str, Any]:
        return audit_pilot_namespace(
            self.execution_config["output_root"], self.configs,
            require_complete=False,
        )

    def _persist_trace(
        self, execution_id: str, payload: bytes | None,
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
        trace_root = (
            Path(self.execution_config["output_root"])
            / RTA4_PILOT_TRACE_DIRECTORY
        )
        trace_root.mkdir(parents=True, exist_ok=True)
        target = trace_root / f"{execution_id}.json"
        atomic_write_text(target, text)
        size = target.stat().st_size
        return size, str(target)

    def _rewrite_terminal(
        self, execution_id: str,
        certificate: TasksetIdentityCertificate,
        metrics: Mapping[str, Any],
    ) -> Dict[str, Any]:
        terminal = _terminal_with_io_size(
            self.selected[execution_id], self.execution_config,
            certificate, metrics,
        )
        path = (
            Path(self.execution_config["output_root"])
            / RTA4_PILOT_TERMINAL_DIRECTORY / f"{execution_id}.json"
        )
        atomic_write_json(path, terminal)
        return terminal

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
    ) -> PilotExecutionSummary:
        is_test = self.execution_config["execution_class"] == (
            RTA4_PILOT_TEST_EXECUTION_CLASS
        )
        if not is_test and any(
            value is not None for value in (
                certificate_provider, rta_callback, simulation_callback,
                interrupt_after,
            )
        ):
            raise RTA4PilotExecutionError(
                "real pilot refuses injected test hooks"
            )
        if validate_only and not resume:
            resume = True
        resume_started = time.monotonic_ns()
        if resume:
            preflight_audit = self._resume_preflight()
        else:
            self._write_initial_namespace()
            preflight_audit = None
        resume_overhead = (
            _nonnegative_milliseconds(time.monotonic_ns() - resume_started)
            if resume else 0
        )
        root = Path(self.execution_config["output_root"])
        terminal_paths = _terminal_paths(root)
        completed = {path.stem for path in terminal_paths}
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
        pending = [
            record for record in self.records
            if record.execution_id not in completed
        ]
        if max_records is not None:
            pending = pending[:max_records]
        if resume and not pending and len(completed) == len(self.records):
            return PilotExecutionSummary(
                self.execution_config["execution_class"],
                self.execution_config["execution_config_id"],
                0, 0, True, root / RTA4_PILOT_CHECKPOINT,
                preflight_audit,
            )
        if use_processes is None:
            use_processes = not is_test
        if type(use_processes) is not bool:
            raise RTA4PilotExecutionError("use_processes must be boolean")
        if not is_test and not use_processes:
            raise RTA4PilotExecutionError(
                "real pilot requires process workers"
            )
        provider = certificate_provider or PilotTasksetProvider(self.configs)
        store = RTA4FormalTasksetStore(
            self.execution_config["taskset_store"]
        )
        certificates: Dict[str, TasksetIdentityCertificate] = {}
        processed = 0
        first_pending = True
        pool_type = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
        max_in_flight = self.execution_config["max_in_flight"]
        for condition_workers, batch in _execution_batches(
            pending, max_in_flight=max_in_flight,
            default_workers=self.execution_config["default_worker_count"],
        ):
            batch_certificates = []
            for record in batch:
                certificate = provider(record)
                if type(certificate) is not TasksetIdentityCertificate:
                    raise RTA4PilotExecutionError(
                        "pilot provider must return a PR-B certificate"
                    )
                RTA4FormalRunner(
                    self.configs[record.core]
                )._validate_plan_certificate(record, certificate)
                store.put(certificate)
                certificates[str(record.execution_id)] = certificate
                batch_certificates.append(certificate)
            worker_count = condition_workers
            batch_started = time.monotonic_ns()
            futures: list[Future[Any]] = []
            with pool_type(max_workers=max(1, worker_count)) as pool:
                for record, certificate in zip(
                    batch, batch_certificates,
                ):
                    callback = (
                        simulation_callback
                        if record.kind == "simulation" else rta_callback
                    )
                    futures.append(pool.submit(
                        _worker_execute, record, certificate,
                        self.configs[record.core],
                        self.execution_config, callback,
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
            elapsed_ns = time.monotonic_ns() - batch_started
            throughput = (
                0 if elapsed_ns <= 0 else
                (1000 * len(batch) * 1_000_000_000) // elapsed_ns
            )
            for record, certificate, result in zip(
                batch, batch_certificates, results,
            ):
                if (
                    result.get("plan_record_id") != record.record_id
                    or result.get("execution_id") != record.execution_id
                    or result.get("taskset_id") != certificate.taskset_id
                ):
                    raise RTA4PilotExecutionError(
                        "worker result identity mismatch"
                    )
                metrics = _validate_metrics(result["metrics"])
                metrics["worker_throughput_milli_records_per_second"] = (
                    throughput
                )
                if first_pending:
                    metrics["resume_overhead_milliseconds"] = (
                        resume_overhead
                    )
                    first_pending = False
                trace_size, _trace_path = self._persist_trace(
                    str(record.execution_id), result.get("trace_payload"),
                )
                metrics["trace_size_bytes"] = trace_size
                terminal = self._rewrite_terminal(
                    str(record.execution_id), certificate, metrics,
                )
                completed.add(str(record.execution_id))
                processed += 1
                should_checkpoint = (
                    len(completed)
                    % self.execution_config[
                        "checkpoint_interval_records"
                    ] == 0
                )
                if should_checkpoint:
                    checkpoint = build_pilot_checkpoint(
                        self.manifest, self.execution_config,
                        self.execution_manifest, completed,
                    )
                    checkpoint_started = time.monotonic_ns()
                    atomic_write_json(
                        root / RTA4_PILOT_CHECKPOINT, checkpoint,
                    )
                    metrics["checkpoint_overhead_milliseconds"] += (
                        _nonnegative_milliseconds(
                            time.monotonic_ns() - checkpoint_started
                        )
                    )
                    terminal = self._rewrite_terminal(
                        str(record.execution_id), certificate, metrics,
                    )
                if (
                    interrupt_after is not None
                    and processed >= interrupt_after
                ):
                    if not should_checkpoint:
                        checkpoint = build_pilot_checkpoint(
                            self.manifest, self.execution_config,
                            self.execution_manifest, completed,
                        )
                        checkpoint_started = time.monotonic_ns()
                        atomic_write_json(
                            root / RTA4_PILOT_CHECKPOINT, checkpoint,
                        )
                        metrics[
                            "checkpoint_overhead_milliseconds"
                        ] += _nonnegative_milliseconds(
                            time.monotonic_ns() - checkpoint_started
                        )
                        self._rewrite_terminal(
                            str(record.execution_id),
                            certificate, metrics,
                        )
                    raise RTA4PilotExecutionInterrupted(
                        "deterministic pilot interruption"
                    )
        checkpoint = build_pilot_checkpoint(
            self.manifest, self.execution_config,
            self.execution_manifest, completed,
        )
        checkpoint_is_current = (
            processed > 0
            and len(completed) % self.execution_config[
                "checkpoint_interval_records"
            ] == 0
        )
        if processed and not checkpoint_is_current:
            last_id = str(pending[processed - 1].execution_id)
            last_path = (
                root / RTA4_PILOT_TERMINAL_DIRECTORY / f"{last_id}.json"
            )
            last = validate_pilot_terminal(
                _load_json(last_path), self.selected[last_id],
                self.execution_config,
            )
            last_metrics = {
                field: last[field] for field in _METRIC_FIELDS
            }
            checkpoint_started = time.monotonic_ns()
            atomic_write_json(root / RTA4_PILOT_CHECKPOINT, checkpoint)
            last_metrics["checkpoint_overhead_milliseconds"] += (
                _nonnegative_milliseconds(
                    time.monotonic_ns() - checkpoint_started
                )
            )
            self._rewrite_terminal(
                last_id, certificates[last_id], last_metrics,
            )
        remaining = len(self.records) - len(completed)
        audit = None
        if remaining == 0:
            terminal_documents = [
                validate_pilot_terminal(
                    _load_json(path), self.selected[path.stem],
                    self.execution_config,
                )
                for path in _terminal_paths(root)
            ]
            warnings = runtime_ci_engineering_warnings(
                self.manifest["pilot_manifest_id"], terminal_documents,
            )
            finalized = []
            for terminal in terminal_documents:
                execution_id = terminal["execution_id"]
                certificate = (
                    certificates.get(execution_id)
                    or TasksetIdentityCertificate.from_canonical_bytes(
                        (
                            Path(self.execution_config["taskset_store"])
                            / "certificates"
                            / f"{terminal['taskset_id']}.json"
                        ).read_bytes()
                    )
                )
                metrics = {
                    field: terminal[field] for field in _METRIC_FIELDS
                }
                metrics["ci_width_engineering_warning"] = warnings[
                    terminal["plan_record_id"]
                ]
                finalized.append(self._rewrite_terminal(
                    execution_id, certificate, metrics,
                ))
            observations = build_pilot_observations(
                self.manifest,
                [_terminal_observation_input(row) for row in finalized],
            )
            report = build_pilot_report(self.manifest, observations)
            atomic_write_json(root / RTA4_PILOT_OBSERVATIONS, observations)
            atomic_write_json(root / RTA4_PILOT_REPORT, report)
            audit = audit_pilot_namespace(
                root, self.configs, require_complete=True,
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
    "RTA4_PILOT_CHECKPOINT_VERSION", "RTA4_PILOT_EXECUTION_CONFIG",
    "RTA4_PILOT_EXECUTION_CONFIG_DOMAIN",
    "RTA4_PILOT_EXECUTION_CONFIG_VERSION",
    "RTA4_PILOT_EXECUTION_MANIFEST",
    "RTA4_PILOT_EXECUTION_MANIFEST_DOMAIN",
    "RTA4_PILOT_EXECUTION_MANIFEST_VERSION",
    "RTA4_PILOT_RUNTIME_CI_RULE_VERSION",
    "RTA4_PILOT_TERMINAL_DIRECTORY", "RTA4_PILOT_TERMINAL_DOMAIN",
    "RTA4_PILOT_TERMINAL_VERSION", "RTA4_PILOT_TEST_EXECUTION_CLASS",
    "RTA4PilotExecutionError", "RTA4PilotExecutionInterrupted",
    "audit_pilot_namespace", "build_pilot_checkpoint",
    "build_pilot_execution_config", "build_pilot_execution_manifest",
    "build_pilot_terminal", "build_simulation_support",
    "reconstruct_selected_records", "runtime_ci_engineering_warnings",
    "validate_pilot_audit_document", "validate_pilot_checkpoint",
    "validate_pilot_execution_config", "validate_pilot_execution_manifest",
    "validate_pilot_terminal",
]
