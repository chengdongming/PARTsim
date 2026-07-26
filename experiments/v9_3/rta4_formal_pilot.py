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


RTA4_PILOT_VERSION = "ASAP_BLOCK_V9_3_RTA4_PILOT_V4"
RTA4_PILOT_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_CONFIG:v4"
RTA4_PILOT_MANIFEST_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_MANIFEST:v4"
RTA4_PILOT_CLOSURE_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_CLOSURE:v4"
RTA4_PILOT_REPORT_VERSION = "ASAP_BLOCK_V9_3_RTA4_PILOT_REPORT_V3"
RTA4_PILOT_REPORT_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PILOT_REPORT:v3"
RTA4_PILOT_OBSERVATION_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_PILOT_OBSERVATIONS_V1"
)
RTA4_PILOT_OBSERVATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_OBSERVATION:v1"
)
RTA4_PILOT_OBSERVATIONS_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4_PILOT_OBSERVATIONS:v1"
)
RTA4_PILOT_EXECUTION_CLASS = "ENGINEERING_PILOT"
RTA4_PILOT_OUTPUT_MARKER = "rta4_pilot_manifest.json"
RTA4_PILOT_OBSERVATIONS = "rta4_pilot_observations.json"
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
    return domain_hash("ASAP_BLOCK:V9.3:RTA4_PILOT_SELECTION:v4", {
        "pilot_version": RTA4_PILOT_VERSION,
        "seed": seed,
        "core": record.core,
        "plan_record_id": record.record_id,
    })


