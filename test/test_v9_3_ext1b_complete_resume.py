from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from experiments.v9_3.ext1b_config import load_ext1b_config
from experiments.v9_3.ext1b_engine import Ext1BRunner, verify_file_hashes
from experiments.v9_3.result_writer import write_file_hashes
from experiments.v9_3.simulation_engine import (
    SimulationExecution, load_simulation_terminal,
)
from experiments.v9_3.simulation_result import (
    SimulationResult, SimulationStatus, TaskObservation,
)


ROOT = Path(__file__).resolve().parents[1]


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _request_ids(root: Path) -> list[str]:
    with (root / "simulation_requests.csv").open(newline="", encoding="utf-8") as handle:
        return [row["request_id"] for row in csv.DictReader(handle)]


def _completed_run(tmp_path: Path, monkeypatch):
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    config["simulation"]["simulator_bin"] = str(tmp_path / "unused-rtsim")
    Path(config["simulation"]["simulator_bin"]).write_bytes(b"unused")
    calls: list[str] = []
    control = {"attempt_count": 1, "status": SimulationStatus.DEADLINE_MISS}

    def fake_simulation(**kwargs):
        simulation_id = str(kwargs["simulation_id_value"])
        scheduler = str(kwargs["scheduler_id"])
        calls.append(simulation_id)
        trace = Path(kwargs["run_root"]) / "retained_traces" / f"{simulation_id}.json"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text("{}\n", encoding="utf-8")
        tasks = tuple(
            TaskObservation(
                str(task["task_id"]), 1, 0, 1, 0, None, 0.0, False,
            )
            for task in kwargs["task_payload"]
        )
        metrics = {
            "missed_jobs": len(tasks), "first_miss_time": 1,
            "maximum_observed_response_time": None, "mean_response_time": None,
            "completed_jobs": 0, "preemptions": 0, "processor_wait_ticks": 0,
            "energy_blocked_ticks": 0,
            "bypass_count": 1 if scheduler == "gpfp_asap_nonblock" else 0,
            "synchronization_wait_ticks": 0,
            "idle_cores_while_ready_jobs_exist_ticks": 0,
            "st_charge_begin_count": 0, "st_charge_hold_ticks": 0,
            "st_charge_release_count": 0, "st_charge_release_reasons": [],
            "harvested_energy_j": 0.0, "consumed_energy_j": 0.0,
            "battery_minimum_j": 0.0, "battery_maximum_j": 0.0,
            "battery_trajectory": [],
        }
        status = control["status"]
        result = SimulationResult(
            status, "deadline_miss", int(config["simulation"]["horizon"]),
            (), tasks, True, 0.0, {}, 2, scheduler, True,
            "reached_horizon", metrics,
        )
        return SimulationExecution(
            simulation_id, result, 0.01, int(control["attempt_count"]),
            (int(config["simulation"]["horizon"]),),
            Path(kwargs["base_system_path"]), Path(kwargs["base_system_path"]),
            trace, "", "",
        )

    monkeypatch.setattr(
        "experiments.v9_3.ext1b_engine.run_paired_simulation", fake_simulation,
    )
    runner = Ext1BRunner(config)
    initial = runner.run()
    assert initial.requested == initial.terminal == 9
    assert len(calls) == 9
    assert verify_file_hashes(runner.root)
    return runner, calls, control


def test_fully_completed_resume_is_byte_noop(tmp_path, monkeypatch):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    before = _tree_hashes(runner.root)
    native_before = len(calls)
    outcome = runner.run(resume=True)
    assert outcome.requested == outcome.terminal == 9
    assert len(calls) == native_before
    assert _tree_hashes(runner.root) == before


def test_completed_resume_preserves_checkpoint_bytes(tmp_path, monkeypatch):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    path = runner.root / "checkpoint.json"
    before = path.read_bytes()
    timestamp = json.loads(before)["updated_at_utc"]
    runner.run(resume=True)
    assert path.read_bytes() == before
    assert json.loads(path.read_bytes())["updated_at_utc"] == timestamp
    assert len(calls) == 9


