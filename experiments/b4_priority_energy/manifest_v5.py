"""Config-driven, preflight-only B4-PE V5 manifest construction."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from experiments.common.exact_service_curve import fraction_text

try:
    from .energy_source_v5 import (
        B4EnergySourceConfigV5,
        B4EnergySourceV5Error,
        B4SourceMaterialV5,
        B4TaskEnergyMaterialV5,
        B4TasksetBindingV5,
        B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
        B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1,
        B4_PE_THREE_STAGE_SOURCE_V1,
        build_source_material_v5,
        build_task_energy_material_v5,
        normalize_energy_source_v5,
        normalize_taskset_binding_v5,
        render_system_config_v5,
        validate_relative_path,
    )
except ImportError:  # direct script import from this directory
    from energy_source_v5 import (
        B4EnergySourceConfigV5,
        B4EnergySourceV5Error,
        B4SourceMaterialV5,
        B4TaskEnergyMaterialV5,
        B4TasksetBindingV5,
        B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
        B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1,
        B4_PE_THREE_STAGE_SOURCE_V1,
        build_source_material_v5,
        build_task_energy_material_v5,
        normalize_energy_source_v5,
        normalize_taskset_binding_v5,
        render_system_config_v5,
        validate_relative_path,
    )


B4_DIR = Path(__file__).resolve().parent
PROTOCOL_V5_PATH = B4_DIR / "manifest_protocol_v5.json"
LEGACY_PROTOCOL_V4_PATH = B4_DIR / "manifest_protocol_v4.json"
B4_PE_CAMPAIGN_SCHEMA_V5 = "B4_PE_CAMPAIGN_SCHEMA_V5"
B4_PE_CAMPAIGN_PROFILE_V5 = "B4_PE_SELECTABLE_ENERGY_SOURCE_V5"
B4_PE_CAMPAIGN_CONFIG_DOMAIN_V5 = "B4-PE:CAMPAIGN_CONFIG:v5"
B4_PE_CASE_DOMAIN_V5 = "B4-PE:CASE:v5"
B4_PE_MANIFEST_DOMAIN_V5 = "B4-PE:MANIFEST:v5"
B4_PE_FORMAL_STATUS_V5 = "UNAUTHORIZED_LOCAL_NOT_FOR_PAPER_ONLY"


class B4ManifestV5Error(ValueError):
    """Raised when a V5 manifest would be incomplete or unfair."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _field_set(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise B4ManifestV5Error(
            f"{label} field set mismatch; missing={sorted(expected-actual)}, "
            f"unknown={sorted(actual-expected)}"
        )
    return value


