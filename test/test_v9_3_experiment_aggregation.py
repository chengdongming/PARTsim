from experiments.v9_3.aggregation import variant_summary


def test_denominators_are_reported_separately():
    requests = [
        {"cell_id": "c", "variant": "CW_THETA_CW"},
        {"cell_id": "c", "variant": "CW_THETA_CW"},
        {"cell_id": "c", "variant": "CW_THETA_CW"},
    ]
    results = [
        {"cell_id": "c", "analysis_variant": "CW_THETA_CW", "solver_status": "COMPLETED", "taskset_proven": "True", "runtime_wall_seconds": "1"},
        {"cell_id": "c", "analysis_variant": "CW_THETA_CW", "solver_status": "NO_CANDIDATE", "taskset_proven": "False", "runtime_wall_seconds": "2"},
        {"cell_id": "c", "analysis_variant": "CW_THETA_CW", "solver_status": "TIMEOUT", "taskset_proven": "False", "runtime_wall_seconds": "3"},
    ]
    row = variant_summary(requests, results)[0]
    assert row["unconditional_denominator"] == 3
    assert row["completed_only_denominator"] == 2
    assert row["certification_ratio_unconditional"] == 1 / 3
    assert row["certification_ratio_completed_only"] == 1 / 2
