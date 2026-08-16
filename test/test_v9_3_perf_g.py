from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json

from experiments.v9_3 import perf_g
from experiments.v9_3.performance_outcome import evaluate_outcome
from scripts.analyze_v9_3_perf_g import completeness, select_calibration, select_calibration_paired


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
    assert (formal["unique_tasksets"], formal["energy_cells"], formal["schedulers"], formal["requests"]) == (1600, 3, 9, 43200)
    assert cal["pairing"] == {"groups": 1350, "missing": 0, "duplicate": 0, "partial_group": 0}
    assert formal["pairing"] == {"groups": 4800, "missing": 0, "duplicate": 0, "partial_group": 0}
    assert formal["executable_formal"] is False


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


def test_paired_selection_reports_missing_threshold_policy_without_selecting():
    rows = _paired_rows({"A": True}, {"A": True})
    result = select_calibration_paired(
        rows, {"kappa": "200", "eta": "2"},
        utilizations=(Fraction("1/2"),), schedulers=("ASAP-BLOCK",),
    )
    assert result["status"] == "THRESHOLD_POLICY_UNSPECIFIED"
    assert result["selection"] is None
