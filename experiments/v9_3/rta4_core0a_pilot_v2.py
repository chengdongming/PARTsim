"""Portable input freeze for the RTA4 CORE-0A V2 engineering pilot.

This module selects records only.  It has no execution entry point and cannot
authorize either an engineering pilot or a formal/production run.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, Iterable, Mapping, Sequence

import yaml

from . import exact_energy
from .constrained_taskset_identity import (
    GENERATION_DIMENSIONS_SEED_MODE,
    derive_seed,
)
from .rta4_formal_config import (
    RTA4_CORES,
    RTA4_FORMAL_PROFILE,
    canonical_json,
    domain_hash,
    load_rta4_formal_config,
)
from .rta4_formal_config_v2 import (
    RTA4_FORMAL_PLAN_VERSION_V2,
    RTA4_FORMAL_PROFILE_V2,
    formal_taskset_store_identity_v2,
    load_rta4_formal_config_v2,
    rta4_formal_config_hash_v2,
)
from .rta4_formal_pilot import build_pilot_manifest
from .rta4_formal_execution import RTA4_GENERATION_DOMAIN
from .rta4_formal_plan import FormalPlanRecord, iter_formal_plan
from .rta4_formal_plan_v2 import (
    FormalPlanRecordV2,
    describe_all_formal_plans_v2,
    iter_formal_plan_v2,
)
from .rta4_formal_schema_v2 import formal_schema_hash_v2
from .rta4_formal_schema_v2 import V2_ATTEMPT_REQUIRED_FIELDS
from .rta4_formal_lifecycle_v2 import (
    RTA4_CHECKPOINT_V2,
    RTA4_RESULT_DIRECTORY_V2,
    RTA4_RESULT_DOMAIN_V2,
    RTA4_RESULT_ROW_SCHEMA_V2,
    RTA4_RETRY_RESUME_DOMAIN_V2,
    RTA4_RUN_MANIFEST_V2,
    RTA4_TASKSET_STORE_MANIFEST_V2,
)
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_pilot_execution import (
    PILOT_RESUME_POLICY,
    RTA4_PILOT_EXECUTION_CONFIG_VERSION,
)
from .rta4_production_build_manifest import (
    DEFAULT_RELEVANT_SOURCES,
    ENVIRONMENT_ALLOWLIST,
    PRODUCTION_BUILD_MANIFEST_DOMAIN,
    PRODUCTION_BUILD_MANIFEST_SCHEMA,
    load_and_validate_production_build_manifest,
)
from .rta4_shared_energy import (
    BETA_CONTRACT_VERSION,
    HORIZON_CONTRACT_VERSION,
    SERVICE_MATERIAL_DOMAIN,
    SERVICE_MATERIAL_SCHEMA,
    SERVICE_SPEC_DOMAIN,
    TASK_ENERGY_MATERIAL_DOMAIN,
    TASK_ENERGY_MATERIAL_SCHEMA,
)
from .rta4_taskset_v2 import (
    RTA4_GENERATION_DOMAIN_V2,
    RTA4_TASKSET_GENERATOR_CONTRACT_V2,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CORE0A_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_V2"
)
CORE0A_SELECTION_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_SELECTION_V2"
)
CORE0A_SELECTION_ALGORITHM = (
    "V1_DOMAIN_HASH_LOWEST_PORTED_BY_VERSION_NEUTRAL_ORDINAL_V1"
)
CORE0A_SELECTION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:ENGINEERING_PILOT_SELECTION:v2"
)
CORE0A_MATHEMATICAL_CELL_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:MATHEMATICAL_CELL:v2"
)
CORE0A_PORTABLE_SERVICE_SPEC_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:PORTABLE_SERVICE_SPEC:v2"
)
CORE0A_PORTABLE_BUNDLE_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_PORTABLE_CANDIDATE_BUNDLE_V3"
)
CORE0A_PORTABLE_BUNDLE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:PORTABLE_CANDIDATE_BUNDLE:v3"
)
CORE0A_PORTABLE_CONTRACT_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_PORTABLE_V3"
)
CORE0A_HANDOFF_SCHEMA = "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTODL_HANDOFF_V3"
CORE0A_HANDOFF_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTODL_HANDOFF:v3"
CORE0A_DEPLOYMENT_MANIFEST_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTODL_DEPLOYMENT_MANIFEST_V2"
)
CORE0A_DEPLOYMENT_MANIFEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTODL_DEPLOYMENT_MANIFEST:v2"
)
CORE0A_EXECUTION_IDENTITY_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:EXECUTION_IDENTITY:v2"
)
CORE0A_SCIENTIFIC_ANALYSIS_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:SCIENTIFIC_ANALYSIS:v1"
)
CORE0A_TERMINAL_EVIDENCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:TERMINAL_EVIDENCE:v1"
)
CORE0A_AUTHORIZATION_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_AUTHORIZATION_V2"
)
CORE0A_AUTHORIZATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:ENGINEERING_PILOT_AUTHORIZATION:v2"
)
CORE0A_SEED_MIGRATION_MODE = (
    "ORDINALS_AND_VERSION_NEUTRAL_AXES_PRESERVED_V2_NATIVE_SEEDS_REISSUED"
)
CORE0A_V2_NATIVE_SEED_CONTRACT = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_V2_NATIVE_GENERATION_SEED_V1"
)
CORE0A_DEPLOYMENT_SCOPE_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_DERIVED_DEPLOYMENT_SCOPE_V1"
)
CORE0A_RESOURCE_POLICY_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTODL_RESOURCES_V1"
)
CORE0A_DISK_ESTIMATE_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_DISK_ESTIMATE_V1"
)
CORE0A_DISK_SAFETY_MARGIN_VERSION = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_DISK_SAFETY_MARGIN_V1"
)
CORE0A_DEPLOYMENT_WORKSPACE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:DEPLOYMENT_WORKSPACE:v1"
)
CORE0A_RESOURCE_OBSERVATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:RESOURCE_OBSERVATION:v1"
)
CORE0A_DISK_ESTIMATE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:DISK_ESTIMATE:v1"
)
CORE0A_V2_TASKSET_SOURCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:V2_TASKSET_SOURCE_BINDING:v1"
)
CORE0A_CANDIDATE_CONFIG_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:CANDIDATE_CONFIG:v3"
)
CORE0A_TIMEOUT_RESOURCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:TIMEOUT_RESOURCE:v2"
)

UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE = (
    "UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE"
)
CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT = (
    "CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT"
)
AUTHORIZED_CORE0A_ENGINEERING_PILOT = (
    "AUTHORIZED_CORE0A_ENGINEERING_PILOT"
)

EXPECTED_EXECUTION_COUNT = 384
HISTORICAL_SELECTION_SEED = (
    "ASAP-BLOCK-V9.3-RTA4-CORE0A-ENGINEERING-PILOT-V1"
)
HISTORICAL_CORE_RECORD_COUNTS = {core: 64 for core in RTA4_CORES}
HISTORICAL_SELECTION_SOURCE_SHA256 = (
    "eca6c0024fbb00b6de20317e6436b5e9e3fe43ca96dd56dc33fd66036e95e692"
)
HISTORICAL_ORDERED_SELECTION_IDENTITY = (
    "63bd0bb8a5e3fdcc5c0eef35f5fc2189c37b3da36f1e7e3fc12ea817908b97e9"
)

# This is the accepted G2 scientific-input commit/tree.  Pilot-only commits are
# deliberately layered above it and must not mutate its formal inputs.
FORMAL_INPUT_SOURCE_COMMIT = "c6d3a5f2741c5a75ffeeac66c70feb61c04b9830"
FORMAL_INPUT_SOURCE_TREE = "ab095808ace18784c2408323c4ab17034d91c1d1"

V1_CONFIG_PATHS = {
    core: (
        "configs/v9_3_rta4_"
        f"{core.lower().replace('-', '')}_unauthorized_pre_pilot_v1.yaml"
    )
    for core in RTA4_CORES
}
V2_CONFIG_PATHS = {
    core: (
        "configs/v9_3_rta4_"
        f"{core.lower().replace('-', '')}_unauthorized_pre_pilot_v2_shared_energy.yaml"
    )
    for core in RTA4_CORES
}
SELECTION_ARTIFACT_PATH = "configs/v9_3_rta4_core0a_selection_v2.json"
CANDIDATE_CONFIG_PATH = (
    "configs/v9_3_rta4_core0a_engineering_pilot_v2.yaml"
)
CORE0A_OUTPUT_NAMESPACE = "results/v9_3_rta4_core0a_engineering_pilot_v2"
CORE0A_TASKSET_STORE_NAMESPACE = (
    "results/v9_3_rta4_core0a_tasksets_v2_shared_energy"
)
CORE0A_TERMINAL_DIRECTORY = RTA4_RESULT_DIRECTORY_V2
CORE0A_MEMORY_SOFT_LIMIT_FRACTION = "7/10"
CORE0A_CHECKPOINT_FREQUENCY = 8
CORE0A_DISK_MINIMUM_SAFETY_MARGIN_BYTES = 1 << 30
CORE0A_DISK_BYTES_PER_EXECUTION = 16 << 20
CORE0A_DISK_BYTES_PER_UNIQUE_TASKSET = 8 << 20
CORE0A_DISK_FIXED_OVERHEAD_BYTES = 1 << 30
CORE0A_DISK_OBSERVATION_TOLERANCE_BYTES = 64 << 20
CORE0A_MAX_RUNS = 1
CORE0A_AUTHORIZATION_SCOPE = "EXACT_384_RECORD_CORE0A_ONLY"
EXPECTED_PRODUCTION_SOURCE_CLOSURE_COUNT = 53
PORTABLE_FREEZE_SOURCE_PATHS = (
    "experiments/v9_3/rta4_core0a_pilot_v2.py",
    "scripts/build_v9_3_rta4_core0a_pilot_bundle.py",
    CANDIDATE_CONFIG_PATH,
    SELECTION_ARTIFACT_PATH,
)
ALLOWED_PILOT_FREEZE_DIFF_PATHS = frozenset({
    *PORTABLE_FREEZE_SOURCE_PATHS,
    "test/test_v9_3_rta4_core0a_pilot_freeze_v2.py",
})

CORE0A_RETRY_CONTRACT = {
    "rta_methods": {
        "initial_timeout_seconds": 300,
        "retry_timeout_seconds": 300,
        "maximum_attempts": 2,
        "retry_condition": "TIMEOUT_ONLY",
    },
    "core3_simulation": {
        "initial_timeout_seconds": 300,
        "retry_timeout_seconds": None,
        "maximum_attempts": 1,
        "retry_condition": "NO_RETRY_IN_CURRENT_V2_RUNNER",
    },
    "attempt_indexing": "STRICT_ZERO_BASED",
    "attempt_fields": list(V2_ATTEMPT_REQUIRED_FIELDS),
}

CORE0A_DEPLOYMENT_POLICY = {
    "worker_count": "min(4, logical_cpu_count)",
    "max_in_flight": (
        "min(max(worker_count, 2 * worker_count), logical_cpu_count)"
    ),
    "memory_soft_limit_bytes": "floor(physical_memory_bytes * 0.70)",
    "checkpoint_interval_records": 8,
    "resume_policy": PILOT_RESUME_POLICY,
    "output_namespace": CORE0A_OUTPUT_NAMESPACE,
    "taskset_store_namespace": CORE0A_TASKSET_STORE_NAMESPACE,
    "disk_preflight": "REQUIRE_ESTIMATE_AND_FREE_SPACE_MARGIN",
}

CORE0A_VERSION_NEUTRAL_MIGRATION_FIELDS = (
    "core", "ordinal", "kind", "taskset_slot", "taskset_skeleton_slot",
    "formal_master_seed", "utilization", "method", "exact_e0",
    "service_sensitivity", "time_scale", "track", "replica",
    "core5b_mathematical_group_membership",
)

FORBIDDEN_FORMAL_OUTPUT_NAMESPACES = tuple(
    f"results/v9_3_rta4_{core.lower().replace('-', '')}_formal_{suffix}"
    for core in RTA4_CORES
    for suffix in ("v1", "v2_shared_energy")
)
FORBIDDEN_FORMAL_STORE_NAMESPACES = (
    "results/v9_3_rta4_formal_tasksets_v1",
    "results/v9_3_rta4_formal_tasksets_v2_shared_energy",
)


class RTA4Core0APilotV2Error(ValueError):
    """Raised when the engineering-pilot input freeze is ambiguous or drifts."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (canonical_json(dict(value)) + "\n").encode("utf-8")