def load_protocol_v5() -> Mapping[str, Any]:
    try:
        payload = PROTOCOL_V5_PATH.read_bytes()
        protocol = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise B4ManifestV5Error("manifest protocol V5 is unreadable") from exc
    required = {
        "schema_version", "protocol_name", "status", "governance",
        "algorithm_order", "algorithm_cli_mapping", "source_models",
        "identity_scopes",
        "simulator_argv0", "legacy_v4_manifest_protocol_ref",
        "legacy_v4_manifest_protocol_sha256",
    }
    _field_set(protocol, required, "manifest protocol V5")
    expected_order = [
        "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC",
        "ALAP-BLOCK", "ALAP-NONBLOCK", "ALAP-SYNC",
        "ST-BLOCK", "ST-NONBLOCK", "ST-SYNC",
    ]
    expected_mapping = {
        "ASAP-BLOCK": "gpfp_asap_block",
        "ASAP-NONBLOCK": "gpfp_asap_nonblock",
        "ASAP-SYNC": "gpfp_asap_sync",
        "ALAP-BLOCK": "gpfp_alap_block",
        "ALAP-NONBLOCK": "gpfp_alap_nonblock",
        "ALAP-SYNC": "gpfp_alap_sync",
        "ST-BLOCK": "gpfp_st_block",
        "ST-NONBLOCK": "gpfp_st_nonblock",
        "ST-SYNC": "gpfp_st_sync",
    }
    if (
        protocol["schema_version"] != 5
        or protocol["status"] != "unauthorized_local_not_for_paper_only"
        or protocol["algorithm_order"] != expected_order
        or protocol["algorithm_cli_mapping"] != expected_mapping
        or protocol["source_models"] != [
            B4_PE_THREE_STAGE_SOURCE_V1,
            B4_PE_EXACT_SERVICE_CURVE_SOURCE_V1,
        ]
        or protocol["identity_scopes"] != {
            "service_curve_identity": "PURE_MATHEMATICAL_SERVICE_CURVE_V1",
            "source_identity": "HARVEST_SUPPLY_TRACE_V1",
            "task_energy_material_identity": "TASK_DEMAND_MATERIAL_V1",
            "configured_energy_system_identity": (
                B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1
            ),
            "case_id": "FULL_CASE_INCLUDING_ALGORITHM_V1",
        }
        or protocol["governance"] != {
            "formal_runs_authorized": False,
            "negative_control_runs_authorized": False,
            "paper_result_authorized": False,
            "pilot_runs_authorized": False,
            "local_not_for_paper_runs_allowed": True,
            "explicit_acknowledgement_required": True,
        }
    ):
        raise B4ManifestV5Error("manifest protocol V5 semantics drifted")
    if (
        protocol["legacy_v4_manifest_protocol_ref"]
        != LEGACY_PROTOCOL_V4_PATH.name
        or protocol["legacy_v4_manifest_protocol_sha256"]
        != file_sha256(LEGACY_PROTOCOL_V4_PATH)
    ):
        raise B4ManifestV5Error("manifest protocol V4 reference drifted")
    validate_relative_path(protocol["simulator_argv0"], "simulator_argv0")
    return protocol


