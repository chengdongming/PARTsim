from __future__ import annotations

import pytest

from experiments.v9_3.scheduler_pairing import assert_scheduler_only_difference, paired_relation
from experiments.v9_3.scheduler_registry import SCHEDULER_IDS, audited_scheduler_registry


def _rows():
    return [{
        "paired_instance_id": "pair", "scheduler_id": scheduler,
        "taskset_hash": "task", "trace_hash": "trace",
        "simulation_config_hash": "sim", "input_hash": "input",
        "initial_battery": "20", "battery_capacity": "100",
        "horizon": 300, "generation_seed": 1,
    } for scheduler in SCHEDULER_IDS]


def test_nine_scheduler_registry_is_source_audited_and_unique():
    registry = audited_scheduler_registry()
    assert len(registry) == len(set(SCHEDULER_IDS)) == 9
    assert {item.timing_family for item in registry} == {"ASAP", "ALAP", "ST"}
    assert {item.mechanism for item in registry} == {"BLOCK", "NONBLOCK", "SYNC"}


def test_scheduler_is_the_only_paired_dimension():
    assert_scheduler_only_difference(_rows())
    broken = _rows()
    broken[-1]["trace_hash"] = "different"
    with pytest.raises(RuntimeError, match="P0 scheduler pairing mismatch"):
        assert_scheduler_only_difference(broken)


def test_missing_and_duplicate_scheduler_requests_are_rejected():
    with pytest.raises(RuntimeError, match="missing/duplicate"):
        assert_scheduler_only_difference(_rows()[:-1])


def test_paired_relation_keeps_nonterminal_states_separate():
    assert paired_relation({"status": "SIM_PASS_OBSERVED"}, {"status": "SIM_DEADLINE_MISS"}) == "LEFT_WIN"
    assert paired_relation({"status": "SIM_RUNTIME_TIMEOUT"}, {"status": "SIM_DEADLINE_MISS"}) == "NOT_COMPARABLE"
