"""Canonical, domain-separated identities for v9.3 B4."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import domain_hash


TASKSET_STORE_DOMAIN = "ASAP_BLOCK:V9.3:B4:TASKSET_STORE:v1"
ENERGY_DOMAIN = "ASAP_BLOCK:V9.3:B4:ENERGY:v1"
SEMANTIC_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:B4:SIMULATION_SEMANTIC_CONFIG:v1"
SEMANTIC_REQUEST_DOMAIN = "ASAP_BLOCK:V9.3:B4:SEMANTIC_REQUEST:v1"
EXECUTION_DOMAIN = "ASAP_BLOCK:V9.3:B4:EXECUTION:v1"
CALIBRATION_DOMAIN = "ASAP_BLOCK:V9.3:B4:CALIBRATION_SELECTION:v1"
HORIZON_DOMAIN = "ASAP_BLOCK:V9.3:B4:HORIZON_SELECTION:v1"
FORMAL_PLAN_DOMAIN = "ASAP_BLOCK:V9.3:B4:FORMAL_PLAN:v1"
TRACE_SAMPLE_DOMAIN = "ASAP_BLOCK:V9.3:B4:TRACE_SAMPLE:v1"
REQUEST_CONTRACT_VERSION = "ASAP_BLOCK_V9_3_B4_REQUEST_V1"


def taskset_store_identity(material: Mapping[str, Any]) -> str:
    return domain_hash(TASKSET_STORE_DOMAIN, material)


def energy_identity(material: Mapping[str, Any]) -> str:
    forbidden = {"runtime_horizon_ms", "experiment_id", "output_path", "worker_count", "checkpoint_every"}
    overlap = forbidden.intersection(material)
    if overlap:
        raise ValueError(f"energy identity contains runtime-only fields: {sorted(overlap)}")
    return domain_hash(ENERGY_DOMAIN, material)


def semantic_config_hash(material: Mapping[str, Any]) -> str:
    forbidden = {
        "experiment_id", "output_path", "output_root", "worker_count",
        "checkpoint_interval", "checkpoint_every", "resume", "log_path",
        "timeout_seconds", "retry_timeout_seconds",
    }
    overlap = forbidden.intersection(material)
    if overlap:
        raise ValueError(f"semantic config contains execution-only fields: {sorted(overlap)}")
    return domain_hash(SEMANTIC_CONFIG_DOMAIN, material)


def semantic_request_id(
    *, contract_version: str, taskset_semantic_hash: str,
    energy_identity_value: str, scheduler_id: str, runtime_horizon_ms: int,
    simulation_semantic_config_hash: str,
) -> str:
    return domain_hash(SEMANTIC_REQUEST_DOMAIN, {
        "contract_version": contract_version,
        "taskset_semantic_hash": taskset_semantic_hash,
        "energy_identity": energy_identity_value,
        "scheduler_id": scheduler_id,
        "runtime_horizon_ms": int(runtime_horizon_ms),
        "simulation_semantic_config_hash": simulation_semantic_config_hash,
    })


def execution_identity(semantic_request_id_value: str, source_commit: str, simulator_binary_sha256: str) -> str:
    return domain_hash(EXECUTION_DOMAIN, {
        "semantic_request_id": semantic_request_id_value,
        "exact_source_commit": source_commit,
        "simulator_binary_sha256": simulator_binary_sha256,
    })


def calibration_selection_identity(material: Mapping[str, Any]) -> str:
    return domain_hash(CALIBRATION_DOMAIN, material)


def horizon_selection_identity(material: Mapping[str, Any]) -> str:
    return domain_hash(HORIZON_DOMAIN, material)


def formal_plan_identity(request_ids: Sequence[str], material: Mapping[str, Any]) -> str:
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("formal plan contains duplicate semantic request IDs")
    return domain_hash(FORMAL_PLAN_DOMAIN, {
        "material": material, "semantic_request_ids": list(request_ids),
    })


def trace_sample_selected(semantic_request_id_value: str, numerator: int = 1, denominator: int = 20) -> bool:
    if numerator <= 0 or denominator <= 0 or numerator > denominator:
        raise ValueError("invalid trace sample fraction")
    digest = domain_hash(TRACE_SAMPLE_DOMAIN, {"semantic_request_id": semantic_request_id_value})
    return int(digest, 16) % denominator < numerator


def assert_unique_request_ids(requests: Iterable[Mapping[str, Any]]) -> None:
    observed = set()
    for request in requests:
        value = str(request["semantic_request_id"])
        if value in observed:
            raise ValueError(f"duplicate semantic request ID: {value}")
        observed.add(value)


def audit_gate_formal_relationship(
    selected_gate_ids: Iterable[str], unselected_gate_ids: Iterable[str], formal_ids: Iterable[str],
) -> Dict[str, bool]:
    selected = set(selected_gate_ids)
    unselected = set(unselected_gate_ids)
    formal = set(formal_ids)
    result = {
        "selected_gate_strict_subset": bool(selected) and selected < formal,
        "unselected_horizon_disjoint": unselected.isdisjoint(formal),
        "gate_horizons_disjoint": selected.isdisjoint(unselected),
    }
    if not all(result.values()):
        raise ValueError(f"gate/formal request identity relationship failed: {result}")
    return result
