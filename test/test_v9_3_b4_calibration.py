from experiments.v9_3.performance_calibration import (
    calibration_q_values, confirm_30s, resolve_30s_confirmation, select_calibration,
    resolve_branch_a_extension,
)
from experiments.v9_3.performance_calibration_audit import audit_calibration_phase
from experiments.v9_3.performance_config import CAL_UTILIZATIONS, INITIAL_ETAS, INITIAL_KAPPAS, PRIMARY_SCHEDULERS
from experiments.v9_3.performance_identity import energy_identity, execution_identity, semantic_request_id


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


def test_branch_a_endpoint_cannot_reselect_transition():
    values = {
        (kappa, eta, utilization): 0.9
        for kappa in INITIAL_KAPPAS for eta in INITIAL_ETAS
        for utilization in CAL_UTILIZATIONS
    }
    for utilization, value in zip(CAL_UTILIZATIONS, (0.2, 0.5, 0.8)):
        values[("10", "1", utilization)] = value
    provisional = select_calibration(rows(values, tasksets=30))
    assert provisional.status == "EXTENSION_REQUIRED"
    assert provisional.extension_branch == "A"
    assert provisional.eta_transition == "1" and provisional.requested_extension_etas == ("1/4",)
    endpoint_values = {
        ("10", "1/4", utilization): value
        for utilization, value in zip(CAL_UTILIZATIONS, (0.5, 0.2, 0.5))
    }
    endpoint_rows = rows(endpoint_values, tasksets=30)
    # If the endpoint were fed back to the generic selector, it would win the
    # transition score. Branch A must keep the provisional transition instead.
    generic = select_calibration(
        [*rows(values, tasksets=30), *endpoint_rows], extension_already_used=True,
    )
    assert generic.eta_transition == "1/4"
    resolved = resolve_branch_a_extension(
        provisional, rows(values, tasksets=30), endpoint_rows,
    )
    assert resolved.status == "SELECTED"
    assert resolved.kappa_star == "10"
    assert resolved.eta_transition == "1"
    assert resolved.eta_low == "1/4" and resolved.eta_high == "5/4"


def _cal_authority_fixture(*, confirmation=False):
    schedulers = PRIMARY_SCHEDULERS
    tasksets = {
        utilization: [f"task-{utilization}-{index}" for index in range(30)]
        for utilization in CAL_UTILIZATIONS
    }
    if confirmation:
        cells = (("low", "10", "3/4"), ("transition", "10", "1"), ("high", "10", "5/4"))
        horizon = 30000
    else:
        cells = tuple(
            (f"k{kappa}-e{eta}", kappa, eta)
            for kappa in INITIAL_KAPPAS for eta in INITIAL_ETAS
        )
        horizon = 10000
    plan_rows, result_rows = [], []
    for condition, kappa, eta in cells:
        for utilization in CAL_UTILIZATIONS:
            for scheduler in schedulers:
                for taskset_hash in tasksets[utilization]:
                    material = {
                        "contract_version": "energy-v1",
                        "taskset_semantic_hash": taskset_hash,
                        "kappa": kappa, "eta": eta,
                    }
                    energy_id = energy_identity(material)
                    request_id = semantic_request_id(
                        contract_version="ASAP_BLOCK_V9_3_B4_REQUEST_V1",
                        taskset_semantic_hash=taskset_hash,
                        energy_identity_value=energy_id, scheduler_id=scheduler,
                        runtime_horizon_ms=horizon,
                        simulation_semantic_config_hash="semantic-config",
                    )
                    execution_id = execution_identity(request_id, "source", "binary")
                    request = {
                        "semantic_request_id": request_id,
                        "execution_identity": execution_id,
                        "taskset_semantic_hash": taskset_hash,
                        "priority_hash": f"priority-{taskset_hash}",
                        "power_hash": f"power-{taskset_hash}",
                        "release_hash": f"release-{taskset_hash}",
                        "energy_identity": energy_id, "energy_material": material,
                        "scheduler_id": scheduler, "runtime_horizon_ms": horizon,
                        "simulation_semantic_config_hash": "semantic-config",
                        "u_norm": utilization, "energy_condition": condition,
                    }
                    result = {
                        **request, "terminal": True, "simulation_completed": True,
                        "completion_reason": "reached_horizon",
                        "legacy_status": "SIM_PASS_OBSERVED", "legacy_reason": "pass",
                        "attempts": [{"legacy_status": "SIM_PASS_OBSERVED"}],
                        "arrival_offsets_zero": True,
                        "outcome": {
                            "contract_version": "PERF_OUTCOME_V2",
                            "observed_pass": True,
                        },
                    }
                    plan_rows.append(request)
                    result_rows.append(result)
    manifest = {
        "seed_space": "ASAP_BLOCK_V9_3_B4_CAL_R1",
        "configured_tasksets_per_utilization": 30,
        "store_identity": "store",
        "entries": [
            {"taskset_semantic_hash": taskset_hash, "utilization": utilization}
            for utilization in CAL_UTILIZATIONS
            for taskset_hash in tasksets[utilization]
        ],
    }
    return {
        "plan": {
            "source_commit": "source", "simulator_binary_sha256": "binary",
            "formal_plan_identity": "plan", "requests": plan_rows,
        },
        "results": result_rows, "manifest": manifest,
    }


