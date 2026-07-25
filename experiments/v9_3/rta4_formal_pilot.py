"""Result-independent RTA4 pilot selection and engineering-only evidence."""

from __future__ import annotations

import hashlib
import heapq
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .rta4_formal_config import (
    RTA4_CORES, RTA4_FORMAL_PROFILE, canonical_json, domain_hash,
    default_rta4_formal_config, rta4_formal_config_hash,
    validate_rta4_formal_config,
)
from .rta4_formal_plan import FormalPlanRecord, iter_formal_plan


RTA4_PILOT_VERSION = "ASAP_BLOCK_V9_3_RTA4_PILOT_V1"
RTA4_PILOT_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_CONFIG:v1"
RTA4_PILOT_MANIFEST_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_MANIFEST:v1"
RTA4_PILOT_CLOSURE_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_CLOSURE:v1"
RTA4_PILOT_REPORT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_REPORT:v1"
RTA4_PILOT_EXECUTION_CLASS = "ENGINEERING_PILOT"
RTA4_PILOT_OUTPUT_MARKER = "rta4_pilot_manifest.json"
RTA4_PILOT_REPORT = "rta4_pilot_report.json"


class RTA4PilotError(ValueError):
    """Raised when pilot selection or evidence can influence science."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_config_evidence(
    configs: Mapping[str, Mapping[str, Any]], *,
    config_paths: Mapping[str, Path | str] | None = None,
) -> Dict[str, Any]:
    if set(configs) != set(RTA4_CORES):
        raise RTA4PilotError("pilot requires all six pre-pilot configurations")
    rows: Dict[str, Any] = {}
    for core in RTA4_CORES:
        normalized = validate_rta4_formal_config(configs[core], expected_core=core)
        row = {
            "config_semantic_hash": rta4_formal_config_hash(normalized),
        }
        if config_paths is not None:
            if core not in config_paths:
                raise RTA4PilotError("config path set is incomplete")
            path = Path(config_paths[core]).resolve(strict=True)
            row.update({
                "absolute_path": str(path),
                "file_sha256": _sha256(path),
            })
        rows[core] = row
    return rows


def _selection_key(record: FormalPlanRecord, seed: str) -> str:
    return domain_hash("ASAP_BLOCK:V9.3:RTA4_PILOT_SELECTION:v1", {
        "pilot_version": RTA4_PILOT_VERSION,
        "seed": seed,
        "core": record.core,
        "plan_record_id": record.record_id,
    })


def _select(
    records: Iterable[FormalPlanRecord], count: int, seed: str,
) -> Tuple[FormalPlanRecord, ...]:
    # Keep the lexicographically smallest domain hashes without materializing
    # the full scientific plan.
    heap: list[tuple[int, int, FormalPlanRecord]] = []
    for record in records:
        rank = int(_selection_key(record, seed), 16)
        item = (-rank, -record.ordinal, record)
        if len(heap) < count:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    if len(heap) != count:
        raise RTA4PilotError("pilot scale exceeds a trusted core plan")
    return tuple(sorted((item[2] for item in heap), key=lambda row: row.ordinal))


@lru_cache(maxsize=48)
def _default_selection(
    core: str, count: int, seed: str,
) -> Tuple[FormalPlanRecord, ...]:
    return _select(
        iter_formal_plan(default_rta4_formal_config(core)), count, seed,
    )


def build_pilot_manifest(
    configs: Mapping[str, Mapping[str, Any]], *,
    core_record_counts: Mapping[str, int],
    selection_seed: str,
    output_root: Path | str,
    taskset_store: Path | str,
    config_paths: Mapping[str, Path | str] | None = None,
) -> Dict[str, Any]:
    """Build a deterministic engineering sample independent of all outcomes."""

    if set(core_record_counts) != set(RTA4_CORES):
        raise RTA4PilotError("pilot scale must explicitly cover all six cores")
    if not isinstance(selection_seed, str) or not selection_seed:
        raise RTA4PilotError("pilot selection seed must be non-empty")
    output = Path(output_root).resolve()
    store = Path(taskset_store).resolve()
    if output == store:
        raise RTA4PilotError("pilot output and taskset store must be isolated")
    selected: Dict[str, Any] = {}
    for core in RTA4_CORES:
        count = core_record_counts[core]
        if type(count) is not int or isinstance(count, bool) or count < 1:
            raise RTA4PilotError("pilot record counts must be positive integers")
        normalized = validate_rta4_formal_config(
            configs[core], expected_core=core,
        )
        # Every path/resume variant has the same exact scientific plan.  The
        # validator above proves that contract before this cached selection.
        rows = _default_selection(core, count, selection_seed)
        selected[core] = [
            {
                "ordinal": row.ordinal,
                "plan_record_id": row.record_id,
                "kind": row.kind,
                "mathematical_request_id": row.mathematical_request_id,
                "execution_id": row.execution_id,
                "method": str(row.material.get("method", "NA")),
                "selection_key": _selection_key(row, selection_seed),
            }
            for row in rows
        ]
    pilot_config = {
        "pilot_version": RTA4_PILOT_VERSION,
        "profile": RTA4_FORMAL_PROFILE,
        "execution_class": RTA4_PILOT_EXECUTION_CLASS,
        "selection_rule": "DOMAIN_HASH_LOWEST_RESULT_INDEPENDENT_V1",
        "selection_seed": selection_seed,
        "pilot_scale": {
            core: core_record_counts[core] for core in RTA4_CORES
        },
        "output_root": str(output),
        "taskset_store": str(store),
        "source_configs": source_config_evidence(
            configs, config_paths=config_paths,
        ),
        "scientific_interpretation": "FORBIDDEN_ENGINEERING_METRICS_ONLY",
    }
    material = {
        **pilot_config,
        "pilot_config_hash": domain_hash(
            RTA4_PILOT_CONFIG_DOMAIN, pilot_config,
        ),
        "selected_records": selected,
    }
    return {
        **material,
        "pilot_manifest_id": domain_hash(RTA4_PILOT_MANIFEST_DOMAIN, material),
    }


def validate_pilot_manifest(
    manifest: Mapping[str, Any],
    configs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise RTA4PilotError("pilot manifest must be a mapping")
    try:
        source_rows = manifest["source_configs"]
        with_paths = all(
            isinstance(source_rows.get(core), Mapping)
            and "absolute_path" in source_rows[core]
            for core in RTA4_CORES
        )
        expected = build_pilot_manifest(
            configs,
            core_record_counts=manifest["pilot_scale"],
            selection_seed=manifest["selection_seed"],
            output_root=manifest["output_root"],
            taskset_store=manifest["taskset_store"],
            config_paths=(
                {
                    core: source_rows[core]["absolute_path"]
                    for core in RTA4_CORES
                }
                if with_paths else None
            ),
        )
    except Exception as exc:
        raise RTA4PilotError("cannot reconstruct pilot manifest") from exc
    if dict(manifest) != expected:
        raise RTA4PilotError("pilot manifest mismatch")
    return dict(manifest)


def _percentile(values: Sequence[int], numerator: int, denominator: int) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = ((len(ordered) - 1) * numerator + denominator - 1) // denominator
    return ordered[index]


def build_pilot_report(
    manifest: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Summarize runtime/memory/timeout evidence without result statistics."""

    expected = {
        row["plan_record_id"]
        for core in RTA4_CORES
        for row in manifest["selected_records"][core]
    }
    if len(observations) != len(expected):
        raise RTA4PilotError("pilot observation count mismatch")
    seen: set[str] = set()
    normalized = []
    exact_fields = {
        "plan_record_id", "runtime_wall_milliseconds",
        "runtime_cpu_milliseconds", "peak_rss_bytes", "timed_out",
        "attempt_count", "worker_throughput_milli_records_per_second",
        "checkpoint_overhead_milliseconds", "resume_overhead_milliseconds",
        "simulation_wall_milliseconds", "trace_size_bytes",
        "output_io_bytes", "engineering_error",
        "ci_width_engineering_warning",
    }
    for raw in observations:
        if set(raw) != exact_fields:
            raise RTA4PilotError("pilot observation has an unexpected field set")
        record_id = raw["plan_record_id"]
        if record_id not in expected or record_id in seen:
            raise RTA4PilotError("pilot observation membership mismatch")
        for name in (
            "runtime_wall_milliseconds", "runtime_cpu_milliseconds",
            "peak_rss_bytes", "attempt_count",
            "worker_throughput_milli_records_per_second",
            "checkpoint_overhead_milliseconds", "resume_overhead_milliseconds",
            "simulation_wall_milliseconds", "trace_size_bytes",
            "output_io_bytes",
        ):
            if type(raw[name]) is not int or raw[name] < 0:
                raise RTA4PilotError("pilot engineering metrics must be non-negative integers")
        if any(
            type(raw[name]) is not bool
            for name in (
                "timed_out", "engineering_error",
                "ci_width_engineering_warning",
            )
        ):
            raise RTA4PilotError("pilot engineering flags must be strict booleans")
        seen.add(record_id)
        normalized.append(dict(raw))
    normalized.sort(key=lambda row: row["plan_record_id"])
    runtimes = [row["runtime_wall_milliseconds"] for row in normalized]
    cpu = [row["runtime_cpu_milliseconds"] for row in normalized]
    rss = [row["peak_rss_bytes"] for row in normalized]
    observation_sha256 = hashlib.sha256(
        canonical_json(normalized).encode("utf-8")
    ).hexdigest()
    method_for = {
        row["plan_record_id"]: row["method"]
        for core in RTA4_CORES
        for row in manifest["selected_records"][core]
    }
    per_method = {}
    for method in sorted(set(method_for.values())):
        method_rows = [
            row for row in normalized
            if method_for[row["plan_record_id"]] == method
        ]
        per_method[method] = {
            "observation_count": len(method_rows),
            "runtime_wall_milliseconds": sum(
                row["runtime_wall_milliseconds"] for row in method_rows
            ),
            "runtime_cpu_milliseconds": sum(
                row["runtime_cpu_milliseconds"] for row in method_rows
            ),
        }
    pilot_closure_id = domain_hash(RTA4_PILOT_CLOSURE_DOMAIN, {
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "observation_sha256": observation_sha256,
        "observation_count": len(normalized),
    })
    material = {
        "pilot_version": RTA4_PILOT_VERSION,
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "pilot_status": "PILOT_COMPLETE_ENGINEERING_ONLY",
        "pilot_closure_id": pilot_closure_id,
        "observation_count": len(normalized),
        "engineering_metrics": {
            "runtime_wall_milliseconds_p50": _percentile(runtimes, 1, 2),
            "runtime_wall_milliseconds_p95": _percentile(runtimes, 19, 20),
            "runtime_wall_milliseconds_max": max(runtimes, default=0),
            "runtime_cpu_milliseconds_max": max(cpu, default=0),
            "peak_rss_bytes_max": max(rss, default=0),
            "timeout_count": sum(row["timed_out"] for row in normalized),
            "attempt_count_max": max(
                (row["attempt_count"] for row in normalized), default=0,
            ),
            "worker_throughput_milli_records_per_second_max": max(
                (
                    row["worker_throughput_milli_records_per_second"]
                    for row in normalized
                ),
                default=0,
            ),
            "checkpoint_overhead_milliseconds_total": sum(
                row["checkpoint_overhead_milliseconds"] for row in normalized
            ),
            "resume_overhead_milliseconds_total": sum(
                row["resume_overhead_milliseconds"] for row in normalized
            ),
            "simulation_wall_milliseconds_total": sum(
                row["simulation_wall_milliseconds"] for row in normalized
            ),
            "trace_size_bytes_total": sum(
                row["trace_size_bytes"] for row in normalized
            ),
            "output_io_bytes_total": sum(
                row["output_io_bytes"] for row in normalized
            ),
            "engineering_error_count": sum(
                row["engineering_error"] for row in normalized
            ),
            "ci_width_engineering_warning_count": sum(
                row["ci_width_engineering_warning"] for row in normalized
            ),
            "per_method": per_method,
        },
        "observation_sha256": observation_sha256,
        "scientific_results_included": False,
    }
    return {
        **material,
        "pilot_report_id": domain_hash(RTA4_PILOT_REPORT_DOMAIN, material),
    }


