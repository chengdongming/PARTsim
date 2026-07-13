from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import asap_block_rta_v9_3 as core

from experiments.v9_3.core4_sensitivity import scale_taskset_power
from experiments.v9_3.monotonicity import service_curve_relation
from experiments.v9_3.paired_sweep import make_sweep
from experiments.v9_3.taskset_store import StoredTaskset


def _stored(tmp_path: Path) -> StoredTaskset:
    tasks = (core.V93Task("0", 1, 5, 7, Fraction(3, 2)),)
    payload = ({
        "task_id": "0", "source_name": "a", "priority_rank": 0,
        "C": 1, "D": 5, "T": 7, "P": "3/2", "D_over_T": "5/7",
        "workload": "idle", "arrival_offset": 0,
    },)
    return StoredTaskset(
        "base", "generation", 0, 7, "semantic", "priority", "power",
        Fraction(1, 5), Fraction(1, 7), 4, 1, "constrained", tasks,
        payload, 0, "service", tmp_path / "taskset.json",
    )


def test_power_scale_is_exact_and_keeps_paired_generation_identity(tmp_path):
    base = _stored(tmp_path)
    scaled = scale_taskset_power(base, Fraction(4, 3))
    assert scaled.semantic_hash == base.semantic_hash
    assert scaled.taskset_id == base.taskset_id
    assert (scaled.tasks[0].wcet, scaled.tasks[0].deadline, scaled.tasks[0].period) == (1, 5, 7)
    assert scaled.tasks[0].power == Fraction(2)
    assert scaled.task_payload[0]["P"] == "2"
    assert scaled.power_hash != base.power_hash


def test_sweep_identity_is_deterministic_and_target_levels_are_not_seed_material():
    first = make_sweep("experiment", "initial_energy", ["0", "1"])
    second = make_sweep("experiment", "initial_energy", ["0", "1"])
    assert first == second
    assert first.level_encodings == ('"0"', '"1"')


def test_service_strength_uses_exact_pointwise_values_not_names():
    assert service_curve_relation([0, "1/2", 1], [0, "3/4", 1]) == "RIGHT_STRONGER"
    assert service_curve_relation([0, 1, 1], [0, "1/2", 2]) == "NOT_COMPARABLE"
    assert service_curve_relation([0, 1], [0, 1]) == "EQUAL"
