from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from experiments.common.exact_service_curve import (
    EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
)
from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_formal_config_v3 import (
    RTA4_SELECTION_RULE_V3,
    RTA4_TASKSET_FIRST_SELECTION_RULE_V3,
    RTA4FormalConfigV3Error,
    formal_taskset_store_identity_v3,
    normalize_rta4_campaign_v3,
    rta4_formal_config_hash_v3,
)
from experiments.v9_3.rta4_formal_config_v5 import (
    RTA4FormalConfigV5Error,
    normalize_rta4_campaign_v5,
)
from experiments.v9_3.rta4_formal_plan_v3 import (
    RTA4_CORE5B_SELECTION_DOMAIN_V3,
    describe_formal_plan_v3,
    iter_formal_plan_v3,
)
from experiments.v9_3.rta4_formal_plan_v5 import (
    RTA4FormalPlanV5Error,
    describe_formal_plan_v5,
    iter_formal_plan_v5,
    validate_source_dependency_v5,
)
from experiments.v9_3.rta4_task_source_v4 import (
    EXPLICIT_MANIFEST_SCHEMA_V1,
    EXPLICIT_TASKSET_MANIFEST,
    PRIORITY_POLICY_RM,
)
from scripts.create_v9_3_rta4_campaign import campaign_template


METHODS = [
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
]
UTILIZATIONS = [
    "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5",
]
STRATA = ["3/10", "2/5", "1/2", "3/5", "7/10"]
SERVICE = {
    "model": EXACT_RATE_LATENCY_SERVICE_CURVE_V1,
    "rate": "1/10",
    "latency": "1",
    "time_unit": "tick",
}


def _legacy_v3_raw() -> dict:
    raw = campaign_template("CORE-5B")
    raw["campaign_id"] = "legacy-core5b-baseline-audit"
    raw["source"] = deepcopy(raw["source"])
    raw["source"]["taskset_count"] = 2
    raw["utilization_strata"] = ["1/2"]
    raw["candidates_per_method_stratum"] = 3
    raw["selected_per_method_stratum"] = 2
    raw["methods"] = ["CW_THETA_CW"]
    raw["workers"] = [1, 2]
    return raw


def _formal_core1_v3_raw() -> dict:
    raw = campaign_template("CORE-5B")
    raw["campaign_id"] = "core5b-e1-taskset-first-regression"
    raw["source"] = {
        "core": "CORE-1",
        "source_scope": "CORE1_TASKSET_STORE",
        "source_campaign_config_sha256": "1" * 64,
        "source_plan_sha256": "2" * 64,
        "source_taskset_store_identity": "3" * 64,
        "taskset_count": 800,
    }
    raw["utilization_strata"] = list(STRATA)
    raw["candidates_per_method_stratum"] = 100
    raw["selected_per_method_stratum"] = 30
    raw["methods"] = list(METHODS)
    raw["workers"] = [1, 2, 4, 8]
    return raw


def test_legacy_core4_core5b_config_plan_and_stream_identities_are_unchanged():
    scientific = normalize_rta4_campaign_v3(
        _legacy_v3_raw()
    )["normalized_scientific_config"]
    plan = describe_formal_plan_v3(scientific)

    assert scientific["selection_rule"] == RTA4_SELECTION_RULE_V3
    assert scientific["fixed_semantics"]["source_tasksets"] == (
        "CORE-4_BASELINE_HASH_BOUND_REUSE"
    )
    assert rta4_formal_config_hash_v3(scientific) == (
        "716d2b46bf6366cf25d1c0d77b5618b7e21fbfa863dfc074486e6e811d4ad273"
    )
    assert plan["plan_sha256"] == (
        "24aff095fdc1d01076793164be591961972e8fe79f4e790d6ae60cf38693c92d"
    )
    assert plan["ordered_stream_digest"] == (
        "935784e5d8868cc9c14933cf6f5e8beecaa8c7d3d52f1ac1296256eefc8f3c7e"
    )
    assert (
        plan["taskset_skeleton_count"],
        plan["mathematical_request_count"],
        plan["ordered_stream_count"],
    ) == (2, 2, 4)


