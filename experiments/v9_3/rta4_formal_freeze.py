"""Post-pilot prepared configuration and immutable RTA4 freeze contracts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .rta4_formal_config import (
    RTA4_CORES, RTA4_FORMAL_PARAMETER_STATUS, RTA4_FORMAL_PLAN_VERSION,
    RTA4_FORMAL_PROFILE, RTA4_FORMAL_SCHEMA_VERSION, canonical_json,
    domain_hash, rta4_formal_config_hash, validate_rta4_formal_config,
)
from .rta4_formal_pilot import (
    validate_pilot_manifest, validate_pilot_report,
)
from .rta4_formal_schema import formal_schema_hash


RTA4_PREPARED_CONFIG_VERSION = "ASAP_BLOCK_V9_3_RTA4_PREPARED_CONFIG_V1"
RTA4_FREEZE_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_FORMAL_FREEZE_V1"
RTA4_PREPARED_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PREPARED_CONFIG:v1"
RTA4_FREEZE_MANIFEST_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_FREEZE:v1"
RTA4_FROZEN_PARAMETER_STATUS = "FROZEN_FOR_FORMAL_EXECUTION"

RTA4_FROZEN_SCHEMA_SHA256 = (
    "21bd8588835617c433b540515dc0c93d42ec6d1eda356688faf32621fa0ac55d"
)
RTA4_FROZEN_ALL_PLAN_DIGEST = (
    "11a092341ed8434223ad7505374a574264ba6fde65420d5b985f8cc289d41839"
)
RTA4_LEGACY_TABLE_DIGEST = (
    "9cc03a5cb1797aa0f3d4734a3ff00f07a6a3d7f0a37ab5503cda7c6cc56140b5"
)
RTA4_TOTAL_UNIQUE_RTA_REQUESTS = 124_400
RTA4_TOTAL_SIMULATIONS = 6_400
RTA4_CORE5B_MATHEMATICAL_REQUESTS = 3_000
RTA4_CORE5B_EXECUTIONS = 12_000

RTA4_TIMEOUT_METHODS = (
    "CW_D", "LOC_D", "PH_D", "SEQ_D",
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
RTA4_OPERATIONAL_FIELDS = frozenset({
    "worker_count", "max_in_flight", "memory_limit_bytes",
    "checkpoint_interval_records", "simulation_timeout_seconds",
    "output_root", "taskset_store", "source_closures",
    "simulator_binary", "execution_order", "resume_policy",
})


class RTA4FreezeError(ValueError):
    """Raised when pilot evidence or a formal freeze is not exact."""


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise RTA4FreezeError(f"{label} must be a positive integer")
    return value


def _absolute_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RTA4FreezeError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        raise RTA4FreezeError(f"{label} must be absolute")
    return str(path)


def validate_timeout_contract(contract: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(contract, Mapping) or set(contract) != {
        "contract_version", "pilot_report_id", "methods",
    }:
        raise RTA4FreezeError("timeout contract has an unexpected field set")
    if contract["contract_version"] != "ASAP_BLOCK_V9_3_RTA4_TIMEOUT_V1":
        raise RTA4FreezeError("timeout contract version mismatch")
    if (
        not isinstance(contract["pilot_report_id"], str)
        or len(contract["pilot_report_id"]) != 64
    ):
        raise RTA4FreezeError("timeout contract requires pilot evidence")
    methods = contract["methods"]
    if not isinstance(methods, Mapping) or tuple(methods) != RTA4_TIMEOUT_METHODS:
        raise RTA4FreezeError("timeout methods/order mismatch")
    for method, row in methods.items():
        if not isinstance(row, Mapping) or set(row) != {
            "initial_timeout_seconds", "retry_timeout_seconds",
            "maximum_attempts", "failure_origin", "pilot_evidence",
        }:
            raise RTA4FreezeError(f"timeout mapping is incomplete for {method}")
        initial = _positive_int(
            row["initial_timeout_seconds"], "initial timeout",
        )
        retry = _positive_int(row["retry_timeout_seconds"], "retry timeout")
        attempts = _positive_int(row["maximum_attempts"], "maximum attempts")
        if retry < initial or attempts > 2:
            raise RTA4FreezeError("timeout retry/attempt bounds are invalid")
        if row["failure_origin"] != "UNIFIED_RTA_ADAPTER":
            raise RTA4FreezeError("timeout failure origin is not fail-closed")
        if row["pilot_evidence"] != contract["pilot_report_id"]:
            raise RTA4FreezeError("timeout mapping carries stale pilot evidence")
    return deepcopy(dict(contract))


def timeout_contract_identity(contract: Mapping[str, Any]) -> str:
    normalized = validate_timeout_contract(contract)
    return domain_hash("ASAP_BLOCK:V9.3:RTA4_TIMEOUT_CONTRACT:v1", normalized)


def _validate_operational(core: str, value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != RTA4_OPERATIONAL_FIELDS:
        raise RTA4FreezeError("operational contract has an unexpected field set")
    worker_count = _positive_int(value["worker_count"], "worker_count")
    max_in_flight = _positive_int(value["max_in_flight"], "max_in_flight")
    if max_in_flight < worker_count:
        raise RTA4FreezeError("max_in_flight must cover worker_count")
    _positive_int(value["memory_limit_bytes"], "memory_limit_bytes")
    _positive_int(
        value["checkpoint_interval_records"], "checkpoint_interval_records",
    )
    _positive_int(
        value["simulation_timeout_seconds"], "simulation_timeout_seconds",
    )
    output = _absolute_path(value["output_root"], "output_root")
    store = _absolute_path(value["taskset_store"], "taskset_store")
    if output == store:
        raise RTA4FreezeError("output and taskset store paths must differ")
    sources = value["source_closures"]
    required_sources = {
        "CORE-2": ("CORE-1",), "CORE-3": ("CORE-1",),
        "CORE-5B": ("CORE-4",),
    }.get(core, ())
    if not isinstance(sources, Mapping) or tuple(sources) != required_sources:
        raise RTA4FreezeError("source closure DAG mismatch")
    for source, path in sources.items():
        _absolute_path(path, f"source_closures.{source}")
    simulator = value["simulator_binary"]
    if core == "CORE-3":
        _absolute_path(simulator, "simulator_binary")
    elif simulator is not None:
        raise RTA4FreezeError("only CORE-3 may bind a simulator binary")
    expected_order = {
        "CORE-1": 1, "CORE-4": 1, "CORE-5A": 1,
        "CORE-2": 2, "CORE-3": 2, "CORE-5B": 2,
    }[core]
    if value["execution_order"] != expected_order:
        raise RTA4FreezeError("execution order violates the frozen DAG")
    if value["resume_policy"] != "REVALIDATE_ALL_BINDINGS_SKIP_TERMINALS_V1":
        raise RTA4FreezeError("resume policy mismatch")
    return deepcopy(dict(value))


def _frozen_scientific_assertions() -> Dict[str, Any]:
    checks = {
        "profile": RTA4_FORMAL_PROFILE,
        "plan_version": RTA4_FORMAL_PLAN_VERSION,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
        "schema_sha256": formal_schema_hash(),
        "all_plan_digest": RTA4_FROZEN_ALL_PLAN_DIGEST,
        "legacy_table_digest": RTA4_LEGACY_TABLE_DIGEST,
        "total_unique_rta_requests": RTA4_TOTAL_UNIQUE_RTA_REQUESTS,
        "total_simulations": RTA4_TOTAL_SIMULATIONS,
        "core5b_mathematical_requests": RTA4_CORE5B_MATHEMATICAL_REQUESTS,
        "core5b_executions": RTA4_CORE5B_EXECUTIONS,
    }
    expected = {
        "profile": RTA4_FORMAL_PROFILE,
        "plan_version": RTA4_FORMAL_PLAN_VERSION,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
        "schema_sha256": RTA4_FROZEN_SCHEMA_SHA256,
        "all_plan_digest": RTA4_FROZEN_ALL_PLAN_DIGEST,
        "legacy_table_digest": RTA4_LEGACY_TABLE_DIGEST,
        "total_unique_rta_requests": RTA4_TOTAL_UNIQUE_RTA_REQUESTS,
        "total_simulations": RTA4_TOTAL_SIMULATIONS,
        "core5b_mathematical_requests": RTA4_CORE5B_MATHEMATICAL_REQUESTS,
        "core5b_executions": RTA4_CORE5B_EXECUTIONS,
    }
    if checks != expected:
        raise RTA4FreezeError("scientific plan/schema assertions drifted")
    return checks


def _scientific_assertions(configs: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    if set(configs) != set(RTA4_CORES):
        raise RTA4FreezeError("scientific freeze requires all six cores")
    for core in RTA4_CORES:
        validate_rta4_formal_config(configs[core], expected_core=core)
    return deepcopy(_frozen_scientific_assertions())


def prepare_formal_configs(
    configs: Mapping[str, Mapping[str, Any]], *,
    pilot_manifest: Mapping[str, Any],
    pilot_report: Mapping[str, Any],
    timeout_contract: Mapping[str, Any],
    operational: Mapping[str, Mapping[str, Any]],
    config_paths: Mapping[str, Path | str],
) -> Dict[str, Dict[str, Any]]:
    """Prepare all cores without altering the embedded scientific config."""

    if set(configs) != set(RTA4_CORES) or set(operational) != set(RTA4_CORES):
        raise RTA4FreezeError("freeze requires all six core contracts")
    if set(config_paths) != set(RTA4_CORES):
        raise RTA4FreezeError("freeze requires all six source config paths")
    validate_pilot_manifest(pilot_manifest, configs)
    validate_pilot_report(pilot_report, pilot_manifest)
    timeout = validate_timeout_contract(timeout_contract)
    if timeout["pilot_report_id"] != pilot_report["pilot_report_id"]:
        raise RTA4FreezeError("timeout contract was not derived from this pilot")
    scientific = _scientific_assertions(configs)
    prepared: Dict[str, Dict[str, Any]] = {}
    for core in RTA4_CORES:
        source = validate_rta4_formal_config(configs[core], expected_core=core)
        source_path = Path(config_paths[core]).resolve(strict=True)
        source_evidence = pilot_manifest["source_configs"][core]
        if source_evidence.get("absolute_path") != str(source_path):
            raise RTA4FreezeError("pilot/source config path mismatch")
        file_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if source_evidence.get("file_sha256") != file_sha:
            raise RTA4FreezeError("source config changed after pilot")
        if source_evidence["config_semantic_hash"] != rta4_formal_config_hash(
            source
        ):
            raise RTA4FreezeError("source config semantic identity changed")
        operations = _validate_operational(core, operational[core])
        material = {
            "prepared_config_version": RTA4_PREPARED_CONFIG_VERSION,
            "profile": RTA4_FORMAL_PROFILE,
            "parameter_status": RTA4_FROZEN_PARAMETER_STATUS,
            "core": core,
            "source_config": {
                "absolute_path": str(source_path),
                "file_sha256": file_sha,
                "config_semantic_hash": source_evidence[
                    "config_semantic_hash"
                ],
                "validated_pre_pilot_config": source,
                "pre_pilot_parameter_status": RTA4_FORMAL_PARAMETER_STATUS,
            },
            "pilot_manifest_id": pilot_manifest["pilot_manifest_id"],
            "pilot_report_id": pilot_report["pilot_report_id"],
            "timeout_contract": timeout,
            "timeout_contract_id": timeout_contract_identity(timeout),
            "operational": operations,
            "scientific_assertions": scientific,
        }
        prepared[core] = {
            **material,
            "prepared_config_id": domain_hash(
                RTA4_PREPARED_CONFIG_DOMAIN, material,
            ),
        }
    return prepared


def validate_prepared_config(document: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(document, Mapping):
        raise RTA4FreezeError("prepared config must be a mapping")
    exact = {
        "prepared_config_version", "profile", "parameter_status", "core",
        "source_config", "pilot_manifest_id", "pilot_report_id",
        "timeout_contract", "timeout_contract_id", "operational",
        "scientific_assertions", "prepared_config_id",
    }
    if set(document) != exact:
        raise RTA4FreezeError("prepared config field set mismatch")
    if (
        document["prepared_config_version"] != RTA4_PREPARED_CONFIG_VERSION
        or document["profile"] != RTA4_FORMAL_PROFILE
        or document["parameter_status"] != RTA4_FROZEN_PARAMETER_STATUS
        or document["core"] not in RTA4_CORES
    ):
        raise RTA4FreezeError("prepared config contract mismatch")
    source = document["source_config"]
    if not isinstance(source, Mapping) or set(source) != {
        "absolute_path", "file_sha256", "config_semantic_hash",
        "validated_pre_pilot_config", "pre_pilot_parameter_status",
    }:
        raise RTA4FreezeError("prepared source config evidence mismatch")
    normalized = validate_rta4_formal_config(
        source["validated_pre_pilot_config"],
        expected_core=document["core"],
    )
    if source["pre_pilot_parameter_status"] != RTA4_FORMAL_PARAMETER_STATUS:
        raise RTA4FreezeError("prepared source did not come from pre-pilot")
    if source["config_semantic_hash"] != rta4_formal_config_hash(normalized):
        raise RTA4FreezeError("prepared source semantic identity mismatch")
    timeout = validate_timeout_contract(document["timeout_contract"])
    if (
        document["timeout_contract_id"] != timeout_contract_identity(timeout)
        or timeout["pilot_report_id"] != document["pilot_report_id"]
    ):
        raise RTA4FreezeError("prepared timeout identity mismatch")
    _validate_operational(document["core"], document["operational"])
    material = dict(document)
    observed = material.pop("prepared_config_id")
    if observed != domain_hash(RTA4_PREPARED_CONFIG_DOMAIN, material):
        raise RTA4FreezeError("prepared config identity mismatch")
    return deepcopy(dict(document))


def build_freeze_manifest(
    prepared: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    if set(prepared) != set(RTA4_CORES):
        raise RTA4FreezeError("freeze manifest requires all prepared configs")
    normalized = {
        core: validate_prepared_config(prepared[core]) for core in RTA4_CORES
    }
    pilot_ids = {
        (row["pilot_manifest_id"], row["pilot_report_id"])
        for row in normalized.values()
    }
    scientific = {
        canonical_json(row["scientific_assertions"]) for row in normalized.values()
    }
    if len(pilot_ids) != 1 or len(scientific) != 1:
        raise RTA4FreezeError("prepared configs do not share one pilot/science freeze")
    material = {
        "freeze_manifest_version": RTA4_FREEZE_MANIFEST_VERSION,
        "profile": RTA4_FORMAL_PROFILE,
        "parameter_status": RTA4_FROZEN_PARAMETER_STATUS,
        "prepared_config_ids": {
            core: normalized[core]["prepared_config_id"] for core in RTA4_CORES
        },
        "pilot_manifest_id": normalized["CORE-1"]["pilot_manifest_id"],
        "pilot_report_id": normalized["CORE-1"]["pilot_report_id"],
        "scientific_assertions": normalized["CORE-1"]["scientific_assertions"],
        "execution_dag": {
            "CORE-1": [], "CORE-2": ["CORE-1"], "CORE-3": ["CORE-1"],
            "CORE-4": [], "CORE-5A": [], "CORE-5B": ["CORE-4"],
        },
    }
    return {
        **material,
        "freeze_manifest_id": domain_hash(
            RTA4_FREEZE_MANIFEST_DOMAIN, material,
        ),
    }


def validate_freeze_manifest(
    manifest: Mapping[str, Any],
    prepared: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    expected = build_freeze_manifest(prepared)
    if dict(manifest) != expected:
        raise RTA4FreezeError("formal freeze manifest mismatch")
    return expected


def prepared_scientific_config(document: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_prepared_config(document)
    return deepcopy(normalized["source_config"]["validated_pre_pilot_config"])


__all__ = [
    "RTA4_CORE5B_EXECUTIONS", "RTA4_CORE5B_MATHEMATICAL_REQUESTS",
    "RTA4_FREEZE_MANIFEST_DOMAIN", "RTA4_FREEZE_MANIFEST_VERSION",
    "RTA4_FROZEN_ALL_PLAN_DIGEST", "RTA4_FROZEN_PARAMETER_STATUS",
    "RTA4_FROZEN_SCHEMA_SHA256", "RTA4_LEGACY_TABLE_DIGEST",
    "RTA4_OPERATIONAL_FIELDS", "RTA4_PREPARED_CONFIG_DOMAIN",
    "RTA4_PREPARED_CONFIG_VERSION", "RTA4_TIMEOUT_METHODS",
    "RTA4_TOTAL_SIMULATIONS", "RTA4_TOTAL_UNIQUE_RTA_REQUESTS",
    "RTA4FreezeError", "build_freeze_manifest", "prepare_formal_configs",
    "prepared_scientific_config", "timeout_contract_identity",
    "validate_freeze_manifest", "validate_prepared_config",
    "validate_timeout_contract",
]
