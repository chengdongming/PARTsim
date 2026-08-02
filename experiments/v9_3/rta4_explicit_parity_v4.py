"""Audit-only parity between the frozen spotcheck math and V4 adapter.

This harness consumes a normalized explicit manifest.  It does not prepare,
authorize, or execute a formal campaign and writes no taskset-store material.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Any, Mapping, Sequence

from .rta4_energy_service_v4 import EXACT_LINEAR_SERVICE_V1, EnergyServiceV4
from .rta4_formal_config import domain_hash, fraction_text
from .rta4_task_source_v4 import (
    EXPLICIT_TASKSET_MANIFEST,
    TaskSourceV4,
)
from .rta4_t10_parity_audit import (
    METHOD_IDS,
    METHOD_LABELS,
    _canonical_sha,
    _deep_diffs,
)
from .rta4_t10_service_migration_audit import (
    EXACT_SERVICE_MODEL,
    _direct_method,
    _service_material,
    _task_materials,
)
from .rta4_unified_adapter_v4 import execute_normalized_taskset_v4


PARITY_SCHEMA_V4 = "ASAP_BLOCK_RTA4_EXPLICIT_SPOTCHECK_PARITY_V4"


class RTA4ExplicitParityV4Error(ValueError):
    """Raised when the V4 parity input is not a frozen explicit task source."""


def _normalized_e0(values: Sequence[str]) -> tuple[str, ...]:
    if not values:
        raise RTA4ExplicitParityV4Error("at least one E0 value is required")
    normalized = []
    for index, value in enumerate(values):
        if type(value) is not str:
            raise RTA4ExplicitParityV4Error(
                f"E0[{index}] must be exact rational text"
            )
        exact = Fraction(value)
        if exact < 0 or value != fraction_text(exact):
            raise RTA4ExplicitParityV4Error(
                f"E0[{index}] is not canonical nonnegative rational text"
            )
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise RTA4ExplicitParityV4Error("E0 values contain duplicates")
    return tuple(normalized)


def _method_labels(values: Sequence[str]) -> tuple[str, ...]:
    if not values:
        raise RTA4ExplicitParityV4Error("at least one method is required")
    inverse = {method: label for label, method in METHOD_IDS.items()}
    try:
        labels = tuple(inverse[value] for value in values)
    except KeyError as exc:
        raise RTA4ExplicitParityV4Error(
            f"unknown parity method: {exc.args[0]!r}"
        ) from exc
    if len(set(labels)) != len(labels):
        raise RTA4ExplicitParityV4Error("methods contain duplicates")
    return tuple(label for label in METHOD_LABELS if label in labels)


def _semantic_v4_input(
    source: TaskSourceV4, taskset_index: int, e0: str,
    service: EnergyServiceV4,
) -> dict[str, Any]:
    taskset = source.tasksets[taskset_index]
    maximum_deadline = max(task.D for task in taskset.tasks)
    prefix = [
        fraction_text(service.beta(length))
        for length in range(maximum_deadline)
    ]
    return {
        "processors": source.processors,
        "tasks": [task.material() for task in taskset.tasks],
        "task_order": list(taskset.task_order),
        "E0": e0,
        "service_prefix": prefix,
        "semantic_service_identity": _canonical_sha(prefix),
        "semantic_power_identity": _canonical_sha([
            (task.name, task.power) for task in taskset.tasks
        ]),
    }


def run_explicit_spotcheck_parity_v4(
    *, task_source: TaskSourceV4, energy_service: EnergyServiceV4,
    e0_values: Sequence[str], methods: Sequence[str],
    timeout_seconds: int, production_build_manifest_identity: str,
) -> dict[str, Any]:
    """Compare the frozen direct entry and V4 adapter on identical science."""

    if (
        type(task_source) is not TaskSourceV4
        or task_source.mode != EXPLICIT_TASKSET_MANIFEST
    ):
        raise RTA4ExplicitParityV4Error(
            "parity requires a normalized explicit taskset manifest"
        )
    if (
        type(energy_service) is not EnergyServiceV4
        or energy_service.model != EXACT_LINEAR_SERVICE_V1
        or energy_service.normalized_config.get("rate") != "1/10"
    ):
        raise RTA4ExplicitParityV4Error(
            "frozen parity requires EXACT_LINEAR_SERVICE_V1 rate 1/10"
        )
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise RTA4ExplicitParityV4Error("timeout must be a positive integer")
    e0_axis = _normalized_e0(e0_values)
    labels = _method_labels(methods)
    store_identity = domain_hash(
        "ASAP_BLOCK:V9.3:RTA4:V4:PARITY_STORE:v1",
        {"task_source_identity": task_source.identity},
    )
    mismatch_count = 0
    input_mismatch_count = 0
    internal_error_count = 0
    script_errors = []
    first_mismatch = None
    certified = Counter()
    dominance_violation_count = 0
    per_method_rows: dict[str, list[dict[str, Any]]] = {
        f"{e0}:{label}": [] for e0 in e0_axis for label in labels
    }

    for taskset_index, taskset in enumerate(task_source.tasksets):
        if taskset.source_seed is None:
            raise RTA4ExplicitParityV4Error(
                "frozen spotcheck parity requires an explicit source_seed"
            )
        record = {
            "taskset_index": taskset_index,
            "seed": taskset.source_seed,
            "tasks": [task.material() for task in taskset.tasks],
            "task_order": list(taskset.task_order),
        }
        try:
            direct_materials = _task_materials(record)
            direct_service = _service_material(
                direct_materials, EXACT_SERVICE_MODEL,
            )
            for e0 in e0_axis:
                cell_certification: dict[str, bool] = {}
                for label in labels:
                    direct_result, direct_input = _direct_method(
                        label=label, task_materials=direct_materials,
                        service=direct_service, e0=Fraction(e0),
                    )
                    adapter = execute_normalized_taskset_v4(
                        taskset=taskset, processors=task_source.processors,
                        task_source_identity=task_source.identity,
                        taskset_store_identity=store_identity,
                        production_build_manifest_identity=(
                            production_build_manifest_identity
                        ),
                        energy_service=energy_service, e0=e0,
                        method=METHOD_IDS[label],
                        timeout_seconds=timeout_seconds,
                    )
                    v4_input = _semantic_v4_input(
                        task_source, taskset_index, e0, energy_service,
                    )
                    direct_semantic_input = {
                        key: direct_input[key] for key in v4_input
                    }
                    input_diffs = _deep_diffs(
                        direct_semantic_input, v4_input,
                    )
                    result_diffs = _deep_diffs(
                        direct_result, adapter["result"],
                    )
                    if input_diffs:
                        input_mismatch_count += 1
                    if input_diffs or result_diffs:
                        mismatch_count += 1
                        if first_mismatch is None:
                            first_mismatch = {
                                "taskset_index": taskset_index,
                                "E0": e0,
                                "method": label,
                                "input_differences": input_diffs,
                                "result_differences": result_diffs,
                            }
                    if adapter["result"]["solver_status"] == (
                        "INTERNAL_CONFORMANCE_FAILURE"
                    ):
                        internal_error_count += 1
                    proven = bool(adapter["result"]["taskset_proven"])
                    cell_certification[label] = proven
                    certified[f"{e0}:{label}"] += int(proven)
                    per_method_rows[f"{e0}:{label}"].append({
                        "taskset_index": taskset_index,
                        "taskset_proven": proven,
                        "response_vector": adapter["result"][
                            "response_vector"
                        ],
                        "exact_input_identity": adapter["result"][
                            "exact_input_identity"
                        ],
                    })
                if all(label in cell_certification for label in METHOD_LABELS):
                    flags = [cell_certification[label] for label in METHOD_LABELS]
                    dominance_violation_count += sum(
                        int(left and not right)
                        for left, right in zip(flags, flags[1:])
                    )
        except Exception as exc:
            script_errors.append({
                "taskset_index": taskset_index,
                "type": type(exc).__name__,
                "message": str(exc),
            })

    method_units = task_source.taskset_count * len(e0_axis) * len(labels)
    return {
        "schema": PARITY_SCHEMA_V4,
        "task_source_identity": task_source.identity,
        "task_source_content_certificate_identity": (
            task_source.content_certificate["content_certificate_identity"]
        ),
        "manifest_file_sha256": task_source.manifest_file_sha256,
        "manifest_semantic_sha256": task_source.manifest_semantic_sha256,
        "energy_service_identity": energy_service.identity,
        "taskset_count": task_source.taskset_count,
        "method_unit_count": method_units,
        "task_result_record_count": method_units * task_source.task_count,
        "input_mismatch_count": input_mismatch_count,
        "adapter_parity_mismatch_count": mismatch_count,
        "first_mismatch": first_mismatch,
        "internal_error_count": internal_error_count,
        "script_error_count": len(script_errors),
        "script_errors": script_errors,
        "dominance_violation_count": dominance_violation_count,
        "certified_counts": dict(sorted(certified.items())),
        "per_method_result_hashes": {
            key: _canonical_sha(rows)
            for key, rows in sorted(per_method_rows.items())
        },
        "parity_passed": (
            mismatch_count == 0
            and internal_error_count == 0
            and not script_errors
            and dominance_violation_count == 0
        ),
        "formal_experiment_started": False,
        "canonical_summary_sha256": _canonical_sha({
            "task_source_identity": task_source.identity,
            "energy_service_identity": energy_service.identity,
            "certified_counts": dict(sorted(certified.items())),
            "per_method_rows": per_method_rows,
        }),
    }


__all__ = [
    "PARITY_SCHEMA_V4", "RTA4ExplicitParityV4Error",
    "run_explicit_spotcheck_parity_v4",
]
