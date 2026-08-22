from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.v9_3 import scheduler_priority_energy_load_cross as experiment
from experiments.v9_3.simulation_engine import _render_taskset_yaml
from scripts import analyze_scheduler_priority_energy_load_cross as analyzer
from scripts import run_scheduler_priority_energy_load_cross as runner


@dataclass(frozen=True)
class FakeBase:
    taskset_id: str = "base-1"
    semantic_hash: str = "a" * 64
    taskset_index: int = 0
    seed: int = 7
    processors: int = 4
    task_count: int = 6
    target_utilization: Fraction = Fraction("1/2")
    actual_utilization: Fraction = Fraction("1/2")

    def generated_row(self):
        return {"taskset_id": self.taskset_id, "taskset_hash": self.semantic_hash}


def make_taskset() -> experiment.PriorityTaskset:
    # IDs and periods intentionally do not encode priority.  Only the frozen
    # priority_rank field is allowed to select HP/LP groups.
    rows = tuple({
        "task_id": task_id, "priority_rank": rank, "C": 10,
        "D": period, "T": period, "P": "1/10", "workload": "hash",
        "arrival_offset": 0,
    } for rank, (task_id, period) in enumerate(
        (("task-z", 100), ("task-a", 80), ("task-q", 60),
         ("task-b", 40), ("task-y", 120), ("task-c", 140)),
    ))
    return experiment.PriorityTaskset(
        FakeBase(), rows, "b" * 64, "c" * 64,
    )


def test_factor_identity_conservation_and_exact_grouping():
    taskset = make_taskset()
    material = experiment.priority_energy_material(taskset, Fraction(2))
    assert material["H_base"] == "77/1200"
    assert material["L_base"] == "13/840"
    assert Fraction(material["P_dem_base"]) == Fraction(material["P_dem_transformed"])
    assert [row["group"] for row in material["tasks"]] == ["HP"] * 4 + ["LP"] * 2
    assert material["tasks"][0]["task_id"] == "task-z"
    assert material["tasks"][4]["task_id"] == "task-y"

    identity = experiment.priority_energy_material(taskset, Fraction(1))
    assert identity["high_factor"] == "1"
    assert identity["low_factor"] == "1"
    assert [row["transformed_P"] for row in identity["tasks"]] == ["1/10"] * 6


def test_reference_battery_and_supply_are_paired_across_rho():
    taskset = make_taskset()
    raw = (Fraction(1), Fraction(1))
    rho1 = experiment.energy_material(
        experiment.priority_energy_material(taskset, Fraction(1)), Fraction("1/2"), raw,
        kappa=Fraction(10),
    )
    rho2 = experiment.energy_material(
        experiment.priority_energy_material(taskset, Fraction(2)), Fraction("1/2"), raw,
        kappa=Fraction(10),
    )
    assert rho1["battery_capacity_j"] == rho2["battery_capacity_j"]
    assert rho1["initial_energy_j"] == rho2["initial_energy_j"]
    assert rho1["P_dem_j_per_tick"] == rho2["P_dem_j_per_tick"]
    assert Fraction(rho1["eta"]) * Fraction(rho1["target_ue"]) == 1
    assert Fraction(rho1["solar_scale"]) * Fraction(rho1["raw_reference_mean_j_per_tick"]) == Fraction(rho1["target_supply_mean_j_per_tick"])


def test_request_pairing_and_ids_include_rho_and_scheduler():
    taskset = make_taskset()
    rows = experiment.request_rows(
        [taskset], [(Fraction("1/8"), Fraction("1/2"))],
        (Fraction(1), Fraction(2)), ("ASAP-BLOCK", "ASAP-NONBLOCK"), 1000,
    )
    assert len(rows) == 4
    assert len({row["request_id"] for row in rows}) == 4
    assert {row["rho"] for row in rows} == {"1", "2"}
    assert {row["scheduler"] for row in rows} == {"ASAP-BLOCK", "ASAP-NONBLOCK"}
    assert {row["base_taskset_hash"] for row in rows} == {taskset.base_hash}


