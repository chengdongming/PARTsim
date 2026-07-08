#!/usr/bin/env python3
"""Deterministic E1.1 semantic trace micro-case.

This script does not use the random task generator and does not report
acceptance ratios.  It builds a fixed two-task input that makes the ASAP
BLOCK/NONBLOCK/SYNC semantic split visible in JSON traces.
"""

import argparse
import csv
import json
import subprocess
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path(
    "/root/autodl-tmp/partsim_energy_starvation_v1/"
    "e1_official_v1/e1_semantic_microcase"
)

SCHEDULERS = {
    "asap_block": ("gpfp_asap_block", "trace_asap_block.json"),
    "asap_nonblock": ("gpfp_asap_nonblock", "trace_asap_nonblock.json"),
    "asap_sync": ("gpfp_asap_sync", "trace_asap_sync.json"),
}

EPS_MJ = 1e-9


TASKSET_YML = """\
# Deterministic E1.1 semantic trace micro-case.
# H has higher RM priority and requires more than the initial energy per tick.
# L has lower RM priority and is individually affordable at time 0.
taskset:
  - name: H
    iat: 50
    runtime: 5
    deadline: 50
    startcpu: 0
    ph: 0
    qs: 100
    params: "period=50,wcet=5,arrival_offset=0,workload=bzip2"
    code:
      - fixed(5, bzip2)
  - name: L
    iat: 100
    runtime: 5
    deadline: 100
    startcpu: 0
    ph: 0
    qs: 100
    params: "period=100,wcet=5,arrival_offset=0,workload=control"
    code:
      - fixed(5, control)
"""


