#!/usr/bin/env python3
"""Deterministic E1.1 ST charging micro-case.

This script uses real rtsim and semantic JSON traces to audit the repaired
PFPST charging semantics. It does not use the random task generator, does not
run RTA, and does not report acceptance ratios.
"""

import argparse
import csv
import json
import subprocess
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path(
    "/root/autodl-tmp/partsim_energy_starvation_v1/"
    "e1_official_v1/e1_st_charging_microcase"
)

EPS_MJ = 1e-9
H_TASK = "H"


TASKSET_YML = """\
# Deterministic ST charging micro-case.
# H has higher RM priority and is initially energy-insufficient.
# The same taskset is reused across cases; only battery/harvest settings vary.
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


def system_yml(scheduler, initial_energy, max_energy, base_harvesting_rate):
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
  initial_energy: {initial_energy}
  max_energy: {max_energy}
  day_of_year: 187
  # Noon gives the synthetic harvesting model time_factor=1.0.
  time_of_day_ms: 43200000
  base_harvesting_rate: {base_harvesting_rate}
  harvesting_scale: 1.0
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


CASES = {
    "case_a_energy_enough_not_full": {
        "duration": 8,
        "runs": {
            "asap": {
                "scheduler": "gpfp_asap_block",
                "trace": "trace_asap_block.json",
            },
            "st": {
                "scheduler": "gpfp_st_block",
                "trace": "trace_st_block.json",
            },
        },
        # H costs about 0.558 mJ/tick. Starting at 0.5 mJ and harvesting
        # 0.03 mJ/ms makes H affordable before slack is exhausted, but the
        # 1 J battery is far from full.
        "initial_energy": 0.0005,
        "max_energy": 1.0,
        "base_harvesting_rate": 0.03,
    },
    "case_b_battery_full": {
        "duration": 5,
        "runs": {
            "st": {
                "scheduler": "gpfp_st_block",
                "trace": "trace_st_block_battery_full.json",
            },
        },
        # Battery reaches Emax before H slack is exhausted.
        "initial_energy": 0.0005,
        "max_energy": 0.0006,
        "base_harvesting_rate": 0.10,
    },
    "case_c_slack_exhausted": {
        "duration": 48,
        "runs": {
            "st": {
                "scheduler": "gpfp_st_block",
                "trace": "trace_st_block_slack_exhausted.json",
            },
        },
        # Harvesting is too weak to make H affordable or fill the battery
        # before H's slack reaches zero.
        "initial_energy": 0.0005,
        "max_energy": 1.0,
        "base_harvesting_rate": 0.0001,
    },
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic ST charging micro-cases with real rtsim and "
            "semantic traces. No random task generation or RTA is used."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for fixed inputs, traces, and summaries.",
    )
    parser.add_argument(
        "--rtsim",
        type=Path,
        default=Path("build/rtsim/rtsim"),
        help="Path to the built rtsim executable.",
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


def read_events(trace_path):
    with trace_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("events", [])


def event_time(event):
    try:
        return float(event.get("time"))
    except (TypeError, ValueError):
        return None


def scheduled_times(events, task_name):
    times = []
    for event in events:
        if event.get("event_type") == "scheduled" and event.get("task_name") == task_name:
            time = event_time(event)
            if time is not None:
                times.append(time)
    return times


def first_release_reason(events, reason):
    for event in events:
        if (
            event.get("event_type") == "st_charge_release"
            and event.get("release_reason") == reason
        ):
            return event
    return None


def charge_hold_energy_enough_event(events):
    for event in events:
        if event.get("event_type") != "st_charge_hold":
            continue
        try:
            available = float(event.get("available_energy_mJ"))
            required = float(event.get("required_energy_mJ"))
        except (TypeError, ValueError):
            continue
        if available + EPS_MJ >= required:
            return event
    return None


def any_release_reason(events, reason):
    return any(
        event.get("event_type") == "st_charge_release"
        and event.get("release_reason") == reason
        for event in events
    )


def field_consistency(events):
    bad = []
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {
            "st_charge_begin",
            "st_charge_hold",
            "st_charge_release",
        }:
            continue
        try:
            available = float(event.get("available_energy_mJ"))
            required = float(event.get("required_energy_mJ"))
            slack = float(event.get("slack_at_begin"))
        except (TypeError, ValueError):
            bad.append((event_type, "non_numeric_energy_or_slack", event))
            continue
        if available < -EPS_MJ or required < -EPS_MJ or slack < -EPS_MJ:
            bad.append((event_type, "negative_field", event))
        if event_type == "st_charge_begin" and available > required + EPS_MJ:
            bad.append((event_type, "begin_when_energy_sufficient", event))
        if event_type == "st_charge_release":
            reason = event.get("release_reason")
            if reason not in {"battery_full", "slack_exhausted"}:
                bad.append((event_type, "bad_release_reason", event))
    return bad


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

    traces = {}
    for case_name, case in CASES.items():
        for run_name, run in case["runs"].items():
            system_path = output_dir / "system_{}_{}.yml".format(case_name, run_name)
            trace_path = output_dir / run["trace"]
            log_path = output_dir / "run_{}_{}.log".format(case_name, run_name)
            system_path.write_text(
                system_yml(
                    run["scheduler"],
                    case["initial_energy"],
                    case["max_energy"],
                    case["base_harvesting_rate"],
                ),
                encoding="utf-8",
            )
            cmd = [
                str(rtsim),
                str(system_path),
                str(taskset_path),
                str(case["duration"]),
                "--trace",
                str(trace_path),
                "--semantic-traces",
            ]
            run_command(cmd, log_path)
            traces[(case_name, run_name)] = trace_path

    case_a_asap = read_events(traces[("case_a_energy_enough_not_full", "asap")])
    case_a_st = read_events(traces[("case_a_energy_enough_not_full", "st")])
    case_b_st = read_events(traces[("case_b_battery_full", "st")])
    case_c_st = read_events(traces[("case_c_slack_exhausted", "st")])

    hold_enough = charge_hold_energy_enough_event(case_a_st)
    hold_enough_time = event_time(hold_enough) if hold_enough else None
    asap_h_times = scheduled_times(case_a_asap, H_TASK)
    st_case_a_h_times = scheduled_times(case_a_st, H_TASK)

    asap_resumes_when_energy_enough = (
        hold_enough_time is not None
        and any(time >= hold_enough_time - EPS_MJ for time in asap_h_times)
    )
    st_holds_after_energy_enough = (
        hold_enough_time is not None
        and not any(time <= hold_enough_time + EPS_MJ for time in st_case_a_h_times)
        and not any_release_reason(case_a_st, "energy_sufficient")
    )
    st_release_battery_full_found = first_release_reason(case_b_st, "battery_full") is not None
    st_release_slack_exhausted_found = (
        first_release_reason(case_c_st, "slack_exhausted") is not None
    )

    field_errors = (
        field_consistency(case_a_st)
        + field_consistency(case_b_st)
        + field_consistency(case_c_st)
    )
    field_consistency_pass = len(field_errors) == 0

    row = {
        "case_name": "e1_st_charging_microcase",
        "asap_resumes_when_energy_enough": str(asap_resumes_when_energy_enough).lower(),
        "st_holds_after_energy_enough": str(st_holds_after_energy_enough).lower(),
        "st_release_battery_full_found": str(st_release_battery_full_found).lower(),
        "st_release_slack_exhausted_found": str(st_release_slack_exhausted_found).lower(),
        "field_consistency_pass": str(field_consistency_pass).lower(),
        "case_a_asap_h_scheduled_times": json.dumps(asap_h_times),
        "case_a_st_h_scheduled_times": json.dumps(st_case_a_h_times),
        "case_a_hold_energy_enough_event": json.dumps(
            hold_enough or {}, sort_keys=True
        ),
        "field_errors": json.dumps(field_errors, sort_keys=True),
        "trace_asap_block": str(traces[("case_a_energy_enough_not_full", "asap")]),
        "trace_st_block": str(traces[("case_a_energy_enough_not_full", "st")]),
        "trace_st_block_battery_full": str(traces[("case_b_battery_full", "st")]),
        "trace_st_block_slack_exhausted": str(
            traces[("case_c_slack_exhausted", "st")]
        ),
    }

    summary_csv = output_dir / "e1_st_charging_microcase_summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)

    summary_txt = output_dir / "e1_st_charging_microcase_summary.txt"
    summary_txt.write_text(
        "\n".join(
            [
                "E1.1 deterministic ST charging micro-case",
                "output_dir = {}".format(output_dir),
                "asap_resumes_when_energy_enough = {}".format(
                    asap_resumes_when_energy_enough
                ),
                "st_holds_after_energy_enough = {}".format(
                    st_holds_after_energy_enough
                ),
                "st_release_battery_full_found = {}".format(
                    st_release_battery_full_found
                ),
                "st_release_slack_exhausted_found = {}".format(
                    st_release_slack_exhausted_found
                ),
                "field_consistency_pass = {}".format(field_consistency_pass),
                "case_a_asap_h_scheduled_times = {}".format(asap_h_times),
                "case_a_st_h_scheduled_times = {}".format(st_case_a_h_times),
                "case_a_hold_energy_enough_event = {}".format(
                    json.dumps(hold_enough or {}, sort_keys=True)
                ),
                "field_errors = {}".format(json.dumps(field_errors, sort_keys=True)),
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_git_metadata(output_dir)

    print(summary_txt.read_text(encoding="utf-8"))

    required_flags = [
        asap_resumes_when_energy_enough,
        st_holds_after_energy_enough,
        st_release_battery_full_found,
        st_release_slack_exhausted_found,
        field_consistency_pass,
    ]
    if not all(required_flags):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
