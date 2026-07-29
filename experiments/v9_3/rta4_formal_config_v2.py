"""Additive formal configuration contract for RTA4 shared-energy V2."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

from . import exact_energy
from .rta4_formal_config import (
    RTA4_CORES,
    canonical_json,
    default_rta4_formal_config,
    domain_hash,
)
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_production_build_manifest import PRODUCTION_BUILD_MANIFEST_SCHEMA
from .rta4_shared_energy import (
    BETA_CONTRACT_VERSION,
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_SCHEMA,
    TASK_ENERGY_MATERIAL_SCHEMA,
)


RTA4_FORMAL_PROFILE_V2 = "ASAP_BLOCK_V9_3_RTA4_FORMAL_V2_SHARED_ENERGY"
RTA4_FORMAL_PLAN_VERSION_V2 = "ASAP_BLOCK_V9_3_RTA4_FORMAL_PLAN_V2_SHARED_ENERGY"
RTA4_FORMAL_SCHEMA_VERSION_V2 = "ASAP_BLOCK_V9_3_RTA4_FORMAL_SCHEMA_V2_SHARED_ENERGY"
RTA4_FORMAL_STORE_VERSION_V2 = "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_V2_SHARED_ENERGY"
RTA4_FORMAL_TEMPLATE_VERSION_V2 = "ASAP_BLOCK_V9_3_RTA4_PRE_PILOT_TEMPLATE_V2_SHARED_ENERGY"
RTA4_FORMAL_PARAMETER_STATUS_V2 = "UNAUTHORIZED_PRE_PILOT"
RTA4_FORMAL_CONFIG_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v2"
RTA4_FORMAL_AUTHORIZATION_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_AUTHORIZATION:v2"
RTA4_FORMAL_TASKSET_STORE_DOMAIN_V2 = "ASAP_BLOCK:V9.3:RTA4_TASKSET_STORE:v2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RTA4FormalConfigV2Error(ValueError):
    """Raised when a V2 configuration is incomplete or mixed with V1."""


@lru_cache(maxsize=1)
def formal_taskset_store_identity_v2() -> str:
    return domain_hash(RTA4_FORMAL_TASKSET_STORE_DOMAIN_V2, {
        "store_version": RTA4_FORMAL_STORE_VERSION_V2,
        "task_energy_material_schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "workload_vector_required": True,
        "legacy_v1_store_accepted": False,
    })


def default_rta4_formal_config_v2(core: str) -> Dict[str, Any]:
    if core not in RTA4_CORES:
        raise RTA4FormalConfigV2Error(f"unknown RTA4 V2 core: {core!r}")
    base = default_rta4_formal_config(core)
    slug = core.lower().replace("-", "")
    base["experiment_id"] = (
        f"asap-block-v9.3-rta4-{slug}-unauthorized-pre-pilot-v2-shared-energy"
    )
    base["experiment_contract"] = {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V2,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V2,
        "parameter_status": RTA4_FORMAL_PARAMETER_STATUS_V2,
    }
    from .rta4_formal_schema_v2 import formal_schema_hash_v2

    base["identity"] = {
        **base["identity"],
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "formal_schema_sha256": formal_schema_hash_v2(),
        "taskset_store_version": RTA4_FORMAL_STORE_VERSION_V2,
        "taskset_store_identity": formal_taskset_store_identity_v2(),
        "production_build_manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
        "task_energy_material_schema": TASK_ENERGY_MATERIAL_SCHEMA,
        "service_material_schema": SERVICE_MATERIAL_SCHEMA,
        "horizon_contract_version": HORIZON_CONTRACT_VERSION,
        "beta_contract_version": BETA_CONTRACT_VERSION,
        "authorization_domain": RTA4_FORMAL_AUTHORIZATION_DOMAIN_V2,
    }
    base["shared_energy"] = {
        "task_energy_source": "CANONICAL_SYSTEM_WORKLOAD_J_PER_TICK",
        "legacy_actual_power_forbidden": True,
        "service_source": "VERIFIED_REAL_SOLAR_ARBITRARY_WINDOW",
        "service_scale_semantics": "REBUILD_CANONICAL_SOLAR_SUPPORT_INPUT",
        "linear_beta_forbidden": True,
        "system_config": "system_config_unified_template.yml",
        "workload_config": "system_config_unified_template.yml",
        "energy_support": "configs/v9_3_rta4_shared_energy_support_v2.yaml",
        "system_config_sha256": hashlib.sha256(
            (PROJECT_ROOT / "system_config_unified_template.yml").read_bytes()
        ).hexdigest(),
        "workload_config_sha256": hashlib.sha256(
            (PROJECT_ROOT / "system_config_unified_template.yml").read_bytes()
        ).hexdigest(),
        "energy_support_sha256": hashlib.sha256(
            (PROJECT_ROOT / "configs/v9_3_rta4_shared_energy_support_v2.yaml").read_bytes()
        ).hexdigest(),
        "analysis_service_horizon": "maximum_RTA_beta_query_from_task_deadlines",
        "simulation_observation_horizon": "release_horizon_plus_D_max",
        "service_material_horizon": "maximum_of_all_bound_consumers",
        "cache_scope": "RUN_INITIALIZATION_BEFORE_WORKER_POOL",
    }
    base["execution"]["output_root"] = f"results/v9_3_rta4_{slug}_formal_v2_shared_energy"
    base["execution"]["taskset_store"] = "results/v9_3_rta4_formal_tasksets_v2_shared_energy"
    return base


def validate_rta4_formal_config_v2(
    raw: Mapping[str, Any], *, expected_core: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RTA4FormalConfigV2Error("V2 formal configuration must be a mapping")
    core = raw.get("core")
    if core not in RTA4_CORES or (expected_core is not None and core != expected_core):
        raise RTA4FormalConfigV2Error("V2 formal core mismatch")
    expected = default_rta4_formal_config_v2(str(core))
    if canonical_json(raw) != canonical_json(expected):
        raise RTA4FormalConfigV2Error("configuration does not match frozen V2 contract")
    if raw["experiment_contract"]["profile"] == "ASAP_BLOCK_V9_3_RTA4_FORMAL_V1":
        raise RTA4FormalConfigV2Error("V1 profile is forbidden in V2 loader")
    return deepcopy(dict(raw))


def load_rta4_formal_config_v2(
    path: Path | str, *, expected_core: str | None = None,
) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RTA4FormalConfigV2Error(f"cannot load V2 config: {path}") from exc
    if isinstance(raw, Mapping) and raw.get("template") == RTA4_FORMAL_TEMPLATE_VERSION_V2:
        if set(raw) != {"template", "core", "experiment_contract", "execution"}:
            raise RTA4FormalConfigV2Error("V2 compact config field set mismatch")
        expanded = default_rta4_formal_config_v2(str(raw.get("core")))
        if raw.get("experiment_contract") != expanded["experiment_contract"]:
            raise RTA4FormalConfigV2Error("V2 compact profile binding mismatch")
        execution = raw.get("execution")
        if not isinstance(execution, Mapping) or set(execution) != {
            "output_root", "taskset_store",
        }:
            raise RTA4FormalConfigV2Error("V2 compact execution binding mismatch")
        expanded["execution"].update(execution)
        raw = expanded
    return validate_rta4_formal_config_v2(raw, expected_core=expected_core)


def rta4_formal_config_hash_v2(config: Mapping[str, Any]) -> str:
    normalized = validate_rta4_formal_config_v2(config)
    semantic = deepcopy(normalized)
    semantic["execution"].pop("output_root")
    semantic["execution"].pop("taskset_store")
    semantic["execution"].pop("resume")
    return domain_hash(RTA4_FORMAL_CONFIG_DOMAIN_V2, semantic)


__all__ = [
    "RTA4_FORMAL_AUTHORIZATION_DOMAIN_V2", "RTA4_FORMAL_CONFIG_DOMAIN_V2",
    "RTA4_FORMAL_PARAMETER_STATUS_V2", "RTA4_FORMAL_PLAN_VERSION_V2",
    "RTA4_FORMAL_PROFILE_V2", "RTA4_FORMAL_SCHEMA_VERSION_V2",
    "RTA4_FORMAL_STORE_VERSION_V2", "RTA4_FORMAL_TEMPLATE_VERSION_V2",
    "RTA4FormalConfigV2Error", "default_rta4_formal_config_v2",
    "formal_taskset_store_identity_v2", "load_rta4_formal_config_v2",
    "rta4_formal_config_hash_v2", "validate_rta4_formal_config_v2",
]
