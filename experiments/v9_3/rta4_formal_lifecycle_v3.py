"""Prepared and authorization identities for parameterized RTA4 V3 campaigns."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping

from .result_writer import atomic_write_json
from .rta4_formal_config import domain_hash
from .rta4_formal_config_v3 import (
    LoadedCampaignV3,
    RTA4_FORMAL_PROFILE_V3,
    load_rta4_campaign_v3,
    source_binding_v3,
)
from .rta4_formal_environment import load_strict_json
from .rta4_formal_plan_v3 import describe_formal_plan_v3
from .rta4_formal_schema_v3 import formal_schema_hash_v3


RTA4_PREPARED_CONFIG_SCHEMA_V3 = "ASAP_BLOCK_V9_3_RTA4_PREPARED_CONFIG_V3_PARAMETERIZED"
RTA4_PREPARED_CONFIG_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_PREPARED_CONFIG:v3"
RTA4_AUTHORIZATION_SCHEMA_V3 = "ASAP_BLOCK_V9_3_RTA4_AUTHORIZATION_V3_PARAMETERIZED"
RTA4_AUTHORIZATION_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_AUTHORIZATION:v3"
RTA4_RUN_NAMESPACE_SCHEMA_V3 = "ASAP_BLOCK_V9_3_RTA4_RESULT_NAMESPACE_V3"
RTA4_RUN_NAMESPACE_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_RESULT_NAMESPACE:v3"
RTA4_CHECKPOINT_SCHEMA_V3 = "ASAP_BLOCK_V9_3_RTA4_CHECKPOINT_V3"
RTA4_CHECKPOINT_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_CHECKPOINT:v3"
RTA4_RUN_NAMESPACE_FILENAME_V3 = "formal_run_namespace_v3.json"


class RTA4FormalLifecycleV3Error(RuntimeError):
    """Raised before a parameterized identity boundary can drift."""


def _sha(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise RTA4FormalLifecycleV3Error(f"{label} must be a lowercase SHA-256")
    return value


def _absolute_path(value: Path | str, label: str, *, existing_file: bool = False) -> str:
    try:
        path = Path(value).expanduser().resolve(strict=existing_file)
    except OSError as exc:
        raise RTA4FormalLifecycleV3Error(f"{label} cannot be resolved") from exc
    if existing_file and not path.is_file():
        raise RTA4FormalLifecycleV3Error(f"{label} must be an existing file")
    return str(path)


def _production_manifest(path: Path | str) -> tuple[str, str, str]:
    absolute = _absolute_path(path, "production manifest", existing_file=True)
    try:
        payload = Path(absolute).read_bytes()
        document = load_strict_json(absolute)
        identity = _sha(document.get("manifest_id"), "production manifest identity")
    except Exception as exc:
        raise RTA4FormalLifecycleV3Error("cannot bind production manifest") from exc
    return absolute, hashlib.sha256(payload).hexdigest(), identity


def _operational(
    campaign: LoadedCampaignV3, *, output_root: Path | str | None,
    taskset_store: Path | str | None, worker_count: int | None,
    max_in_flight: int | None, timeout_seconds: int | None,
    max_records: int | None, log_path: Path | str | None, resume: bool | None,
) -> Dict[str, Any]:
    runtime = dict(campaign.runtime)
    overrides = {
        "output_root": output_root, "taskset_store": taskset_store,
        "worker_count": worker_count, "max_in_flight": max_in_flight,
        "timeout_seconds": timeout_seconds, "max_records": max_records,
        "log_path": log_path, "resume": resume,
    }
    for key, value in overrides.items():
        if value is not None:
            runtime[key] = value
    if "output_root" not in runtime or "taskset_store" not in runtime:
        raise RTA4FormalLifecycleV3Error(
            "prepared config requires output_root and taskset_store operational paths"
        )
    result: Dict[str, Any] = {
        "output_root": _absolute_path(runtime["output_root"], "output_root"),
        "taskset_store": _absolute_path(runtime["taskset_store"], "taskset_store"),
        "worker_count": runtime.get("worker_count", 1),
        "max_in_flight": runtime.get("max_in_flight", runtime.get("worker_count", 1)),
        "timeout_seconds": runtime.get("timeout_seconds", 60),
        "max_records": runtime.get("max_records"),
        "resume": runtime.get("resume", False),
    }
    if "log_path" in runtime:
        result["log_path"] = _absolute_path(runtime["log_path"], "log_path")
    for key in ("worker_count", "max_in_flight", "timeout_seconds"):
        if type(result[key]) is not int or result[key] <= 0:
            raise RTA4FormalLifecycleV3Error(f"operational.{key} must be positive")
    if result["max_in_flight"] < result["worker_count"]:
        raise RTA4FormalLifecycleV3Error("max_in_flight must cover worker_count")
    if result["max_records"] is not None and (
        type(result["max_records"]) is not int or result["max_records"] < 0
    ):
        raise RTA4FormalLifecycleV3Error("max_records must be non-negative")
    if type(result["resume"]) is not bool:
        raise RTA4FormalLifecycleV3Error("resume must be a strict boolean")
    return result


def build_prepared_config_v3(
    campaign: LoadedCampaignV3, *, production_manifest_path: Path | str,
    output_root: Path | str | None = None,
    taskset_store: Path | str | None = None,
    worker_count: int | None = None, max_in_flight: int | None = None,
    timeout_seconds: int | None = None, log_path: Path | str | None = None,
    max_records: int | None = None, resume: bool | None = None,
    observed_source_binding: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    if type(campaign) is not LoadedCampaignV3:
        raise RTA4FormalLifecycleV3Error("prepared config requires a loaded V3 campaign")
    expected_source = source_binding_v3(campaign.normalized_scientific_config)
    if expected_source is not None and observed_source_binding is None:
        raise RTA4FormalLifecycleV3Error(
            "downstream prepared config requires an observed source binding"
        )
    if expected_source is None and observed_source_binding is not None:
        raise RTA4FormalLifecycleV3Error("CORE-1 does not accept a source binding")
    plan = describe_formal_plan_v3(
        campaign.normalized_scientific_config,
        observed_source_binding=observed_source_binding,
    )
    manifest_path, manifest_sha, manifest_identity = _production_manifest(
        production_manifest_path,
    )
    material: Dict[str, Any] = {
        "prepared_schema": RTA4_PREPARED_CONFIG_SCHEMA_V3,
        "profile": RTA4_FORMAL_PROFILE_V3,
        "campaign_file": {
            "absolute_path": str(campaign.campaign_path),
            "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
        },
        "normalized_scientific_config": dict(
            campaign.normalized_scientific_config
        ),
        "normalized_scientific_config_sha256": (
            campaign.normalized_scientific_config_sha256
        ),
        "formal_schema_sha256": formal_schema_hash_v3(),
        "plan_sha256": plan["plan_sha256"],
        "ordered_stream_digest": plan["ordered_stream_digest"],
        "ordered_stream_count": plan["ordered_stream_count"],
        "dynamic_counts": {
            "taskset_skeleton_count": plan["taskset_skeleton_count"],
            "mathematical_request_count": plan["mathematical_request_count"],
            "ordered_stream_count": plan["ordered_stream_count"],
        },
        "taskset_store_identity": plan["taskset_store_identity"],
        "source_binding": source_binding_v3(
            campaign.normalized_scientific_config
        ),
        "production_manifest": {
            "absolute_path": manifest_path,
            "file_sha256": manifest_sha,
            "production_build_manifest_identity": manifest_identity,
        },
        "operational": _operational(
            campaign, output_root=output_root, taskset_store=taskset_store,
            worker_count=worker_count, max_in_flight=max_in_flight,
            timeout_seconds=timeout_seconds, max_records=max_records,
            log_path=log_path, resume=resume,
        ),
    }
    material["prepared_config_id"] = domain_hash(
        RTA4_PREPARED_CONFIG_DOMAIN_V3, material,
    )
    return validate_prepared_config_v3(material)


def validate_prepared_config_v3(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4FormalLifecycleV3Error("prepared config must be a mapping")
    exact = {
        "prepared_schema", "profile", "campaign_file",
        "normalized_scientific_config", "normalized_scientific_config_sha256",
        "formal_schema_sha256", "plan_sha256", "ordered_stream_digest",
        "ordered_stream_count", "dynamic_counts", "taskset_store_identity",
        "source_binding", "production_manifest", "operational", "prepared_config_id",
    }
    if set(value) != exact or value.get("prepared_schema") != RTA4_PREPARED_CONFIG_SCHEMA_V3:
        raise RTA4FormalLifecycleV3Error("prepared config field/schema mismatch")
    if value.get("profile") != RTA4_FORMAL_PROFILE_V3:
        raise RTA4FormalLifecycleV3Error("prepared config profile mismatch")
    campaign_file = value["campaign_file"]
    if not isinstance(campaign_file, Mapping) or set(campaign_file) != {
        "absolute_path", "raw_campaign_file_sha256",
    }:
        raise RTA4FormalLifecycleV3Error("campaign file binding mismatch")
    loaded = load_rta4_campaign_v3(campaign_file["absolute_path"])
    if (
        loaded.raw_campaign_file_sha256 != campaign_file["raw_campaign_file_sha256"]
        or dict(loaded.normalized_scientific_config)
        != dict(value["normalized_scientific_config"])
        or loaded.normalized_scientific_config_sha256
        != value["normalized_scientific_config_sha256"]
    ):
        raise RTA4FormalLifecycleV3Error("campaign file/scientific identity drift")
    plan = describe_formal_plan_v3(loaded.normalized_scientific_config)
    for key in (
        "plan_sha256", "ordered_stream_digest", "ordered_stream_count",
        "taskset_store_identity",
    ):
        if value[key] != plan[key]:
            raise RTA4FormalLifecycleV3Error(f"prepared {key} mismatch")
    expected_counts = {
        "taskset_skeleton_count": plan["taskset_skeleton_count"],
        "mathematical_request_count": plan["mathematical_request_count"],
        "ordered_stream_count": plan["ordered_stream_count"],
    }
    if value["dynamic_counts"] != expected_counts:
        raise RTA4FormalLifecycleV3Error("prepared dynamic count mismatch")
    if value["formal_schema_sha256"] != formal_schema_hash_v3():
        raise RTA4FormalLifecycleV3Error("prepared schema identity mismatch")
    if value["source_binding"] != source_binding_v3(loaded.normalized_scientific_config):
        raise RTA4FormalLifecycleV3Error("prepared source identity mismatch")
    manifest = value["production_manifest"]
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "absolute_path", "file_sha256", "production_build_manifest_identity",
    }:
        raise RTA4FormalLifecycleV3Error("prepared manifest binding mismatch")
    manifest_path, manifest_sha, manifest_id = _production_manifest(manifest["absolute_path"])
    if (manifest_path != manifest["absolute_path"] or manifest_sha != manifest["file_sha256"]
            or manifest_id != manifest["production_build_manifest_identity"]):
        raise RTA4FormalLifecycleV3Error("production manifest drift")
    operation = value["operational"]
    if not isinstance(operation, Mapping) or set(operation).difference({
        "output_root", "taskset_store", "worker_count", "max_in_flight",
        "timeout_seconds", "max_records", "resume", "log_path",
    }):
        raise RTA4FormalLifecycleV3Error("prepared operational field mismatch")
    _operational(
        loaded, output_root=operation.get("output_root"),
        taskset_store=operation.get("taskset_store"),
        worker_count=operation.get("worker_count"),
        max_in_flight=operation.get("max_in_flight"),
        timeout_seconds=operation.get("timeout_seconds"),
        max_records=operation.get("max_records"), log_path=operation.get("log_path"),
        resume=operation.get("resume"),
    )
    unsigned = dict(value)
    observed = unsigned.pop("prepared_config_id")
    if observed != domain_hash(RTA4_PREPARED_CONFIG_DOMAIN_V3, unsigned):
        raise RTA4FormalLifecycleV3Error("prepared config identity mismatch")
    return deepcopy(dict(value))


def build_authorization_v3(prepared_config: Mapping[str, Any]) -> Dict[str, Any]:
    prepared = validate_prepared_config_v3(prepared_config)
    material = {
        "authorization_schema": RTA4_AUTHORIZATION_SCHEMA_V3,
        "profile": RTA4_FORMAL_PROFILE_V3,
        "authorization_scope": "HASH_BOUND_PARAMETERIZED_CAMPAIGN_V3",
        "prepared_config_id": prepared["prepared_config_id"],
        "normalized_scientific_config_sha256": prepared[
            "normalized_scientific_config_sha256"
        ],
        "plan_sha256": prepared["plan_sha256"],
        "production_build_manifest_identity": prepared["production_manifest"][
            "production_build_manifest_identity"
        ],
        "taskset_store_identity": prepared["taskset_store_identity"],
        "output_root": prepared["operational"]["output_root"],
    }
    material["authorization_id"] = domain_hash(
        RTA4_AUTHORIZATION_DOMAIN_V3, material,
    )
    return validate_authorization_v3(material, prepared_config=prepared)


def validate_authorization_v3(
    value: Mapping[str, Any], *, prepared_config: Mapping[str, Any],
) -> Dict[str, Any]:
    prepared = validate_prepared_config_v3(prepared_config)
    exact = {
        "authorization_schema", "profile", "authorization_scope",
        "prepared_config_id", "normalized_scientific_config_sha256",
        "plan_sha256", "production_build_manifest_identity",
        "taskset_store_identity", "output_root", "authorization_id",
    }
    if not isinstance(value, Mapping) or set(value) != exact:
        raise RTA4FormalLifecycleV3Error("authorization field mismatch")
    expected = {
        "authorization_schema": RTA4_AUTHORIZATION_SCHEMA_V3,
        "profile": RTA4_FORMAL_PROFILE_V3,
        "authorization_scope": "HASH_BOUND_PARAMETERIZED_CAMPAIGN_V3",
        "prepared_config_id": prepared["prepared_config_id"],
        "normalized_scientific_config_sha256": prepared[
            "normalized_scientific_config_sha256"
        ],
        "plan_sha256": prepared["plan_sha256"],
        "production_build_manifest_identity": prepared["production_manifest"][
            "production_build_manifest_identity"
        ],
        "taskset_store_identity": prepared["taskset_store_identity"],
        "output_root": prepared["operational"]["output_root"],
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise RTA4FormalLifecycleV3Error("authorization/prepared binding mismatch")
    if value["authorization_id"] != domain_hash(RTA4_AUTHORIZATION_DOMAIN_V3, expected):
        raise RTA4FormalLifecycleV3Error("authorization identity mismatch")
    return deepcopy(dict(value))


def ensure_result_namespace_v3(
    prepared_config: Mapping[str, Any], authorization: Mapping[str, Any], *,
    resume: bool,
) -> Dict[str, Any]:
    prepared = validate_prepared_config_v3(prepared_config)
    authorized = validate_authorization_v3(authorization, prepared_config=prepared)
    if type(resume) is not bool:
        raise RTA4FormalLifecycleV3Error("resume must be a strict boolean")
    root = Path(prepared["operational"]["output_root"])
    marker = root / RTA4_RUN_NAMESPACE_FILENAME_V3
    material = {
        "namespace_schema": RTA4_RUN_NAMESPACE_SCHEMA_V3,
        "profile": RTA4_FORMAL_PROFILE_V3,
        "campaign_config_sha256": prepared["normalized_scientific_config_sha256"],
        "plan_sha256": prepared["plan_sha256"],
        "prepared_config_id": prepared["prepared_config_id"],
        "authorization_id": authorized["authorization_id"],
        "output_root": str(root.resolve()),
    }
    expected = {**material, "namespace_id": domain_hash(RTA4_RUN_NAMESPACE_DOMAIN_V3, material)}
    if root.exists() and any(root.iterdir()):
        if not marker.is_file() or load_strict_json(marker) != expected:
            raise RTA4FormalLifecycleV3Error(
                "existing result directory belongs to another or legacy campaign"
            )
    elif resume:
        raise RTA4FormalLifecycleV3Error("resume requires an existing V3 namespace")
    else:
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, expected)
    return expected


def validate_checkpoint_v3(
    checkpoint: Mapping[str, Any], *, prepared_config: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> Dict[str, Any]:
    prepared = validate_prepared_config_v3(prepared_config)
    authorized = validate_authorization_v3(authorization, prepared_config=prepared)
    if not isinstance(checkpoint, Mapping) or set(checkpoint) != {
        "checkpoint_schema", "prepared_config_id", "authorization_id",
        "plan_sha256", "completed_execution_ids", "checkpoint_id",
    }:
        raise RTA4FormalLifecycleV3Error("legacy or malformed checkpoint")
    if (
        checkpoint["checkpoint_schema"] != RTA4_CHECKPOINT_SCHEMA_V3
        or checkpoint["prepared_config_id"] != prepared["prepared_config_id"]
        or checkpoint["authorization_id"] != authorized["authorization_id"]
        or checkpoint["plan_sha256"] != prepared["plan_sha256"]
        or type(checkpoint["completed_execution_ids"]) is not list
    ):
        raise RTA4FormalLifecycleV3Error("checkpoint campaign identity mismatch")
    unsigned = dict(checkpoint)
    observed = unsigned.pop("checkpoint_id")
    if observed != domain_hash(RTA4_CHECKPOINT_DOMAIN_V3, unsigned):
        raise RTA4FormalLifecycleV3Error("checkpoint identity mismatch")
    return deepcopy(dict(checkpoint))


__all__ = [
    "RTA4_AUTHORIZATION_SCHEMA_V3", "RTA4_CHECKPOINT_SCHEMA_V3",
    "RTA4_PREPARED_CONFIG_SCHEMA_V3", "RTA4FormalLifecycleV3Error",
    "build_authorization_v3", "build_prepared_config_v3",
    "ensure_result_namespace_v3", "validate_authorization_v3",
    "validate_checkpoint_v3", "validate_prepared_config_v3",
]
