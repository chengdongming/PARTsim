"""Read-only identity pairing of CORE-3 observations with Experiment-1 RTA."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml

from .result_writer import atomic_write_json
from .rta4_formal_config import domain_hash
from .rta4_formal_config_v5 import (
    CORE3_RESULT_DOMAIN_V6,
    CORE3_RESULT_SCHEMA_V6,
    LoadedCampaignV5,
    RTA4_FORMAL_PROFILE_V5,
    normalize_rta4_campaign_v5,
    rta4_formal_config_hash_v5,
)
from .rta4_formal_plan_v5 import describe_formal_plan_v5, iter_formal_plan_v5
from .rta4_local_execution_v5 import (
    RTA4_LOCAL_RESULT_DOMAIN_V5,
    RTA4_LOCAL_RUN_DOMAIN_V5,
)
from .rta4_task_source_v4 import _UniqueKeyLoader
from .simulation_result import CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6


EXPERIMENT1_RTA_RESULT_COUNT_V6 = 22400
EXPERIMENT1_TASKSET_COUNT_V6 = 800
EXPERIMENT1_BATCH_TASKSET_COUNT_V6 = 400
EXPERIMENT1_BATCH_RTA_RESULT_COUNT_V6 = 11200
EXPERIMENT1_METHODS_V6 = (
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
EXPERIMENT1_E0_V6 = ("34", "35", "36", "37", "38", "39", "40")
CORE3_EXPERIMENT1_AUDIT_DOMAIN_V6 = (
    "ASAP_BLOCK:V9.3:RTA4:CORE3_EXPERIMENT1_AUDIT:v6"
)


class RTA4Core3Experiment1AuditV6Error(ValueError):
    """Raised on any incomplete or identity-drifted pairing input."""


_E1_BATCH_LAYOUT = {
    "batch_a": (
        "rta4_e1_wang_confirm_batch_a.yaml",
        "t10_wang_confirm_batch_a.json",
    ),
    "batch_b": (
        "rta4_e1_wang_confirm_batch_b.yaml",
        "t10_wang_confirm_batch_b.json",
    ),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_CORE3_IDENTITY_FIELDS = (
    "result_schema_version", "simulation_status", "observed_status", "track",
    "release_mode", "battery_model", "battery_capacity",
    "physical_initial_energy", "release_horizon", "dmax",
    "observation_horizon", "release_cutoff_enabled",
    "observation_horizon_reached", "released_job_count",
    "completed_job_count", "deadline_miss_job_count", "unfinished_job_count",
    "unfinished_without_miss_count", "classified_job_count",
    "conditional_coverage", "minimum_release_energy_j",
    "maximum_release_energy_j", "mean_release_energy_j", "offered_energy_j",
    "credited_energy_j", "clipped_energy_j", "consumed_energy_j",
    "overflow_energy_j", "overflow_ratio_numerator",
    "overflow_ratio_denominator", "battery_min_j", "battery_max_j",
    "battery_final_j", "battery_empty_ticks", "battery_full_ticks",
    "observed_energy_intervals", "theorem_alignment_valid",
    "theorem_alignment_failure_reason", "job_observations_relative_path",
    "job_observations_sha256", "job_observation_count",
    "job_observations_schema_version", "task_energy_material_identity",
    "service_material_identity", "beta_material_identity",
    "simulation_tick_ms", "simulation_projection_identity",
    "release_projection_identity", "trace_schema_version", "trace_sha256",
)


def _strict_json(path: Path) -> Any:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = {}
        for key, item in pairs:
            if key in value:
                raise RTA4Core3Experiment1AuditV6Error(
                    f"duplicate JSON key {key!r}: {path}"
                )
            value[key] = item
        return value

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
        )
    except RTA4Core3Experiment1AuditV6Error:
        raise
    except Exception as exc:
        raise RTA4Core3Experiment1AuditV6Error(
            f"unreadable JSON input: {path}"
        ) from exc


def _json_files(root: Path) -> Iterable[tuple[Path, Mapping[str, Any]]]:
    """Scan CORE-3 result roots only; Experiment-1 never uses this path."""

    for path in sorted(root.rglob("*.json")):
        value = _strict_json(path)
        if isinstance(value, Mapping):
            yield path, value


def _confirmation_root(root: Path) -> Path:
    if root.name == "03_final_confirmation":
        candidates = [root]
    else:
        candidates = [root / "03_final_confirmation"]
        candidates.extend(
            child / "03_final_confirmation"
            for child in root.iterdir() if child.is_dir()
        )
        candidates = [path for path in candidates if path.is_dir()]
    resolved = {path.resolve() for path in candidates}
    if len(resolved) != 1:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 archive has a missing or ambiguous "
            "03_final_confirmation directory"
        )
    confirmation = resolved.pop()
    for relative in ("configs", "manifests", "outputs/batch_a", "outputs/batch_b"):
        if not (confirmation / relative).is_dir():
            raise RTA4Core3Experiment1AuditV6Error(
                f"Experiment-1 confirmation layout misses {relative}"
            )
    return confirmation


def _campaign_from_archive(
    campaign_path: Path, manifest_path: Path,
) -> LoadedCampaignV5:
    payload = campaign_path.read_bytes()
    try:
        raw = yaml.load(payload, Loader=_UniqueKeyLoader)
    except Exception as exc:
        raise RTA4Core3Experiment1AuditV6Error(
            f"cannot parse Experiment-1 campaign: {campaign_path}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise RTA4Core3Experiment1AuditV6Error(
            f"Experiment-1 campaign is not an object: {campaign_path}"
        )
    relocated = deepcopy(dict(raw))
    task_source = relocated.get("task_source")
    archived_path = (
        task_source.get("manifest_path")
        if isinstance(task_source, Mapping) else None
    )
    if type(archived_path) is not str:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 campaign has no explicit manifest path"
        )
    if Path(archived_path).name != manifest_path.name:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 campaign manifest basename drift"
        )
    relocated["task_source"] = {
        **dict(task_source), "manifest_path": str(manifest_path),
    }
    try:
        normalized = normalize_rta4_campaign_v5(
            relocated, base_directory=campaign_path.parent,
        )
    except Exception as exc:
        raise RTA4Core3Experiment1AuditV6Error(
            f"cannot reconstruct Experiment-1 campaign: {campaign_path}"
        ) from exc
    scientific = normalized["normalized_scientific_config"]
    return LoadedCampaignV5(
        campaign_path,
        hashlib.sha256(payload).hexdigest(),
        scientific,
        rta4_formal_config_hash_v5(scientific),
        normalized["runtime"],
        normalized["v3_scientific_config"],
        normalized["task_sources"],
        normalized["service_curve"],
    )


def _plan_manifest_row(record: Any) -> dict[str, Any]:
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
    }


def _validate_run_manifest(
    path: Path, campaign: LoadedCampaignV5, plan: Mapping[str, Any],
    records: tuple[Any, ...],
) -> Mapping[str, Any]:
    value = _strict_json(path)
    if not isinstance(value, Mapping):
        raise RTA4Core3Experiment1AuditV6Error(
            f"local run manifest is not an object: {path}"
        )
    unsigned = dict(value)
    observed_run_identity = unsigned.pop("run_identity", None)
    if (
        type(observed_run_identity) is not str
        or not _SHA256.fullmatch(observed_run_identity)
        or observed_run_identity != domain_hash(RTA4_LOCAL_RUN_DOMAIN_V5, unsigned)
    ):
        raise RTA4Core3Experiment1AuditV6Error("local run identity drift")
    expected_common = {
        "schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_RUN_V5",
        "profile": RTA4_FORMAL_PROFILE_V5,
        "campaign_id": campaign.normalized_scientific_config["campaign_id"],
        "raw_campaign_file_sha256": campaign.raw_campaign_file_sha256,
        "normalized_scientific_config_sha256": (
            campaign.normalized_scientific_config_sha256
        ),
        "plan_sha256": plan["plan_sha256"],
        "ordered_stream_digest": plan["ordered_stream_digest"],
    }
    if any(value.get(key) != item for key, item in expected_common.items()):
        raise RTA4Core3Experiment1AuditV6Error(
            "reconstructed Experiment-1 plan differs from local run manifest"
        )
    rows = value.get("plan_records")
    if not isinstance(rows, list) or len(rows) != len(records):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 local run plan record count drift"
        )
    expected = {record.execution_id: _plan_manifest_row(record) for record in records}
    if len(expected) != len(records):
        raise RTA4Core3Experiment1AuditV6Error(
            "reconstructed Experiment-1 plan duplicates execution identity"
        )
    observed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 run manifest contains a malformed plan record"
            )
        execution = row.get("execution_identity")
        if execution in observed:
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 run manifest duplicates execution identity"
            )
        observed[str(execution)] = row
    if set(observed) != set(expected):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 run manifest execution set differs from plan"
        )
    for execution, expected_row in expected.items():
        if dict(observed[execution]) != expected_row:
            raise RTA4Core3Experiment1AuditV6Error(
                f"Experiment-1 plan record identity drift: {execution}"
            )
    if (
        value.get("execution_backend") != "PHYSICAL_CORE_PROCESS_SLOTS"
        or value.get("physical_core_binding_required") is not True
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 run did not use required physical-core slots"
        )
    return value


def _normalize_task_results(
    result: Mapping[str, Any], record: Any, expected_order: tuple[str, ...],
) -> list[dict[str, Any]]:
    task_results = result.get("task_results")
    mechanisms = result.get("mechanism_rows")
    taskset_count = len(expected_order)
    if (
        not isinstance(task_results, list)
        or not isinstance(mechanisms, list)
        or len(task_results) != taskset_count
        or len(mechanisms) != taskset_count
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 task/mechanism result count drift"
        )
    ranked: dict[int, Mapping[str, Any]] = {}
    task_ids = set()
    for mechanism in mechanisms:
        if not isinstance(mechanism, Mapping):
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 mechanism row is malformed"
            )
        rank = mechanism.get("priority_rank")
        task_id = mechanism.get("task_id")
        if (
            type(rank) is not int
            or rank in ranked
            or type(task_id) is not str
            or task_id in task_ids
            or mechanism.get("method")
            != record.material["v3_grid_material"]["method"]
        ):
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 mechanism priority/task/method drift"
            )
        ranked[rank] = mechanism
        task_ids.add(task_id)
    if set(ranked) != set(range(taskset_count)):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 mechanism priority ranks are not contiguous"
        )
    if [ranked[index]["task_id"] for index in range(taskset_count)] != list(
        expected_order
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 mechanism task order differs from the formal plan"
        )
    normalized = []
    for rank in range(taskset_count):
        task = task_results[rank]
        if not isinstance(task, Mapping):
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 task result is malformed"
            )
        normalized.append({
            **dict(task),
            "task_id": ranked[rank]["task_id"],
            "priority_rank": rank,
        })
    return normalized


def _load_e1_terminal(
    path: Path, record: Any, run_manifest: Mapping[str, Any],
    expected_task_order: tuple[str, ...],
) -> Mapping[str, Any]:
    terminal = _strict_json(path)
    if not isinstance(terminal, Mapping):
        raise RTA4Core3Experiment1AuditV6Error(
            f"Experiment-1 terminal is not an object: {path}"
        )
    expected = {
        "row_schema": "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V5",
        "profile": RTA4_FORMAL_PROFILE_V5,
        "core": "CORE-1",
        "kind": record.kind,
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
    if path.stem != record.execution_id or any(
        terminal.get(key) != item for key, item in expected.items()
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            f"Experiment-1 terminal differs from its formal plan: {path}"
        )
    unsigned = dict(terminal)
    observed_identity = unsigned.pop("result_identity", None)
    if observed_identity != domain_hash(RTA4_LOCAL_RESULT_DOMAIN_V5, unsigned):
        raise RTA4Core3Experiment1AuditV6Error(
            f"Experiment-1 terminal result identity drift: {path}"
        )
    result = terminal.get("result")
    required = {
        "solver_status", "taskset_proven", "taskset_certification_status",
        "task_energy_material_identity", "service_material_identity",
        "beta_material_identity", "task_results", "mechanism_rows",
    }
    if not isinstance(result, Mapping) or not required.issubset(result):
        raise RTA4Core3Experiment1AuditV6Error(
            f"Experiment-1 terminal nested result is incomplete: {path}"
        )
    if result["solver_status"] not in {"COMPLETED", "NO_CANDIDATE"}:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 contains a final error, timeout, or unknown solver status"
        )
    proven = result["taskset_proven"]
    certification = result["taskset_certification_status"]
    if (
        type(proven) is not bool
        or certification not in {
            "CERTIFIED_TASKSET", "NOT_CERTIFIED",
        }
        or (proven and (
            result["solver_status"] != "COMPLETED"
            or certification != "CERTIFIED_TASKSET"
        ))
        or (not proven and certification == "CERTIFIED_TASKSET")
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 taskset certification/status drift"
        )
    return {
        "taskset_identity": record.taskset_identity,
        "taskset_content_sha256": record.material["taskset_content_sha256"],
        "task_order_sha256": record.material["task_order_sha256"],
        "configured_service_identity": record.configured_service_identity,
        "effective_service_identity": record.effective_service_identity,
        "method": record.material["v3_grid_material"]["method"],
        "exact_e0": str(record.material["v3_grid_material"]["exact_e0"]),
        "execution_identity": record.execution_id,
        **{
            key: result[key]
            for key in (
                "solver_status", "taskset_proven",
                "taskset_certification_status",
                "task_energy_material_identity", "service_material_identity",
                "beta_material_identity",
            )
        },
        "task_results": _normalize_task_results(
            result, record, expected_task_order,
        ),
        "mechanism_rows": result["mechanism_rows"],
    }


def load_experiment1_rta_v6(root: Path | str) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    source = Path(root).expanduser().resolve(strict=True)
    confirmation = _confirmation_root(source)
    rows: list[Mapping[str, Any]] = []
    for batch, (campaign_name, manifest_name) in _E1_BATCH_LAYOUT.items():
        campaign_path = confirmation / "configs" / campaign_name
        manifest_path = confirmation / "manifests" / manifest_name
        run_root = confirmation / "outputs" / batch
        if not campaign_path.is_file() or not manifest_path.is_file():
            raise RTA4Core3Experiment1AuditV6Error(
                f"Experiment-1 confirmation files are incomplete for {batch}"
            )
        campaign = _campaign_from_archive(campaign_path, manifest_path)
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
        if (
            len(records) != EXPERIMENT1_BATCH_RTA_RESULT_COUNT_V6
            or len({record.taskset_identity for record in records})
            != EXPERIMENT1_BATCH_TASKSET_COUNT_V6
        ):
            raise RTA4Core3Experiment1AuditV6Error(
                f"Experiment-1 reconstructed {batch} count drift"
            )
        run_manifest = _validate_run_manifest(
            run_root / "local_run_manifest_v5.json", campaign, plan, records,
        )
        task_orders = {
            taskset.identity: taskset.task_order
            for binding in campaign.task_sources
            for taskset in binding.source.tasksets
        }
        terminal_root = run_root / "local_terminal_results_v5"
        if not terminal_root.is_dir():
            raise RTA4Core3Experiment1AuditV6Error(
                f"Experiment-1 terminal directory is absent for {batch}"
            )
        actual_paths = sorted(terminal_root.glob("*.json"))
        actual_names = {path.name for path in actual_paths}
        expected_names = {f"{record.execution_id}.json" for record in records}
        if len(actual_paths) != len(actual_names) or actual_names != expected_names:
            raise RTA4Core3Experiment1AuditV6Error(
                f"Experiment-1 terminal set differs from {batch} formal plan"
            )
        record_index = {record.execution_id: record for record in records}
        rows.extend(
            _load_e1_terminal(
                path, record_index[path.stem], run_manifest,
                task_orders[record_index[path.stem].taskset_identity],
            )
            for path in actual_paths
        )
    if len(rows) != EXPERIMENT1_RTA_RESULT_COUNT_V6:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 terminal count drift; "
            f"expected {EXPERIMENT1_RTA_RESULT_COUNT_V6}, observed {len(rows)}"
        )
    methods = {row["method"] for row in rows}
    e0_values = {str(row["exact_e0"]) for row in rows}
    tasksets = {row["taskset_identity"] for row in rows}
    if methods != set(EXPERIMENT1_METHODS_V6):
        raise RTA4Core3Experiment1AuditV6Error("Experiment-1 method set drift")
    if e0_values != set(EXPERIMENT1_E0_V6):
        raise RTA4Core3Experiment1AuditV6Error("Experiment-1 E0 set drift")
    if len(tasksets) != EXPERIMENT1_TASKSET_COUNT_V6:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 must contain exactly 800 tasksets"
        )
    index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    identity_fields = (
        "taskset_content_sha256", "task_order_sha256",
        "configured_service_identity", "effective_service_identity",
        "task_energy_material_identity",
    )
    identities_by_taskset: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        status = row["solver_status"]
        if status in {"INTERNAL_ERROR", "TIMEOUT"}:
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 contains a final error/timeout"
            )
        key = (
            str(row["taskset_identity"]), str(row["method"]),
            str(row["exact_e0"]),
        )
        if key in index:
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 contains a duplicate RTA terminal"
            )
        identity = tuple(row[field] for field in identity_fields)
        prior = identities_by_taskset.setdefault(key[0], identity)
        if prior != identity:
            raise RTA4Core3Experiment1AuditV6Error(
                "Experiment-1 identities drift within one taskset"
            )
        index[key] = row
    expected_keys = {
        (taskset, method, e0)
        for taskset in tasksets
        for method in EXPERIMENT1_METHODS_V6
        for e0 in EXPERIMENT1_E0_V6
    }
    if set(index) != expected_keys:
        raise RTA4Core3Experiment1AuditV6Error(
            "Experiment-1 RTA Cartesian product is incomplete"
        )
    return index


def _run_root_for_terminal(path: Path, core3_root: Path) -> Path:
    for parent in path.parents:
        if (parent / "local_run_manifest_v5.json").is_file():
            return parent
        if parent == core3_root:
            break
    return core3_root


def _core3_result_material(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in _CORE3_IDENTITY_FIELDS if field not in row]
    if missing:
        raise RTA4Core3Experiment1AuditV6Error(
            f"CORE-3 terminal misses scientific fields: {missing}"
        )
    return {field: row[field] for field in _CORE3_IDENTITY_FIELDS}


def _load_sidecar(
    terminal_path: Path, row: Mapping[str, Any], core3_root: Path,
) -> list[Mapping[str, Any]]:
    relative = row["job_observations_relative_path"]
    if type(relative) is not str:
        raise RTA4Core3Experiment1AuditV6Error("invalid sidecar path")
    run_root = _run_root_for_terminal(terminal_path, core3_root)
    path = (run_root / relative).resolve(strict=True)
    try:
        path.relative_to(run_root.resolve())
    except ValueError as exc:
        raise RTA4Core3Experiment1AuditV6Error(
            "sidecar escapes its run root"
        ) from exc
    if hashlib.sha256(path.read_bytes()).hexdigest() != row[
        "job_observations_sha256"
    ]:
        raise RTA4Core3Experiment1AuditV6Error("sidecar SHA-256 mismatch")
    value = _strict_json(path)
    if not isinstance(value, Mapping):
        raise RTA4Core3Experiment1AuditV6Error("sidecar is not an object")
    jobs = value.get("job_observations")
    if (
        value.get("job_observations_schema_version")
        != CORE3_JOB_OBSERVATIONS_SCHEMA_VERSION_V6
        or value.get("execution_identity") != row.get("execution_identity")
        or not isinstance(jobs, list)
        or len(jobs) != row["job_observation_count"]
        or value.get("job_observation_count") != len(jobs)
    ):
        raise RTA4Core3Experiment1AuditV6Error("sidecar schema/count mismatch")
    required = {
        "task_id", "task_name", "job_index", "release",
        "absolute_deadline", "completion", "response_time",
        "deadline_miss", "release_energy_j",
        "release_energy_sampling_stage", "executed_ticks",
        "energy_blocked_ticks", "processor_wait_ticks", "censored",
        "censoring_reason",
    }
    identities = set()
    counts = defaultdict(int)
    for job in jobs:
        if not isinstance(job, Mapping) or not required.issubset(job):
            raise RTA4Core3Experiment1AuditV6Error(
                "sidecar job observation is malformed"
            )
        identity = (str(job["task_id"]), job["release"])
        if identity in identities:
            raise RTA4Core3Experiment1AuditV6Error(
                "sidecar contains a duplicate job identity"
            )
        identities.add(identity)
        energy = job["release_energy_j"]
        if (
            isinstance(energy, bool)
            or not isinstance(energy, (int, float))
            or not math.isfinite(float(energy))
            or energy < 0
            or job["release_energy_sampling_stage"]
            != "post_harvest_pre_consumption"
            or type(job["release"]) is not int
            or job["release"] < 0
            or job["release"] >= row["release_horizon"]
            or type(job["absolute_deadline"]) is not int
            or job["absolute_deadline"] > row["observation_horizon"]
            or type(job["deadline_miss"]) is not bool
        ):
            raise RTA4Core3Experiment1AuditV6Error(
                "sidecar job observation violates the CORE-3 contract"
            )
        completed = job["completion"] is not None
        missed = job["deadline_miss"]
        counts["completed_job_count"] += completed
        counts["deadline_miss_job_count"] += missed
        counts["unfinished_job_count"] += not completed
        counts["unfinished_without_miss_count"] += not completed and not missed
        counts["classified_job_count"] += completed or missed
    counts["released_job_count"] = len(jobs)
    if any(row[field] != counts[field] for field in counts):
        raise RTA4Core3Experiment1AuditV6Error(
            "sidecar job classifications disagree with the terminal"
        )
    return jobs


def load_core3_result_file_v6(
    terminal_path: Path | str, run_root: Path | str,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Strictly validate one known CORE-3 terminal and its bound sidecar."""

    path = Path(terminal_path).expanduser().resolve(strict=True)
    source = Path(run_root).expanduser().resolve(strict=True)
    value = _strict_json(path)
    if (
        not isinstance(value, Mapping)
        or value.get("result_schema_version") != CORE3_RESULT_SCHEMA_V6
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            f"not a CORE-3 V6 result terminal: {path}"
        )
    material = _core3_result_material(value)
    if value.get("simulation_result_identity") != domain_hash(
        CORE3_RESULT_DOMAIN_V6, material,
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            "CORE-3 simulation result identity drift"
        )
    if (
        value["observation_horizon"]
        != value["release_horizon"] + value["dmax"]
        or value["released_job_count"] != value["job_observation_count"]
    ):
        raise RTA4Core3Experiment1AuditV6Error(
            "CORE-3 horizon/job count closure drift"
        )
    return value, _load_sidecar(path, value, source)


