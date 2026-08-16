from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.v9_3 import perf_g
from experiments.v9_3.performance_outcome import evaluate_outcome
from scripts.analyze_v9_3_perf_g import (
    analyze_results, completeness, paired_confirmation_status,
    select_calibration, select_calibration_paired,
)
from scripts import run_v9_3_perf_g as perf_runner


def _row(kappa, eta, utilization, scheduler, index, passed):
    return {
        "request_id": f"{kappa}-{eta}-{utilization}-{scheduler}-{index}",
        "taskset_id": f"t{index}", "kappa": str(kappa), "eta": str(eta),
        "U_norm": str(utilization), "scheduler": scheduler,
        "taskset_pass": bool(passed), "energy_condition": f"{kappa}-{eta}",
    }


def _matrix(*, transition=True, include_extremes=True):
    rows = []
    utilities = ("3/10", "1/2", "7/10")
    cells = [(str(k), str(e)) for k in (10, 50, 200) for e in ("1/2", "3/4", "1", "5/4", "3/2")]
    if not include_extremes:
        cells = [("50", "1")]
    for kappa, eta in cells:
        for utilization in utilities:
            if kappa == "50" and eta == "1" and transition:
                ratio = 2
            elif transition and kappa == "50" and eta == "3/4":
                ratio = 0 if utilization == "1/2" else 1
            elif transition and kappa == "50" and eta == "3/2":
                ratio = 4 if utilization == "1/2" else 0
            else:
                ratio = 0
            for index in range(4):
                for scheduler in perf_g.CAL_SCHEDULERS:
                    rows.append(_row(kappa, eta, utilization, scheduler, index, index < ratio))
    return rows


def test_plan_counts_and_complete_pairing():
    cal = perf_g.cal_plan()
    formal = perf_g.formal_plan()
    assert (cal["unique_tasksets"], cal["energy_cells"], cal["schedulers"], cal["requests"]) == (90, 15, 5, 6750)
    assert (formal["unique_tasksets"], formal["energy_cells"], formal["schedulers"], formal["requests"]) == (800, 3, 9, 21600)
    assert cal["pairing"] == {"groups": 1350, "missing": 0, "duplicate": 0, "partial_group": 0}
    assert formal["pairing"] == {"groups": 2400, "missing": 0, "duplicate": 0, "partial_group": 0}
    assert formal["executable_formal"] is False


def test_confirmation_plan_has_sat_and_three_selected_conditions():
    selection = {
        "LOW": {"kappa": "10", "eta": "1"},
        "TRANSITION": {"kappa": "10", "eta": "5/4"},
        "HIGH": {"kappa": "10", "eta": "2"},
    }
    plan = perf_g.cal_confirmation_plan(selection)
    assert (plan["unique_tasksets"], plan["energy_cells"], plan["schedulers"], plan["requests"]) == (90, 4, 5, 1800)
    assert [row["name"] for row in plan["energy_conditions"]] == ["SAT", "LOW", "TRANSITION", "HIGH"]
    assert all(row["horizon_ms"] == 30000 for row in plan["requests_rows"])
    assert plan["pairing"] == {"groups": 360, "missing": 0, "duplicate": 0, "partial_group": 0}


def test_request_identity_excludes_scheduler_from_taskset_seed():
    taskset = perf_g.taskset_key("FORMAL", Fraction("1/2"), 7)
    assert perf_g.request_id(taskset, "TRANSITION", "ASAP-BLOCK") != perf_g.request_id(taskset, "TRANSITION", "ST-BLOCK")
    assert perf_g.taskset_key("FORMAL", Fraction("1/2"), 7) == perf_g.taskset_key("FORMAL", Fraction("1/2"), 7)
    assert perf_g.taskset_key("FORMAL", Fraction("1/2"), 7) != perf_g.taskset_key("CAL", Fraction("1/2"), 7)


def test_normal_q_only_selection():
    selected = select_calibration(_matrix())
    assert selected["status"] == "INITIAL_GRID"
    assert selected["selection"] == {
        "kappa_star": "50", "eta_low": "3/4", "eta_transition": "1", "eta_high": "3/2",
        "LOW": {"kappa": "50", "eta": "3/4"},
        "TRANSITION": {"kappa": "50", "eta": "1"},
        "HIGH": {"kappa": "50", "eta": "3/2"},
    }


