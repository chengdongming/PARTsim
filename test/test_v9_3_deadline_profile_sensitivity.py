from fractions import Fraction
from types import SimpleNamespace

import pytest

from experiments.v9_3.deadline_profile_sensitivity import (
    DeadlineProfileError,
    PROFILE_ORDER,
    project_profiles_from_original_deadline,
    project_deadline,
    project_relaxed_deadline,
    project_profiles,
    project_taskset,
    project_task_payload,
    validate_implicit_priority_order,
    validate_same_projected_material,
)
from scripts.run_deadline_profile_sensitivity import (
    _build_energy_material,
    build_requests,
    parse_alphas,
    parse_priority_policies,
    validate_implicit_outcomes,
)


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


def _relax_base_taskset():
    base = _base_taskset()
    rows = [dict(row) for row in base.task_payload]
    rows[0]["D"] = 40
    rows[0]["D_over_T"] = "2/5"
    base.task_payload = tuple(rows)
    return base


def test_relax_original_formula_uses_each_task_original_deadline():
    assert project_relaxed_deadline(20, 40, 100, Fraction(0)) == 40
    assert project_relaxed_deadline(20, 40, 100, Fraction(1, 3)) == 60
    assert project_relaxed_deadline(20, 40, 100, Fraction(2, 3)) == 80
    assert project_relaxed_deadline(20, 40, 100, Fraction(1)) == 100


def test_relax_original_bounds_and_monotonicity():
    values = [
        project_relaxed_deadline(10, 23, 37, alpha)
        for alpha in (Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1))
    ]
    assert values == [23, 27, 32, 37]
    assert values == sorted(values)
    assert all(10 <= value <= 37 for value in values)


def test_alpha_zero_is_exact_base_and_alpha_one_is_implicit():
    base = _relax_base_taskset()
    profiles = project_profiles_from_original_deadline(
        base, (Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1)),
    )
    assert profiles[0].deadline_profile == "ORIGINAL"
    assert profiles[0].task_payload == base.task_payload
    assert profiles[-1].deadline_profile == "IMPLICIT"
    assert all(row["D"] == row["T"] for row in profiles[-1].task_payload)


def test_relax_profiles_preserve_heterogeneous_original_deadlines_and_identity():
    base = _relax_base_taskset()
    profiles = project_profiles_from_original_deadline(
        base, (Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1)),
    )
    assert [row["D"] for row in profiles[1].task_payload] == [60, 56]
    assert len({profile.projected_taskset_hash for profile in profiles}) == 4
    for rows in zip(*(profile.task_payload for profile in profiles)):
        for row in rows[1:]:
            assert {
                key: value for key, value in row.items()
                if key not in {"D", "D_over_T"}
            } == {
                key: value for key, value in rows[0].items()
                if key not in {"D", "D_over_T"}
            }


def test_alpha_parser_is_exact_ordered_and_fail_closed():
    assert parse_alphas("0,1/3,2/3,1") == (
        Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1),
    )
    for invalid in ("-1/3,1", "0,4/3", "0,1/2,1/2", "1/2,0"):
        with pytest.raises(ValueError):
            parse_alphas(invalid)
    assert parse_priority_policies("RM,DM") == ("RM", "DM")
    with pytest.raises(ValueError):
        parse_priority_policies("RM,EDF")


def test_base_tasksets_reuse_by_uc_and_dynamic_request_counts():
    base_uc = _relax_base_taskset()
    other_uc = _relax_base_taskset()
    other_uc.taskset_id = "base-taskset-other-uc"
    projected = {
        Fraction(3, 10): project_profiles_from_original_deadline(
            base_uc, (Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1)),
        ),
        Fraction(2, 5): project_profiles_from_original_deadline(
            other_uc, (Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1)),
        ),
    }
    cells = (
        (Fraction(3, 10), Fraction(3, 5)),
        (Fraction(3, 10), Fraction(7, 10)),
        (Fraction(2, 5), Fraction(7, 10)),
    )
    requests = build_requests(
        projected, cells, policies=("RM",), schedulers=("ASAP-BLOCK",),
    )
    assert len(projected) == 2
    assert len(requests) == 12
    assert len({row["base_taskset_id"] for row in requests}) == 2
    assert len({row["request_id"] for row in requests}) == 12


def test_request_identity_binds_energy_horizon_and_is_stable():
    projected = project_profiles_from_original_deadline(
        _relax_base_taskset(), (Fraction(1, 3),),
    )
    cells = ((Fraction(3, 10), Fraction(7, 10)),)
    first = build_requests(
        {Fraction(3, 10): projected}, cells, kappa=Fraction(10), horizon_ms=60000,
    )
    repeat = build_requests(
        {Fraction(3, 10): projected}, cells, kappa=Fraction(10), horizon_ms=60000,
    )
    changed = build_requests(
        {Fraction(3, 10): projected}, cells, kappa=Fraction(11), horizon_ms=61000,
    )
    assert [row["request_id"] for row in first] == [row["request_id"] for row in repeat]
    assert {row["request_id"] for row in first}.isdisjoint(
        row["request_id"] for row in changed
    )