def load_core3_results_v6(root: Path | str) -> list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]:
    source = Path(root).expanduser().resolve(strict=True)
    results = []
    execution_ids = set()
    for path, value in _json_files(source):
        if value.get("result_schema_version") != CORE3_RESULT_SCHEMA_V6:
            continue
        execution = value.get("execution_identity")
        if execution in execution_ids:
            raise RTA4Core3Experiment1AuditV6Error(
                "duplicate CORE-3 execution terminal"
            )
        execution_ids.add(execution)
        results.append(load_core3_result_file_v6(path, source))
    if not results:
        raise RTA4Core3Experiment1AuditV6Error("no CORE-3 V6 results found")
    return results


def _task_bounds(row: Mapping[str, Any]) -> dict[str, int]:
    if row.get("taskset_proven") is not True:
        return {}
    tasks = row.get("task_results")
    if not isinstance(tasks, list):
        raise RTA4Core3Experiment1AuditV6Error("RTA task_results is malformed")
    bounds = {}
    for task in tasks:
        if not isinstance(task, Mapping) or "task_id" not in task:
            raise RTA4Core3Experiment1AuditV6Error(
                "RTA task result has no explicit task identity"
            )
        task_id = str(task["task_id"])
        if task_id in bounds:
            raise RTA4Core3Experiment1AuditV6Error(
                "RTA task result is duplicated"
            )
        certification = task.get("task_certification_status")
        if certification not in {
            "CERTIFIED", "NOT_CERTIFIED", "TIMEOUT", "ERROR",
        }:
            raise RTA4Core3Experiment1AuditV6Error(
                "RTA task result has an unknown certification status"
            )
        certified = certification == "CERTIFIED"
        if not certified:
            continue
        candidate = task.get("candidate_response_time")
        if isinstance(candidate, bool) or type(candidate) is float:
            raise RTA4Core3Experiment1AuditV6Error(
                "certified task has no exact response bound"
            )
        try:
            exact = Fraction(str(candidate))
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4Core3Experiment1AuditV6Error(
                "certified task has no exact response bound"
            ) from exc
        if exact < 0 or exact.denominator != 1:
            raise RTA4Core3Experiment1AuditV6Error(
                "certified response bound is not an integer tick"
            )
        bounds[task_id] = exact.numerator
    return bounds


