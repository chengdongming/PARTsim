from experiments.v9_3.performance_calibration import (
    calibration_q_values, confirm_30s, resolve_30s_confirmation, select_calibration,
)
from experiments.v9_3.performance_config import CAL_UTILIZATIONS, INITIAL_ETAS, INITIAL_KAPPAS, PRIMARY_SCHEDULERS


def rows(q_by_cell, tasksets=10):
    output = []
    for (kappa, eta, utilization), ratio in q_by_cell.items():
        successes = round(ratio * tasksets)
        for scheduler in PRIMARY_SCHEDULERS:
            for index in range(tasksets):
                output.append({
                    "kappa": kappa, "eta": eta, "u_norm": utilization,
                    "scheduler_id": scheduler, "taskset_id": str(index),
                    "observed_pass": index < successes,
                })
    return output


def selected_fixture():
    values = {}
    for kappa in INITIAL_KAPPAS:
        for eta in INITIAL_ETAS:
            for utilization in CAL_UTILIZATIONS:
                values[(kappa, eta, utilization)] = 0.5
    # The dictionary rule chooses eta=1, kappa=10; give it valid low/high.
    for utilization in CAL_UTILIZATIONS:
        values[("10", "3/4", utilization)] = 0.1
        values[("10", "5/4", utilization)] = 0.9
    return values


def test_q_only_lexicographic_selection_and_confirmation():
    decision = select_calibration(rows(selected_fixture()))
    assert decision.status == "SELECTED"
    assert (decision.kappa_star, decision.eta_low, decision.eta_transition, decision.eta_high) == ("10", "3/4", "1", "5/4")
    assert confirm_30s(decision, rows(selected_fixture()))["confirmed"]


def test_extension_branch_b_when_no_transition():
    values = {
        (kappa, eta, utilization): 0.0
        for kappa in INITIAL_KAPPAS for eta in INITIAL_ETAS for utilization in CAL_UTILIZATIONS
    }
    decision = select_calibration(rows(values))
    assert decision.status == "EXTENSION_REQUIRED" and decision.extension_branch == "B"


def test_extension_branch_a_and_full_grid_fallback():
    values = selected_fixture()
    for utilization in CAL_UTILIZATIONS:
        values[("10", "3/4", utilization)] = 0.5
        values[("10", "5/4", utilization)] = 0.5
    decision = select_calibration(rows(values))
    assert decision.status == "EXTENSION_REQUIRED" and decision.extension_branch == "A"

    provisional = select_calibration(rows(selected_fixture()))
    failed = selected_fixture()
    failed[("10", "3/4", "1/2")] = 0.4
    outcome = resolve_30s_confirmation(provisional, rows(failed))
    assert outcome["status"] == "FULL_30S_GRID_REQUIRED"
    recovered = resolve_30s_confirmation(
        provisional, rows(failed), full_grid_rows=rows(selected_fixture()),
    )
    assert recovered["status"] == "CONFIRMED"
    assert recovered["fallback_full_30s_grid_used"]


def test_calibration_pairing_and_single_extension_fail_closed():
    complete = rows(selected_fixture())
    with __import__("pytest").raises(ValueError, match="duplicate"):
        calibration_q_values(complete + [dict(complete[0])])
    with __import__("pytest").raises(ValueError, match="paired"):
        calibration_q_values(complete[:-1])

    no_transition = {
        (kappa, eta, utilization): 0.0
        for kappa in INITIAL_KAPPAS
        for eta in ("1/4", *INITIAL_ETAS, "2")
        for utilization in CAL_UTILIZATIONS
    }
    stopped = select_calibration(rows(no_transition), extension_already_used=True)
    assert stopped.status == "STOP_NO_THREE_CONDITIONS"