def test_core1_core5b_config_is_explicit_and_other_source_pairs_fail_closed():
    raw = _formal_core1_v3_raw()
    scientific = normalize_rta4_campaign_v3(raw)["normalized_scientific_config"]
    assert scientific["selection_rule"] == RTA4_TASKSET_FIRST_SELECTION_RULE_V3
    assert scientific["fixed_semantics"] == {
        **{
            key: value for key, value in scientific["fixed_semantics"].items()
            if key not in {
                "source_tasksets", "independent_taskset_generation",
                "selection_unit", "selection_rule",
                "selection_depends_on_results",
            }
        },
        "source_tasksets": "CORE-1_EXPERIMENT-1_HASH_BOUND_REUSE",
        "independent_taskset_generation": False,
        "selection_unit": "TASKSET_BEFORE_METHOD_CARTESIAN_PRODUCT",
        "selection_rule": RTA4_TASKSET_FIRST_SELECTION_RULE_V3,
        "selection_depends_on_results": False,
    }

    for core, scope in (
        ("CORE-1", "CORE4_BASELINE"),
        ("CORE-4", "CORE1_TASKSET_STORE"),
        ("CORE-2", "CORE1_TASKSET_STORE"),
    ):
        invalid = deepcopy(raw)
        invalid["source"]["core"] = core
        invalid["source"]["source_scope"] = scope
        with pytest.raises(RTA4FormalConfigV3Error):
            normalize_rta4_campaign_v3(invalid)


def test_core1_core5b_selects_tasksets_once_then_expands_methods_and_workers():
    raw = _formal_core1_v3_raw()
    scientific = normalize_rta4_campaign_v3(raw)["normalized_scientific_config"]
    plan = describe_formal_plan_v3(scientific)
    records = tuple(iter_formal_plan_v3(scientific))

    assert (
        plan["taskset_skeleton_count"],
        plan["mathematical_request_count"],
        plan["ordered_stream_count"],
    ) == (150, 600, 2400)
    assert len({record.taskset_skeleton_slot_id for record in records}) == 150
    assert len({record.mathematical_request_id for record in records}) == 600
    assert all(
        record.material["namespace"]
        == "RTA4_CORE5B_SELECTED_CORE1_EXPERIMENT1_V3"
        for record in records
    )

    tasksets_by_method = defaultdict(set)
    workers_by_math = defaultdict(set)
    hashes_by_taskset = defaultdict(set)
    first_seen_by_stratum = defaultdict(list)
    seen = set()
    forbidden_result_fields = {
        "solver_status", "certification_status", "response_time", "runtime",
        "timeout", "ph_triggered", "seq_triggered", "improvement",
    }
    for record in records:
        key = (
            record.material["utilization_stratum"],
            record.material["candidate_index"],
        )
        tasksets_by_method[record.material["method"]].add(key)
        workers_by_math[record.mathematical_request_id].add(
            record.material["worker_count"]
        )
        hashes_by_taskset[key].add(record.material["selection_hash"])
        assert forbidden_result_fields.isdisjoint(record.material)
        assert {
            "repeat_index", "repetition_index", "repetition",
        }.isdisjoint(record.material)
        if key not in seen:
            first_seen_by_stratum[key[0]].append(
                (record.material["selection_hash"], key[1])
            )
            seen.add(key)

    assert set(tasksets_by_method) == set(METHODS)
    assert len({frozenset(value) for value in tasksets_by_method.values()}) == 1
    assert all(len(value) == 150 for value in tasksets_by_method.values())
    assert all(value == {1, 2, 4, 8} for value in workers_by_math.values())
    assert all(len(value) == 1 for value in hashes_by_taskset.values())

    source = scientific["source"]
    for stratum in STRATA:
        expected = sorted(
            (
                domain_hash(RTA4_CORE5B_SELECTION_DOMAIN_V3, {
                    "selection_rule": RTA4_TASKSET_FIRST_SELECTION_RULE_V3,
                    "source_campaign_config_sha256": source[
                        "source_campaign_config_sha256"
                    ],
                    "source_plan_sha256": source["source_plan_sha256"],
                    "source_taskset_store_identity": source[
                        "source_taskset_store_identity"
                    ],
                    "utilization_stratum": stratum,
                    "candidate_index": candidate,
                }),
                candidate,
            )
            for candidate in range(100)
        )[:30]
        assert first_seen_by_stratum[stratum] == expected


def test_worker_order_does_not_change_core1_core5b_mathematical_requests():
    first_raw = _formal_core1_v3_raw()
    second_raw = deepcopy(first_raw)
    second_raw["workers"] = list(reversed(second_raw["workers"]))
    first = normalize_rta4_campaign_v3(first_raw)["normalized_scientific_config"]
    second = normalize_rta4_campaign_v3(second_raw)["normalized_scientific_config"]

    assert {
        record.mathematical_request_id for record in iter_formal_plan_v3(first)
    } == {
        record.mathematical_request_id for record in iter_formal_plan_v3(second)
    }


