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
    JobObservation, SimulationResult, SimulationStatus, TaskObservation,
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


def _completed_run(tmp_path: Path, monkeypatch, scheduler_ids=None):
    config = load_ext1b_config(ROOT / "configs/v9_3_ext1b1_smoke.yaml")
    if scheduler_ids is not None:
        config["scheduler_ids"] = list(scheduler_ids)
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
        first, second = kwargs["task_payload"][:2]
        events = []
        if scheduler == "gpfp_asap_nonblock":
            blocked = {
                "task_name": f"v93_task_{first['task_id']}",
                "arrival_time": 0,
            }
            bypassed = {
                "task_name": f"v93_task_{second['task_id']}",
                "arrival_time": 0,
            }
            events.extend([
                {
                    "time": 0, "event_type": "scheduler_decision",
                    "scheduler": "ASAP-NonBlock",
                    "ready_jobs": [blocked, bypassed],
                    "selected_jobs": [bypassed],
                },
                {
                    "time": 0, "event_type": "nonblock_bypass",
                    "scheduler": "ASAP-NonBlock",
                    "blocked_higher_priority_task": blocked["task_name"],
                    "bypassed_task": bypassed["task_name"],
                    "reason": "lower_priority_bypass_due_to_energy",
                },
                {
                    "time": 1, "event_type": "dline_miss", **blocked,
                },
            ])
        trace.write_text(json.dumps({
            "trace_schema_version": 2,
            "configured_scheduler": scheduler,
            "events": events,
        }) + "\n", encoding="utf-8")
        tasks = tuple(
            TaskObservation(
                str(task["task_id"]), 1, 0, 1, 0, None, 0.0, False,
            )
            for task in kwargs["task_payload"]
        )
        jobs = tuple(
            JobObservation(
                str(task["task_id"]), 0, 0, None, int(task["D"]), None,
                True, None, 0, 0, None, 0, True, False, None,
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
            jobs, tasks, True, 0.0, {}, 2, scheduler, True,
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
    expected = len(config["scheduler_ids"])
    assert initial.requested == initial.terminal == expected
    assert len(calls) == expected
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


def test_two_scheduler_completed_resume_and_b1_dimensions(tmp_path, monkeypatch):
    selected = ("gpfp_asap_block", "gpfp_asap_nonblock")
    runner, calls, _ = _completed_run(tmp_path, monkeypatch, selected)
    with (runner.root / "simulation_requests.csv").open(
        newline="", encoding="utf-8",
    ) as handle:
        assert [row["scheduler_id"] for row in csv.DictReader(handle)] == list(selected)
    with (runner.root / "scheduler_registry.csv").open(
        newline="", encoding="utf-8",
    ) as handle:
        assert [row["scheduler_id"] for row in csv.DictReader(handle)] == list(selected)
    before = _tree_hashes(runner.root)
    outcome = runner.run(resume=True)
    assert outcome.requested == outcome.terminal == 2
    assert len(calls) == 2
    assert _tree_hashes(runner.root) == before

    for name in (
        "b1_bypass_episodes.csv", "b1_task_effects.csv",
        "b1_paired_effects.csv", "b1_summary.csv",
    ):
        with (runner.root / name).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        assert {row["normalized_utilization"] for row in rows} == {"1/5"}
        assert {row["nominal_energy_supply_ratio"] for row in rows} == {"0"}


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
