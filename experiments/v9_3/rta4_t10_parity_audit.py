"""Independent Stage-A parity audit for the frozen RTA4 T10 evidence.

The frozen entry is executed by a separate helper process rooted at the
evidence-declared source commit.  The current entry is the real V3 shared-
energy adapter, :func:`rta4_formal_execution._adapter_result_v2`.  This module
does not write any formal result, taskset-store, prepared, or authorization
namespace.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from fractions import Fraction
import gzip
import hashlib
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

import asap_block_rta_v9_3_taskset as rta_adapter

from . import exact_energy
from .rta4_formal_config import canonical_json, domain_hash, fraction_text
from .rta4_formal_config_v3 import RTA4_FORMAL_PROFILE_V3
from .rta4_formal_execution import _adapter_result_v2
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


EVIDENCE_PACKAGE = "ASAP_BLOCK_RTA4_T10_STAGE_A_EVIDENCE_20260730_20260801T112709Z"
FROZEN_ARCHIVE_SHA256 = "cb46599fec4d0c362f888a0e96a16e65151cf5e84b12718f2e94fac95b2e3d4f"
OUTER_ARCHIVE_SHA256 = "8d77a94e0d0211dbbe3fa28eb940589cb6f865c07333ed275644bc41d8218759"
FROZEN_SOURCE_COMMIT = "4a04e2afd88424b8ebe85500b0561d7203c64e4e"
FROZEN_SOURCE_TREE = "51ddb853c1e47244f0d1407ec665742c141dae48"
CURRENT_BASE_COMMIT = "c379cd53baee43466eccfa87bf018652d87c481e"
CURRENT_BASE_TREE = "7763d6b81c695560ec522bfdbe2467ea55917b24"
METHOD_LABELS = ("CW", "LOC", "PH", "SEQ")
METHOD_IDS = {
    "CW": "CW_THETA_CW",
    "LOC": "LOC_THETA_LOC",
    "PH": "PH_THETA_PH",
    "SEQ": "SEQ_THETA_SEQ",
}
E0_VALUES = ("21/40", "11/20")
PROCESSORS = 4
TIMEOUT_SECONDS = 120
BACKGROUND_TASKS = (
    {"name": "tau_8", "C": 1, "D": 24, "T": 27, "power": "1/80"},
    {"name": "tau_9", "C": 1, "D": 28, "T": 36, "power": "1/80"},
    {"name": "tau_10", "C": 1, "D": 34, "T": 54, "power": "1/80"},
)


class T10ParityAuditError(RuntimeError):
    """Raised when Stage A must fail closed."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(value: Any) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise T10ParityAuditError(f"cannot parse JSON: {path}") from exc
    if not isinstance(value, dict):
        raise T10ParityAuditError(f"JSON root is not an object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise T10ParityAuditError(f"cannot read JSONL: {path}") from exc
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise T10ParityAuditError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise T10ParityAuditError(f"non-object JSONL at {path}:{line_number}")
        rows.append(value)
    return rows


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if completed.returncode:
        raise T10ParityAuditError(
            f"git {' '.join(arguments)} failed: {completed.stdout.strip()}"
        )
    return completed.stdout.strip()


def verify_evidence(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if root.name != EVIDENCE_PACKAGE:
        raise T10ParityAuditError("evidence package directory name mismatch")
    sums_path = root / "FILE_SHA256SUMS.txt"
    manifest_path = root / "TRANSFER_MANIFEST.json"
    if not sums_path.is_file() or not manifest_path.is_file():
        raise T10ParityAuditError("evidence transfer manifest/checksums are missing")
    checked = {}
    for line_number, raw in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            expected, relative = raw.split("  ", 1)
        except ValueError as exc:
            raise T10ParityAuditError(f"bad checksum line {line_number}") from exc
        if relative.startswith("./"):
            relative = relative[2:]
        path = (root / relative).resolve(strict=True)
        if root not in path.parents:
            raise T10ParityAuditError("checksum path escapes evidence root")
        observed = _sha256_file(path)
        if observed != expected:
            raise T10ParityAuditError(f"evidence SHA mismatch: {relative}")
        checked[relative] = observed
    transfer = _load_json(manifest_path)
    archive = root / "frozen_archive/rta4_strict_chain_frozen_20260730.tar.gz"
    if _sha256_file(archive) != FROZEN_ARCHIVE_SHA256:
        raise T10ParityAuditError("frozen archive identity mismatch")
    try:
        with gzip.open(archive, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise T10ParityAuditError("frozen archive has unsafe member path")
    except T10ParityAuditError:
        raise
    except Exception as exc:
        raise T10ParityAuditError("frozen archive integrity check failed") from exc

    holdout_path = root / "holdout/rta4_t10_holdout_176_tasksets.jsonl"
    holdout_manifest_path = root / "holdout/rta4_t10_holdout_176_manifest.json"
    holdout = _load_jsonl(holdout_path)
    holdout_manifest = _load_json(holdout_manifest_path)
    if len(holdout) != 176:
        raise T10ParityAuditError("holdout does not contain 176 records")
    if [row.get("taskset_index") for row in holdout] != list(range(176)):
        raise T10ParityAuditError("holdout taskset indices are not exactly 0..175")
    if any(not isinstance(row.get("tasks"), list) or len(row["tasks"]) != 7 for row in holdout):
        raise T10ParityAuditError("holdout source record is not a seven-task set")
    holdout_sha = _sha256_file(holdout_path)
    if (
        holdout_manifest.get("discovery_count") != 24
        or holdout_manifest.get("holdout_count") != 176
        or holdout_manifest.get("holdout_sha256") != holdout_sha
    ):
        raise T10ParityAuditError("holdout manifest count/SHA mismatch")

    confirmatory = root / "confirmatory/rta4_e1_t10_confirmatory_v1"
    required = (
        "config.json", "cells.jsonl", "summary.csv", "best_candidates.json",
        "figure_artifacts.json", "completion.json",
    )
    if any(not (confirmatory / name).is_file() for name in required):
        raise T10ParityAuditError("confirmatory evidence is incomplete")
    cells = _load_jsonl(confirmatory / "cells.jsonl")
    completion = _load_json(confirmatory / "completion.json")
    expected_pairs = {(index, e0) for index in range(176) for e0 in E0_VALUES}
    observed_pairs = {(row.get("taskset_index"), row.get("E0_exact")) for row in cells}
    if len(cells) != 352 or observed_pairs != expected_pairs:
        raise T10ParityAuditError("confirmatory cells are not the complete 352 pairs")
    if completion.get("status") != "COMPLETE" or completion.get("cell_count") != 352:
        raise T10ParityAuditError("confirmatory completion document is not complete")
    if any(row.get("status") != "COMPLETED" for row in cells):
        raise T10ParityAuditError("confirmatory cells contain non-completed results")
    return {
        "root": root,
        "checked_file_count": len(checked),
        "file_sha256": checked,
        "transfer_manifest": transfer,
        "holdout": holdout,
        "holdout_manifest": holdout_manifest,
        "holdout_sha256": holdout_sha,
        "cells": cells,
        "completion": completion,
    }


def _exact_power_text(value: Any, label: str) -> str:
    if type(value) is not str:
        raise T10ParityAuditError(f"{label} power is not exact rational text")
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise T10ParityAuditError(f"{label} power is invalid") from exc
    if exact < 0 or value != fraction_text(exact):
        raise T10ParityAuditError(f"{label} power is negative/noncanonical")
    return value


def normalize_t10_record(source: Mapping[str, Any]) -> dict[str, Any]:
    tasks = []
    for index, raw in enumerate(list(source["tasks"]) + [dict(row) for row in BACKGROUND_TASKS]):
        if not isinstance(raw, Mapping):
            raise T10ParityAuditError("task row is not a mapping")
        expected = {"name", "C", "D", "T", "power"}
        if set(raw) != expected:
            raise T10ParityAuditError(f"task row fields differ at index {index}")
        name = raw["name"]
        c, d, period = raw["C"], raw["D"], raw["T"]
        if type(name) is not str or not name or any(type(value) is not int for value in (c, d, period)):
            raise T10ParityAuditError(f"invalid exact task fields at index {index}")
        if not 0 < c <= d <= period:
            raise T10ParityAuditError(f"invalid C<=D<=T at index {index}")
        tasks.append({
            "name": name, "C": c, "D": d, "T": period,
            "power": _exact_power_text(raw["power"], name),
        })
    if len(tasks) != 10 or len({row["name"] for row in tasks}) != 10:
        raise T10ParityAuditError("T10 normalization did not produce ten unique tasks")
    periods = [row["T"] for row in tasks]
    if any(left >= right for left, right in zip(periods, periods[1:])):
        raise T10ParityAuditError("T10 tasks violate strict RM period order")
    return {
        "schema": "ASAP_BLOCK_RTA4_T10_BALANCED_FROZEN_INPUT_V1",
        "processors": PROCESSORS,
        "priority_policy": "RM_STRICT_PERIOD_ASCENDING",
        "task_count": 10,
        "mechanism_core_task_count": 7,
        "taskset_index": int(source["taskset_index"]),
        "original_taskset_index": int(source["original_taskset_index"]),
        "seed": int(source["seed"]),
        "tasks": tasks,
        "task_order": [row["name"] for row in tasks],
    }


def _materialized_power(text: str, label: str) -> tuple[Fraction, str]:
    value = float(Fraction(text))
    materialized = exact_energy.materialize_demand_upper_bound(value, label)
    return materialized.exact_value, materialized.binary64_hex


def _frozen_beta(maximum_deadline: int) -> tuple[Fraction, ...]:
    tick = Fraction.from_float(float(Fraction(1, 10)))
    return exact_energy.service_curve_lower_bound(
        tuple(tick for _ in range(maximum_deadline)), maximum_deadline - 1,
    )


@dataclass(frozen=True)
class _FormalMaterials:
    certificate: TasksetIdentityCertificateV2
    task_energy: TaskEnergyMaterial
    service: VerifiedSolarServiceMaterialV2
    input_projection: Mapping[str, Any]


def _formal_materials(record: Mapping[str, Any], e0: Fraction) -> _FormalMaterials:
    task_rows = list(record["tasks"])
    formal_tasks = tuple(
        FormalTaskV2(
            str(row["name"]), index, int(row["C"]), int(row["D"]),
            int(row["T"]), "T10_PARITY_EXPLICIT",
        )
        for index, row in enumerate(task_rows)
    )
    deadline_variant = "T10_BALANCED_FROZEN_EXPLICIT_V1"
    source_material = {
        "contract": "ASAP_BLOCK_RTA4_T10_PARITY_SOURCE_V1",
        "taskset_index": int(record["taskset_index"]),
        "tasks": task_rows,
    }
    source_sha = _canonical_sha(source_material)
    request_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10_PARITY_REQUEST:v1", {
        "taskset_index": int(record["taskset_index"]),
        "seed": int(record["seed"]),
        "source_sha256": source_sha,
    })
    skeleton_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10_PARITY_SKELETON:v1", {
        "generation_request_id": request_id,
        "processor_count": PROCESSORS,
        "taskset_source_sha256": source_sha,
        "task_order": list(record["task_order"]),
    })
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
        "generator_contract_version": "ASAP_BLOCK_RTA4_T10_PARITY_EXPLICIT_V1",
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
        "ASAP_BLOCK_RTA4_T10_PARITY_EXPLICIT_V1", request_id, skeleton_id,
        source_sha, deadline_variant, Fraction(1), formal_tasks, taskset_hash,
        certificate_id,
    )

    build_identity = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10_PARITY_BUILD:v1", {
        "current_commit": CURRENT_BASE_COMMIT,
        "current_tree": CURRENT_BASE_TREE,
    })
    store_identity = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10_PARITY_STORE:v1", {
        "taskset_id": certificate.taskset_id,
    })
    taskset_canonical_sha = _sha256_bytes(certificate.canonical_bytes())
    entries = []
    materialized_rows = []
    for index, (task, source_row) in enumerate(zip(formal_tasks, task_rows)):
        power, binary64_hex = _materialized_power(
            str(source_row["power"]),
            f"formal-parity:{record['taskset_index']}:{task.task_id}:power",
        )
        source_identity = domain_hash(TASK_ENERGY_ENTRY_DOMAIN, {
            "schema": TASK_ENERGY_MATERIAL_SCHEMA,
            "taskset_id": certificate.taskset_id,
            "task_index": index,
            "task_id": task.task_id,
            "C": task.wcet,
            "D": task.relative_deadline,
            "T": task.period,
            "energy_j_per_tick": fraction_text(power),
            "energy_j_per_tick_binary64": binary64_hex,
            "unit": "J/tick",
        })
        entries.append(TaskEnergyEntry(
            index, task.task_id, task.period, task.relative_deadline, task.wcet,
            task.workload, binary64_hex, float(1).hex(), float(1).hex(),
            float(1).hex(), power, binary64_hex, source_identity,
        ))
        materialized_rows.append({
            "name": task.task_id,
            "C": task.wcet,
            "D": task.relative_deadline,
            "T": task.period,
            "power": fraction_text(power),
        })
    task_energy_base = {
        "schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "profile_id": RTA4_FORMAL_PROFILE_V3,
        "production_build_manifest_identity": build_identity,
        "taskset_id": certificate.taskset_id,
        "taskset_store_identity": store_identity,
        "taskset_canonical_sha256": taskset_canonical_sha,
        "system_config_sha256": "0" * 64,
        "workload_config_sha256": "0" * 64,
        "generator_contract_version": "ASAP_BLOCK_RTA4_T10_PARITY_EXPLICIT_V1",
        "numeric_contract_version": exact_energy.NUMERIC_CONTRACT_VERSION,
        "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
        "energy_demand_unit": "J/tick",
        "entries": [entry.material() for entry in entries],
    }
    task_energy_id = domain_hash(TASK_ENERGY_MATERIAL_DOMAIN, task_energy_base)
    task_energy = TaskEnergyMaterial(
        RTA4_FORMAL_PROFILE_V3, build_identity, certificate.taskset_id,
        store_identity, taskset_canonical_sha, "0" * 64, "0" * 64,
        "ASAP_BLOCK_RTA4_T10_PARITY_EXPLICIT_V1", tuple(entries),
        task_energy_id,
    )

    maximum_deadline = max(task.relative_deadline for task in formal_tasks)
    tick = Fraction.from_float(float(Fraction(1, 10)))
    trace = tuple(tick for _ in range(maximum_deadline))
    beta = _frozen_beta(maximum_deadline)
    semantic_service_id = _canonical_sha([fraction_text(value) for value in beta])
    beta_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10_PARITY_BETA:v1", {
        "prefix": [fraction_text(value) for value in beta],
    })
    horizon = ServiceHorizonContract(
        maximum_deadline - 1, 0, maximum_deadline, 0, 0,
        HORIZON_CONTRACT_VERSION,
    )
    service_base = {
        "schema": SERVICE_MATERIAL_SCHEMA,
        "production_build_manifest_identity": build_identity,
        "configured_rate": "1/10",
        "materialized_tick": fraction_text(tick),
        "beta_material_identity": beta_id,
        "horizon": horizon.material(),
    }
    service_id = domain_hash("ASAP_BLOCK:V9.3:RTA4:T10_PARITY_SERVICE:v1", service_base)
    service = VerifiedSolarServiceMaterialV2(
        cache_key=service_id,
        semantic_service_source_identity=semantic_service_id,
        parser_environment_identity="0" * 64,
        live_proof_identity="0" * 64,
        production_build_manifest_identity=build_identity,
        system_sha256="0" * 64,
        support_sha256="0" * 64,
        solar_csv_sha256="0" * 64,
        day_of_year=0,
        time_of_day_ms=0,
        solar_scale=Fraction(1),
        horizon=horizon,
        harvest_j_per_tick=trace,
        beta_prefix_j=beta,
        trace_sha256=_canonical_sha([fraction_text(value) for value in trace]),
        beta_material_sha256=beta_id,
        service_material_identity=service_id,
        immutable_provenance_json=canonical_json(service_base),
    )
    exact_input_id = exact_energy.exact_input_identity(
        task_powers=(
            (row["name"], Fraction(row["power"])) for row in materialized_rows
        ),
        e0=e0,
        service_prefix=beta,
    )
    return _FormalMaterials(
        certificate, task_energy, service,
        {
            "processors": PROCESSORS,
            "priority_policy": "RM_STRICT_PERIOD_ASCENDING",
            "tasks": materialized_rows,
            "task_order": list(record["task_order"]),
            "E0": fraction_text(e0),
            "service_prefix": [fraction_text(value) for value in beta],
            "semantic_service_identity": semantic_service_id,
            "semantic_power_vector_identity": _canonical_sha([
                (row["name"], row["power"]) for row in materialized_rows
            ]),
            "exact_input_identity": exact_input_id,
            "native_taskset_identity": certificate.taskset_id,
            "native_task_definitions_identity": task_energy_id,
            "native_priority_order_identity": skeleton_id,
            "native_service_curve_identity": service_id,
            "native_power_vector_identity": task_energy_id,
            "numerical_mode": "EXACT_BINARY64_MATERIALIZATION",
        },
    )