def _taskset_row(index: int) -> dict:
    return {
        "taskset_id": f"experiment1-{index:04d}",
        "source_seed": 10000 + index,
        "tasks": [
            {
                "name": "tau_1", "C": 1, "D": 4 + index % 2,
                "T": 10, "power": "1/10",
            },
            {
                "name": "tau_2", "C": 2, "D": 12 + index % 3,
                "T": 20, "power": "1/5",
            },
        ],
    }


def _write_manifest(
    path: Path, rows: list[dict], *, task_order: list[str] | None = None,
) -> Path:
    path.write_text(yaml.safe_dump({
        "schema": EXPLICIT_MANIFEST_SCHEMA_V1,
        "processors": 4,
        "priority_policy": PRIORITY_POLICY_RM,
        "task_count": 2,
        "taskset_count": len(rows),
        "task_order": ["tau_1", "tau_2"] if task_order is None else task_order,
        "tasksets": rows,
    }, sort_keys=False), encoding="utf-8")
    return path


def _task_source(path: Path) -> dict:
    return {
        "mode": EXPLICIT_TASKSET_MANIFEST,
        "manifest_path": str(path),
    }


def _source_v5_raw(path: Path) -> dict:
    raw = campaign_template("CORE-1")
    raw.update({
        "campaign_id": "experiment1-source-regression",
        "processors": 4,
        "task_count": 2,
        "normalized_utilization": list(UTILIZATIONS),
        "tasksets_per_utilization": 100,
        "e0": ["37"],
        "methods": list(METHODS),
        "task_source": _task_source(path),
        "service_curve": deepcopy(SERVICE),
        "runtime": {},
    })
    return raw


def _dependent_v5_raw(path: Path, source: dict) -> dict:
    source_v3 = source["v3_scientific_config"]
    source_plan = describe_formal_plan_v3(source_v3)
    raw = _formal_core1_v3_raw()
    raw.update({
        "source": {
            "core": "CORE-1",
            "source_scope": "CORE1_TASKSET_STORE",
            "source_campaign_config_sha256": rta4_formal_config_hash_v3(
                source_v3
            ),
            "source_plan_sha256": source_plan["plan_sha256"],
            "source_taskset_store_identity": formal_taskset_store_identity_v3(
                source_v3
            ),
            "taskset_count": 800,
        },
        "source_baseline_exact_e0": "37",
        "task_source": _task_source(path),
        "service_curve": deepcopy(SERVICE),
        "runtime": {},
    })
    return raw


def _normalized_dependency(tmp_path: Path) -> tuple[dict, dict, list[dict]]:
    source_rows = [_taskset_row(index) for index in range(800)]
    source_path = _write_manifest(tmp_path / "experiment1.yaml", source_rows)
    candidate_rows = deepcopy(source_rows[200:700])
    candidate_path = _write_manifest(tmp_path / "candidates.yaml", candidate_rows)
    source = normalize_rta4_campaign_v5(_source_v5_raw(source_path))
    dependent = normalize_rta4_campaign_v5(
        _dependent_v5_raw(candidate_path, source)
    )
    return source, dependent, candidate_rows


def _validate_dependency(source: dict, dependent: dict) -> None:
    validate_source_dependency_v5(
        dependent["normalized_scientific_config"],
        source["normalized_scientific_config"],
        source["task_sources"],
        source["service_curve"],
        dependent["task_sources"],
    )


def test_v5_accepts_exact_500_of_800_experiment1_subset_and_preserves_material(
    tmp_path,
):
    source, dependent, _ = _normalized_dependency(tmp_path)
    _validate_dependency(source, dependent)

    scientific = dependent["normalized_scientific_config"]
    plan = describe_formal_plan_v5(
        scientific, dependent["task_sources"], dependent["service_curve"],
    )
    records = tuple(iter_formal_plan_v5(
        scientific, dependent["task_sources"], dependent["service_curve"],
    ))
    assert (
        plan["taskset_skeleton_count"],
        plan["mathematical_request_count"],
        plan["ordered_stream_count"],
    ) == (150, 600, 2400)
    assert all(
        record.material["v3_grid_material"]["exact_e0"] == "37"
        for record in records
    )
    assert all(
        record.configured_service_identity == record.effective_service_identity
        == dependent["service_curve"].identity
        for record in records
    )

    material_by_taskset = defaultdict(set)
    for record in records:
        key = record.material["taskset_source_index"]
        grid = record.material["v3_grid_material"]
        material_by_taskset[key].add((
            record.taskset_identity,
            record.material["taskset_content_sha256"],
            record.material["task_order_sha256"],
            grid["selection_hash"],
            grid["exact_e0"],
            record.configured_service_identity,
            record.effective_service_identity,
        ))
    assert len(material_by_taskset) == 150
    assert all(len(value) == 1 for value in material_by_taskset.values())


