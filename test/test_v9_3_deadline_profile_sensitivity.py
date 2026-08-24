from fractions import Fraction
from types import SimpleNamespace

import pytest

from experiments.v9_3.deadline_profile_sensitivity import (
    DeadlineProfileError,
    PROFILE_ORDER,
    project_deadline,
    project_profiles,
    project_taskset,
    project_task_payload,
    validate_implicit_priority_order,
    validate_same_projected_material,
)
from scripts.run_deadline_profile_sensitivity import build_requests
from scripts.run_deadline_profile_sensitivity import validate_implicit_outcomes


def _base_taskset():
    payload = (
        {
            "task_id": "0", "source_name": "task-0", "priority_rank": 0,
            "C": 20, "D": 60, "T": 100, "P": "5",
            "D_over_T": "3/5", "workload": "hash", "arrival_offset": 0,
        },
        {
            "task_id": "1", "source_name": "task-1", "priority_rank": 1,
            "C": 10, "D": 25, "T": 120, "P": "3",
            "D_over_T": "5/24", "workload": "bzip2", "arrival_offset": 0,
        },
    )
    return SimpleNamespace(
        taskset_id="base-taskset-0", semantic_hash="base-hash-0",
        taskset_index=0, seed=20260823, processors=4, task_count=2,
        target_utilization=Fraction(3, 10), actual_utilization=Fraction(3, 10),
        task_payload=payload,
    )


def test_normalized_slack_projection_uses_exact_floor():
    assert project_deadline(20, 100, Fraction(1, 4)) == 40
    assert project_deadline(20, 100, Fraction(1, 2)) == 60
    assert project_deadline(20, 100, Fraction(3, 4)) == 80
    assert project_deadline(20, 100, Fraction(1)) == 100


def test_projection_bounds_and_monotonicity():
    deadlines = [
        project_deadline(10, 37, lam)
        for lam in (Fraction(1, 4), Fraction(1, 2), Fraction(3, 4), Fraction(1))
    ]
    assert deadlines == sorted(deadlines)
    assert all(10 <= deadline <= 37 for deadline in deadlines)


def test_equal_c_and_t_projects_to_t_for_every_profile():
    assert [
        project_deadline(40, 40, lam)
        for lam in (Fraction(1, 4), Fraction(1, 2), Fraction(3, 4), Fraction(1))
    ] == [40, 40, 40, 40]


def test_projection_rejects_non_exact_or_invalid_deadline_inputs():
    with pytest.raises(DeadlineProfileError):
        project_deadline(20, 100, 0.25)
    with pytest.raises(DeadlineProfileError):
        project_deadline(21, 20, Fraction(1, 2))


def test_one_base_taskset_is_paired_across_four_profiles_and_only_d_changes():
    base = _base_taskset()
    profiles = project_profiles(base)
    assert tuple(item.deadline_profile for item in profiles) == PROFILE_ORDER
    assert [row["D"] for row in profiles[0].task_payload] == [40, 37]
    assert [row["D"] for row in profiles[1].task_payload] == [60, 65]
    assert [row["D"] for row in profiles[2].task_payload] == [80, 92]
    assert [row["D"] for row in profiles[3].task_payload] == [100, 120]
    for rows in zip(*(item.task_payload for item in profiles)):
        for row in rows[1:]:
            assert {
                key: value for key, value in row.items()
                if key not in {"D", "D_over_T"}
            } == {
                key: value for key, value in rows[0].items()
                if key not in {"D", "D_over_T"}
            }


def test_projected_hashes_are_profile_specific():
    profiles = project_profiles(_base_taskset())
    assert len({item.projected_taskset_hash for item in profiles}) == 4
    assert len({item.projected_taskset_id for item in profiles}) == 4


def test_same_profile_rm_and_dm_share_projected_material():
    base = _base_taskset()
    rm = project_taskset(base, "MEDIUM")
    dm = project_taskset(base, "MEDIUM")
    validate_same_projected_material(rm, dm)


def test_implicit_profile_has_identical_rm_and_dm_order():
    implicit = project_profiles(_base_taskset())[-1]
    validate_implicit_priority_order(implicit)


def test_implicit_priority_check_fails_closed_when_order_differs():
    base = _base_taskset()
    invalid = [dict(row) for row in base.task_payload]
    invalid[1]["T"] = 80
    base.task_payload = tuple(invalid)
    implicit = project_taskset(base, "IMPLICIT")
    with pytest.raises(DeadlineProfileError, match="priority orders differ"):
        validate_implicit_priority_order(implicit)


def test_projection_does_not_mutate_base_payload():
    base = _base_taskset()
    original = tuple(dict(row) for row in base.task_payload)
    project_task_payload(base.task_payload, "TIGHT")
    assert tuple(dict(row) for row in base.task_payload) == original


def test_exploratory_request_plan_is_one_cell_480_requests():
    bases = []
    for index in range(20):
        base = _base_taskset()
        base.taskset_id = f"base-taskset-{index}"
        bases.append(base)
    projected = [profile for base in bases for profile in project_profiles(base)]
    requests = build_requests(projected)
    assert len(requests) == 480
    assert len({row["request_id"] for row in requests}) == 480
    assert {row["deadline_profile"] for row in requests} == set(PROFILE_ORDER)
    assert {row["priority_policy"] for row in requests} == {"RM", "DM"}
    assert {row["scheduler"] for row in requests} == {
        "ASAP-BLOCK", "ASAP-NONBLOCK", "ST-NONBLOCK",
    }


def _implicit_outcome_rows(*, dm_wholepass=True, dm_deadline_miss=False):
    rows = []
    for index in range(20):
        for scheduler in ("ASAP-BLOCK", "ASAP-NONBLOCK", "ST-NONBLOCK"):
            rows.extend([
                {
                    "base_taskset_id": f"base-taskset-{index}",
                    "deadline_profile": "IMPLICIT", "scheduler": scheduler,
                    "priority_policy": "RM", "wholepass": True,
                    "deadline_miss": False,
                },
                {
                    "base_taskset_id": f"base-taskset-{index}",
                    "deadline_profile": "IMPLICIT", "scheduler": scheduler,
                    "priority_policy": "DM", "wholepass": dm_wholepass,
                    "deadline_miss": dm_deadline_miss,
                },
            ])
    return rows


def test_implicit_outcome_invariant_accepts_paired_rm_and_dm():
    validate_implicit_outcomes(_implicit_outcome_rows())


def test_implicit_outcome_invariant_fails_closed_on_rm_dm_difference():
    with pytest.raises(RuntimeError, match="wholepass invariant"):
        validate_implicit_outcomes(_implicit_outcome_rows(dm_wholepass=False))