def validate_pilot_report(
    report: Mapping[str, Any], manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(report, Mapping):
        raise RTA4PilotError("pilot report must be a mapping")
    if set(report) != {
        "pilot_version", "pilot_manifest_id", "pilot_status",
        "pilot_closure_id", "observation_count", "engineering_metrics",
        "observation_sha256", "scientific_results_included",
        "pilot_report_id",
    }:
        raise RTA4PilotError("pilot report has an unexpected field set")
    material = dict(report)
    observed = material.pop("pilot_report_id", None)
    if material.get("pilot_version") != RTA4_PILOT_VERSION:
        raise RTA4PilotError("pilot report version mismatch")
    if material.get("pilot_manifest_id") != manifest.get("pilot_manifest_id"):
        raise RTA4PilotError("pilot report belongs to another pilot manifest")
    if material.get("pilot_status") != "PILOT_COMPLETE_ENGINEERING_ONLY":
        raise RTA4PilotError("pilot is not complete")
    if material.get("scientific_results_included") is not False:
        raise RTA4PilotError("pilot report must not contain scientific results")
    if (
        not isinstance(material.get("pilot_closure_id"), str)
        or len(material["pilot_closure_id"]) != 64
    ):
        raise RTA4PilotError("pilot closure identity is missing")
    metrics = material.get("engineering_metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != {
        "runtime_wall_milliseconds_p50",
        "runtime_wall_milliseconds_p95",
        "runtime_wall_milliseconds_max",
        "runtime_cpu_milliseconds_max", "peak_rss_bytes_max",
        "timeout_count", "attempt_count_max",
        "worker_throughput_milli_records_per_second_max",
        "checkpoint_overhead_milliseconds_total",
        "resume_overhead_milliseconds_total",
        "simulation_wall_milliseconds_total", "trace_size_bytes_total",
        "output_io_bytes_total", "engineering_error_count",
        "ci_width_engineering_warning_count", "per_method",
    }:
        raise RTA4PilotError("pilot engineering metric field set mismatch")
    if observed != domain_hash(RTA4_PILOT_REPORT_DOMAIN, material):
        raise RTA4PilotError("pilot report identity mismatch")
    return dict(report)


__all__ = [
    "RTA4_PILOT_CLOSURE_DOMAIN", "RTA4_PILOT_CONFIG_DOMAIN",
    "RTA4_PILOT_EXECUTION_CLASS",
    "RTA4_PILOT_MANIFEST_DOMAIN",
    "RTA4_PILOT_OUTPUT_MARKER", "RTA4_PILOT_REPORT",
    "RTA4_PILOT_REPORT_DOMAIN", "RTA4_PILOT_VERSION",
    "RTA4PilotError", "build_pilot_manifest", "build_pilot_report",
    "source_config_evidence", "validate_pilot_manifest",
    "validate_pilot_report",
]