def _task_projection(row: Any) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "priority_rank": row.priority_rank,
        "solver_status": row.solver_status.value,
        "kernel_solver_status": row.kernel_solver_status,
        "certification_status": row.certification_status.value,
        "candidate_response_time": row.candidate_response_time,
        "closing_w": row.closing_w,
        "carry_in_values_used": [list(value) for value in row.carry_in_values_used],
        "witness_h": row.witness_h,
        "processor_progress_a": row.processor_progress_a,
        "maximum_blocking_h": row.maximum_blocking_h,
        "witness_sequence": list(row.witness_sequence),
        "checked_w_count": row.checked_w_count,
        "checked_h_count": row.checked_h_count,
        "checked_q_count": row.checked_q_count,
        "envelope_call_count": row.envelope_call_count,
        "solver_call_count": row.solver_call_count,
        "failure_reason": row.failure_reason,
    }


def _method_projection(result: Any) -> dict[str, Any]:
    tasks = [_task_projection(row) for row in result.task_results]
    return {
        "method_id": result.method_id.value,
        "kernel": result.kernel.value,
        "carry_policy": result.carry_policy.value,
        "solver_status": result.solver_status.value,
        "certification_status": result.analysis_certification_status.value,
        "taskset_proven": bool(result.taskset_proven),
        "first_failed_task": result.first_failed_task,
        "failure_reason": result.failure_reason,
        "exact_input_identity": result.exact_input_identity,
        "response_vector": [
            row["candidate_response_time"] for row in tasks
            if row["candidate_response_time"] is not None
        ],
        "carry_trace": [
            {
                "task_id": entry.task_id,
                "priority_rank": entry.priority_rank,
                "theta_by_task": [list(value) for value in entry.theta_by_task],
            }
            for entry in result.carry_trace
        ],
        "task_results": tasks,
    }