def system_yml(scheduler):
    return f"""\
cpu_islands:
  - name: energy_aware_cpus
    numcpus: 4
    kernel:
      scheduler: {scheduler}
      task_placement: global
    volts: [1.00]
    freqs: [8100]
    base_freq: 8100
    power_model: energy_aware_model
    speed_model: energy_aware_model

energy_management:
  # 0.5 mJ.  At 8100 MHz, H is about 0.558 mJ/tick and L is
  # about 0.0465 mJ/tick under the scheduler energy model below.
  initial_energy: 0.0005
  max_energy: 1.0
  day_of_year: 187
  time_of_day_ms: 0
  base_harvesting_rate: 0.0
  harvesting_scale: 0.0
  use_real_solar_data: false
  solar_data_file: ""
  pv_efficiency: 0.18
  pv_area_m2: 1.0
  periodic_collection_interval_ms: 1
  enable_energy_recovery: false
  max_recovery_wait_time_ms: 0
  scheduler_energy_model:
    base_power: 0.5
    workload_coefficients:
      bzip2: 1.2
      control: 0.1
      idle: 0.1
    frequency_power_ratios:
      8100: 0.93

power_models:
  - name: energy_aware_model
    type: balsini_pannocchi
    params:
      - workload: bzip2
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 1.2
      - workload: control
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1
      - workload: idle
        power_params: [0.001, 1.0, 1.0, 1.0e-10]
        speed_params: [1, 0, 0, 0]
        energy_coefficient: 0.1
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic E1.1 ASAP semantic trace micro-case. "
            "No random task generation or acceptance-ratio analysis is used."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for fixed inputs, JSON traces, and summary files.",
    )
    parser.add_argument(
        "--rtsim",
        type=Path,
        default=Path("build/rtsim/rtsim"),
        help="Path to the built rtsim executable.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=5,
        help="Simulation duration in rtsim time units.",
    )
    return parser.parse_args()


def run_command(cmd, log_path):
    completed = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
    )
    log_path.write_text(
        "COMMAND: {}\n\nSTDOUT:\n{}\n\nSTDERR:\n{}\n".format(
            " ".join(str(part) for part in cmd),
            completed.stdout,
            completed.stderr,
        ),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "rtsim exited with code {} for {}; see {}".format(
                completed.returncode, cmd[0], log_path
            )
        )


def events_from(trace_path):
    with trace_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("events", [])


def first_event(events, event_type):
    for event in events:
        if event.get("event_type") == event_type:
            return event
    return None


def event_time_is_zero(event):
    try:
        return float(event.get("time")) == 0.0
    except (TypeError, ValueError):
        return False


def validate_block(event):
    if not event:
        return False, "missing energy_block"
    blocked_energy = float(event.get("blocked_task_unit_energy_mJ", -1.0))
    available = float(event.get("available_energy_mJ", -1.0))
    checks = [
        event_time_is_zero(event),
        event.get("blocked_task") == "H",
        event.get("reason") == "highest_priority_energy_insufficient",
        blocked_energy > available - EPS_MJ,
    ]
    return all(checks), json.dumps(event, sort_keys=True)


def validate_nonblock(event):
    if not event:
        return False, "missing nonblock_bypass"
    blocked_energy = float(event.get("blocked_task_unit_energy_mJ", -1.0))
    bypassed_energy = float(event.get("bypassed_task_unit_energy_mJ", -1.0))
    available = float(event.get("available_energy_mJ", -1.0))
    checks = [
        event_time_is_zero(event),
        event.get("blocked_higher_priority_task") == "H",
        event.get("bypassed_task") == "L",
        event.get("reason") == "lower_priority_bypass_due_to_energy",
        blocked_energy > available - EPS_MJ,
        bypassed_energy <= available + EPS_MJ,
    ]
    return all(checks), json.dumps(event, sort_keys=True)


def validate_sync(event):
    if not event:
        return False, "missing sync_batch_block"
    required = float(event.get("batch_required_energy_mJ", -1.0))
    available = float(event.get("available_energy_mJ", -1.0))
    tasks = event.get("batch_tasks") or []
    task_names = {task.get("task_name") for task in tasks}
    expected_feasible = any(
        float(task.get("task_unit_energy_mJ", -1.0)) <= available + EPS_MJ
        for task in tasks
    )
    checks = [
        event_time_is_zero(event),
        {"H", "L"}.issubset(task_names),
        event.get("reason") == "sync_batch_energy_insufficient",
        required > available - EPS_MJ,
        bool(event.get("feasible_subset_exists")) is True,
        expected_feasible is True,
    ]
    return all(checks), json.dumps(event, sort_keys=True)


def write_git_metadata(output_dir):
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )
    status = subprocess.run(
        ["git", "status", "--short"],
        check=False,
        text=True,
        capture_output=True,
    )
    (output_dir / "git_commit.txt").write_text(
        commit.stdout if commit.returncode == 0 else commit.stderr,
        encoding="utf-8",
    )
    (output_dir / "git_status.txt").write_text(
        status.stdout if status.returncode == 0 else status.stderr,
        encoding="utf-8",
    )


def main():
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rtsim = args.rtsim.resolve()
    if not rtsim.exists():
        raise FileNotFoundError("rtsim executable not found: {}".format(rtsim))

    taskset_path = output_dir / "input_taskset.yml"
    taskset_path.write_text(TASKSET_YML, encoding="utf-8")

    trace_paths = {}
    for case_name, (scheduler, trace_name) in SCHEDULERS.items():
        system_path = output_dir / "system_{}.yml".format(case_name)
        trace_path = output_dir / trace_name
        log_path = output_dir / "run_{}.log".format(case_name)
        system_path.write_text(system_yml(scheduler), encoding="utf-8")
        cmd = [
            str(rtsim),
            str(system_path),
            str(taskset_path),
            str(args.duration),
            "--trace",
            str(trace_path),
            "--semantic-traces",
        ]
        run_command(cmd, log_path)
        trace_paths[case_name] = trace_path

    block_events = events_from(trace_paths["asap_block"])
    nonblock_events = events_from(trace_paths["asap_nonblock"])
    sync_events = events_from(trace_paths["asap_sync"])

    energy_block = first_event(block_events, "energy_block")
    nonblock_bypass = first_event(nonblock_events, "nonblock_bypass")
    sync_batch_block = first_event(sync_events, "sync_batch_block")

    block_ok, block_detail = validate_block(energy_block)
    nonblock_ok, nonblock_detail = validate_nonblock(nonblock_bypass)
    sync_ok, sync_detail = validate_sync(sync_batch_block)
    field_consistency_pass = block_ok and nonblock_ok and sync_ok

    row = {
        "case_name": "e1_semantic_microcase",
        "energy_block_found": str(energy_block is not None).lower(),
        "nonblock_bypass_found": str(nonblock_bypass is not None).lower(),
        "sync_batch_block_found": str(sync_batch_block is not None).lower(),
        "field_consistency_pass": str(field_consistency_pass).lower(),
        "asap_block_trace": str(trace_paths["asap_block"]),
        "asap_nonblock_trace": str(trace_paths["asap_nonblock"]),
        "asap_sync_trace": str(trace_paths["asap_sync"]),
        "energy_block_detail": block_detail,
        "nonblock_bypass_detail": nonblock_detail,
        "sync_batch_block_detail": sync_detail,
    }

    summary_csv = output_dir / "e1_semantic_microcase_summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)

    summary_txt = output_dir / "e1_semantic_microcase_summary.txt"
    summary_txt.write_text(
        "\n".join(
            [
                "E1.1 deterministic semantic micro-case",
                "output_dir = {}".format(output_dir),
                "energy_block_found = {}".format(energy_block is not None),
                "nonblock_bypass_found = {}".format(nonblock_bypass is not None),
                "sync_batch_block_found = {}".format(sync_batch_block is not None),
                "field_consistency_pass = {}".format(field_consistency_pass),
                "energy_block_detail = {}".format(block_detail),
                "nonblock_bypass_detail = {}".format(nonblock_detail),
                "sync_batch_block_detail = {}".format(sync_detail),
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_git_metadata(output_dir)

    print(summary_txt.read_text(encoding="utf-8"))
    if not field_consistency_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
