"""Post-pilot prepared configuration and immutable RTA4 freeze contracts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping

from .rta4_formal_config import (
    RTA4_CORES, RTA4_FORMAL_PARAMETER_STATUS, RTA4_FORMAL_PLAN_VERSION,
    RTA4_FORMAL_PROFILE, RTA4_FORMAL_SCHEMA_VERSION, canonical_json,
    domain_hash, rta4_formal_config_hash, validate_rta4_formal_config,
)
from .rta4_formal_pilot import (
    validate_pilot_manifest, validate_pilot_observations,
    validate_pilot_report,
)
from .rta4_pilot_execution import (
    audit_pilot_namespace, validate_pilot_audit_document,
    validate_pilot_phase_inventory,
)
from .rta4_formal_environment import (
    RTA4_DEPENDENCY_DOMAIN, RTA4_DEPENDENCY_MANIFEST_VERSION,
    RTA4_ENVIRONMENT_DOMAIN, RTA4_ENVIRONMENT_MANIFEST_VERSION,
    RTA4_HARDWARE_DOMAIN, RTA4_HARDWARE_MANIFEST_VERSION,
    build_dependency_manifest, build_environment_manifest,
    build_hardware_manifest, validate_identity_manifest,
)
from .rta4_formal_schema import formal_schema_hash


RTA4_PREPARED_CONFIG_VERSION = "ASAP_BLOCK_V9_3_RTA4_PREPARED_CONFIG_V4"
RTA4_FREEZE_MANIFEST_VERSION = "ASAP_BLOCK_V9_3_RTA4_FORMAL_FREEZE_V4"
RTA4_PREPARED_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_PREPARED_CONFIG:v4"
RTA4_FREEZE_MANIFEST_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_FREEZE:v4"
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
RTA4_FROZEN_CORE_PLANS = {
    "CORE-1": {
        "count": 19_200,
        "ordered_digest": "5e33d6cdb14a67cac08299b3a2bc988b3e1628132293954b2d8acdb532fc1c8b",
        "plan_sha256": "5451288b09c118eae9c8e7e0bbe875ebf15c5e7a3991c1b78fb683075da3812b",
    },
    "CORE-2": {
        "count": 28_800,
        "ordered_digest": "7f11a0ad3a412495e14b29f09f38d952fe3af346aa0374049d56691532f55f12",
        "plan_sha256": "1c4e9b675095c845cdf68dbaf9523242bc2e29b2a702951556c082e6ea5a86d5",
    },
    "CORE-3": {
        "count": 6_400,
        "ordered_digest": "41148a135d8ca99dd6f545afbf39f9b8bee4e26749764f7b72a2b3ae8e45f153",
        "plan_sha256": "8619e4f2c7602f45e2d312a379c1d0d3c6af3f81864da50ec3ef14a272c9809e",
    },
    "CORE-4": {
        "count": 72_000,
        "ordered_digest": "098db8c6680153549cd2b35ff105a196d1093fc8d6139e6190bc751a8da8f4e6",
        "plan_sha256": "f18d2eb044852d1806926ff28ac0dbe652b2b6ad87dddab14cb46f0ea0938c66",
    },
    "CORE-5A": {
        "count": 4_400,
        "ordered_digest": "966429bcbf41f1f15b1f849a02cc9c9c4c9dd3353655868db3c83e5cce7fb521",
        "plan_sha256": "54614447fc2a05b39f7577304a079f96d18dc166984de7a2f5ea2a4aadd748b7",
    },
    "CORE-5B": {
        "count": 12_000,
        "ordered_digest": "85ed2de830f2f239d91d760067cebe25caf7b564518d9eed95ce9a3fee3408cf",
        "plan_sha256": "243fc6bc411d16e9f3e052e204e7da035bd5516ec43a72637ffa36e22a5df18f",
    },
}

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
    return str(path.resolve())


def validate_timeout_contract(contract: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(contract, Mapping) or set(contract) != {
        "contract_version", "pilot_report_id", "method_order", "methods",
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
    if (
        contract["method_order"] != list(RTA4_TIMEOUT_METHODS)
        or not isinstance(methods, Mapping)
        or set(methods) != set(RTA4_TIMEOUT_METHODS)
    ):
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
    normalized = deepcopy(dict(value))
    normalized["output_root"] = output
    normalized["taskset_store"] = store
    normalized["source_closures"] = {
        source: _absolute_path(path, f"source_closures.{source}")
        for source, path in sources.items()
    }
    normalized["simulator_binary"] = (
        None
        if simulator is None
        else _absolute_path(simulator, "simulator_binary")
    )
    return normalized


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
        "core_plans": deepcopy(RTA4_FROZEN_CORE_PLANS),
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
        "core_plans": deepcopy(RTA4_FROZEN_CORE_PLANS),
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
    pilot_observations: Mapping[str, Any],
    pilot_report: Mapping[str, Any],
    pilot_audit: Mapping[str, Any],
    timeout_contract: Mapping[str, Any],
    operational: Mapping[str, Mapping[str, Any]],
    config_paths: Mapping[str, Path | str],
    pilot_root: Path | str,
) -> Dict[str, Dict[str, Any]]:
    """Prepare all cores without altering the embedded scientific config."""

    if set(configs) != set(RTA4_CORES) or set(operational) != set(RTA4_CORES):
        raise RTA4FreezeError("freeze requires all six core contracts")
    if set(config_paths) != set(RTA4_CORES):
        raise RTA4FreezeError("freeze requires all six source config paths")
    validate_pilot_manifest(pilot_manifest, configs)
    observations = validate_pilot_observations(
        pilot_observations, pilot_manifest,
    )
    validate_pilot_report(pilot_report, pilot_manifest, observations)
    try:
        audit = audit_pilot_namespace(
            pilot_root, configs, require_complete=True,
        )
    except Exception as exc:
        raise RTA4FreezeError(
            "freeze requires a fresh file-backed pilot audit"
        ) from exc
    if validate_pilot_audit_document(pilot_audit) != audit:
        raise RTA4FreezeError(
            "persisted/supplied pilot audit differs from fresh reconstruction"
        )
    audited_order = [
        f"freeze-audited-execution-{index:08d}"
        for index in range(audit["raw_terminal_count"])
    ]
    validate_pilot_phase_inventory(
        phase="PILOT_COMPLETE",
        expected_store_slot_order=("freeze-audited-store",),
        completed_store_slot_order=("freeze-audited-store",),
        store_manifest_present=True,
        selected_execution_order=audited_order,
        raw_execution_order=audited_order,
        final_execution_order=audited_order,
        trace_execution_ids=(),
        required_trace_execution_ids=(),
        observations_present=True,
        report_present=True,
        audit_present=True,
        completion_seal_present=True,
    )
    pilot_path = Path(pilot_root).resolve(strict=True)
    try:
        from .rta4_formal_environment import load_strict_json
        from .rta4_formal_pilot import (
            RTA4_PILOT_OBSERVATIONS, RTA4_PILOT_OUTPUT_MARKER,
            RTA4_PILOT_REPORT,
        )
        file_documents = (
            load_strict_json(pilot_path / RTA4_PILOT_OUTPUT_MARKER),
            load_strict_json(pilot_path / RTA4_PILOT_OBSERVATIONS),
            load_strict_json(pilot_path / RTA4_PILOT_REPORT),
        )
    except Exception as exc:
        raise RTA4FreezeError(
            "pilot root lacks canonical final evidence"
        ) from exc
    if file_documents != (
        dict(pilot_manifest), dict(pilot_observations), dict(pilot_report),
    ):
        raise RTA4FreezeError(
            "freeze inputs differ from canonical pilot-root evidence"
        )
    if (
        audit["freeze_eligible"] is not True
        or audit["execution_class"] != "ENGINEERING_PILOT"
        or audit["checkpoint_state"] != "PILOT_COMPLETE"
        or audit["pilot_manifest_id"] != pilot_manifest["pilot_manifest_id"]
        or audit["terminal_count"] != observations["observation_count"]
        or audit["pilot_observations_id"]
        != observations["pilot_observations_id"]
        or audit["pilot_report_id"] != pilot_report["pilot_report_id"]
        or audit["pilot_closure_id"] != pilot_report["pilot_closure_id"]
    ):
        raise RTA4FreezeError(
            "freeze requires one independently audited real engineering pilot"
        )
    timeout = validate_timeout_contract(timeout_contract)
    if timeout["pilot_report_id"] != pilot_report["pilot_report_id"]:
        raise RTA4FreezeError("timeout contract was not derived from this pilot")
    scientific = _scientific_assertions(configs)
    dependency_manifest = build_dependency_manifest()
    environment_manifest = build_environment_manifest(dependency_manifest)
    hardware_manifest = build_hardware_manifest()
    runtime_environment = {
        "dependency_manifest": dependency_manifest,
        "environment_manifest": environment_manifest,
        "hardware_manifest": hardware_manifest,
    }
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
            "pilot_observations_id": observations["pilot_observations_id"],
            "pilot_report_id": pilot_report["pilot_report_id"],
            "pilot_closure_id": pilot_report["pilot_closure_id"],
            "pilot_audit_id": audit["audit_id"],
            "pilot_execution_config_id": audit["execution_config_id"],
            "pilot_execution_manifest_id": audit["execution_manifest_id"],
            "pilot_store_manifest_id": audit["store_manifest_id"],
            "pilot_checkpoint_id": audit["checkpoint_id"],
            "timeout_contract": timeout,
            "timeout_contract_id": timeout_contract_identity(timeout),
            "operational": operations,
            "runtime_environment": runtime_environment,
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
        "pilot_observations_id", "pilot_closure_id", "timeout_contract",
        "pilot_audit_id", "pilot_execution_config_id",
        "pilot_execution_manifest_id", "pilot_checkpoint_id",
        "pilot_store_manifest_id",
        "timeout_contract_id", "operational",
        "runtime_environment", "scientific_assertions", "prepared_config_id",
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
    try:
        source_path = Path(source["absolute_path"]).resolve(strict=True)
    except OSError as exc:
        raise RTA4FreezeError("prepared source config path is stale") from exc
    if (
        not source_path.is_file()
        or str(source_path) != source["absolute_path"]
        or hashlib.sha256(source_path.read_bytes()).hexdigest()
        != source["file_sha256"]
    ):
        raise RTA4FreezeError("prepared source config byte binding drift")
    normalized = validate_rta4_formal_config(
        source["validated_pre_pilot_config"],
        expected_core=document["core"],
    )
    if source["pre_pilot_parameter_status"] != RTA4_FORMAL_PARAMETER_STATUS:
        raise RTA4FreezeError("prepared source did not come from pre-pilot")
    if source["config_semantic_hash"] != rta4_formal_config_hash(normalized):
        raise RTA4FreezeError("prepared source semantic identity mismatch")
    if (
        any(
            not isinstance(document[field], str)
            or len(document[field]) != 64
            for field in (
                "pilot_manifest_id", "pilot_observations_id",
                "pilot_report_id", "pilot_closure_id", "pilot_audit_id",
                "pilot_execution_config_id",
                "pilot_execution_manifest_id", "pilot_checkpoint_id",
                "pilot_store_manifest_id",
            )
        )
    ):
        raise RTA4FreezeError("prepared config lacks pilot evidence identity")
    timeout = validate_timeout_contract(document["timeout_contract"])
    if (
        document["timeout_contract_id"] != timeout_contract_identity(timeout)
        or timeout["pilot_report_id"] != document["pilot_report_id"]
    ):
        raise RTA4FreezeError("prepared timeout identity mismatch")
    if document["scientific_assertions"] != _frozen_scientific_assertions():
        raise RTA4FreezeError("prepared scientific assertions drifted")
    _validate_operational(document["core"], document["operational"])
    runtime = document["runtime_environment"]
    if not isinstance(runtime, Mapping) or set(runtime) != {
        "dependency_manifest", "environment_manifest", "hardware_manifest",
    }:
        raise RTA4FreezeError("prepared runtime environment field mismatch")
    dependency = validate_identity_manifest(
        runtime["dependency_manifest"],
        version=RTA4_DEPENDENCY_MANIFEST_VERSION,
        domain=RTA4_DEPENDENCY_DOMAIN,
    )
    environment = validate_identity_manifest(
        runtime["environment_manifest"],
        version=RTA4_ENVIRONMENT_MANIFEST_VERSION,
        domain=RTA4_ENVIRONMENT_DOMAIN,
    )
    validate_identity_manifest(
        runtime["hardware_manifest"],
        version=RTA4_HARDWARE_MANIFEST_VERSION,
        domain=RTA4_HARDWARE_DOMAIN,
    )
    if environment.get("dependency_manifest_id") != dependency.get("manifest_id"):
        raise RTA4FreezeError("prepared runtime dependency binding mismatch")
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
        (
            row["pilot_manifest_id"], row["pilot_observations_id"],
            row["pilot_closure_id"], row["pilot_report_id"],
            row["pilot_audit_id"], row["pilot_execution_config_id"],
            row["pilot_execution_manifest_id"], row["pilot_checkpoint_id"],
            row["pilot_store_manifest_id"],
        )
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
        "pilot_observations_id": normalized["CORE-1"][
            "pilot_observations_id"
        ],
        "pilot_report_id": normalized["CORE-1"]["pilot_report_id"],
        "pilot_closure_id": normalized["CORE-1"]["pilot_closure_id"],
        "pilot_audit_id": normalized["CORE-1"]["pilot_audit_id"],
        "pilot_execution_config_id": normalized["CORE-1"][
            "pilot_execution_config_id"
        ],
        "pilot_execution_manifest_id": normalized["CORE-1"][
            "pilot_execution_manifest_id"
        ],
        "pilot_store_manifest_id": normalized["CORE-1"][
            "pilot_store_manifest_id"
        ],
        "pilot_checkpoint_id": normalized["CORE-1"][
            "pilot_checkpoint_id"
        ],
        "scientific_assertions": normalized["CORE-1"]["scientific_assertions"],
        "execution_dag": {
            "CORE-1": [], "CORE-2": ["CORE-1"], "CORE-3": ["CORE-1"],
            "CORE-4": [], "CORE-5A": [], "CORE-5B": ["CORE-4"],
        },
        "runtime_manifest_ids": {
            core: {
                name: normalized[core]["runtime_environment"][name][
                    "manifest_id"
                ]
                for name in (
                    "dependency_manifest", "environment_manifest",
                    "hardware_manifest",
                )
            }
            for core in RTA4_CORES
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
    "RTA4_FROZEN_CORE_PLANS", "RTA4_FROZEN_SCHEMA_SHA256",
    "RTA4_LEGACY_TABLE_DIGEST",
    "RTA4_OPERATIONAL_FIELDS", "RTA4_PREPARED_CONFIG_DOMAIN",
    "RTA4_PREPARED_CONFIG_VERSION", "RTA4_TIMEOUT_METHODS",
    "RTA4_TOTAL_SIMULATIONS", "RTA4_TOTAL_UNIQUE_RTA_REQUESTS",
    "RTA4FreezeError", "build_freeze_manifest", "prepare_formal_configs",
    "prepared_scientific_config", "timeout_contract_identity",
    "validate_freeze_manifest", "validate_prepared_config",
    "validate_timeout_contract",
]
