from __future__ import annotations

from pathlib import Path

from experiments.v9_3.ext1_aggregation import aggregate_ext1_rows
from experiments.v9_3.ext1_scheduler_comparison import Ext1Runner
from experiments.v9_3.simulation_result import SimulationStatus
from experiments.v9_3.scheduler_registry import SCHEDULER_IDS


ROOT = Path(__file__).resolve().parents[1]


def test_ext1_dry_run_has_exact_smoke_cardinality():
    runner = Ext1Runner.from_path(ROOT / "configs/v9_3_ext1_smoke.yaml")
    description = runner.describe()
    assert description["simulation_request_count"] == 18
    assert description["scheduler_count"] == 9


def test_aggregation_uses_requested_valid_and_observed_denominators():
    requests = [
        {"request_id": scheduler, "scheduler_id": scheduler}
        for scheduler in SCHEDULER_IDS
    ]
    results = []
    for index, scheduler in enumerate(SCHEDULER_IDS):
        status = "SIM_PASS_OBSERVED" if index == 0 else "SIM_HORIZON_INSUFFICIENT"
        results.append({
            "request_id": scheduler, "paired_instance_id": "pair",
            "scheduler_id": scheduler, "status": status,
            "taskset_hash": "task", "trace_hash": "trace",
            "maximum_observed_response_time": "UNAVAILABLE",
            "energy_blocked_ticks": 0, "processor_wait_ticks": "UNAVAILABLE",
            "runtime_seconds": 1,
        })
    paired, summary, plots = aggregate_ext1_rows(requests, results)
    first = next(row for row in summary if row["scheduler_id"] == SCHEDULER_IDS[0])
    second = next(row for row in summary if row["scheduler_id"] == SCHEDULER_IDS[1])
    assert first["requested_denominator"] == first["valid_terminal_denominator"] == 1
    assert second["requested_denominator"] == 1
    assert second["valid_terminal_denominator"] == 0
    assert second["schedulability_ratio_valid"] == "UNAVAILABLE"
    assert len(paired) == 36
    assert plots


def test_terminal_status_vocabulary_keeps_timeout_and_censoring_distinct():
    assert {status.value for status in SimulationStatus} == {
        "SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS",
        "SIM_HORIZON_INSUFFICIENT", "SIM_RUNTIME_TIMEOUT",
        "SIM_INTERNAL_ERROR",
    }