def test_energy_material_is_identical_across_deadline_profiles(monkeypatch):
    profiles = project_profiles_from_original_deadline(
        _relax_base_taskset(), (Fraction(0), Fraction(1, 3), Fraction(1)),
    )

    def fake_energy(profile, target_ue, raw_trace, *, kappa, raw_trace_id):
        return {
            "target_ue": str(target_ue), "kappa": str(kappa),
            "raw_trace_id": raw_trace_id, "payload": profile.task_payload[0]["P"],
        }

    monkeypatch.setattr(
        "scripts.run_deadline_profile_sensitivity.load_cross.energy_material",
        fake_energy,
    )
    result = _build_energy_material(
        profiles, Fraction(7, 10), Fraction(10), (Fraction(1),), "trace-id",
    )
    assert result["material"]["payload"] == "5"


def test_implicit_outcome_status_is_not_requested_without_alpha_one():
    assert validate_implicit_outcomes([]) == "NOT_REQUESTED"


def _projected_population(count, prefix, alphas):
    profiles = []
    for index in range(count):
        base = _relax_base_taskset()
        base.taskset_id = f"{prefix}-{index}"
        base.semantic_hash = f"{prefix}-hash-{index}"
        base.taskset_index = index
        profiles.extend(project_profiles_from_original_deadline(base, alphas))
    return tuple(profiles)


def test_planning_request_counts_cover_one_cell_and_mid_alpha_subset():
    full_alphas = (Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1))
    mid_alphas = (Fraction(1, 3), Fraction(2, 3))
    full = _projected_population(20, "one-cell-full", full_alphas)
    mid = _projected_population(20, "one-cell-mid", mid_alphas)
    cell = ((Fraction(3, 10), Fraction(7, 10)),)
    assert len(full) == 80
    assert len(build_requests({Fraction(3, 10): full}, cell)) == 480
    assert len(mid) == 40
    assert len(build_requests({Fraction(3, 10): mid}, cell)) == 240


def test_planning_request_counts_reuse_uc_across_three_ue_cells():
    alphas = (Fraction(1, 3), Fraction(2, 3))
    profiles = _projected_population(20, "same-uc", alphas)
    cells = (
        (Fraction(3, 10), Fraction(3, 5)),
        (Fraction(3, 10), Fraction(7, 10)),
        (Fraction(3, 10), Fraction(4, 5)),
    )
    assert len(profiles) == 40
    requests = build_requests({Fraction(3, 10): profiles}, cells)
    assert len({row["base_taskset_id"] for row in requests}) == 20
    assert len(requests) == 720


def test_planning_request_counts_generate_each_distinct_uc_once():
    alphas = (Fraction(1, 3), Fraction(2, 3))
    first = _projected_population(20, "uc-3-10", alphas)
    second = _projected_population(20, "uc-2-5", alphas)
    cells = (
        (Fraction(3, 10), Fraction(7, 10)),
        (Fraction(2, 5), Fraction(7, 10)),
    )
    requests = build_requests(
        {Fraction(3, 10): first, Fraction(2, 5): second}, cells,
    )
    assert len(first) + len(second) == 80
    assert len({row["base_taskset_id"] for row in requests}) == 40
    assert len(requests) == 480


def test_request_identity_changes_for_every_scientific_dimension():
    base = _relax_base_taskset()
    one_third = project_profiles_from_original_deadline(base, (Fraction(1, 3),))
    two_thirds = project_profiles_from_original_deadline(base, (Fraction(2, 3),))
    base_map = {Fraction(3, 10): one_third}
    cells = ((Fraction(3, 10), Fraction(7, 10)),)
    common = build_requests(
        base_map, cells, kappa=Fraction(10), horizon_ms=60000,
        schedulers=("ASAP-BLOCK",), policies=("RM",),
    )
    assert [row["request_id"] for row in common] == [
        row["request_id"] for row in build_requests(
            base_map, cells, kappa=Fraction(10), horizon_ms=60000,
            schedulers=("ASAP-BLOCK",), policies=("RM",),
        )
    ]
    variants = [
        build_requests(base_map, ((Fraction(3, 10), Fraction(3, 5)),)),
        build_requests(base_map, cells, kappa=Fraction(11)),
        build_requests(base_map, cells, horizon_ms=61000),
        build_requests(base_map, cells, schedulers=("ST-NONBLOCK",)),
        build_requests(base_map, cells, policies=("DM",)),
        build_requests({Fraction(3, 10): two_thirds}, cells),
    ]
    for variant in variants:
        assert {row["request_id"] for row in common}.isdisjoint(
            row["request_id"] for row in variant
        )


def test_implicit_outcome_comparison_is_not_applicable_for_single_policy():
    rows = [row for row in _implicit_outcome_rows() if row["priority_policy"] == "RM"]
    assert validate_implicit_outcomes(rows, policies=("RM",)) == "NOT_APPLICABLE_POLICY_SUBSET"
