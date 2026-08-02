"""Fail-closed scientific campaign configuration for RTA4 formal V4."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from . import exact_energy
from .rta4_energy_service_v4 import (
    EnergyServiceV4,
    normalize_energy_service_v4,
)
from .rta4_formal_config import domain_hash, fraction_text
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_task_source_v4 import (
    PRIORITY_POLICY_RM,
    RTA4TaskSourceV4Error,
    TaskSourceV4,
    _UniqueKeyLoader,
    load_task_source_v4,
)


RTA4_FORMAL_PROFILE_V4 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_V4_EXACT_VERSIONED_TASK_SOURCE"
)
RTA4_FORMAL_SCHEMA_VERSION_V4 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_SCHEMA_V4_EXACT_TASK_SOURCE"
)
RTA4_FORMAL_PLAN_VERSION_V4 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_PLAN_V4_EXACT_TASK_SOURCE"
)
RTA4_FORMAL_CONFIG_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v4"
RTA4_FORMAL_TASKSET_STORE_DOMAIN_V4 = (
    "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE:v4"
)
RTA4_FORMAL_TASKSET_STORE_HEADER_DOMAIN_V4 = (
    "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE_HEADER:v4"
)
RTA4_SOURCE_CLOSURE_DOMAIN_V4 = "ASAP_BLOCK:V9.3:RTA4:SOURCE_CLOSURE:v4"
RTA4_RECURSIVE_METHODS_V4 = (
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
RTA4_FORMAL_CAMPAIGN_AUTHORIZATION_STATUS_V4 = (
    "UNAUTHORIZED_REQUIRES_SEPARATE_PARAMETER_FREEZE"
)

_CAMPAIGN_FIELDS = {
    "campaign_id", "core", "processors", "priority_policy", "task_source",
    "energy_service", "e0", "methods", "runtime",
}
_RUNTIME_FIELDS = {
    "output_root", "taskset_store", "log_path", "resume", "worker_count",
    "max_in_flight", "timeout_seconds", "max_records",
    "checkpoint_every_records", "checkpoint_every_seconds",
}


class RTA4FormalConfigV4Error(ValueError):
    """Raised before a V4 scientific or execution identity can be issued."""


@dataclass(frozen=True)
class LoadedCampaignV4:
    campaign_path: Path
    raw_campaign_file_sha256: str
    normalized_scientific_config: Mapping[str, Any]
    normalized_scientific_config_sha256: str
    runtime: Mapping[str, Any]
    task_source: TaskSourceV4
    energy_service: EnergyServiceV4


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise RTA4FormalConfigV4Error(f"{label} must be a positive plain integer")
    return value


def _exact_axis(value: Any, label: str) -> list[str]:
    if type(value) is not list or not value:
        raise RTA4FormalConfigV4Error(f"{label} must be a non-empty list")
    result = []
    for index, item in enumerate(value):
        if type(item) is not str or not item:
            raise RTA4FormalConfigV4Error(
                f"{label}[{index}] must be an exact rational string"
            )
        try:
            exact = Fraction(item)
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4FormalConfigV4Error(
                f"{label}[{index}] is not rational"
            ) from exc
        if exact < 0:
            raise RTA4FormalConfigV4Error(f"{label}[{index}] is negative")
        canonical = fraction_text(exact)
        if item != canonical:
            raise RTA4FormalConfigV4Error(
                f"{label}[{index}] must be canonical: {canonical}"
            )
        result.append(canonical)
    if len(set(result)) != len(result):
        raise RTA4FormalConfigV4Error(f"{label} contains duplicates")
    return result


def _methods(value: Any) -> list[str]:
    if (
        type(value) is not list or not value
        or any(type(item) is not str for item in value)
        or len(set(value)) != len(value)
    ):
        raise RTA4FormalConfigV4Error("methods must be a unique non-empty list")
    unknown = set(value).difference(RTA4_RECURSIVE_METHODS_V4)
    if unknown:
        raise RTA4FormalConfigV4Error(
            f"methods contain unknown identifiers: {sorted(unknown)}"
        )
    selected = set(value)
    return [method for method in RTA4_RECURSIVE_METHODS_V4 if method in selected]


def _runtime(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value).difference(_RUNTIME_FIELDS):
        raise RTA4FormalConfigV4Error("runtime contains unknown fields")
    normalized: dict[str, Any] = {}
    for field in ("output_root", "taskset_store", "log_path"):
        if field in value:
            if type(value[field]) is not str or not value[field]:
                raise RTA4FormalConfigV4Error(
                    f"runtime.{field} must be a non-empty path"
                )
            normalized[field] = value[field]
    if "resume" in value:
        if type(value["resume"]) is not bool:
            raise RTA4FormalConfigV4Error("runtime.resume must be boolean")
        normalized["resume"] = value["resume"]
    for field in (
        "worker_count", "max_in_flight", "timeout_seconds",
        "checkpoint_every_records", "checkpoint_every_seconds",
    ):
        if field in value:
            normalized[field] = _positive_int(value[field], f"runtime.{field}")
    if "max_records" in value:
        if type(value["max_records"]) is not int or value["max_records"] < 0:
            raise RTA4FormalConfigV4Error(
                "runtime.max_records must be a nonnegative plain integer"
            )
        normalized["max_records"] = value["max_records"]
    if (
        "worker_count" in normalized and "max_in_flight" in normalized
        and normalized["max_in_flight"] < normalized["worker_count"]
    ):
        raise RTA4FormalConfigV4Error(
            "runtime.max_in_flight must cover worker_count"
        )
    return normalized


def normalize_rta4_campaign_v4(
    raw: Any, *, base_directory: Path | str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RTA4FormalConfigV4Error("campaign must be a mapping")
    actual = set(raw)
    if actual != _CAMPAIGN_FIELDS:
        raise RTA4FormalConfigV4Error(
            f"campaign field set mismatch; missing={sorted(_CAMPAIGN_FIELDS - actual)}, "
            f"unknown={sorted(actual - _CAMPAIGN_FIELDS)}"
        )
    campaign_id = raw["campaign_id"]
    if (
        type(campaign_id) is not str
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", campaign_id)
    ):
        raise RTA4FormalConfigV4Error("campaign_id is invalid")
    if raw["core"] != "CORE-1":
        raise RTA4FormalConfigV4Error("formal V4 currently supports CORE-1 only")
    processors = _positive_int(raw["processors"], "processors")
    if raw["priority_policy"] != PRIORITY_POLICY_RM:
        raise RTA4FormalConfigV4Error("priority_policy is unsupported")
    try:
        task_source = load_task_source_v4(
            raw["task_source"], base_directory=base_directory,
        )
        energy_service = normalize_energy_service_v4(raw["energy_service"])
    except (RTA4TaskSourceV4Error, ValueError) as exc:
        raise RTA4FormalConfigV4Error(str(exc)) from exc
    if task_source.processors != processors:
        raise RTA4FormalConfigV4Error(
            "campaign processors differ from task source"
        )
    if task_source.priority_policy != raw["priority_policy"]:
        raise RTA4FormalConfigV4Error(
            "campaign priority policy differs from task source"
        )
    scientific = {
        "profile": RTA4_FORMAL_PROFILE_V4,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V4,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V4,
        "campaign_id": campaign_id,
        "core": "CORE-1",
        "processors": processors,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": task_source.task_count,
        "taskset_count": task_source.taskset_count,
        "task_source": deepcopy(dict(task_source.normalized_config)),
        "task_source_identity": task_source.identity,
        "task_source_content_certificate": deepcopy(
            dict(task_source.content_certificate)
        ),
        "energy_service": deepcopy(dict(energy_service.normalized_config)),
        "energy_service_identity": energy_service.identity,
        "e0": _exact_axis(raw["e0"], "e0"),
        "methods": _methods(raw["methods"]),
        "numeric_contract": {
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "kernel_numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "scientific_service_contract": "EXPLICIT_VERSIONED_V4",
            "scientific_float_inputs_allowed": False,
        },
        "fixed_semantics": {
            "scheduling_model": "GLOBAL_FIXED_PRIORITY",
            "scheduler": "ASAP_BLOCK",
            "task_source_default_allowed": False,
            "energy_service_default_allowed": False,
            "family_specific_math_dispatch_allowed": False,
            "legacy_binary64_service_formal_eligible": False,
        },
        "formal_campaign_authorization_status": (
            RTA4_FORMAL_CAMPAIGN_AUTHORIZATION_STATUS_V4
        ),
    }
    return {
        "normalized_scientific_config": scientific,
        "runtime": _runtime(raw["runtime"]),
        "task_source": task_source,
        "energy_service": energy_service,
    }


def rta4_formal_config_hash_v4(scientific_config: Mapping[str, Any]) -> str:
    if (
        not isinstance(scientific_config, Mapping)
        or scientific_config.get("profile") != RTA4_FORMAL_PROFILE_V4
    ):
        raise RTA4FormalConfigV4Error("not a normalized V4 scientific config")
    return domain_hash(RTA4_FORMAL_CONFIG_DOMAIN_V4, scientific_config)


def formal_taskset_store_identity_v4(
    scientific_config: Mapping[str, Any],
) -> str:
    config_identity = rta4_formal_config_hash_v4(scientific_config)
    return domain_hash(RTA4_FORMAL_TASKSET_STORE_DOMAIN_V4, {
        "profile": RTA4_FORMAL_PROFILE_V4,
        "store_schema": "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_V4",
        "scientific_config_identity": config_identity,
        "task_source_mode": scientific_config["task_source"]["mode"],
        "task_source_identity": scientific_config["task_source_identity"],
        "task_source_content_certificate_identity": scientific_config[
            "task_source_content_certificate"
        ]["content_certificate_identity"],
        "energy_service_identity": scientific_config["energy_service_identity"],
        "legacy_store_accepted": False,
        "v3_store_accepted": False,
    })


def formal_taskset_store_header_v4(
    scientific_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the immutable header every future V4 store must persist."""

    store_identity = formal_taskset_store_identity_v4(scientific_config)
    task_source = scientific_config["task_source"]
    base = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_HEADER_V4",
        "profile": RTA4_FORMAL_PROFILE_V4,
        "taskset_store_identity": store_identity,
        "task_source_mode": task_source["mode"],
        "task_source_identity": scientific_config["task_source_identity"],
        "task_source_content_certificate": deepcopy(
            scientific_config["task_source_content_certificate"]
        ),
        "manifest_file_sha256": task_source.get("manifest_file_sha256"),
        "manifest_semantic_sha256": task_source.get(
            "manifest_semantic_sha256"
        ),
        "energy_service_identity": scientific_config[
            "energy_service_identity"
        ],
    }
    return {
        **base,
        "taskset_store_header_identity": domain_hash(
            RTA4_FORMAL_TASKSET_STORE_HEADER_DOMAIN_V4, base,
        ),
    }


