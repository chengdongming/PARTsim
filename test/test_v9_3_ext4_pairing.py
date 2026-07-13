from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import asap_block_rta_v9_3 as core
import pytest

from experiments.v9_3.ext4_robustness import derive_constrained_deadline_sample
from experiments.v9_3.priority_policy import apply_priority_policy, registered_priority_policies
from experiments.v9_3.robustness_pairing import pairing_type, verify_single_axis
from experiments.v9_3.taskset_store import StoredTaskset


def payload():
    return (
        {"task_id": "0", "source_name": "a", "priority_rank": 0, "C": 2, "D": 10, "T": 10, "P": "1/10", "D_over_T": "1", "workload": "hash", "arrival_offset": 0},
        {"task_id": "1", "source_name": "b", "priority_rank": 1, "C": 1, "D": 20, "T": 20, "P": "1/5", "D_over_T": "1", "workload": "bzip2", "arrival_offset": 0},
    )


def stored(tmp_path):
    rows = payload()
    return StoredTaskset(
        "base", "generation", 0, 7, "a" * 64, "b" * 64, "c" * 64,
        Fraction(1, 5), Fraction(1, 4), 4, 2, "implicit",
        tuple(core.V93Task(row["task_id"], row["C"], row["D"], row["T"], Fraction(row["P"])) for row in rows),
        rows, 0.0, "service", tmp_path / "base.json",
    )


def test_deadline_axis_changes_only_deadline_fields_and_is_deterministic(tmp_path):
    first, checks = derive_constrained_deadline_sample(stored(tmp_path), tmp_path)
    second, _ = derive_constrained_deadline_sample(stored(tmp_path), tmp_path)
    assert first.task_payload == second.task_payload
    assert all(row["status"] == "PASS" for row in checks)
    assert all(left["C"] == right["C"] and left["T"] == right["T"] and left["P"] == right["P"] for left, right in zip(payload(), first.task_payload))


def test_priority_adapter_exposes_only_rm_and_preserves_task_parameters():
    assert registered_priority_policies() == ("RM",)
    reversed_rows = tuple(reversed(payload()))
    transformed = apply_priority_policy(reversed_rows, "RM")
    verify_single_axis(reversed_rows, transformed, "priority_policy")
    assert [row["T"] for row in transformed] == [10, 20]
    with pytest.raises(ValueError, match="unsupported priority"):
        apply_priority_policy(payload(), "DM")


def test_period_range_is_never_misrepresented_as_paired():
    assert pairing_type("period_range") == "UNPAIRED_STRATIFIED_COMPARISON"
