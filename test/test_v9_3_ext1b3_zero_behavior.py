"""Baseline/candidate byte-equivalence gate for additive B3 observations."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test"))

import acceptance_ratio_test as acceptance  # noqa: E402
from test_scheduler_trace_identity import system_yaml, task_yaml  # noqa: E402


BASELINE_ENV = "PARTSIM_B3_BASELINE_RTSIM_BIN"
CANDIDATE_ENV = "PARTSIM_B3_CANDIDATE_RTSIM_BIN"

SCHEDULERS = (
    "gpfp_asap_block",
    "gpfp_asap_nonblock",
    "gpfp_asap_sync",
    "gpfp_alap_block",
    "gpfp_alap_nonblock",
    "gpfp_alap_sync",
    "gpfp_st_block",
    "gpfp_st_nonblock",
    "gpfp_st_sync",
)

SCENARIOS = {
    "positive_slack_abundant": {
        "initial": 1.0,
        "maximum": 1.0,
        "harvest": 0.0,
        "duration": 4,
        "wcet": 2,
        "deadline": 20,
    },
    "urgent_abundant": {
        "initial": 1.0,
        "maximum": 1.0,
        "harvest": 0.0,
        "duration": 3,
        "wcet": 2,
        "deadline": 2,
    },
    "positive_slack_energy_shortage": {
        "initial": 0.0,
        "maximum": 1.0,
        "harvest": 0.0,
        "duration": 3,
        "wcet": 1,
        "deadline": 20,
    },
}


def _binaries():
    raw_base = os.environ.get(BASELINE_ENV, "").strip()
    raw_candidate = os.environ.get(CANDIDATE_ENV, "").strip()
    if not raw_base or not raw_candidate:
        pytest.skip(f"set {BASELINE_ENV} and {CANDIDATE_ENV}")
    binaries = (Path(raw_base).resolve(), Path(raw_candidate).resolve())
    for binary in binaries:
        assert binary.is_file(), binary
        assert os.access(binary, os.X_OK), binary
        assert (binary.parent.parent / "librtsim" / "librtsim.so").is_file()
    return binaries


def _run(binary, system, tasks, trace, duration, semantic):
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(binary.parent.parent / "librtsim")
    command = [
        str(binary),
        str(system),
        str(tasks),
        str(duration),
        "--trace",
        str(trace),
    ]
    if semantic:
        command.append("--semantic-traces")
    command.extend([
        "--run-id",
        "b3-zero-behavior",
        "--taskset-semantic-hash",
        acceptance.taskset_semantic_hash(tasks),
    ])
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(trace.read_text(encoding="utf-8"))
    return completed, data


def _without_b3_events(trace):
    normalized = dict(trace)
    normalized["events"] = [
        event for event in trace["events"]
        if event.get("event_type") != "b3_timing_observation"
    ]
    return normalized


def _normalized_log(value):
    value = re.sub(r"\x1b\[[0-9;]*m", "", value)
    value = re.sub(r"\b\d{2}:\d{2}:\d{2}\.\d{3}\b", "TIME", value)
    value = re.sub(r"\b0x[0-9a-fA-F]+\b", "POINTER", value)
    value = re.sub(r"\([^\n()]+\.(?:cpp|hpp|cc|h):\d+\)", "(SOURCE)", value)
    return value


@pytest.mark.parametrize("scheduler", SCHEDULERS)
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_semantic_trace_adds_only_b3_events(
        tmp_path, scheduler, scenario):
    baseline, candidate = _binaries()
    values = SCENARIOS[scenario]
    system = tmp_path / f"{scheduler}-{scenario}-system.yml"
    tasks = tmp_path / f"{scheduler}-{scenario}-tasks.yml"
    system.write_text(
        system_yaml(
            scheduler,
            initial=values["initial"],
            maximum=values["maximum"],
            harvest=values["harvest"],
        ),
        encoding="utf-8",
    )
    tasks.write_text(
        task_yaml(wcet=values["wcet"], deadline=values["deadline"]),
        encoding="utf-8",
    )
    base_result, base_trace = _run(
        baseline,
        system,
        tasks,
        tmp_path / "baseline.json",
        values["duration"],
        True,
    )
    head_result, head_trace = _run(
        candidate,
        system,
        tasks,
        tmp_path / "candidate.json",
        values["duration"],
        True,
    )
    assert head_result.returncode == base_result.returncode == 0
    assert _normalized_log(head_result.stdout) == _normalized_log(
        base_result.stdout
    )
    assert _normalized_log(head_result.stderr) == _normalized_log(
        base_result.stderr
    )
    assert any(
        event.get("event_type") == "b3_timing_observation"
        for event in head_trace["events"]
    )
    assert _without_b3_events(head_trace) == base_trace


@pytest.mark.parametrize("scheduler", SCHEDULERS)
def test_observation_disabled_is_byte_equivalent(tmp_path, scheduler):
    baseline, candidate = _binaries()
    system = tmp_path / f"{scheduler}-system.yml"
    tasks = tmp_path / f"{scheduler}-tasks.yml"
    system.write_text(system_yaml(scheduler), encoding="utf-8")
    tasks.write_text(task_yaml(wcet=2, deadline=20), encoding="utf-8")
    base_result, base_trace = _run(
        baseline, system, tasks, tmp_path / "baseline.json", 4, False
    )
    head_result, head_trace = _run(
        candidate, system, tasks, tmp_path / "candidate.json", 4, False
    )
    assert head_result.returncode == base_result.returncode == 0
    assert _normalized_log(head_result.stdout) == _normalized_log(
        base_result.stdout
    )
    assert _normalized_log(head_result.stderr) == _normalized_log(
        base_result.stderr
    )
    assert head_trace == base_trace
