"""Stage-A.5 paired audit for the RTA4 T10 linear-service migration.

This is an audit-only harness.  It keeps task inputs, priority order, E0, and
exact rational task powers fixed while changing only the service prefix:

* ``LEGACY_BINARY64_MATERIALIZED_LINEAR_SERVICE_V1`` reproduces the service
  prefix actually used by the frozen experiment.
* ``EXACT_LINEAR_SERVICE_V1`` constructs beta directly as
  ``Fraction(length, 10)`` without a floating-point conversion path.

The exact input is executed through both the direct mathematical entry and the
current formal adapter.  No formal output, taskset-store, prepared config, or
authorization namespace is written.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from fractions import Fraction
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

import asap_block_rta_v9_3 as rta_core
import asap_block_rta_v9_3_taskset as rta_adapter

from . import exact_energy
from . import rta4_formal_execution
from .rta4_formal_config import canonical_json, domain_hash, fraction_text
from .rta4_formal_config_v3 import RTA4_FORMAL_PROFILE_V3
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_shared_energy import (
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    TASK_ENERGY_ENTRY_DOMAIN,
    TASK_ENERGY_MATERIAL_DOMAIN,
    TASK_ENERGY_MATERIAL_SCHEMA,
    ServiceHorizonContract,
    TaskEnergyEntry,
    TaskEnergyMaterial,
    VerifiedSolarServiceMaterialV2,
)
from .rta4_taskset_v2 import (
    RTA4_FORMAL_PROFILE_V2,
    RTA4_TASKSET_CERTIFICATE_DOMAIN_V2,
    RTA4_TASKSET_CERTIFICATE_SCHEMA_V2,
    RTA4_TASKSET_SOURCE_DOMAIN_V2,
    FormalTaskV2,
    TasksetIdentityCertificateV2,
)
from .rta4_t10_parity_audit import (
    CURRENT_BASE_COMMIT,
    CURRENT_BASE_TREE,
    E0_VALUES,
    FROZEN_ARCHIVE_SHA256,
    METHOD_IDS,
    METHOD_LABELS,
    OUTER_ARCHIVE_SHA256,
    PROCESSORS,
    TIMEOUT_SECONDS,
    T10ParityAuditError,
    _canonical_sha,
    _deep_diffs,
    _dominance_violations,
    _frozen_beta,
    _git,
    _method_projection,
    _sha256_bytes,
    _sha256_file,
    _write_json,
    normalize_t10_record,
    verify_evidence,
)


STAGE_A_COMMIT = "cee718db62b390e75ac0e7f79511972d04081140"
STAGE_A_TREE = "218117db02073edda45ca2b4de9ddcacdd183550"
LEGACY_SERVICE_MODEL = "LEGACY_BINARY64_MATERIALIZED_LINEAR_SERVICE_V1"
EXACT_SERVICE_MODEL = "EXACT_LINEAR_SERVICE_V1"
EXACT_SERVICE_RATE = "1/10"
AUDIT_SCHEMA = "ASAP_BLOCK_RTA4_T10_STAGE_A5_SERVICE_CONTRACT_MIGRATION_V1"


class T10ServiceMigrationAuditError(T10ParityAuditError):
    """Raised when the Stage-A.5 audit must fail closed."""


def exact_linear_service_prefix(maximum_deadline: int) -> tuple[Fraction, ...]:
    """Return beta(0)..beta(Dmax-1) directly under beta(L)=L/10."""

    if type(maximum_deadline) is not int or maximum_deadline < 1:
        raise T10ServiceMigrationAuditError(
            "exact service maximum deadline must be a positive integer"
        )
    return tuple(Fraction(length, 10) for length in range(maximum_deadline))


def legacy_binary64_service_prefix(
    maximum_deadline: int,
) -> tuple[Fraction, ...]:
    """Return the historical service prefix through the frozen audit helper."""

    if type(maximum_deadline) is not int or maximum_deadline < 1:
        raise T10ServiceMigrationAuditError(
            "legacy service maximum deadline must be a positive integer"
        )
    return _frozen_beta(maximum_deadline)


@dataclass(frozen=True)
class _TaskMaterials:
    certificate: TasksetIdentityCertificateV2
    task_energy: TaskEnergyMaterial
    tasks: tuple[rta_core.V93Task, ...]
    common_projection: Mapping[str, Any]


def _task_materials(record: Mapping[str, Any]) -> _TaskMaterials:
    task_rows = list(record["tasks"])
    formal_tasks = tuple(
        FormalTaskV2(
            str(row["name"]), index, int(row["C"]), int(row["D"]),
            int(row["T"]), "T10_A5_EXACT_RATIONAL",
        )
        for index, row in enumerate(task_rows)
    )
    exact_powers = tuple(Fraction(str(row["power"])) for row in task_rows)
    if any(power < 0 for power in exact_powers):
        raise T10ServiceMigrationAuditError("task power must be nonnegative")

    source_material = {
        "contract": "ASAP_BLOCK_RTA4_T10_A5_PAIRED_SOURCE_V1",
        "taskset_index": int(record["taskset_index"]),
        "seed": int(record["seed"]),
        "tasks": task_rows,
        "task_order": list(record["task_order"]),
    }
    source_sha = _canonical_sha(source_material)
    request_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10:A5:REQUEST:v1", {
        "taskset_index": int(record["taskset_index"]),
        "seed": int(record["seed"]),
        "source_sha256": source_sha,
    })
    skeleton_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10:A5:SKELETON:v1", {
        "generation_request_id": request_id,
        "processor_count": PROCESSORS,
        "taskset_source_sha256": source_sha,
        "task_order": list(record["task_order"]),
    })
    deadline_variant = "T10_BALANCED_A5_EXACT_RATIONAL_V1"
    taskset_hash = domain_hash(RTA4_TASKSET_SOURCE_DOMAIN_V2, {
        "processor_count": PROCESSORS,
        "deadline_variant": deadline_variant,
        "tasks": [task.material() for task in formal_tasks],
    })
    certificate_base = {
        "schema": RTA4_TASKSET_CERTIFICATE_SCHEMA_V2,
        "profile": RTA4_FORMAL_PROFILE_V2,
        "processor_count": PROCESSORS,
        "formal_master_seed": int(record["seed"]),
        "generator_seed": int(record["seed"]),
        "generator_contract_version": "ASAP_BLOCK_RTA4_T10_A5_EXACT_RATIONAL_V1",
        "generation_request_id": request_id,
        "taskset_skeleton_id": skeleton_id,
        "taskset_source_sha256": source_sha,
        "deadline_variant": deadline_variant,
        "energy_coefficient": "1",
        "tasks": [task.material() for task in formal_tasks],
        "taskset_hash": taskset_hash,
    }
    certificate_id = domain_hash(
        RTA4_TASKSET_CERTIFICATE_DOMAIN_V2, certificate_base,
    )
    certificate = TasksetIdentityCertificateV2(
        PROCESSORS, int(record["seed"]), int(record["seed"]),
        "ASAP_BLOCK_RTA4_T10_A5_EXACT_RATIONAL_V1", request_id, skeleton_id,
        source_sha, deadline_variant, Fraction(1), formal_tasks, taskset_hash,
        certificate_id,
    )
    certificate.validate()

    build_identity = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10:A5:BUILD:v1", {
        "stage_a_commit": STAGE_A_COMMIT,
        "stage_a_tree": STAGE_A_TREE,
    })
    store_identity = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10:A5:STORE:v1", {
        "taskset_id": certificate.taskset_id,
        "source_sha256": source_sha,
    })
    taskset_canonical_sha = _sha256_bytes(certificate.canonical_bytes())
    entries = []
    for index, (task, power) in enumerate(zip(formal_tasks, exact_powers)):
        entry_base = {
            "schema": TASK_ENERGY_MATERIAL_SCHEMA,
            "profile_id": RTA4_FORMAL_PROFILE_V3,
            "production_build_manifest_identity": build_identity,
            "taskset_id": certificate.taskset_id,
            "task_index": index,
            "task_id": task.task_id,
            "C": task.wcet,
            "D": task.relative_deadline,
            "T": task.period,
            "energy_j_per_tick": fraction_text(power),
            "numeric_source": "EXACT_RATIONAL_MANIFEST_TEXT_V1",
            "unit": "J/tick",
        }
        entry_identity = domain_hash(TASK_ENERGY_ENTRY_DOMAIN, entry_base)
        entries.append(TaskEnergyEntry(
            index, task.task_id, task.period, task.relative_deadline, task.wcet,
            task.workload,
            "NOT_APPLICABLE_EXACT_RATIONAL_V1",
            "NOT_APPLICABLE_EXACT_RATIONAL_V1",
            "NOT_APPLICABLE_EXACT_RATIONAL_V1",
            "NOT_APPLICABLE_EXACT_RATIONAL_V1",
            power,
            "NOT_APPLICABLE_EXACT_RATIONAL_V1",
            entry_identity,
        ))
    task_energy_base = {
        "schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "profile_id": RTA4_FORMAL_PROFILE_V3,
        "production_build_manifest_identity": build_identity,
        "taskset_id": certificate.taskset_id,
        "taskset_store_identity": store_identity,
        "taskset_canonical_sha256": taskset_canonical_sha,
        "system_config_sha256": "0" * 64,
        "workload_config_sha256": "0" * 64,
        "generator_contract_version": "ASAP_BLOCK_RTA4_T10_A5_EXACT_RATIONAL_V1",
        "numeric_source": "EXACT_RATIONAL_MANIFEST_TEXT_V1",
        "entries": [entry.material() for entry in entries],
    }
    task_energy_identity = domain_hash(
        TASK_ENERGY_MATERIAL_DOMAIN, task_energy_base,
    )
    task_energy = TaskEnergyMaterial(
        RTA4_FORMAL_PROFILE_V3, build_identity, certificate.taskset_id,
        store_identity, taskset_canonical_sha, "0" * 64, "0" * 64,
        "ASAP_BLOCK_RTA4_T10_A5_EXACT_RATIONAL_V1", tuple(entries),
        task_energy_identity,
    )
    mathematical_tasks = tuple(
        rta_core.V93Task(
            task.task_id, task.wcet, task.relative_deadline, task.period, power,
        )
        for task, power in zip(formal_tasks, exact_powers)
    )
    return _TaskMaterials(
        certificate, task_energy, mathematical_tasks,
        {
            "processors": PROCESSORS,
            "priority_policy": "RM_STRICT_PERIOD_ASCENDING",
            "tasks": [
                {
                    "name": task.name,
                    "C": task.wcet,
                    "D": task.deadline,
                    "T": task.period,
                    "power": fraction_text(task.power),
                }
                for task in mathematical_tasks
            ],
            "task_order": [task.name for task in mathematical_tasks],
            "taskset_identity": certificate.taskset_id,
            "priority_order_identity": certificate.taskset_skeleton_id,
            "power_identity": task_energy_identity,
            "semantic_power_identity": _canonical_sha([
                (task.name, fraction_text(task.power))
                for task in mathematical_tasks
            ]),
            "power_numeric_source": "EXACT_RATIONAL_MANIFEST_TEXT_V1",
        },
    )


def _service_material(
    task_materials: _TaskMaterials,
    service_model: str,
) -> VerifiedSolarServiceMaterialV2:
    maximum_deadline = max(task.deadline for task in task_materials.tasks)
    if service_model == EXACT_SERVICE_MODEL:
        beta = exact_linear_service_prefix(maximum_deadline)
        trace = tuple(Fraction(1, 10) for _ in range(maximum_deadline))
        numeric_source = "DIRECT_FRACTION_LENGTH_OVER_10"
    elif service_model == LEGACY_SERVICE_MODEL:
        beta = legacy_binary64_service_prefix(maximum_deadline)
        tick = beta[1]
        trace = tuple(tick for _ in range(maximum_deadline))
        numeric_source = "FROZEN_BINARY64_INTERVAL_ACCUMULATION"
    else:
        raise T10ServiceMigrationAuditError(
            f"unknown service model: {service_model}"
        )
    horizon = ServiceHorizonContract(
        maximum_deadline - 1, 0, maximum_deadline, 0, 0,
        HORIZON_CONTRACT_VERSION,
    )
    prefix_text = [fraction_text(value) for value in beta]
    trace_text = [fraction_text(value) for value in trace]
    beta_identity = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10:A5:BETA:v1", {
        "service_model": service_model,
        "rate": EXACT_SERVICE_RATE,
        "prefix": prefix_text,
    })
    service_base = {
        "schema": SERVICE_MATERIAL_SCHEMA,
        "service_model": service_model,
        "rate": EXACT_SERVICE_RATE,
        "numeric_source": numeric_source,
        "production_build_manifest_identity": (
            task_materials.task_energy.production_build_manifest_identity
        ),
        "taskset_identity": task_materials.certificate.taskset_id,
        "beta_material_identity": beta_identity,
        "horizon": horizon.material(),
    }
    service_identity = domain_hash(
        "ASAP_BLOCK:V9.3:RTA4:T10:A5:SERVICE:v1", service_base,
    )
    return VerifiedSolarServiceMaterialV2(
        cache_key=service_identity,
        semantic_service_source_identity=_canonical_sha({
            "service_model": service_model,
            "rate": EXACT_SERVICE_RATE,
            "prefix": prefix_text,
        }),
        parser_environment_identity="0" * 64,
        live_proof_identity="0" * 64,
        production_build_manifest_identity=(
            task_materials.task_energy.production_build_manifest_identity
        ),
        system_sha256="0" * 64,
        support_sha256="0" * 64,
        solar_csv_sha256="0" * 64,
        day_of_year=0,
        time_of_day_ms=0,
        solar_scale=Fraction(1),
        horizon=horizon,
        harvest_j_per_tick=trace,
        beta_prefix_j=beta,
        trace_sha256=_canonical_sha(trace_text),
        beta_material_sha256=beta_identity,
        service_material_identity=service_identity,
        immutable_provenance_json=canonical_json(service_base),
    )


def _timeout_contract() -> dict[str, dict[str, int]]:
    return {
        method: {
            "initial_timeout_seconds": TIMEOUT_SECONDS,
            "retry_timeout_seconds": TIMEOUT_SECONDS * 2,
            "maximum_attempts": 2,
        }
        for method in METHOD_IDS.values()
    }


def _input_projection(
    analysis_input: rta_adapter.TasksetAnalysisInput,
) -> dict[str, Any]:
    required = max(task.deadline for task in analysis_input.tasks) - 1
    beta = analysis_input.beta
    if callable(beta):
        prefix = tuple(beta(length) for length in range(required + 1))
    else:
        prefix = tuple(beta)
    context = analysis_input.dependency_context
    return {
        "processors": analysis_input.processors,
        "tasks": [
            {
                "name": task.name,
                "C": task.wcet,
                "D": task.deadline,
                "T": task.period,
                "power": fraction_text(task.power),
            }
            for task in analysis_input.tasks
        ],
        "task_order": [task.name for task in analysis_input.tasks],
        "E0": fraction_text(Fraction(analysis_input.e0)),
        "service_prefix": [fraction_text(value) for value in prefix],
        "semantic_service_identity": _canonical_sha([
            fraction_text(value) for value in prefix
        ]),
        "semantic_power_identity": _canonical_sha([
            (task.name, fraction_text(task.power))
            for task in analysis_input.tasks
        ]),
        "taskset_identity": context.taskset_identity,
        "priority_order_identity": context.priority_order_identity,
        "service_identity": context.service_curve_identity,
        "power_identity": context.power_vector_identity,
        "exact_input_identity": context.exact_input_identity,
        "numerical_mode": context.numerical_mode,
        "float_decision_path": context.float_decision_path,
    }


def _direct_analysis_input(
    task_materials: _TaskMaterials,
    service: VerifiedSolarServiceMaterialV2,
    e0: Fraction,
) -> rta_adapter.TasksetAnalysisInput:
    exact_input_identity = exact_energy.exact_input_identity(
        task_powers=(
            (task.name, task.power) for task in task_materials.tasks
        ),
        e0=e0,
        service_prefix=service.beta_prefix_j,
    )
    context = rta_adapter.DependencyContext(
        taskset_identity=task_materials.certificate.taskset_id,
        task_definitions_identity=(
            task_materials.task_energy.task_energy_material_identity
        ),
        priority_order_identity=(
            task_materials.certificate.taskset_skeleton_id
        ),
        e0_canonical_identity=fraction_text(e0),
        service_curve_identity=service.service_material_identity,
        power_vector_identity=(
            task_materials.task_energy.task_energy_material_identity
        ),
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=exact_energy.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=(
            rta_adapter.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity=RTA4_FORMAL_PROFILE_V3,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=exact_input_identity,
        float_decision_path=False,
    )
    return rta_adapter.TasksetAnalysisInput(
        tasks=task_materials.tasks,
        processors=PROCESSORS,
        e0=e0,
        beta=service.beta_prefix_j,
        dependency_context=context,
        timeout_seconds=TIMEOUT_SECONDS,
    )


def _adapter_method(
    *, label: str, task_materials: _TaskMaterials,
    service: VerifiedSolarServiceMaterialV2, e0: Fraction,
) -> tuple[dict[str, Any], dict[str, Any]]:
    timeout = _timeout_contract()
    config = {
        "identity": {
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        },
        "experiment_contract": {"profile": RTA4_FORMAL_PROFILE_V3},
        "execution": {"timeout_contract": timeout},
    }
    identity_contract = {
        "formal_profile": RTA4_FORMAL_PROFILE_V3,
        "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:T10:A5:FORMAL_ANALYSIS:v1",
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "timeout_contract": timeout,
    }
    record = SimpleNamespace(material={
        "method": METHOD_IDS[label], "exact_e0": fraction_text(e0),
    })
    captured: dict[str, rta_adapter.TasksetAnalysisInput] = {}
    original_dispatch = rta4_formal_execution.dispatch_formal_rta

    def observing_dispatch(
        *, analysis_id: str, method: str,
        analysis_input: rta_adapter.TasksetAnalysisInput,
    ) -> Any:
        captured["analysis_input"] = analysis_input
        return original_dispatch(
            analysis_id=analysis_id, method=method,
            analysis_input=analysis_input,
        )

    rta4_formal_execution.dispatch_formal_rta = observing_dispatch
    try:
        _mapped, raw = rta4_formal_execution._adapter_result_v2(
            record, task_materials.certificate, config, TIMEOUT_SECONDS,
            task_materials.task_energy, service, identity_contract,
        )
    finally:
        rta4_formal_execution.dispatch_formal_rta = original_dispatch
    if "analysis_input" not in captured:
        raise T10ServiceMigrationAuditError(
            "formal adapter did not dispatch a mathematical input"
        )
    return _method_projection(raw), _input_projection(captured["analysis_input"])


def _direct_method(
    *, label: str, task_materials: _TaskMaterials,
    service: VerifiedSolarServiceMaterialV2, e0: Fraction,
) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis_input = _direct_analysis_input(task_materials, service, e0)
    raw = rta_adapter.analyze_method_taskset_v9_3(
        analysis_id=domain_hash(
            "ASAP_BLOCK:V9.3:RTA4:T10:A5:DIRECT_ANALYSIS:v1", {
                "taskset_id": task_materials.certificate.taskset_id,
                "service_identity": service.service_material_identity,
                "method": METHOD_IDS[label],
                "E0": fraction_text(e0),
            },
        ),
        method_spec=METHOD_IDS[label],
        analysis_input=analysis_input,
    )
    return _method_projection(raw), _input_projection(analysis_input)


def _migration_job(job: Mapping[str, Any]) -> dict[str, Any]:
    key = str(job["cell_key"])
    try:
        e0 = Fraction(str(job["e0"]))
        task_materials = _task_materials(job["record"])
        legacy_service = _service_material(task_materials, LEGACY_SERVICE_MODEL)
        exact_service = _service_material(task_materials, EXACT_SERVICE_MODEL)
        legacy_methods = {}
        exact_direct_methods = {}
        exact_adapter_methods = {}
        exact_input_pairs = {}
        legacy_adapter_inputs = {}
        for label in METHOD_LABELS:
            legacy_result, legacy_input = _adapter_method(
                label=label, task_materials=task_materials,
                service=legacy_service, e0=e0,
            )
            direct_result, direct_input = _direct_method(
                label=label, task_materials=task_materials,
                service=exact_service, e0=e0,
            )
            adapter_result, adapter_input = _adapter_method(
                label=label, task_materials=task_materials,
                service=exact_service, e0=e0,
            )
            legacy_methods[label] = legacy_result
            exact_direct_methods[label] = direct_result
            exact_adapter_methods[label] = adapter_result
            legacy_adapter_inputs[label] = legacy_input
            exact_input_pairs[label] = {
                "direct": direct_input,
                "adapter": adapter_input,
            }
        return {
            "cell_key": key,
            "status": "COMPLETED",
            "common_input": dict(task_materials.common_projection),
            "legacy": {
                "service_model": LEGACY_SERVICE_MODEL,
                "service_prefix": [
                    fraction_text(value)
                    for value in legacy_service.beta_prefix_j
                ],
                "service_identity": legacy_service.service_material_identity,
                "adapter_inputs": legacy_adapter_inputs,
                "methods": legacy_methods,
            },
            "exact": {
                "service_model": EXACT_SERVICE_MODEL,
                "service_prefix": [
                    fraction_text(value)
                    for value in exact_service.beta_prefix_j
                ],
                "service_identity": exact_service.service_material_identity,
                "input_pairs": exact_input_pairs,
                "direct_methods": exact_direct_methods,
                "adapter_methods": exact_adapter_methods,
            },
        }
    except Exception as exc:
        import traceback
        return {
            "cell_key": key,
            "status": "SCRIPT_FAILURE",
            "failure": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        }


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _internal_error_count(rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    for row in rows:
        if row.get("status") != "COMPLETED":
            continue
        for result_set in (
            row["legacy"]["methods"],
            row["exact"]["direct_methods"],
            row["exact"]["adapter_methods"],
        ):
            for label in METHOD_LABELS:
                if result_set[label]["solver_status"] == (
                    "INTERNAL_CONFORMANCE_FAILURE"
                ):
                    count += 1
    return count


def _migration_statistics(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, Any], list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]],
]:
    summary: dict[str, Any] = {}
    mathematical_changes = []
    identity_changes = []
    per_unit_hashes = []
    for e0 in E0_VALUES:
        selected = [row for row in rows if row["cell_key"].endswith(f"|{e0}")]
        summary[e0] = {}
        for label in METHOD_LABELS:
            table = Counter()
            response_changed = 0
            maximum_absolute_change = 0
            comparable_response_count = 0
            for row in selected:
                legacy = row["legacy"]["methods"][label]
                exact = row["exact"]["adapter_methods"][label]
                legacy_certified = bool(legacy["taskset_proven"])
                exact_certified = bool(exact["taskset_proven"])
                category = (
                    "both_certified" if legacy_certified and exact_certified else
                    "legacy_only" if legacy_certified else
                    "exact_only" if exact_certified else
                    "neither"
                )
                table[category] += 1
                if legacy["response_vector"] != exact["response_vector"]:
                    response_changed += 1
                unit_maximum = 0
                for old_task, new_task in zip(
                    legacy["task_results"], exact["task_results"],
                ):
                    old_response = old_task["candidate_response_time"]
                    new_response = new_task["candidate_response_time"]
                    if old_response is not None and new_response is not None:
                        comparable_response_count += 1
                        unit_maximum = max(
                            unit_maximum, abs(new_response - old_response),
                        )
                maximum_absolute_change = max(
                    maximum_absolute_change, unit_maximum,
                )
                identity_diffs = _deep_diffs(
                    legacy["exact_input_identity"],
                    exact["exact_input_identity"],
                    "$.exact_input_identity",
                )
                if identity_diffs:
                    identity_changes.append({
                        "cell_key": row["cell_key"],
                        "method": label,
                        "diffs": identity_diffs,
                    })
                legacy_mathematical = {
                    key: value for key, value in legacy.items()
                    if key != "exact_input_identity"
                }
                exact_mathematical = {
                    key: value for key, value in exact.items()
                    if key != "exact_input_identity"
                }
                diffs = _deep_diffs(
                    legacy_mathematical, exact_mathematical,
                )
                if diffs:
                    mathematical_changes.append({
                        "cell_key": row["cell_key"],
                        "method": label,
                        "legacy_certified": legacy_certified,
                        "exact_certified": exact_certified,
                        "diffs": diffs,
                    })
                per_unit_hashes.append({
                    "cell_key": row["cell_key"],
                    "method": label,
                    "legacy_result_sha256": _canonical_sha(legacy),
                    "exact_result_sha256": _canonical_sha(exact),
                })
            legacy_count = table["both_certified"] + table["legacy_only"]
            exact_count = table["both_certified"] + table["exact_only"]
            summary[e0][label] = {
                "taskset_count": len(selected),
                "legacy_certified": legacy_count,
                "legacy_certification_rate": f"{legacy_count}/{len(selected)}",
                "exact_certified": exact_count,
                "exact_certification_rate": f"{exact_count}/{len(selected)}",
                "both_certified": table["both_certified"],
                "legacy_only": table["legacy_only"],
                "exact_only": table["exact_only"],
                "neither": table["neither"],
                "certification_status_change_count": (
                    table["legacy_only"] + table["exact_only"]
                ),
                "response_vector_change_count": response_changed,
                "maximum_absolute_response_time_change": maximum_absolute_change,
                "comparable_task_response_count": comparable_response_count,
            }
    return summary, mathematical_changes, identity_changes, per_unit_hashes


def _report_markdown(audit: Mapping[str, Any]) -> str:
    lines = [
        "# RTA4 T10 阶段 A.5 服务合同迁移审计",
        "",
        "## 结论",
        "",
        f"- `stage_b_infrastructure_authorized = {str(audit['stage_b_infrastructure_authorized']).lower()}`",
        f"- `formal_t10_campaign_authorized = {str(audit['formal_t10_campaign_authorized']).lower()}`",
        f"- exact direct/adapter parity mismatch：{audit['exact_adapter_parity_mismatch_count']}",
        f"- exact 输入 identity mismatch：{audit['exact_input_parity_mismatch_count']}",
        f"- exact float 决策路径：{audit['exact_float_decision_path_count']}",
        f"- 预期 exact-input identity 变化：{audit['exact_input_identity_change_count']}",
        f"- 数学/认证结果变化方法单元：{audit['migration_change_method_unit_count']}",
        f"- 支配违反：{audit['dominance_violation_count']}",
        f"- 脚本失败：{audit['script_failure_count']}",
        f"- 未分类内部错误：{audit['unclassified_internal_error_count']}",
        "",
        "精确服务由 `Fraction(length, 10)` 直接构造。历史服务合同仅用于迁移审计，",
        "不作为正式 campaign 选项，也不声明等价于精确 `beta(L)=L/10`。",
        "配对审计两侧均使用 manifest 的精确有理功耗，故这里的 legacy 是服务合同",
        "对照而不是完整历史 binary64 功耗回放；完整历史复现仍由 Stage A 产物记录。",
        "",
        "## 身份与证据",
        "",
        f"- 仓库输入提交：`{audit['repository']['commit']}`",
        f"- 仓库输入 tree：`{audit['repository']['tree']}`",
        f"- Stage A 提交：`{audit['stage_a']['commit']}`",
        f"- Stage A tree：`{audit['stage_a']['tree']}`",
        f"- 外层证据归档：`{audit['evidence']['outer_archive_sha256']}`",
        f"- 内层冻结归档：`{audit['evidence']['frozen_archive_sha256']}`",
        f"- holdout：`{audit['evidence']['holdout_sha256']}`",
        f"- 冻结入口：`{audit['evidence']['spotcheck_sha256']}`",
        f"- 确认 runner：`{audit['evidence']['confirmatory_runner_sha256']}`",
        "",
        "## 执行规模",
        "",
        f"- 任务集：{audit['normalized_taskset_count']}",
        f"- taskset/E0 单元：{audit['cell_comparison_count']}",
        f"- 服务迁移方法级比较：{audit['method_comparison_count']}",
        f"- 逐任务记录：{audit['task_result_record_count']}",
        "- exact 执行入口：直接数学入口与当前正式 unified adapter/worker 入口",
        "",
        "## 配对迁移统计",
        "",
        "| E0 | 方法 | Legacy | Exact | Both | Legacy only | Exact only | Neither | 认证变化 | 响应向量变化 | 最大绝对 R 变化 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for e0 in E0_VALUES:
        for label in METHOD_LABELS:
            row = audit["migration_statistics"][e0][label]
            lines.append(
                f"| {e0} | {label} | {row['legacy_certified']} | "
                f"{row['exact_certified']} | {row['both_certified']} | "
                f"{row['legacy_only']} | {row['exact_only']} | "
                f"{row['neither']} | {row['certification_status_change_count']} | "
                f"{row['response_vector_change_count']} | "
                f"{row['maximum_absolute_response_time_change']} |"
            )
    first = audit["first_migration_change"]
    lines.extend([
        "",
        "## 首个数学或认证变化",
        "",
        "无。" if first is None else f"```json\n{json.dumps(first, ensure_ascii=False, indent=2, sort_keys=True)}\n```",
        "",
        "## 授权边界",
        "",
        "基础设施授权只表示 exact direct/adapter 等价、精确输入无 float 决策路径、",
        "身份完整、无内部错误且支配关系成立。正式 T10 campaign 仍保持未授权；",
        "必须由用户另行冻结任务生成合同、新正式种子、样本数、E0 和统计方案。",
        "",
    ])
    return "\n".join(lines)


def run_audit(
    *, evidence_root: Path, evidence_archive: Path, repository: Path,
    output_path: Path, report_path: Path, workers: int,
) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    evidence_archive = evidence_archive.resolve(strict=True)
    verified = verify_evidence(evidence_root)
    if _sha256_file(evidence_archive) != OUTER_ARCHIVE_SHA256:
        raise T10ServiceMigrationAuditError("outer evidence archive SHA mismatch")
    commit = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    if commit != STAGE_A_COMMIT or tree != STAGE_A_TREE:
        raise T10ServiceMigrationAuditError(
            "Stage-A.5 repository must start at the immutable Stage A commit/tree"
        )
    if workers < 1:
        raise T10ServiceMigrationAuditError("worker count must be positive")

    normalized = [normalize_t10_record(row) for row in verified["holdout"]]
    jobs = [
        {
            "cell_key": f"T10_BALANCED|{record['taskset_index']}|{e0}",
            "record": {
                "taskset_index": record["taskset_index"],
                "seed": record["seed"],
                "tasks": record["tasks"],
                "task_order": record["task_order"],
            },
            "e0": e0,
        }
        for e0 in E0_VALUES
        for record in normalized
    ]
    context = multiprocessing.get_context("spawn")
    rows = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        futures = {pool.submit(_migration_job, job): job["cell_key"] for job in jobs}
        for count, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if count == 1 or count % 25 == 0 or count == len(futures):
                print(f"stage_a5_progress={count}/{len(futures)}", flush=True)
    rows.sort(key=lambda row: row["cell_key"])
    expected_keys = {job["cell_key"] for job in jobs}
    if {row["cell_key"] for row in rows} != expected_keys:
        raise T10ServiceMigrationAuditError("Stage-A.5 cell coverage mismatch")

    script_failures = sum(row["status"] != "COMPLETED" for row in rows)
    completed_rows = [row for row in rows if row["status"] == "COMPLETED"]
    parity_mismatches = []
    input_mismatches = []
    identity_failures = []
    for row in completed_rows:
        for label in METHOD_LABELS:
            direct = row["exact"]["direct_methods"][label]
            adapter = row["exact"]["adapter_methods"][label]
            diffs = _deep_diffs(direct, adapter)
            if diffs:
                parity_mismatches.append({
                    "cell_key": row["cell_key"], "method": label,
                    "diffs": diffs,
                })
            input_pair = row["exact"]["input_pairs"][label]
            diffs = _deep_diffs(input_pair["direct"], input_pair["adapter"])
            if diffs:
                input_mismatches.append({
                    "cell_key": row["cell_key"], "method": label,
                    "diffs": diffs,
                })
            for entry_name, input_projection in input_pair.items():
                for field in (
                    "taskset_identity", "priority_order_identity",
                    "service_identity", "power_identity",
                    "exact_input_identity", "semantic_service_identity",
                    "semantic_power_identity",
                ):
                    if not _is_sha256(input_projection[field]):
                        identity_failures.append({
                            "cell_key": row["cell_key"],
                            "method": label,
                            "entry": entry_name,
                            "field": field,
                            "value": input_projection[field],
                        })

    (
        migration_stats, migration_changes, identity_changes,
        per_unit_hashes,
    ) = (
        _migration_statistics(completed_rows)
    )
    legacy_rows = [
        {"cell_key": row["cell_key"], "methods": row["legacy"]["methods"]}
        for row in completed_rows
    ]
    exact_rows = [
        {
            "cell_key": row["cell_key"],
            "methods": row["exact"]["adapter_methods"],
        }
        for row in completed_rows
    ]
    legacy_dominance = _dominance_violations(legacy_rows)
    exact_dominance = _dominance_violations(exact_rows)
    internal_errors = _internal_error_count(rows)
    exact_float_paths = sum(
        bool(input_pair[entry]["float_decision_path"])
        for row in completed_rows
        for input_pair in row["exact"]["input_pairs"].values()
        for entry in ("direct", "adapter")
    )
    infrastructure_authorized = bool(
        script_failures == 0
        and not parity_mismatches
        and not input_mismatches
        and exact_float_paths == 0
        and not identity_failures
        and internal_errors == 0
        and not legacy_dominance
        and not exact_dominance
        and len(completed_rows) == 352
    )
    audit = {
        "schema": AUDIT_SCHEMA,
        "repository": {"commit": commit, "tree": tree},
        "stage_a": {
            "commit": STAGE_A_COMMIT,
            "tree": STAGE_A_TREE,
            "stage_b_authorized": False,
            "source_base_commit": CURRENT_BASE_COMMIT,
            "source_base_tree": CURRENT_BASE_TREE,
        },
        "evidence": {
            "outer_archive_sha256": _sha256_file(evidence_archive),
            "frozen_archive_sha256": FROZEN_ARCHIVE_SHA256,
            "holdout_sha256": verified["holdout_sha256"],
            "spotcheck_sha256": verified["file_sha256"][
                "frozen_extracted/scripts/run_recursive_theta_spotcheck_v2.py"
            ],
            "confirmatory_runner_sha256": verified["file_sha256"][
                "runner/run_rta4_e1_t10_confirmatory_v1.py"
            ],
            "checked_file_count": verified["checked_file_count"],
        },
        "service_contracts": {
            "legacy": {
                "service_model": LEGACY_SERVICE_MODEL,
                "configured_rate": EXACT_SERVICE_RATE,
                "implementation": "FROZEN_BINARY64_INTERVAL_ACCUMULATION",
                "formal_campaign_eligible": False,
                "purpose": "AUDIT_HISTORY_REPLAY_REGRESSION_ONLY",
            },
            "exact": {
                "service_model": EXACT_SERVICE_MODEL,
                "rate": EXACT_SERVICE_RATE,
                "implementation": "Fraction(length, 10)",
                "float_conversion_allowed": False,
                "formal_mainline_service": True,
            },
        },
        "paired_input_contract": {
            "task_power_source": "EXACT_RATIONAL_MANIFEST_TEXT_V1",
            "task_power_equal_between_services": True,
            "task_order_equal_between_services": True,
            "processor_count": PROCESSORS,
            "priority_policy": "RM_STRICT_PERIOD_ASCENDING",
            "E0": list(E0_VALUES),
            "methods": list(METHOD_LABELS),
        },
        "normalized_taskset_count": len(normalized),
        "cell_comparison_count": len(jobs),
        "method_comparison_count": len(jobs) * len(METHOD_LABELS),
        "task_result_record_count": len(jobs) * len(METHOD_LABELS) * 10,
        "exact_direct_execution_count": len(completed_rows) * len(METHOD_LABELS),
        "exact_adapter_execution_count": len(completed_rows) * len(METHOD_LABELS),
        "legacy_adapter_execution_count": len(completed_rows) * len(METHOD_LABELS),
        "script_failure_count": script_failures,
        "script_failures": [
            row for row in rows if row["status"] != "COMPLETED"
        ],
        "unclassified_internal_error_count": internal_errors,
        "exact_adapter_parity_mismatch_count": len(parity_mismatches),
        "exact_input_parity_mismatch_count": len(input_mismatches),
        "exact_float_decision_path_count": exact_float_paths,
        "input_identity_failure_count": len(identity_failures),
        "dominance_violation_count": (
            len(legacy_dominance) + len(exact_dominance)
        ),
        "legacy_dominance_violation_count": len(legacy_dominance),
        "exact_dominance_violation_count": len(exact_dominance),
        "migration_statistics": migration_stats,
        "certification_status_change_total": sum(
            migration_stats[e0][label]["certification_status_change_count"]
            for e0 in E0_VALUES for label in METHOD_LABELS
        ),
        "response_vector_change_total": sum(
            migration_stats[e0][label]["response_vector_change_count"]
            for e0 in E0_VALUES for label in METHOD_LABELS
        ),
        "first_migration_change": (
            migration_changes[0] if migration_changes else None
        ),
        "migration_change_method_unit_count": len(migration_changes),
        "exact_input_identity_change_count": len(identity_changes),
        "first_expected_input_identity_change": (
            identity_changes[0] if identity_changes else None
        ),
        "first_exact_adapter_parity_mismatch": (
            parity_mismatches[0] if parity_mismatches else None
        ),
        "first_exact_input_parity_mismatch": (
            input_mismatches[0] if input_mismatches else None
        ),
        "mismatches": {
            "exact_adapter": parity_mismatches,
            "exact_input": input_mismatches,
            "identity": identity_failures,
            "legacy_dominance": legacy_dominance,
            "exact_dominance": exact_dominance,
        },
        "per_method_result_hashes": per_unit_hashes,
        "stage_b_infrastructure_authorized": infrastructure_authorized,
        "formal_t10_campaign_authorized": False,
        "formula_changes": False,
        "formal_experiment_started": False,
    }
    _write_json(output_path, audit)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_report_markdown(audit), encoding="utf-8")
    return audit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--evidence-archive", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        audit = run_audit(
            evidence_root=args.evidence_root,
            evidence_archive=args.evidence_archive,
            repository=args.repository,
            output_path=args.output,
            report_path=args.report,
            workers=args.workers,
        )
    except T10ServiceMigrationAuditError as exc:
        print(f"STAGE_A5=FAIL_CLOSED reason={exc}")
        return 2
    print(f"cell_comparison_count={audit['cell_comparison_count']}")
    print(f"method_comparison_count={audit['method_comparison_count']}")
    print(
        "exact_adapter_parity_mismatch_count="
        f"{audit['exact_adapter_parity_mismatch_count']}"
    )
    print(
        "dominance_violation_count="
        f"{audit['dominance_violation_count']}"
    )
    print(
        "stage_b_infrastructure_authorized="
        f"{str(audit['stage_b_infrastructure_authorized']).lower()}"
    )
    print(
        "formal_t10_campaign_authorized="
        f"{str(audit['formal_t10_campaign_authorized']).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