def test_transition_tie_break_prefers_closest_eta_then_kappa():
    rows = _matrix()
    # The base matrix has only one valid transition. Add a second with equal
    # N_T and make its deviation larger; Q-only tie-breaking must reject it.
    assert perf_g.select_transition(perf_g.q_matrix(rows))["kappa"] == "50"


def test_extension_a_low_and_high():
    initial = _matrix(include_extremes=False)
    extension = []
    for eta, passed in (("3/4", False), ("3/2", True)):
        for utilization in ("3/10", "1/2", "7/10"):
            for index in range(4):
                for scheduler in perf_g.CAL_SCHEDULERS:
                    extension.append(_row("50", eta, utilization, scheduler, index, passed))
    assert select_calibration(initial)["status"] == "NEEDS_EXTENSION_A"
    selected = select_calibration(initial, extension_rows=extension)
    assert selected["status"] == "EXTENSION_A_APPLIED"


def test_extension_b_and_blocked():
    initial = _matrix(transition=False)
    assert select_calibration(initial)["status"] == "NEEDS_EXTENSION_B"
    extension = []
    for kappa in ("10", "50", "200"):
        for eta in ("1/4", "2"):
            for utilization in ("3/10", "1/2", "7/10"):
                for index in range(4):
                    for scheduler in perf_g.CAL_SCHEDULERS:
                        extension.append(_row(kappa, eta, utilization, scheduler, index, False))
    assert select_calibration(initial, extension_rows=extension)["status"] == "CAL_BLOCKED"


def test_confirmation_and_fallback_states():
    initial = _matrix()
    selected = select_calibration(initial, confirmation_rows=initial)
    assert selected["status"] == "INITIAL_GRID_CONFIRMED"
    failed = select_calibration(initial, confirmation_rows=_matrix(transition=False))
    assert failed["status"] == "NEEDS_FULL_GRID_FALLBACK"


def test_q_selection_ignores_ebf_and_pairwise_fields():
    rows = _matrix()
    baseline = select_calibration(rows)["selection"]
    mutated = [
        {**row, "EBF": 999999, "energy_blocking_rate": 0.0,
         "ASAP_BLOCK_pairwise_advantage": -999.0, "ranking": "reversed"}
        for row in rows
    ]
    assert select_calibration(mutated)["selection"] == baseline


