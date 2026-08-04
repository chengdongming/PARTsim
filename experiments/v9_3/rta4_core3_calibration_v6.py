"""Independent, paired CORE-3 calibration generation and audit contracts."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from .result_writer import atomic_write_json, atomic_write_text
from .rta4_formal_config import domain_hash, fraction_text
from .rta4_formal_config_v5 import (
    RTA4_FORMAL_PROFILE_V5,
    load_rta4_campaign_v5,
    normalize_rta4_campaign_v5,
)
from .rta4_formal_plan_v5 import describe_formal_plan_v5, iter_formal_plan_v5
from .rta4_local_execution_v5 import (
    RTA4_LOCAL_RESULT_DOMAIN_V6,
    RTA4_LOCAL_RUN_DOMAIN_V5,
    _prepared_record_material,
)
from .rta4_core3_experiment1_audit_v6 import (
    RTA4Core3Experiment1AuditV6Error,
    load_core3_result_file_v6,
)
from .rta4_task_source_v4 import GENERATED_FAMILY, _UniqueKeyLoader


CORE3_CALIBRATION_CONFIG_V6 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_CONFIG_V6"
)
CORE3_CALIBRATION_MANIFEST_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_MANIFEST:v6"
)
CORE3_CALIBRATION_SUMMARY_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_SUMMARY:v6"
)
CORE3_CALIBRATION_FREEZE_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_CALIBRATION_FREEZE:v6"
)
CORE3_CALIBRATION_RELEASE_HORIZONS_V6 = (30000, 60000)
CORE3_CALIBRATION_SUMMARY_SCHEMA_V6 = (
    "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_SUMMARY_V6"
)


class RTA4Core3CalibrationV6Error(ValueError):
    """Raised when calibration pairing or provenance is incomplete."""


def _sha(value: Any, label: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RTA4Core3CalibrationV6Error(f"{label} must be a SHA-256")
    return value


def _canonical_capacity_axis(value: Any) -> list[str]:
    if type(value) is not list or not value:
        raise RTA4Core3CalibrationV6Error(
            "finite battery candidate capacities must be non-empty"
        )
    result = []
    for index, item in enumerate(value):
        if type(item) is not str:
            raise RTA4Core3CalibrationV6Error(
                f"capacity {index} must be a rational string"
            )
        try:
            exact = Fraction(item)
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4Core3CalibrationV6Error(
                f"capacity {index} is not rational"
            ) from exc
        if exact <= 0 or fraction_text(exact) != item:
            raise RTA4Core3CalibrationV6Error(
                f"capacity {index} must be canonical and positive"
            )
        result.append(item)
    if len(set(result)) != len(result):
        raise RTA4Core3CalibrationV6Error("capacity candidates are duplicated")
    return result


def materialize_calibration_campaigns_v6(
    raw: Any, *, base_directory: Path | str | None = None,
) -> dict[str, Any]:
    """Build the two paired campaigns without selecting a winning setting."""

    required = {
        "schema_version", "calibration_id", "experiment1_task_source_identity",
        "release_horizons", "finite_battery_candidate_capacities",
        "base_campaign",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise RTA4Core3CalibrationV6Error(
            "calibration config field set mismatch"
        )
    if raw["schema_version"] != CORE3_CALIBRATION_CONFIG_V6:
        raise RTA4Core3CalibrationV6Error("calibration schema version mismatch")
    if (
        type(raw["calibration_id"]) is not str
        or not raw["calibration_id"].strip()
    ):
        raise RTA4Core3CalibrationV6Error("calibration_id is invalid")
    if tuple(raw["release_horizons"]) != CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        raise RTA4Core3CalibrationV6Error(
            "calibration release horizons must be exactly 30000 and 60000"
        )
    capacities = _canonical_capacity_axis(
        raw["finite_battery_candidate_capacities"]
    )
    experiment1_identity = _sha(
        raw["experiment1_task_source_identity"],
        "experiment1_task_source_identity",
    )
    if not isinstance(raw["base_campaign"], Mapping):
        raise RTA4Core3CalibrationV6Error("base_campaign must be a mapping")
    base = deepcopy(dict(raw["base_campaign"]))
    if base.get("core") != "CORE-3":
        raise RTA4Core3CalibrationV6Error("base_campaign must target CORE-3")
    if base.get("core3_campaign_type") != "CALIBRATION":
        raise RTA4Core3CalibrationV6Error(
            "base_campaign must explicitly be a CALIBRATION campaign"
        )
    if base.get("finite_battery_capacities") != capacities:
        raise RTA4Core3CalibrationV6Error(
            "candidate capacity axis differs from base campaign"
        )
    task_source = base.get("task_source")
    parameters = (
        task_source.get("parameters")
        if isinstance(task_source, Mapping) else None
    )
    if (
        not isinstance(parameters, Mapping)
        or task_source.get("mode") != GENERATED_FAMILY
    ):
        raise RTA4Core3CalibrationV6Error(
            "calibration task source must be a generated family"
        )
    base_seed = parameters.get("base_seed")
    taskset_count = parameters.get("taskset_count")
    generation_indices = parameters.get("generation_indices")
    if (
        type(base_seed) is not int
        or type(taskset_count) is not int
        or taskset_count <= 0
        or type(generation_indices) is not list
        or len(generation_indices) != taskset_count
        or len(set(generation_indices)) != taskset_count
        or any(type(index) is not int or index < 0 for index in generation_indices)
    ):
        raise RTA4Core3CalibrationV6Error(
            "calibration seed/generation index binding is invalid"
        )

    campaigns: list[dict[str, Any]] = []
    task_source_identity: str | None = None
    for horizon in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        campaign = deepcopy(base)
        campaign["campaign_id"] = f"{raw['calibration_id']}-hrel-{horizon}"
        campaign["simulation_horizon"] = {
            "release_horizon": horizon,
            "observation_horizon": "release_horizon_plus_dmax",
        }
        normalized = normalize_rta4_campaign_v5(
            campaign, base_directory=base_directory,
        )
        scientific = normalized["normalized_scientific_config"]
        binding = normalized["task_sources"]
        if len(binding) != 1:
            raise RTA4Core3CalibrationV6Error(
                "calibration campaign has no unique task source"
            )
        observed_identity = binding[0].source.identity
        if task_source_identity is None:
            task_source_identity = observed_identity
        elif observed_identity != task_source_identity:
            raise RTA4Core3CalibrationV6Error(
                "paired horizons changed calibration task source"
            )
        campaigns.append({
            "release_horizon": horizon,
            "campaign": campaign,
            "scientific_config_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v5", scientific,
            ),
        })
    assert task_source_identity is not None
    if task_source_identity == experiment1_identity:
        raise RTA4Core3CalibrationV6Error(
            "calibration task source is not independent from experiment one"
        )
    material = {
        "schema_version": CORE3_CALIBRATION_CONFIG_V6,
        "calibration_id": raw["calibration_id"],
        "calibration_task_source_identity": task_source_identity,
        "experiment1_task_source_identity": experiment1_identity,
        "base_seed": base_seed,
        "generation_indices": list(generation_indices),
        "taskset_count": taskset_count,
        "release_horizons": list(CORE3_CALIBRATION_RELEASE_HORIZONS_V6),
        "finite_battery_candidate_capacities": capacities,
        "release_modes": list(base["release_modes"]),
        "campaigns": campaigns,
        "automatic_parameter_selection": False,
    }
    return {
        **material,
        "calibration_manifest_identity": domain_hash(
            CORE3_CALIBRATION_MANIFEST_DOMAIN_V6, material,
        ),
    }


def write_calibration_campaigns_v6(
    config_path: Path | str, output_root: Path | str,
) -> Mapping[str, Any]:
    source = Path(config_path).expanduser().resolve(strict=True)
    try:
        raw = yaml.load(source.read_bytes(), Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4Core3CalibrationV6Error(
            "cannot parse calibration config"
        ) from exc
    manifest = materialize_calibration_campaigns_v6(
        raw, base_directory=source.parent,
    )
    root = Path(output_root).expanduser().resolve(strict=False)
    if root.exists() and any(root.iterdir()):
        raise RTA4Core3CalibrationV6Error(
            "refusing to write into a non-empty calibration output root"
        )
    root.mkdir(parents=True, exist_ok=True)
    campaign_rows = []
    for item in manifest["campaigns"]:
        horizon = item["release_horizon"]
        path = root / f"core3_calibration_hrel_{horizon}_v6.yaml"
        atomic_write_text(
            path,
            yaml.safe_dump(item["campaign"], sort_keys=False),
        )
        campaign_rows.append({
            "release_horizon": horizon,
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "scientific_config_identity": item["scientific_config_identity"],
        })
    persisted = {
        **{key: value for key, value in manifest.items() if key != "campaigns"},
        "campaigns": campaign_rows,
    }
    atomic_write_json(root / "core3_calibration_manifest_v6.json", persisted)
    return persisted


_CALIBRATION_MANIFEST_FIELDS = {
    "schema_version", "calibration_id", "calibration_task_source_identity",
    "experiment1_task_source_identity", "base_seed", "generation_indices",
    "taskset_count", "release_horizons",
    "finite_battery_candidate_capacities", "release_modes", "campaigns",
    "automatic_parameter_selection", "calibration_manifest_identity",
}
_SUMMARY_MATERIAL_FIELDS = {
    "summary_schema_version", "calibration_manifest_identity",
    "campaign_30000_identity", "campaign_60000_identity",
    "candidate_release_horizons", "candidate_battery_capacities",
    "expected_run_count", "actual_run_count", "missing_execution_ids",
    "extra_execution_ids", "invalid_execution_ids",
    "horizon_insufficient_execution_ids", "pair_count",
    "expected_pair_count", "horizon_summary", "capacity_summary",
    "paired_differences", "pairing_complete", "automatic_parameter_selection",
}


def _strict_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise RTA4Core3CalibrationV6Error(
                    f"duplicate JSON key {key!r}: {path}"
                )
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except RTA4Core3CalibrationV6Error:
        raise
    except Exception as exc:
        raise RTA4Core3CalibrationV6Error(f"unreadable JSON: {path}") from exc


def _read_campaign_raw(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.load(path.read_bytes(), Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4Core3CalibrationV6Error(
            f"cannot parse calibration campaign: {path}"
        ) from exc
    if not isinstance(value, Mapping):
        raise RTA4Core3CalibrationV6Error("calibration campaign is not an object")
    return value


def _load_calibration_manifest(
    path: Path,
) -> tuple[Mapping[str, Any], dict[int, Any], dict[int, Mapping[str, Any]]]:
    manifest = _strict_json(path)
    if not isinstance(manifest, Mapping) or set(manifest) != _CALIBRATION_MANIFEST_FIELDS:
        raise RTA4Core3CalibrationV6Error("calibration manifest field set mismatch")
    if (
        manifest["schema_version"] != CORE3_CALIBRATION_CONFIG_V6
        or tuple(manifest["release_horizons"])
        != CORE3_CALIBRATION_RELEASE_HORIZONS_V6
        or manifest["automatic_parameter_selection"] is not False
    ):
        raise RTA4Core3CalibrationV6Error("calibration manifest contract drift")
    _sha(manifest["calibration_manifest_identity"], "calibration manifest identity")
    calibration_source = _sha(
        manifest["calibration_task_source_identity"],
        "calibration task source identity",
    )
    _sha(manifest["experiment1_task_source_identity"], "Experiment-1 task source identity")
    capacities = _canonical_capacity_axis(
        manifest["finite_battery_candidate_capacities"]
    )
    if (
        type(manifest["taskset_count"]) is not int
        or manifest["taskset_count"] <= 0
        or type(manifest["generation_indices"]) is not list
        or len(manifest["generation_indices"]) != manifest["taskset_count"]
        or len(set(manifest["generation_indices"])) != manifest["taskset_count"]
        or type(manifest["release_modes"]) is not list
        or not manifest["release_modes"]
    ):
        raise RTA4Core3CalibrationV6Error("calibration manifest axes drift")
    campaign_rows = manifest["campaigns"]
    if not isinstance(campaign_rows, list) or len(campaign_rows) != 2:
        raise RTA4Core3CalibrationV6Error("calibration manifest must bind two campaigns")
    loaded: dict[int, Any] = {}
    raw_campaigns: dict[int, Mapping[str, Any]] = {}
    reconstructed_rows = []
    root = path.parent.resolve()
    for item in campaign_rows:
        if not isinstance(item, Mapping) or set(item) != {
            "release_horizon", "relative_path", "sha256",
            "scientific_config_identity",
        }:
            raise RTA4Core3CalibrationV6Error("calibration campaign entry drift")
        horizon = item["release_horizon"]
        if horizon not in CORE3_CALIBRATION_RELEASE_HORIZONS_V6 or horizon in loaded:
            raise RTA4Core3CalibrationV6Error("calibration campaign horizon drift")
        relative = item["relative_path"]
        if type(relative) is not str or Path(relative).is_absolute():
            raise RTA4Core3CalibrationV6Error("calibration campaign path is not relative")
        campaign_path = (root / relative).resolve(strict=True)
        try:
            campaign_path.relative_to(root)
        except ValueError as exc:
            raise RTA4Core3CalibrationV6Error(
                "calibration campaign path escapes manifest root"
            ) from exc
        if hashlib.sha256(campaign_path.read_bytes()).hexdigest() != _sha(
            item["sha256"], "campaign file SHA-256",
        ):
            raise RTA4Core3CalibrationV6Error("calibration campaign SHA-256 drift")
        campaign = load_rta4_campaign_v5(campaign_path)
        if campaign.normalized_scientific_config_sha256 != _sha(
            item["scientific_config_identity"], "campaign identity",
        ):
            raise RTA4Core3CalibrationV6Error("calibration campaign identity drift")
        if (
            len(campaign.task_sources) != 1
            or campaign.task_sources[0].source.identity != calibration_source
            or campaign.task_sources[0].source.taskset_count
            != manifest["taskset_count"]
        ):
            raise RTA4Core3CalibrationV6Error("calibration task source binding drift")
        raw = _read_campaign_raw(campaign_path)
        effective_horizon = raw.get("simulation_horizon", {}).get("release_horizon")
        if effective_horizon != horizon:
            raise RTA4Core3CalibrationV6Error("campaign release horizon drift")
        loaded[horizon] = campaign
        raw_campaigns[horizon] = raw
        reconstructed_rows.append({
            "release_horizon": horizon,
            "campaign": raw,
            "scientific_config_identity": item["scientific_config_identity"],
        })
    reconstructed_rows.sort(key=lambda item: item["release_horizon"])
    material = {
        key: manifest[key]
        for key in _CALIBRATION_MANIFEST_FIELDS
        if key not in {"calibration_manifest_identity", "campaigns"}
    }
    material["campaigns"] = reconstructed_rows
    if manifest["calibration_manifest_identity"] != domain_hash(
        CORE3_CALIBRATION_MANIFEST_DOMAIN_V6, material,
    ):
        raise RTA4Core3CalibrationV6Error("calibration manifest identity drift")
    low, high = deepcopy(dict(raw_campaigns[30000])), deepcopy(dict(raw_campaigns[60000]))
    for row in (low, high):
        row.pop("campaign_id", None)
        row["simulation_horizon"] = {
            "release_horizon": "PAIRED_RELEASE_HORIZON",
            "observation_horizon": "release_horizon_plus_dmax",
        }
    if low != high:
        raise RTA4Core3CalibrationV6Error(
            "paired calibration campaigns differ outside the horizon axis"
        )
    low_tasksets = {row.identity for row in loaded[30000].task_sources[0].source.tasksets}
    high_tasksets = {row.identity for row in loaded[60000].task_sources[0].source.tasksets}
    if low_tasksets != high_tasksets:
        raise RTA4Core3CalibrationV6Error("paired calibration taskset identities drift")
    if capacities != list(manifest["finite_battery_candidate_capacities"]):
        raise RTA4Core3CalibrationV6Error("calibration capacity axis drift")
    return manifest, loaded, raw_campaigns


def _manifest_plan_row(record: Any) -> dict[str, Any]:
    return {
        "ordinal": record.ordinal,
        "core": record.core,
        "kind": record.kind,
        "plan_record_identity": record.record_id,
        "mathematical_request_identity": record.mathematical_request_id,
        "execution_identity": record.execution_id,
        "taskset_identity": record.taskset_identity,
        "taskset_content_sha256": record.material["taskset_content_sha256"],
        "task_order_sha256": record.material["task_order_sha256"],
        "configured_service_identity": record.configured_service_identity,
        "effective_service_identity": record.effective_service_identity,
        "simulation_tick_ms": record.material["simulation_tick_ms"],
        "simulation_projection_identity": record.material["service_material"]
        ["simulation_projection"]["simulation_projection_identity"],
        "effective_core3_simulation_material": deepcopy(dict(
            record.material["effective_core3_simulation_material"]
        )),
    }


def _expected_plan(campaign: Any) -> tuple[Mapping[str, Any], tuple[Any, ...], dict[str, dict[str, Any]]]:
    plan = describe_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    )
    records = tuple(iter_formal_plan_v5(
        campaign.normalized_scientific_config,
        campaign.task_sources,
        campaign.service_curve,
    ))
    expected = {}
    prepared_cache: dict[tuple[str, int], tuple[Any, Any]] = {}
    for record in records:
        effective = record.material["effective_core3_simulation_material"]
        cache_key = (record.taskset_identity, int(effective["observation_horizon"]))
        if cache_key not in prepared_cache:
            _worker, _certificate, context, _material = _prepared_record_material(
                campaign, record,
            )
            binding = context.binding_for(record.record_id)
            service = context.service_materials[
                binding["service_material_identity"]
            ]
            prepared_cache[cache_key] = (binding, service)
        binding, service = prepared_cache[cache_key]
        expected[record.execution_id] = {
            "record": record,
            "taskset_identity": record.taskset_identity,
            "track": effective["track"],
            "release_mode": effective["release_mode"],
            "battery_capacity": str(effective["battery_capacity"]),
            "release_horizon": int(effective["release_horizon"]),
            "observation_horizon": int(effective["observation_horizon"]),
            "task_energy_material_identity": binding[
                "task_energy_material_identity"
            ],
            "service_material_identity": binding["service_material_identity"],
            "beta_material_identity": service.beta_material_identity,
        }
    if len(expected) != len(records):
        raise RTA4Core3CalibrationV6Error("calibration plan duplicates execution identity")
    return plan, records, expected


def _validate_run_manifest(
    root: Path, campaign: Any, plan: Mapping[str, Any], records: tuple[Any, ...],
) -> Mapping[str, Any]:
    path = root / "local_run_manifest_v5.json"
    value = _strict_json(path)
    if not isinstance(value, Mapping):
        raise RTA4Core3CalibrationV6Error("local run manifest is not an object")
    unsigned = dict(value)
    observed_identity = unsigned.pop("run_identity", None)
    if observed_identity != domain_hash(RTA4_LOCAL_RUN_DOMAIN_V5, unsigned):
        raise RTA4Core3CalibrationV6Error("calibration local run identity drift")
    common = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_RUN_V5",
        "profile": RTA4_FORMAL_PROFILE_V5,
        "campaign_id": campaign.normalized_scientific_config["campaign_id"],
        "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
        "normalized_scientific_config_sha256": (
            campaign.normalized_scientific_config_sha256
        ),
        "plan_sha256": plan["plan_sha256"],
        "ordered_stream_digest": plan["ordered_stream_digest"],
        "execution_backend": "PHYSICAL_CORE_PROCESS_SLOTS",
        "physical_core_binding_required": True,
    }
    if any(value.get(key) != expected for key, expected in common.items()):
        raise RTA4Core3CalibrationV6Error("calibration run manifest/plan drift")
    rows = value.get("plan_records")
    if not isinstance(rows, list) or len(rows) != len(records):
        raise RTA4Core3CalibrationV6Error("calibration run plan count drift")
    observed = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RTA4Core3CalibrationV6Error("malformed local run plan record")
        execution = row.get("execution_identity")
        if execution in observed:
            raise RTA4Core3CalibrationV6Error("duplicate local run execution identity")
        observed[execution] = row
    expected_rows = {record.execution_id: _manifest_plan_row(record) for record in records}
    if set(observed) != set(expected_rows) or any(
        dict(observed[key]) != expected_rows[key] for key in expected_rows
    ):
        raise RTA4Core3CalibrationV6Error("calibration run plan records drift")
    return value


def _validate_terminal_wrapper(
    path: Path, expected: Mapping[str, Any], run_manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    terminal = _strict_json(path)
    record = expected["record"]
    common = {
        "row_schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V6",
        "profile": RTA4_FORMAL_PROFILE_V5,
        "core": "CORE-3", "kind": "simulation",
        "run_identity": run_manifest["run_identity"],
        "plan_sha256": run_manifest["plan_sha256"],
        "plan_record_identity": record.record_id,
        "mathematical_request_identity": record.mathematical_request_id,
        "execution_identity": record.execution_id,
        "taskset_identity": record.taskset_identity,
        "taskset_content_sha256": record.material["taskset_content_sha256"],
        "task_order_sha256": record.material["task_order_sha256"],
        "configured_service_identity": record.configured_service_identity,
        "effective_service_identity": record.effective_service_identity,
        "worker_backend": run_manifest["execution_backend"],
        "physical_core_binding_required": True,
    }
    if not isinstance(terminal, Mapping) or path.stem != record.execution_id or any(
        terminal.get(key) != value for key, value in common.items()
    ):
        raise RTA4Core3CalibrationV6Error(f"terminal/plan identity drift: {path}")
    unsigned = dict(terminal)
    observed_identity = unsigned.pop("result_identity", None)
    if observed_identity != domain_hash(RTA4_LOCAL_RESULT_DOMAIN_V6, unsigned):
        raise RTA4Core3CalibrationV6Error(f"terminal result identity drift: {path}")
    envelope = terminal.get("result")
    if not isinstance(envelope, Mapping) or envelope.get("status") != "COMPLETED":
        raise RTA4Core3CalibrationV6Error(f"terminal simulation failed or timed out: {path}")
    row, _jobs = load_core3_result_file_v6(path, path.parents[1])
    identity_fields = {
        "taskset_identity": expected["taskset_identity"],
        "track": expected["track"],
        "release_mode": expected["release_mode"],
        "battery_capacity": expected["battery_capacity"],
        "release_horizon": expected["release_horizon"],
        "observation_horizon": expected["observation_horizon"],
        "task_energy_material_identity": expected["task_energy_material_identity"],
        "service_material_identity": expected["service_material_identity"],
        "beta_material_identity": expected["beta_material_identity"],
    }
    if any(row.get(key) != value for key, value in identity_fields.items()):
        raise RTA4Core3CalibrationV6Error(f"CORE-3 result/plan identity drift: {path}")
    enriched = dict(row)
    enriched["runtime_wall_seconds"] = envelope.get("runtime_wall_seconds", 0)
    return enriched


def summarize_calibration_v6(
    calibration_manifest_path: Path | str,
    run_root_30000: Path | str,
    run_root_60000: Path | str,
) -> dict[str, Any]:
    """Compare exact expected plans with both explicit terminal namespaces."""

    manifest_path = Path(calibration_manifest_path).expanduser().resolve(strict=True)
    run_roots = {
        30000: Path(run_root_30000).expanduser().resolve(strict=True),
        60000: Path(run_root_60000).expanduser().resolve(strict=True),
    }
    manifest, campaigns, _raw = _load_calibration_manifest(manifest_path)
    expected_by_horizon = {}
    run_manifests = {}
    for horizon in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        plan, records, expected = _expected_plan(campaigns[horizon])
        if any(row["release_horizon"] != horizon for row in expected.values()):
            raise RTA4Core3CalibrationV6Error("calibration plan horizon drift")
        expected_by_horizon[horizon] = expected
        run_manifests[horizon] = _validate_run_manifest(
            run_roots[horizon], campaigns[horizon], plan, records,
        )

    expected_pairs: dict[tuple[Any, ...], dict[int, str]] = defaultdict(dict)
    for horizon, expected in expected_by_horizon.items():
        for execution, row in expected.items():
            key = (
                row["taskset_identity"], row["track"], row["release_mode"],
                row["battery_capacity"],
            )
            if horizon in expected_pairs[key]:
                raise RTA4Core3CalibrationV6Error("duplicate expected calibration pair")
            expected_pairs[key][horizon] = execution
    if any(set(pair) != {30000, 60000} for pair in expected_pairs.values()):
        raise RTA4Core3CalibrationV6Error("expected calibration horizon pairing drift")

    rows = []
    missing: list[str] = []
    extra: list[str] = []
    invalid: list[str] = []
    insufficient: list[str] = []
    observed_internal: dict[int, set[str]] = defaultdict(set)
    actual_run_count = 0
    valid_by_execution: dict[str, Mapping[str, Any]] = {}
    for horizon in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        terminal_root = run_roots[horizon] / "local_terminal_results_v5"
        if not terminal_root.is_dir():
            raise RTA4Core3CalibrationV6Error(
                f"calibration terminal directory is absent: {terminal_root}"
            )
        paths = sorted(terminal_root.glob("*.json"))
        actual_run_count += len(paths)
        expected = expected_by_horizon[horizon]
        expected_names = {f"{execution}.json" for execution in expected}
        actual_names = {path.name for path in paths}
        missing.extend(
            f"{horizon}:{Path(name).stem}" for name in sorted(expected_names - actual_names)
        )
        extra.extend(
            f"{horizon}:{Path(name).stem}" for name in sorted(actual_names - expected_names)
        )
        for path in paths:
            execution = path.stem
            if execution not in expected:
                try:
                    value = _strict_json(path)
                    internal = value.get("execution_identity") if isinstance(value, Mapping) else None
                    if internal in observed_internal[horizon]:
                        invalid.append(f"{horizon}:{path.name}:duplicate:{internal}")
                    if type(internal) is str:
                        observed_internal[horizon].add(internal)
                except RTA4Core3CalibrationV6Error:
                    invalid.append(f"{horizon}:{path.name}")
                continue
            try:
                row = _validate_terminal_wrapper(
                    path, expected[execution], run_manifests[horizon],
                )
                internal = str(row["execution_identity"])
                if internal in observed_internal[horizon]:
                    raise RTA4Core3CalibrationV6Error(
                        f"duplicate terminal execution identity: {internal}"
                    )
                observed_internal[horizon].add(internal)
                if (
                    row.get("observed_status") == "SIM_HORIZON_INSUFFICIENT"
                    or int(row.get("unfinished_without_miss_count", 0)) != 0
                ):
                    insufficient.append(f"{horizon}:{execution}")
                    continue
                if (
                    row.get("simulation_status") != "COMPLETED"
                    or row.get("observed_status")
                    not in {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
                    or (
                        row.get("track") == "THEOREM_ALIGNED"
                        and row.get("theorem_alignment_valid") is not True
                    )
                ):
                    invalid.append(f"{horizon}:{execution}")
                    continue
                rows.append(row)
                valid_by_execution[execution] = row
            except (OSError, RTA4Core3CalibrationV6Error, RTA4Core3Experiment1AuditV6Error):
                invalid.append(f"{horizon}:{execution}")

    by_horizon: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    by_capacity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    pair_rows: dict[tuple[Any, ...], dict[int, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        horizon = row.get("release_horizon")
        capacity = row.get("battery_capacity")
        key = (
            row.get("taskset_identity"), row.get("track"),
            row.get("release_mode"), capacity,
        )
        pair_rows[key][horizon] = row
        by_horizon[horizon].append(row)
        by_capacity[capacity].append(row)

    def exact(value: Any, label: str) -> Fraction:
        try:
            result = Fraction(str(value))
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4Core3CalibrationV6Error(
                f"calibration {label} is not exact numeric material"
            ) from exc
        return result

    def exact_value(value: Fraction) -> int | str:
        return (
            value.numerator
            if value.denominator == 1 else fraction_text(value)
        )

    def ratio(numerator: Any, denominator: Any) -> dict[str, Any]:
        exact_numerator = exact(numerator, "ratio numerator")
        exact_denominator = exact(denominator, "ratio denominator")
        return {
            "numerator": exact_value(exact_numerator),
            "denominator": exact_value(exact_denominator),
            "display": (
                exact_value(exact_numerator / exact_denominator)
                if exact_denominator else None
            ),
        }

    horizon_summary = []
    for horizon in CORE3_CALIBRATION_RELEASE_HORIZONS_V6:
        group = by_horizon[horizon]
        released = sum(int(row["released_job_count"]) for row in group)
        classified = sum(int(row["classified_job_count"]) for row in group)
        missed = sum(int(row["deadline_miss_job_count"]) for row in group)
        coverage: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in group:
            for item in row["conditional_coverage"]:
                coverage[str(item["exact_e0"])][0] += int(
                    item["coverage_rate_numerator"]
                )
                coverage[str(item["exact_e0"])][1] += int(
                    item["coverage_rate_denominator"]
                )
        horizon_summary.append({
            "release_horizon": horizon,
            "run_count": len(group),
            "released_job_count": released,
            "classified_job_count": classified,
            "unfinished_without_miss_count": sum(
                int(row["unfinished_without_miss_count"]) for row in group
            ),
            "deadline_miss": ratio(missed, released),
            "conditional_coverage": [
                {"exact_e0": exact, **ratio(*coverage[exact])}
                for exact in sorted(coverage, key=Fraction)
            ],
            "wall_seconds": exact_value(sum(
                (
                    exact(row.get("runtime_wall_seconds", 0), "wall time")
                    for row in group
                ),
                Fraction(0),
            )),
            "simulator_timeout_count": sum(
                row.get("simulation_status") == "TIMEOUT" for row in group
            ),
        })

    capacity_summary = []
    for capacity in sorted(by_capacity, key=Fraction):
        group = by_capacity[capacity]
        offered = sum(
            (exact(row["offered_energy_j"], "offered energy") for row in group),
            Fraction(0),
        )
        clipped = sum(
            (exact(row["clipped_energy_j"], "clipped energy") for row in group),
            Fraction(0),
        )
        released = sum(int(row["released_job_count"]) for row in group)
        missed = sum(int(row["deadline_miss_job_count"]) for row in group)
        capacity_summary.append({
            "battery_capacity": capacity,
            "run_count": len(group),
            "offered_energy_j": exact_value(offered),
            "clipped_energy_j": exact_value(clipped),
            "overflow_ratio": ratio(clipped, offered),
            "battery_full_ticks": sum(
                int(row["battery_full_ticks"]) for row in group
            ),
            "deadline_miss": ratio(missed, released),
            "valid_job_count": released,
            "wall_seconds": exact_value(sum(
                (
                    exact(row.get("runtime_wall_seconds", 0), "wall time")
                    for row in group
                ),
                Fraction(0),
            )),
        })

    paired_differences = []
    delta_fields = (
        "released_job_count", "classified_job_count",
        "unfinished_without_miss_count", "deadline_miss_job_count",
        "offered_energy_j", "clipped_energy_j", "battery_full_ticks",
        "runtime_wall_seconds",
    )
    for key in sorted(pair_rows, key=lambda item: tuple(map(str, item))):
        if set(pair_rows[key]) != {30000, 60000}:
            continue
        low, high = pair_rows[key][30000], pair_rows[key][60000]
        paired_differences.append({
            "taskset_identity": key[0], "track": key[1],
            "release_mode": key[2], "battery_capacity": key[3],
            "from_release_horizon": 30000,
            "to_release_horizon": 60000,
            "delta": {
                field: exact_value(
                    exact(high.get(field, 0), field)
                    - exact(low.get(field, 0), field)
                )
                for field in delta_fields
            },
        })
    missing = sorted(set(missing))
    extra = sorted(set(extra))
    invalid = sorted(set(invalid))
    insufficient = sorted(set(insufficient))
    expected_run_count = sum(len(rows) for rows in expected_by_horizon.values())
    pair_count = len(paired_differences)
    expected_pair_count = len(expected_pairs)
    pairing_complete = (
        not missing
        and not extra
        and not invalid
        and not insufficient
        and actual_run_count == expected_run_count
        and len(rows) == expected_run_count
        and pair_count == expected_pair_count
    )
    material = {
        "summary_schema_version": CORE3_CALIBRATION_SUMMARY_SCHEMA_V6,
        "calibration_manifest_identity": manifest[
            "calibration_manifest_identity"
        ],
        "campaign_30000_identity": campaigns[
            30000
        ].normalized_scientific_config_sha256,
        "campaign_60000_identity": campaigns[
            60000
        ].normalized_scientific_config_sha256,
        "candidate_release_horizons": list(
            CORE3_CALIBRATION_RELEASE_HORIZONS_V6
        ),
        "candidate_battery_capacities": list(
            manifest["finite_battery_candidate_capacities"]
        ),
        "expected_run_count": expected_run_count,
        "actual_run_count": actual_run_count,
        "missing_execution_ids": missing,
        "extra_execution_ids": extra,
        "invalid_execution_ids": invalid,
        "horizon_insufficient_execution_ids": insufficient,
        "pair_count": pair_count,
        "expected_pair_count": expected_pair_count,
        "horizon_summary": horizon_summary,
        "capacity_summary": capacity_summary,
        "paired_differences": paired_differences,
        "pairing_complete": pairing_complete,
        "automatic_parameter_selection": False,
    }
    return {
        **material,
        "calibration_summary_identity": domain_hash(
            CORE3_CALIBRATION_SUMMARY_DOMAIN_V6, material,
        ),
    }


def freeze_calibration_v6(
    summary_path: Path | str,
    output_path: Path | str,
    *,
    release_horizon: int,
    b_low: str,
    b_high: str,
) -> Mapping[str, Any]:
    """Bind a human-selected tuple to an immutable calibration summary."""

    source = Path(summary_path).expanduser().resolve(strict=True)
    payload = source.read_bytes()
    summary = _strict_json(source)
    if not isinstance(summary, Mapping) or set(summary) != (
        _SUMMARY_MATERIAL_FIELDS | {"calibration_summary_identity"}
    ):
        raise RTA4Core3CalibrationV6Error(
            "calibration summary field set mismatch"
        )
    if summary["summary_schema_version"] != CORE3_CALIBRATION_SUMMARY_SCHEMA_V6:
        raise RTA4Core3CalibrationV6Error("calibration summary schema drift")
    material_summary = {
        key: summary[key] for key in _SUMMARY_MATERIAL_FIELDS
    }
    if summary["calibration_summary_identity"] != domain_hash(
        CORE3_CALIBRATION_SUMMARY_DOMAIN_V6, material_summary,
    ):
        raise RTA4Core3CalibrationV6Error(
            "calibration summary identity drift"
        )
    _sha(summary["calibration_manifest_identity"], "calibration manifest identity")
    _sha(summary["campaign_30000_identity"], "30000 campaign identity")
    _sha(summary["campaign_60000_identity"], "60000 campaign identity")
    if (
        tuple(summary["candidate_release_horizons"])
        != CORE3_CALIBRATION_RELEASE_HORIZONS_V6
        or summary["automatic_parameter_selection"] is not False
        or type(summary["expected_run_count"]) is not int
        or type(summary["actual_run_count"]) is not int
        or summary["expected_run_count"] != summary["actual_run_count"]
        or any(summary[field] for field in (
            "missing_execution_ids", "extra_execution_ids",
            "invalid_execution_ids", "horizon_insufficient_execution_ids",
        ))
        or type(summary["pair_count"]) is not int
        or type(summary["expected_pair_count"]) is not int
        or summary["pair_count"] != summary["expected_pair_count"]
    ):
        raise RTA4Core3CalibrationV6Error("calibration summary is incomplete")
    if summary["pairing_complete"] is not True:
        raise RTA4Core3CalibrationV6Error("calibration summary is incomplete")
    if release_horizon not in summary["candidate_release_horizons"]:
        raise RTA4Core3CalibrationV6Error("freeze horizon is not a candidate")
    capacities = set(_canonical_capacity_axis(
        summary["candidate_battery_capacities"]
    ))
    if b_low not in capacities or b_high not in capacities:
        raise RTA4Core3CalibrationV6Error("freeze capacity is not a candidate")
    if Fraction(b_low) >= Fraction(b_high):
        raise RTA4Core3CalibrationV6Error("freeze requires B_low < B_high")
    material = {
        "freeze_schema_version": (
            "ASAP_BLOCK_V9_3_RTA4_CORE3_CALIBRATION_FREEZE_V6"
        ),
        "calibration_summary_sha256": hashlib.sha256(payload).hexdigest(),
        "calibration_summary_identity": summary.get(
            "calibration_summary_identity"
        ),
        "release_horizon": release_horizon,
        "b_low": b_low,
        "b_high": b_high,
        "selection_mode": "EXPLICIT_HUMAN_REVIEWED_NO_AUTOMATIC_SELECTION",
    }
    value = {
        **material,
        "calibration_freeze_identity": domain_hash(
            CORE3_CALIBRATION_FREEZE_DOMAIN_V6, material,
        ),
    }
    atomic_write_json(Path(output_path), value)
    return value


__all__ = [
    "CORE3_CALIBRATION_CONFIG_V6",
    "CORE3_CALIBRATION_RELEASE_HORIZONS_V6",
    "RTA4Core3CalibrationV6Error",
    "freeze_calibration_v6",
    "materialize_calibration_campaigns_v6",
    "summarize_calibration_v6",
    "write_calibration_campaigns_v6",
]
