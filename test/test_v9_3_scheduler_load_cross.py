import json
from fractions import Fraction

from experiments.v9_3 import scheduler_load_cross as experiment
from scripts.analyze_scheduler_load_cross import analyze


def test_exact_ue_eta_mapping_and_deduplicated_cells():
    assert experiment.eta_for_ue(Fraction(2, 5)) == Fraction(5, 2)
    assert experiment.eta_for_ue(Fraction(3, 10)) == Fraction(10, 3)
    assert experiment.parse_cells("1/2:2/5,0.5:0.4,1/2:1/5") == (
        (Fraction(1, 2), Fraction(2, 5)), (Fraction(1, 2), Fraction(1, 5)),
    )
    assert len(experiment.DEFAULT_CELLS) == 12


def test_default_and_explicit_nine_scheduler_lists():
    assert experiment.parse_schedulers(None) == experiment.DEFAULT_SCHEDULERS
    assert experiment.parse_schedulers(",".join(experiment.ALL_SCHEDULERS)) == experiment.ALL_SCHEDULERS


def test_requests_pair_two_energy_cells_and_five_schedulers():
    class Taskset:
        taskset_id = "t"
        semantic_hash = "h"
        target_utilization = Fraction(2)
        actual_utilization = Fraction(2)
        processors = 4
        taskset_index = 0
        seed = 9
    rows = experiment.request_rows(
        [Taskset()], ((Fraction(1, 2), Fraction(1, 5)), (Fraction(1, 2), Fraction(2, 5))),
        experiment.DEFAULT_SCHEDULERS, 2000,
    )
    assert len(rows) == 10
    assert len({row["request_id"] for row in rows}) == 10
    assert len({row["taskset_id"] for row in rows}) == 1
    assert {row["target_ue"] for row in rows} == {"1/5", "2/5"}


def test_service_only_energy_material_preserves_canonical_power():
    class Taskset:
        processors = 4
        task_count = 2
        task_payload = ({"C": 1, "T": 10, "P": "2"}, {"C": 1, "T": 10, "P": "4"})
    material = experiment.energy_material(Taskset(), Fraction(2, 5), (Fraction(1),) * 10, kappa=Fraction(10), normalization_horizon=10)
    assert material["eta"] == "5/2"
    assert material["target_supply_mean_j_per_tick"] == "3/2"
    assert material["solar_scale"] == "3/2"
    assert material["energy_control"] == "SERVICE_ONLY_SCALING"


def test_analyzer_writes_both_figure_csvs(tmp_path):
    config = {"cells": [["1/2", "2/5"]], "samples_per_cell": 1,
              "schedulers": ["ASAP-BLOCK"], "processors": 4,
              "util_tolerance_total": "1/100"}
    taskset = {"taskset_id": "t", "taskset_hash": "h", "canonical_task_power": True,
               "target_uc": "1/2", "actual_uc": "1/2"}
    request = {"request_id": "r", "taskset_id": "t", "taskset_hash": "h",
               "target_uc": "1/2", "target_ue": "2/5", "generation_index": 0,
               "scheduler": "ASAP-BLOCK"}
    energy = {"target_ue": "2/5", "eta": "5/2", "P_dem_j_per_tick": "3/5",
              "target_supply_mean_j_per_tick": "3/2", "raw_reference_mean_j_per_tick": "1",
              "solar_scale": "3/2"}
    result = {**request, "energy": energy, "schedulable": True, "deadline_miss": False,
              "simulation_status": "SIM_PASS_OBSERVED", "technical_error": None}
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text(json.dumps(taskset) + "\n", encoding="utf-8")
    (tmp_path / "requests.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
    assert analyze(tmp_path)["complete"]
    assert (tmp_path / "figure_scheduler_uc.csv").is_file()
    assert (tmp_path / "figure_scheduler_ue.csv").is_file()