@pytest.mark.parametrize("field", ["C", "D", "T", "power", "source_seed"])
def test_v5_dependency_rejects_any_candidate_task_material_change(tmp_path, field):
    source, dependent, candidate_rows = _normalized_dependency(tmp_path)
    changed = deepcopy(candidate_rows)
    if field == "source_seed":
        changed[0][field] += 1
    elif field == "power":
        changed[0]["tasks"][0][field] = "1/9"
    else:
        changed[0]["tasks"][0][field] += 1
    path = _write_manifest(tmp_path / f"changed-{field}.yaml", changed)
    changed_dependent = normalize_rta4_campaign_v5(
        _dependent_v5_raw(path, source)
    )
    with pytest.raises(RTA4FormalPlanV5Error, match="exact ordered"):
        _validate_dependency(source, changed_dependent)


def test_v5_dependency_rejects_task_order_reorder_missing_and_duplicate(tmp_path):
    source, dependent, candidate_rows = _normalized_dependency(tmp_path)

    reordered = deepcopy(candidate_rows)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    reordered_path = _write_manifest(tmp_path / "reordered.yaml", reordered)
    reordered_dependent = normalize_rta4_campaign_v5(
        _dependent_v5_raw(reordered_path, source)
    )
    with pytest.raises(RTA4FormalPlanV5Error, match="exact ordered"):
        _validate_dependency(source, reordered_dependent)

    missing_path = _write_manifest(tmp_path / "missing.yaml", candidate_rows[:-1])
    with pytest.raises(RTA4FormalConfigV5Error, match="exactly 500"):
        normalize_rta4_campaign_v5(_dependent_v5_raw(missing_path, source))

    duplicated = deepcopy(candidate_rows)
    duplicated[-1] = deepcopy(duplicated[0])
    duplicate_path = _write_manifest(tmp_path / "duplicate.yaml", duplicated)
    with pytest.raises(RTA4FormalConfigV5Error, match="not unique"):
        normalize_rta4_campaign_v5(_dependent_v5_raw(duplicate_path, source))

    renamed = deepcopy(candidate_rows)
    for row in renamed:
        row["tasks"][0]["name"] = "tau_0"
    renamed_path = _write_manifest(
        tmp_path / "task-order.yaml", renamed, task_order=["tau_0", "tau_2"],
    )
    renamed_dependent = normalize_rta4_campaign_v5(
        _dependent_v5_raw(renamed_path, source)
    )
    with pytest.raises(RTA4FormalPlanV5Error, match="exact ordered"):
        _validate_dependency(source, renamed_dependent)


@pytest.mark.parametrize("hash_field", [
    "source_campaign_config_sha256",
    "source_plan_sha256",
    "source_taskset_store_identity",
])
def test_v5_dependency_rejects_each_v3_source_hash_drift(tmp_path, hash_field):
    source, dependent, candidate_rows = _normalized_dependency(tmp_path)
    path = _write_manifest(tmp_path / f"hash-{hash_field}.yaml", candidate_rows)
    raw = _dependent_v5_raw(path, source)
    raw["source"][hash_field] = "f" * 64
    drifted = normalize_rta4_campaign_v5(raw)
    with pytest.raises(RTA4FormalPlanV5Error, match="hashes drifted"):
        _validate_dependency(source, drifted)


def test_v5_dependency_rejects_utilization_mapping_and_service_drift(tmp_path):
    source, dependent, candidate_rows = _normalized_dependency(tmp_path)
    path = _write_manifest(tmp_path / "mapping-service.yaml", candidate_rows)

    mapping_raw = _dependent_v5_raw(path, source)
    mapping_raw["utilization_strata"] = list(reversed(STRATA))
    mapping = normalize_rta4_campaign_v5(mapping_raw)
    with pytest.raises(RTA4FormalPlanV5Error, match="exact ordered"):
        _validate_dependency(source, mapping)

    service_raw = _dependent_v5_raw(path, source)
    service_raw["service_curve"]["rate"] = "1/9"
    service = normalize_rta4_campaign_v5(service_raw)
    with pytest.raises(RTA4FormalPlanV5Error, match="service differs"):
        _validate_dependency(source, service)


def test_v5_core1_dependency_requires_actual_candidate_binding(tmp_path):
    source, dependent, _ = _normalized_dependency(tmp_path)
    with pytest.raises(RTA4FormalPlanV5Error, match="exact candidate"):
        validate_source_dependency_v5(
            dependent["normalized_scientific_config"],
            source["normalized_scientific_config"],
            source["task_sources"],
            source["service_curve"],
        )
