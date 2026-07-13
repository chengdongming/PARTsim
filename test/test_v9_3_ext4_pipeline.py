from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from experiments.v9_3.config import load_config
from experiments.v9_3.ext4_aggregation import aggregate_ext4
from experiments.v9_3.ext4_robustness import Ext4Runner
from experiments.v9_3.result_writer import write_csv
from experiments.v9_3.ext4_robustness import (
    CELL_COLUMNS, RTA_COLUMNS, SAMPLE_COLUMNS, SIM_COLUMNS,
)


ROOT = Path(__file__).resolve().parents[1]


def test_invalid_unregistered_priority_mode_is_rejected():
    config = load_config(ROOT / "configs/v9_3_ext4_smoke.yaml", expected_core="CORE-3")
    config["robustness"]["priority_policies"] = ["RM", "DM"]
    with pytest.raises(ValueError, match="audited registered"):
        Ext4Runner(config)


def test_resume_initialization_is_config_hash_guarded(tmp_path):
    config = load_config(ROOT / "configs/v9_3_ext4_smoke.yaml", expected_core="CORE-3")
    config["execution"]["output_root"] = str(tmp_path)
    runner = Ext4Runner(config)
    runner._initialize(False)
    runner._initialize(True)


def test_aggregation_preserves_rta_simulation_pairing_and_timeout(tmp_path):
    cells = [
        {"cell_id": "base-cell", "changed_axis": "baseline", "level": "implicit", "availability": "AVAILABLE", "pairing_type": "BASELINE", "reason": ""},
        {"cell_id": "derived-cell", "changed_axis": "deadline_mode", "level": "constrained", "availability": "AVAILABLE", "pairing_type": "PAIRED_SINGLE_AXIS", "reason": ""},
    ]
    samples = []
    for sample_id, base_id, cell_id, axis, level, kind in (
        ("base", "base", "base-cell", "baseline", "implicit", "BASELINE"),
        ("derived", "base", "derived-cell", "deadline_mode", "constrained", "PAIRED_SINGLE_AXIS"),
    ):
        samples.append({
            "sample_id": sample_id, "base_sample_id": base_id,
            "derived_sample_id": sample_id, "cell_id": cell_id,
            "changed_axis": axis, "level": level, "pairing_type": kind,
            "taskset_id": sample_id, "taskset_hash": sample_id,
            "before_canonical_input_hash": "before", "after_canonical_input_hash": "after",
            "priority_mapping_hash": "priority", "generation_seed": 1,
            "canonical_path": "path",
        })
    rta = []
    for sample_id in ("base", "derived"):
        for method in ("CW_THETA_CW", "LOC_THETA_LOC"):
            timeout = sample_id == "derived" and method == "LOC_THETA_LOC"
            rta.append({
                "analysis_id": sample_id + method, "sample_id": sample_id,
                "taskset_id": sample_id, "taskset_hash": sample_id, "method": method,
                "solver_status": "TIMEOUT" if timeout else "COMPLETED",
                "certification_status": "NOT_CERTIFIED" if timeout else "CERTIFIED_TASKSET",
                "taskset_proven": not timeout, "response_bound": "UNAVAILABLE" if timeout else 5,
                "candidate_vector_json": "{}", "runtime_seconds": 1,
                "attempt_count": 1, "timeout": timeout,
                "soundness_class": "NOT_EVALUABLE", "tightness_gap": "UNAVAILABLE",
                "p0_rta_pass_sim_fail": False, "dominance_violation": False,
            })
    simulations = [{
        "simulation_id": sample_id, "sample_id": sample_id,
        "taskset_id": sample_id, "taskset_hash": sample_id,
        "scheduler_id": "gpfp_asap_block", "status": "SIM_PASS_OBSERVED",
        "reason": "observed", "comparison_eligible": True, "horizon": 10,
        "runtime_seconds": 1, "maximum_observed_response_time": 4,
        "missed_jobs": 0, "energy_blocked_ticks": 0, "horizon_censoring": False,
    } for sample_id in ("base", "derived")]
    write_csv(tmp_path / "robustness_cells.csv", CELL_COLUMNS, cells)
    write_csv(tmp_path / "base_and_derived_samples.csv", SAMPLE_COLUMNS, samples)
    write_csv(tmp_path / "rta_results.csv", RTA_COLUMNS, rta)
    write_csv(tmp_path / "simulation_results.csv", SIM_COLUMNS, simulations)
    result = aggregate_ext4(tmp_path)
    assert result["paired_rows"] == 2