def load_strict_canonical_json(path: Path | str) -> Dict[str, Any]:
    source = Path(path)

    def unique(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RTA4Core0APilotV2Error(
                    f"duplicate JSON key in {source}: {key}"
                )
            result[key] = value
        return result

    try:
        payload = source.read_bytes()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except RTA4Core0APilotV2Error:
        raise
    except Exception as exc:
        raise RTA4Core0APilotV2Error(
            f"cannot load strict CORE-0A JSON: {source}"
        ) from exc
    if type(value) is not dict or payload != canonical_json_bytes(value):
        raise RTA4Core0APilotV2Error(
            f"CORE-0A JSON is not canonical: {source}"
        )
    return value


def write_canonical_json(path: Path | str, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(value))


def _load_configs(*, version: int) -> Dict[str, Dict[str, Any]]:
    if version == 1:
        paths = V1_CONFIG_PATHS
        loader = load_rta4_formal_config
    elif version == 2:
        paths = V2_CONFIG_PATHS
        loader = load_rta4_formal_config_v2
    else:
        raise RTA4Core0APilotV2Error("unknown RTA4 formal config version")
    return {
        core: loader(PROJECT_ROOT / paths[core], expected_core=core)
        for core in RTA4_CORES
    }


@lru_cache(maxsize=1)
def _historical_ordered_rows() -> tuple[Dict[str, Any], ...]:
    source = PROJECT_ROOT / "experiments/v9_3/rta4_formal_pilot.py"
    if _sha256(source) != HISTORICAL_SELECTION_SOURCE_SHA256:
        raise RTA4Core0APilotV2Error(
            "historical pilot selection source SHA drift"
        )
    manifest = build_pilot_manifest(
        _load_configs(version=1),
        core_record_counts=HISTORICAL_CORE_RECORD_COUNTS,
        selection_seed=HISTORICAL_SELECTION_SEED,
        output_root="/PORTABLE/CORE0A/V1/OUTPUT",
        taskset_store="/PORTABLE/CORE0A/V1/TASKSET_STORE",
    )
    rows = tuple(
        {**dict(row), "core": core}
        for core in RTA4_CORES
        for row in manifest["selected_records"][core]
    )
    observed = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
    if (
        len(rows) != EXPECTED_EXECUTION_COUNT
        or observed != HISTORICAL_ORDERED_SELECTION_IDENTITY
    ):
        raise RTA4Core0APilotV2Error(
            "historical 384-record selection cannot be reconstructed"
        )
    return rows


def _selected_v2_records(
    configs: Mapping[str, Mapping[str, Any]],
) -> tuple[FormalPlanRecordV2, ...]:
    historical = _historical_ordered_rows()
    ordinals = {
        core: tuple(
            int(row["ordinal"]) for row in historical if row["core"] == core
        )
        for core in RTA4_CORES
    }
    selected = []
    for core in RTA4_CORES:
        wanted = set(ordinals[core])
        rows = tuple(
            record for record in iter_formal_plan_v2(configs[core])
            if record.ordinal in wanted
        )
        if (
            len(rows) != HISTORICAL_CORE_RECORD_COUNTS[core]
            or tuple(record.ordinal for record in rows) != ordinals[core]
        ):
            raise RTA4Core0APilotV2Error(
                f"historical selection is incompatible with {core} V2 plan"
            )
        selected.extend(rows)
    return tuple(selected)


def _selected_v1_records(
    configs: Mapping[str, Mapping[str, Any]],
) -> tuple[FormalPlanRecord, ...]:
    historical = _historical_ordered_rows()
    ordinals = {
        core: tuple(
            int(row["ordinal"]) for row in historical if row["core"] == core
        )
        for core in RTA4_CORES
    }
    selected = []
    for core in RTA4_CORES:
        wanted = set(ordinals[core])
        rows = tuple(
            record for record in iter_formal_plan(configs[core])
            if record.ordinal in wanted
        )
        if (
            len(rows) != HISTORICAL_CORE_RECORD_COUNTS[core]
            or tuple(record.ordinal for record in rows) != ordinals[core]
        ):
            raise RTA4Core0APilotV2Error(
                f"historical selection is incompatible with {core} V1 plan"
            )
        selected.extend(rows)
    return tuple(selected)


def _taskset_seed(record: FormalPlanRecordV2, config: Mapping[str, Any]) -> int:
    generation_id = domain_hash(RTA4_GENERATION_DOMAIN_V2, {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
    })
    return derive_seed(
        int(config["generation"]["formal_master_seed"]),
        generation_id,
        int(record.material.get("replicate_index", 0)),
        seed_mode=GENERATION_DIMENSIONS_SEED_MODE,
    )


def _v1_taskset_seed(
    record: FormalPlanRecord, config: Mapping[str, Any],
) -> int:
    generation_id = domain_hash(RTA4_GENERATION_DOMAIN, {
        "profile": RTA4_FORMAL_PROFILE,
        "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
    })
    return derive_seed(
        int(config["generation"]["formal_master_seed"]),
        generation_id,
        int(record.material.get("replicate_index", 0)),
        seed_mode=GENERATION_DIMENSIONS_SEED_MODE,
    )


def _method(record: FormalPlanRecord | FormalPlanRecordV2) -> str:
    return str(
        record.material.get(
            "method",
            "CORE3_SIMULATION_V2" if record.kind == "simulation" else "NA",
        )
    )


def _e0(record: FormalPlanRecord | FormalPlanRecordV2) -> str:
    return str(record.material.get(
        "exact_e0", record.material.get("physical_initial_energy", "NA"),
    ))


def _time_scale(record: FormalPlanRecord | FormalPlanRecordV2) -> str:
    if record.material.get("axis") == "integer_time_scale":
        return str(record.material["axis_value"])
    return "1"


def _track(record: FormalPlanRecord | FormalPlanRecordV2) -> str:
    if record.kind == "simulation":
        return (
            f"{record.material['applicability_track']}:"
            f"{record.material['release_mode']}"
        )
    return str(record.material.get("scenario", "NA"))


def _execution_replica(
    record: FormalPlanRecord | FormalPlanRecordV2,
) -> str:
    if record.kind == "worker_execution":
        return str(record.material["worker_count"])
    return "PRIMARY"


def _service_spec_identity(record: FormalPlanRecordV2) -> str:
    material = {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "service_spec_domain": SERVICE_SPEC_DOMAIN,
        "service_scale": str(record.material.get("service_scale", "1")),
        "consumer": (
            "CORE3_OBSERVATION_HORIZON"
            if record.kind == "simulation" else "RTA_QUERY_HORIZON"
        ),
        "horizon_contract_version": HORIZON_CONTRACT_VERSION,
        "beta_contract_version": BETA_CONTRACT_VERSION,
    }
    return domain_hash(CORE0A_PORTABLE_SERVICE_SPEC_DOMAIN, material)


def _mathematical_cell_identity(record: FormalPlanRecordV2) -> str:
    if record.mathematical_request_id is not None:
        return str(record.mathematical_request_id)
    return domain_hash(CORE0A_MATHEMATICAL_CELL_DOMAIN, {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "core": record.core,
        "kind": record.kind,
        "taskset_slot": record.taskset_slot_id,
        "utilization": str(record.material.get("normalized_utilization", "NA")),
        "method": _method(record),
        "exact_e0": _e0(record),
        "service_scale": str(record.material.get("service_scale", "1")),
        "time_scale": _time_scale(record),
        "track": _track(record),
    })


def _selection_row(
    record: FormalPlanRecordV2, config: Mapping[str, Any],
) -> Dict[str, Any]:
    if record.execution_id is None or record.taskset_slot_id is None:
        raise RTA4Core0APilotV2Error("selected V2 record lacks execution inputs")
    return {
        "core": record.core,
        "ordinal": record.ordinal,
        "kind": record.kind,
        "plan_record_identity": record.record_id,
        "mathematical_cell_identity": _mathematical_cell_identity(record),
        "execution_identity": record.execution_id,
        "taskset_slot": record.taskset_slot_id,
        "taskset_skeleton_slot": record.taskset_skeleton_slot_id,
        "seed": _taskset_seed(record, config),
        "formal_master_seed": int(config["generation"]["formal_master_seed"]),
        "taskset_replicate_index": int(
            record.material.get("replicate_index", 0)
        ),
        "utilization": str(record.material.get("normalized_utilization", "NA")),
        "method": _method(record),
        "exact_e0": _e0(record),
        "service_sensitivity": str(record.material.get("service_scale", "1")),
        "service_spec_identity": _service_spec_identity(record),
        "time_scale": _time_scale(record),
        "track": _track(record),
        "replica": _execution_replica(record),
    }


def _count(rows: Iterable[Mapping[str, Any]], field: str) -> Dict[str, int]:
    counts = Counter(str(row[field]) for row in rows)
    return {key: counts[key] for key in sorted(counts)}


def coverage_matrix(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "by_core": _count(records, "core"),
        "by_method": _count(records, "method"),
        "by_e0": _count(records, "exact_e0"),
        "by_utilization_stratum": _count(records, "utilization"),
        "by_service_scale": _count(records, "service_sensitivity"),
        "by_time_scale": _count(records, "time_scale"),
        "by_track": _count(records, "track"),
        "by_replica": _count(records, "replica"),
        "unique_taskset_slot_count": len({
            str(row["taskset_slot"]) for row in records
        }),
        "unique_service_spec_count": len({
            str(row["service_spec_identity"]) for row in records
        }),
    }


def _validate_core5b_groups(records: Sequence[Mapping[str, Any]]) -> None:
    rows = [row for row in records if row["core"] == "CORE-5B"]
    if len(rows) != 64:
        raise RTA4Core0APilotV2Error("CORE-5B selection count drift")
    for offset in range(0, 64, 4):
        group = rows[offset:offset + 4]
        if (
            [row["replica"] for row in group] != ["1", "2", "4", "8"]
            or len({row["mathematical_cell_identity"] for row in group}) != 1
            or len({row["taskset_slot"] for row in group}) != 1
            or [row["ordinal"] for row in group]
            != list(range(int(group[0]["ordinal"]), int(group[0]["ordinal"]) + 4))
        ):
            raise RTA4Core0APilotV2Error(
                "CORE-5B selection breaks the complete 1/2/4/8 group contract"
            )


def _contains_v1_identity(value: Any) -> bool:
    if isinstance(value, str):
        return (
            value == "ASAP_BLOCK_V9_3_RTA4_FORMAL_V1"
            or "FORMAL_PLAN_V1" in value
            or "FORMAL_SCHEMA_V1" in value
        )
    if isinstance(value, Mapping):
        return any(_contains_v1_identity(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_v1_identity(item) for item in value)
    return False


@lru_cache(maxsize=1)
def build_core0a_selection_v2() -> Dict[str, Any]:
    configs = _load_configs(version=2)
    plans = describe_all_formal_plans_v2(configs)
    selected = _selected_v2_records(configs)
    records = tuple(_selection_row(record, configs[record.core]) for record in selected)
    coverage = coverage_matrix(records)
    _validate_core5b_groups(records)
    config_rows = {
        core: {
            "path": V2_CONFIG_PATHS[core],
            "file_sha256": _sha256(PROJECT_ROOT / V2_CONFIG_PATHS[core]),
            "semantic_identity": rta4_formal_config_hash_v2(configs[core]),
            "parameter_status": configs[core]["experiment_contract"][
                "parameter_status"
            ],
        }
        for core in RTA4_CORES
    }
    material: Dict[str, Any] = {
        "selection_schema": CORE0A_SELECTION_SCHEMA,
        "contract_version": CORE0A_CONTRACT_VERSION,
        "status": UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE,
        "formal_authorization": False,
        "production_authorization": False,
        "profile": RTA4_FORMAL_PROFILE_V2,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V2,
        "formal_schema_sha256": formal_schema_hash_v2(),
        "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
        "formal_input_source": {
            "git_commit": FORMAL_INPUT_SOURCE_COMMIT,
            "git_tree": FORMAL_INPUT_SOURCE_TREE,
            "clean_state_required": True,
        },
        "selection_algorithm": {
            "version": CORE0A_SELECTION_ALGORITHM,
            "rule": "PORT_HISTORICAL_V1_HASH_SELECTION_BY_STABLE_GRID_ORDINAL",
            "result_independent": True,
            "order": "RTA4_CORES_THEN_ASCENDING_PLAN_ORDINAL",
            "core5b_unit": "COMPLETE_MATHEMATICAL_REQUEST_GROUP",
        },
        "historical_selection_contract": {
            "selection_seed": HISTORICAL_SELECTION_SEED,
            "core_execution_counts": dict(HISTORICAL_CORE_RECORD_COUNTS),
            "selection_source_sha256": HISTORICAL_SELECTION_SOURCE_SHA256,
            "ordered_selection_identity": (
                HISTORICAL_ORDERED_SELECTION_IDENTITY
            ),
            "identity_migration": (
                "ORDINALS_ONLY_V1_IDENTITIES_DISCARDED_V2_IDENTITIES_REISSUED"
            ),
        },
        "v2_plans": {
            "all_plan_digest": plans["all_plan_digest"],
            "plans": {
                core: {
                    "plan_sha256": plans["plans"][core]["plan_sha256"],
                    "ordered_stream_count": plans["plans"][core][
                        "ordered_stream_count"
                    ],
                    "ordered_stream_digest": plans["plans"][core][
                        "ordered_stream_digest"
                    ],
                }
                for core in RTA4_CORES
            },
        },
        "v2_configs": config_rows,
        "expected_execution_count": EXPECTED_EXECUTION_COUNT,
        "coverage_matrix": coverage,
        "ordered_records": list(records),
    }
    if _contains_v1_identity(material["ordered_records"]):
        raise RTA4Core0APilotV2Error("V1 identity leaked into V2 selection")
    return {
        **material,
        "core0a_selection_identity": domain_hash(
            CORE0A_SELECTION_DOMAIN, material,
        ),
    }


def validate_core0a_selection_v2(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4Core0APilotV2Error("CORE-0A selection must be a mapping")
    document = dict(value)
    records = document.get("ordered_records")
    if type(records) is not list:
        raise RTA4Core0APilotV2Error("CORE-0A ordered records must be a list")
    if len(records) != EXPECTED_EXECUTION_COUNT:
        raise RTA4Core0APilotV2Error("CORE-0A selection count is not 384")
    for field in (
        "plan_record_identity", "execution_identity",
    ):
        values = [row.get(field) for row in records if isinstance(row, Mapping)]
        if len(values) != len(set(values)):
            raise RTA4Core0APilotV2Error(
                f"duplicate CORE-0A {field}"
            )
    if coverage_matrix(records) != document.get("coverage_matrix"):
        raise RTA4Core0APilotV2Error("CORE-0A coverage matrix mismatch")
    _validate_core5b_groups(records)
    if _contains_v1_identity(records):
        raise RTA4Core0APilotV2Error("V1 identity leaked into V2 selection")
    unsigned = dict(document)
    observed = unsigned.pop("core0a_selection_identity", None)
    if observed != domain_hash(CORE0A_SELECTION_DOMAIN, unsigned):
        raise RTA4Core0APilotV2Error("CORE-0A selection identity mismatch")
    expected = build_core0a_selection_v2()
    if document != expected:
        raise RTA4Core0APilotV2Error(
            "CORE-0A selection does not match the current V2 plans"
        )
    return document


def load_core0a_selection_v2(
    path: Path | str = PROJECT_ROOT / SELECTION_ARTIFACT_PATH,
) -> Dict[str, Any]:
    return validate_core0a_selection_v2(load_strict_canonical_json(path))


def _version_neutral_migration_rows(
    records: Sequence[FormalPlanRecord | FormalPlanRecordV2],
    configs: Mapping[str, Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    core5b_group = -1
    result = []
    for record in records:
        membership: Dict[str, Any] | None = None
        if record.core == "CORE-5B":
            if _execution_replica(record) == "1":
                core5b_group += 1
            membership = {
                "selected_group_index": core5b_group,
                "taskset_slot": record.taskset_slot_id,
                "replica": _execution_replica(record),
            }
        result.append({
            "core": record.core,
            "ordinal": record.ordinal,
            "kind": record.kind,
            "taskset_slot": record.taskset_slot_id,
            "taskset_skeleton_slot": record.taskset_skeleton_slot_id,
            "formal_master_seed": int(
                configs[record.core]["generation"]["formal_master_seed"]
            ),
            "utilization": str(
                record.material.get("normalized_utilization", "NA")
            ),
            "method": _method(record),
            "exact_e0": _e0(record),
            "service_sensitivity": str(
                record.material.get("service_scale", "1")
            ),
            "time_scale": _time_scale(record),
            "track": _track(record),
            "replica": _execution_replica(record),
            "core5b_mathematical_group_membership": membership,
        })
    return result


def _v2_taskset_source_binding(
    record: FormalPlanRecordV2,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    generation_request_id = domain_hash(RTA4_GENERATION_DOMAIN_V2, {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "taskset_skeleton_slot_id": record.taskset_skeleton_slot_id,
    })
    material = {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "generator_contract": RTA4_TASKSET_GENERATOR_CONTRACT_V2,
        "taskset_store_identity": formal_taskset_store_identity_v2(),
        "taskset_slot": record.taskset_slot_id,
        "taskset_skeleton_slot": record.taskset_skeleton_slot_id,
        "generation_request_id": generation_request_id,
        "formal_master_seed": int(
            config["generation"]["formal_master_seed"]
        ),
        "replicate_index": int(record.material.get("replicate_index", 0)),
        "derived_generation_seed": _taskset_seed(record, config),
    }
    return {
        **material,
        "taskset_source_binding_identity": domain_hash(
            CORE0A_V2_TASKSET_SOURCE_DOMAIN, material,
        ),
    }


@lru_cache(maxsize=1)
def build_seed_migration_contract_v2() -> Dict[str, Any]:
    selection = build_core0a_selection_v2()
    v1_configs = _load_configs(version=1)
    v2_configs = _load_configs(version=2)
    v1_records = _selected_v1_records(v1_configs)
    v2_records = _selected_v2_records(v2_configs)
    v1_axes = _version_neutral_migration_rows(v1_records, v1_configs)
    v2_axes = _version_neutral_migration_rows(v2_records, v2_configs)
    if v1_axes != v2_axes or len(v2_axes) != EXPECTED_EXECUTION_COUNT:
        raise RTA4Core0APilotV2Error(
            "V1/V2 version-neutral CORE-0A migration axes drift"
        )
    selected_rows = selection["ordered_records"]
    v1_seeds = [
        _v1_taskset_seed(record, v1_configs[record.core])
        for record in v1_records
    ]
    v2_seeds = [
        _taskset_seed(record, v2_configs[record.core])
        for record in v2_records
    ]
    if v2_seeds != [int(row["seed"]) for row in selected_rows]:
        raise RTA4Core0APilotV2Error(
            "CORE-0A selection does not carry canonical V2 native seeds"
        )
    source_bindings = [
        _v2_taskset_source_binding(record, v2_configs[record.core])
        for record in v2_records
    ]
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for binding in source_bindings:
        groups.setdefault(str(binding["taskset_slot"]), []).append(binding)
    for slot, rows in groups.items():
        if len({
            row["taskset_source_binding_identity"] for row in rows
        }) != 1:
            raise RTA4Core0APilotV2Error(
                f"V2 paired taskset source drift for slot {slot}"
            )
    core5b_rows = [
        row for row in v2_axes if row["core"] == "CORE-5B"
    ]
    if (
        len(core5b_rows) != 64
        or {
            row["core5b_mathematical_group_membership"]["replica"]
            for row in core5b_rows
        } != {"1", "2", "4", "8"}
        or len({
            row["core5b_mathematical_group_membership"][
                "selected_group_index"
            ]
            for row in core5b_rows
        }) != 16
    ):
        raise RTA4Core0APilotV2Error(
            "V1/V2 CORE-5B mathematical group membership drift"
        )
    material = {
        "migration_mode": CORE0A_SEED_MIGRATION_MODE,
        "historical_v1_selection": {
            "selection_source_sha256": HISTORICAL_SELECTION_SOURCE_SHA256,
            "ordered_selection_identity": (
                HISTORICAL_ORDERED_SELECTION_IDENTITY
            ),
            "selected_ordinals_by_core": {
                core: [
                    int(row["ordinal"])
                    for row in _historical_ordered_rows()
                    if row["core"] == core
                ]
                for core in RTA4_CORES
            },
        },
        "version_neutral_axis_comparison": {
            "fields": list(CORE0A_VERSION_NEUTRAL_MIGRATION_FIELDS),
            "record_count": len(v2_axes),
            "matching_record_count": len(v2_axes),
            "ordered_rows_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:CORE0A:VERSION_NEUTRAL_AXES:v1",
                {"ordered_rows": v2_axes},
            ),
            "all_match": True,
        },
        "derived_generation_seed_migration": {
            "field_is_profile_and_domain_scoped": True,
            "v1_generation_domain": RTA4_GENERATION_DOMAIN,
            "v2_generation_domain": RTA4_GENERATION_DOMAIN_V2,
            "equality_required": False,
            "different_seed_count": sum(
                left != right for left, right in zip(v1_seeds, v2_seeds)
            ),
            "v1_ordered_seed_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:CORE0A:V1_SELECTED_SEEDS:v1",
                {"ordered_seeds": v1_seeds},
            ),
            "v2_ordered_seed_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:CORE0A:V2_SELECTED_SEEDS:v1",
                {"ordered_seeds": v2_seeds},
            ),
            "v2_native_seed_generation_contract": (
                CORE0A_V2_NATIVE_SEED_CONTRACT
            ),
            "v2_native_seed_record_count": len(v2_seeds),
            "v2_native_seeds_recomputed_from_current_plans": True,
        },
        "v2_taskset_pairing_validation": {
            "taskset_store_identity": formal_taskset_store_identity_v2(),
            "unique_taskset_slot_count": len(groups),
            "reused_execution_count": len(source_bindings) - len(groups),
            "paired_slot_count": sum(len(rows) > 1 for rows in groups.values()),
            "same_slot_uses_same_source": True,
            "ordered_source_binding_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:CORE0A:V2_SOURCE_BINDINGS:v1",
                {"ordered_bindings": source_bindings},
            ),
        },
        "core5b_group_validation": {
            "complete_group_count": 16,
            "replicas_per_group": ["1", "2", "4", "8"],
            "all_groups_complete": True,
        },
        "interpretation": {
            "selection_reuses_historical_positions_not_v1_taskset_instances": True,
            "engineering_validation_only": True,
            "cross_version_scientific_comparison_forbidden": True,
        },
    }
    return material


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader which rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False,
) -> Dict[Any, Any]:
    loader.flatten_mapping(node)
    result: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise RTA4Core0APilotV2Error(
                f"duplicate candidate config key: {key}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def expected_candidate_config_v2() -> Dict[str, Any]:
    return {
        "candidate_schema": (
            "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_CANDIDATE_V3"
        ),
        "contract_version": CORE0A_PORTABLE_CONTRACT_VERSION,
        "status": UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE,
        "purpose": "ENGINEERING_VALIDATION_ONLY_NOT_SCIENTIFIC_RESULTS",
        "selection_artifact": SELECTION_ARTIFACT_PATH,
        "expected_execution_count": EXPECTED_EXECUTION_COUNT,
        "output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "taskset_store_namespace": CORE0A_TASKSET_STORE_NAMESPACE,
        "retry_contract": deepcopy(CORE0A_RETRY_CONTRACT),
        "deployment_policy": deepcopy(CORE0A_DEPLOYMENT_POLICY),
        "seed_migration_contract": {
            "migration_mode": CORE0A_SEED_MIGRATION_MODE,
            "preserved_fields": list(
                CORE0A_VERSION_NEUTRAL_MIGRATION_FIELDS
            ),
            "derived_generation_seed_is_profile_and_domain_scoped": True,
            "v1_v2_seed_equality_required": False,
            "v2_native_seed_generation_contract": (
                CORE0A_V2_NATIVE_SEED_CONTRACT
            ),
            "historical_positions_not_v1_taskset_instances": True,
            "engineering_only_no_cross_version_scientific_comparison": True,
        },
        "formal_authorization": False,
        "production_authorization": False,
        "engineering_pilot_authorization": False,
    }


def load_candidate_config_v2(
    path: Path | str = PROJECT_ROOT / CANDIDATE_CONFIG_PATH,
) -> Dict[str, Any]:
    source = Path(path)
    try:
        value = yaml.load(
            source.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader,
        )
    except RTA4Core0APilotV2Error:
        raise
    except Exception as exc:
        raise RTA4Core0APilotV2Error(
            "cannot load CORE-0A candidate config"
        ) from exc
    if value != expected_candidate_config_v2():
        raise RTA4Core0APilotV2Error("CORE-0A candidate config drift")
    return dict(value)


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments), cwd=str(PROJECT_ROOT), check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode:
        raise RTA4Core0APilotV2Error(
            f"git identity command failed: {' '.join(arguments)}"
        )
    return completed.stdout.strip()


def repository_identity(*, require_clean: bool = True) -> Dict[str, Any]:
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise RTA4Core0APilotV2Error(
            "portable CORE-0A bundle requires a clean worktree"
        )
    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", FORMAL_INPUT_SOURCE_COMMIT, "HEAD"),
        cwd=str(PROJECT_ROOT), check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode:
        raise RTA4Core0APilotV2Error(
            "accepted G2 source commit is not an ancestor of the freeze"
        )
    changed = tuple(filter(None, _git(
        "diff", "--name-only", f"{FORMAL_INPUT_SOURCE_COMMIT}..HEAD",
    ).splitlines()))
    forbidden = sorted(set(changed).difference(ALLOWED_PILOT_FREEZE_DIFF_PATHS))
    if forbidden:
        raise RTA4Core0APilotV2Error(
            f"formal source changed outside CORE-0A scope: {forbidden}"
        )
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_tree": _git("rev-parse", "HEAD^{tree}"),
        "clean_state_required": True,
        "observed_clean": not bool(status),
        "formal_input_source_commit": FORMAL_INPUT_SOURCE_COMMIT,
        "formal_input_source_tree": FORMAL_INPUT_SOURCE_TREE,
        "pilot_only_changed_paths": list(changed),
    }


