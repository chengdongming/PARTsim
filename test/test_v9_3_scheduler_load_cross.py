import csv
from concurrent.futures.process import BrokenProcessPool
import hashlib
import json
from concurrent.futures import Future
from fractions import Fraction
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from experiments.v9_3 import perf_g
from experiments.v9_3 import scheduler_load_cross as experiment
from experiments.v9_3 import taskset_store
from experiments.v9_3.simulation_engine import should_retain_failure_trace
from experiments.v9_3.simulation_engine import (
    _render_taskset_yaml, derive_fixed_priority_ranks,
    normalize_scheduler_priority_policy, SimulationConfigurationError,
)
from experiments.v9_3.simulation_result import (
    SimulationStatus, SimulationTraceError, _strict_json, _trace_parse_slot,
    parse_simulation_trace,
)
from experiments.v9_3 import implicit_trace_stream
from experiments.v9_3.implicit_wholepass_fast import (
    FAST_MODE, FAST_SCHEMA, FastWholePassError, validate_fast_document,
)
from experiments.v9_3.simulation_engine import simulation_result_to_dict
from experiments.v9_3.performance_outcome import evaluate_outcome
from scripts.analyze_scheduler_load_cross import (
    analyze, dmr_cluster_bootstrap_ci, summarize_dmr, wilson_ci,
    _parse_dmr_ymin, _plot_style, decimal_axis_labels, plot_composite_dmr,
    plot_composite_scan, plot_dmr_scan, plot_scan, _v5_plot_style,
    plot_v5_composite_dmr, plot_v5_composite_scan,
)
import scripts.analyze_scheduler_load_cross as analyzer_module
import scripts.run_scheduler_load_cross as scheduler_runner
import scripts.run_v6_implicit_wholepass_fast as fast_overlay_runner
import scripts.run_v6_implicit_wholepass_fast as fast_overlay_runner


def _fast_result_fixture(*, passed=True):
    task_ids = [f"v93_task_{index}" for index in range(10)]
    return {
        "schema": FAST_SCHEMA,
        "fast_mode": FAST_MODE,
        "run_id": "v93-request123456-h60000",
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": "gpfp_asap_block",
        "processors": 4,
        "task_count": 10,
        "task_ids": task_ids,
        "deadline_mode": "implicit",
        "horizon": 60000,
        "simulation_generation": 1,
        "simulation_completed": passed,
        "completion_reason": "reached_horizon" if passed else "first_hardrt_deadline_miss",
        "taskset_pass": passed,
        "released_jobs": 100,
        "adjudicable_jobs": 90,
        "completed_adjudicable_jobs": 90 if passed else 50,
        "first_deadline_miss": None if passed else {
            "task_id": "v93_task_0", "job_id": "v93_task_0@0",
            "release": 0, "absolute_deadline": 100,
            "miss_time": 100,
            "evidence": "deadline_event_for_active_job",
        },
    }


def _available_outcome(adjudicable_jobs=1, deadline_miss_jobs=0, wholepass=True):
    return {
        "outcome_status": "AVAILABLE",
        "adjudicable_jobs": adjudicable_jobs,
        "deadline_miss_jobs": deadline_miss_jobs,
        "wholepass": wholepass,
        "taskset_pass": wholepass,
    }


def _trace_parse_gate_worker(
    slot_dir, concurrency, active, maximum, counter_lock, fail_inside,
):
    os.environ["PARTSIM_TRACE_PARSE_CONCURRENCY"] = str(concurrency)
    os.environ["PARTSIM_TRACE_PARSE_SLOT_DIR"] = str(slot_dir)
    try:
        with _trace_parse_slot():
            with counter_lock:
                active.value += 1
                maximum.value = max(maximum.value, active.value)
            try:
                if fail_inside:
                    raise RuntimeError("intentional parse failure")
                time.sleep(0.08)
            finally:
                with counter_lock:
                    active.value -= 1
    except RuntimeError as exc:
        if str(exc) != "intentional parse failure":
            raise


def _real_process_group_worker(
    child_pid_path, child_ready_path, ignore_sigterm=False, exit_leader=False,
    leader_release_path=None,
):
    child_code = "import time\n"
    if ignore_sigterm:
        child_code += "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    child_code += (
        "from pathlib import Path\n"
        f"Path({str(child_ready_path)!r}).write_text('ready')\n"
    )
    child_code += "time.sleep(300)\n"
    child = subprocess.Popen([sys.executable, "-c", child_code])
    Path(child_pid_path).write_text(str(child.pid), encoding="utf-8")
    if exit_leader:
        while not Path(leader_release_path).exists():
            time.sleep(0.01)
        os._exit(17)
    time.sleep(300)


def _broken_pool_probe_blocking_worker(child_pid_path, ready_path):
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(300)",
    ])
    Path(child_pid_path).write_text(str(child.pid), encoding="utf-8")
    Path(ready_path).write_text("ready", encoding="utf-8")
    time.sleep(300)


def _broken_pool_probe_crashing_worker(release_path):
    while not Path(release_path).exists():
        time.sleep(0.01)
    os._exit(17)


def _broken_pool_probe_hold_shutdown_lock(shutdown_lock, ready_path):
    with shutdown_lock:
        Path(ready_path).write_text("held", encoding="utf-8")
        time.sleep(300)


def _broken_pool_probe_queued_worker(payload):
    time.sleep(30)
    return len(payload)


def _broken_pool_probe(state_path):
    context = multiprocessing.get_context("fork")
    parse_semaphore = context.Semaphore(1)
    executor = scheduler_runner.ProcessPoolExecutor(
        max_workers=2,
        mp_context=context,
        initializer=scheduler_runner._initialize_simulation_worker,
        initargs=(parse_semaphore,),
    )
    child_pid_path = Path(state_path).with_name("probe-child.pid")
    child_ready_path = Path(state_path).with_name("probe-child.ready")
    release_path = Path(state_path).with_name("probe-release")
    shutdown_lock_ready_path = Path(state_path).with_name("probe-shutdown-lock.ready")
    futures = []
    try:
        futures.append(executor.submit(
            _broken_pool_probe_blocking_worker,
            str(child_pid_path), str(child_ready_path),
        ))
        crash_future = executor.submit(
            _broken_pool_probe_crashing_worker, str(release_path),
        )
        worker_process_groups = scheduler_runner._capture_worker_process_groups(
            executor
        )
        worker_processes = tuple(executor._processes.values())
        Path(state_path).write_text(json.dumps({
            "worker_pids": [process.pid for process in worker_processes],
            "worker_process_groups": sorted(worker_process_groups),
        }), encoding="utf-8")
        deadline = time.monotonic() + 2.0
        while not child_ready_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_ready_path.is_file()
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        futures.extend(
            executor.submit(_broken_pool_probe_queued_worker, b"x" * 1_000_000)
            for _ in range(64)
        )
        release_path.write_text("release", encoding="utf-8")
        try:
            crash_future.result(timeout=3.0)
        except BrokenProcessPool:
            pass
        else:
            raise AssertionError("crashing worker did not raise BrokenProcessPool")
        assert _wait_for_test_exitcode(worker_processes, 17)
        worker_diagnostics = scheduler_runner._worker_diagnostics(executor)
        lock_thread = threading.Thread(
            target=_broken_pool_probe_hold_shutdown_lock,
            args=(executor._shutdown_lock, str(shutdown_lock_ready_path)),
            daemon=True,
        )
        lock_thread.start()
        deadline = time.monotonic() + 2.0
        while not shutdown_lock_ready_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert shutdown_lock_ready_path.is_file()
        cleanup_started = time.monotonic()
        cleanup_complete = scheduler_runner._abort_executor(
            executor, futures, worker_process_groups,
        )
        cleanup_elapsed = time.monotonic() - cleanup_started
        Path(state_path).write_text(json.dumps({
            "stage": "abort_completed",
            "cleanup_elapsed": cleanup_elapsed,
            "cleanup_complete": cleanup_complete,
            "child_pid": child_pid,
            "worker_pids": [process.pid for process in worker_processes],
            "worker_process_groups": sorted(worker_process_groups),
            "worker_diagnostics": worker_diagnostics,
        }), encoding="utf-8")
        os._exit(2)
    finally:
        # The parent test process owns the hard timeout and performs final
        # process-group cleanup if this probe is stuck in broken-pool teardown.
        pass


