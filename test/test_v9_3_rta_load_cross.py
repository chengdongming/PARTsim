from fractions import Fraction
import json

import asap_block_rta_v9_3_methods as methods
from experiments.v9_3 import rta_load_cross as cross


def test_frozen_cells_and_static_counts():
    assert len(cross.frozen_cells()) == 42
    assert cross.static_counts() == {
        "cells": 42, "skeletons": 4000, "scaled_tasksets": 21000, "requests": 168000,
    }


def test_cross_scan_deduplicates_nine_shared_cells():
    first = {(uc, ue) for uc in cross.FROZEN_UC for ue in cross.FROZEN_UE_FIRST}
    second = {(uc, ue) for uc in cross.FROZEN_UC_SECOND for ue in cross.FROZEN_UE_SECOND}
    assert len(first & second) == 9
    assert len(cross.frozen_cells()) == len(first | second)


def test_exact_energy_and_scaling_and_pairing(tmp_path):
    config = tmp_path / "system.yml"
    config.write_text("""
energy_management:
  scheduler_energy_model:
    base_power: 0.5
    frequency_power_ratios: {8100: 0.93}
    workload_coefficients:
      bzip2: 1.2
      control: 0.1
      decrypt: 1.5
      encrypt: 1.5
      hash: 0.8
""", encoding="utf-8")
    energies = cross._load_exact_energy_model(config)
    assert energies == {
        "bzip2": Fraction(279, 500), "control": Fraction(93, 2000),
        "decrypt": Fraction(279, 400), "encrypt": Fraction(279, 400),
        "hash": Fraction(93, 250),
    }
    skeleton = (
        {"name": "task_0", "priority": 0, "C": 2, "D": 4, "T": 5, "workload": "bzip2"},
        {"name": "task_1", "priority": 1, "C": 1, "D": 3, "T": 7, "workload": "hash"},
    )
    low = cross.scale_skeleton(skeleton, target_uc=Fraction(1, 10), target_ue=Fraction(1, 2), generation_index=0, seed=1, processors=4, rho=Fraction(11, 2), base_energies=energies)
    high = cross.scale_skeleton(skeleton, target_uc=Fraction(1, 10), target_ue=Fraction(4, 5), generation_index=0, seed=1, processors=4, rho=Fraction(11, 2), base_energies=energies)
    assert low["actual_ue"] == "1/2"
    assert high["actual_ue"] == "4/5"
    assert [(row["C"], row["D"], row["T"], row["workload"], row["priority"]) for row in low["tasks"]] == [(row["C"], row["D"], row["T"], row["workload"], row["priority"]) for row in high["tasks"]]


def test_stable_ids_seeds_and_method_catalog():
    seed = cross.stable_seed(20260814, 4, 10, Fraction(3, 10), 7)
    assert seed == cross.stable_seed(20260814, 4, 10, Fraction(3, 10), 7)
    assert seed != cross.stable_seed(20260814, 4, 10, Fraction(3, 10), 8)
    assert cross.request_id("uc0.3-i0000-ue0.8", Fraction(37), "SEQ") == "uc0.3-i0000-ue0.8-e0-37-SEQ"
    assert [methods.method_spec_v9_3(cross.METHOD_DISPLAY_TO_ID[name]).display_name for name in ("CW", "LOC", "PH", "SEQ")] == ["CW", "LOC", "PH", "SEQ"]


def test_core3_export_is_slice_of_existing_tasksets(tmp_path):
    rows = []
    for uc in cross.FROZEN_UC:
        for index in range(2):
            rows.append({"taskset_id": cross.taskset_id(uc, index, Fraction(4, 5)), "target_uc": cross.fraction_text(uc), "target_ue": "4/5", "generation_index": index, "tasks": []})
    path = tmp_path / "core3.jsonl"
    assert cross.export_core3_tasksets(rows, path) == 16
    assert len(path.read_text(encoding="utf-8").splitlines()) == 16