def _portable_source_rows() -> list[Dict[str, Any]]:
    rows = []
    for relative in PORTABLE_FREEZE_SOURCE_PATHS:
        path = PROJECT_ROOT / relative
        if not path.is_file():
            raise RTA4Core0APilotV2Error(
                f"portable freeze source is absent: {relative}"
            )
        rows.append({
            "path": relative,
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        })
    return rows


def _credential_key(value: Any) -> str | None:
    forbidden = {"credential", "credentials", "password", "secret", "token"}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                return str(key)
            nested = _credential_key(item)
            if nested is not None:
                return nested
    elif isinstance(value, (tuple, list)):
        for item in value:
            nested = _credential_key(item)
            if nested is not None:
                return nested
    return None


def build_portable_candidate_bundle_v2(
    *, selection: Mapping[str, Any] | None = None,
    source_commit: str | None = None,
    source_tree: str | None = None,
    require_clean: bool = True,
) -> Dict[str, Any]:
    selected = (
        load_core0a_selection_v2()
        if selection is None else validate_core0a_selection_v2(selection)
    )
    config = load_candidate_config_v2()
    if (source_commit is None) != (source_tree is None):
        raise RTA4Core0APilotV2Error(
            "portable source commit and tree must be supplied together"
        )
    if source_commit is None:
        source = repository_identity(require_clean=require_clean)
    else:
        source = {
            "git_commit": str(source_commit),
            "git_tree": str(source_tree),
            "clean_state_required": True,
            "observed_clean": True,
            "formal_input_source_commit": FORMAL_INPUT_SOURCE_COMMIT,
            "formal_input_source_tree": FORMAL_INPUT_SOURCE_TREE,
            "pilot_only_changed_paths": [],
        }
    if len(DEFAULT_RELEVANT_SOURCES) != EXPECTED_PRODUCTION_SOURCE_CLOSURE_COUNT:
        raise RTA4Core0APilotV2Error(
            "V2 production source closure count is not 53"
        )
    material: Dict[str, Any] = {
        "bundle_schema": CORE0A_PORTABLE_BUNDLE_SCHEMA,
        "contract_version": CORE0A_PORTABLE_CONTRACT_VERSION,
        "status": CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT,
        "purpose": "ENGINEERING_VALIDATION_ONLY_NOT_SCIENTIFIC_RESULTS",
        "source": source,
        "selection": {
            "artifact_path": SELECTION_ARTIFACT_PATH,
            "artifact_sha256": hashlib.sha256(
                canonical_json_bytes(selected)
            ).hexdigest(),
            "core0a_selection_identity": selected[
                "core0a_selection_identity"
            ],
            "execution_count": len(selected["ordered_records"]),
            "coverage_matrix": selected["coverage_matrix"],
            "algorithm": selected["selection_algorithm"],
            "historical_contract": selected[
                "historical_selection_contract"
            ],
        },
        "scientific_inputs": {
            "profile": RTA4_FORMAL_PROFILE_V2,
            "plan_version": RTA4_FORMAL_PLAN_VERSION_V2,
            "formal_schema_sha256": formal_schema_hash_v2(),
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "all_plan_digest": selected["v2_plans"]["all_plan_digest"],
            "plans": selected["v2_plans"]["plans"],
            "configs": selected["v2_configs"],
        },
        "candidate_config": {
            "path": CANDIDATE_CONFIG_PATH,
            "sha256": _sha256(PROJECT_ROOT / CANDIDATE_CONFIG_PATH),
            "material": config,
            "semantic_identity": domain_hash(
                CORE0A_CANDIDATE_CONFIG_DOMAIN, config,
            ),
        },
        "seed_migration_contract": build_seed_migration_contract_v2(),
        "required_source_files": {
            "production_default_closure_count": len(DEFAULT_RELEVANT_SOURCES),
            "production_default_closure": list(DEFAULT_RELEVANT_SOURCES),
            "portable_freeze_sources": _portable_source_rows(),
        },
        "taskset_contract": {
            "generator_contract": RTA4_TASKSET_GENERATOR_CONTRACT_V2,
            "native_seed_generation_contract": (
                CORE0A_V2_NATIVE_SEED_CONTRACT
            ),
            "derived_seed_is_profile_and_domain_scoped": True,
            "v1_seed_reuse_forbidden": True,
            "taskset_store_domain": (
                "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE:v2"
            ),
            "taskset_store_identity": formal_taskset_store_identity_v2(),
            "store_manifest": RTA4_TASKSET_STORE_MANIFEST_V2,
            "expected_namespace": CORE0A_TASKSET_STORE_NAMESPACE,
        },
        "energy_service_contract": {
            "task_energy_schema": TASK_ENERGY_MATERIAL_SCHEMA,
            "task_energy_domain": TASK_ENERGY_MATERIAL_DOMAIN,
            "task_energy_unit": "J/tick",
            "service_schema": SERVICE_MATERIAL_SCHEMA,
            "service_domain": SERVICE_MATERIAL_DOMAIN,
            "service_source": "VERIFIED_REAL_SOLAR_ARBITRARY_WINDOW",
            "service_spec_domain": SERVICE_SPEC_DOMAIN,
            "beta_contract": BETA_CONTRACT_VERSION,
            "horizon_contract": HORIZON_CONTRACT_VERSION,
            "core3_horizon": (
                "release_horizon_plus_D_max_within_service_material_horizon"
            ),
        },
        "execution_contract": {
            "runner": "AuthorizedRTA4RunnerV2_EXISTING_LIFECYCLE_ONLY",
            "legacy_pilot_execution_config_version": (
                RTA4_PILOT_EXECUTION_CONFIG_VERSION
            ),
            "retry": deepcopy(CORE0A_RETRY_CONTRACT),
            "deployment_policy": deepcopy(CORE0A_DEPLOYMENT_POLICY),
            "checkpoint_schema": RTA4_CHECKPOINT_V2,
            "resume_identity_domain": RTA4_RETRY_RESUME_DOMAIN_V2,
            "run_manifest": RTA4_RUN_MANIFEST_V2,
            "result_directory": RTA4_RESULT_DIRECTORY_V2,
            "failure_classification": [
                "COMPLETED", "TIMEOUT", "INTERNAL_ERROR",
                "UNIFIED_RTA_ADAPTER_TIMEOUT", "RTA_EXECUTOR_MEMORY_BUDGET",
            ],
        },
        "result_contract": {
            "row_schema": RTA4_RESULT_ROW_SCHEMA_V2,
            "result_identity_domain": RTA4_RESULT_DOMAIN_V2,
            "scientific_analysis_identity": (
                "attempt.analysis_identity; excludes runtime/RSS/worker deployment"
            ),
            "terminal_evidence_identity": (
                "result_identity/content hash; includes attempt runtime and RSS and may vary"
            ),
        },
        "worker_consistency_contract": {
            "must_match": [
                "taskset_identity", "task_energy_material_identity",
                "service_material_identity", "beta_material_identity",
                "method", "exact_e0", "scientific_analysis_identity",
                "response_status", "mathematical_result",
                "attempt_index_timeout_status_structure",
            ],
            "may_vary": [
                "runtime_wall_seconds", "runtime_cpu_seconds",
                "peak_rss_bytes", "terminal_content_hash", "result_identity",
            ],
            "runtime_evidence_must_not_be_removed_or_fabricated": True,
        },
        "autodl_deployment_contract": {
            "deployment_manifest_schema": CORE0A_DEPLOYMENT_MANIFEST_SCHEMA,
            "production_build_manifest_schema": PRODUCTION_BUILD_MANIFEST_SCHEMA,
            "environment_allowlist": list(ENVIRONMENT_ALLOWLIST),
            "required_fields": [
                "python_identity", "toolchain_identity", "simulator_identity",
                "verifier_identity", "environment_identity",
                "logical_cpu_count", "physical_memory_bytes",
                "free_disk_bytes", "estimated_required_disk_bytes",
                "worker_count", "max_in_flight",
                "memory_soft_limit_fraction", "memory_soft_limit_bytes",
                "timeout_resource_identity", "actual_output_root",
                "taskset_store_root", "terminal_directory",
                "deployment_workspace_root", "source_root",
                "production_build_manifest_identity",
            ],
            "path_scope_version": CORE0A_DEPLOYMENT_SCOPE_VERSION,
            "resource_policy_version": CORE0A_RESOURCE_POLICY_VERSION,
            "disk_estimate_version": CORE0A_DISK_ESTIMATE_VERSION,
            "disk_safety_margin_version": (
                CORE0A_DISK_SAFETY_MARGIN_VERSION
            ),
            "formal_validator_accepts_file_paths_only": True,
            "generated_on_autodl_only": True,
        },
        "authorization_gate": {
            "current_engineering_authorization": False,
            "formal_authorization": False,
            "production_authorization": False,
            "required_next_status": AUTHORIZED_CORE0A_ENGINEERING_PILOT,
            "independent_read_only_review_required": True,
            "scope_if_later_authorized": CORE0A_AUTHORIZATION_SCOPE,
        },
        "expected_output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "forbidden_actions": [
            "RUN_CANDIDATE_OR_PENDING_REAUDIT_BUNDLE",
            "WRITE_FORMAL_RESULT_ROOT",
            "RUN_CORE0B",
            "RUN_CORE1_TO_CORE5_FORMAL_EXPERIMENTS",
            "GENERATE_PRODUCTION_OR_FORMAL_AUTHORIZATION",
            "CHANGE_SELECTION_OR_SCIENTIFIC_CONFIG",
            "INTERPRET_AS_SCHEDULABILITY_OR_RTA_RESULT",
        ],
        "formal_authorization": False,
        "production_authorization": False,
    }
    if _credential_key(material) is not None:
        raise RTA4Core0APilotV2Error("portable bundle contains credential material")
    encoded = canonical_json(material)
    if "/tmp/" in encoded or "simulator_binary" in encoded:
        raise RTA4Core0APilotV2Error(
            "portable bundle contains a local temporary/binary path"
        )
    return {
        **material,
        "portable_freeze_identity": domain_hash(
            CORE0A_PORTABLE_BUNDLE_DOMAIN, material,
        ),
    }