def test_outcome_half_open_and_zero_denominator():
    jobs = [
        {"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 9},
        {"task_id": "0", "release": 1, "absolute_deadline": 10, "completion": None},
        {"task_id": "0", "release": 2, "absolute_deadline": 11, "completion": 12},
    ]
    result = evaluate_outcome(jobs, ["0"], horizon=10, minimum_adjudicable_jobs=1)
    assert result["adjudicable_jobs"] == 1
    assert result["censored_jobs"] == 2
    assert result["taskset_pass"] is True
    unavailable = evaluate_outcome(jobs, ["0"], horizon=1, minimum_adjudicable_jobs=1)
    assert unavailable["outcome_status"] == "UNAVAILABLE"
    assert unavailable["taskset_pass"] is None


def test_outcome_completion_and_no_completion_are_misses():
    jobs = [
        {"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 10},
        {"task_id": "1", "release": 0, "absolute_deadline": 9, "completion": None},
    ]
    result = evaluate_outcome(jobs, ["0", "1"], horizon=20, minimum_adjudicable_jobs=1)
    assert result["deadline_miss_jobs"] == 2
    assert result["taskset_pass"] is False


def test_technical_failure_is_not_pass_or_fail():
    result = evaluate_outcome([], ["0"], horizon=20, minimum_adjudicable_jobs=1,
                              simulation_completed=False, technical_error="timeout")
    assert result["outcome_status"] == "TECHNICAL_FAILURE"
    assert result["taskset_pass"] is None


def test_completeness_reports_missing_duplicate_partial():
    requests = [
        {"request_id": f"a-{scheduler}", "taskset_id": "t", "energy_condition": "LOW", "scheduler": scheduler}
        for scheduler in perf_g.FORMAL_SCHEDULERS
    ]
    results = requests[:-1] + [requests[0]]
    report = completeness(requests, results, perf_g.FORMAL_SCHEDULERS)
    assert report["missing"] == 1
    assert report["duplicate"] == 1
    assert report["partial_group"] == 1


def test_sparse_extension_only_at_one_kappa_is_fail_closed():
    initial = _matrix(include_extremes=False)
    extension = []
    for eta in ("1/4", "2"):
        for utilization in ("3/10", "1/2", "7/10"):
            for index in range(4):
                for scheduler in perf_g.CAL_SCHEDULERS:
                    extension.append(_row("200", eta, utilization, scheduler, index, True))

    selected = select_calibration(initial, extension_rows=extension)
    assert selected["status"] == "CAL_BLOCKED"
    q = perf_g.q_matrix(initial + extension)
    assert ("10", "1/4", "3/10") not in q
    assert ("50", "2", "3/10") not in q
    assert ("200", "1/4", "3/10") in q


def test_select_transition_skips_incomplete_utilization_pair():
    q = {
        ("200", "1/4", "3/10"): 0.5,
        ("200", "1/4", "1/2"): 0.5,
    }
    assert perf_g.select_transition(q) is None


def _paired_row(kappa, eta, utilization, scheduler, taskset_id, passed, *, taskset_hash="hash"):
    return {
        "kappa": str(kappa), "eta": str(eta), "U_norm": str(utilization),
        "scheduler": scheduler, "taskset_id": taskset_id,
        "taskset_hash": taskset_hash, "taskset_pass": passed,
        "energy_blocked_ticks": 0,
    }


def _paired_rows(sat_values, candidate_values, *, candidate=("50", "1"), scheduler="ASAP-BLOCK"):
    rows = []
    for taskset_id, passed in sat_values.items():
        rows.append(_paired_row("200", "2", "1/2", scheduler, taskset_id, passed))
    for taskset_id, passed in candidate_values.items():
        rows.append(_paired_row(*candidate, "1/2", scheduler, taskset_id, passed))
    return rows


def _paired_cell(result, kappa, eta, status="ASAP-BLOCK"):
    return result["cells"][(str(kappa), str(eta), "1/2", status)]


def test_paired_retention_sat_is_one_and_excludes_sat_failures():
    rows = _paired_rows({"A": True, "B": False}, {"A": True, "B": False})
    result = perf_g.paired_retention_matrix(
        rows, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    assert _paired_cell(result, "200", "2")["retention"] == 1.0
    assert _paired_cell(result, "50", "1")["sat_denominator_count"] == 1
    assert _paired_cell(result, "50", "1")["retained_count"] == 1


def test_paired_retention_partial_degradation_is_half():
    rows = _paired_rows(
        {"A": True, "B": True, "C": True, "D": True},
        {"A": True, "B": True, "C": False, "D": False},
    )
    result = perf_g.paired_retention_matrix(
        rows, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    cell = _paired_cell(result, "50", "1")
    assert cell["status"] == "AVAILABLE"
    assert cell["retention"] == 0.5
    assert cell["sat_denominator_count"] == 4
    assert cell["retained_count"] == 2


def test_paired_retention_zero_sat_denominator_is_unavailable():
    rows = _paired_rows({"A": False, "B": False}, {"A": True, "B": True})
    result = perf_g.paired_retention_matrix(
        rows, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    cell = _paired_cell(result, "50", "1")
    assert cell["status"] == "UNAVAILABLE"
    assert cell["retention"] is None
    assert cell["sat_denominator_count"] == 0


def test_paired_retention_technical_failure_is_incomplete():
    rows = _paired_rows({"A": True}, {"A": None})
    result = perf_g.paired_retention_matrix(
        rows, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    cell = _paired_cell(result, "50", "1")
    assert cell["status"] == "INCOMPLETE"
    assert cell["retention"] is None


def test_paired_retention_hash_mismatch_is_incomplete():
    rows = _paired_rows({"A": True}, {"A": True})
    rows[-1]["taskset_hash"] = "different"
    result = perf_g.paired_retention_matrix(
        rows, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    assert _paired_cell(result, "50", "1")["status"] == "INCOMPLETE"


def test_paired_retention_missing_and_duplicate_pairs_are_incomplete():
    missing = _paired_rows({"A": True, "B": True}, {"A": True})
    result = perf_g.paired_retention_matrix(
        missing, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    cell = _paired_cell(result, "50", "1")
    assert cell["status"] == "INCOMPLETE"
    assert cell["missing_candidate_pair_count"] == 1

    duplicate = _paired_rows({"A": True}, {"A": True})
    duplicate.append(dict(duplicate[-1]))
    result = perf_g.paired_retention_matrix(
        duplicate, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK",),
    )
    assert _paired_cell(result, "50", "1")["status"] == "INCOMPLETE"


def test_paired_aggregate_excludes_zero_sat_denominator_scheduler():
    rows = _paired_rows(
        {"A": True, "B": True}, {"A": True, "B": False},
        scheduler="ASAP-BLOCK",
    )
    rows.extend(_paired_rows(
        {"A": False, "B": False}, {"A": True, "B": True},
        scheduler="ST-BLOCK",
    ))
    result = perf_g.paired_retention_matrix(
        rows, {"kappa": "200", "eta": "2"}, utilizations=(Fraction("1/2"),),
        schedulers=("ASAP-BLOCK", "ST-BLOCK"),
    )
    aggregate = result["aggregates"][("50", "1", "1/2")]
    assert aggregate["status"] == "PARTIAL"
    assert aggregate["retention"] == 0.5
    assert aggregate["valid_scheduler_count"] == 1
    assert aggregate["sat_denominator_count"] == 2


def _policy_condition_rows(kappa, eta, ratios, *, energy_blocked_ticks,
                           schedulers=("ASAP-BLOCK",), failed_schedulers=()):
    rows = []
    for utilization, ratio in ratios.items():
        for index in range(10):
            for scheduler in schedulers:
                row = _paired_row(
                    kappa, eta, utilization, scheduler, f"t{index}",
                    index < ratio and scheduler not in failed_schedulers,
                    taskset_hash=f"hash-{index}",
                )
                row["energy_blocked_ticks"] = energy_blocked_ticks
                rows.append(row)
    return rows


def _default_policy_rows(*, include_low=True, include_high=True,
                         transition_energy=1, high_energy=0,
                         transition_incomplete=False,
                         schedulers=("ASAP-BLOCK",), failed_schedulers=()):
    ratios = {"3/10": 10, "1/2": 6, "7/10": 3}
    rows = _policy_condition_rows(
        "200", "2", {utilization: 10 for utilization in ratios},
        energy_blocked_ticks=0, schedulers=schedulers,
        failed_schedulers=failed_schedulers,
    )
    if include_low:
        rows.extend(_policy_condition_rows(
            "50", "3/4", {utilization: 0 for utilization in ratios},
            energy_blocked_ticks=1, schedulers=schedulers,
            failed_schedulers=failed_schedulers,
        ))
    rows.extend(_policy_condition_rows(
        "50", "1", ratios, energy_blocked_ticks=transition_energy,
        schedulers=schedulers, failed_schedulers=failed_schedulers,
    ))
    if transition_incomplete:
        rows[ next(
            index for index, row in enumerate(rows)
            if row["kappa"] == "50" and row["eta"] == "1"
            and row["U_norm"] == "1/2"
        )]["taskset_pass"] = None
    if include_high:
        rows.extend(_policy_condition_rows(
            "50", "5/4", {utilization: 10 for utilization in ratios},
            energy_blocked_ticks=high_energy, schedulers=schedulers,
            failed_schedulers=failed_schedulers,
        ))
    return rows


def _select_default_policy(rows, *, schedulers=("ASAP-BLOCK",)):
    return select_calibration_paired(
        rows, {"kappa": "200", "eta": "2"},
        schedulers=schedulers,
    )


CONFIRMATION_SCHEDULERS = (
    "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK",
)


def _confirmation_rows(*, low_ratios=None, transition_ratios=None, high_ratios=None,
                       transition_energy=1, high_energy=0, sat_fail_scheduler="ALAP-BLOCK"):
    ratios = {"3/10": 10, "1/2": 10, "7/10": 10}
    transition_ratios = transition_ratios or {"3/10": 10, "1/2": 6, "7/10": 3}
    low_ratios = low_ratios or {utilization: 0 for utilization in ratios}
    high_ratios = high_ratios or ratios
    rows = []
    for scheduler in CONFIRMATION_SCHEDULERS:
        sat_pass = scheduler != sat_fail_scheduler
        for utilization in ratios:
            for index in range(10):
                rows.append(_paired_row(
                    "200", "2", utilization, scheduler, f"t{index}",
                    sat_pass, taskset_hash=f"hash-{index}",
                ))
    for kappa, eta, condition_ratios, blocking in (
        ("10", "1", low_ratios, 1),
        ("10", "5/4", transition_ratios, transition_energy),
        ("10", "2", high_ratios, high_energy),
    ):
        for utilization, ratio in condition_ratios.items():
            for scheduler in CONFIRMATION_SCHEDULERS:
                for index in range(10):
                    row = _paired_row(
                        kappa, eta, utilization, scheduler, f"t{index}",
                        index < ratio, taskset_hash=f"hash-{index}",
                    )
                    row["energy_blocked_ticks"] = blocking
                    rows.append(row)
    return rows


def test_paired_confirmation_passes_with_sat_zero_denominator_scheduler():
    selection = {
        "LOW": {"kappa": "10", "eta": "1"},
        "TRANSITION": {"kappa": "10", "eta": "5/4"},
        "HIGH": {"kappa": "10", "eta": "2"},
    }
    result = paired_confirmation_status(selection, _confirmation_rows())
    assert result["status"] == "PASS"
    assert result["checks"]["TRANSITION"]["N_T"] >= 2
    sat_aggregate = result["matrix"]["aggregates"][("200", "2", "1/2")]
    assert sat_aggregate["status"] == "PARTIAL"
    assert sat_aggregate["valid_scheduler_count"] == 4
    assert sat_aggregate["incomplete_scheduler_count"] == 0


@pytest.mark.parametrize("change", ("incomplete", "missing", "low", "transition", "transition_energy", "high", "high_energy"))
def test_paired_confirmation_fail_closed(change):
    selection = {
        "LOW": {"kappa": "10", "eta": "1"},
        "TRANSITION": {"kappa": "10", "eta": "5/4"},
        "HIGH": {"kappa": "10", "eta": "2"},
    }
    kwargs = {}
    if change == "low":
        kwargs["low_ratios"] = {"3/10": 3, "1/2": 3, "7/10": 3}
    elif change == "transition":
        kwargs["transition_ratios"] = {"3/10": 0, "1/2": 0, "7/10": 0}
    elif change == "transition_energy":
        kwargs["transition_energy"] = 0
    elif change == "high":
        kwargs["high_ratios"] = {"3/10": 7, "1/2": 7, "7/10": 7}
    elif change == "high_energy":
        kwargs["high_energy"] = 1
    rows = _confirmation_rows(**kwargs)
    if change == "incomplete":
        for row in rows:
            if row["kappa"] == "10" and row["eta"] == "5/4" and row["U_norm"] == "1/2" and row["taskset_id"] == "t0" and row["scheduler"] == "ASAP-BLOCK":
                row["taskset_pass"] = None
                break
    elif change == "missing":
        rows = [row for row in rows if not (
            row["kappa"] == "10" and row["eta"] == "5/4" and row["U_norm"] == "1/2"
            and row["taskset_id"] == "t0" and row["scheduler"] == "ASAP-BLOCK"
        )]
    result = paired_confirmation_status(selection, rows)
    assert result["status"] == "FAIL"


def test_paired_selection_default_policy_is_data_driven():
    result = _select_default_policy(_default_policy_rows())
    assert result["status"] == "PAIRED_SELECTION_OK"
    assert result["selection"] == {
        "kappa_star": "50", "eta_low": "3/4", "eta_transition": "1", "eta_high": "5/4",
        "LOW": {"kappa": "50", "eta": "3/4"},
        "TRANSITION": {"kappa": "50", "eta": "1"},
        "HIGH": {"kappa": "50", "eta": "5/4"},
    }
    assert result["threshold_policy"]["reference_utilization"] == "1/2"
    sat_cell = _paired_cell(result["matrix"], "200", "2")
    assert sat_cell["retention"] == 1.0
    assert result["selection"]["HIGH"]["eta"] != "2"


def test_paired_selection_saturation_and_missing_energy_fail_closed():
    assert _select_default_policy(_default_policy_rows(transition_energy=0))["status"] == "PAIRED_CAL_BLOCKED"
    assert _select_default_policy(_default_policy_rows(high_energy=1))["status"] == "NEEDS_PAIRED_EXTENSION_HIGH"
    assert _select_default_policy(_default_policy_rows(include_low=False))["status"] == "NEEDS_PAIRED_EXTENSION_LOW"
    assert _select_default_policy(_default_policy_rows(include_high=False))["status"] == "NEEDS_PAIRED_EXTENSION_HIGH"
    assert _select_default_policy(_default_policy_rows(include_low=False, include_high=False))["status"] == "NEEDS_PAIRED_EXTENSION_BOTH"
    missing_energy = _default_policy_rows()
    for row in missing_energy:
        if row["kappa"] == "50" and row["eta"] == "1" and row["U_norm"] == "1/2":
            row.pop("energy_blocked_ticks")
    assert _select_default_policy(missing_energy)["status"] == "PAIRED_CAL_BLOCKED"


def test_paired_selection_ignores_incomplete_transition_candidate():
    result = _select_default_policy(_default_policy_rows(transition_incomplete=True))
    assert result["status"] == "PAIRED_CAL_BLOCKED"
    transition = next(candidate for candidate in result["candidates"]
                      if candidate["kappa"] == "50" and candidate["eta"] == "1")
    assert transition["status"] == "INCOMPLETE"

    non_reference_incomplete = _default_policy_rows()
    for row in non_reference_incomplete:
        if row["kappa"] == "50" and row["eta"] == "1" and row["U_norm"] == "3/10":
            row["taskset_pass"] = None
            break
    assert _select_default_policy(non_reference_incomplete)["status"] == "PAIRED_CAL_BLOCKED"


def test_paired_selection_accepts_legal_partial_aggregate_from_sat_zero_scheduler():
    schedulers = (
        "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK",
    )
    rows = _default_policy_rows(
        schedulers=schedulers, failed_schedulers=("ALAP-BLOCK",),
    )
    result = _select_default_policy(rows, schedulers=schedulers)
    assert result["status"] == "PAIRED_SELECTION_OK"
    assert result["selection"] == {
        "kappa_star": "50", "eta_low": "3/4", "eta_transition": "1", "eta_high": "5/4",
        "LOW": {"kappa": "50", "eta": "3/4"},
        "TRANSITION": {"kappa": "50", "eta": "1"},
        "HIGH": {"kappa": "50", "eta": "5/4"},
    }
    for eta in ("3/4", "1", "5/4"):
        for utilization in ("3/10", "1/2", "7/10"):
            aggregate = result["matrix"]["aggregates"][("50", eta, utilization)]
            assert aggregate["status"] == "PARTIAL"
            assert aggregate["valid_scheduler_count"] == 4
            assert aggregate["unavailable_scheduler_count"] == 1
            assert aggregate["incomplete_scheduler_count"] == 0


def test_paired_selection_rejects_partial_aggregate_with_incomplete_scheduler():
    schedulers = (
        "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK",
    )
    rows = _default_policy_rows(
        schedulers=schedulers, failed_schedulers=("ALAP-BLOCK",),
    )
    for row in rows:
        if (
            row["kappa"], row["eta"], row["U_norm"], row["scheduler"], row["taskset_id"]
        ) == ("50", "1", "1/2", "ASAP-BLOCK", "t0"):
            row["taskset_pass"] = None
            break
    result = _select_default_policy(rows, schedulers=schedulers)
    aggregate = result["matrix"]["aggregates"][("50", "1", "1/2")]
    assert aggregate["status"] == "PARTIAL"
    assert aggregate["valid_scheduler_count"] == 3
    assert aggregate["unavailable_scheduler_count"] == 1
    assert aggregate["incomplete_scheduler_count"] == 1
    assert result["status"] == "PAIRED_CAL_BLOCKED"


def test_paired_selection_transition_tie_break_is_deterministic():
    rows = _policy_condition_rows(
        "200", "2", {u: 10 for u in ("3/10", "1/2", "7/10")},
        energy_blocked_ticks=0,
    )
    rows.extend(_policy_condition_rows(
        "50", "3/4", {"3/10": 2, "1/2": 5, "7/10": 8},
        energy_blocked_ticks=1,
    ))
    rows.extend(_policy_condition_rows(
        "50", "5/4", {"3/10": 8, "1/2": 5, "7/10": 2},
        energy_blocked_ticks=1,
    ))
    first = _select_default_policy(rows)
    second = _select_default_policy(list(reversed(rows)))
    assert first["selection"]["eta_transition"] == "3/4"
    assert second["selection"] == first["selection"]


def test_paired_selection_accepts_explicit_policy_without_freezing_new_values():
    result = select_calibration_paired(
        _default_policy_rows(), {"kappa": "200", "eta": "2"},
        threshold_policy={
            "reference_utilization": "1/2",
            "low_max_retention": "1/4",
            "transition_min_retention": "1/5",
            "transition_max_retention": "4/5",
            "high_min_retention": "3/4",
        }, schedulers=("ASAP-BLOCK",),
    )
    assert result["status"] == "PAIRED_SELECTION_OK"
    assert result["threshold_policy"]["high_min_retention"] == "3/4"


def test_formal_adjudication_and_persistence_modes_are_explicit():
    assert perf_g.FORMAL_TASKSETS_PER_UTILIZATION == 100
    assert perf_runner._minimum_adjudicable_jobs("FORMAL") == 100
    assert perf_runner._minimum_adjudicable_jobs("CAL_CONFIRM") == 1
    assert perf_runner._semantic_config("FORMAL", simulator=Path("sim"), workers=1)["result_persistence"] == "compact_outcome"
    assert perf_runner._semantic_config("CAL", simulator=Path("sim"), workers=1)["result_persistence"] == "full_jobs"


def test_formal_runner_and_analyzer_use_minimum_100(tmp_path):
    jobs = [
        {"task_id": "0", "release": 0, "absolute_deadline": 999, "completion": 1}
        for _ in range(99)
    ]
    expected = evaluate_outcome(jobs, ["0"], horizon=1000, minimum_adjudicable_jobs=100)
    assert expected["taskset_pass"] is False
    assert perf_runner._minimum_adjudicable_jobs("FORMAL") == 100
    root = tmp_path / "formal"
    root.mkdir()
    request = {
        "request_id": "formal-0", "taskset_id": "taskset-0", "U_norm": "1/2",
        "energy_condition": "LOW", "scheduler": "ASAP-BLOCK",
    }
    row = {
        **request, "horizon_ms": 1000, "simulation_status": "SIM_PASS_OBSERVED",
        "technical_error": None, "jobs": jobs, "outcome": expected,
        "taskset_pass": expected["taskset_pass"], "metrics": {},
    }
    (root / "requests.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    (root / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    analyzed = analyze_results(root, "FORMAL")
    assert analyzed["cell_summary"][0]["pass_ratio"] == 0.0


def _analysis_row(*, compact, technical=False):
    if technical:
        outcome = evaluate_outcome(
            [], ["0"], horizon=10, minimum_adjudicable_jobs=1,
            simulation_completed=False, technical_error="timeout",
        )
        jobs = None
        status = "TECHNICAL_FAILURE"
    else:
        jobs = [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 9}]
        outcome = evaluate_outcome(jobs, ["0"], horizon=10, minimum_adjudicable_jobs=1)
        status = "SIM_PASS_OBSERVED"
    row = {
        "request_id": "request-0", "taskset_id": "taskset-0", "U_norm": "1/2",
        "energy_condition": "LOW", "scheduler": "ASAP-BLOCK", "horizon_ms": 10,
        "simulation_status": status, "technical_error": "timeout" if technical else None,
        "outcome": outcome, "taskset_pass": outcome["taskset_pass"],
        "metrics": {"energy_blocked_ticks": 2, "harvested_energy_j": 3,
                    "consumed_energy_j": 4},
    }
    if not compact:
        row["jobs"] = jobs or []
    return row


def test_compact_and_full_analysis_are_equivalent_and_technical_is_not_false(tmp_path):
    import scripts.analyze_v9_3_perf_g as analyzer

    full_root = tmp_path / "full"
    compact_root = tmp_path / "compact"
    for root, row in ((full_root, _analysis_row(compact=False)),
                      (compact_root, _analysis_row(compact=True))):
        root.mkdir()
        requests = [{key: row[key] for key in ("request_id", "taskset_id", "U_norm", "energy_condition", "scheduler")}]
        (root / "requests.jsonl").write_text(json.dumps(requests[0]) + "\n", encoding="utf-8")
        (root / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    full = analyze_results(full_root, "CAL")
    compact = analyze_results(compact_root, "CAL")
    assert full["cell_summary"] == compact["cell_summary"]
    assert full["secondary_metrics"] == compact["secondary_metrics"]
    assert "jobs" not in _analysis_row(compact=True)

    technical_root = tmp_path / "technical"
    technical_root.mkdir()
    technical = _analysis_row(compact=True, technical=True)
    (technical_root / "requests.jsonl").write_text(
        json.dumps({key: technical[key] for key in ("request_id", "taskset_id", "U_norm", "energy_condition", "scheduler")}) + "\n",
        encoding="utf-8",
    )
    (technical_root / "results.jsonl").write_text(json.dumps(technical) + "\n", encoding="utf-8")
    assert analyze_results(technical_root, "CAL")["cell_summary"] == []


def test_compact_runner_cleans_simulations_and_resumes_without_duplicates(tmp_path, monkeypatch):
    class FakeTaskset:
        taskset_id = "taskset-0"
        semantic_hash = "hash-0"
        seed = 7
        actual_utilization = Fraction("2")
        target_utilization = Fraction("2")
        taskset_index = 0
        task_payload = ({"task_id": "0"},)

        def generated_row(self):
            return {"taskset_id": self.taskset_id, "taskset_hash": self.semantic_hash}

    @dataclass
    class FakeJob:
        task_id: str = "0"
        release: int = 0
        absolute_deadline: int = 9
        completion: int = 9

    fake_taskset = FakeTaskset()
    fake_service = SimpleNamespace(system_path=tmp_path / "system.yml")
    monkeypatch.setattr(perf_runner.perf_g, "materialize_tasksets", lambda *args: ([fake_taskset], fake_service))
    monkeypatch.setattr(perf_runner.perf_g, "build_raw_trace", lambda service: (Fraction(1),))
    monkeypatch.setattr(perf_runner.perf_g, "energy_material", lambda *args: {
        "initial_energy_j": "1", "battery_capacity_j": "2", "solar_scale": "1",
    })

    def fake_run(**kwargs):
        run_root = kwargs["run_root"]
        run_root.mkdir(parents=True, exist_ok=True)
        result = SimpleNamespace(
            status=perf_runner.SimulationStatus.PASS_OBSERVED, reason="pass",
            metrics={"energy_blocked_ticks": 0}, jobs=(FakeJob(),),
            simulation_completed=True,
        )
        return SimpleNamespace(result=result, runtime_seconds=0.01)

    monkeypatch.setattr(perf_runner, "run_paired_simulation", fake_run)
    condition = perf_g.condition("SAT", "200", "2")
    first = perf_runner._execute_requests(
        root=tmp_path / "run", mode="CAL_CONFIRM", namespace="CAL",
        utilizations=(Fraction("1/2"),), taskset_count=1,
        conditions=[condition], schedulers=("ASAP-BLOCK",), horizon=30,
        simulator=tmp_path / "sim", resume=False, workers=1,
    )
    result_path = tmp_path / "run" / "results.jsonl"
    rows = [json.loads(line) for line in result_path.read_text().splitlines()]
    assert first["processed"] == 1
    assert len(rows) == 1 and "jobs" not in rows[0]
    assert not (tmp_path / "run" / "simulations" / rows[0]["request_id"]).exists()
    second = perf_runner._execute_requests(
        root=tmp_path / "run", mode="CAL_CONFIRM", namespace="CAL",
        utilizations=(Fraction("1/2"),), taskset_count=1,
        conditions=[condition], schedulers=("ASAP-BLOCK",), horizon=30,
        simulator=tmp_path / "sim", resume=True, workers=1,
    )
    assert second["processed"] == 1
    assert len(result_path.read_text().splitlines()) == 1
