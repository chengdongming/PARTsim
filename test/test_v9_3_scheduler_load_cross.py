import json
from fractions import Fraction
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3.simulation_result import SimulationStatus
from scripts.analyze_scheduler_load_cross import analyze
import scripts.run_scheduler_load_cross as scheduler_runner


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


class _FakeTaskset:
    taskset_id = "taskset-0"
    semantic_hash = "hash-0"
    target_utilization = Fraction(2, 5)
    actual_utilization = Fraction(2, 5)
    processors = 4
    task_count = 1
    taskset_index = 0
    seed = 710213
    task_payload = ({"task_id": "task-0", "C": 1, "D": 2, "T": 4, "P": "1"},)

    def generated_row(self):
        return {
            "taskset_id": self.taskset_id,
            "taskset_hash": self.semantic_hash,
            "target_utilization": "2/5",
            "actual_utilization": "2/5",
        }


def _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation):
    taskset = _FakeTaskset()
    service = SimpleNamespace(system_path=tmp_path / "system.yml")
    service.system_path.write_text("system", encoding="utf-8")
    request = {
        "request_id": "scheduler-load-cross-test-request",
        "taskset_id": taskset.taskset_id,
        "taskset_hash": taskset.semantic_hash,
        "target_uc": "1/10", "actual_uc": "1/10",
        "target_ue": "2/5", "eta": "5/2",
        "generation_index": 0, "seed": taskset.seed,
        "scheduler": "ASAP-BLOCK", "scheduler_cli": "gpfp_asap_block",
        "horizon_ms": 20,
    }
    monkeypatch.setattr(
        scheduler_runner.experiment, "materialize_tasksets",
        lambda *args, **kwargs: ([taskset], service),
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: [dict(request)],
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "construct_paired_harvest_trace",
        lambda *args, **kwargs: (Fraction(1),) * 10,
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "energy_material",
        lambda *args, **kwargs: {
            "target_ue": "2/5", "eta": "5/2",
            "initial_energy_j": "1", "battery_capacity_j": "2",
            "solar_scale": "1", "P_dem_j_per_tick": "1",
            "raw_reference_mean_j_per_tick": "1",
        },
    )
    monkeypatch.setattr(
        scheduler_runner, "evaluate_outcome",
        lambda *args, **kwargs: {
            "outcome_status": "AVAILABLE", "taskset_pass": True,
        },
    )
    monkeypatch.setattr(scheduler_runner, "run_paired_simulation", run_simulation)


def _scheduler_runner_args(output, resume=False):
    return [
        "--output", str(output), "--seed", "710213", "--workers", "1",
        "--samples-per-cell", "1", "--cells", "0.1:0.4",
        "--schedulers", "ASAP-BLOCK", "--simulation-horizon", "20",
        "--timeout-seconds", "5", "--simulator", str(output / "rtsim"),
        *( ["--resume"] if resume else [] ),
    ]


def test_attempt_history_separates_technical_failure_and_retries_in_new_dir(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs["run_root"])
        if len(calls) == 1:
            raise RuntimeError("trace_target_locked")
        result = SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="out",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    assert not (output / "results.jsonl").exists()
    first_attempt = output / "simulations" / "scheduler-load-cross-test-request" / "attempt_0001"
    assert first_attempt.is_dir()
    history = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert [(row["attempt_index"], row["simulation_status"]) for row in history] == [(1, "TECHNICAL_FAILURE")]

    stale_lock = first_attempt / "simulation_trace_work" / "trace.lock"
    stale_lock.parent.mkdir(parents=True)
    stale_lock.write_text("pid=999999\n", encoding="utf-8")
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 0
    second_attempt = output / "simulations" / "scheduler-load-cross-test-request" / "attempt_0002"
    assert second_attempt.is_dir()
    assert stale_lock.is_file()
    assert calls == [first_attempt, second_attempt]
    results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(results) == 1
    assert results[0]["request_id"] == "scheduler-load-cross-test-request"
    history = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert [row["attempt_index"] for row in history] == [1, 2]
    assert len({row["request_id"] for row in results}) == 1


def test_completed_request_resume_does_not_create_attempt(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs)
        raise AssertionError("completed request was re-executed")

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    calls.clear()
    attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    terminal = {
        "request_id": attempts[0]["request_id"],
        "taskset_id": attempts[0]["taskset_id"], "taskset_hash": attempts[0]["taskset_hash"],
        "target_uc": "1/10", "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
        "scheduler": "ASAP-BLOCK", "simulation_status": "SIM_PASS_OBSERVED",
        "technical_error": None, "energy": {"eta": "5/2", "target_ue": "2/5"},
    }
    (output / "results.jsonl").write_text(json.dumps(terminal) + "\n", encoding="utf-8")
    before = sorted((output / "simulations" / terminal["request_id"]).iterdir())
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 0
    assert calls == []
    assert sorted((output / "simulations" / terminal["request_id"]).iterdir()) == before


def test_legacy_technical_row_in_active_results_fails_closed(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    (output / "results.jsonl").write_text(json.dumps({
        "request_id": "scheduler-load-cross-test-request",
        "simulation_status": "SIM_INTERNAL_ERROR", "technical_error": "old failure",
    }) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="migration/recovery"):
        scheduler_runner.main(_scheduler_runner_args(output, resume=True))


def test_live_attempt_lock_fails_closed_without_allocating_next_attempt(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs["run_root"])
        raise RuntimeError("interrupted")

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    calls.clear()
    request_root = output / "simulations" / "scheduler-load-cross-test-request"
    live_lock = request_root / "attempt_0001" / "simulation_trace_work" / "trace.lock"
    live_lock.parent.mkdir(parents=True)
    live_lock.write_text(f"pid={os.getpid()}\n", encoding="utf-8")
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True)) == 2
    assert calls == []
    assert not (request_root / "attempt_0002").exists()


def test_existing_attempt_directory_without_history_is_never_overwritten(tmp_path, monkeypatch):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs["run_root"])
        result = SimpleNamespace(
            status=SimulationStatus.DEADLINE_MISS, reason="deadline_miss",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "campaign"
    old_attempt = output / "simulations" / "scheduler-load-cross-test-request" / "attempt_0001"
    old_attempt.mkdir(parents=True)
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 0
    assert calls == [old_attempt.parent / "attempt_0002"]
    assert old_attempt.is_dir()
    assert (old_attempt.parent / "attempt_0002").is_dir()
    results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert len(results) == 1
    assert results[0]["simulation_status"] == SimulationStatus.DEADLINE_MISS.value


def test_duplicate_active_request_ids_fail_closed(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    output = tmp_path / "campaign"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    attempt = json.loads((output / "attempts.jsonl").read_text().splitlines()[0])
    terminal = {
        "request_id": attempt["request_id"], "taskset_id": attempt["taskset_id"],
        "taskset_hash": attempt["taskset_hash"], "simulation_status": "SIM_PASS_OBSERVED",
        "technical_error": None,
    }
    (output / "results.jsonl").write_text(
        json.dumps(terminal) + "\n" + json.dumps(terminal) + "\n", encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="duplicate"):
        scheduler_runner.main(_scheduler_runner_args(output, resume=True))