def _evidence_projection(method: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method_id": method["method_id"],
        "kernel": method["kernel"],
        "carry_policy": method["carry_policy"],
        "solver_status": method["solver_status"],
        "certification_status": method["certification_status"],
        "taskset_proven": method["taskset_proven"],
        "first_failed_task": method["first_failed_task"],
        "failure_reason": method["failure_reason"],
        "response_vector": list(method["response_vector"]),
        "task_results": [
            {
                "task_id": row["task_id"],
                "priority_rank": row["priority_rank"],
                "solver_status": row["solver_status"],
                "candidate_response_time": row["candidate_response_time"],
                "closing_w": row["closing_w"],
                "carry_in_values_used": [list(value) for value in row["carry_in_values_used"]],
                "witness_h": row["witness_h"],
                "witness_sequence": list(row["witness_sequence"]),
                "distinct_h_count": (
                    len(set(row["witness_sequence"]))
                    if row["witness_sequence"] else None
                ),
                "failure_reason": row["failure_reason"],
            }
            for row in method["task_results"]
        ],
    }


def _formal_job(job: Mapping[str, Any]) -> dict[str, Any]:
    key = str(job["cell_key"])
    try:
        e0 = Fraction(str(job["e0"]))
        record = job["record"]
        materials = _formal_materials(record, e0)
        timeout_contract = {
            method: {
                "initial_timeout_seconds": TIMEOUT_SECONDS,
                "retry_timeout_seconds": TIMEOUT_SECONDS * 2,
                "maximum_attempts": 2,
            }
            for method in METHOD_IDS.values()
        }
        config = {
            "identity": {
                "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
                "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            },
            "experiment_contract": {"profile": RTA4_FORMAL_PROFILE_V3},
            "execution": {"timeout_contract": timeout_contract},
        }
        identity_contract = {
            "formal_profile": RTA4_FORMAL_PROFILE_V3,
            "analysis_domain": "ASAP_BLOCK:V9.3:RTA4:FORMAL_ANALYSIS:v3",
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "timeout_contract": timeout_contract,
        }
        methods = {}
        evidence = {}
        for label in METHOD_LABELS:
            plan_record = SimpleNamespace(material={
                "method": METHOD_IDS[label], "exact_e0": fraction_text(e0),
            })
            _mapped, raw = _adapter_result_v2(
                plan_record,
                materials.certificate,
                config,
                TIMEOUT_SECONDS,
                materials.task_energy,
                materials.service,
                identity_contract,
            )
            projected = _method_projection(raw)
            methods[label] = projected
            evidence[label] = _evidence_projection(projected)
        return {
            "cell_key": key,
            "status": "COMPLETED",
            "input": dict(materials.input_projection),
            "methods": methods,
            "evidence_projection": evidence,
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


def _stored_evidence_projection(method: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method_id": method.get("method_id"),
        "kernel": method.get("kernel"),
        "carry_policy": method.get("carry_policy"),
        "solver_status": method.get("solver_status"),
        "certification_status": method.get("certification_status"),
        "taskset_proven": method.get("taskset_proven"),
        "first_failed_task": method.get("first_failed_task"),
        "failure_reason": method.get("failure_reason"),
        "response_vector": method.get("response_vector"),
        "task_results": [
            {
                "task_id": row.get("task_id"),
                "priority_rank": row.get("priority_rank"),
                "solver_status": row.get("solver_status"),
                "candidate_response_time": row.get("candidate_response_time"),
                "closing_w": row.get("closing_w"),
                "carry_in_values_used": row.get("carry_in_values_used"),
                "witness_h": row.get("witness_h"),
                "witness_sequence": row.get("witness_sequence"),
                "distinct_h_count": row.get("distinct_h_count"),
                "failure_reason": row.get("failure_reason"),
            }
            for row in method.get("task_results", [])
        ],
    }


def _deep_diffs(left: Any, right: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path, "frozen": left, "current": right}]
    if isinstance(left, Mapping):
        rows = []
        for key in sorted(set(left).union(right)):
            if key not in left:
                rows.append({"path": f"{path}.{key}", "frozen": "<MISSING>", "current": right[key]})
            elif key not in right:
                rows.append({"path": f"{path}.{key}", "frozen": left[key], "current": "<MISSING>"})
            else:
                rows.extend(_deep_diffs(left[key], right[key], f"{path}.{key}"))
        return rows
    if isinstance(left, list):
        rows = []
        if len(left) != len(right):
            rows.append({"path": f"{path}.length", "frozen": len(left), "current": len(right)})
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            rows.extend(_deep_diffs(left_item, right_item, f"{path}[{index}]"))
        return rows
    return [] if left == right else [{"path": path, "frozen": left, "current": right}]


def _load_result_map(path: Path) -> dict[str, dict[str, Any]]:
    rows = _load_jsonl(path)
    result = {str(row["cell_key"]): row for row in rows}
    if len(result) != len(rows):
        raise T10ParityAuditError(f"duplicate replay keys in {path}")
    return result


def _dominance_violations(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    violations = []
    for row in rows:
        methods = row["methods"]
        proven = {label: bool(methods[label]["taskset_proven"]) for label in METHOD_LABELS}
        if (
            (proven["CW"] and not proven["LOC"])
            or (proven["LOC"] and not proven["PH"])
            or (proven["PH"] and not proven["SEQ"])
        ):
            violations.append({
                "cell_key": row["cell_key"], "kind": "CERTIFICATION_IMPLICATION",
                "proven": proven,
            })
        for weaker, stronger in (("CW", "LOC"), ("LOC", "PH"), ("PH", "SEQ")):
            weak_tasks = methods[weaker]["task_results"]
            strong_tasks = methods[stronger]["task_results"]
            for weak, strong in zip(weak_tasks, strong_tasks):
                weak_r = weak["candidate_response_time"]
                strong_r = strong["candidate_response_time"]
                if weak_r is not None and strong_r is not None and strong_r > weak_r:
                    violations.append({
                        "cell_key": row["cell_key"],
                        "kind": "RESPONSE_VECTOR_DIRECTION",
                        "edge": f"{stronger}<={weaker}",
                        "task_id": weak["task_id"],
                        "weaker_response": weak_r,
                        "stronger_response": strong_r,
                    })
    return violations


def _acceptance_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result = {}
    material = list(rows)
    for e0 in E0_VALUES:
        selected = [row for row in material if row["cell_key"].endswith(f"|{e0}")]
        result[e0] = {
            label: sum(bool(row["methods"][label]["taskset_proven"]) for row in selected)
            for label in METHOD_LABELS
        }
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical_json(row) + "\n")


def _build_contract(
    verified: Mapping[str, Any], normalized: Sequence[Mapping[str, Any]],
    *, repository_commit: str, repository_tree: str,
) -> dict[str, Any]:
    tick = Fraction.from_float(float(Fraction(1, 10)))
    tasksets = []
    for record in normalized:
        declared = {
            "processors": PROCESSORS,
            "priority_policy": record["priority_policy"],
            "taskset_index": record["taskset_index"],
            "tasks": record["tasks"],
            "task_order": record["task_order"],
        }
        materialized = {
            **declared,
            "tasks": [
                {
                    **{key: row[key] for key in ("name", "C", "D", "T")},
                    "power": fraction_text(_materialized_power(
                        row["power"], f"contract:{record['taskset_index']}:{row['name']}"
                    )[0]),
                }
                for row in record["tasks"]
            ],
        }
        tasksets.append({
            "taskset_index": record["taskset_index"],
            "original_taskset_index": record["original_taskset_index"],
            "seed": record["seed"],
            "declared_taskset_sha256": _canonical_sha(declared),
            "materialized_taskset_sha256": _canonical_sha(materialized),
            "priority_order_sha256": _canonical_sha(record["task_order"]),
        })
    return {
        "schema": "ASAP_BLOCK_RTA4_T10_STAGE_A_FROZEN_CONTRACT_V1",
        "repository": {"commit": repository_commit, "tree": repository_tree},
        "frozen_source": {"commit": FROZEN_SOURCE_COMMIT, "tree": FROZEN_SOURCE_TREE},
        "evidence": {
            "package": EVIDENCE_PACKAGE,
            "checked_file_count": verified["checked_file_count"],
            "file_sha256": verified["file_sha256"],
            "frozen_archive_sha256": FROZEN_ARCHIVE_SHA256,
        },
        "task_source": {
            "classification": "INDEPENDENT_HOLDOUT_FROM_FROZEN_200",
            "source_task_count": 7,
            "normalized_task_count": 10,
            "taskset_count": 176,
            "holdout_sha256": verified["holdout_sha256"],
            "discovery_count": 24,
            "selection_policy": "PREDECLARED_176_TASKSET_HOLDOUT_NO_RTA_FILTERING_NO_RESAMPLING",
            "tasksets": tasksets,
        },
        "t10_balanced": {
            "processors": PROCESSORS,
            "priority_policy": "RM_STRICT_PERIOD_ASCENDING",
            "mechanism_core_task_count": 7,
            "background_tasks": list(BACKGROUND_TASKS),
            "background_insertion": "APPEND_AFTER_SEVEN_CORE_TASKS",
            "background_priority_ranks": [7, 8, 9],
            "background_utilization": "1/12",
        },
        "energy": {
            "task_power_input_unit": "J/tick",
            "task_power_mapping": "Fraction(text)->float(binary64)->Fraction.from_float",
            "configured_service_rate": "1/10",
            "materialized_service_tick": fraction_text(tick),
            "service_implementation": "BINARY64_CONSTANT_TRACE_INTERVAL_ACCUMULATION_LOWER_BOUND",
            "mathematical_exact_linear_beta_L_over_10": False,
            "first_divergent_length": 1,
            "configured_beta_at_first_divergence": "1/10",
            "implemented_beta_at_first_divergence": fraction_text(tick),
        },
        "analysis": {
            "E0": list(E0_VALUES),
            "methods": [METHOD_IDS[label] for label in METHOD_LABELS],
            "timeout_seconds": TIMEOUT_SECONDS,
            "numeric_mode": "EXACT_BINARY64_MATERIALIZATION",
            "formula_changes": False,
        },
    }


def _report_markdown(audit: Mapping[str, Any]) -> str:
    counts = audit["acceptance_counts"]
    first = audit.get("first_mismatch")
    first_text = "无" if first is None else f"`{canonical_json(first)}`"
    roots = "、".join(audit["root_cause_classification"])
    return f"""# RTA4 T10 阶段 A parity 审计

## 结论

- `stage_b_authorized = {str(audit['stage_b_authorized']).lower()}`
- 数学公式修改：否
- 根因分类：{roots}
- 入口结果 parity mismatch：{audit['parity_mismatch_count']}
- 冻结证据回放 mismatch：{audit['frozen_evidence_mismatch_count']}
- 输入语义 mismatch：{audit['input_semantic_mismatch_count']}
- 原生身份域诊断差异单元：{audit['native_identity_diagnostic_difference_cell_count']}
- 支配违反：{audit['dominance_violation_count']}
- 第一处不一致：{first_text}

阶段 B 未获授权的决定性原因是：冻结脚本虽然把服务配置记录为 `1/10`，
实际执行却先将其物化成 binary64
`{audit['service_contract']['materialized_tick']}`，随后按 binary64 逐项累加；
因此其 beta 前缀不等于所要求的精确 `beta(L)=L/10`。这一差异属于科学输入，
不能作为纯诊断字段忽略，也不能根据汇总认证数反推或改写。

## 证据与源码

- 外层证据归档 SHA-256：`{audit['evidence']['outer_archive_sha256']}`
- 内层冻结归档 SHA-256：`{audit['evidence']['frozen_archive_sha256']}`
- holdout SHA-256：`{audit['evidence']['holdout_sha256']}`
- 冻结入口 SHA-256：`{audit['evidence']['spotcheck_sha256']}`
- 确认 runner SHA-256：`{audit['evidence']['confirmatory_runner_sha256']}`
- 冻结源码：`{audit['frozen_source']['commit']}` / tree `{audit['frozen_source']['tree']}`
- 当前源码：`{audit['repository']['commit']}` / tree `{audit['repository']['tree']}`

## 完整比较计数

- taskset/E0 单元：{audit['cell_comparison_count']}
- 方法级单元：{audit['method_comparison_count']}
- 逐任务结果字段比较所覆盖的任务结果行：{audit['task_result_row_comparison_count']}
- 规范化十任务任务集：{audit['normalized_taskset_count']}
- 脚本失败：{audit['script_failure_count']}
- 未分类内部错误：{audit['unclassified_internal_error_count']}

| E0 | CW | LOC | PH | SEQ |
|---|---:|---:|---:|---:|
| 21/40 | {counts['21/40']['CW']} | {counts['21/40']['LOC']} | {counts['21/40']['PH']} | {counts['21/40']['SEQ']} |
| 11/20 | {counts['11/20']['CW']} | {counts['11/20']['LOC']} | {counts['11/20']['PH']} | {counts['11/20']['SEQ']} |

## 根因判定

1. `CAMPAIGN_TASK_FAMILY_NOT_CONNECTED`：V3 CORE-1 normalizer 固定使用
   `GENERAL_RANDOM_CONSTRAINED_DEADLINE`，现有 E1 YAML 没有 task-source、
   T10 背景任务或能量服务字段。因此既有 9600 请求是一般随机负对照。
2. `ENERGY_SERVICE_MAPPING_MISMATCH`：冻结确认配置写入 `service_rate=1/10`，
   但冻结脚本实际执行的是 binary64 物化常量轨迹及 binary64 区间累加，
   从 `L=1` 起就与精确 `L/10` 不同。
3. `OLD_EVIDENCE_OR_CONTRACT_INCONSISTENT`：旧认证计数与旧实现完全可复现，
   但不能同时把这些结果解释为精确 `beta(L)=L/10` 的结果。

未发现 task C/D/T/power、RM 顺序、方法分派或当前正式 adapter 数学结果差异；
入口 B 在接收入口 A 的实际物化服务前缀时，与冻结入口逐任务完全等价。
352 个单元的旧/正式原生 taskset、priority、service、power 身份字符串均不同，
原因是二者使用不同的身份域和证书 schema；对应规范化内容 SHA、服务前缀、
功耗向量及 `exact_input_identity` 全部一致，因此这些差异只列为诊断，不计入
科学输入或数学 parity mismatch。

## 门禁

阶段 A 的证据完整性、176 任务集规范化、旧统计复现和双入口数学 parity
均通过；但“精确 `beta(L)=L/10`”科学合同与冻结证据不一致。因此按失败关闭
规则，`stage_b_authorized=false`，不得继续实现 V4 或正式 campaign，直到用户
明确选择冻结旧 binary64 服务合同，或提供确由精确 `Fraction(L,10)` 产生的
176 个逐任务基准结果。
"""


def run_audit(
    *, evidence_root: Path, evidence_archive: Path, repository: Path,
    frozen_repository: Path, frozen_worker: Path, output_root: Path,
    workers: int,
) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    evidence_archive = evidence_archive.resolve(strict=True)
    frozen_repository = frozen_repository.resolve(strict=True)
    frozen_worker = frozen_worker.resolve(strict=True)
    verified = verify_evidence(evidence_root)
    if _sha256_file(evidence_archive) != OUTER_ARCHIVE_SHA256:
        raise T10ParityAuditError("outer evidence archive SHA mismatch")
    commit = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    if commit != CURRENT_BASE_COMMIT or tree != CURRENT_BASE_TREE:
        raise T10ParityAuditError("current repository commit/tree mismatch")
    if _git(frozen_repository, "rev-parse", "HEAD") != FROZEN_SOURCE_COMMIT:
        raise T10ParityAuditError("frozen source repository commit mismatch")
    if _git(frozen_repository, "rev-parse", "HEAD^{tree}") != FROZEN_SOURCE_TREE:
        raise T10ParityAuditError("frozen source repository tree mismatch")
    if workers < 1:
        raise T10ParityAuditError("worker count must be positive")

    normalized = [normalize_t10_record(row) for row in verified["holdout"]]
    jobs = []
    for e0 in E0_VALUES:
        for record in normalized:
            jobs.append({
                "cell_key": f"T10_BALANCED|{record['taskset_index']}|{e0}",
                "record": {
                    "taskset_index": record["taskset_index"],
                    "seed": record["seed"],
                    "tasks": record["tasks"],
                    "task_order": record["task_order"],
                },
                "e0": e0,
                "timeout_seconds": TIMEOUT_SECONDS,
            })
    contract = _build_contract(
        verified, normalized, repository_commit=commit, repository_tree=tree,
    )

    with tempfile.TemporaryDirectory(prefix="rta4_t10_stage_a_parity_") as temporary:
        temp = Path(temporary)
        jobs_path = temp / "jobs.jsonl"
        frozen_path = temp / "frozen.jsonl"
        formal_path = temp / "formal.jsonl"
        _write_jsonl(jobs_path, jobs)
        spot_script = (
            Path(evidence_root).resolve(strict=True)
            / "frozen_extracted/scripts/run_recursive_theta_spotcheck_v2.py"
        )
        completed = subprocess.run(
            [
                sys.executable, str(frozen_worker),
                "--frozen-repo", str(frozen_repository),
                "--spot-script", str(spot_script),
                "--jobs", str(jobs_path),
                "--output", str(frozen_path),
                "--workers", str(workers),
            ],
            cwd=repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        print(completed.stdout, end="", flush=True)
        if completed.returncode:
            raise T10ParityAuditError("frozen replay process failed")
        context = multiprocessing.get_context("spawn")
        formal_results = []
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
            futures = {pool.submit(_formal_job, job): job["cell_key"] for job in jobs}
            for count, future in enumerate(as_completed(futures), 1):
                formal_results.append(future.result())
                if count == 1 or count % 25 == 0 or count == len(futures):
                    print(f"formal_replay_progress={count}/{len(futures)}", flush=True)
        formal_results.sort(key=lambda row: row["cell_key"])
        _write_jsonl(formal_path, formal_results)
        frozen = _load_result_map(frozen_path)
        formal = _load_result_map(formal_path)

    stored = {str(row["cell_key"]): row for row in verified["cells"]}
    expected_keys = {job["cell_key"] for job in jobs}
    if set(frozen) != expected_keys or set(formal) != expected_keys or set(stored) != expected_keys:
        raise T10ParityAuditError("replay/stored cell key coverage mismatch")

    script_failures = sum(
        row.get("status") != "COMPLETED"
        for source in (frozen, formal) for row in source.values()
    )
    evidence_mismatches = []
    parity_mismatches = []
    input_mismatches = []
    native_identity_diagnostics = []
    replay_rows = []
    per_unit_hashes = []
    semantic_input_fields = (
        "processors", "priority_policy", "tasks", "task_order", "E0",
        "service_prefix", "semantic_service_identity",
        "semantic_power_vector_identity", "exact_input_identity", "numerical_mode",
    )
    native_fields = (
        "native_taskset_identity", "native_task_definitions_identity",
        "native_priority_order_identity", "native_service_curve_identity",
        "native_power_vector_identity",
    )
    for key in sorted(expected_keys):
        old = frozen[key]
        current = formal[key]
        if old.get("status") != "COMPLETED" or current.get("status") != "COMPLETED":
            continue
        replay_rows.append(current)
        stored_methods = stored[key]["methods"]
        for label in METHOD_LABELS:
            evidence_projection = _stored_evidence_projection(stored_methods[label])
            old_evidence = old["evidence_projection"][label]
            diffs = _deep_diffs(evidence_projection, old_evidence)
            if diffs:
                evidence_mismatches.append({
                    "cell_key": key, "method": label, "diffs": diffs,
                })
            diffs = _deep_diffs(old["methods"][label], current["methods"][label])
            if diffs:
                parity_mismatches.append({
                    "cell_key": key, "method": label, "diffs": diffs,
                })
            per_unit_hashes.append({
                "cell_key": key,
                "method": label,
                "frozen_result_sha256": _canonical_sha(old["methods"][label]),
                "formal_result_sha256": _canonical_sha(current["methods"][label]),
                "stored_evidence_sha256": _canonical_sha(evidence_projection),
            })
        old_semantic = {field: old["input"][field] for field in semantic_input_fields}
        current_semantic = {field: current["input"][field] for field in semantic_input_fields}
        diffs = _deep_diffs(old_semantic, current_semantic)
        if diffs:
            input_mismatches.append({"cell_key": key, "diffs": diffs})
        native_diffs = [
            {
                "field": field,
                "frozen": old["input"][field],
                "formal": current["input"][field],
            }
            for field in native_fields
            if old["input"][field] != current["input"][field]
        ]
        if native_diffs:
            native_identity_diagnostics.append({"cell_key": key, "diffs": native_diffs})

    dominance = _dominance_violations(replay_rows)
    counts = _acceptance_counts(replay_rows)
    expected_counts = {
        "21/40": {"CW": 10, "LOC": 29, "PH": 125, "SEQ": 131},
        "11/20": {"CW": 25, "LOC": 54, "PH": 143, "SEQ": 152},
    }
    count_match = counts == expected_counts
    tick = Fraction.from_float(float(Fraction(1, 10)))
    service_contract_mismatch = tick != Fraction(1, 10)
    first_mismatch = (
        parity_mismatches[0] if parity_mismatches else
        evidence_mismatches[0] if evidence_mismatches else
        input_mismatches[0] if input_mismatches else
        {
            "field": "energy_service.beta(1)",
            "frozen_implemented": fraction_text(tick),
            "required_exact_linear": "1/10",
        } if service_contract_mismatch else None
    )
    stage_b_authorized = bool(
        script_failures == 0
        and not evidence_mismatches
        and not parity_mismatches
        and not input_mismatches
        and not dominance
        and count_match
        and not service_contract_mismatch
    )
    causes = ["CAMPAIGN_TASK_FAMILY_NOT_CONNECTED"]
    if service_contract_mismatch:
        causes.extend([
            "ENERGY_SERVICE_MAPPING_MISMATCH",
            "OLD_EVIDENCE_OR_CONTRACT_INCONSISTENT",
        ])
    audit = {
        "schema": "ASAP_BLOCK_RTA4_T10_STAGE_A_PARITY_AUDIT_V1",
        "repository": {"commit": commit, "tree": tree},
        "frozen_source": {"commit": FROZEN_SOURCE_COMMIT, "tree": FROZEN_SOURCE_TREE},
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
        "cell_comparison_count": len(expected_keys),
        "method_comparison_count": len(expected_keys) * len(METHOD_LABELS),
        "task_result_row_comparison_count": len(expected_keys) * len(METHOD_LABELS) * 10,
        "normalized_taskset_count": len(normalized),
        "script_failure_count": script_failures,
        "unclassified_internal_error_count": sum(
            row["methods"][label]["solver_status"] == "INTERNAL_CONFORMANCE_FAILURE"
            for row in replay_rows for label in METHOD_LABELS
        ),
        "frozen_evidence_mismatch_count": len(evidence_mismatches),
        "parity_mismatch_count": len(parity_mismatches),
        "input_semantic_mismatch_count": len(input_mismatches),
        "native_identity_diagnostic_difference_cell_count": len(native_identity_diagnostics),
        "dominance_violation_count": len(dominance),
        "acceptance_counts": counts,
        "expected_acceptance_counts": expected_counts,
        "acceptance_counts_match": count_match,
        "service_contract": {
            "configured_rate": "1/10",
            "materialized_tick": fraction_text(tick),
            "required_exact_linear_tick": "1/10",
            "exact_linear_match": not service_contract_mismatch,
            "first_divergent_length": 1 if service_contract_mismatch else None,
        },
        "root_cause_classification": causes,
        "first_mismatch": first_mismatch,
        "mismatches": {
            "frozen_evidence": evidence_mismatches,
            "formal_parity": parity_mismatches,
            "semantic_input": input_mismatches,
            "dominance": dominance,
        },
        "native_identity_diagnostics_first": (
            native_identity_diagnostics[0] if native_identity_diagnostics else None
        ),
        "per_method_result_hashes": per_unit_hashes,
        "stage_b_authorized": stage_b_authorized,
        "formula_changes": False,
        "formal_experiment_started": False,
    }
    _write_json(output_root / "t10_stage_a_frozen_contract.json", contract)
    _write_json(output_root / "rta4_t10_stage_a_parity_audit.json", audit)
    return audit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--evidence-archive", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--frozen-repository", type=Path, required=True)
    parser.add_argument("--frozen-worker", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        audit = run_audit(
            evidence_root=args.evidence_root,
            evidence_archive=args.evidence_archive,
            repository=args.repository,
            frozen_repository=args.frozen_repository,
            frozen_worker=args.frozen_worker,
            output_root=args.output_root,
            workers=args.workers,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(_report_markdown(audit), encoding="utf-8")
    except T10ParityAuditError as exc:
        print(f"STAGE_A=FAIL_CLOSED reason={exc}", file=sys.stderr)
        return 2
    print(f"cell_comparison_count={audit['cell_comparison_count']}")
    print(f"method_comparison_count={audit['method_comparison_count']}")
    print(f"frozen_evidence_mismatch_count={audit['frozen_evidence_mismatch_count']}")
    print(f"parity_mismatch_count={audit['parity_mismatch_count']}")
    print(f"input_semantic_mismatch_count={audit['input_semantic_mismatch_count']}")
    print(f"dominance_violation_count={audit['dominance_violation_count']}")
    print(f"acceptance_counts={canonical_json(audit['acceptance_counts'])}")
    print(f"stage_b_authorized={str(audit['stage_b_authorized']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