def test_task_energy_factor_rendering_is_decimal_and_optional():
    payload = tuple({
        "task_id": task_id, "priority_rank": rank, "C": 1,
        "D": 10, "T": 10, "workload": "hash", "arrival_offset": 0,
    } for rank, task_id in enumerate(("a", "b")))
    legacy = _render_taskset_yaml(payload)
    assert "task_energy_factor" not in legacy
    projected = _render_taskset_yaml(
        payload, task_energy_factors={"a": "0.5", "b": "2"},
    )
    assert "task_energy_factor=0.5,workload=hash" in projected
    assert "task_energy_factor=2,workload=hash" in projected


def test_invalid_priority_order_and_ratio_are_rejected():
    taskset = make_taskset()
    bad_rows = list(taskset.task_payload)
    bad_rows[0] = {**bad_rows[0], "priority_rank": 9}
    bad = experiment.PriorityTaskset(taskset.base, tuple(bad_rows), taskset.base_hash, taskset.priority_hash)
    with pytest.raises(ValueError, match="priority ranks"):
        experiment.priority_energy_material(bad, Fraction(2))
    with pytest.raises(ValueError, match="include reference"):
        experiment.parse_ratios("1")
    with pytest.raises(ValueError, match="at least one"):
        experiment.parse_ratios("1/2,2")


def test_hash_projection_contract_is_explicit():
    assert experiment.DEFAULT_RATIOS == (Fraction(1), Fraction(2))
    assert experiment.REFERENCE_RATIO == Fraction(2)
    assert experiment.DEFAULT_SCHEDULERS == tuple(experiment.perf_g.CAL_SCHEDULERS)
    assert experiment.parse_schedulers("ASAP-BLOCK,ASAP-NONBLOCK") == (
        "ASAP-BLOCK", "ASAP-NONBLOCK",
    )
    taskset = make_taskset()
    assert all(row["workload"] == "hash" for row in taskset.task_payload)


def test_runtime_power_reproduces_scheduler_operation_order():
    payload = ({
        "task_id": "0", "priority_rank": 0, "C": 5, "D": 10,
        "T": 10, "P": "1/2500", "workload": "hash", "arrival_offset": 0,
    },)
    powers = experiment.runtime_task_powers(
        payload, {"0": "2"}, Path("v9_3_b4_priority_energy_system_template.yml"),
    )
    assert powers["0"]["runtime_power_float"] == pytest.approx(0.0008)
    assert powers["0"]["runtime_power_binary64_hex"] == powers["0"]["runtime_power_float"].hex()
    assert Fraction(powers["0"]["runtime_power_exact_fraction"]) == Fraction.from_float(
        powers["0"]["runtime_power_float"]
    )


def test_figure_slices_are_exact_and_fail_closed():
    cells = ((Fraction(1, 5), Fraction(2, 5)), (Fraction(2, 5), Fraction(2, 5)))
    slices = experiment.resolve_figure_slices(cells, fixed_uc=Fraction(1, 5))
    assert slices["uc_scan"]["fixed_value"] == "2/5"
    assert analyzer.select_scan_rows(
        [{"target_uc": "1/5", "target_ue": "2/5", "acceptance_ratio": 1}],
        "target_ue", "2/5",
    )[0]["target_uc"] == "1/5"
    with pytest.raises(ValueError, match="absent"):
        experiment.resolve_figure_slices(cells, fixed_ue=Fraction(1, 2))


def test_paired_counts_use_taskset_identity_not_row_position():
    block = [
        {"taskset_id": "b", "taskset_pass": True},
        {"taskset_id": "a", "taskset_pass": False},
    ]
    nonblock = [
        {"taskset_id": "a", "taskset_pass": True},
        {"taskset_id": "b", "taskset_pass": True},
    ]
    counts = analyzer.paired_counts(block, nonblock)
    assert counts["n"] == 2
    assert counts["both_pass"] == 1
    assert counts["block_only"] == 0
    assert counts["nonblock_only"] == 1
    with pytest.raises(SystemExit, match="pairing"):
        analyzer.paired_counts(block, nonblock[:-1])


def test_resume_ignores_execution_only_changes():
    stored = {"seed": 1, "cells": [["1/5", "1/2"]], "execution": {"workers": 2}}
    requested = {"seed": 1, "cells": [["1/5", "1/2"]], "execution": {"workers": 1}}
    assert runner.resume_configuration_matches(stored, requested)
    assert not runner.resume_configuration_matches(stored, {"seed": 2, "cells": [["1/5", "1/2"]]})
