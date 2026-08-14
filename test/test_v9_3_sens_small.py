from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from experiments.v9_3.sens_small import (
    E0,
    METHODS,
    RHO,
    SENS_SMALL_SEED_NAMESPACE,
    TARGET_UE,
    U_C_VALUES,
    condition_taskset,
    conditions,
    make_requests,
    plan_rows,
    plan_summary,
    stable_sens_seed,
)


def _skeleton():
    return {
        "skeleton_id": "synthetic-skeleton",
        "target_uc": "3/10",
        "actual_uc": "3/10",
        "target_ue": "4/5",
        "actual_ue": "4/5",
        "generation_index": 0,
        "seed": 123,
        "tasks": [
            {"name": "t0", "priority": 0, "C": 2, "D": 5, "T": 10,
             "workload": "hash", "base_energy_per_tick": "1", "energy_per_tick": "2"},
            {"name": "t1", "priority": 1, "C": 1, "D": 8, "T": 20,
             "workload": "encrypt", "base_energy_per_tick": "1", "energy_per_tick": "3"},
        ],
    }


def test_frozen_scientific_contract_and_plan_counts():
    summary = plan_summary()
    assert U_C_VALUES == (Fraction(3, 10), Fraction(7, 10))
    assert TARGET_UE == Fraction(4, 5)
    assert E0 == 0
    assert RHO == Fraction(11, 2)
    assert summary["U_C_POINTS"] == 2
    assert summary["SKELETONS_PER_UC"] == 300
    assert summary["UNIQUE_SKELETONS"] == 600
    assert summary["CONDITIONS_PER_SKELETON"] == 5
    assert summary["METHODS"] == 4
    assert summary["REQUESTS"] == 12000
    assert summary["CENTER_REQUESTS"] == 2400
    assert summary["DEADLINE_AXIS_UNIQUE_CONDITIONS"] == 3
    assert summary["LATENCY_AXIS_UNIQUE_CONDITIONS"] == 3
    assert summary["PAIRING"] == "PASS"


def test_conditions_have_one_center_and_exact_axes():
    rows = conditions()
    assert [row.name for row in rows] == ["D_LOW", "CENTER", "D_HIGH", "L_LOW", "L_HIGH"]
    assert [row.deadline_slack_fraction for row in rows[:3]] == [Fraction(1, 2), Fraction(3, 4), Fraction(1)]
    assert [row.latency for row in rows[:3]] == [Fraction(2, 5)] * 3
    assert [row.latency for row in rows[3:]] == [Fraction(0), Fraction(2)]
    assert sum(row.name == "CENTER" for row in rows) == 1
    assert set(rows[1].views) == {"deadline", "latency"}


def test_plan_request_identity_and_pairing_are_unique():
    rows = plan_rows()
    assert len(rows) == 12000
    assert len({row["request_id"] for row in rows}) == 12000
    assert {row["method"] for row in rows} == set(METHODS)
    assert all(row["e0"] == "0" for row in rows)
    assert all(row["target_uc"] in {"3/10", "7/10"} for row in rows)


def test_deadline_mapping_is_the_existing_exact_floor_formula():
    derived = [condition_taskset(_skeleton(), condition) for condition in conditions()[:3]]
    assert [row["tasks"][0]["D"] for row in derived] == [6, 8, 10]
    assert [row["tasks"][1]["D"] for row in derived] == [10, 15, 20]
    assert derived[0]["tasks"][0]["C"] == derived[2]["tasks"][0]["C"]
    assert derived[0]["tasks"][0]["T"] == derived[2]["tasks"][0]["T"]


def test_service_latency_changes_only_service_material():
    skeleton = _skeleton()
    center = condition_taskset(skeleton, conditions()[1])
    low = condition_taskset(skeleton, conditions()[3])
    high = condition_taskset(skeleton, conditions()[4])
    for taskset in (low, high):
        assert taskset["tasks"] == center["tasks"]
        assert taskset["rho"] == center["rho"] == "11/2"
        assert taskset["e0"] == center["e0"] == "0"
        assert taskset["actual_ue"] == center["actual_ue"] == "4/5"
    assert low["latency"] == "0"
    assert center["latency"] == "2/5"
    assert high["latency"] == "2"


def test_same_skeleton_conditions_keep_energy_demand_and_seed():
    tasksets = [condition_taskset(_skeleton(), condition) for condition in conditions()]
    assert len({row["skeleton_id"] for row in tasksets}) == 1
    assert len({row["seed"] for row in tasksets}) == 1
    assert len({_energy_signature(row) for row in tasksets}) == 1
    assert {row["condition"] for row in tasksets} == {row.name for row in conditions()}


def _energy_signature(taskset):
    return tuple((task["C"], task["T"], task["energy_per_tick"], task["workload"], task["priority"]) for task in taskset["tasks"])


def test_smoke_request_expansion_has_20_requests():
    tasksets = [condition_taskset(_skeleton(), condition) for condition in conditions()]
    requests = make_requests(tasksets, timeout=30.0)
    assert len(requests) == 20
    assert len({row["request_id"] for row in requests}) == 20
    assert {row["metadata"]["condition"] for row in requests} == {row.name for row in conditions()}
    assert {row["metadata"]["method"] for row in requests} == set(METHODS)


def test_seed_namespace_is_stable_and_isolated():
    assert SENS_SMALL_SEED_NAMESPACE == "ASAP_BLOCK_V9_3_SENS_SMALL_V41"
    assert stable_sens_seed(Fraction(3, 10), 0) == stable_sens_seed(Fraction(3, 10), 0)
    assert stable_sens_seed(Fraction(3, 10), 0) != stable_sens_seed(Fraction(3, 10), 1)