def source_closure_identity_v4(scientific_config: Mapping[str, Any]) -> str:
    rta4_formal_config_hash_v4(scientific_config)
    return domain_hash(RTA4_SOURCE_CLOSURE_DOMAIN_V4, {
        "profile": RTA4_FORMAL_PROFILE_V4,
        "task_source": scientific_config["task_source"],
        "task_source_identity": scientific_config["task_source_identity"],
        "content_certificate": scientific_config[
            "task_source_content_certificate"
        ],
        "energy_service": scientific_config["energy_service"],
        "energy_service_identity": scientific_config["energy_service_identity"],
    })


def load_rta4_campaign_v4(path: Path | str) -> LoadedCampaignV4:
    campaign_path = Path(path).expanduser().resolve(strict=True)
    payload = campaign_path.read_bytes()
    try:
        raw = yaml.load(payload, Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4FormalConfigV4Error(
            f"cannot parse V4 campaign: {campaign_path}"
        ) from exc
    normalized = normalize_rta4_campaign_v4(
        raw, base_directory=campaign_path.parent,
    )
    scientific = normalized["normalized_scientific_config"]
    return LoadedCampaignV4(
        campaign_path,
        hashlib.sha256(payload).hexdigest(),
        deepcopy(scientific),
        rta4_formal_config_hash_v4(scientific),
        deepcopy(normalized["runtime"]),
        normalized["task_source"],
        normalized["energy_service"],
    )


__all__ = [
    "LoadedCampaignV4", "RTA4_FORMAL_CAMPAIGN_AUTHORIZATION_STATUS_V4",
    "RTA4_FORMAL_CONFIG_DOMAIN_V4", "RTA4_FORMAL_PLAN_VERSION_V4",
    "RTA4_FORMAL_PROFILE_V4", "RTA4_FORMAL_SCHEMA_VERSION_V4",
    "RTA4_RECURSIVE_METHODS_V4", "RTA4FormalConfigV4Error",
    "formal_taskset_store_header_v4", "formal_taskset_store_identity_v4",
    "load_rta4_campaign_v4",
    "normalize_rta4_campaign_v4", "rta4_formal_config_hash_v4",
    "source_closure_identity_v4",
]
