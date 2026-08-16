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


def _policy_condition_rows(kappa, eta, ratios, *, energy_blocked_ticks, scheduler="ASAP-BLOCK"):
    rows = []
    for utilization, ratio in ratios.items():
        for index in range(10):
            row = _paired_row(
                kappa, eta, utilization, scheduler, f"t{index}",
                index < ratio, taskset_hash=f"hash-{index}",
            )
            row["energy_blocked_ticks"] = energy_blocked_ticks
            rows.append(row)
    return rows


def _default_policy_rows(*, include_low=True, include_high=True,
                         transition_energy=1, high_energy=0,
                         transition_incomplete=False):
    ratios = {"3/10": 10, "1/2": 6, "7/10": 3}
    rows = _policy_condition_rows(
        "200", "2", {utilization: 10 for utilization in ratios},
        energy_blocked_ticks=0,
    )
    if include_low:
        rows.extend(_policy_condition_rows(
            "50", "3/4", {utilization: 0 for utilization in ratios},
            energy_blocked_ticks=1,
        ))
    rows.extend(_policy_condition_rows(
        "50", "1", ratios, energy_blocked_ticks=transition_energy,
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
            energy_blocked_ticks=high_energy,
        ))
    return rows


def _select_default_policy(rows):
    return select_calibration_paired(
        rows, {"kappa": "200", "eta": "2"},
        schedulers=("ASAP-BLOCK",),
    )


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