def _cleanup_broken_pool_probe(state_path):
    if not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for group_id in state.get("worker_process_groups", []):
        if scheduler_runner._validate_worker_process_groups({group_id}):
            try:
                os.killpg(group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
    child_pid = state.get("child_pid")
    if not isinstance(child_pid, int):
        child_pid_path = state_path.with_name("probe-child.pid")
        try:
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            child_pid = None
    if isinstance(child_pid, int) and _test_pid_is_alive(child_pid):
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _wait_for_test_pid(path, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                return int(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        time.sleep(0.01)
    raise AssertionError(f"child PID was not published: {path}")


def _wait_for_test_file(path, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.01)
    raise AssertionError(f"readiness file was not published: {path}")


def _test_pid_is_alive(pid):
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        fields = proc_stat.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_test_pid_exit(pid, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _test_pid_is_alive(pid):
            return True
        time.sleep(0.01)
    return not _test_pid_is_alive(pid)


def _wait_for_test_process_exit(process, timeout=2.0):
    deadline = time.monotonic() + timeout
    while process.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    return not process.is_alive()


def _wait_for_test_exitcode(processes, expected_exitcode, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.exitcode == expected_exitcode for process in processes):
            return True
        time.sleep(0.01)
    return any(process.exitcode == expected_exitcode for process in processes)


def _harvest_model_fields():
    return dict(experiment.HARVEST_MODEL_IDENTITY)


def _write_schema2_stream_trace(path, scheduler="gpfp_asap_block"):
    document = {
        "trace_schema_version": 2,
        "run_id": "stream-test",
        "run_count": 1,
        "target_run_generation": 1,
        "run_generation": 1,
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": scheduler,
        "scheduler_display_name": "ASAP-Block",
        "scheduler_implementation": "ASAPBlockScheduler",
        "expected_simulation_horizon_ms": 2,
        "observed_simulation_end_ms": 2,
        "simulation_completed": True,
        "simulation_completion_reason": "reached_horizon",
        "metadata_note": {"events": "not the top-level array"},
        "events": [
            {
                "time": 0, "event_type": "arrival", "task_name": "v93_task_0",
                "arrival_time": 0, "current_energy_mJ": 1,
                "note": 'escaped "events" text',
            },
            {
                "time": 0, "event_type": "scheduled", "task_name": "v93_task_0",
                "arrival_time": 0, "task_unit_energy_mJ": 1,
            },
            {
                "time": 1, "event_type": "end_instance", "task_name": "v93_task_0",
                "arrival_time": 0,
            },
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def _parse_schema2_stream_trace(path, scheduler="gpfp_asap_block", stream=False):
    return parse_simulation_trace(
        path, [{"task_id": "0", "priority_rank": 0, "C": 1, "D": 5, "T": 5}],
        expected_taskset_hash="a" * 64, horizon=2, warmup=0,
        minimum_jobs_per_task=0, release_e0=Fraction(0),
        expected_scheduler=scheduler, stream_events=stream,
    )


def _v6_fixture_config(priority_policy):
    profile = experiment.normalize_scan_profile(
        uc_scan_values="1/10", ue_scan_values="1/5",
        uc_figure_fixed_ues="1/5", uc_figure_labels="selected",
        ue_figure_fixed_ucs="1/10", ue_figure_labels="selected",
    )
    contract = experiment.build_scan_contract(profile)
    config = {
        "experiment": experiment.V6_EXPERIMENT,
        "domain": experiment.V6_DOMAIN,
        "campaign_contract": experiment.V6_CAMPAIGN_CONTRACT,
        "seed": 123, "workers": 1,
        "deadline_modes": list(experiment.deadline_modes_for_priority_policy(priority_policy)),
        "priority_policy": priority_policy,
        "expected_request_count": 1 * 1 * 9 * len(experiment.deadline_modes_for_priority_policy(priority_policy)),
        "expected_taskset_count": 1 * 1 * len(experiment.deadline_modes_for_priority_policy(priority_policy)),
        "implicit_priority_equivalence": experiment.V6_IMPLICIT_PRIORITY_EQUIVALENCE,
        "implicit_canonical_priority_policy": experiment.V6_IMPLICIT_CANONICAL_PRIORITY_POLICY,
        "implicit_reuse_policy": experiment.V6_IMPLICIT_REUSE_POLICY,
        "shared_implicit_contract_version": experiment.V6_SHARED_IMPLICIT_CONTRACT_VERSION,
        "samples_per_cell": 1, "cells": [["1/10", "1/5"]],
        "schedulers": list(experiment.ALL_SCHEDULERS), "processors": 1, "tasks": 1,
        "period_min": 10, "period_max": 20,
        "min_task_util": "1/100", "max_task_util": "4/5",
        "util_tolerance_total": "1/100", "rho": "11/2", "latency": "2/5",
        "kappa": "10", "initial_energy_rule": "battery_capacity/2",
        "normalization_horizon_ms": 60000, "simulation_horizon_ms": 20,
        "use_real_solar_data": False, **_harvest_model_fields(),
        "release_semantics": "synchronous arrival_offset=0",
        "energy_control": "SERVICE_ONLY_SCALING",
        "energy_unit": "J/tick exact canonical P",
        "simulator": "build/rtsim/rtsim",
        "canonical_taskset_source": "PERF-G TasksetStore",
        "keep_traces": False, "parse_concurrency": 1,
        "figure_slices": experiment.build_v4_figure_slices(profile),
        "scan_contract": contract,
    }
    config["run_identity"] = experiment.run_identity(config)
    return config


def _write_v6_fixture(root, priority_policy):
    config = _v6_fixture_config(priority_policy)
    root.mkdir()
    (root / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    modes = tuple(config["deadline_modes"])
    tasksets = []
    requests = []
    results = []
    for mode in modes:
        taskset_id = f"{priority_policy.lower()}-{mode}-taskset"
        taskset_hash = f"hash-{priority_policy.lower()}-{mode}"
        deadline = 10 if mode == "implicit" else 5
        payload = [{
            "task_id": "0", "priority_rank": 0, "C": 1,
            "D": deadline, "T": 10, "P": "1", "workload": "hash",
            "arrival_offset": 0,
        }]
        tasksets.append({
            "taskset_id": taskset_id, "taskset_hash": taskset_hash,
            "deadline_mode": mode, "canonical_task_power": True,
            "task_input_json": json.dumps(payload),
        })
        for scheduler in experiment.ALL_SCHEDULERS:
            request = {
                "request_id": f"{priority_policy.lower()}-{mode}-{scheduler}",
                "experiment": experiment.V6_EXPERIMENT, "domain": experiment.V6_DOMAIN,
                "taskset_id": taskset_id, "taskset_hash": taskset_hash,
                "target_uc": "1/10", "actual_uc": "1/10", "target_ue": "1/5",
                "eta": "5", "generation_index": 0, "seed": 123,
                "scheduler": scheduler, "scheduler_cli": perf_g.SCHEDULER_CLI[scheduler],
                "horizon_ms": 20, "priority_policy": priority_policy,
                "deadline_mode": mode, **_harvest_model_fields(),
            }
            energy = {
                "target_ue": "1/5", "eta": "5", "P_dem_j_per_tick": "1",
                "target_supply_mean_j_per_tick": "5",
                "raw_reference_mean_j_per_tick": "5", "solar_scale": "1",
                "runtime_configured_average_supply_j_per_tick": "5",
                "actual_ue": "1/5", "actual_ue_abs_error": "0",
                "actual_ue_minus_target_ue": "0", "actual_ue_rel_error": "0",
                "harvest_trace_id": "raw-trace-fixture", **_harvest_model_fields(),
            }
            result = {
                **request, "simulation_status": "SIM_PASS_OBSERVED",
                "technical_error": None, "deadline_miss": False,
                "wholepass": True, "taskset_pass": True,
                "outcome": _available_outcome(), "energy": energy,
            }
            requests.append(request)
            results.append(result)
    for name, rows in (("tasksets.jsonl", tasksets), ("requests.jsonl", requests), ("results.jsonl", results)):
        (root / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
        )


def _write_legacy_analyzer_fixture(root, experiment_name):
    _write_v6_fixture(root, "RM")
    domains = {
        experiment.V3_EXPERIMENT: experiment.V3_DOMAIN,
        experiment.V4_EXPERIMENT: experiment.V4_DOMAIN,
        experiment.V5_EXPERIMENT: experiment.V5_DOMAIN,
    }
    config = json.loads((root / "run_config.json").read_text())
    config.update({"experiment": experiment_name, "domain": domains[experiment_name]})
    if experiment_name != experiment.V5_EXPERIMENT:
        config.update({
            "deadline_modes": ["constrained"],
            "expected_request_count": 9,
            "expected_taskset_count": 1,
        })
    (root / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name in ("requests.jsonl", "results.jsonl"):
        rows = [
            json.loads(line) for line in (root / name).read_text().splitlines()
            if line.strip()
        ]
        if experiment_name != experiment.V5_EXPERIMENT:
            rows = [row for row in rows if row["deadline_mode"] == "constrained"]
        for row in rows:
            row.update({"experiment": experiment_name, "domain": domains[experiment_name]})
        (root / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
        )
    if experiment_name != experiment.V5_EXPERIMENT:
        rows = [
            json.loads(line) for line in (root / "tasksets.jsonl").read_text().splitlines()
            if line.strip()
        ]
        (root / "tasksets.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows if row["deadline_mode"] == "constrained"),
            encoding="utf-8",
        )
    tasksets = [
        json.loads(line) for line in (root / "tasksets.jsonl").read_text().splitlines()
        if line.strip()
    ]
    for row in tasksets:
        row.update({"target_uc": "1/10", "actual_uc": "1/10"})
    (root / "tasksets.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tasksets), encoding="utf-8",
    )


def test_exact_ue_eta_mapping_and_deduplicated_cells():
    assert experiment.eta_for_ue(Fraction(2, 5)) == Fraction(5, 2)
    assert experiment.eta_for_ue(Fraction(3, 10)) == Fraction(10, 3)
    assert experiment.parse_cells("1/2:2/5,0.5:0.4,1/2:1/5") == (
        (Fraction(1, 2), Fraction(2, 5)), (Fraction(1, 2), Fraction(1, 5)),
    )
    assert len(experiment.DEFAULT_CELLS) == 51
    assert len(set(experiment.DEFAULT_CELLS)) == 51


def test_v5_identity_and_deadline_modes_are_fixed():
    assert experiment.DOMAIN == "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v6"
    assert experiment.V5_EXPERIMENT == "scheduler-load-cross-v5"
    assert experiment.V6_DOMAIN == "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v6"
    assert experiment.V6_EXPERIMENT == "scheduler-load-cross-v6"
    assert experiment.DEADLINE_MODES == ("constrained", "implicit")
    assert experiment.deadline_modes_for_priority_policy("RM") == (
        "constrained", "implicit",
    )
    assert experiment.deadline_modes_for_priority_policy("DM") == ("constrained",)
    assert len(experiment.FORMAL_CELLS) == 51
    assert len(set(experiment.FORMAL_CELLS)) == 51
    with pytest.raises(ValueError, match="deadline_mode"):
        experiment.normalize_deadline_mode("unknown")


def test_v5_request_identity_and_count_include_deadline_mode():
    class Taskset:
        processors = 4
        actual_utilization = Fraction(1)
        taskset_index = 0
        seed = 7

        def __init__(self, uc, mode):
            self.target_utilization = uc * self.processors
            self.deadline_mode = mode
            self.taskset_id = f"{mode}-{uc}"
            self.semantic_hash = f"hash-{mode}-{uc}"

    tasksets = [
        Taskset(uc, mode)
        for mode in experiment.DEADLINE_MODES
        for uc in {Fraction(uc) for uc, _ue in experiment.FORMAL_CELLS}
    ]
    rows = [
        row
        for mode in experiment.DEADLINE_MODES
        for row in experiment.request_rows(
            [taskset for taskset in tasksets if taskset.deadline_mode == mode],
            experiment.FORMAL_CELLS, experiment.ALL_SCHEDULERS, 60000,
            experiment_name=experiment.V5_EXPERIMENT, deadline_mode=mode,
        )
    ]
    assert len(rows) == 51 * 9 * 2
    assert {row["deadline_mode"] for row in rows} == set(experiment.DEADLINE_MODES)
    assert len({row["request_id"] for row in rows}) == len(rows)


def test_v6_mode_plan_and_request_count_contract():
    assert experiment.DOMAIN == experiment.V6_DOMAIN
    assert experiment.V6_EXPERIMENT == "scheduler-load-cross-v6"
    assert experiment.deadline_modes_for_priority_policy("RM") == (
        "constrained", "implicit",
    )
    assert experiment.deadline_modes_for_priority_policy("DM") == ("constrained",)
    assert len(experiment.FORMAL_CELLS) == 51
    assert len(experiment.ALL_SCHEDULERS) == 9
    assert 51 * 9 * 2 == 918
    assert 51 * 9 == 459
    assert 51 * 9 * 3 == 1377
    assert [1377 * n for n in (20, 100, 120, 200)] == [27540, 137700, 165240, 275400]
    assert [30 * n for n in (100, 120)] == [3000, 3600]

    class Taskset:
        processors = 1
        actual_utilization = Fraction(1, 10)
        taskset_index = 0
        seed = 123
        semantic_hash = "v6-hash"

        def __init__(self, uc, mode):
            self.target_utilization = uc
            self.taskset_id = f"{mode}-{uc}"
            self.deadline_mode = mode

    ucs = tuple(dict.fromkeys(uc for uc, _ue in experiment.FORMAL_CELLS))
    rm_rows = sum(
        len(experiment.request_rows(
            [Taskset(uc, mode) for uc in ucs], experiment.FORMAL_CELLS,
            experiment.ALL_SCHEDULERS, 60000, priority_policy="RM",
            experiment_name=experiment.V6_EXPERIMENT, deadline_mode=mode,
        ))
        for mode in ("constrained", "implicit")
    )
    dm_rows = len(experiment.request_rows(
        [Taskset(uc, "constrained") for uc in ucs], experiment.FORMAL_CELLS,
        experiment.ALL_SCHEDULERS, 60000, priority_policy="DM",
        experiment_name=experiment.V6_EXPERIMENT, deadline_mode="constrained",
    ))
    assert rm_rows == 918
    assert dm_rows == 459


def test_v6_request_identity_distinguishes_policy_and_mode():
    class Taskset:
        processors = 1
        actual_utilization = Fraction(1, 10)
        target_utilization = Fraction(1, 10)
        taskset_index = 0
        seed = 123
        taskset_id = "v6-taskset"
        semantic_hash = "v6-hash"
        deadline_mode = "implicit"

    rm = experiment.request_rows(
        [Taskset()], ((Fraction(1, 10), Fraction(1, 5)),),
        ("ASAP-BLOCK",), 60000, priority_policy="RM",
        experiment_name=experiment.V6_EXPERIMENT, deadline_mode="implicit",
    )[0]
    dm = experiment.request_rows(
        [Taskset()], ((Fraction(1, 10), Fraction(1, 5)),),
        ("ASAP-BLOCK",), 60000, priority_policy="DM",
        experiment_name=experiment.V6_EXPERIMENT, deadline_mode="implicit",
    )[0]
    assert rm["domain"] == experiment.V6_DOMAIN
    assert rm["deadline_mode"] == "implicit"
    assert rm["priority_policy"] == "RM"
    assert dm["priority_policy"] == "DM"
    assert rm["request_id"] != dm["request_id"]


def test_v5_scheduler_style_combinations_are_unique():
    styles = [_v5_plot_style(scheduler) for scheduler in experiment.ALL_SCHEDULERS]
    assert len({(item["color"], item["marker"], item["linestyle"]) for item in styles}) == 9


def test_v5_composite_plots_have_six_axes_and_nine_scheduler_curves(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt

    profiles = []
    for index, label in enumerate(("low", "medium", "high")):
        slice_config = {
            "x_key": "target_uc", "fixed_key": "target_ue",
            "fixed_value": str(Fraction(index + 3, 10)),
            "label": label,
            "x_values": list(experiment.normalize_scan_profile()["uc_scan_values"]),
        }
        for mode in experiment.DEADLINE_MODES:
            rows = []
            for scheduler in experiment.ALL_SCHEDULERS:
                for x_value in slice_config["x_values"]:
                    rows.append({
                        "scheduler": scheduler, "target_uc": x_value,
                        "wholepass_ratio": 0.8, "ci95_low": 0.8,
                        "ci95_high": 0.8, "dmr": 0.8,
                        "dmr_ci95_low": 0.8, "dmr_ci95_high": 0.8,
                    })
            profiles.append((slice_config, mode, rows))

    figures = []
    monkeypatch.setattr(plt, "close", lambda figure: figures.append(figure))
    ticks = experiment.normalize_scan_profile()["axis_ticks"]
    plot_v5_composite_scan(
        profiles, tmp_path, "v5.png", "target_uc", list(experiment.ALL_SCHEDULERS),
        "U_C", "v5", axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert len(figures[-1].axes) == 6
    assert all(
        len([line for line in axis.lines if line.get_label() in experiment.ALL_SCHEDULERS]) == 9
        for axis in figures[-1].axes
    )
    assert all(axis.get_xlim() == (0.0, 1.0) for axis in figures[-1].axes)

    plot_v5_composite_dmr(
        profiles, tmp_path, "v5-dmr.png", "target_uc", list(experiment.ALL_SCHEDULERS),
        "U_C", "v5 dmr", 0.0, axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert len(figures[-1].axes) == 6
    assert all(
        len([line for line in axis.lines if line.get_label() in experiment.ALL_SCHEDULERS]) == 9
        for axis in figures[-1].axes
    )


def test_v5_composite_slice_labels_follow_rows_for_both_metrics_and_scans(
    tmp_path, monkeypatch,
):
    import matplotlib.pyplot as plt

    figures = []
    monkeypatch.setattr(plt, "close", lambda figure: figures.append(figure))
    scheduler = "ASAP-BLOCK"
    scan_values = [str(value) for value in experiment.normalize_scan_profile()["uc_scan_values"]]

    def slice_rows(fixed_key, x_key):
        result = []
        for label, fixed_value in zip(("low", "medium", "high"), ("3/10", "3/5", "9/10")):
            config = {
                "x_key": x_key, "fixed_key": fixed_key,
                "fixed_value": fixed_value, "label": label,
            }
            rows = []
            for mode in experiment.DEADLINE_MODES:
                for x_value in scan_values:
                    rows.append({
                        "scheduler": scheduler, x_key: x_value,
                        "wholepass_ratio": 0.5, "ci95_low": 0.4,
                        "ci95_high": 0.6, "dmr": 0.5,
                        "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
                    })
                result.append((config, mode, rows[-len(scan_values):]))
        return result

    def labels_for(figure):
        return [figure.axes[row * 2].get_ylabel() for row in range(3)]

    plot_v5_composite_scan(
        slice_rows("target_ue", "target_uc"), tmp_path, "uc.png", "target_uc",
        [scheduler], "U_C", "U_C", axis_min="0", axis_max="1",
        axis_ticks=experiment.normalize_scan_profile()["axis_ticks"],
    )
    assert labels_for(figures[-1]) == [
        "low: U_E=0.3\nWhole-taskset pass ratio",
        "medium: U_E=0.6\nWhole-taskset pass ratio",
        "high: U_E=0.9\nWhole-taskset pass ratio",
    ]

    plot_v5_composite_dmr(
        slice_rows("target_ue", "target_uc"), tmp_path, "uc-dmr.png", "target_uc",
        [scheduler], "U_C", "U_C DMR", 0.0, axis_min="0", axis_max="1",
        axis_ticks=experiment.normalize_scan_profile()["axis_ticks"],
    )
    assert labels_for(figures[-1]) == [
        "low: U_E=0.3\nDMR", "medium: U_E=0.6\nDMR", "high: U_E=0.9\nDMR",
    ]

    plot_v5_composite_scan(
        slice_rows("target_uc", "target_ue"), tmp_path, "ue.png", "target_ue",
        [scheduler], "U_E", "U_E", axis_min="0", axis_max="1",
        axis_ticks=experiment.normalize_scan_profile()["axis_ticks"],
    )
    assert labels_for(figures[-1]) == [
        "low: U_C=0.3\nWhole-taskset pass ratio",
        "medium: U_C=0.6\nWhole-taskset pass ratio",
        "high: U_C=0.9\nWhole-taskset pass ratio",
    ]

    plot_v5_composite_dmr(
        slice_rows("target_uc", "target_ue"), tmp_path, "ue-dmr.png", "target_ue",
        [scheduler], "U_E", "U_E DMR", 0.0, axis_min="0", axis_max="1",
        axis_ticks=experiment.normalize_scan_profile()["axis_ticks"],
    )
    assert labels_for(figures[-1]) == [
        "low: U_C=0.3\nDMR", "medium: U_C=0.6\nDMR", "high: U_C=0.9\nDMR",
    ]


def test_decimal_axis_labels_preserve_exact_scan_ticks():
    profile = experiment.normalize_scan_profile()
    assert profile["axis_ticks"] == [
        "0", "1/10", "1/5", "3/10", "2/5", "1/2", "3/5",
        "7/10", "4/5", "9/10", "1",
    ]
    assert decimal_axis_labels(profile["axis_ticks"]) == [
        "0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6",
        "0.7", "0.8", "0.9", "1",
    ]
    custom = experiment.normalize_scan_profile(
        axis_display_min="0", axis_display_max="1", axis_tick_step="1/5",
    )
    assert custom["axis_ticks"] == ["0", "1/5", "2/5", "3/5", "4/5", "1"]
    assert decimal_axis_labels(custom["axis_ticks"]) == [
        "0", "0.2", "0.4", "0.6", "0.8", "1",
    ]


def test_all_scan_plot_paths_use_decimal_axis_labels(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    monkeypatch.setattr(plt, "close", lambda _figure: None)

    expected_labels = [
        "0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6",
        "0.7", "0.8", "0.9", "1",
    ]
    expected_ticks = [index / 10 for index in range(11)]

    class CaptureAxis:
        def __init__(self):
            self.xticks = None
            self.xticklabels = None
            self.errorbar_x = []

        def errorbar(self, x, *_args, **_kwargs):
            self.errorbar_x.extend(x)
            return SimpleNamespace(lines=[SimpleNamespace()])

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, _label):
            pass

        def set_title(self, _title):
            pass

        def set_xlim(self, *_args):
            pass

        def set_xticks(self, ticks):
            self.xticks = ticks

        def set_xticklabels(self, labels):
            self.xticklabels = labels

        def set_ylim(self, *_args):
            pass

        def grid(self, **_kwargs):
            pass

        def legend(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def tight_layout(self):
            pass

        def legend(self, *_args, **_kwargs):
            pass

        def subplots_adjust(self, **_kwargs):
            pass

    ticks = experiment.normalize_scan_profile()["axis_ticks"]
    scan_rows = [{
        "target_uc": "1/10", "scheduler": "ASAP-BLOCK",
        "wholepass_ratio": 0.5, "ci95_low": 0.4, "ci95_high": 0.6,
    }]
    dmr_rows = [{
        "target_uc": "1/10", "scheduler": "ASAP-BLOCK", "dmr": 0.5,
        "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
    }]
    slice_config = {
        "x_key": "target_uc", "fixed_key": "target_ue",
        "fixed_value": "7/10", "label": "low", "x_values": ["1/10"],
    }
    composite_rows = [{
        "scheduler": "ASAP-BLOCK", "target_uc": "1/10",
        "wholepass_ratio": 0.5, "ci95_low": 0.4, "ci95_high": 0.6,
        "dmr": 0.5, "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
    }]

    def capture_axes():
        axes = [CaptureAxis() for _ in range(3)]
        monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
        return axes

    axes = capture_axes()
    plot_scan(
        scan_rows, tmp_path, "scan.png", "target_uc", ["ASAP-BLOCK"], "U_C", "scan",
        axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes] == [expected_labels] * 3
    assert [axis.xticks for axis in axes] == [expected_ticks] * 3
    assert all(0 not in axis.errorbar_x for axis in axes)

    axes = capture_axes()
    plot_dmr_scan(
        dmr_rows, tmp_path, "dmr.png", "target_uc", ["ASAP-BLOCK"], "U_C", "dmr",
        axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes] == [expected_labels] * 3
    assert [axis.xticks for axis in axes] == [expected_ticks] * 3
    assert all(0 not in axis.errorbar_x for axis in axes)

    axes = [[CaptureAxis()]]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    plot_composite_scan(
        [(slice_config, composite_rows)], tmp_path, "composite.png", "target_uc",
        ["ASAP-BLOCK"], "U_C", "composite", axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes[0]] == [expected_labels]
    assert [axis.xticks for axis in axes[0]] == [expected_ticks]
    assert all(0 not in axis.errorbar_x for axis in axes[0])

    axes = [[CaptureAxis()]]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    plot_composite_dmr(
        [(slice_config, composite_rows)], tmp_path, "composite-dmr.png", "target_uc",
        ["ASAP-BLOCK"], "U_C", "composite dmr", 0.0,
        axis_min="0", axis_max="1", axis_ticks=ticks,
    )
    assert [axis.xticklabels for axis in axes[0]] == [expected_labels]
    assert [axis.xticks for axis in axes[0]] == [expected_ticks]
    assert all(0 not in axis.errorbar_x for axis in axes[0])


@pytest.mark.parametrize(
    "xkey, fixed_key, campaign, fixed_values",
    [
        (
            "target_uc", "target_ue", experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN,
            tuple(str(experiment.V7_REFERENCE_UES[level]) for level in ("low", "medium", "high")),
        ),
        (
            "target_ue", "target_uc", experiment.V7_UE_SERVICE_SCALING_CAMPAIGN,
            ("3/10", "1/2", "7/10"),
        ),
    ],
)
def test_composite_main_plot_has_three_vertical_nine_scheduler_panels(
    tmp_path, monkeypatch, xkey, fixed_key, campaign, fixed_values,
):
    import matplotlib.pyplot as plt

    class CaptureAxis:
        def __init__(self):
            self.scheduler_labels = []
            self.title = None
            self.ylabel = None

        def errorbar(self, *_args, **kwargs):
            self.scheduler_labels.append(kwargs["label"])
            return SimpleNamespace(lines=[SimpleNamespace()])

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, label):
            self.ylabel = label

        def set_title(self, title):
            self.title = title

        def set_xlim(self, *_args):
            pass

        def set_xticks(self, *_args):
            pass

        def set_xticklabels(self, *_args):
            pass

        def set_ylim(self, *_args):
            pass

        def grid(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def legend(self, handles, labels, **_kwargs):
            self.legend_labels = labels

        def subplots_adjust(self, **_kwargs):
            pass

    captured = {}

    def capture_subplots(nrows, ncols, **_kwargs):
        captured["shape"] = (nrows, ncols)
        axes = [[CaptureAxis()] for _ in range(nrows)]
        captured["axes"] = axes
        captured["figure"] = CaptureFigure()
        return captured["figure"], axes

    monkeypatch.setattr(plt, "subplots", capture_subplots)
    monkeypatch.setattr(plt, "close", lambda _figure: None)
    ticks = experiment.normalize_scan_profile()["axis_ticks"]
    slice_rows = []
    for label, fixed_value in zip(("low", "medium", "high"), fixed_values):
        rows = [
            {
                "scheduler": scheduler, xkey: "1/10", "wholepass_ratio": 0.5,
                "ci95_low": 0.4, "ci95_high": 0.6, "dmr": 0.5,
                "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
            }
            for scheduler in experiment.ALL_SCHEDULERS
        ]
        slice_rows.append((
            {"x_key": xkey, "fixed_key": fixed_key, "fixed_value": fixed_value, "label": label},
            rows,
        ))
    display_labels = analyzer_module._v7_publication_slice_labels(campaign, slice_rows)

    plot_composite_scan(
        slice_rows, tmp_path, "main.png", xkey, list(experiment.ALL_SCHEDULERS),
        "U_C" if xkey == "target_uc" else "U_E", "main",
        axis_min="0", axis_max="1", axis_ticks=ticks,
        slice_display_labels=display_labels,
    )

    assert captured["shape"] == (3, 1)
    assert [axis[0].scheduler_labels for axis in captured["axes"]] == [
        list(experiment.ALL_SCHEDULERS),
    ] * 3
    assert [axis[0].title for axis in captured["axes"]] == display_labels
    assert all(
        axis[0].ylabel == "Whole-taskset pass ratio"
        for axis in captured["axes"]
    )
    assert captured["figure"].legend_labels == list(experiment.ALL_SCHEDULERS)
    if campaign == experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN:
        assert all("U_E=" not in label for label in display_labels)
        assert display_labels == [
            f"{level}: fixed supply = "
            f"{float(experiment.V7_FIXED_SUPPLIES[level] * 1000):.3f} mJ/tick"
            for level in ("low", "medium", "high")
        ]
    else:
        assert display_labels == ["low: U_C=0.3", "medium: U_C=0.5", "high: U_C=0.7"]

    plot_composite_dmr(
        slice_rows, tmp_path, "main-dmr.png", xkey, list(experiment.ALL_SCHEDULERS),
        "U_C" if xkey == "target_uc" else "U_E", "main dmr", 0.0,
        axis_min="0", axis_max="1", axis_ticks=ticks,
        slice_display_labels=display_labels,
    )
    assert captured["shape"] == (3, 1)
    assert [axis[0].scheduler_labels for axis in captured["axes"]] == [
        list(experiment.ALL_SCHEDULERS),
    ] * 3
    assert [axis[0].title for axis in captured["axes"]] == display_labels
    assert all(axis[0].ylabel == "DMR" for axis in captured["axes"])
    assert captured["figure"].legend_labels == list(experiment.ALL_SCHEDULERS)


def test_frozen_main_figure_has_exactly_51_cells_and_three_slices():
    assert len(experiment.FORMAL_CELLS) == 51
    assert len(set(experiment.FORMAL_CELLS)) == 51
    assert (Fraction(3, 10), Fraction(3, 10)) in experiment.FORMAL_CELLS
    assert experiment.FORMAL_CELLS.count((Fraction(3, 10), Fraction(3, 10))) == 1
    slices = experiment.resolve_figure_slices(
        experiment.FORMAL_CELLS,
        fixed_ue=Fraction(3, 10), fixed_uc=Fraction(3, 10),
    )
    assert slices["uc_scan"]["fixed_value"] == "3/10"
    assert slices["ue_scan"]["fixed_value"] == "3/10"
    experiment.validate_frozen_main_figure(
        experiment.FORMAL_CELLS, experiment.ALL_SCHEDULERS, horizon_ms=60000,
    )


def test_default_and_explicit_nine_scheduler_lists():
    assert experiment.parse_schedulers(None) == experiment.DEFAULT_SCHEDULERS
    assert experiment.parse_schedulers(",".join(experiment.ALL_SCHEDULERS)) == experiment.ALL_SCHEDULERS


def _priority_projection_payload():
    return [
        {"task_id": "A", "priority_rank": 0, "C": 3, "D": 9, "T": 10,
         "P": "5/2", "workload": "hash", "arrival_offset": 0},
        {"task_id": "B", "priority_rank": 1, "C": 4, "D": 5, "T": 20,
         "P": "3", "workload": "bzip2", "arrival_offset": 0},
    ]


def test_priority_policy_normalization_and_fail_closed():
    assert normalize_scheduler_priority_policy(" rm ") == "RM"
    assert normalize_scheduler_priority_policy("dm") == "DM"
    for value in ("EDF", "", "garbage", None):
        with pytest.raises(Exception, match="priority_policy"):
            normalize_scheduler_priority_policy(value)


def test_rm_projection_is_byte_for_byte_backward_compatible():
    payload = _priority_projection_payload()
    legacy = _render_taskset_yaml(payload)
    explicit = _render_taskset_yaml(payload, priority_policy="RM")
    assert legacy == explicit
    assert "fixed_priority_rank" not in explicit


def test_dm_projection_uses_relative_deadline_and_does_not_mutate_payload():
    payload = _priority_projection_payload()
    original = [dict(row) for row in payload]
    assert derive_fixed_priority_ranks(payload, "DM") == {"A": 1, "B": 0}
    rendered = _render_taskset_yaml(payload, priority_policy="DM")
    assert "fixed_priority_rank=1" in rendered
    assert "fixed_priority_rank=0" in rendered
    assert payload == original
    assert [(row["task_id"], row["C"], row["D"], row["T"], row["P"], row["workload"])
            for row in payload] == [
                ("A", 3, 9, 10, "5/2", "hash"),
                ("B", 4, 5, 20, "3", "bzip2"),
            ]


def test_dm_priority_tie_breaks_are_deterministic_and_d_equals_t_matches_rm():
    payload = [
        {"task_id": "A", "priority_rank": 0, "C": 1, "D": 5, "T": 10},
        {"task_id": "B", "priority_rank": 1, "C": 1, "D": 5, "T": 20},
        {"task_id": "C", "priority_rank": 2, "C": 1, "D": 5, "T": 20},
    ]
    assert derive_fixed_priority_ranks(payload, "DM") == {"A": 0, "B": 1, "C": 2}
    implicit = [
        {"task_id": "A", "priority_rank": 0, "C": 1, "D": 10, "T": 10},
        {"task_id": "B", "priority_rank": 1, "C": 1, "D": 20, "T": 20},
    ]
    assert derive_fixed_priority_ranks(implicit, "DM") == {"A": 0, "B": 1}


def test_implicit_same_period_tie_preserves_rm_order_for_dm():
    payload = [
        {"task_id": "C", "priority_rank": 0, "C": 1, "D": 10, "T": 10},
        {"task_id": "A", "priority_rank": 1, "C": 1, "D": 20, "T": 20},
        {"task_id": "B", "priority_rank": 2, "C": 1, "D": 20, "T": 20},
    ]
    assert all(row["D"] == row["T"] for row in payload)
    assert derive_fixed_priority_ranks(payload, "RM") is None
    rm_order = [row["task_id"] for row in payload]
    dm_ranks = derive_fixed_priority_ranks(payload, "DM")
    dm_order = [task_id for task_id, _rank in sorted(dm_ranks.items(), key=lambda item: item[1])]
    assert rm_order == ["C", "A", "B"]
    assert dm_order == rm_order


def test_implicit_payload_order_is_the_frozen_rm_priority_invariant():
    payload = [
        {"task_id": "C", "priority_rank": 0, "C": 1, "D": 10, "T": 10},
        {"task_id": "B", "priority_rank": 2, "C": 1, "D": 20, "T": 20},
        {"task_id": "A", "priority_rank": 1, "C": 1, "D": 20, "T": 20},
    ]
    assert all(row["D"] == row["T"] for row in payload)
    for policy in ("RM", "DM"):
        with pytest.raises(
            SimulationConfigurationError,
            match="contiguous priority order",
        ):
            derive_fixed_priority_ranks(payload, policy)


def test_rm_and_dm_request_identity_is_paired_but_distinct():
    class Taskset:
        taskset_id = "same-taskset"
        semantic_hash = "same-hash"
        target_utilization = Fraction(2)
        actual_utilization = Fraction(2)
        processors = 4
        taskset_index = 0
        seed = 9

    rm = experiment.request_rows(
        [Taskset()], ((Fraction(1, 2), Fraction(1, 5)),),
        ("ASAP-BLOCK",), 2000, priority_policy="RM",
    )[0]
    dm = experiment.request_rows(
        [Taskset()], ((Fraction(1, 2), Fraction(1, 5)),),
        ("ASAP-BLOCK",), 2000, priority_policy="DM",
    )[0]
    assert rm["taskset_id"] == dm["taskset_id"]
    assert rm["taskset_hash"] == dm["taskset_hash"]
    assert rm["priority_policy"] == "RM"
    assert dm["priority_policy"] == "DM"
    assert rm["request_id"] != dm["request_id"]


def test_nine_scheduler_mapping_is_complete_and_unique():
    assert tuple(perf_g.FORMAL_SCHEDULERS) == experiment.ALL_SCHEDULERS
    assert len(perf_g.FORMAL_SCHEDULERS) == 9
    assert len(perf_g.SCHEDULER_CLI) == 9
    assert len(set(perf_g.SCHEDULER_CLI.values())) == 9
    assert all(
        perf_g.SCHEDULER_CLI[name].startswith("gpfp_")
        for name in perf_g.FORMAL_SCHEDULERS
    )


def test_system_templates_are_scoped_to_their_experiment_paths():
    ordinary = experiment._config(
        1, utilizations=[Fraction(1, 10)], count=1, processors=4, tasks=10,
        period_min=perf_g.PERIOD_MIN_MS, period_max=perf_g.PERIOD_MAX_MS,
        min_task_util=perf_g.MIN_TASK_UTILIZATION,
        max_task_util=perf_g.MAX_TASK_UTILIZATION,
        tolerance=perf_g.UTILIZATION_TOLERANCE,
    )
    perf_g_default = perf_g._task_generation_config(
        "FORMAL", [Fraction(1, 10)], 1,
    )
    assert ordinary["energy"]["service_curve"]["system_template"] == (
        experiment.ORDINARY_SYSTEM_TEMPLATE
    )
    assert perf_g_default["energy"]["service_curve"]["system_template"] == (
        perf_g.BASE_SYSTEM_TEMPLATE
    )
    assert perf_g.BASE_SYSTEM_TEMPLATE != experiment.ORDINARY_SYSTEM_TEMPLATE


def _synthetic_service_config(template_name):
    config = experiment._config(
        1, utilizations=[Fraction(1, 10)], count=1, processors=4, tasks=2,
        period_min=1, period_max=2, min_task_util=Fraction(1, 100),
        max_task_util=Fraction(1, 10), tolerance=Fraction(1, 100),
    )
    config["energy"]["service_curve"]["system_template"] = template_name
    config["energy"]["service_curve"]["horizon"] = 4
    return config


def _synthetic_template(source, *, solar_file, pv_area, pv_efficiency):
    rendered = source.replace(
        'solar_data_file: "data/processed/shenyang_solar_minute.csv"',
        f'solar_data_file: "{solar_file}"',
    )
    rendered = rendered.replace("pv_area_m2: 1.0", f"pv_area_m2: {pv_area}")
    rendered = rendered.replace(
        "pv_efficiency: 0.18", f"pv_efficiency: {pv_efficiency}"
    )
    return rendered


def test_synthetic_service_does_not_require_solar_csv(tmp_path, monkeypatch):
    source = (
        taskset_store.PROJECT_ROOT / experiment.ORDINARY_SYSTEM_TEMPLATE
    ).read_text(encoding="utf-8")
    template = tmp_path / "synthetic.yml"
    template.write_text(
        _synthetic_template(
            source, solar_file="missing.csv", pv_area="1", pv_efficiency="0.18",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(taskset_store, "PROJECT_ROOT", tmp_path)

    service = taskset_store.prepare_service_curve(
        _synthetic_service_config(template.name), tmp_path / "service"
    )
    material = json.loads(service.raw_spec)

    assert material["use_real_solar_data"] is False
    assert material["harvest_model"] == experiment.HARVEST_MODEL
    assert material["base_harvesting_rate"]
    assert "solar_data_sha256" not in material
    assert "effective_pv_area_m2" not in material
    assert "source_template_sha256" not in material


def test_synthetic_service_identity_ignores_solar_and_pv_fields(
    tmp_path, monkeypatch,
):
    source = (
        taskset_store.PROJECT_ROOT / experiment.ORDINARY_SYSTEM_TEMPLATE
    ).read_text(encoding="utf-8")
    template = tmp_path / "synthetic.yml"
    monkeypatch.setattr(taskset_store, "PROJECT_ROOT", tmp_path)
    config = _synthetic_service_config(template.name)

    template.write_text(
        _synthetic_template(
            source, solar_file="missing-a.csv", pv_area="1", pv_efficiency="0.18",
        ),
        encoding="utf-8",
    )
    first = taskset_store.prepare_service_curve(config, tmp_path / "first")

    template.write_text(
        _synthetic_template(
            source, solar_file="missing-b.csv", pv_area="7.5", pv_efficiency="0.91",
        ),
        encoding="utf-8",
    )
    second = taskset_store.prepare_service_curve(config, tmp_path / "second")

    assert first.values == second.values
    assert first.raw_spec == second.raw_spec
    assert first.identity == second.identity


def test_strict_wholepass_and_technical_outcome_contract():
    on_time = evaluate_outcome(
        [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 9}],
        ["0"], horizon=10, minimum_adjudicable_jobs=1, strict_wholepass=True,
    )
    assert on_time["wholepass"] is True
    miss = evaluate_outcome(
        [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": 10}],
        ["0"], horizon=20, minimum_adjudicable_jobs=1, strict_wholepass=True,
    )
    assert miss["wholepass"] is False
    unfinished = evaluate_outcome(
        [{"task_id": "0", "release": 0, "absolute_deadline": 9, "completion": None}],
        ["0"], horizon=20, minimum_adjudicable_jobs=1, strict_wholepass=True,
    )
    assert unfinished["wholepass"] is False
    technical = evaluate_outcome(
        [], ["0"], horizon=20, minimum_adjudicable_jobs=1,
        simulation_completed=False, technical_error="timeout", strict_wholepass=True,
    )
    assert technical["wholepass"] is None
    assert technical["technical_failure"] is True


def test_wilson_ci_handles_zero_one_and_middle():
    low = wilson_ci(0, 100)
    high = wilson_ci(100, 100)
    middle = wilson_ci(5, 10)
    assert 0 <= low[0] <= low[1] <= 1
    assert 0 <= high[0] <= high[1] <= 1
    assert 0 <= middle[0] <= middle[1] <= 1
    assert low[0] == 0.0
    assert low[0] <= 0 <= low[1]
    assert high[0] <= 1 <= high[1]
    assert high[1] == 1.0
    assert middle[0] < 0.5 < middle[1]

    for k in range(101):
        p = k / 100
        interval = wilson_ci(k, 100)
        assert 0 <= interval[0] <= p <= interval[1] <= 1


def test_plot_scan_accepts_zero_wholepass_ratio(tmp_path):
    rows = [{
        "target_uc": "1/10",
        "target_ue": "7/10",
        "scheduler": "ASAP-BLOCK",
        "wholepass_ratio": 0.0,
        "ci95_low": wilson_ci(0, 100)[0],
        "ci95_high": wilson_ci(0, 100)[1],
    }]

    plot_scan(
        rows,
        tmp_path,
        "zero-wholepass.png",
        "target_uc",
        ["ASAP-BLOCK"],
        "U_C",
        "test",
    )

    assert (tmp_path / "zero-wholepass.png").is_file()


def test_plot_styles_are_consistent_across_panels_and_emphasize_asap(
    tmp_path, monkeypatch,
):
    import matplotlib.pyplot as plt

    class CaptureAxis:
        def __init__(self):
            self.errorbars = []
            self.ylabel = None
            self.ylims = []

        def errorbar(self, *args, **kwargs):
            self.errorbars.append(kwargs)

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, label):
            self.ylabel = label

        def set_title(self, _title):
            pass

        def set_ylim(self, *args):
            self.ylims.append(args)

        def grid(self, **_kwargs):
            pass

        def legend(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def tight_layout(self):
            pass

    axes = [CaptureAxis() for _ in range(3)]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    monkeypatch.setattr(plt, "close", lambda _figure: None)
    rows = []
    for panel_schedulers in experiment.PANEL_GROUPS.values():
        for scheduler in panel_schedulers:
            rows.append({
                "target_uc": "1/10", "scheduler": scheduler,
                "wholepass_ratio": 0.5, "ci95_low": 0.4, "ci95_high": 0.6,
            })

    plot_scan(rows, tmp_path, "styles.png", "target_uc",
              list(experiment.ALL_SCHEDULERS), "U_C", "RM — Whole-taskset pass ratio")

    assert all(len(axis.errorbars) == 3 for axis in axes)
    per_panel = [
        {
            call["label"].split("-", 1)[0]: {
                key: call[key]
                for key in ("color", "linestyle", "linewidth", "markersize", "zorder")
            }
            for call in axis.errorbars
        }
        for axis in axes
    ]
    assert per_panel[0] == per_panel[1] == per_panel[2]
    assert per_panel[0]["ASAP"]["color"] == "tab:blue"
    assert per_panel[0]["ASAP"]["linestyle"] == "-"
    assert per_panel[0]["ASAP"]["linewidth"] > per_panel[0]["ALAP"]["linewidth"]
    assert per_panel[0]["ASAP"]["markersize"] > per_panel[0]["ALAP"]["markersize"]
    assert per_panel[0]["ASAP"]["zorder"] > per_panel[0]["ALAP"]["zorder"]
    assert _plot_style("ALAP")["linestyle"] == "--"
    assert _plot_style("ST")["linestyle"] == ":"

    dmr_rows = [{
        "target_uc": "1/10", "scheduler": scheduler, "dmr": 0.5,
        "dmr_ci95_low": 0.4, "dmr_ci95_high": 0.6,
    } for scheduler in experiment.ALL_SCHEDULERS]
    plot_dmr_scan(
        dmr_rows, tmp_path, "styles-dmr.png", "target_uc",
        list(experiment.ALL_SCHEDULERS), "U_C",
        "DM — Job-level deadline-meeting ratio (DMR)",
    )
    assert axes[0].ylabel == "Deadline-meeting ratio (DMR)"
    assert all(axis.ylims[-1] == (0.0, 1.0) for axis in axes)


def test_dmr_ymin_is_validated_and_never_clips_ci(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt

    class CaptureAxis:
        def __init__(self):
            self.ylims = []

        def errorbar(self, *args, **kwargs):
            pass

        def set_xlabel(self, _label):
            pass

        def set_ylabel(self, _label):
            pass

        def set_title(self, _title):
            pass

        def set_ylim(self, *args):
            self.ylims.append(args)

        def grid(self, **_kwargs):
            pass

        def legend(self, **_kwargs):
            pass

    class CaptureFigure:
        def savefig(self, _path):
            pass

        def suptitle(self, _title):
            pass

        def tight_layout(self):
            pass

    axes = [CaptureAxis() for _ in range(3)]
    monkeypatch.setattr(plt, "subplots", lambda *args, **kwargs: (CaptureFigure(), axes))
    monkeypatch.setattr(plt, "close", lambda _figure: None)
    rows = [{
        "target_uc": "1/10", "scheduler": "ASAP-BLOCK", "dmr": 0.95,
        "dmr_ci95_low": 0.90, "dmr_ci95_high": 0.99,
    }]

    plot_dmr_scan(
        rows, tmp_path, "zoomed.png", "target_uc", ["ASAP-BLOCK"],
        "U_C", "RM — DMR (zoomed y-axis)", 0.89,
    )
    assert all(axis.ylims == [(0.89, 1.0)] for axis in axes)

    with pytest.raises(ValueError, match="minimum observed.*0.9"):
        plot_dmr_scan(
            rows, tmp_path, "clipped.png", "target_uc", ["ASAP-BLOCK"],
            "U_C", "DMR", 0.91,
        )
    for invalid in (-0.01, 1.0, float("nan")):
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            plot_dmr_scan(
                rows, tmp_path, "invalid.png", "target_uc", ["ASAP-BLOCK"],
                "U_C", "DMR", invalid,
            )


def test_dmr_ymin_cli_values_are_finite_and_in_half_open_unit_interval():
    assert _parse_dmr_ymin("0") == 0.0
    assert _parse_dmr_ymin("0.89") == 0.89
    for invalid in ("-0.01", "1", "nan", "inf"):
        with pytest.raises(Exception, match=r"\[0, 1\)"):
            _parse_dmr_ymin(invalid)


@pytest.mark.parametrize("adjudicable,misses,expected", [
    (100, 0, 1.0), (100, 1, 0.99), (100, 100, 0.0),
])
def test_dmr_uses_job_weighted_taskset_counts(adjudicable, misses, expected):
    row = {
        "outcome": _available_outcome(adjudicable, misses, misses == 0),
        "wholepass": misses == 0,
    }
    summary = summarize_dmr(
        [row], target_uc="1/10", target_ue="7/10",
        scheduler="ASAP-BLOCK", campaign_seed=710213,
    )
    assert summary["total_on_time_jobs"] == adjudicable - misses
    assert summary["dmr"] == expected
    assert summary["dmr_ci95_low"] is None
    assert summary["dmr_ci95_high"] is None


def test_dmr_is_job_weighted_not_mean_of_taskset_ratios():
    rows = [
        {"outcome": _available_outcome(100, 0), "wholepass": True},
        {"outcome": _available_outcome(10, 5, False), "wholepass": False},
    ]
    summary = summarize_dmr(
        rows, target_uc="3/10", target_ue="7/10",
        scheduler="ASAP-BLOCK", campaign_seed=710213,
    )
    assert summary["total_adjudicable_jobs"] == 110
    assert summary["total_deadline_miss_jobs"] == 5
    assert summary["total_on_time_jobs"] == 105
    assert summary["dmr"] == 105 / 110
    assert summary["dmr"] != 0.75


@pytest.mark.parametrize("rows", [
    [{"outcome": _available_outcome(100, 1), "wholepass": True}],
    [{"outcome": _available_outcome(0, 0), "wholepass": True}],
    [{"outcome": _available_outcome(10, 11, False), "wholepass": False}],
    [{"outcome": {"outcome_status": "TECHNICAL_FAILURE"}, "wholepass": None}],
])
def test_dmr_cross_checks_fail_closed(rows):
    with pytest.raises(ValueError):
        summarize_dmr(
            rows, target_uc="1/10", target_ue="7/10",
            scheduler="ASAP-BLOCK", campaign_seed=710213,
        )


def test_dmr_cluster_bootstrap_is_reproducible_and_bounded():
    counts = [(100, 0), (10, 5), (30, 3)]
    first = dmr_cluster_bootstrap_ci(counts, seed=1234, replicates=200)
    second = dmr_cluster_bootstrap_ci(counts, seed=1234, replicates=200)
    assert first == second
    assert 0 <= first[0] <= first[1] <= 1


def test_dmr_cluster_bootstrap_is_unavailable_for_one_taskset():
    assert dmr_cluster_bootstrap_ci([(100, 1)], seed=1234) == (None, None)


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


def test_frozen_grid_plans_nine_paired_requests_per_cell_taskset():
    class Taskset:
        processors = 4
        actual_utilization = Fraction(1)
        seed = 123

        def __init__(self, uc, index):
            self.target_utilization = uc * self.processors
            self.taskset_index = index
            self.taskset_id = f"taskset-{uc}-{index}"
            self.semantic_hash = f"hash-{uc}-{index}"

    tasksets = [Taskset(uc, 0) for uc in experiment.FORMAL_UC_SCAN]
    rows = experiment.request_rows(
        tasksets, experiment.FORMAL_CELLS, experiment.ALL_SCHEDULERS, 60000,
    )
    assert len(rows) == 51 * 9
    groups = {}
    for row in rows:
        groups.setdefault((row["target_uc"], row["target_ue"], row["generation_index"]), []).append(row)
    assert len(groups) == 51
    assert all(len(group) == 9 for group in groups.values())
    assert all({row["scheduler"] for row in group} == set(experiment.ALL_SCHEDULERS)
               for group in groups.values())
    for uc in experiment.FORMAL_UC_SCAN:
        ids = {
            row["taskset_id"] for row in rows
            if Fraction(row["target_uc"]) == uc
        }
        assert len(ids) == 1


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
    assert {
        key: material[key] for key in experiment.HARVEST_MODEL_IDENTITY
    } == experiment.HARVEST_MODEL_IDENTITY


def test_analyzer_writes_both_figure_csvs(tmp_path):
    config = {"cells": [["1/2", "2/5"]], "samples_per_cell": 1,
              "schedulers": ["ASAP-BLOCK"], "processors": 4,
              "util_tolerance_total": "1/100", "use_real_solar_data": False,
              **experiment.HARVEST_MODEL_IDENTITY}
    taskset = {"taskset_id": "t", "taskset_hash": "h", "canonical_task_power": True,
               "target_uc": "1/2", "actual_uc": "1/2"}
    request = {"request_id": "r", "taskset_id": "t", "taskset_hash": "h",
               "target_uc": "1/2", "target_ue": "2/5", "generation_index": 0,
               "scheduler": "ASAP-BLOCK", **_harvest_model_fields()}
    energy = {"target_ue": "2/5", "eta": "5/2", "P_dem_j_per_tick": "3/5",
              "target_supply_mean_j_per_tick": "3/2", "raw_reference_mean_j_per_tick": "1",
              "solar_scale": "3/2",
              "runtime_configured_average_supply_j_per_tick": "3/2",
              "actual_ue": "2/5", "actual_ue_abs_error": "0",
              "actual_ue_rel_error": "0", "actual_ue_minus_target_ue": "0",
              **_harvest_model_fields()}
    result = {**request, "energy": energy, "outcome": _available_outcome(),
              "schedulable": True, "deadline_miss": False,
              "simulation_status": "SIM_PASS_OBSERVED", "technical_error": None,
              "wholepass": True, "taskset_pass": True}
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text(json.dumps(taskset) + "\n", encoding="utf-8")
    (tmp_path / "requests.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
    assert analyze(tmp_path)["complete"]
    assert (tmp_path / "figure_scheduler_uc.csv").is_file()
    assert (tmp_path / "figure_scheduler_ue.csv").is_file()
    assert (tmp_path / "figure_scheduler_uc.png").is_file()
    assert (tmp_path / "figure_scheduler_ue.png").is_file()
    assert (tmp_path / "summary_dmr.csv").is_file()
    assert (tmp_path / "figure_scheduler_uc_dmr.csv").is_file()
    assert (tmp_path / "figure_scheduler_ue_dmr.csv").is_file()
    assert (tmp_path / "figure_scheduler_uc_dmr.png").is_file()
    assert (tmp_path / "figure_scheduler_ue_dmr.png").is_file()


def test_analyzer_rejects_empty_campaign_explicitly(tmp_path):
    config = {
        "cells": [["1/2", "2/5"]], "samples_per_cell": 1,
        "schedulers": ["ASAP-BLOCK"], "processors": 4,
        "util_tolerance_total": "1/100", "use_real_solar_data": False,
        **experiment.HARVEST_MODEL_IDENTITY,
    }
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name in ("tasksets.jsonl", "requests.jsonl", "results.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="requests are empty"):
        analyze(tmp_path)


def test_analyzer_keeps_csv_and_png_scans_on_the_same_fixed_axis(tmp_path, monkeypatch):
    config = {"cells": [
        ["1/10", "2/5"], ["1/5", "2/5"],
        ["1/2", "1/5"], ["1/2", "3/10"],
        ["1/2", "2/5"], ["1/2", "1/2"],
    ], "samples_per_cell": 1, "schedulers": ["ASAP-BLOCK"],
       "processors": 4, "util_tolerance_total": "1/100",
       "use_real_solar_data": False, **experiment.HARVEST_MODEL_IDENTITY}
    tasksets = [
        {"taskset_id": "t-1", "taskset_hash": "h-1",
         "canonical_task_power": True, "target_uc": "1/10", "actual_uc": "1/10"},
        {"taskset_id": "t-2", "taskset_hash": "h-2",
         "canonical_task_power": True, "target_uc": "1/5", "actual_uc": "1/5"},
        {"taskset_id": "t-5", "taskset_hash": "h-5",
         "canonical_task_power": True, "target_uc": "1/2", "actual_uc": "1/2"},
    ]
    cells = [("t-1", "1/10", "2/5"), ("t-2", "1/5", "2/5"),
             ("t-5", "1/2", "1/5"), ("t-5", "1/2", "3/10"),
             ("t-5", "1/2", "2/5"), ("t-5", "1/2", "1/2")]
    requests = []
    results = []
    for index, (taskset_id, target_uc, target_ue) in enumerate(cells):
        request = {
            "request_id": f"r-{index}", "taskset_id": taskset_id,
            "taskset_hash": next(row["taskset_hash"] for row in tasksets if row["taskset_id"] == taskset_id),
            "target_uc": target_uc, "target_ue": target_ue,
            "generation_index": 0, "scheduler": "ASAP-BLOCK",
            **_harvest_model_fields(),
        }
        eta = str(1 / Fraction(target_ue))
        target_supply = str(1 / Fraction(target_ue))
        energy = {
            "target_ue": target_ue, "eta": eta,
            "P_dem_j_per_tick": "1", "E_burst_j": "10",
            "battery_capacity_j": "100", "initial_energy_j": "50",
            "target_supply_mean_j_per_tick": target_supply,
            "raw_reference_mean_j_per_tick": "1", "solar_scale": target_supply,
            "runtime_configured_average_supply_j_per_tick": target_supply,
            "actual_ue": target_ue, "actual_ue_abs_error": "0",
            "actual_ue_rel_error": "0", "actual_ue_minus_target_ue": "0",
            "harvest_trace_id": "fixture-trace",
            **_harvest_model_fields(),
        }
        requests.append(request)
        results.append({
            **request, "energy": energy, "schedulable": True,
            "deadline_miss": False, "simulation_status": "SIM_PASS_OBSERVED",
            "technical_error": None, "outcome": _available_outcome(),
            "wholepass": True, "taskset_pass": True,
        })
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tasksets), encoding="utf-8"
    )
    (tmp_path / "requests.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in requests), encoding="utf-8"
    )
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
    )
    plotted = {}
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title:
            plotted.setdefault(filename, list(rows)),
    )
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_dmr_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title, ymin=0.0:
            plotted.setdefault("dmr:" + filename, list(rows)),
    )
    assert analyze(tmp_path)["complete"]

    uc_rows = list(csv.DictReader((tmp_path / "figure_scheduler_uc.csv").open()))
    ue_rows = list(csv.DictReader((tmp_path / "figure_scheduler_ue.csv").open()))
    assert {row["target_ue"] for row in uc_rows} == {"2/5"}
    assert {row["target_uc"] for row in ue_rows} == {"1/2"}
    assert [row["target_uc"] for row in uc_rows] == ["1/10", "1/5", "1/2"]
    assert [row["target_ue"] for row in ue_rows] == ["1/5", "3/10", "2/5", "1/2"]
    assert [row["target_uc"] for row in plotted["figure_scheduler_uc.png"]] == [
        "1/10", "1/5", "1/2",
    ]
    assert [row["target_ue"] for row in plotted["figure_scheduler_ue.png"]] == [
        "1/5", "3/10", "2/5", "1/2",
    ]
    assert len(uc_rows) == 3
    assert len(ue_rows) == 4
    dmr_uc_rows = list(csv.DictReader((tmp_path / "figure_scheduler_uc_dmr.csv").open()))
    dmr_ue_rows = list(csv.DictReader((tmp_path / "figure_scheduler_ue_dmr.csv").open()))
    assert [row["target_uc"] for row in dmr_uc_rows] == ["1/10", "1/5", "1/2"]
    assert [row["target_ue"] for row in dmr_ue_rows] == ["1/5", "3/10", "2/5", "1/2"]
    assert [row["target_uc"] for row in plotted["dmr:figure_scheduler_uc_dmr.png"]] == [
        "1/10", "1/5", "1/2",
    ]
    assert [row["target_ue"] for row in plotted["dmr:figure_scheduler_ue_dmr.png"]] == [
        "1/5", "3/10", "2/5", "1/2",
    ]


def _write_configured_analyzer_fixture(tmp_path, priority_policy="RM"):
    config = {
        "cells": [["1/10", "3/7"], ["1/5", "3/7"],
                  ["2/5", "3/7"], ["2/5", "1/2"], ["2/5", "4/5"]],
        "samples_per_cell": 1, "schedulers": ["ASAP-BLOCK"],
        "priority_policy": priority_policy,
        "processors": 4, "util_tolerance_total": "1/100",
        "use_real_solar_data": False, **experiment.HARVEST_MODEL_IDENTITY,
        "figure_slices": {
            "uc_scan": {
                "x_key": "target_uc", "fixed_key": "target_ue",
                "fixed_value": "3/7",
            },
            "ue_scan": {
                "x_key": "target_ue", "fixed_key": "target_uc",
                "fixed_value": "2/5",
            },
        },
    }
    tasksets = [
        {"taskset_id": "t-1", "taskset_hash": "h-1",
         "canonical_task_power": True, "target_uc": "1/10", "actual_uc": "1/10"},
        {"taskset_id": "t-2", "taskset_hash": "h-2",
         "canonical_task_power": True, "target_uc": "1/5", "actual_uc": "1/5"},
        {"taskset_id": "t-4", "taskset_hash": "h-4", "canonical_task_power": True,
         "target_uc": "2/5", "actual_uc": "2/5"},
    ]
    cells = [("t-1", "1/10", "3/7"), ("t-2", "1/5", "3/7"),
             ("t-4", "2/5", "3/7"), ("t-4", "2/5", "1/2"),
             ("t-4", "2/5", "4/5")]
    requests = []
    results = []
    for index, (taskset_id, target_uc, target_ue) in enumerate(cells):
        taskset_hash = next(row["taskset_hash"] for row in tasksets if row["taskset_id"] == taskset_id)
        request = {
            "request_id": f"configured-r-{index}", "taskset_id": taskset_id,
            "taskset_hash": taskset_hash, "target_uc": target_uc,
            "target_ue": target_ue, "generation_index": 0,
            "scheduler": "ASAP-BLOCK", "priority_policy": priority_policy,
            **_harvest_model_fields(),
        }
        results.append({
            **request,
            "energy": {
                "target_ue": target_ue, "eta": str(1 / Fraction(target_ue)),
                "P_dem_j_per_tick": "1", "E_burst_j": "10",
                "battery_capacity_j": "100", "initial_energy_j": "50",
                "target_supply_mean_j_per_tick": str(1 / Fraction(target_ue)),
                "raw_reference_mean_j_per_tick": "1",
                "solar_scale": str(1 / Fraction(target_ue)),
                "runtime_configured_average_supply_j_per_tick": str(1 / Fraction(target_ue)),
                "actual_ue": target_ue, "actual_ue_abs_error": "0",
                "actual_ue_rel_error": "0", "actual_ue_minus_target_ue": "0",
                "harvest_trace_id": "fixture-trace",
                **_harvest_model_fields(),
            },
            "outcome": _available_outcome(),
            "schedulable": True, "deadline_miss": False,
            "simulation_status": "SIM_PASS_OBSERVED", "technical_error": None,
            "wholepass": True, "taskset_pass": True,
        })
        requests.append(request)
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name, rows in (("tasksets.jsonl", tasksets), ("requests.jsonl", requests),
                       ("results.jsonl", results)):
        (tmp_path / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )


@pytest.mark.parametrize("priority_policy", ["RM", "DM"])
def test_analyzer_uses_configured_custom_slices_and_dynamic_titles(
    tmp_path, monkeypatch, priority_policy,
):
    _write_configured_analyzer_fixture(tmp_path, priority_policy)
    plotted = {}
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title:
            plotted.setdefault(filename, (list(rows), title)),
    )
    monkeypatch.setattr(
        "scripts.analyze_scheduler_load_cross.plot_dmr_scan",
        lambda rows, output, filename, xkey, schedulers, xlabel, title, ymin=0.0:
            plotted.setdefault("dmr:" + filename, (list(rows), title, ymin)),
    )
    assert analyze(tmp_path, uc_dmr_ymin=0.11, ue_dmr_ymin=0.22)["complete"]
    uc_rows = list(csv.DictReader((tmp_path / "figure_scheduler_uc.csv").open()))
    ue_rows = list(csv.DictReader((tmp_path / "figure_scheduler_ue.csv").open()))
    assert {row["target_ue"] for row in uc_rows} == {"3/7"}
    assert {row["target_uc"] for row in ue_rows} == {"2/5"}
    assert [row["target_uc"] for row in uc_rows] == ["1/10", "1/5", "2/5"]
    assert [row["target_ue"] for row in ue_rows] == ["3/7", "1/2", "4/5"]
    assert "U_E=3/7" in plotted["figure_scheduler_uc.png"][1]
    assert "U_C=2/5" in plotted["figure_scheduler_ue.png"][1]
    assert priority_policy in plotted["figure_scheduler_uc.png"][1]
    assert priority_policy in plotted["figure_scheduler_ue.png"][1]
    assert "Whole-taskset pass ratio" in plotted["figure_scheduler_uc.png"][1]
    assert "Whole-taskset pass ratio" in plotted["figure_scheduler_ue.png"][1]
    assert "Schedulability ratio" not in plotted["figure_scheduler_uc.png"][1]
    assert "Schedulability ratio" not in plotted["figure_scheduler_ue.png"][1]
    assert "Job-level deadline-meeting ratio (DMR)" in plotted[
        "dmr:figure_scheduler_uc_dmr.png"
    ][1]
    assert "Job-level deadline-meeting ratio (DMR)" in plotted[
        "dmr:figure_scheduler_ue_dmr.png"
    ][1]
    assert priority_policy in plotted["dmr:figure_scheduler_uc_dmr.png"][1]
    assert priority_policy in plotted["dmr:figure_scheduler_ue_dmr.png"][1]
    assert plotted["dmr:figure_scheduler_uc_dmr.png"][2] == 0.11
    assert plotted["dmr:figure_scheduler_ue_dmr.png"][2] == 0.22
    assert "zoomed y-axis" in plotted["dmr:figure_scheduler_uc_dmr.png"][1]
    assert "zoomed y-axis" in plotted["dmr:figure_scheduler_ue_dmr.png"][1]
    assert [row["target_uc"] for row in plotted["figure_scheduler_uc.png"][0]] == [
        "1/10", "1/5", "2/5",
    ]
    assert [row["target_ue"] for row in plotted["figure_scheduler_ue.png"][0]] == [
        "3/7", "1/2", "4/5",
    ]


def test_analyzer_rejects_configured_slice_absent_from_cells(tmp_path):
    _write_configured_analyzer_fixture(tmp_path)
    config = json.loads((tmp_path / "run_config.json").read_text())
    config["figure_slices"]["uc_scan"]["fixed_value"] = "7/10"
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(SystemExit, match="absent from cells"):
        analyze(tmp_path)


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
    service = SimpleNamespace(system_path=tmp_path / "system.yml", identity="test-service")
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
        **_harvest_model_fields(),
    }
    monkeypatch.setattr(
        scheduler_runner.experiment, "materialize_tasksets",
        lambda *args, **kwargs: ([taskset], service),
    )

    def fake_request_rows(*args, **kwargs):
        row = dict(request)
        mode = kwargs["deadline_mode"]
        row.update({
            "experiment": experiment.V6_EXPERIMENT,
            "domain": experiment.V6_DOMAIN,
            "priority_policy": kwargs.get("priority_policy", "RM"),
            "deadline_mode": mode,
        })
        rows = []
        for index, (uc, ue) in enumerate(args[1]):
            item = dict(row, target_uc=str(uc), target_ue=str(ue))
            suffix = "" if index == 0 and mode == "constrained" else f"-{index}-{mode}"
            item["request_id"] = f"{request['request_id']}{suffix}"
            rows.append(item)
        return rows

    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        fake_request_rows,
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
            "runtime_configured_average_supply_j_per_tick": "1",
            "actual_ue": "2/5", "actual_ue_abs_error": "0",
            "actual_ue_rel_error": "0", **_harvest_model_fields(),
        },
    )
    monkeypatch.setattr(
        scheduler_runner, "evaluate_outcome",
        lambda *args, **kwargs: {
            "outcome_status": "AVAILABLE", "taskset_pass": True,
        },
    )
    monkeypatch.setattr(scheduler_runner, "run_paired_simulation", run_simulation)

    class _InlineExecutor:
        def __init__(self, *args, initializer=None, initargs=(), **kwargs):
            self._processes = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def shutdown(self, **kwargs):
            pass

        def submit(self, function, job):
            future = Future()
            try:
                future.set_result(function(job))
            except Exception as exc:
                future.set_exception(exc)
            return future

    monkeypatch.setattr(scheduler_runner, "ProcessPoolExecutor", _InlineExecutor)


def _scheduler_runner_args(
    output, resume=False, keep_traces=False, parse_concurrency=1,
    cells=None, fixed_ue=None, fixed_uc=None, priority_policy="DM",
    samples_per_cell=1, workers=1, prepare_workers=None,
):
    args = [
        "--output", str(output), "--seed", "710213", "--workers", str(workers),
        "--samples-per-cell", str(samples_per_cell),
        "--schedulers", "ASAP-BLOCK", "--simulation-horizon", "20",
        "--priority-policy", priority_policy,
        "--timeout-seconds", "5", "--simulator", str(output / "rtsim"),
        "--parse-concurrency", str(parse_concurrency),
        *( ["--keep-traces"] if keep_traces else [] ),
        *( ["--resume"] if resume else [] ),
    ]
    if prepare_workers is not None:
        args.extend(["--prepare-workers", str(prepare_workers)])
    if cells is None:
        uc_scan_values = ("1/10",)
        ue_scan_values = ("1/5",)
        fixed_ues = ("1/5",)
        fixed_ucs = ("1/10",)
        uc_labels = ("selected",)
        ue_labels = ("selected",)
    else:
        parsed_cells = experiment.parse_cells(cells)
        uc_scan_values = tuple(str(value) for value in sorted({uc for uc, _ue in parsed_cells}))
        ue_scan_values = tuple(str(value) for value in sorted({ue for _uc, ue in parsed_cells}))
        inferred = experiment.resolve_figure_slices(parsed_cells)
        fixed_ues = (fixed_ue or inferred["uc_scan"]["fixed_value"],)
        fixed_ucs = (fixed_uc or inferred["ue_scan"]["fixed_value"],)
        uc_labels = ("selected",)
        ue_labels = ("selected",)
    if fixed_ue is not None:
        fixed_ues = (fixed_ue,)
    if fixed_uc is not None:
        fixed_ucs = (fixed_uc,)
    args.extend([
        "--uc-scan-values", ",".join(uc_scan_values),
        "--ue-scan-values", ",".join(ue_scan_values),
        "--uc-figure-fixed-ues", ",".join(fixed_ues),
        "--uc-figure-labels", ",".join(uc_labels),
        "--ue-figure-fixed-ucs", ",".join(fixed_ucs),
        "--ue-figure-labels", ",".join(ue_labels),
    ])
    return args


def test_normal_horizon_pass_never_is_a_failure_trace():
    result = SimpleNamespace(
        status=SimulationStatus.PASS_OBSERVED,
        simulation_completed=True,
        completion_reason="reached_horizon",
        release_e0_valid=False,
    )
    assert not should_retain_failure_trace(
        result, {"trace_on_failure": True}
    )


def test_failure_trace_retention_remains_opt_in_for_diagnostics():
    result = SimpleNamespace(
        status=SimulationStatus.DEADLINE_MISS,
        simulation_completed=True,
        completion_reason="reached_horizon",
        release_e0_valid=True,
    )
    assert not should_retain_failure_trace(
        result, {"trace_on_failure": False}
    )
    assert should_retain_failure_trace(
        result, {"trace_on_failure": True}
    )


def test_scheduler_runner_disables_trace_retention_by_default(tmp_path, monkeypatch):
    configs = []

    def run_simulation(**kwargs):
        configs.append(kwargs["simulation_config"])
        result = SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(tmp_path / "default")) == 0
    assert configs[0]["trace_on_failure"] is False
    assert configs[0]["retain_trace"] is False

    configs.clear()
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(
        _scheduler_runner_args(tmp_path / "debug", keep_traces=True)
    ) == 0
    assert configs[0]["trace_on_failure"] is True
    assert configs[0]["retain_trace"] is True


def test_runner_uses_v6_runtime_ue_report_name(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        result = SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed",
            jobs=(), metrics={}, simulation_completed=True,
        )
        return SimpleNamespace(
            result=result, runtime_seconds=0.1, stdout_tail="",
            stderr_tail="", retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    output = tmp_path / "v6-runtime-ue"
    assert scheduler_runner.main(_scheduler_runner_args(output, priority_policy="RM")) == 0
    config = json.loads((output / "run_config.json").read_text())
    report = json.loads((output / "invariant_report.json").read_text())
    assert config["experiment"] == "scheduler-load-cross-v6"
    assert config["domain"] == "ASAP_BLOCK:SCHEDULER_LOAD_CROSS:v6"
    assert config["deadline_modes"] == ["constrained", "implicit"]
    assert config["expected_request_count"] == 2
    assert report["runtime_config_ue_exact"] is True
    assert "actual_" + "ue_exact" not in report


def test_effective_parser_concurrency_keeps_worker_pool_independent():
    assert scheduler_runner.effective_concurrent_parsers(30, 8) == 8
    assert scheduler_runner.effective_concurrent_parsers(8, 30) == 8


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock gate")
def test_shared_trace_parse_gate_bounds_real_processes_and_releases_on_error(
    tmp_path,
):
    context = multiprocessing.get_context("fork")
    active = context.Value("i", 0, lock=False)
    maximum = context.Value("i", 0, lock=False)
    counter_lock = context.Lock()
    processes = [
        context.Process(
            target=_trace_parse_gate_worker,
            args=(tmp_path / "slots", 2, active, maximum, counter_lock, index == 0),
        )
        for index in range(6)
    ]
    try:
        for process in processes:
            process.start()
        deadline = time.monotonic() + 5.0
        for process in processes:
            process.join(max(0.0, deadline - time.monotonic()))
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        assert maximum.value <= 2
        assert active.value == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(1.0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock gate")
def test_trace_parse_gate_preserves_strict_json_value_across_runtime_limits(
    tmp_path, monkeypatch,
):
    path = tmp_path / "trace.json"
    path.write_text('{"events": [], "value": [1, 2, 3]}', encoding="utf-8")
    observed = []
    for concurrency in (1, 2, 4):
        monkeypatch.setenv("PARTSIM_TRACE_PARSE_CONCURRENCY", str(concurrency))
        monkeypatch.setenv("PARTSIM_TRACE_PARSE_SLOT_DIR", str(tmp_path / "slots"))
        observed.append(_strict_json(path))
    assert observed[0] == observed[1] == observed[2]


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock gate")
def test_trace_parse_gate_rejects_invalid_runtime_limit(monkeypatch):
    monkeypatch.setenv("PARTSIM_TRACE_PARSE_CONCURRENCY", "not-an-integer")
    with pytest.raises(SimulationTraceError, match="invalid PARTSIM_TRACE_PARSE_CONCURRENCY"):
        with _trace_parse_slot():
            pass


def test_implicit_streaming_opt_in_is_equivalent_for_all_nine_scheduler_ids(tmp_path):
    for scheduler in perf_g.SCHEDULER_CLI.values():
        path = tmp_path / f"{scheduler}.json"
        _write_schema2_stream_trace(path, scheduler)
        legacy = _parse_schema2_stream_trace(path, scheduler)
        streamed = _parse_schema2_stream_trace(path, scheduler, stream=True)
        assert simulation_result_to_dict(legacy) == simulation_result_to_dict(streamed)


def test_default_trace_parser_does_not_enter_implicit_streaming(monkeypatch, tmp_path):
    path = tmp_path / "legacy.json"
    _write_schema2_stream_trace(path)

    def fail_if_called(_path):
        raise AssertionError("legacy parsing unexpectedly entered streaming path")

    monkeypatch.setattr(implicit_trace_stream, "open_strict_stream", fail_if_called)
    _parse_schema2_stream_trace(path)


@pytest.mark.parametrize("payload", [
    '{"events":[{"event_type":"arrival"}',
    '{"events":[],"events":[]}',
    '{"events":[{"event_type":"arrival","event_type":"arrival"}]}',
    '{"events":{}}',
    '{"events":[{"event_type":}]}',
    '{"events":[{"event_type":"arrival"} {"event_type":"arrival"}]}',
    '{"events":[{"event_type":"arrival"}]} trailing',
    '{"events":[{"note":"unterminated}]}',
    '{"events":[]}',
    '{"events":[1]}',
    '{"events":[{"x":"\\q"}]}',
    '{"events":[{"x":{"a":1,"a":2}}]}',
    '{"events":[{"x":01}]}',
    '{"events":[{"x":truth}]}',
    '{"events":[{"x":1},]}',
    '{"events":[{"x":1}',
    '{"events":[{"x":1]}}',
    '{}',
])
def test_implicit_stream_reader_rejects_malformed_or_unsafe_json(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        metadata, events = implicit_trace_stream.open_strict_stream(path)
        list(events)


def test_implicit_stream_reader_handles_nested_and_escaped_events_text(tmp_path):
    path = tmp_path / "nested.json"
    path.write_text(
        '{"note":"escaped \\\"events\\\"",'
        '"nested":{"events":[1]},'
        '"events":[{"event_type":"arrival","run_generation":1,"time":0}]}',
        encoding="utf-8",
    )
    metadata, events = implicit_trace_stream.open_strict_stream(path)
    assert metadata["nested"] == {"events": [1]}
    assert list(events) == [{"event_type": "arrival", "run_generation": 1, "time": 0}]


def test_implicit_stream_reader_skips_metadata_pass_events_and_decodes_once(
    monkeypatch, tmp_path,
):
    path = tmp_path / "decode-count.json"
    _write_schema2_stream_trace(path)
    original = implicit_trace_stream._decode_event
    calls = []

    def counted(raw):
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(implicit_trace_stream, "_decode_event", counted)
    metadata, events = implicit_trace_stream.open_strict_stream(path)
    assert metadata["trace_schema_version"] == 2
    assert calls == []
    assert len(list(events)) == 3
    assert len(calls) == 3


@pytest.mark.parametrize("document", [
    '{"events":[{"event_type":"arrival"}],"after":1}',
    '{"before":1,"events":[{"event_type":"arrival"}],"after":1}',
    '{"before":1,"after":1,"events":[{"event_type":"arrival"}]}',
])
def test_implicit_stream_reader_does_not_depend_on_events_key_order(tmp_path, document):
    path = tmp_path / "events-order.json"
    path.write_text(document, encoding="utf-8")
    metadata, events = implicit_trace_stream.open_strict_stream(path)
    assert (metadata.get("before") == 1) if "before" in metadata else True
    assert list(events) == [{"event_type": "arrival"}]


def test_implicit_streaming_runner_rejects_remaining_constrained_before_executor(
    tmp_path, monkeypatch,
):
    output = tmp_path / "guard"
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: SimpleNamespace(
        result=SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
            metrics={}, simulation_completed=True,
        ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
        retained_trace_path=None,
    ))
    assert scheduler_runner.main(_scheduler_runner_args(
        output, priority_policy="RM",
    )) == 0
    (output / "results.jsonl").unlink()
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("simulation must not start")
    ))
    with pytest.raises(
        SystemExit,
        match="all remaining requests to be implicit",
    ):
        scheduler_runner.main(_scheduler_runner_args(
            output, priority_policy="RM", resume=True,
        ) + ["--implicit-streaming-parse"])


def test_implicit_streaming_runner_passes_only_implicit_pending_jobs(
    tmp_path, monkeypatch,
):
    calls = []

    def run_simulation(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    output = tmp_path / "implicit-resume"
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(
        output, priority_policy="RM",
    )) == 0
    results_path = output / "results.jsonl"
    results = [json.loads(line) for line in results_path.read_text().splitlines()]
    results_path.write_text(
        "".join(json.dumps(row) + "\n" for row in results
                if row["deadline_mode"] == "constrained"),
        encoding="utf-8",
    )
    calls.clear()
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(
        output, priority_policy="RM", resume=True,
    ) + ["--implicit-streaming-parse"]) == 0
    assert calls
    assert all(call["implicit_streaming_parse"] is True for call in calls)


def test_implicit_streaming_runner_rejects_v7_and_dm(tmp_path):
    with pytest.raises(SystemExit, match="requires v6 RM resume"):
        scheduler_runner.main([
            "--output", str(tmp_path / "v7"), "--seed", "1",
            "--campaign", experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN,
            "--resume", "--implicit-streaming-parse",
        ])
    with pytest.raises(SystemExit, match="requires v6 RM resume"):
        scheduler_runner.main(_scheduler_runner_args(
            tmp_path / "dm", priority_policy="DM", resume=True,
        ) + ["--implicit-streaming-parse"])


def test_parse_concurrency_can_change_on_v6_resume_and_history_is_append_only(
    tmp_path, monkeypatch,
):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    output = tmp_path / "parse-concurrency"
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(
        output, workers=30, prepare_workers=30, parse_concurrency=30,
    )) == 0
    original = json.loads((output / "run_config.json").read_text())
    original_identity = original["run_identity"]

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(
        output, resume=True, workers=30, prepare_workers=30, parse_concurrency=8,
    )) == 0
    config = json.loads((output / "run_config.json").read_text())
    assert config["workers"] == 30
    assert config["parse_concurrency"] == 30
    assert config["execution"]["workers"] == 30
    assert config["execution"]["parse_concurrency"] == 30
    assert config["run_identity"] == original_identity
    assert len(config["execution"]["resume_history"]) == 1
    first_resume = config["execution"]["resume_history"][0]
    assert first_resume["workers"] == 30
    assert first_resume["prepare_workers"] == 30
    assert first_resume["parse_concurrency"] == 8
    assert first_resume["completed_result_count_at_resume_start"] == 1
    assert first_resume["remaining_request_count_at_resume_start"] == 0
    assert first_resume["stored_run_identity"] == original_identity
    assert first_resume["timestamp_utc"].endswith("Z")

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(
        output, resume=True, workers=30, prepare_workers=30, parse_concurrency=6,
    )) == 0
    history = json.loads((output / "run_config.json").read_text())[
        "execution"]["resume_history"]
    assert [row["parse_concurrency"] for row in history] == [8, 6]
    assert all(row["stored_run_identity"] == original_identity for row in history)


def test_resume_comparison_allows_only_explicit_runtime_fields():
    stored = _v6_fixture_config("RM")
    requested = json.loads(json.dumps(stored))
    requested["workers"] = 30
    requested["keep_traces"] = True
    requested["parse_concurrency"] = 8
    requested["execution"] = {
        "workers": 30, "prepare_workers": 30, "parse_concurrency": 8,
        "keep_traces": True,
    }
    assert scheduler_runner._resume_configs_match(stored, requested)
    for field in ("experiment", "domain", "seed", "cells", "priority_policy", "simulation_horizon_ms"):
        changed = json.loads(json.dumps(requested))
        changed[field] = "changed"
        assert not scheduler_runner._resume_configs_match(stored, changed)


def test_resume_configuration_failure_does_not_append_history(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    output = tmp_path / "invalid-resume-history"
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 0
    before = json.loads((output / "run_config.json").read_text())
    with pytest.raises(SystemExit, match="resume configuration mismatch"):
        scheduler_runner.main(_scheduler_runner_args(
            output, resume=True, priority_policy="RM",
        ))
    after = json.loads((output / "run_config.json").read_text())
    assert "resume_history" not in before["execution"]
    assert "resume_history" not in after["execution"]


def test_attempt_history_is_indexed_once_and_allocation_uses_directory_state(tmp_path, monkeypatch):
    history = [
        {"request_id": "request-a", "attempt_index": 1},
        {"request_id": "request-a", "attempt_index": 2},
        {"request_id": "request-b", "attempt_index": 4},
    ]
    indexed = scheduler_runner._index_attempt_history(history)
    assert indexed == {"request-a": {1, 2}, "request-b": {4}}

    request_root = tmp_path / "simulations" / "request-a"
    (request_root / "attempt_0001").mkdir(parents=True)
    (request_root / "attempt_0002").mkdir()
    attempt_index, attempt_root = scheduler_runner._next_attempt_root(
        tmp_path, "request-a", indexed,
    )
    assert attempt_index == 3
    assert attempt_root.name == "attempt_0003"
    assert (request_root / "attempt_0001").is_dir()
    assert (request_root / "attempt_0002").is_dir()

    with pytest.raises(ValueError):
        scheduler_runner._index_attempt_history([{"request_id": "request-a"}])
    with pytest.raises(ValueError):
        scheduler_runner._index_attempt_history([
            {"request_id": "request-a", "attempt_index": "1"},
        ])


def test_attempt_history_preindex_is_not_rebuilt_for_each_pending_request(
    tmp_path, monkeypatch,
):
    calls = []
    original = scheduler_runner._index_attempt_history

    def counting_index(rows):
        calls.append(rows)
        return original(rows)

    monkeypatch.setattr(scheduler_runner, "_index_attempt_history", counting_index)
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    assert scheduler_runner.main(
        _scheduler_runner_args(tmp_path / "indexed-attempts")
    ) == 2
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def _run_real_process_group_abort_case(tmp_path, *, exit_leader, ignore_sigterm):
    executor = None
    worker_processes = ()
    worker_process_groups = set()
    child_pid = None
    try:
        context = multiprocessing.get_context("fork")
        parse_semaphore = context.Semaphore(1)
        executor = scheduler_runner.ProcessPoolExecutor(
            max_workers=1,
            mp_context=context,
            initializer=scheduler_runner._initialize_simulation_worker,
            initargs=(parse_semaphore,),
        )
        child_pid_path = tmp_path / "child.pid"
        child_ready_path = tmp_path / "child.ready"
        leader_release_path = tmp_path / "release-leader"
        future = executor.submit(
            _real_process_group_worker,
            str(child_pid_path), str(child_ready_path), ignore_sigterm, exit_leader,
            str(leader_release_path),
        )
        worker_processes = tuple(executor._processes.values())
        worker_process_groups = scheduler_runner._capture_worker_process_groups(
            executor
        )
        child_pid = _wait_for_test_pid(child_pid_path)
        _wait_for_test_file(child_ready_path)
        assert _test_pid_is_alive(child_pid)
        if exit_leader:
            leader_release_path.write_text("release", encoding="utf-8")
            assert _wait_for_test_process_exit(worker_processes[0])
            assert not worker_processes[0].is_alive()
            assert _test_pid_is_alive(child_pid)
            assert os.getpgid(child_pid) == next(iter(worker_process_groups))
        started = time.monotonic()
        assert scheduler_runner._abort_executor(
            executor, {future}, worker_process_groups,
        )
        assert time.monotonic() - started <= (
            scheduler_runner._FAILURE_CLEANUP_TIMEOUT_SECONDS + 0.5
        )
        assert all(not process.is_alive() for process in worker_processes)
        assert _wait_for_test_pid_exit(
            child_pid, scheduler_runner._FAILURE_CLEANUP_TIMEOUT_SECONDS,
        )
    finally:
        if executor is not None:
            if exit_leader:
                leader_release_path.write_text("release", encoding="utf-8")
            if not worker_process_groups and child_pid is not None:
                try:
                    child_group = os.getpgid(child_pid)
                    if scheduler_runner._validate_worker_process_groups({child_group}):
                        worker_process_groups = {child_group}
                except ProcessLookupError:
                    pass
            if worker_process_groups:
                scheduler_runner._abort_executor(
                    executor, (), worker_process_groups,
                )
            else:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    executor.shutdown(wait=False)
                for process in worker_processes:
                    if process.is_alive():
                        process.terminate()
                        process.join(1.0)
        if child_pid is not None and _test_pid_is_alive(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_for_test_pid_exit(child_pid, 1.0)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_real_worker_and_simulator_child_are_cleaned_on_abort(tmp_path):
    _run_real_process_group_abort_case(
        tmp_path, exit_leader=False, ignore_sigterm=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_orphaned_simulator_child_is_cleaned_after_worker_leader_exits(tmp_path):
    _run_real_process_group_abort_case(
        tmp_path, exit_leader=True, ignore_sigterm=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_sigterm_ignoring_simulator_child_is_escalated_to_sigkill(tmp_path):
    _run_real_process_group_abort_case(
        tmp_path, exit_leader=False, ignore_sigterm=True,
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_process_group_cleanup_rejects_runner_process_group():
    assert not scheduler_runner._validate_worker_process_groups({os.getpgrp()})


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_real_broken_pool_with_queued_work_exits_within_hard_deadline(tmp_path):
    state_path = tmp_path / "probe-state.json"
    probe = multiprocessing.Process(target=_broken_pool_probe, args=(str(state_path),))
    probe.start()
    try:
        probe.join(10.0)
        if probe.is_alive():
            _cleanup_broken_pool_probe(state_path)
            probe.terminate()
            probe.join(1.0)
        assert not probe.is_alive(), "broken pool probe exceeded hard timeout"
        assert probe.exitcode == 2
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["stage"] == "abort_completed"
        assert state["cleanup_elapsed"] <= 2.5
        assert any(
            diagnostic["exitcode"] == 17
            for diagnostic in state["worker_diagnostics"]
        )
        for diagnostic in state["worker_diagnostics"]:
            if isinstance(diagnostic["exitcode"], int) and diagnostic["exitcode"] < 0:
                assert diagnostic["signal_name"] == signal.Signals(
                    -diagnostic["exitcode"]
                ).name
        assert all(not _test_pid_is_alive(pid) for pid in state["worker_pids"])
        assert not _test_pid_is_alive(state["child_pid"])
        assert all(
            scheduler_runner._process_group_state(group_id) is False
            for group_id in state["worker_process_groups"]
        )
    finally:
        _cleanup_broken_pool_probe(state_path)
        if probe.is_alive():
            probe.terminate()
            probe.join(1.0)


def test_broken_process_pool_is_persisted_as_technical_attempt_and_shutdown_is_bounded(
    tmp_path, monkeypatch,
):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    shutdown_calls = []

    class _BrokenExecutor:
        def __init__(self, *args, **kwargs):
            self._processes = {}

        def submit(self, function, job):
            future = Future()
            future.set_exception(BrokenProcessPool("worker exited"))
            return future

        def shutdown(self, **kwargs):
            shutdown_calls.append(kwargs)

    monkeypatch.setattr(scheduler_runner, "ProcessPoolExecutor", _BrokenExecutor)
    output = tmp_path / "broken-pool"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 2
    attempts = [
        json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()
    ]
    assert len(attempts) == 1
    assert attempts[0]["simulation_status"] == "TECHNICAL_FAILURE"
    assert "BrokenProcessPool" in attempts[0]["technical_error"]
    assert not (output / "results.jsonl").exists()
    assert shutdown_calls == [{"wait": False, "cancel_futures": True}]


def test_worker_diagnostics_name_negative_exit_signals():
    class _Process:
        pid = 12345
        exitcode = -signal.SIGKILL

    class _Executor:
        _processes = {12345: _Process()}

    assert scheduler_runner._worker_diagnostics(_Executor()) == [{
        "pid": 12345, "exitcode": -signal.SIGKILL, "signal_name": "SIGKILL",
    }]


def test_submit_broken_process_pool_records_only_current_job(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    base_request_rows = scheduler_runner.experiment.request_rows

    def three_request_rows(*args, **kwargs):
        row = base_request_rows(*args, **kwargs)[0]
        return [dict(row, request_id=f"submit-request-{index}", generation_index=index)
                for index in range(3)]

    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows", three_request_rows,
    )
    submitted_futures = []
    shutdown_calls = []

    class _TrackedFuture(Future):
        def __init__(self):
            super().__init__()
            self.cancel_calls = 0

        def cancel(self):
            self.cancel_calls += 1
            return super().cancel()

    class _SubmitBrokenExecutor:
        def __init__(self, *args, **kwargs):
            self._processes = {}

        def submit(self, function, job):
            if len(submitted_futures) == 1:
                raise BrokenProcessPool("executor broke while submitting")
            future = _TrackedFuture()
            submitted_futures.append((job, future))
            return future

        def shutdown(self, **kwargs):
            shutdown_calls.append(kwargs)

    monkeypatch.setattr(
        scheduler_runner, "ProcessPoolExecutor", _SubmitBrokenExecutor,
    )
    output = tmp_path / "submit-broken"
    assert scheduler_runner.main(_scheduler_runner_args(
        output, samples_per_cell=3,
    )) == 2
    attempts = [
        json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()
    ]
    assert [row["request_id"] for row in attempts] == ["submit-request-1"]
    assert not (output / "results.jsonl").exists()
    assert submitted_futures[0][1].cancel_calls == 1
    assert shutdown_calls == [{"wait": False, "cancel_futures": True}]


def test_unattributed_executor_exception_does_not_batch_mark_pending_jobs(
    tmp_path, monkeypatch, capsys,
):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    base_request_rows = scheduler_runner.experiment.request_rows

    def three_request_rows(*args, **kwargs):
        row = base_request_rows(*args, **kwargs)[0]
        return [dict(row, request_id=f"unattributed-request-{index}", generation_index=index)
                for index in range(3)]

    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows", three_request_rows,
    )

    def fail_as_completed(_future_to_job):
        raise RuntimeError("executor infrastructure failed")

    monkeypatch.setattr(scheduler_runner, "as_completed", fail_as_completed)
    output = tmp_path / "unattributed-exception"
    assert scheduler_runner.main(_scheduler_runner_args(
        output, samples_per_cell=3,
    )) == 2
    assert not (output / "attempts.jsonl").exists()
    assert not (output / "results.jsonl").exists()
    assert "runner-level technical failure" in capsys.readouterr().err


def test_future_broken_process_pool_only_records_its_request_among_three_pending(
    tmp_path, monkeypatch,
):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    base_request_rows = scheduler_runner.experiment.request_rows

    def three_request_rows(*args, **kwargs):
        row = base_request_rows(*args, **kwargs)[0]
        return [dict(row, request_id=f"future-request-{index}", generation_index=index)
                for index in range(3)]

    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows", three_request_rows,
    )
    futures_by_request = {}
    shutdown_calls = []

    class _FutureBrokenExecutor:
        def __init__(self, *args, **kwargs):
            self._processes = {}

        def submit(self, function, job):
            future = Future()
            request_id = job["request_id"]
            if request_id == "future-request-1":
                future.set_exception(BrokenProcessPool("worker exited"))
            else:
                future.set_result(function(job))
            futures_by_request[request_id] = future
            return future

        def shutdown(self, **kwargs):
            shutdown_calls.append(kwargs)

    def broken_first(future_to_job):
        return sorted(
            future_to_job,
            key=lambda future: future_to_job[future]["request_id"] != "future-request-1",
        )

    monkeypatch.setattr(
        scheduler_runner, "ProcessPoolExecutor", _FutureBrokenExecutor,
    )
    monkeypatch.setattr(scheduler_runner, "as_completed", broken_first)
    output = tmp_path / "future-broken"
    assert scheduler_runner.main(_scheduler_runner_args(
        output, samples_per_cell=3,
    )) == 2
    attempts = [
        json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()
    ]
    assert len(attempts) == 1
    assert attempts[0]["request_id"] == "future-request-1"
    assert not (output / "results.jsonl").exists()
    assert shutdown_calls == [{"wait": False, "cancel_futures": True}]


def test_normal_completion_waits_for_all_futures(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    shutdown_calls = []

    class _WaitingExecutor:
        def __init__(self, *args, **kwargs):
            self._processes = {}

        def submit(self, function, job):
            future = Future()
            future.set_result(function(job))
            return future

        def shutdown(self, **kwargs):
            shutdown_calls.append(kwargs)

    monkeypatch.setattr(scheduler_runner, "ProcessPoolExecutor", _WaitingExecutor)
    output = tmp_path / "normal-wait"
    assert scheduler_runner.main(_scheduler_runner_args(output)) == 0
    assert shutdown_calls == [{"wait": True}]


def test_runner_bounds_in_flight_simulation_futures(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    base_request_rows = scheduler_runner.experiment.request_rows

    def bounded_request_rows(*args, **kwargs):
        row = base_request_rows(*args, **kwargs)[0]
        return [
            dict(
                row, request_id=f"bounded-request-{index}",
                generation_index=index,
            )
            for index in range(137)
        ]

    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows", bounded_request_rows,
    )
    submitted_request_ids = []
    observed_in_flight_sizes = []

    class _TrackedExecutor:
        def __init__(self, *args, **kwargs):
            self._processes = {}

        def submit(self, function, job):
            submitted_request_ids.append(job["request_id"])
            future = Future()
            future.set_result(function(job))
            return future

        def shutdown(self, **kwargs):
            pass

    def observe_as_completed(futures):
        futures = tuple(futures)
        observed_in_flight_sizes.append(len(futures))
        return iter(futures)

    monkeypatch.setattr(scheduler_runner, "ProcessPoolExecutor", _TrackedExecutor)
    monkeypatch.setattr(scheduler_runner, "as_completed", observe_as_completed)
    output = tmp_path / "bounded-in-flight"
    assert scheduler_runner.main(_scheduler_runner_args(
        output, samples_per_cell=137, workers=30,
    )) == 0
    assert len(submitted_request_ids) == 137
    assert max(observed_in_flight_sizes) == 60
    assert all(size <= 60 for size in observed_in_flight_sizes)
    assert scheduler_runner.simulation_in_flight_limit(30) == 60


def test_runner_persists_explicit_figure_slices_and_infers_unique_defaults(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    cells = "0.1:3/7,0.2:3/7,0.4:2/5,0.4:1/2"
    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    explicit = tmp_path / "explicit-slices"
    assert scheduler_runner.main(_scheduler_runner_args(
        explicit, cells=cells, fixed_ue="3/7", fixed_uc="2/5",
    )) == 0
    config = json.loads((explicit / "run_config.json").read_text())
    assert config["figure_slices"]["uc_scans"][0]["fixed_value"] == "3/7"
    assert config["figure_slices"]["ue_scans"][0]["fixed_value"] == "2/5"

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    inferred = tmp_path / "inferred-slices"
    assert scheduler_runner.main(_scheduler_runner_args(
        inferred, cells=cells,
    )) == 0
    inferred_config = json.loads((inferred / "run_config.json").read_text())
    assert inferred_config["figure_slices"] == config["figure_slices"]


@pytest.mark.parametrize("option,value", [
    ("fixed_ue", "4/5"), ("fixed_uc", "3/5"),
])
def test_runner_rejects_figure_slice_absent_from_cells(tmp_path, monkeypatch, option, value):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    kwargs = {option: value}
    with pytest.raises(SystemExit, match="must be unique values"):
        scheduler_runner.main(_scheduler_runner_args(
            tmp_path / option, cells="0.1:3/7,0.2:3/7,0.4:2/5,0.4:1/2",
            **kwargs,
        ))


@pytest.mark.parametrize("value", ["0", "6/5", "not-a-fraction"])
def test_runner_rejects_invalid_figure_slice_fraction(tmp_path, monkeypatch, value):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: None)
    with pytest.raises(SystemExit):
        scheduler_runner.main(_scheduler_runner_args(
            tmp_path / value.replace("/", "-"), fixed_ue=value,
        ))


def test_runner_binds_figure_slices_to_resume_configuration(tmp_path, monkeypatch):
    def run_simulation(**kwargs):
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    cells = "0.1:3/7,0.2:3/7,0.3:4/7,0.4:2/5,0.4:1/2"
    output = tmp_path / "resume-slices"
    assert scheduler_runner.main(_scheduler_runner_args(
        output, cells=cells, fixed_ue="3/7", fixed_uc="2/5",
    )) == 0
    with pytest.raises(SystemExit, match="resume configuration mismatch"):
        scheduler_runner.main(_scheduler_runner_args(
            output, resume=True, cells=cells, fixed_ue="4/7", fixed_uc="2/5",
        ))


def test_runner_rejects_legacy_cells_before_writes(tmp_path, monkeypatch):
    output = tmp_path / "legacy-cells"
    materialized = []
    monkeypatch.setattr(
        scheduler_runner.experiment, "materialize_tasksets",
        lambda *args, **kwargs: materialized.append(True),
    )
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: materialized.append(True),
    )
    args = [
        "--output", str(output), "--seed", "1", "--workers", "1",
        "--cells", "1/5:1/5", "--schedulers", "ASAP-BLOCK",
    ]
    with pytest.raises(SystemExit, match="--cells is a legacy write path") as exc_info:
        scheduler_runner.main(args)
    assert exc_info.value.code != 0
    assert materialized == []
    assert not output.exists()


def test_runner_rejects_cells_even_with_structured_options(tmp_path):
    args = scheduler_runner.make_parser().parse_args([
        "--output", str(tmp_path), "--seed", "1", "--cells", "1/5:1/5",
        "--uc-scan-values", "1/5,2/5",
    ])
    with pytest.raises(SystemExit, match="--cells is a legacy write path"):
        scheduler_runner._resolve_grid(args)


def test_completion_order_persists_early_and_canonicalizes_final_results(tmp_path, monkeypatch):
    requests = [
        {
            "request_id": "request-1", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 0, "seed": 710213, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
        {
            "request_id": "request-2", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 1, "seed": 710214, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
    ]
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: SimpleNamespace(
        result=SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
            metrics={}, simulation_completed=True,
        ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
        retained_trace_path=None,
    ))
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: (
            [dict(row, experiment=experiment.V6_EXPERIMENT, domain=experiment.V6_DOMAIN, priority_policy="DM", deadline_mode="constrained") for row in requests]
            if kwargs.get("deadline_mode") == "constrained" else []
        ),
    )
    def reverse_as_completed(future_to_job):
        futures = list(reversed(list(future_to_job)))
        for future in futures:
            yield future
            assert future not in future_to_job
        assert not future_to_job

    monkeypatch.setattr(scheduler_runner, "as_completed", reverse_as_completed)
    output = tmp_path / "completion-order"
    results_path = output / "results.jsonl"
    appended_ids = []
    original_append = scheduler_runner._append_jsonl

    def record_append(path, row):
        if path == results_path:
            appended_ids.append(row["request_id"])
        return original_append(path, row)

    monkeypatch.setattr(scheduler_runner, "_append_jsonl", record_append)
    assert scheduler_runner.main(_scheduler_runner_args(output, samples_per_cell=2)) == 0
    assert appended_ids == ["request-2", "request-1"]
    final_ids = [
        json.loads(line)["request_id"]
        for line in results_path.read_text().splitlines()
    ]
    assert final_ids == ["request-1", "request-2"]


def test_progress_is_periodic_and_resume_relative():
    new_run = [completed for completed in range(1, 13) if scheduler_runner._progress_due(
        completed=completed, completed_at_start=0, total=12, interval=5,
    )]
    resumed_run = [completed for completed in range(8, 20) if scheduler_runner._progress_due(
        completed=completed, completed_at_start=7, total=19, interval=5,
    )]
    assert new_run == [5, 10, 12]
    assert resumed_run == [12, 17, 19]


def test_progress_uses_outstanding_request_metric(capsys):
    scheduler_runner._print_progress(
        completed=5, total=12, outstanding_requests=7, started=0.0,
        completed_at_start=0, parse_concurrency=1,
    )
    output = capsys.readouterr().out
    assert "outstanding_requests=7" in output
    assert "active_workers" not in output


def test_persisted_metrics_drop_only_battery_trajectory_without_mutation():
    metrics = {
        "battery_trajectory": [{"time": 0, "energy_j": "1"}],
        "missed_jobs": 3,
        "energy_blocked_ticks": 7,
        "battery_minimum_j": "1/2",
        "battery_maximum_j": "3/2",
    }
    original = dict(metrics)
    persisted = scheduler_runner._persisted_metrics(metrics)
    assert "battery_trajectory" not in persisted
    assert persisted == {
        "missed_jobs": 3,
        "energy_blocked_ticks": 7,
        "battery_minimum_j": "1/2",
        "battery_maximum_j": "3/2",
    }
    assert metrics == original
    assert "battery_trajectory" in metrics
    assert persisted is not metrics


def test_completed_results_survive_later_technical_failure_and_resume(tmp_path, monkeypatch):
    requests = [
        {
            "request_id": "request-1", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 0, "seed": 710213, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
        {
            "request_id": "request-2", "taskset_id": "taskset-0",
            "taskset_hash": "hash-0", "target_uc": "1/10",
            "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
            "generation_index": 1, "seed": 710214, "scheduler": "ASAP-BLOCK",
            "scheduler_cli": "gpfp_asap_block", "horizon_ms": 20,
            **_harvest_model_fields(),
        },
    ]
    fail_second = {"value": True}

    def run_simulation(**kwargs):
        if kwargs["simulation_id_value"] == "request-2" and fail_second["value"]:
            raise RuntimeError("synthetic technical failure")
        return SimpleNamespace(
            result=SimpleNamespace(
                status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
                metrics={}, simulation_completed=True,
            ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
            retained_trace_path=None,
        )

    _patch_scheduler_runner(monkeypatch, tmp_path, run_simulation)
    monkeypatch.setattr(
        scheduler_runner.experiment, "request_rows",
        lambda *args, **kwargs: (
            [dict(row, experiment=experiment.V6_EXPERIMENT, domain=experiment.V6_DOMAIN, priority_policy="DM", deadline_mode="constrained") for row in requests]
            if kwargs.get("deadline_mode") == "constrained" else []
        ),
    )
    monkeypatch.setattr(
        scheduler_runner, "as_completed",
        lambda future_to_job: sorted(
            future_to_job, key=lambda future: future_to_job[future]["request_id"]
        ),
    )
    output = tmp_path / "partial-resume"
    assert scheduler_runner.main(_scheduler_runner_args(output, samples_per_cell=2)) == 2
    results_path = output / "results.jsonl"
    assert [
        json.loads(line)["request_id"] for line in results_path.read_text().splitlines()
    ] == ["request-1"]

    fail_second["value"] = False
    assert scheduler_runner.main(_scheduler_runner_args(output, resume=True, samples_per_cell=2)) == 0
    assert [
        json.loads(line)["request_id"] for line in results_path.read_text().splitlines()
    ] == ["request-1", "request-2"]


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
        "experiment": experiment.V6_EXPERIMENT, "domain": experiment.V6_DOMAIN,
        "priority_policy": "DM", "deadline_mode": "constrained",
        "target_uc": "1/10", "actual_uc": "1/10", "target_ue": "2/5", "eta": "5/2",
        "scheduler": "ASAP-BLOCK", "simulation_status": "SIM_PASS_OBSERVED",
        "technical_error": None, **_harvest_model_fields(), "energy": {
            "eta": "5/2", "target_ue": "2/5", "actual_ue": "2/5",
            "actual_ue_abs_error": "0", "actual_ue_rel_error": "0",
            **_harvest_model_fields(),
        },
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
        "experiment": experiment.V6_EXPERIMENT, "domain": experiment.V6_DOMAIN,
        "priority_policy": "DM", "deadline_mode": "constrained",
        "simulation_status": "SIM_INTERNAL_ERROR", "technical_error": "old failure",
        **_harvest_model_fields(),
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
        "experiment": experiment.V6_EXPERIMENT, "domain": experiment.V6_DOMAIN,
        "priority_policy": "DM", "deadline_mode": "constrained",
        "technical_error": None, **_harvest_model_fields(),
    }
    (output / "results.jsonl").write_text(
        json.dumps(terminal) + "\n" + json.dumps(terminal) + "\n", encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="duplicate"):
        scheduler_runner.main(_scheduler_runner_args(output, resume=True))


def test_v4_default_scan_contract_is_51_ordered_cells():
    profile = experiment.normalize_scan_profile()
    contract = experiment.build_scan_contract(profile)
    cells = experiment.build_scan_cells(
        profile["uc_scan_values"], profile["ue_scan_values"],
        profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
    )
    assert len(cells) == 51
    assert len(set(cells)) == 51
    assert len(profile["uc_scan_values"]) == 10
    assert len(profile["ue_scan_values"]) == 10
    assert len(profile["uc_figure_fixed_ues"]) == 3
    assert len(profile["ue_figure_fixed_ucs"]) == 3
    assert cells[:3] == (
        (Fraction(1, 10), Fraction(3, 10)),
        (Fraction(1, 5), Fraction(3, 10)),
        (Fraction(3, 10), Fraction(3, 10)),
    )
    assert (Fraction(0), Fraction(1, 10)) not in cells
    assert contract["unique_cell_count"] == 51
    assert contract["axis_ticks"] == [
        "0", "1/10", "1/5", "3/10", "2/5", "1/2", "3/5",
        "7/10", "4/5", "9/10", "1",
    ]


def test_v4_composite_plot_layout_and_csv_contract(tmp_path):
    schedulers = list(experiment.ALL_SCHEDULERS)

    def rows_for(slice_config):
        rows = []
        for x_value in slice_config["x_values"]:
            for scheduler in schedulers:
                rows.append({
                    "scheduler": scheduler, slice_config["x_key"]: x_value,
                    "wholepass_ratio": 1.0, "ci95_low": 1.0, "ci95_high": 1.0,
                    "n_wholepass": 1, "n_total": 1,
                })
        return rows

    profile = experiment.normalize_scan_profile(
        uc_scan_values="1/5,2/5,3/5,4/5,1",
        ue_scan_values="1/5,2/5,3/5,4/5,1",
        uc_figure_fixed_ues="1/5,4/5", uc_figure_labels="abundant,tight",
        ue_figure_fixed_ucs="2/5,3/5", ue_figure_labels="low_compute,high_compute",
        axis_display_min="0", axis_display_max="1", axis_tick_step="1/5",
    )
    slices = experiment.build_v4_figure_slices(profile)
    uc_rows = [(item, rows_for(item)) for item in slices["uc_scans"]]
    assert len(uc_rows) == 2
    plot_composite_scan(
        uc_rows, tmp_path, "custom-2x3.png", "target_uc", schedulers, "U_C",
        "custom", axis_min="0", axis_max="1", axis_ticks=profile["axis_ticks"],
    )
    assert (tmp_path / "custom-2x3.png").is_file()

    default = experiment.normalize_scan_profile()
    default_slices = experiment.build_v4_figure_slices(default)
    default_rows = [(item, rows_for(item)) for item in default_slices["uc_scans"]]
    plot_composite_scan(
        default_rows, tmp_path, "default-3x3.png", "target_uc", schedulers, "U_C",
        "default", axis_min="0", axis_max="1", axis_ticks=default["axis_ticks"],
    )
    assert (tmp_path / "default-3x3.png").is_file()


def test_analyzer_rejects_empty_requests_with_explainable_error(tmp_path):
    config = {
        "cells": [["1/5", "1/5"]], "samples_per_cell": 1,
        "schedulers": ["ASAP-BLOCK"], "priority_policy": "RM",
        "processors": 4, "util_tolerance_total": "1/100",
        "use_real_solar_data": False, **experiment.HARVEST_MODEL_IDENTITY,
        "figure_slices": {
            "uc_scan": {"x_key": "target_uc", "fixed_key": "target_ue", "fixed_value": "1/5"},
            "ue_scan": {"x_key": "target_ue", "fixed_key": "target_uc", "fixed_value": "1/5"},
        },
    }
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "tasksets.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "requests.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "results.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="requests are empty"):
        analyze(tmp_path)


def test_v6_dm_analyzer_requires_shared_implicit_source(tmp_path):
    target = tmp_path / "dm"
    _write_v6_fixture(target, "DM")
    with pytest.raises(SystemExit, match="shared-implicit-run-dir"):
        analyze(target)
    assert not (target / "summary.csv").exists()
    assert not (target / "figure_scheduler_uc_slices.csv").exists()


@pytest.mark.parametrize("legacy_experiment", [
    experiment.V3_EXPERIMENT, experiment.V4_EXPERIMENT, experiment.V5_EXPERIMENT,
])
def test_historical_analyzer_rejects_shared_implicit_argument(
    tmp_path, legacy_experiment, capsys,
):
    root = tmp_path / legacy_experiment
    _write_legacy_analyzer_fixture(root, legacy_experiment)
    with pytest.raises(SystemExit) as exc_info:
        analyzer_module.main([
            "--input", str(root),
            "--shared-implicit-run-dir", str(tmp_path / "missing-shared-source"),
        ])
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    message = str(exc_info.value)
    assert "--shared-implicit-run-dir" in message
    assert "only valid for scheduler-load-cross-v6" in message
    assert "cannot be read" not in message
    assert "Traceback" not in captured.out + captured.err
    assert not list(root.glob("*.csv"))
    assert not list(root.glob("*.png"))


def test_v6_shared_implicit_source_is_read_and_marked_without_rewriting(tmp_path):
    source = tmp_path / "rm"
    target = tmp_path / "dm"
    _write_v6_fixture(source, "RM")
    _write_v6_fixture(target, "DM")
    source_before = {
        name: (source / name).read_bytes()
        for name in ("run_config.json", "tasksets.jsonl", "requests.jsonl", "results.jsonl")
    }
    assert analyze(source)["implicit_data_reused"] is False
    assert analyze(target, shared_implicit_run_dir=source)["implicit_data_reused"] is True
    assert source_before == {
        name: (source / name).read_bytes()
        for name in source_before
    }
    with (target / "figure_scheduler_uc_slices.csv").open(newline="") as handle:
        uc_rows = list(csv.DictReader(handle))
    implicit = [row for row in uc_rows if row["deadline_mode"] == "implicit"]
    assert len(implicit) == 9
    assert {row["figure_priority_policy"] for row in implicit} == {"DM"}
    assert {row["source_priority_policy"] for row in implicit} == {"RM"}
    assert {row["implicit_data_reused"] for row in implicit} == {"True"}
    assert {row["implicit_canonical_priority_policy"] for row in implicit} == {"RM"}
    assert {row["implicit_priority_equivalence"] for row in implicit} == {
        "RM_equals_DM_when_D_equals_T"
    }
    assert {row["source_run_identity"] for row in implicit} == {
        json.loads((source / "run_config.json").read_text())["run_identity"]
    }


@pytest.mark.parametrize("bad_source", ["v3", "v4", "DM", "missing"])
def test_v6_shared_implicit_source_rejects_invalid_source(tmp_path, bad_source):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_v6_fixture(source, "RM")
    _write_v6_fixture(target, "DM")
    if bad_source == "missing":
        (source / "requests.jsonl").unlink()
        match = "cannot be read"
    else:
        config = json.loads((source / "run_config.json").read_text())
        if bad_source == "v3":
            config["experiment"] = experiment.V3_EXPERIMENT
        elif bad_source == "v4":
            config["experiment"] = experiment.V4_EXPERIMENT
        else:
            config["priority_policy"] = "DM"
            config["deadline_modes"] = ["constrained"]
            config["expected_request_count"] = 9
            config["expected_taskset_count"] = 1
            config["run_identity"] = experiment.run_identity(config)
        (source / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
        match = "scheduler-load-cross-v6" if bad_source in {"v3", "v4"} else "priority policy"
    with pytest.raises(SystemExit, match=match):
        analyze(target, shared_implicit_run_dir=source)
    assert not (target / "summary.csv").exists()


def test_v6_shared_source_rejects_configuration_drift_before_output(tmp_path):
    source = tmp_path / "rm"
    target = tmp_path / "dm"
    _write_v6_fixture(source, "RM")
    _write_v6_fixture(target, "DM")
    config = json.loads((source / "run_config.json").read_text())
    config["seed"] = 999
    config["run_identity"] = experiment.run_identity(config)
    (source / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(SystemExit, match="configurations do not match"):
        analyze(target, shared_implicit_run_dir=source)
    assert not (target / "summary.csv").exists()


def test_v6_runner_dm_config_has_only_constrained_mode(tmp_path, monkeypatch):
    _patch_scheduler_runner(monkeypatch, tmp_path, lambda **kwargs: SimpleNamespace(
        result=SimpleNamespace(
            status=SimulationStatus.PASS_OBSERVED, reason="observed", jobs=(),
            metrics={}, simulation_completed=True,
        ), runtime_seconds=0.1, stdout_tail="", stderr_tail="",
        retained_trace_path=None,
    ))
    output = tmp_path / "dm-v6"
    assert scheduler_runner.main(_scheduler_runner_args(output, priority_policy="DM")) == 0
    config = json.loads((output / "run_config.json").read_text())
    assert config["experiment"] == experiment.V6_EXPERIMENT
    assert config["domain"] == experiment.V6_DOMAIN
    assert config["deadline_modes"] == ["constrained"]
    assert config["expected_request_count"] == 1


@pytest.mark.parametrize("legacy_experiment", [
    experiment.V3_EXPERIMENT, experiment.V4_EXPERIMENT, experiment.V5_EXPERIMENT,
])
def test_v6_runner_rejects_legacy_resume_identity(tmp_path, legacy_experiment):
    output = tmp_path / legacy_experiment
    output.mkdir()
    (output / "run_config.json").write_text(
        json.dumps({"experiment": legacy_experiment}), encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="cannot resume non-v6"):
        scheduler_runner.main(_scheduler_runner_args(output, resume=True))


def test_v6_rm_and_dm_implicit_aggregates_are_identical(tmp_path):
    source = tmp_path / "rm"
    target = tmp_path / "dm"
    _write_v6_fixture(source, "RM")
    _write_v6_fixture(target, "DM")
    analyze(source)
    analyze(target, shared_implicit_run_dir=source)
    def implicit_rows(path):
        with (path / "figure_scheduler_uc_slices.csv").open(newline="") as handle:
            return sorted(
                (row["scheduler"], row["x_value"], row["whole_taskset_pass_ratio"])
                for row in csv.DictReader(handle)
                if row["deadline_mode"] == "implicit"
            )
    def implicit_dmr_rows(path):
        with (path / "figure_scheduler_uc_slices_dmr.csv").open(newline="") as handle:
            return sorted(
                (row["scheduler"], row["x_value"], row["dmr"], row["dmr_ci95_low"], row["dmr_ci95_high"])
                for row in csv.DictReader(handle)
                if row["deadline_mode"] == "implicit"
            )
    assert implicit_rows(source) == implicit_rows(target)
    assert implicit_dmr_rows(source) == implicit_dmr_rows(target)


def test_v4_custom_scan_contract_and_fraction_tokens():
    profile = experiment.normalize_scan_profile(
        uc_scan_values="1/5,2/5,3/5,4/5,1",
        ue_scan_values="1/5,2/5,3/5,4/5,1",
        uc_figure_fixed_ues="1/5,4/5",
        uc_figure_labels="abundant,tight",
        ue_figure_fixed_ucs="2/5,3/5",
        ue_figure_labels="low_compute,high_compute",
        axis_display_min="0", axis_display_max="1", axis_tick_step="1/5",
    )
    cells = experiment.build_scan_cells(
        profile["uc_scan_values"], profile["ue_scan_values"],
        profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
    )
    assert len(cells) == 16
    slices = experiment.build_v4_figure_slices(profile)
    assert [item["label"] for item in slices["uc_scans"]] == ["abundant", "tight"]
    assert [item["label"] for item in slices["ue_scans"]] == ["low_compute", "high_compute"]
    assert [item["fixed_value"] for item in slices["ue_scans"]] == ["2/5", "3/5"]
    assert experiment.fraction_token("3/10") == "3of10"
    assert experiment.fraction_token("1") == "1"
    assert profile["axis_ticks"] == ["0", "1/5", "2/5", "3/5", "4/5", "1"]


def test_v4_default_request_count_is_dynamic_51_times_nine():
    profile = experiment.normalize_scan_profile()
    cells = experiment.build_scan_cells(
        profile["uc_scan_values"], profile["ue_scan_values"],
        profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
    )

    class Taskset:
        processors = 4
        actual_utilization = Fraction(1)
        taskset_index = 0
        seed = 1

        def __init__(self, uc):
            self.target_utilization = uc * self.processors
            self.taskset_id = f"t-{uc}"
            self.semantic_hash = f"h-{uc}"

    tasksets = [Taskset(uc) for uc in {
        Fraction(uc) for uc, _ue in cells
    }]
    requests = experiment.request_rows(
        tasksets, cells, experiment.ALL_SCHEDULERS, 60000,
        experiment_name=experiment.V4_EXPERIMENT,
    )
    assert len(requests) == 51 * 9
    assert {row["experiment"] for row in requests} == {experiment.V4_EXPERIMENT}


@pytest.mark.parametrize("kwargs", [
    {"uc_scan_values": "1/5,1/5"},
    {"uc_scan_values": "1/5,1/10"},
    {"uc_scan_values": "0,1/5"},
    {"ue_figure_fixed_ucs": "7/20"},
    {"axis_tick_step": "2/7"},
    {"uc_figure_labels": "unsafe label,tight"},
])
def test_v4_scan_contract_rejects_invalid_grid(kwargs):
    with pytest.raises(ValueError):
        experiment.normalize_scan_profile(**kwargs)


def test_v4_runner_rejects_cells_and_structured_grid_conflict(tmp_path):
    args = scheduler_runner.make_parser().parse_args([
        "--output", str(tmp_path), "--seed", "1", "--cells", "1/5:1/5",
        "--uc-scan-values", "1/5,2/5",
    ])
    with pytest.raises(SystemExit, match="--cells is a legacy write path"):
        scheduler_runner._resolve_grid(args)


def test_v4_runner_grid_resolution_binds_scan_configuration(tmp_path):
    args = scheduler_runner.make_parser().parse_args([
        "--output", str(tmp_path), "--seed", "1",
        "--uc-scan-values", "1/5,2/5,3/5,4/5,1",
        "--ue-scan-values", "1/5,2/5,3/5,4/5,1",
        "--uc-figure-fixed-ues", "1/5,4/5",
        "--uc-figure-labels", "abundant,tight",
        "--ue-figure-fixed-ucs", "2/5,3/5",
        "--ue-figure-labels", "low_compute,high_compute",
        "--axis-display-min", "0", "--axis-display-max", "1",
        "--axis-tick-step", "1/5",
    ])
    cells, slices, contract, is_v4 = scheduler_runner._resolve_grid(args)
    assert is_v4 is True
    assert len(cells) == contract["unique_cell_count"] == 16
    assert contract["ordered_cells"] == [[str(uc), str(ue)] for uc, ue in cells]
    assert len(slices["uc_scans"]) == 2


def test_v7_campaign_grids_are_exact_and_constrained_only():
    fixed = experiment.v7_campaign_spec(experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN)
    service = experiment.v7_campaign_spec(experiment.V7_UE_SERVICE_SCALING_CAMPAIGN)
    assert len(fixed["cells"]) == 24
    assert len(set(fixed["cells"])) == 24
    assert len(service["cells"]) == 30
    assert len(set(service["cells"])) == 30
    assert experiment.v7_deadline_modes_for_priority_policy("RM") == ("constrained",)
    assert experiment.v7_deadline_modes_for_priority_policy("DM") == ("constrained",)
    assert fixed["energy_control"] == "FIXED_ABSOLUTE_SUPPLY"
    assert service["energy_control"] == "SERVICE_ONLY_SCALING"


def test_v7_fixed_supply_is_exact_and_independent_of_demand():
    class Taskset:
        processors = 4
        task_count = 2

        def __init__(self, power):
            self.task_payload = (
                {"C": 1, "T": 10, "P": str(power)},
                {"C": 1, "T": 10, "P": "4"},
            )

    raw = (Fraction(1),) * 10
    supply = experiment.V7_FIXED_SUPPLIES["low"]
    first = experiment.fixed_supply_energy_material(
        Taskset(2), supply, raw, kappa=Fraction(10),
        reference_ue=Fraction(9, 10), energy_level="low", normalization_horizon=10,
    )
    second = experiment.fixed_supply_energy_material(
        Taskset(8), supply, raw, kappa=Fraction(10),
        reference_ue=Fraction(9, 10), energy_level="low", normalization_horizon=10,
    )
    assert first["target_supply_mean_j_per_tick"] == second["target_supply_mean_j_per_tick"] == str(supply)
    assert Fraction(first["solar_scale"]) * Fraction(first["raw_reference_mean_j_per_tick"]) == supply
    assert Fraction(second["solar_scale"]) * Fraction(second["raw_reference_mean_j_per_tick"]) == supply
    assert first["actual_ue"] != second["actual_ue"]
    assert first["battery_capacity_j"] == "60"
    assert first["initial_energy_j"] == "30"
    assert first["energy_control"] == "FIXED_ABSOLUTE_SUPPLY"
    assert first["target_ue_is_reference"] == "true"
    with pytest.raises(ValueError, match="does not match"):
        experiment.fixed_supply_energy_material(
            Taskset(2), supply + Fraction(1), raw, kappa=Fraction(10),
            reference_ue=Fraction(9, 10), energy_level="low", normalization_horizon=10,
        )


def test_v7_supply_levels_and_identity_isolation():
    assert experiment.V7_FIXED_SUPPLIES["low"] < experiment.V7_FIXED_SUPPLIES["medium"] < experiment.V7_FIXED_SUPPLIES["high"]
    assert {
        experiment.v7_energy_level(value): value
        for value in experiment.V7_REFERENCE_UES.values()
    } == experiment.V7_REFERENCE_UES
    base = {
        "experiment": experiment.V6_EXPERIMENT, "domain": experiment.V6_DOMAIN,
        "seed": 1, "cells": [["1/10", "1/5"]],
    }
    v7 = {**base, "experiment": experiment.V7_EXPERIMENT, "domain": experiment.V7_DOMAIN,
          "campaign": experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN,
          "energy_control": "FIXED_ABSOLUTE_SUPPLY"}
    service = {**v7, "campaign": experiment.V7_UE_SERVICE_SCALING_CAMPAIGN,
               "energy_control": "SERVICE_ONLY_SCALING"}
    assert experiment.run_identity(base) != experiment.run_identity(v7)
    assert experiment.run_identity(v7) != experiment.run_identity(service)


def test_v7_cli_uses_only_the_frozen_grid(tmp_path):
    for campaign, expected_count in (
        (experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN, 24),
        (experiment.V7_UE_SERVICE_SCALING_CAMPAIGN, 30),
    ):
        args = scheduler_runner.make_parser().parse_args([
            "--output", str(tmp_path), "--seed", "1", "--campaign", campaign,
        ])
        cells, _slices, contract, structured = scheduler_runner._resolve_grid(args)
        assert len(cells) == expected_count
        assert contract["unique_cell_count"] == expected_count
        assert structured is False


def test_v7_request_identity_contains_campaign_and_control():
    class Taskset:
        processors = 4
        actual_utilization = Fraction(1)
        taskset_index = 0
        seed = 1
        deadline_mode = "constrained"

        def __init__(self, uc):
            self.target_utilization = uc * self.processors
            self.taskset_id = f"taskset-{uc}"
            self.semantic_hash = f"hash-{uc}"

    for campaign in (
        experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN,
        experiment.V7_UE_SERVICE_SCALING_CAMPAIGN,
    ):
        spec = experiment.v7_campaign_spec(campaign)
        tasksets = [Taskset(uc) for uc in sorted({uc for uc, _ue in spec["cells"]})]
        rows = experiment.request_rows(
            tasksets, spec["cells"], experiment.ALL_SCHEDULERS, 60000,
            experiment_name=experiment.V7_EXPERIMENT, deadline_mode="constrained",
            campaign=campaign, energy_control=spec["energy_control"],
        )
        assert len(rows) == len(spec["cells"]) * len(experiment.ALL_SCHEDULERS)
        assert len({row["request_id"] for row in rows}) == len(rows)
        assert {row["deadline_mode"] for row in rows} == {"constrained"}
        assert {row["campaign"] for row in rows} == {campaign}
        assert {row["energy_control"] for row in rows} == {spec["energy_control"]}


def test_v7_resume_rejects_v6_output(tmp_path):
    output = tmp_path / "v7-resume"
    output.mkdir()
    (output / "run_config.json").write_text(
        json.dumps(_v6_fixture_config("RM")), encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="different campaign version"):
        scheduler_runner.main([
            "--output", str(output), "--seed", "1", "--campaign",
            experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN, "--resume",
        ])


def test_v7_freezes_task_generation_parameters_and_v6_remains_compatible():
    frozen = {
        "period_min": perf_g.PERIOD_MIN_MS,
        "period_max": perf_g.PERIOD_MAX_MS,
        "min_task_util": perf_g.MIN_TASK_UTILIZATION,
        "max_task_util": perf_g.MAX_TASK_UTILIZATION,
        "util_tolerance_total": perf_g.UTILIZATION_TOLERANCE,
    }
    for campaign in (
        experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN,
        experiment.V7_UE_SERVICE_SCALING_CAMPAIGN,
    ):
        scheduler_runner._validate_v7_generation_parameters(campaign, **frozen)
        with pytest.raises(SystemExit, match="freeze PERF-G task generation parameters"):
            scheduler_runner._validate_v7_generation_parameters(
                campaign, **{**frozen, "period_min": frozen["period_min"] + 1},
            )
        with pytest.raises(SystemExit, match="freeze PERF-G task generation parameters"):
            scheduler_runner._validate_v7_generation_parameters(
                campaign, **{
                    **frozen,
                    "min_task_util": frozen["min_task_util"] + Fraction(1, 100),
                },
            )
    scheduler_runner._validate_v7_generation_parameters(
        "v6", **{
            **frozen,
            "period_min": frozen["period_min"] + 1,
            "min_task_util": frozen["min_task_util"] + Fraction(1, 100),
        },
    )


@pytest.mark.parametrize("passed", [True, False])
def test_v6_implicit_wholepass_fast_result_is_strictly_validated(passed):
    value = _fast_result_fixture(passed=passed)
    observed = validate_fast_document(
        value,
        expected_run_id=value["run_id"],
        expected_taskset_hash=value["taskset_semantic_hash"],
        expected_scheduler=value["configured_scheduler"],
        expected_processors=4,
        expected_task_ids=value["task_ids"],
        expected_horizon=60000,
    )
    assert observed == value


def test_v6_implicit_wholepass_fast_result_rejects_duplicate_and_bad_pass(tmp_path):
    value = _fast_result_fixture()
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema":"%s","schema":"%s"}' % (FAST_SCHEMA, FAST_SCHEMA),
        encoding="utf-8",
    )
    from experiments.v9_3.implicit_wholepass_fast import validate_fast_result
    with pytest.raises(FastWholePassError, match="duplicate JSON key"):
        validate_fast_result(
            path,
            expected_run_id=value["run_id"],
            expected_taskset_hash=value["taskset_semantic_hash"],
            expected_scheduler=value["configured_scheduler"],
            expected_processors=4,
            expected_task_ids=value["task_ids"],
            expected_horizon=60000,
        )
    value["completion_reason"] = "reached_horizon"
    value["simulation_completed"] = False
    with pytest.raises(FastWholePassError, match="horizon pass result is invalid"):
        validate_fast_document(
            value,
            expected_run_id=value["run_id"],
            expected_taskset_hash=value["taskset_semantic_hash"],
            expected_scheduler=value["configured_scheduler"],
            expected_processors=4,
            expected_task_ids=value["task_ids"],
            expected_horizon=60000,
        )


def test_v6_implicit_wholepass_fast_rejects_constrained_scope(tmp_path):
    command = [
        str(Path("build/rtsim/rtsim")),
        str(tmp_path / "missing-system.yaml"),
        str(tmp_path / "missing-taskset.yaml"),
        "100",
        "--run-id", "v93-scope-h100",
        "--taskset-semantic-hash", "a" * 64,
        "--wholepass-fast-output", str(tmp_path / "fast.json"),
        "--wholepass-fast-campaign", "v6",
        "--wholepass-fast-priority-policy", "RM",
        "--wholepass-fast-deadline-mode", "constrained",
        "--wholepass-fast-mode", "hard-rt-wholepass",
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode != 0
    assert "explicit v6/RM/implicit/hard-rt-wholepass" in completed.stderr


def test_v6_implicit_wholepass_fast_python_scope_guard():
    from experiments.v9_3.simulation_engine import run_paired_simulation
    with pytest.raises(SimulationConfigurationError, match="requires v6 RM implicit"):
        run_paired_simulation(
            simulation_id_value="scope-guard",
            base_system_path=Path("missing-system.yaml"),
            run_root=Path("/tmp/partsim-scope-guard"),
            task_payload=(), taskset_hash="a" * 64, processors=4,
            exact_e0=Fraction(1),
            energy_config={"simulation_initial_battery": "1", "battery_capacity": "2"},
            simulation_config={
                "priority_policy": "RM", "deadline_mode": "constrained",
                "campaign": "v6", "wholepass_mode": "hard-rt",
            }, implicit_wholepass_fast=True,
        )


def _overlay_request_fixture(request_id, deadline_mode):
    return {
        "request_id": request_id,
        "experiment": experiment.V6_EXPERIMENT,
        "domain": experiment.V6_DOMAIN,
        "taskset_id": "fixture-taskset",
        "taskset_hash": "a" * 64,
        "scheduler": "ASAP-BLOCK",
        "scheduler_cli": "gpfp_asap_block",
        "target_ue": "1/5",
        "deadline_mode": deadline_mode,
        "horizon_ms": 60000,
    }


def _overlay_taskset_fixture():
    return SimpleNamespace(
        processors=4,
        task_payload=tuple({
            "task_id": str(index), "priority_rank": index, "C": 1,
            "D": 10, "T": 10, "P": "1", "workload": "fixture",
            "arrival_offset": 0,
        } for index in range(10)),
    )


def test_v6_fast_overlay_partitions_mixed_mode_baseline_by_canonical_mode():
    expected = {
        "constrained": {"deadline_mode": "constrained"},
        "implicit-existing": {"deadline_mode": "implicit"},
        "implicit-pending": {"deadline_mode": "implicit"},
    }
    implicit, constrained, baseline_implicit, baseline_constrained = (
        fast_overlay_runner._partition_baseline_ids(
            {"constrained", "implicit-existing"}, expected,
        )
    )
    assert implicit == {"implicit-existing", "implicit-pending"}
    assert constrained == {"constrained"}
    assert baseline_implicit == {"implicit-existing"}
    assert baseline_constrained == {"constrained"}
    assert implicit - baseline_implicit == {"implicit-pending"}

    _, _, only_constrained, constrained_rows = (
        fast_overlay_runner._partition_baseline_ids({"constrained"}, expected)
    )
    assert only_constrained == set()
    assert constrained_rows == {"constrained"}


def test_v6_fast_overlay_accepts_mixed_baseline_and_implicit_overlay(
    tmp_path, monkeypatch, capsys,
):
    formal_root = tmp_path / "formal"
    overlay_root = tmp_path / "overlay"
    formal_root.mkdir()
    overlay_root.mkdir()
    (formal_root / "run_config.json").write_text(
        json.dumps({"simulation_horizon_ms": 60000, "kappa": "10"}),
        encoding="utf-8",
    )
    expected = {
        request["request_id"]: request for request in (
            _overlay_request_fixture("constrained-fixture", "constrained"),
            _overlay_request_fixture("implicit-existing", "implicit"),
            _overlay_request_fixture("implicit-overlay", "implicit"),
            _overlay_request_fixture("implicit-pending", "implicit"),
        )
    }
    taskset = _overlay_taskset_fixture()
    service = SimpleNamespace(system_path=tmp_path / "system.yaml")
    monkeypatch.setattr(
        fast_overlay_runner, "_prepare_requests",
        lambda config, root: (list(expected.values()), expected,
                              {"fixture-taskset": taskset}, service),
    )
    monkeypatch.setattr(
        fast_overlay_runner.experiment, "construct_paired_harvest_trace",
        lambda *args: [Fraction(1)],
    )
    monkeypatch.setattr(
        fast_overlay_runner.experiment, "harvest_trace_identity",
        lambda trace: "fixture-raw-trace",
    )
    monkeypatch.setattr(
        fast_overlay_runner.experiment, "set_prepare_raw_trace",
        lambda trace: None,
    )
    monkeypatch.setattr(
        fast_overlay_runner.experiment, "energy_material",
        lambda *args, **kwargs: {},
    )
    constrained_row = {
        **expected["constrained-fixture"],
        "simulation_status": "PASS_OBSERVED",
        "technical_error": None, "taskset_pass": True,
    }
    existing_row = {
        **expected["implicit-existing"],
        "simulation_status": "PASS_OBSERVED",
        "technical_error": None, "taskset_pass": True,
    }
    (formal_root / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n"
                for row in (constrained_row, existing_row)), encoding="utf-8",
    )
    (formal_root / "attempts.jsonl").write_text("", encoding="utf-8")
    fast_result = _fast_result_fixture()
    fast_result.update({
        "run_id": "v93-implicit-overlay-h60000",
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": "gpfp_asap_block",
    })
    overlay_row = {
        **expected["implicit-overlay"],
        "fast_mode": FAST_MODE,
        "simulation_status": "PASS_OBSERVED",
        "technical_error": None,
        "taskset_pass": True,
        "fast_result": fast_result,
    }
    overlay_path = overlay_root / "implicit_wholepass_fast_results.jsonl"
    overlay_path.write_text(json.dumps(overlay_row) + "\n", encoding="utf-8")
    pending_result = _fast_result_fixture()
    pending_result.update({
        "run_id": "v93-implicit-pending-h60000",
        "taskset_semantic_hash": "a" * 64,
        "configured_scheduler": "gpfp_asap_block",
    })

    def fake_worker(job):
        return {
            "request": dict(job["request"]),
            "fast_result": pending_result,
            "runtime_seconds": 0.0,
        }

    class InlinePool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, function, job):
            future = Future()
            future.set_result(function(job))
            return future

    monkeypatch.setattr(fast_overlay_runner, "_fast_worker", fake_worker)
    monkeypatch.setattr(fast_overlay_runner, "ProcessPoolExecutor", InlinePool)
    before = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in (
            ("results", formal_root / "results.jsonl"),
            ("attempts", formal_root / "attempts.jsonl"),
        )
    }
    simulator = tmp_path / "rtsim"
    simulator.touch()

    assert fast_overlay_runner.main([
        "--formal-root", str(formal_root), "--overlay-root", str(overlay_root),
        "--simulator", str(simulator),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["expected_implicit"] == 3
    assert output["baseline_implicit"] == 1
    assert output["baseline_constrained"] == 1
    assert output["fast_overlay"] == 2
    assert output["union"] == 3
    assert before == {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in (
            ("results", formal_root / "results.jsonl"),
            ("attempts", formal_root / "attempts.jsonl"),
        )
    }


def test_v6_fast_overlay_rejects_cross_mode_and_overlap_ids():
    expected = {
        "constrained": {"deadline_mode": "constrained"},
        "implicit": {"deadline_mode": "implicit"},
    }
    with pytest.raises(ValueError, match="unknown deadline-mode"):
        fast_overlay_runner._partition_baseline_ids({"unknown"}, expected)
    with pytest.raises(ValueError, match="non-implicit"):
        fast_overlay_runner._validate_overlay_ids({"constrained"}, {"implicit"})
    with pytest.raises(ValueError, match="non-implicit"):
        fast_overlay_runner._validate_overlay_ids({"unknown"}, {"implicit"})
    with pytest.raises(ValueError, match="overlap"):
        fast_overlay_runner._validate_overlay_overlap({"implicit"}, {"implicit"})
    with pytest.raises(ValueError, match="deadline_mode"):
        fast_overlay_runner._assert_request_match(
            {"request_id": "implicit", "deadline_mode": "constrained"},
            {"request_id": "implicit", "deadline_mode": "implicit"},
        )
