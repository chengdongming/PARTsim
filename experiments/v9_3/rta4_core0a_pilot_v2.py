"""Portable input freeze for the RTA4 CORE-0A V2 engineering pilot.

This module selects records only.  It has no execution entry point and cannot
authorize either an engineering pilot or a formal/production run.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
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
    canonical_json,
    domain_hash,
    load_rta4_formal_config,
)
from .rta4_formal_config_v2 import (
    RTA4_FORMAL_PLAN_VERSION_V2,
    RTA4_FORMAL_PROFILE_V2,
    load_rta4_formal_config_v2,
    rta4_formal_config_hash_v2,
)
from .rta4_formal_pilot import build_pilot_manifest
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
    PRODUCTION_BUILD_MANIFEST_SCHEMA,
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
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_PORTABLE_CANDIDATE_BUNDLE_V2"
)
CORE0A_PORTABLE_BUNDLE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:PORTABLE_CANDIDATE_BUNDLE:v2"
)
CORE0A_HANDOFF_SCHEMA = "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTODL_HANDOFF_V2"
CORE0A_HANDOFF_DOMAIN = "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTODL_HANDOFF:v2"
CORE0A_DEPLOYMENT_MANIFEST_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_AUTODL_DEPLOYMENT_MANIFEST_V1"
)
CORE0A_DEPLOYMENT_MANIFEST_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:AUTODL_DEPLOYMENT_MANIFEST:v1"
)
CORE0A_EXECUTION_IDENTITY_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:EXECUTION_IDENTITY:v1"
)
CORE0A_SCIENTIFIC_ANALYSIS_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:SCIENTIFIC_ANALYSIS:v1"
)
CORE0A_TERMINAL_EVIDENCE_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:TERMINAL_EVIDENCE:v1"
)
CORE0A_AUTHORIZATION_SCHEMA = (
    "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_AUTHORIZATION_V1"
)
CORE0A_AUTHORIZATION_DOMAIN = (
    "ASAP_BLOCK:V9.3:RTA4:CORE0A:ENGINEERING_PILOT_AUTHORIZATION:v1"
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


def _method(record: FormalPlanRecordV2) -> str:
    return str(
        record.material.get(
            "method",
            "CORE3_SIMULATION_V2" if record.kind == "simulation" else "NA",
        )
    )


def _e0(record: FormalPlanRecordV2) -> str:
    return str(record.material.get(
        "exact_e0", record.material.get("physical_initial_energy", "NA"),
    ))


def _time_scale(record: FormalPlanRecordV2) -> str:
    if record.material.get("axis") == "integer_time_scale":
        return str(record.material["axis_value"])
    return "1"


def _track(record: FormalPlanRecordV2) -> str:
    if record.kind == "simulation":
        return (
            f"{record.material['applicability_track']}:"
            f"{record.material['release_mode']}"
        )
    return str(record.material.get("scenario", "NA"))


def _execution_replica(record: FormalPlanRecordV2) -> str:
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
            "ASAP_BLOCK_V9_3_RTA4_CORE0A_ENGINEERING_PILOT_CANDIDATE_V2"
        ),
        "contract_version": CORE0A_CONTRACT_VERSION,
        "status": UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE,
        "purpose": "ENGINEERING_VALIDATION_ONLY_NOT_SCIENTIFIC_RESULTS",
        "selection_artifact": SELECTION_ARTIFACT_PATH,
        "expected_execution_count": EXPECTED_EXECUTION_COUNT,
        "output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "taskset_store_namespace": CORE0A_TASKSET_STORE_NAMESPACE,
        "retry_contract": CORE0A_RETRY_CONTRACT,
        "deployment_policy": CORE0A_DEPLOYMENT_POLICY,
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
        "contract_version": CORE0A_CONTRACT_VERSION,
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
        },
        "required_source_files": {
            "production_default_closure_count": len(DEFAULT_RELEVANT_SOURCES),
            "production_default_closure": list(DEFAULT_RELEVANT_SOURCES),
            "portable_freeze_sources": _portable_source_rows(),
        },
        "taskset_contract": {
            "generator_contract": RTA4_TASKSET_GENERATOR_CONTRACT_V2,
            "taskset_store_domain": (
                "ASAP_BLOCK:V9.3:RTA4:TASKSET_STORE:v2"
            ),
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
            "retry": CORE0A_RETRY_CONTRACT,
            "deployment_policy": CORE0A_DEPLOYMENT_POLICY,
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
                "verifier_identity", "environment_identity", "worker_count",
                "max_in_flight", "memory_soft_limit_bytes",
                "timeout_resource_identity", "output_root", "taskset_store",
                "source_root", "production_build_manifest_identity",
            ],
            "generated_on_autodl_only": True,
        },
        "authorization_gate": {
            "current_engineering_authorization": False,
            "formal_authorization": False,
            "production_authorization": False,
            "required_next_status": AUTHORIZED_CORE0A_ENGINEERING_PILOT,
            "independent_read_only_review_required": True,
            "scope_if_later_authorized": "EXACT_384_RECORD_CORE0A_ONLY",
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


_DEPLOYMENT_FIELDS = frozenset({
    "production_build_manifest_identity", "python_identity",
    "toolchain_identity", "simulator_identity", "verifier_identity",
    "environment_identity", "worker_count", "max_in_flight",
    "memory_soft_limit_bytes", "timeout_resource_identity", "output_root",
    "taskset_store", "source_root",
})


def build_autodl_deployment_manifest_v2(
    bundle: Mapping[str, Any], deployment: Mapping[str, Any],
) -> Dict[str, Any]:
    if set(deployment) != _DEPLOYMENT_FIELDS:
        raise RTA4Core0APilotV2Error("AutoDL deployment field set mismatch")
    if bundle.get("status") != CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT:
        raise RTA4Core0APilotV2Error("deployment requires a frozen portable bundle")
    workers = deployment["worker_count"]
    in_flight = deployment["max_in_flight"]
    memory = deployment["memory_soft_limit_bytes"]
    if (
        type(workers) is not int or workers < 1
        or type(in_flight) is not int or in_flight < workers
        or type(memory) is not int or memory < 1
    ):
        raise RTA4Core0APilotV2Error("AutoDL deployment resource bounds are invalid")
    for field in ("output_root", "taskset_store", "source_root"):
        if not isinstance(deployment[field], str) or not Path(
            deployment[field]
        ).is_absolute():
            raise RTA4Core0APilotV2Error(
                f"AutoDL deployment {field} must be absolute"
            )
    for field in _DEPLOYMENT_FIELDS.difference({
        "worker_count", "max_in_flight", "memory_soft_limit_bytes",
        "output_root", "taskset_store", "source_root",
    }):
        value = deployment[field]
        if not isinstance(value, str) or len(value) != 64:
            raise RTA4Core0APilotV2Error(
                f"AutoDL deployment {field} must be a SHA-256 identity"
            )
    material = {
        "deployment_manifest_schema": CORE0A_DEPLOYMENT_MANIFEST_SCHEMA,
        "status": "UNAUTHORIZED_AUTODL_DEPLOYMENT_CANDIDATE",
        "portable_freeze_identity": bundle["portable_freeze_identity"],
        "source_commit": bundle["source"]["git_commit"],
        "source_tree": bundle["source"]["git_tree"],
        "selection_identity": bundle["selection"]["core0a_selection_identity"],
        "selection_count": EXPECTED_EXECUTION_COUNT,
        **dict(deployment),
        "checkpoint_interval_records": CORE0A_DEPLOYMENT_POLICY[
            "checkpoint_interval_records"
        ],
        "retry_contract": CORE0A_RETRY_CONTRACT,
        "expected_output_namespace": CORE0A_OUTPUT_NAMESPACE,
        "formal_authorization": False,
        "production_authorization": False,
    }
    return {
        **material,
        "deployment_manifest_identity": domain_hash(
            CORE0A_DEPLOYMENT_MANIFEST_DOMAIN, material,
        ),
    }


def validate_autodl_deployment_manifest_v2(
    value: Mapping[str, Any], bundle: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RTA4Core0APilotV2Error("AutoDL deployment must be a mapping")
    document = dict(value)
    unsigned = dict(document)
    observed = unsigned.pop("deployment_manifest_identity", None)
    if (
        document.get("deployment_manifest_schema")
        != CORE0A_DEPLOYMENT_MANIFEST_SCHEMA
        or document.get("portable_freeze_identity")
        != bundle.get("portable_freeze_identity")
        or document.get("source_commit") != bundle.get("source", {}).get(
            "git_commit"
        )
        or document.get("source_tree") != bundle.get("source", {}).get(
            "git_tree"
        )
        or observed != domain_hash(CORE0A_DEPLOYMENT_MANIFEST_DOMAIN, unsigned)
    ):
        raise RTA4Core0APilotV2Error(
            "AutoDL deployment identity/source mismatch"
        )
    return document


def core0a_execution_identity(
    bundle: Mapping[str, Any], deployment_manifest: Mapping[str, Any],
) -> str:
    deployment = validate_autodl_deployment_manifest_v2(
        deployment_manifest, bundle,
    )
    return domain_hash(CORE0A_EXECUTION_IDENTITY_DOMAIN, {
        "portable_freeze_identity": bundle["portable_freeze_identity"],
        "deployment_manifest_identity": deployment[
            "deployment_manifest_identity"
        ],
        "selection_identity": bundle["selection"]["core0a_selection_identity"],
        "selection_count": EXPECTED_EXECUTION_COUNT,
    })


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
    bundle: Mapping[str, Any], authorization: Mapping[str, Any] | None,
    deployment_manifest: Mapping[str, Any] | None,
) -> None:
    if authorization is None or deployment_manifest is None:
        raise RTA4Core0APilotV2Error(
            "CORE-0A execution requires independent authorization and deployment"
        )
    exact = {
        "authorization_schema", "status", "independent_read_only_review",
        "review_identity", "portable_freeze_identity", "selection_identity",
        "source_commit", "source_tree", "deployment_manifest_identity",
        "execution_identity", "output_namespace", "authorized_execution_count",
        "max_runs", "scope", "formal_authorization",
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
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization source/scope drift")
    if authorization.get("deployment_manifest_identity") != deployment_manifest.get(
        "deployment_manifest_identity"
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization deployment drift")
    if authorization.get("execution_identity") != core0a_execution_identity(
        bundle, deployment_manifest,
    ):
        raise RTA4Core0APilotV2Error("CORE-0A authorization execution drift")
    if (
        authorization.get("authorized_execution_count")
        != EXPECTED_EXECUTION_COUNT
        or authorization.get("max_runs") != 1
        or authorization.get("scope") != "EXACT_384_RECORD_CORE0A_ONLY"
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
    "AUTHORIZED_CORE0A_ENGINEERING_PILOT", "CORE0A_CONTRACT_VERSION",
    "CANDIDATE_CONFIG_PATH", "CORE0A_AUTHORIZATION_SCHEMA",
    "CORE0A_DEPLOYMENT_MANIFEST_SCHEMA", "CORE0A_DEPLOYMENT_POLICY",
    "CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT",
    "CORE0A_HANDOFF_SCHEMA", "CORE0A_OUTPUT_NAMESPACE",
    "CORE0A_PORTABLE_BUNDLE_SCHEMA", "CORE0A_RETRY_CONTRACT",
    "CORE0A_SELECTION_ALGORITHM", "CORE0A_SELECTION_SCHEMA",
    "CORE0A_TASKSET_STORE_NAMESPACE",
    "EXPECTED_EXECUTION_COUNT", "FORMAL_INPUT_SOURCE_COMMIT",
    "FORMAL_INPUT_SOURCE_TREE", "HISTORICAL_CORE_RECORD_COUNTS",
    "HISTORICAL_ORDERED_SELECTION_IDENTITY", "HISTORICAL_SELECTION_SEED",
    "HISTORICAL_SELECTION_SOURCE_SHA256", "PROJECT_ROOT",
    "RTA4Core0APilotV2Error", "SELECTION_ARTIFACT_PATH",
    "UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE",
    "V1_CONFIG_PATHS", "V2_CONFIG_PATHS", "build_core0a_selection_v2",
    "build_autodl_deployment_manifest_v2", "build_autodl_handoff_v2",
    "build_portable_candidate_bundle_v2", "core0a_execution_identity",
    "canonical_json_bytes", "coverage_matrix", "load_core0a_selection_v2",
    "load_candidate_config_v2", "load_strict_canonical_json",
    "repository_identity", "require_authorized_core0a_engineering_pilot",
    "scientific_analysis_identity", "selected_ordinals_by_core",
    "terminal_evidence_identity", "validate_autodl_handoff_v2",
    "validate_autodl_deployment_manifest_v2",
    "validate_core0a_selection_v2", "validate_portable_candidate_bundle_v2",
    "write_canonical_json",
]
