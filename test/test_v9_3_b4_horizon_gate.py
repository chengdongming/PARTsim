from experiments.v9_3.performance_config import FORMAL_UTILIZATIONS, PRIMARY_SCHEDULERS
from experiments.v9_3.performance_horizon_gate import INVALID_GATE, SELECT_30S, SELECT_60S, decide_horizon_gate


def gate_rows(change=False):
    rows = []
    for utilization in FORMAL_UTILIZATIONS:
        for index in range(50):
            for scheduler in PRIMARY_SCHEDULERS:
                for horizon in (30000, 60000):
                    passed = True
                    if change and horizon == 60000 and index < 10:
                        passed = False
                    rows.append({
                        "semantic_request_id": f"{utilization}-{index}-{scheduler}-{horizon}",
                        "u_norm": utilization, "taskset_semantic_hash": f"{utilization}-{index}",
                        "scheduler_id": scheduler, "horizon_ms": horizon,
                        "observed_pass": passed, "identity_valid": True,
                        "outcome_recomputed": True, "minimum_jobs_satisfied": True,
                        "simulation_reached_horizon": True,
                    })
    return rows


def test_select_30_when_all_preregistered_checks_hold():
    assert decide_horizon_gate(gate_rows()).state == SELECT_30S


def test_select_60_is_normal_when_stability_threshold_fails():
    assert decide_horizon_gate(gate_rows(change=True)).state == SELECT_60S


def test_technical_or_identity_failure_is_invalid_gate():
    rows = gate_rows()
    rows[0]["identity_valid"] = False
    assert decide_horizon_gate(rows).state == INVALID_GATE