PROTOCOL_V5 = load_protocol_v5()
PROTOCOL_V5_SHA256 = file_sha256(PROTOCOL_V5_PATH)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise B4ManifestV5Error(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class LoadedB4CampaignV5:
    path: Path
    raw_file_sha256: str
    campaign_id: str
    phase: str
    rho_E: str
    horizon_ms: int
    tick_ms: int
    tasksets: tuple[B4TasksetBindingV5, ...]
    energy_source: B4EnergySourceConfigV5
    runtime: Mapping[str, Any]
    normalized_scientific_config: Mapping[str, Any]
    normalized_scientific_config_sha256: str


def _runtime(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw).difference({
        "output_root", "simulator_path", "timeout_seconds", "resume",
        "retry_failed",
    }):
        raise B4ManifestV5Error("runtime contains unknown fields")
    result: dict[str, Any] = {}
    for field in ("output_root", "simulator_path"):
        if field in raw:
            if type(raw[field]) is not str or not raw[field].strip():
                raise B4ManifestV5Error(
                    f"runtime.{field} must be a non-empty path"
                )
            result[field] = raw[field]
    if "timeout_seconds" in raw:
        if type(raw["timeout_seconds"]) is not int or raw["timeout_seconds"] <= 0:
            raise B4ManifestV5Error("runtime.timeout_seconds must be positive")
        result["timeout_seconds"] = raw["timeout_seconds"]
    for field in ("resume", "retry_failed"):
        if field in raw:
            if type(raw[field]) is not bool:
                raise B4ManifestV5Error(
                    f"runtime.{field} must be a strict boolean"
                )
            result[field] = raw[field]
    return result


def normalize_campaign_v5(raw: Any) -> dict[str, Any]:
    row = _field_set(raw, {
        "schema", "campaign_id", "phase", "rho_E", "horizon_ms", "tick_ms",
        "tasksets", "energy_source", "runtime",
    }, "campaign")
    if row["schema"] != B4_PE_CAMPAIGN_SCHEMA_V5:
        raise B4ManifestV5Error("unknown B4-PE campaign V5 schema")
    campaign_id = row["campaign_id"]
    if (
        type(campaign_id) is not str
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", campaign_id) is None
    ):
        raise B4ManifestV5Error("campaign_id is not stable lowercase")
    if row["phase"] not in {"pilot", "formal_main", "negative_control"}:
        raise B4ManifestV5Error("campaign phase is unsupported")
    if row["rho_E"] not in {"1", "2"}:
        raise B4ManifestV5Error("rho_E must be explicit exact text '1' or '2'")
    if type(row["tasksets"]) is not list or not row["tasksets"]:
        raise B4ManifestV5Error("tasksets must be a non-empty list")
    try:
        tasksets = tuple(
            normalize_taskset_binding_v5(item) for item in row["tasksets"]
        )
        source = normalize_energy_source_v5(
            row["energy_source"],
            horizon_ms=row["horizon_ms"],
            tick_ms=row["tick_ms"],
        )
    except B4EnergySourceV5Error as exc:
        raise B4ManifestV5Error(str(exc)) from exc
    if len({taskset.taskset_id for taskset in tasksets}) != len(tasksets):
        raise B4ManifestV5Error("taskset IDs contain duplicates")
    if len({taskset.taskset_identity for taskset in tasksets}) != len(tasksets):
        raise B4ManifestV5Error("taskset identities contain duplicates")
    scientific = {
        "profile": B4_PE_CAMPAIGN_PROFILE_V5,
        "schema": B4_PE_CAMPAIGN_SCHEMA_V5,
        "campaign_id": campaign_id,
        "phase": row["phase"],
        "rho_E": row["rho_E"],
        "horizon_ms": source.horizon_ms,
        "tick_ms": source.tick_ms,
        "tasksets": [taskset.material() for taskset in tasksets],
        "energy_source": deepcopy(dict(source.normalized_config)),
        "energy_source_configuration_identity": source.configuration_identity,
        "configured_energy_system_identity_scope": (
            B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1
        ),
        "algorithm_order": deepcopy(PROTOCOL_V5["algorithm_order"]),
        "algorithm_cli_mapping": deepcopy(
            PROTOCOL_V5["algorithm_cli_mapping"]
        ),
        "fairness_contract": {
            "same_taskset_per_algorithm": True,
            "same_source_per_algorithm": True,
            "same_task_energy_material_per_algorithm": True,
            "same_task_energy_scale_per_algorithm": True,
            "same_initial_and_max_energy_per_algorithm": True,
            "task_energy_scale_is_explicit": True,
            "task_energy_auto_scaling": False,
            "initial_energy_auto_scaling": False,
            "max_energy_auto_scaling": False,
            "algorithm_implementation_changes": False,
        },
        "formal_status": B4_PE_FORMAL_STATUS_V5,
        "manifest_protocol_v5_sha256": PROTOCOL_V5_SHA256,
    }
    return {
        "campaign_id": campaign_id,
        "phase": row["phase"],
        "rho_E": row["rho_E"],
        "horizon_ms": source.horizon_ms,
        "tick_ms": source.tick_ms,
        "tasksets": tasksets,
        "energy_source": source,
        "runtime": _runtime(row["runtime"]),
        "normalized_scientific_config": scientific,
        "normalized_scientific_config_sha256": domain_hash(
            B4_PE_CAMPAIGN_CONFIG_DOMAIN_V5, scientific,
        ),
    }


def load_campaign_v5(path: Path | str) -> LoadedB4CampaignV5:
    campaign_path = Path(path).expanduser().resolve(strict=True)
    payload = campaign_path.read_bytes()
    try:
        raw = yaml.load(payload, Loader=_UniqueLoader)
    except B4ManifestV5Error:
        raise
    except Exception as exc:
        raise B4ManifestV5Error(
            f"cannot parse B4-PE V5 campaign: {campaign_path}"
        ) from exc
    normalized = normalize_campaign_v5(raw)
    for taskset in normalized["tasksets"]:
        base_path = B4_DIR.parents[1].joinpath(*Path(taskset.base_taskset_path).parts)
        execution_path = B4_DIR.parents[1].joinpath(
            *Path(taskset.execution_taskset_path).parts
        )
        if not base_path.is_file() or not execution_path.is_file():
            raise B4ManifestV5Error(
                "B4-PE V5 taskset paths must name existing repository files"
            )
        if file_sha256(execution_path) != taskset.taskset_identity:
            raise B4ManifestV5Error(
                f"execution taskset content identity drift: {taskset.taskset_id}"
            )
        try:
            base_document = yaml.safe_load(base_path.read_text(encoding="utf-8"))
            execution_document = yaml.safe_load(
                execution_path.read_text(encoding="utf-8")
            )
            import materialization_common as legacy_materialization
            legacy_materialization.validate_execution_taskset(
                base_document, execution_document, normalized["rho_E"],
            )
        except Exception as exc:
            raise B4ManifestV5Error(
                f"taskset is outside the frozen B4 family: {taskset.taskset_id}"
            ) from exc
    return LoadedB4CampaignV5(
        campaign_path,
        hashlib.sha256(payload).hexdigest(),
        normalized["campaign_id"],
        normalized["phase"],
        normalized["rho_E"],
        normalized["horizon_ms"],
        normalized["tick_ms"],
        normalized["tasksets"],
        normalized["energy_source"],
        normalized["runtime"],
        normalized["normalized_scientific_config"],
        normalized["normalized_scientific_config_sha256"],
    )


def _case(
    campaign: LoadedB4CampaignV5,
    taskset: B4TasksetBindingV5,
    source: B4SourceMaterialV5,
    task_energy: B4TaskEnergyMaterialV5,
    algorithm: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cli = str(PROTOCOL_V5["algorithm_cli_mapping"][algorithm])
    identity_material = {
        "profile": B4_PE_CAMPAIGN_PROFILE_V5,
        "normalized_scientific_config_sha256": (
            campaign.normalized_scientific_config_sha256
        ),
        "phase": campaign.phase,
        "rho_E": campaign.rho_E,
        "taskset_identity": taskset.taskset_identity,
        "source_identity": source.source_identity,
        "task_energy_material_identity": (
            task_energy.task_energy_material_identity
        ),
        "configured_energy_system_identity": (
            source.configured_energy_system_identity
        ),
        "configured_energy_system_identity_scope": (
            source.configured_energy_system_identity_scope
        ),
        "algorithm": algorithm,
        "algorithm_cli": cli,
    }
    case_id = "case-v5-" + domain_hash(B4_PE_CASE_DOMAIN_V5, identity_material)
    source_path = f"artifacts/b4_pe_v5/sources/{source.source_id}.json"
    system_path = f"artifacts/b4_pe_v5/configs/{cli}/{case_id}.yml"
    result_path = f"results/b4_pe_v5/{campaign.phase}/{case_id}.json"
    timeout = int(campaign.runtime.get("timeout_seconds", 300))
    simulator = str(campaign.runtime.get(
        "simulator_path", PROTOCOL_V5["simulator_argv0"]
    ))
    system_bytes = render_system_config_v5(source, cli)
    record = {
        "schema_version": 5,
        "profile": B4_PE_CAMPAIGN_PROFILE_V5,
        "campaign_id": campaign.campaign_id,
        "phase": campaign.phase,
        "case_id": case_id,
        "taskset_id": taskset.taskset_id,
        "taskset_identity": taskset.taskset_identity,
        "source_id": source.source_id,
        "source_identity": source.source_identity,
        "configured_energy_system_identity": (
            source.configured_energy_system_identity
        ),
        "configured_energy_system_identity_scope": (
            source.configured_energy_system_identity_scope
        ),
        "service_curve_identity": source.service_curve_identity,
        "energy_bounds_identity": source.energy_bounds_identity,
        "task_energy_material_identity": (
            task_energy.task_energy_material_identity
        ),
        "trace_sha256": source.trace_sha256,
        "algorithm": algorithm,
        "algorithm_cli": cli,
        "rho_E": campaign.rho_E,
        "horizon_ms": campaign.horizon_ms,
        "tick_ms": campaign.tick_ms,
        "task_energy_scale": fraction_text(source.task_energy_scale),
        "initial_energy_j": fraction_text(source.initial_energy),
        "max_energy_j": fraction_text(source.max_energy),
        "source_taskset_artifact_relpath": taskset.execution_taskset_path,
        "taskset_artifact_relpath": task_energy.artifact_relpath,
        "taskset_artifact_sha256": task_energy.artifact_sha256,
        "source_artifact_relpath": source_path,
        "system_config_artifact_relpath": system_path,
        "system_config_sha256": hashlib.sha256(system_bytes).hexdigest(),
        "result_relpath": result_path,
        "timeout_seconds": timeout,
        "retry_policy": {
            "initial_timeout_seconds": timeout,
            "retry_timeout_seconds": timeout * 2,
            "max_attempts": 2,
            "retry_on": ["timeout"],
            "on_final_failure": "fail_closed",
        },
        "command_argv": [
            simulator,
            system_path,
            task_energy.artifact_relpath,
            str(campaign.horizon_ms),
            "-t",
            result_path,
            "--run-id",
            case_id,
            "--b4-observability-summary",
            "--b4-summary-horizon",
            str(campaign.horizon_ms),
            "--b4-observability-contract-version",
            "2",
        ],
        "manifest_protocol_v5_sha256": PROTOCOL_V5_SHA256,
        "formal_status": B4_PE_FORMAL_STATUS_V5,
        "execution_authorized": False,
        "local_not_for_paper_execution_allowed": True,
    }
    system_preview = {
        "case_id": case_id,
        "algorithm": algorithm,
        "algorithm_cli": cli,
        "source_id": source.source_id,
        "system_config_artifact_relpath": system_path,
        "system_config_sha256": record["system_config_sha256"],
        "system_config_yaml": system_bytes.decode("utf-8"),
    }
    return record, system_preview


def build_manifest_v5(
    campaign: LoadedB4CampaignV5,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[B4SourceMaterialV5, ...],
    tuple[B4TaskEnergyMaterialV5, ...],
    tuple[dict[str, Any], ...],
]:
    if type(campaign) is not LoadedB4CampaignV5:
        raise B4ManifestV5Error("campaign has not been loaded by V5")
    records: list[dict[str, Any]] = []
    sources: list[B4SourceMaterialV5] = []
    task_materials: list[B4TaskEnergyMaterialV5] = []
    configs: list[dict[str, Any]] = []
    for taskset in campaign.tasksets:
        try:
            source = build_source_material_v5(campaign.energy_source, taskset)
        except B4EnergySourceV5Error as exc:
            raise B4ManifestV5Error(str(exc)) from exc
        sources.append(source)
        try:
            task_energy = build_task_energy_material_v5(
                taskset,
                str(campaign.energy_source.normalized_config.get(
                    "task_energy_scale", "1"
                )),
            )
        except B4EnergySourceV5Error as exc:
            raise B4ManifestV5Error(str(exc)) from exc
        task_materials.append(task_energy)
        taskset_records = []
        for algorithm in PROTOCOL_V5["algorithm_order"]:
            record, config = _case(
                campaign, taskset, source, task_energy, str(algorithm)
            )
            records.append(record)
            taskset_records.append(record)
            configs.append(config)
        if len({row["source_identity"] for row in taskset_records}) != 1:
            raise B4ManifestV5Error("nine algorithms do not share one source")
        if len({
            row["configured_energy_system_identity"]
            for row in taskset_records
        }) != 1:
            raise B4ManifestV5Error(
                "nine algorithms do not share one configured energy system"
            )
        if {
            row["configured_energy_system_identity_scope"]
            for row in taskset_records
        } != {B4_PE_CONFIGURED_ENERGY_SYSTEM_SCOPE_V1}:
            raise B4ManifestV5Error(
                "configured energy system identity scope drifted"
            )
        if len({row["taskset_identity"] for row in taskset_records}) != 1:
            raise B4ManifestV5Error("nine algorithms do not share one taskset")
        if len({
            row["task_energy_material_identity"]
            for row in taskset_records
        }) != 1:
            raise B4ManifestV5Error(
                "nine algorithms do not share one task-energy material"
            )
        if len({
            (row["initial_energy_j"], row["max_energy_j"], row["task_energy_scale"])
            for row in taskset_records
        }) != 1:
            raise B4ManifestV5Error("nine algorithms do not share energy bounds")
    if len({row["case_id"] for row in records}) != len(records):
        raise B4ManifestV5Error("V5 case identity collision")
    if len(records) != len(campaign.tasksets) * 9:
        raise B4ManifestV5Error("V5 case count is not nine per taskset")
    return (
        tuple(records), tuple(sources), tuple(task_materials), tuple(configs)
    )


def render_manifest_v5(records: Sequence[Mapping[str, Any]]) -> bytes:
    if not records:
        raise B4ManifestV5Error("manifest has no records")
    return b"".join(
        canonical_json(dict(record)).encode("utf-8") + b"\n"
        for record in records
    )


def materialize_campaign_v5(
    campaign: LoadedB4CampaignV5,
    output_root: Path | str,
) -> dict[str, Any]:
    """Publish V5 inputs for the unchanged B4 execution state machine."""

    if type(campaign) is not LoadedB4CampaignV5:
        raise B4ManifestV5Error("campaign has not been loaded by V5")
    root = Path(output_root).expanduser().resolve(strict=False)
    repository = B4_DIR.parents[1].resolve(strict=True)
    if root == repository or repository in root.parents:
        raise B4ManifestV5Error(
            "V5 local output root must be outside the repository"
        )
    records, sources, task_materials, configs = build_manifest_v5(campaign)
    manifest_bytes = render_manifest_v5(records)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_relative = (
        f"artifacts/b4_pe_v5/manifests/{manifest_sha}.jsonl"
    )
    try:
        import materialization_common as legacy_materialization

        for material in task_materials:
            legacy_materialization._publish_identical_or_fail(
                root, material.artifact_relpath, material.artifact_bytes,
            )
        records_by_source = {
            record["source_id"]: record for record in records
        }
        for source in sources:
            record = records_by_source[source.source_id]
            payload = (
                canonical_json(dict(source.descriptor)).encode("utf-8") + b"\n"
            )
            legacy_materialization._publish_identical_or_fail(
                root, record["source_artifact_relpath"], payload,
            )
        for config in configs:
            legacy_materialization._publish_identical_or_fail(
                root,
                config["system_config_artifact_relpath"],
                config["system_config_yaml"].encode("utf-8"),
            )
        legacy_materialization._publish_identical_or_fail(
            root, manifest_relative, manifest_bytes,
        )
    except Exception as exc:
        if isinstance(exc, B4ManifestV5Error):
            raise
        raise B4ManifestV5Error(
            f"V5 execution input materialization failed: {exc}"
        ) from exc
    return {
        "output_root": root,
        "manifest_path": root.joinpath(*Path(manifest_relative).parts),
        "manifest_sha256": manifest_sha,
        "records": records,
        "sources": sources,
        "task_energy_materials": task_materials,
        "system_configs": configs,
    }


def execute_local_campaign_v5(
    path: Path | str,
    *,
    acknowledge_not_for_paper: bool,
    output_root: Path | str | None = None,
    simulator_binary: Path | str | None = None,
    limit: int | None = None,
    resume: bool | None = None,
    retry_failed: bool | None = None,
    _executor: Any = None,
) -> dict[str, Any]:
    """Materialize V5 and enter the existing executor only with explicit ack."""

    if acknowledge_not_for_paper is not True:
        raise B4ManifestV5Error(
            "local execution requires acknowledge_not_for_paper=true"
        )
    if limit is not None and (type(limit) is not int or limit < 0):
        raise B4ManifestV5Error("local execution limit must be non-negative")
    campaign = load_campaign_v5(path)
    configured = campaign.runtime
    root_value = output_root if output_root is not None else configured.get(
        "output_root"
    )
    simulator_value = (
        simulator_binary
        if simulator_binary is not None
        else configured.get("simulator_path")
    )
    if root_value is None or simulator_value is None:
        raise B4ManifestV5Error(
            "local execution requires output_root and simulator_binary"
        )
    root = Path(root_value).expanduser().resolve(strict=False)
    simulator = Path(simulator_value).expanduser()
    if not simulator.is_absolute():
        simulator = B4_DIR.parents[1] / simulator
    simulator = simulator.resolve(strict=False)
    bundle = materialize_campaign_v5(campaign, root)
    if _executor is None:
        try:
            from .execution_common import execute_validated_cases
        except ImportError:
            from execution_common import execute_validated_cases
        executor = execute_validated_cases
    else:
        executor = _executor
    summary = executor(
        list(bundle["records"]),
        bundle["manifest_path"],
        root,
        simulator,
        limit=limit,
        resume=(
            bool(configured.get("resume", False))
            if resume is None else resume
        ),
        retry_failed=(
            bool(configured.get("retry_failed", False))
            if retry_failed is None else retry_failed
        ),
    )
    return {
        "profile": B4_PE_CAMPAIGN_PROFILE_V5,
        "campaign_id": campaign.campaign_id,
        "manifest_sha256": bundle["manifest_sha256"],
        "manifest_record_count": len(bundle["records"]),
        "executor_summary": dict(summary),
        "execution_started": True,
        "formal_campaign_started": False,
        "paper_result_authorized": False,
        "not_for_paper": True,
    }


def preflight_campaign_v5(path: Path | str) -> dict[str, Any]:
    campaign = load_campaign_v5(path)
    records, sources, task_materials, configs = build_manifest_v5(campaign)
    manifest_bytes = render_manifest_v5(records)
    preview_material = {
        "profile": B4_PE_CAMPAIGN_PROFILE_V5,
        "campaign_id": campaign.campaign_id,
        "phase": campaign.phase,
        "raw_campaign_file_sha256": campaign.raw_file_sha256,
        "normalized_scientific_config_sha256": (
            campaign.normalized_scientific_config_sha256
        ),
        "manifest_protocol_v5_sha256": PROTOCOL_V5_SHA256,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest_record_count": len(records),
        "source_count": len(sources),
        "algorithm_order": deepcopy(PROTOCOL_V5["algorithm_order"]),
        "records": list(records),
        "source_materials": [dict(source.descriptor) for source in sources],
        "energy_bounds_materials": [
            dict(source.energy_bounds_descriptor) for source in sources
        ],
        "configured_energy_system_materials": [
            dict(source.configured_energy_system_descriptor)
            for source in sources
        ],
        "task_energy_materials": [
            dict(material.descriptor) for material in task_materials
        ],
        "system_config_previews": list(configs),
        "runtime": dict(campaign.runtime),
        "formal_status": B4_PE_FORMAL_STATUS_V5,
        "execution_started": False,
    }
    return {
        **preview_material,
        "preflight_identity": domain_hash(
            B4_PE_MANIFEST_DOMAIN_V5,
            {
                key: value for key, value in preview_material.items()
                if key not in {"system_config_previews", "runtime"}
            },
        ),
    }


__all__ = [
    "B4ManifestV5Error",
    "B4_PE_CAMPAIGN_PROFILE_V5",
    "B4_PE_CAMPAIGN_SCHEMA_V5",
    "B4_PE_FORMAL_STATUS_V5",
    "LoadedB4CampaignV5",
    "PROTOCOL_V5",
    "PROTOCOL_V5_SHA256",
    "build_manifest_v5",
    "execute_local_campaign_v5",
    "load_campaign_v5",
    "materialize_campaign_v5",
    "normalize_campaign_v5",
    "preflight_campaign_v5",
    "render_manifest_v5",
]