def validate_portable_candidate_bundle_v2(
    value: Mapping[str, Any], *, require_clean: bool = True,
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4Core0APilotV2Error("portable bundle must be a mapping")
    document = dict(value)
    unsigned = dict(document)
    observed = unsigned.pop("portable_freeze_identity", None)
    if observed != domain_hash(CORE0A_PORTABLE_BUNDLE_DOMAIN, unsigned):
        raise RTA4Core0APilotV2Error("portable bundle identity mismatch")
    expected = build_portable_candidate_bundle_v2(require_clean=require_clean)
    if document != expected:
        raise RTA4Core0APilotV2Error("portable bundle/source drift")
    return document


def build_autodl_handoff_v2(
    bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    portable = dict(bundle)
    if (
        portable.get("bundle_schema") != CORE0A_PORTABLE_BUNDLE_SCHEMA
        or portable.get("status")
        != CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT
        or portable.get("formal_authorization") is not False
        or portable.get("production_authorization") is not False
    ):
        raise RTA4Core0APilotV2Error("handoff requires an unauthorized freeze bundle")
    material = {
        "handoff_schema": CORE0A_HANDOFF_SCHEMA,
        "status": "AUTODL_HANDOFF_CANDIDATE_NOT_AUTHORIZED_TO_RUN",
        "portable_freeze_identity": portable["portable_freeze_identity"],
        "source_commit": portable["source"]["git_commit"],
        "source_tree": portable["source"]["git_tree"],
        "selection_identity": portable["selection"][
            "core0a_selection_identity"
        ],
        "selection_count": EXPECTED_EXECUTION_COUNT,
        "production_source_closure_count": (
            EXPECTED_PRODUCTION_SOURCE_CLOSURE_COUNT
        ),
        "steps": [
            "CHECKOUT_EXACT_SOURCE_COMMIT_AND_TREE",
            "REQUIRE_TRACKED_AND_UNTRACKED_CLEAN",
            "BUILD_SIMULATOR_AND_VERIFIER_ON_AUTODL",
            "GENERATE_AUTODL_PRODUCTION_BUILD_MANIFEST",
            "LIVE_CHECK_ALL_53_PRODUCTION_SOURCE_FILES",
            "LOAD_AND_VALIDATE_PORTABLE_FREEZE_BUNDLE",
            "BUILD_AUTODL_DEPLOYMENT_MANIFEST_AND_EXECUTION_IDENTITY",
            "VERIFY_EXACT_384_RECORD_SELECTION",
            "VERIFY_SIX_V2_CONFIGS_UNAUTHORIZED_PRE_PILOT",
            "OBTAIN_INDEPENDENT_LIMITED_ENGINEERING_AUTHORIZATION",
            "USE_DEDICATED_CORE0A_OUTPUT_NAMESPACE",
            "ENABLE_V2_CHECKPOINT_AND_RESUME",
            "REJECT_CORE0B_AND_CORE1_TO_CORE5_FORMAL_SCOPE",
            "PERSIST_LOGS_ENVIRONMENT_TERMINALS_STORE_AND_SUMMARY",
            "LABEL_ALL_RESULTS_ENGINEERING_AUDIT_ONLY",
        ],
        "required_deployment_manifest_schema": (
            CORE0A_DEPLOYMENT_MANIFEST_SCHEMA
        ),
        "required_production_manifest_schema": (
            PRODUCTION_BUILD_MANIFEST_SCHEMA
        ),
        "expected_output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "authorization_required_before_any_record": True,
        "formal_authorization": False,
        "production_authorization": False,
    }
    return {
        **material,
        "autodl_handoff_identity": domain_hash(CORE0A_HANDOFF_DOMAIN, material),
    }


def validate_autodl_handoff_v2(
    value: Mapping[str, Any], bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = build_autodl_handoff_v2(bundle)
    if dict(value) != expected:
        raise RTA4Core0APilotV2Error("AutoDL handoff identity/material mismatch")
    return dict(value)


@dataclass(frozen=True)
class AutoDLResourceObservation:
    logical_cpu_count: int
    physical_memory_bytes: int
    free_disk_bytes: int


@dataclass(frozen=True)
class ValidatedCore0ADeployment:
    portable_bundle: Mapping[str, Any]
    selection: Mapping[str, Any]
    candidate_config: Mapping[str, Any]
    production_manifest: Mapping[str, Any]
    deployment_manifest: Mapping[str, Any]
    source_root: str
    deployment_workspace_root: str
    execution_identity: str


def _resolved_existing_directory(path: Path | str, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RTA4Core0APilotV2Error(f"{label} must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RTA4Core0APilotV2Error(f"cannot resolve {label}") from exc
    if not resolved.is_dir():
        raise RTA4Core0APilotV2Error(f"{label} must be a directory")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or _is_within(left, right) or _is_within(right, left)


def _derived_deployment_paths(
    deployment_workspace_root: Path | str,
) -> Dict[str, str]:
    workspace = _resolved_existing_directory(
        deployment_workspace_root, "deployment workspace root",
    )
    output = (workspace / CORE0A_OUTPUT_NAMESPACE).resolve(strict=False)
    taskset_store = (
        workspace / CORE0A_TASKSET_STORE_NAMESPACE
    ).resolve(strict=False)
    terminal = (output / CORE0A_TERMINAL_DIRECTORY).resolve(strict=False)
    for label, path in (
        ("CORE-0A output root", output),
        ("CORE-0A taskset store root", taskset_store),
        ("CORE-0A terminal directory", terminal),
    ):
        if not _is_within(path, workspace):
            raise RTA4Core0APilotV2Error(
                f"{label} escapes deployment workspace after symlink resolution"
            )
    forbidden_outputs = tuple(
        (workspace / relative).resolve(strict=False)
        for relative in FORBIDDEN_FORMAL_OUTPUT_NAMESPACES
    )
    if any(output == root or _is_within(output, root) for root in forbidden_outputs):
        raise RTA4Core0APilotV2Error(
            "CORE-0A output root overlaps a formal result root"
        )
    forbidden_stores = tuple(
        (workspace / relative).resolve(strict=False)
        for relative in FORBIDDEN_FORMAL_STORE_NAMESPACES
    )
    if any(
        taskset_store == root or _is_within(taskset_store, root)
        for root in forbidden_stores
    ):
        raise RTA4Core0APilotV2Error(
            "CORE-0A taskset store overlaps a formal store root"
        )
    if _overlaps(output, taskset_store):
        raise RTA4Core0APilotV2Error(
            "CORE-0A output and taskset store roots overlap"
        )
    if terminal == output or not _is_within(terminal, output):
        raise RTA4Core0APilotV2Error(
            "CORE-0A terminal directory must be inside its output root"
        )
    workspace_identity = domain_hash(
        CORE0A_DEPLOYMENT_WORKSPACE_DOMAIN,
        {
            "canonical_deployment_workspace_root": str(workspace),
            "output_namespace": CORE0A_OUTPUT_NAMESPACE,
            "taskset_store_namespace": CORE0A_TASKSET_STORE_NAMESPACE,
        },
    )
    return {
        "deployment_workspace_root": str(workspace),
        "deployment_workspace_identity": workspace_identity,
        "expected_output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "actual_output_root": str(output),
        "taskset_store_namespace": CORE0A_TASKSET_STORE_NAMESPACE,
        "taskset_store_root": str(taskset_store),
        "terminal_directory_name": CORE0A_TERMINAL_DIRECTORY,
        "terminal_directory": str(terminal),
    }


def _observe_autodl_resources(
    deployment_workspace_root: Path | str,
) -> AutoDLResourceObservation:
    workspace = _resolved_existing_directory(
        deployment_workspace_root, "deployment workspace root",
    )
    logical_cpu_count = os.cpu_count()
    try:
        physical_memory_bytes = (
            int(os.sysconf("SC_PHYS_PAGES"))
            * int(os.sysconf("SC_PAGE_SIZE"))
        )
    except (OSError, ValueError, TypeError) as exc:
        raise RTA4Core0APilotV2Error(
            "cannot observe physical memory"
        ) from exc
    free_disk_bytes = int(shutil.disk_usage(workspace).free)
    if (
        type(logical_cpu_count) is not int or logical_cpu_count < 1
        or physical_memory_bytes < 1 or free_disk_bytes < 0
    ):
        raise RTA4Core0APilotV2Error(
            "AutoDL resource observation is invalid"
        )
    return AutoDLResourceObservation(
        logical_cpu_count=logical_cpu_count,
        physical_memory_bytes=physical_memory_bytes,
        free_disk_bytes=free_disk_bytes,
    )


def _disk_estimate(selection: Mapping[str, Any]) -> Dict[str, Any]:
    execution_count = len(selection["ordered_records"])
    unique_tasksets = int(
        selection["coverage_matrix"]["unique_taskset_slot_count"]
    )
    estimated = (
        execution_count * CORE0A_DISK_BYTES_PER_EXECUTION
        + unique_tasksets * CORE0A_DISK_BYTES_PER_UNIQUE_TASKSET
        + CORE0A_DISK_FIXED_OVERHEAD_BYTES
    )
    safety_margin = max(
        CORE0A_DISK_MINIMUM_SAFETY_MARGIN_BYTES,
        (estimated + 9) // 10,
    )
    material = {
        "estimate_version": CORE0A_DISK_ESTIMATE_VERSION,
        "estimate_source": "FROZEN_384_SCOPE_COMPONENT_BUDGET",
        "execution_count": execution_count,
        "unique_taskset_slot_count": unique_tasksets,
        "bytes_per_execution": CORE0A_DISK_BYTES_PER_EXECUTION,
        "bytes_per_unique_taskset": CORE0A_DISK_BYTES_PER_UNIQUE_TASKSET,
        "fixed_overhead_bytes": CORE0A_DISK_FIXED_OVERHEAD_BYTES,
        "estimated_required_disk_bytes": estimated,
        "safety_margin_version": CORE0A_DISK_SAFETY_MARGIN_VERSION,
        "safety_margin_algorithm": "max(1_GiB,ceil(estimate/10))",
        "explicit_safety_margin_bytes": safety_margin,
        "required_free_disk_bytes": estimated + safety_margin,
    }
    return {
        **material,
        "disk_estimate_identity": domain_hash(
            CORE0A_DISK_ESTIMATE_DOMAIN, material,
        ),
    }


def _resource_policy(
    observation: AutoDLResourceObservation,
    disk_estimate: Mapping[str, Any],
) -> Dict[str, Any]:
    logical_cpu_count = observation.logical_cpu_count
    workers = min(4, logical_cpu_count)
    in_flight = min(max(workers, 2 * workers), logical_cpu_count)
    memory_limit = observation.physical_memory_bytes * 7 // 10
    required_disk = int(disk_estimate["required_free_disk_bytes"])
    if observation.free_disk_bytes < required_disk:
        raise RTA4Core0APilotV2Error(
            "AutoDL free disk is below estimate plus safety margin"
        )
    observed_material = {
        "logical_cpu_count": logical_cpu_count,
        "physical_memory_bytes": observation.physical_memory_bytes,
        "free_disk_bytes": observation.free_disk_bytes,
    }
    timeout_material = {
        "resource_policy_version": CORE0A_RESOURCE_POLICY_VERSION,
        "worker_count": workers,
        "max_in_flight": in_flight,
        "memory_soft_limit_fraction": CORE0A_MEMORY_SOFT_LIMIT_FRACTION,
        "memory_soft_limit_bytes": memory_limit,
        "checkpoint_frequency_records": CORE0A_CHECKPOINT_FREQUENCY,
        "retry_contract": deepcopy(CORE0A_RETRY_CONTRACT),
    }
    return {
        "resource_policy_version": CORE0A_RESOURCE_POLICY_VERSION,
        **observed_material,
        "resource_observation_identity": domain_hash(
            CORE0A_RESOURCE_OBSERVATION_DOMAIN, observed_material,
        ),
        "worker_count": workers,
        "max_in_flight": in_flight,
        "memory_soft_limit_fraction": CORE0A_MEMORY_SOFT_LIMIT_FRACTION,
        "memory_soft_limit_bytes": memory_limit,
        "checkpoint_frequency_records": CORE0A_CHECKPOINT_FREQUENCY,
        "resume_policy": PILOT_RESUME_POLICY,
        "retry_contract": deepcopy(CORE0A_RETRY_CONTRACT),
        "timeout_resource_identity": domain_hash(
            CORE0A_TIMEOUT_RESOURCE_DOMAIN, timeout_material,
        ),
        "disk_preflight_passed": True,
    }


def _production_component_identities(
    production_manifest: Mapping[str, Any],
) -> Dict[str, str]:
    if not isinstance(production_manifest, Mapping):
        raise RTA4Core0APilotV2Error(
            "production build manifest must be a mapping"
        )
    document = dict(production_manifest)
    observed = document.pop("manifest_id", None)
    if (
        production_manifest.get("manifest_schema")
        != PRODUCTION_BUILD_MANIFEST_SCHEMA
        or production_manifest.get("formal_authorization") is not False
        or observed
        != domain_hash(PRODUCTION_BUILD_MANIFEST_DOMAIN, document)
    ):
        raise RTA4Core0APilotV2Error(
            "production build manifest identity mismatch"
        )
    try:
        components = {
            "production_build_manifest_identity": str(observed),
            "python_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:PYTHON_DEPLOYMENT:v2",
                production_manifest["python"],
            ),
            "toolchain_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:TOOLCHAIN_DEPLOYMENT:v2",
                production_manifest["cpp_toolchain"],
            ),
            "simulator_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:SIMULATOR_DEPLOYMENT:v2",
                production_manifest["simulator"],
            ),
            "verifier_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:VERIFIER_DEPLOYMENT:v2",
                production_manifest["solar_verifier"],
            ),
            "environment_identity": domain_hash(
                "ASAP_BLOCK:V9.3:RTA4:ENVIRONMENT_DEPLOYMENT:v2",
                production_manifest["environment"],
            ),
        }
    except (KeyError, TypeError) as exc:
        raise RTA4Core0APilotV2Error(
            "production build manifest is incomplete"
        ) from exc
    return components


def _validate_bundle_for_deployment(
    bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    portable = dict(bundle)
    if (
        portable.get("bundle_schema") != CORE0A_PORTABLE_BUNDLE_SCHEMA
        or portable.get("status")
        != CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT
        or portable.get("formal_authorization") is not False
        or portable.get("production_authorization") is not False
        or portable.get("selection", {}).get("execution_count")
        != EXPECTED_EXECUTION_COUNT
    ):
        raise RTA4Core0APilotV2Error(
            "deployment requires an exact unauthorized portable freeze"
        )
    unsigned = dict(portable)
    observed = unsigned.pop("portable_freeze_identity", None)
    if observed != domain_hash(CORE0A_PORTABLE_BUNDLE_DOMAIN, unsigned):
        raise RTA4Core0APilotV2Error("portable bundle identity mismatch")
    return portable


def _build_autodl_deployment_manifest_v2(
    *,
    bundle: Mapping[str, Any],
    production_manifest: Mapping[str, Any],
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    observation: AutoDLResourceObservation,
) -> Dict[str, Any]:
    portable = _validate_bundle_for_deployment(bundle)
    source = _resolved_existing_directory(source_root, "source root")
    paths = _derived_deployment_paths(deployment_workspace_root)
    try:
        production_source = _resolved_existing_directory(
            production_manifest["repository"]["source_root"],
            "production manifest source root",
        )
        production_commit = str(
            production_manifest["repository"]["git_commit"]
        )
        production_tree = str(production_manifest["repository"]["git_tree"])
    except (KeyError, TypeError) as exc:
        raise RTA4Core0APilotV2Error(
            "production repository binding is incomplete"
        ) from exc
    if source != production_source:
        raise RTA4Core0APilotV2Error(
            "source root differs from production manifest"
        )
    if (
        production_commit != portable["source"]["git_commit"]
        or production_tree != portable["source"]["git_tree"]
    ):
        raise RTA4Core0APilotV2Error(
            "production source commit/tree differs from portable freeze"
        )
    selection = build_core0a_selection_v2()
    if (
        portable["selection"]["core0a_selection_identity"]
        != selection["core0a_selection_identity"]
        or portable["selection"]["execution_count"]
        != len(selection["ordered_records"])
    ):
        raise RTA4Core0APilotV2Error(
            "portable selection scope differs from current V2 plans"
        )
    disk = _disk_estimate(selection)
    resources = _resource_policy(
        observation,
        disk,
    )
    production = _production_component_identities(production_manifest)
    material = {
        "deployment_manifest_schema": CORE0A_DEPLOYMENT_MANIFEST_SCHEMA,
        "deployment_scope_version": CORE0A_DEPLOYMENT_SCOPE_VERSION,
        "status": "UNAUTHORIZED_AUTODL_DEPLOYMENT_CANDIDATE",
        "portable_freeze_identity": portable["portable_freeze_identity"],
        "source_commit": portable["source"]["git_commit"],
        "source_tree": portable["source"]["git_tree"],
        "source_root": str(source),
        "selection_identity": selection["core0a_selection_identity"],
        "selection_count": len(selection["ordered_records"]),
        "authorization_scope": CORE0A_AUTHORIZATION_SCOPE,
        "max_runs": CORE0A_MAX_RUNS,
        "scientific_inputs": {
            "profile": RTA4_FORMAL_PROFILE_V2,
            "plan_version": RTA4_FORMAL_PLAN_VERSION_V2,
            "formal_schema_sha256": formal_schema_hash_v2(),
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "all_plan_digest": selection["v2_plans"]["all_plan_digest"],
            "plans": selection["v2_plans"]["plans"],
            "config_identities": {
                core: selection["v2_configs"][core]["semantic_identity"]
                for core in RTA4_CORES
            },
            "candidate_config_identity": portable["candidate_config"][
                "semantic_identity"
            ],
        },
        **production,
        **paths,
        "taskset_store_identity": formal_taskset_store_identity_v2(),
        **resources,
        **disk,
        "formal_authorization": False,
        "production_authorization": False,
        "engineering_pilot_authorization": False,
    }
    return {
        **material,
        "deployment_manifest_identity": domain_hash(
            CORE0A_DEPLOYMENT_MANIFEST_DOMAIN, material,
        ),
    }


def build_autodl_deployment_manifest_v2(
    *,
    bundle: Mapping[str, Any],
    production_manifest: Mapping[str, Any],
    source_root: Path | str,
    deployment_workspace_root: Path | str,
) -> Dict[str, Any]:
    return _build_autodl_deployment_manifest_v2(
        bundle=bundle,
        production_manifest=production_manifest,
        source_root=source_root,
        deployment_workspace_root=deployment_workspace_root,
        observation=_observe_autodl_resources(deployment_workspace_root),
    )


def _combined_execution_identity(
    bundle: Mapping[str, Any],
    deployment_manifest: Mapping[str, Any],
) -> str:
    return domain_hash(CORE0A_EXECUTION_IDENTITY_DOMAIN, {
        "portable_freeze_identity": bundle["portable_freeze_identity"],
        "deployment_manifest_identity": deployment_manifest[
            "deployment_manifest_identity"
        ],
        "selection_identity": bundle["selection"]["core0a_selection_identity"],
        "selection_count": EXPECTED_EXECUTION_COUNT,
        "expected_output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "actual_output_root": deployment_manifest["actual_output_root"],
        "taskset_store_root": deployment_manifest["taskset_store_root"],
        "deployment_workspace_identity": deployment_manifest[
            "deployment_workspace_identity"
        ],
        "resource_observation_identity": deployment_manifest[
            "resource_observation_identity"
        ],
        "max_runs": CORE0A_MAX_RUNS,
    })


def validate_autodl_deployment_manifest_v2(
    *,
    portable_bundle_path: Path | str,
    selection_artifact_path: Path | str,
    candidate_config_path: Path | str,
    production_manifest_path: Path | str,
    deployment_manifest_path: Path | str,
    source_root: Path | str,
    deployment_workspace_root: Path | str,
    require_clean: bool = True,
) -> ValidatedCore0ADeployment:
    source = _resolved_existing_directory(source_root, "source root")
    if source != PROJECT_ROOT.resolve(strict=True):
        raise RTA4Core0APilotV2Error(
            "formal deployment validator must execute from the frozen source root"
        )
    selection = load_core0a_selection_v2(selection_artifact_path)
    candidate = load_candidate_config_v2(candidate_config_path)
    portable = validate_portable_candidate_bundle_v2(
        load_strict_canonical_json(portable_bundle_path),
        require_clean=require_clean,
    )
    if (
        portable["selection"]["artifact_sha256"]
        != _sha256(Path(selection_artifact_path))
        or portable["selection"]["core0a_selection_identity"]
        != selection["core0a_selection_identity"]
        or portable["selection"]["execution_count"]
        != len(selection["ordered_records"])
        or portable["candidate_config"]["sha256"]
        != _sha256(Path(candidate_config_path))
        or portable["candidate_config"]["material"] != candidate
        or portable["candidate_config"]["semantic_identity"]
        != domain_hash(CORE0A_CANDIDATE_CONFIG_DOMAIN, candidate)
    ):
        raise RTA4Core0APilotV2Error(
            "portable bundle does not bind the supplied frozen artifacts"
        )
    try:
        production = load_and_validate_production_build_manifest(
            production_manifest_path,
            require_clean=require_clean,
            require_default_closure=True,
        )
    except Exception as exc:
        raise RTA4Core0APilotV2Error(
            "live production build manifest validation failed"
        ) from exc
    document = load_strict_canonical_json(deployment_manifest_path)
    observed = AutoDLResourceObservation(
        logical_cpu_count=document.get("logical_cpu_count"),
        physical_memory_bytes=document.get("physical_memory_bytes"),
        free_disk_bytes=document.get("free_disk_bytes"),
    )
    if any(
        type(value) is not int or value < minimum
        for value, minimum in (
            (observed.logical_cpu_count, 1),
            (observed.physical_memory_bytes, 1),
            (observed.free_disk_bytes, 0),
        )
    ):
        raise RTA4Core0APilotV2Error(
            "deployment resource observation fields are invalid"
        )
    live = _observe_autodl_resources(deployment_workspace_root)
    if (
        observed.logical_cpu_count != live.logical_cpu_count
        or observed.physical_memory_bytes != live.physical_memory_bytes
        or abs(observed.free_disk_bytes - live.free_disk_bytes)
        > CORE0A_DISK_OBSERVATION_TOLERANCE_BYTES
    ):
        raise RTA4Core0APilotV2Error(
            "deployment resource observations differ from live AutoDL"
        )
    required_disk = _disk_estimate(selection)["required_free_disk_bytes"]
    if (
        observed.free_disk_bytes < required_disk
        or live.free_disk_bytes < required_disk
    ):
        raise RTA4Core0APilotV2Error(
            "live or recorded disk space is below the required margin"
        )
    expected = _build_autodl_deployment_manifest_v2(
        bundle=portable,
        production_manifest=production,
        source_root=source,
        deployment_workspace_root=deployment_workspace_root,
        observation=observed,
    )
    if document != expected:
        raise RTA4Core0APilotV2Error(
            "AutoDL deployment differs from reconstructed frozen scope"
        )
    execution_identity = _combined_execution_identity(portable, document)
    return ValidatedCore0ADeployment(
        portable_bundle=portable,
        selection=selection,
        candidate_config=candidate,
        production_manifest=production,
        deployment_manifest=document,
        source_root=str(source),
        deployment_workspace_root=str(
            _resolved_existing_directory(
                deployment_workspace_root, "deployment workspace root",
            )
        ),
        execution_identity=execution_identity,
    )


def core0a_execution_identity(
    validated_deployment: ValidatedCore0ADeployment,
) -> str:
    if type(validated_deployment) is not ValidatedCore0ADeployment:
        raise RTA4Core0APilotV2Error(
            "execution identity requires a validated deployment object"
        )
    expected = _combined_execution_identity(
        validated_deployment.portable_bundle,
        validated_deployment.deployment_manifest,
    )
    if expected != validated_deployment.execution_identity:
        raise RTA4Core0APilotV2Error(
            "validated deployment execution identity drift"
        )
    return expected


def scientific_analysis_identity(
    *, record: Mapping[str, Any], taskset_identity: str,
    task_energy_material_identity: str, service_material_identity: str,
    beta_material_identity: str, response_status: str,
    mathematical_result: Mapping[str, Any],
    attempt_index_timeout_status: Sequence[Mapping[str, Any]],
) -> str:
    attempts = [
        {
            "attempt_index": row["attempt_index"],
            "timeout_seconds": row["timeout_seconds"],
            "status": row["status"],
        }
        for row in attempt_index_timeout_status
    ]
    material = {
        "profile": RTA4_FORMAL_PROFILE_V2,
        "plan_record_identity": record["plan_record_identity"],
        "mathematical_cell_identity": record["mathematical_cell_identity"],
        "taskset_identity": taskset_identity,
        "task_energy_material_identity": task_energy_material_identity,
        "service_material_identity": service_material_identity,
        "beta_material_identity": beta_material_identity,
        "method": record["method"],
        "exact_e0": record["exact_e0"],
        "response_status": response_status,
        "mathematical_result": dict(mathematical_result),
        "attempt_index_timeout_status": attempts,
    }
    return domain_hash(CORE0A_SCIENTIFIC_ANALYSIS_DOMAIN, material)


def terminal_evidence_identity(
    *, scientific_identity: str, runtime_wall_seconds: str,
    runtime_cpu_seconds: str, peak_rss_bytes: int, worker_count: int,
    terminal_content: Mapping[str, Any],
) -> str:
    return domain_hash(CORE0A_TERMINAL_EVIDENCE_DOMAIN, {
        "scientific_analysis_identity": scientific_identity,
        "runtime_wall_seconds": runtime_wall_seconds,
        "runtime_cpu_seconds": runtime_cpu_seconds,
        "peak_rss_bytes": peak_rss_bytes,
        "worker_count": worker_count,
        "terminal_content": dict(terminal_content),
    })


def require_authorized_core0a_engineering_pilot(
    validated_deployment: ValidatedCore0ADeployment | None,
    authorization: Mapping[str, Any] | None,
) -> None:
    if (
        authorization is None
        or type(validated_deployment) is not ValidatedCore0ADeployment
    ):
        raise RTA4Core0APilotV2Error(
            "CORE-0A execution requires independent authorization and a "
            "file-validated deployment"
        )
    bundle = validated_deployment.portable_bundle
    deployment_manifest = validated_deployment.deployment_manifest
    exact = {
        "authorization_schema", "status", "independent_read_only_review",
        "review_identity", "portable_freeze_identity", "selection_identity",
        "source_commit", "source_tree", "deployment_manifest_identity",
        "execution_identity", "output_namespace", "actual_output_root",
        "taskset_store_root", "deployment_workspace_identity",
        "authorized_execution_count", "max_runs", "scope",
        "run_nonce", "expires_at_utc", "formal_authorization",
        "production_authorization", "authorization_id",
    }
    if set(authorization) != exact:
        raise RTA4Core0APilotV2Error("CORE-0A authorization field set mismatch")
    if authorization.get("status") != AUTHORIZED_CORE0A_ENGINEERING_PILOT:
        raise RTA4Core0APilotV2Error("CORE-0A candidate is not authorized")
    if authorization.get("independent_read_only_review") is not True:
        raise RTA4Core0APilotV2Error("CORE-0A independent review is absent")
    if authorization.get("portable_freeze_identity") != bundle.get(
        "portable_freeze_identity"
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization freeze drift")
    if (
        authorization.get("selection_identity")
        != bundle.get("selection", {}).get("core0a_selection_identity")
        or authorization.get("source_commit")
        != bundle.get("source", {}).get("git_commit")
        or authorization.get("source_tree")
        != bundle.get("source", {}).get("git_tree")
        or authorization.get("output_namespace") != CORE0A_OUTPUT_NAMESPACE
        or authorization.get("actual_output_root")
        != deployment_manifest.get("actual_output_root")
        or authorization.get("taskset_store_root")
        != deployment_manifest.get("taskset_store_root")
        or authorization.get("deployment_workspace_identity")
        != deployment_manifest.get("deployment_workspace_identity")
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization source/scope drift")
    if authorization.get("deployment_manifest_identity") != deployment_manifest.get(
        "deployment_manifest_identity"
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization deployment drift")
    if authorization.get("execution_identity") != core0a_execution_identity(
        validated_deployment,
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization execution drift")
    if (
        authorization.get("authorized_execution_count")
        != EXPECTED_EXECUTION_COUNT
        or authorization.get("max_runs") != CORE0A_MAX_RUNS
        or authorization.get("scope") != CORE0A_AUTHORIZATION_SCOPE
        or not isinstance(authorization.get("run_nonce"), str)
        or not authorization["run_nonce"]
        or not isinstance(authorization.get("expires_at_utc"), str)
        or not authorization["expires_at_utc"]
        or authorization.get("formal_authorization") is not False
        or authorization.get("production_authorization") is not False
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization scope is invalid")
    unsigned = dict(authorization)
    observed = unsigned.pop("authorization_id", None)
    if (
        authorization.get("authorization_schema")
        != CORE0A_AUTHORIZATION_SCHEMA
        or not isinstance(authorization.get("review_identity"), str)
        or len(authorization["review_identity"]) != 64
        or observed != domain_hash(CORE0A_AUTHORIZATION_DOMAIN, unsigned)
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization identity mismatch")


def selected_ordinals_by_core(
    selection: Mapping[str, Any],
) -> Dict[str, tuple[int, ...]]:
    document = validate_core0a_selection_v2(selection)
    return {
        core: tuple(
            int(row["ordinal"])
            for row in document["ordered_records"] if row["core"] == core
        )
        for core in RTA4_CORES
    }


__all__ = [
    "AutoDLResourceObservation",
    "AUTHORIZED_CORE0A_ENGINEERING_PILOT", "CORE0A_CONTRACT_VERSION",
    "CANDIDATE_CONFIG_PATH", "CORE0A_AUTHORIZATION_SCHEMA",
    "CORE0A_DEPLOYMENT_MANIFEST_SCHEMA", "CORE0A_DEPLOYMENT_POLICY",
    "CORE0A_DEPLOYMENT_SCOPE_VERSION",
    "CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT",
    "CORE0A_HANDOFF_SCHEMA", "CORE0A_OUTPUT_NAMESPACE",
    "CORE0A_PORTABLE_CONTRACT_VERSION",
    "CORE0A_PORTABLE_BUNDLE_SCHEMA", "CORE0A_RETRY_CONTRACT",
    "CORE0A_RESOURCE_POLICY_VERSION", "CORE0A_SEED_MIGRATION_MODE",
    "CORE0A_SELECTION_ALGORITHM", "CORE0A_SELECTION_SCHEMA",
    "CORE0A_TASKSET_STORE_NAMESPACE",
    "EXPECTED_EXECUTION_COUNT", "FORMAL_INPUT_SOURCE_COMMIT",
    "FORMAL_INPUT_SOURCE_TREE", "HISTORICAL_CORE_RECORD_COUNTS",
    "HISTORICAL_ORDERED_SELECTION_IDENTITY", "HISTORICAL_SELECTION_SEED",
    "HISTORICAL_SELECTION_SOURCE_SHA256", "PROJECT_ROOT",
    "RTA4Core0APilotV2Error", "SELECTION_ARTIFACT_PATH",
    "UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE",
    "ValidatedCore0ADeployment",
    "V1_CONFIG_PATHS", "V2_CONFIG_PATHS", "build_core0a_selection_v2",
    "build_autodl_deployment_manifest_v2", "build_autodl_handoff_v2",
    "build_portable_candidate_bundle_v2", "build_seed_migration_contract_v2",
    "core0a_execution_identity",
    "canonical_json_bytes", "coverage_matrix", "load_core0a_selection_v2",
    "load_candidate_config_v2", "load_strict_canonical_json",
    "repository_identity", "require_authorized_core0a_engineering_pilot",
    "scientific_analysis_identity", "selected_ordinals_by_core",
    "terminal_evidence_identity", "validate_autodl_handoff_v2",
    "validate_autodl_deployment_manifest_v2",
    "validate_core0a_selection_v2", "validate_portable_candidate_bundle_v2",
    "write_canonical_json",
]