def _group_selection_key(mathematical_request_id: str, seed: str) -> str:
    return domain_hash("ASAP_BLOCK:V9.3:RTA4_PILOT_GROUP_SELECTION:v1", {
        "pilot_version": RTA4_PILOT_VERSION,
        "seed": seed,
        "core": "CORE-5B",
        "mathematical_request_id": mathematical_request_id,
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


def _select_core5b(
    records: Iterable[FormalPlanRecord], count: int, seed: str,
) -> Tuple[FormalPlanRecord, ...]:
    if count % 4:
        raise RTA4PilotError(
            "CORE-5B pilot scale must be a positive multiple of four "
            "execution records"
        )
    group_count = count // 4
    heap: list[tuple[int, int, tuple[FormalPlanRecord, ...]]] = []
    group: list[FormalPlanRecord] = []
    for record in records:
        if group and (
            record.mathematical_request_id
            != group[0].mathematical_request_id
        ):
            if len(group) != 4:
                raise RTA4PilotError(
                    "trusted CORE-5B plan contains an incomplete request group"
                )
            rows = tuple(group)
            rank = int(_group_selection_key(
                str(rows[0].mathematical_request_id), seed,
            ), 16)
            item = (-rank, -rows[0].ordinal, rows)
            if len(heap) < group_count:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
            group = []
        group.append(record)
    if group:
        if len(group) != 4:
            raise RTA4PilotError(
                "trusted CORE-5B plan contains an incomplete request group"
            )
        rows = tuple(group)
        rank = int(_group_selection_key(
            str(rows[0].mathematical_request_id), seed,
        ), 16)
        item = (-rank, -rows[0].ordinal, rows)
        if len(heap) < group_count:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    if len(heap) != group_count:
        raise RTA4PilotError("pilot scale exceeds the trusted CORE-5B plan")
    selected = tuple(
        record
        for item in sorted(
            heap, key=lambda value: value[2][0].ordinal,
        )
        for record in item[2]
    )
    for offset in range(0, len(selected), 4):
        rows = selected[offset:offset + 4]
        workers = tuple(
            int(row.material.get("worker_count", 1)) for row in rows
        )
        if (
            workers != (1, 2, 4, 8)
            or len({row.mathematical_request_id for row in rows}) != 1
            or len({row.execution_id for row in rows}) != 4
            or len({
                canonical_json({
                    key: value
                    for key, value in row.material.items()
                    if key not in {"worker_count", "axis_value"}
                })
                for row in rows
            }) != 1
        ):
            raise RTA4PilotError(
                "trusted CORE-5B request group violates the 1/2/4/8 contract"
            )
    return selected


@lru_cache(maxsize=48)
def _default_selection(
    core: str, count: int, seed: str,
) -> Tuple[FormalPlanRecord, ...]:
    records = iter_formal_plan(default_rta4_formal_config(core))
    if core == "CORE-5B":
        return _select_core5b(records, count, seed)
    return _select(records, count, seed)


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
        validate_rta4_formal_config(configs[core], expected_core=core)
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
                "taskset_skeleton_slot_id": row.taskset_skeleton_slot_id,
                "taskset_slot_id": row.taskset_slot_id,
                "worker_count": int(row.material.get("worker_count", 1)),
                "selection_key": (
                    _group_selection_key(
                        str(row.mathematical_request_id), selection_seed,
                    )
                    if core == "CORE-5B"
                    else _selection_key(row, selection_seed)
                ),
            }
            for row in rows
        ]
    pilot_config = {
        "pilot_version": RTA4_PILOT_VERSION,
        "profile": RTA4_FORMAL_PROFILE,
        "execution_class": RTA4_PILOT_EXECUTION_CLASS,
        "selection_rule": "DOMAIN_HASH_LOWEST_RESULT_INDEPENDENT_V1",
        "scale_unit": "EXECUTION_RECORDS",
        "selection_unit": {
            core: (
                "MATHEMATICAL_REQUEST_GROUP"
                if core == "CORE-5B" else "PLAN_RECORD"
            )
            for core in RTA4_CORES
        },
        "required_group_workers": {
            core: ([1, 2, 4, 8] if core == "CORE-5B" else [])
            for core in RTA4_CORES
        },
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


_PILOT_METRIC_FIELDS = frozenset({
    "runtime_wall_milliseconds", "runtime_cpu_milliseconds",
    "peak_rss_bytes", "timed_out", "attempt_count",
    "worker_throughput_milli_records_per_second",
    "checkpoint_overhead_milliseconds", "resume_overhead_milliseconds",
    "simulation_wall_milliseconds", "trace_size_bytes",
    "output_io_bytes", "engineering_error",
    "ci_width_engineering_warning",
})
_PILOT_OBSERVATION_INPUT_FIELDS = frozenset({
    "plan_record_id", "mathematical_request_id", "execution_id",
    "worker_count", *_PILOT_METRIC_FIELDS,
})


def build_pilot_observations(
    manifest: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build independently reconstructable raw engineering observations."""

    expected = {
        row["plan_record_id"]: {
            **row, "core": core,
        }
        for core in RTA4_CORES
        for row in manifest["selected_records"][core]
    }
    if len(observations) != len(expected):
        raise RTA4PilotError("pilot observation count mismatch")
    seen: set[str] = set()
    normalized = []
    for raw in observations:
        if not isinstance(raw, Mapping) or set(raw) != (
            _PILOT_OBSERVATION_INPUT_FIELDS
        ):
            raise RTA4PilotError("pilot observation has an unexpected field set")
        record_id = raw["plan_record_id"]
        if record_id not in expected or record_id in seen:
            raise RTA4PilotError("pilot observation membership mismatch")
        selected = expected[record_id]
        if (
            raw["mathematical_request_id"]
            != selected["mathematical_request_id"]
            or raw["execution_id"] != selected["execution_id"]
            or raw["worker_count"] != selected["worker_count"]
        ):
            raise RTA4PilotError(
                "pilot observation execution identity mismatch"
            )
        if type(raw["worker_count"]) is not int or raw["worker_count"] < 1:
            raise RTA4PilotError(
                "pilot observation worker count must be positive"
            )
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
        material = {
            "pilot_manifest_id": manifest["pilot_manifest_id"],
            "selection_key": selected["selection_key"],
            "core": selected["core"],
            "method": selected["method"],
            "taskset_skeleton_slot_id": selected[
                "taskset_skeleton_slot_id"
            ],
            "taskset_slot_id": selected["taskset_slot_id"],
            **dict(raw),
        }
        normalized.append({
            **material,
            "observation_id": domain_hash(
                RTA4_PILOT_OBSERVATION_DOMAIN, material,
            ),
        })
    normalized.sort(key=lambda row: row["plan_record_id"])
    observations_sha256 = hashlib.sha256(
        canonical_json(normalized).encode("utf-8")
    ).hexdigest()
    material = {
        "pilot_observation_version": RTA4_PILOT_OBSERVATION_VERSION,
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "observation_count": len(normalized),
        "observations": normalized,
        "observations_sha256": observations_sha256,
    }
    return {
        **material,
        "pilot_observations_id": domain_hash(
            RTA4_PILOT_OBSERVATIONS_DOMAIN, material,
        ),
    }


def validate_pilot_observations(
    document: Mapping[str, Any], manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(document, Mapping) or set(document) != {
        "pilot_observation_version", "pilot_manifest_id",
        "observation_count", "observations", "observations_sha256",
        "pilot_observations_id",
    }:
        raise RTA4PilotError(
            "pilot observation document has an unexpected field set"
        )
    if (
        document["pilot_observation_version"]
        != RTA4_PILOT_OBSERVATION_VERSION
        or document["pilot_manifest_id"] != manifest.get("pilot_manifest_id")
        or not isinstance(document["observations"], Sequence)
        or isinstance(document["observations"], (str, bytes))
    ):
        raise RTA4PilotError("pilot observation document binding mismatch")
    inputs = []
    wrapper_fields = {
        "observation_id", "pilot_manifest_id", "selection_key", "core",
        "method", "taskset_skeleton_slot_id", "taskset_slot_id",
    }
    for row in document["observations"]:
        if not isinstance(row, Mapping) or set(row) != (
            _PILOT_OBSERVATION_INPUT_FIELDS | wrapper_fields
        ):
            raise RTA4PilotError(
                "pilot raw observation has an unexpected field set"
            )
        inputs.append({
            key: value for key, value in row.items()
            if key not in wrapper_fields
        })
    expected = build_pilot_observations(manifest, inputs)
    if dict(document) != expected:
        raise RTA4PilotError(
            "pilot observations cannot be reconstructed from raw evidence"
        )
    return expected


def build_pilot_report(
    manifest: Mapping[str, Any],
    observation_document: Mapping[str, Any],
) -> Dict[str, Any]:
    """Summarize validated raw runtime/memory/timeout evidence."""

    observations = validate_pilot_observations(
        observation_document, manifest,
    )
    normalized = observations["observations"]
    runtimes = [row["runtime_wall_milliseconds"] for row in normalized]
    cpu = [row["runtime_cpu_milliseconds"] for row in normalized]
    rss = [row["peak_rss_bytes"] for row in normalized]
    observation_sha256 = observations["observations_sha256"]
    selected_for = {
        row["plan_record_id"]: {**row, "core": core}
        for core in RTA4_CORES
        for row in manifest["selected_records"][core]
    }
    per_method = {}
    for method in sorted({
        row["method"] for row in selected_for.values()
    }):
        method_rows = [
            row for row in normalized
            if selected_for[row["plan_record_id"]]["method"] == method
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
    strata = []
    group_keys = sorted({
        (
            selected_for[row["plan_record_id"]]["core"],
            selected_for[row["plan_record_id"]]["method"],
            row["worker_count"],
        )
        for row in normalized
    })
    for core, method, worker_count in group_keys:
        rows = [
            row for row in normalized
            if (
                selected_for[row["plan_record_id"]]["core"],
                selected_for[row["plan_record_id"]]["method"],
                row["worker_count"],
            ) == (core, method, worker_count)
        ]
        group_runtimes = [
            row["runtime_wall_milliseconds"] for row in rows
        ]
        group_cpu = [row["runtime_cpu_milliseconds"] for row in rows]
        group_rss = [row["peak_rss_bytes"] for row in rows]
        group_throughput = [
            row["worker_throughput_milli_records_per_second"]
            for row in rows
        ]
        group_simulation = [
            row["simulation_wall_milliseconds"] for row in rows
        ]
        group_trace = [row["trace_size_bytes"] for row in rows]
        warnings = {
            row["ci_width_engineering_warning"] for row in rows
        }
        if len(warnings) != 1:
            raise RTA4PilotError(
                "runtime CI warning must be constant within one stratum"
            )
        strata.append({
            "core": core,
            "method": method,
            "worker_count": worker_count,
            "observation_count": len(rows),
            "runtime_wall_milliseconds_p50": _percentile(
                group_runtimes, 1, 2,
            ),
            "runtime_wall_milliseconds_p95": _percentile(
                group_runtimes, 19, 20,
            ),
            "runtime_wall_milliseconds_max": max(
                group_runtimes, default=0,
            ),
            "runtime_cpu_milliseconds_max": max(group_cpu, default=0),
            "peak_rss_bytes_max": max(group_rss, default=0),
            "timeout_count": sum(row["timed_out"] for row in rows),
            "engineering_error_count": sum(
                row["engineering_error"] for row in rows
            ),
            "worker_throughput_milli_records_per_second_p50": _percentile(
                group_throughput, 1, 2,
            ),
            "simulation_wall_milliseconds_p95": _percentile(
                group_simulation, 19, 20,
            ),
            "trace_size_bytes_max": max(group_trace, default=0),
            "runtime_ci_engineering_warning": next(iter(warnings)),
        })
    pilot_closure_id = domain_hash(RTA4_PILOT_CLOSURE_DOMAIN, {
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "pilot_observations_id": observations["pilot_observations_id"],
        "observation_sha256": observation_sha256,
        "observation_count": len(normalized),
    })
    material = {
        "pilot_report_version": RTA4_PILOT_REPORT_VERSION,
        "pilot_version": RTA4_PILOT_VERSION,
        "pilot_manifest_id": manifest["pilot_manifest_id"],
        "pilot_observations_id": observations["pilot_observations_id"],
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
            "strata": strata,
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
    observation_document: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(report, Mapping):
        raise RTA4PilotError("pilot report must be a mapping")
    expected = build_pilot_report(manifest, observation_document)
    if dict(report) != expected:
        raise RTA4PilotError(
            "pilot report cannot be reconstructed from raw observations"
        )
    return expected


__all__ = [
    "RTA4_PILOT_CLOSURE_DOMAIN", "RTA4_PILOT_CONFIG_DOMAIN",
    "RTA4_PILOT_EXECUTION_CLASS",
    "RTA4_PILOT_MANIFEST_DOMAIN",
    "RTA4_PILOT_OBSERVATION_DOMAIN", "RTA4_PILOT_OBSERVATION_VERSION",
    "RTA4_PILOT_OBSERVATIONS", "RTA4_PILOT_OBSERVATIONS_DOMAIN",
    "RTA4_PILOT_OUTPUT_MARKER", "RTA4_PILOT_REPORT",
    "RTA4_PILOT_REPORT_DOMAIN", "RTA4_PILOT_REPORT_VERSION",
    "RTA4_PILOT_VERSION",
    "RTA4PilotError", "build_pilot_manifest", "build_pilot_observations",
    "build_pilot_report",
    "source_config_evidence", "validate_pilot_manifest",
    "validate_pilot_observations", "validate_pilot_report",
]