def test_complete_initial_and_confirmation_cal_authority_closure():
    initial = _cal_authority_fixture()
    audited = audit_calibration_phase("initial", initial["plan"], initial["results"], initial["manifest"])
    assert audited.status == "CAL_VALID"
    assert audited.planned_requests == audited.observed_results == 6750
    assert len(audited.audited_rows) == 6750
    confirmation = _cal_authority_fixture(confirmation=True)
    audited = audit_calibration_phase(
        "confirmation", confirmation["plan"], confirmation["results"], confirmation["manifest"],
    )
    assert audited.status == "CAL_VALID"
    assert audited.planned_requests == audited.observed_results == 1350


def test_cal_authority_rejects_missing_29_technical_extra_and_identity_errors():
    fixture = _cal_authority_fixture()
    plan, results, manifest = fixture["plan"], fixture["results"], fixture["manifest"]

    # All five schedulers lack the same taskset in one cell: still invalid even
    # though the remaining 29 are completely paired.
    victim = results[0]
    cell = (
        victim["energy_material"]["kappa"], victim["energy_material"]["eta"],
        victim["u_norm"], victim["taskset_semantic_hash"],
    )
    removed_ids = {
        row["semantic_request_id"] for row in results
        if (
            row["energy_material"]["kappa"], row["energy_material"]["eta"],
            row["u_norm"], row["taskset_semantic_hash"],
        ) == cell
    }
    short_plan = {**plan, "requests": [row for row in plan["requests"] if row["semantic_request_id"] not in removed_ids]}
    short_results = [row for row in results if row["semantic_request_id"] not in removed_ids]
    assert audit_calibration_phase("initial", short_plan, short_results, manifest).status == "CAL_INVALID"

    internal = [dict(row) for row in results]
    internal[0] = {
        **internal[0], "legacy_status": "SIM_INTERNAL_ERROR",
        "outcome": {"contract_version": "PERF_OUTCOME_V2", "observed_pass": False},
    }
    assert audit_calibration_phase("initial", plan, internal, manifest).status == "CAL_INVALID"

    timeout = [dict(row) for row in results]
    timeout[0] = {**timeout[0], "attempts": [{"legacy_status": "SIM_RUNTIME_TIMEOUT"}]}
    assert audit_calibration_phase("initial", plan, timeout, manifest).status == "CAL_INVALID"

    extra = [*results, {**results[0], "semantic_request_id": "extra"}]
    assert audit_calibration_phase("initial", plan, extra, manifest).status == "CAL_INVALID"

    wrong_execution = [dict(row) for row in results]
    wrong_execution[0] = {**wrong_execution[0], "execution_identity": "wrong"}
    assert audit_calibration_phase("initial", plan, wrong_execution, manifest).status == "CAL_INVALID"