def test_completed_resume_preserves_cell_manifest_bytes(tmp_path, monkeypatch):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    path = runner.root / "file_hashes.sha256"
    before = path.read_bytes()
    runner.run(resume=True)
    assert path.read_bytes() == before
    assert verify_file_hashes(runner.root)
    assert len(calls) == 9


def test_repeated_completed_resume_is_byte_noop(tmp_path, monkeypatch):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    before = _tree_hashes(runner.root)
    first = runner.run(resume=True)
    after_first = _tree_hashes(runner.root)
    second = runner.run(resume=True)
    assert first.summary == second.summary
    assert before == after_first == _tree_hashes(runner.root)
    assert len(calls) == 9


def test_partial_resume_still_updates_state(tmp_path, monkeypatch):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    missing = runner.terminals / f"{_request_ids(runner.root)[-1]}.json"
    missing.unlink()
    checkpoint_before = (runner.root / "checkpoint.json").read_bytes()
    manifest_before = (runner.root / "file_hashes.sha256").read_bytes()
    outcome = runner.run(resume=True)
    assert outcome.terminal == 9 and missing.is_file()
    assert len(calls) == 10
    assert (runner.root / "checkpoint.json").read_bytes() != checkpoint_before
    assert (runner.root / "file_hashes.sha256").read_bytes() != manifest_before
    assert verify_file_hashes(runner.root)


def test_retry_state_change_resume_still_updates_state(tmp_path, monkeypatch):
    runner, calls, control = _completed_run(tmp_path, monkeypatch)
    missing = runner.terminals / f"{_request_ids(runner.root)[-1]}.json"
    missing.unlink()
    control["attempt_count"] = 2
    before = _tree_hashes(runner.root)
    runner.run(resume=True)
    resumed = load_simulation_terminal(missing)
    assert resumed.attempt_count == 2
    assert len(calls) == 10
    assert _tree_hashes(runner.root) != before
    assert verify_file_hashes(runner.root)


@pytest.mark.parametrize(
    "failure", ["terminal_identity", "result_identity", "config"],
)
def test_completed_resume_identity_and_config_fail_closed(
    tmp_path, monkeypatch, failure,
):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    if failure == "terminal_identity":
        terminal = runner.terminals / f"{_request_ids(runner.root)[0]}.json"
        payload = json.loads(terminal.read_text(encoding="utf-8"))
        payload["simulation_id"] = "wrong"
        terminal.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        write_file_hashes(runner.root)
        failing_runner = runner
        match = "simulation_id mismatch"
    elif failure == "result_identity":
        results_path = runner.root / "simulation_results.csv"
        with results_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        rows[0]["taskset_hash"] = "wrong"
        with results_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        write_file_hashes(runner.root)
        failing_runner = runner
        match = "P0 EXT-1B result identity mismatch"
    else:
        changed = deepcopy(runner.config)
        changed["statistics"]["bootstrap_seed"] += 1
        failing_runner = Ext1BRunner(changed)
        match = "config hash mismatch"
    before = _tree_hashes(runner.root)
    with pytest.raises(RuntimeError, match=match):
        failing_runner.run(resume=True)
    assert _tree_hashes(runner.root) == before
    assert len(calls) == 9


def test_noop_detection_requires_complete_output_contract(tmp_path, monkeypatch):
    runner, calls, _ = _completed_run(tmp_path, monkeypatch)
    missing = runner.root / "scheduler_summary.csv"
    missing.unlink()
    write_file_hashes(runner.root)
    before = _tree_hashes(runner.root)
    runner.run(resume=True)
    assert missing.is_file()
    assert len(calls) == 9
    assert _tree_hashes(runner.root) != before
    assert verify_file_hashes(runner.root)
