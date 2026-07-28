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
from typing import Any, Dict, Iterable, Mapping, Sequence

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
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256
from .rta4_shared_energy import (
    BETA_CONTRACT_VERSION,
    HORIZON_CONTRACT_VERSION,
    SERVICE_SPEC_DOMAIN,
)
from .rta4_taskset_v2 import RTA4_GENERATION_DOMAIN_V2


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


__all__ = [
    "AUTHORIZED_CORE0A_ENGINEERING_PILOT", "CORE0A_CONTRACT_VERSION",
    "CORE0A_ENGINEERING_PILOT_FROZEN_PENDING_REAUDIT",
    "CORE0A_SELECTION_ALGORITHM", "CORE0A_SELECTION_SCHEMA",
    "EXPECTED_EXECUTION_COUNT", "FORMAL_INPUT_SOURCE_COMMIT",
    "FORMAL_INPUT_SOURCE_TREE", "HISTORICAL_CORE_RECORD_COUNTS",
    "HISTORICAL_ORDERED_SELECTION_IDENTITY", "HISTORICAL_SELECTION_SEED",
    "HISTORICAL_SELECTION_SOURCE_SHA256", "PROJECT_ROOT",
    "RTA4Core0APilotV2Error", "SELECTION_ARTIFACT_PATH",
    "UNAUTHORIZED_ENGINEERING_PILOT_CANDIDATE",
    "V1_CONFIG_PATHS", "V2_CONFIG_PATHS", "build_core0a_selection_v2",
    "canonical_json_bytes", "coverage_matrix", "load_core0a_selection_v2",
    "load_strict_canonical_json", "validate_core0a_selection_v2",
    "write_canonical_json",
]