def audit_core3_against_experiment1_v6(
    experiment1_root: Path | str,
    core3_root: Path | str,
) -> dict[str, Any]:
    """Pair exclusively by identities and emit complete per-job evidence."""

    rta = load_experiment1_rta_v6(experiment1_root)
    simulations = load_core3_results_v6(core3_root)
    aggregate: dict[tuple[str, str, str, str, str], dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    violations = []
    identity_fields = (
        ("taskset_content_sha256", "taskset_content_sha256"),
        ("task_order_sha256", "task_order_sha256"),
        ("configured_service_identity", "configured_service_identity"),
        ("effective_service_identity", "effective_service_identity"),
        ("task_energy_material_identity", "task_energy_material_identity"),
    )
    for simulation, jobs in simulations:
        taskset = str(simulation.get("taskset_identity"))
        if taskset not in {key[0] for key in rta}:
            raise RTA4Core3Experiment1AuditV6Error(
                "CORE-3 taskset identity is absent from Experiment-1"
            )
        if len(jobs) != simulation["released_job_count"]:
            raise RTA4Core3Experiment1AuditV6Error(
                "CORE-3 sidecar released-job count drift"
            )
        for method in EXPERIMENT1_METHODS_V6:
            for e0 in EXPERIMENT1_E0_V6:
                rta_row = rta[(taskset, method, e0)]
                for sim_field, rta_field in identity_fields:
                    if simulation.get(sim_field) != rta_row.get(rta_field):
                        raise RTA4Core3Experiment1AuditV6Error(
                            f"CORE-3/Experiment-1 identity mismatch: {sim_field}"
                        )
                group_key = (
                    method, e0, str(simulation["track"]),
                    str(simulation["release_mode"]),
                    str(simulation["battery_capacity"]),
                )
                counts = aggregate[group_key]
                if (
                    simulation["track"] == "THEOREM_ALIGNED"
                    and simulation["theorem_alignment_valid"] is not True
                ):
                    counts["theorem_alignment_invalid_runs"] += 1
                    continue
                bounds = _task_bounds(rta_row)
                exact_e0 = Fraction(e0)
                for job in jobs:
                    energy = job.get("release_energy_j")
                    if (
                        isinstance(energy, bool)
                        or not isinstance(energy, (int, float))
                        or not math.isfinite(float(energy))
                    ):
                        raise RTA4Core3Experiment1AuditV6Error(
                            "CORE-3 job has no release-energy observation"
                        )
                    if Fraction(str(energy)) < exact_e0:
                        counts["rta_inapplicable_jobs"] += 1
                        continue
                    counts["covered_jobs"] += 1
                    task_id = str(job.get("task_id"))
                    if task_id not in bounds:
                        counts["rta_not_certified_covered_jobs"] += 1
                        continue
                    counts["rta_certified_covered_jobs"] += 1
                    unclassified = (
                        job.get("completion") is None
                        and job.get("deadline_miss") is not True
                    )
                    if unclassified:
                        counts["unclassified_jobs"] += 1
                    evidence = {
                        "taskset_identity": taskset,
                        "method": method,
                        "exact_e0": e0,
                        "task_id": task_id,
                        "simulation_track": simulation["track"],
                        "release_mode": simulation["release_mode"],
                        "battery_capacity": simulation["battery_capacity"],
                        "job_release": job.get("release"),
                        "release_energy_j": energy,
                        "rta_response_bound": bounds[task_id],
                        "observed_response_time": job.get("response_time"),
                    }
                    if job.get("deadline_miss") is True:
                        counts["deadline_soundness_violations"] += 1
                        violations.append({
                            **evidence,
                            "violation_type": "CERTIFIED_JOB_DEADLINE_MISS",
                        })
                    response = job.get("response_time")
                    if response is not None and int(response) > bounds[task_id]:
                        counts["response_bound_violations"] += 1
                        violations.append({
                            **evidence,
                            "violation_type": "CERTIFIED_RESPONSE_BOUND_EXCEEDED",
                        })
    summary_rows = []
    fields = (
        "covered_jobs", "rta_certified_covered_jobs",
        "rta_not_certified_covered_jobs", "rta_inapplicable_jobs",
        "deadline_soundness_violations", "response_bound_violations",
        "unclassified_jobs", "theorem_alignment_invalid_runs",
    )
    for key in sorted(aggregate):
        counts = aggregate[key]
        summary_rows.append({
            "method": key[0], "exact_e0": key[1],
            "simulation_track": key[2], "release_mode": key[3],
            "battery_capacity": key[4],
            **{field: counts[field] for field in fields},
        })
    material = {
        "audit_schema_version": (
            "ASAP_BLOCK_V9_3_RTA4_CORE3_EXPERIMENT1_AUDIT_V6"
        ),
        "experiment1_rta_terminal_count": len(rta),
        "experiment1_taskset_count": len({key[0] for key in rta}),
        "core3_run_count": len(simulations),
        "summary": summary_rows,
        "violations": violations,
        "violation_count": len(violations),
        "identity_pairing_complete": True,
        "rta_recomputed": False,
    }
    return {
        **material,
        "audit_identity": domain_hash(
            CORE3_EXPERIMENT1_AUDIT_DOMAIN_V6, material,
        ),
    }


def write_core3_experiment1_audit_v6(
    experiment1_root: Path | str,
    core3_root: Path | str,
    output_root: Path | str,
) -> Mapping[str, Any]:
    root = Path(output_root).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    result = audit_core3_against_experiment1_v6(
        experiment1_root, core3_root,
    )
    atomic_write_json(root / "core3_experiment1_audit_v6.json", result)
    return result


__all__ = [
    "EXPERIMENT1_E0_V6", "EXPERIMENT1_METHODS_V6",
    "EXPERIMENT1_RTA_RESULT_COUNT_V6", "EXPERIMENT1_TASKSET_COUNT_V6",
    "RTA4Core3Experiment1AuditV6Error",
    "audit_core3_against_experiment1_v6", "load_core3_result_file_v6",
    "load_core3_results_v6",
    "load_experiment1_rta_v6", "write_core3_experiment1_audit_v6",
]
