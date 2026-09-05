#!/usr/bin/env python3
"""Run the paired scheduler LOAD-CROSS experiment locally."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from datetime import datetime, timezone
import json
from fractions import Fraction
import multiprocessing
from pathlib import Path
import os
import re
import signal
import shutil
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g, scheduler_load_cross as experiment
from experiments.v9_3.parallel_prepare import run_prepare_jobs, validate_workers
from experiments.v9_3 import simulation_engine
from experiments.v9_3.performance_outcome import evaluate_outcome
from experiments.v9_3.simulation_engine import SimulationStatus, run_paired_simulation


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


_RESUME_RUNTIME_CONFIG_KEYS = frozenset({
    "execution", "keep_traces", "parse_concurrency", "run_identity",
    "status", "telemetry", "workers",
})


def _resume_comparable_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return the scientific configuration used for a strict resume check."""
    return {
        key: value for key, value in config.items()
        if key not in _RESUME_RUNTIME_CONFIG_KEYS
    }


def _resume_configs_match(
    stored_config: dict[str, Any], requested_config: dict[str, Any],
) -> bool:
    """Compare every scientific field while allowing only runtime resources to vary."""
    return _resume_comparable_config(stored_config) == _resume_comparable_config(
        requested_config
    )


def effective_concurrent_parsers(workers: int, parse_concurrency: int) -> int:
    """Return the maximum number of parsers that can actually run concurrently."""
    if workers < 1 or parse_concurrency < 1:
        raise ValueError("workers and parse_concurrency must be positive")
    return min(workers, parse_concurrency)


def simulation_in_flight_limit(workers: int) -> int:
    """Bound queued simulations while keeping every worker supplied."""
    if workers < 1:
        raise ValueError("workers must be positive")
    return 2 * workers


def _run_simulation_job(job: dict[str, Any]) -> tuple[Any, str | None]:
    """Run one independent simulation in a worker process."""

    try:
        execution = run_paired_simulation(
            simulation_id_value=str(job["simulation_id"]),
            base_system_path=Path(job["base_system_path"]),
            run_root=Path(job["run_root"]),
            task_payload=job["task_payload"],
            taskset_hash=str(job["taskset_hash"]),
            processors=int(job["processors"]),
            exact_e0=job["exact_e0"],
            energy_config=job["energy_config"],
            simulation_config=job["simulation_config"],
            scheduler_id=str(job["scheduler_id"]),
            implicit_streaming_parse=bool(job.get("implicit_streaming_parse", False)),
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return execution, None


def _initialize_simulation_worker(parse_semaphore: Any) -> None:
    if os.name != "posix":
        raise RuntimeError("worker process-group isolation requires POSIX")
    os.setsid()
    simulation_engine._set_trace_parse_semaphore(parse_semaphore)


def _print_progress(
    *, completed: int, total: int, outstanding_requests: int,
    started: float, completed_at_start: int, parse_concurrency: int,
) -> None:
    elapsed = max(0.0, time.perf_counter() - started)
    completed_run = completed - completed_at_start
    throughput = completed_run / elapsed * 60.0 if elapsed else 0.0
    print(
        "scheduler-load-cross progress: "
        f"completed={completed} total={total} "
        f"outstanding_requests={outstanding_requests} "
        f"elapsed_seconds={elapsed:.1f} "
        f"throughput_requests_per_min={throughput:.2f} "
        f"parse_concurrency={parse_concurrency}",
        flush=True,
    )


def _progress_due(
    *, completed: int, completed_at_start: int, total: int,
    interval: int,
) -> bool:
    completed_run = completed - completed_at_start
    return completed_run > 0 and (
        completed_run % interval == 0 or completed == total
    )


_ATTEMPT_DIR_RE = re.compile(r"^attempt_(\d+)$")
_NORMAL_SCIENTIFIC_STATUSES = {
    SimulationStatus.PASS_OBSERVED.value,
    SimulationStatus.DEADLINE_MISS.value,
}
_TECHNICAL_STATUSES = {
    "SIM_INTERNAL_ERROR", "TECHNICAL_FAILURE", "RUNTIME_TIMEOUT",
    SimulationStatus.INTERNAL_ERROR.value,
    SimulationStatus.RUNTIME_TIMEOUT.value,
    SimulationStatus.HORIZON_INSUFFICIENT.value,
}


def _is_technical_result(row: dict[str, Any]) -> bool:
    status = str(row.get("simulation_status", ""))
    return row.get("technical_error") is not None or status in _TECHNICAL_STATUSES


def _read_pid(lock_path: Path) -> int | None:
    for line in lock_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("pid="):
            try:
                return int(line[4:])
            except ValueError:
                return None
    return None


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _assert_no_live_attempt_lock(request_root: Path) -> None:
    for lock_path in request_root.rglob("*.lock") if request_root.is_dir() else ():
        pid = _read_pid(lock_path)
        if pid is None:
            raise RuntimeError(f"cannot determine lock owner: {lock_path}")
        if _pid_is_alive(pid):
            raise RuntimeError(f"request has an active execution lock: {lock_path} (pid={pid})")


def _index_attempt_history(attempts: list[dict[str, Any]]) -> dict[str, set[int]]:
    """Validate attempts once and index used attempt numbers by request."""
    indexed: dict[str, set[int]] = {}
    for row in attempts:
        if not isinstance(row, dict):
            raise ValueError("attempt history contains an invalid row")
        request_id = row.get("request_id")
        attempt_index = row.get("attempt_index")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("attempt history contains an invalid request_id")
        if (
            isinstance(attempt_index, bool)
            or not isinstance(attempt_index, int)
            or attempt_index < 1
        ):
            raise ValueError(f"invalid attempt history for {request_id}")
        used = indexed.setdefault(request_id, set())
        if attempt_index in used:
            raise ValueError(
                f"duplicate attempt history for {request_id}: {attempt_index}"
            )
        used.add(attempt_index)
    return indexed


def _next_attempt_root(
    root: Path, request_id: str, attempts_by_request: dict[str, set[int]],
) -> tuple[int, Path]:
    request_root = root / "simulations" / request_id
    _assert_no_live_attempt_lock(request_root)
    used = set(attempts_by_request.get(request_id, set()))
    if request_root.is_dir():
        for child in request_root.iterdir():
            match = _ATTEMPT_DIR_RE.match(child.name)
            if match and child.is_dir():
                used.add(int(match.group(1)))
    attempt_index = 1
    while attempt_index in used:
        attempt_index += 1
    attempt_root = request_root / f"attempt_{attempt_index:04d}"
    try:
        attempt_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        # A concurrent invocation won the allocation race.  Do not overwrite
        # or reuse its directory; fail closed and let the caller retry.
        raise RuntimeError(f"attempt directory allocation raced: {attempt_root}")
    attempts_by_request.setdefault(request_id, set()).add(attempt_index)
    return attempt_index, attempt_root


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_resume_history(
    run_config: Path, stored_config: dict[str, Any], *, workers: int,
    prepare_workers: int, parse_concurrency: int, keep_traces: bool,
    completed_result_count: int, remaining_request_count: int,
    implicit_streaming_parse: bool = False,
) -> dict[str, Any]:
    """Append runtime resume metadata without changing the original config."""
    execution = stored_config.get("execution")
    if not isinstance(execution, dict):
        raise SystemExit("resume execution metadata is invalid")
    history = execution.get("resume_history", [])
    if not isinstance(history, list):
        raise SystemExit("resume history is invalid")
    record = {
        "workers": workers,
        "prepare_workers": prepare_workers,
        "parse_concurrency": parse_concurrency,
        "keep_traces": bool(keep_traces),
        "completed_result_count_at_resume_start": completed_result_count,
        "remaining_request_count_at_resume_start": remaining_request_count,
        "implicit_streaming_parse": bool(implicit_streaming_parse),
        "stored_run_identity": stored_config.get("run_identity"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    updated = dict(stored_config)
    updated_execution = dict(execution)
    updated_execution["resume_history"] = [*history, record]
    updated["execution"] = updated_execution
    write_json(run_config, updated)
    return updated


_FAILURE_CLEANUP_TIMEOUT_SECONDS = 2.0
_FAILURE_TERMINATION_GRACE_SECONDS = 0.5
_WORKER_GROUP_CAPTURE_TIMEOUT_SECONDS = 1.0


def _capture_worker_process_groups(executor: Any) -> set[int]:
    """Capture and validate the independent process groups owned by executor."""
    if os.name != "posix":
        raise RuntimeError("worker process-group cleanup requires POSIX")
    own_pid = os.getpid()
    own_group = os.getpgrp()
    deadline = time.monotonic() + _WORKER_GROUP_CAPTURE_TIMEOUT_SECONDS
    while True:
        process_map = getattr(executor, "_processes", None)
        if process_map is None:
            raise RuntimeError("cannot determine executor worker processes")
        groups: set[int] = set()
        initializing = False
        for process in process_map.values():
            pid = getattr(process, "pid", None)
            if not isinstance(pid, int) or pid <= 1:
                raise RuntimeError("cannot determine executor worker PID")
            try:
                group_id = os.getpgid(pid)
            except ProcessLookupError as exc:
                raise RuntimeError(
                    f"executor worker {pid} exited before process-group verification"
                ) from exc
            if group_id != pid:
                initializing = True
                continue
            if group_id in {own_pid, own_group} or group_id <= 1:
                raise RuntimeError(f"refusing unsafe worker process group {group_id}")
            groups.add(group_id)
        if not initializing:
            return groups
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "executor worker did not establish an independent process group"
            )
        time.sleep(0.01)


def _validate_worker_process_groups(worker_process_groups: set[int]) -> bool:
    """Return whether all target groups are safe to signal from this runner."""
    if os.name != "posix":
        return False
    own_pid = os.getpid()
    own_group = os.getpgrp()
    return bool(worker_process_groups) and all(
        isinstance(group_id, int)
        and group_id > 1
        and group_id not in {own_pid, own_group}
        for group_id in worker_process_groups
    )


def _process_group_state(group_id: int) -> bool | None:
    """Return alive/dead, or None when liveness cannot be verified safely."""
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            entries = tuple(proc_root.iterdir())
        except OSError:
            return None
        live_member = False
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(
                    encoding="ascii", errors="replace",
                )
                suffix = stat.rsplit(")", 1)[1].split()
                member_group = int(suffix[2])
            except (OSError, IndexError, ValueError):
                continue
            if member_group == group_id and suffix[0] != "Z":
                live_member = True
                break
        return live_member
    return True


def _wait_for_worker_process_groups(
    worker_process_groups: set[int], deadline: float,
) -> bool:
    while True:
        states = [_process_group_state(group_id) for group_id in worker_process_groups]
        if any(state is None for state in states):
            return False
        if not any(states):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _worker_diagnostics(executor: Any) -> list[dict[str, Any]]:
    """Capture worker PID/exit diagnostics without guessing unavailable data."""
    process_map = getattr(executor, "_processes", None)
    processes = tuple(process_map.values()) if process_map else ()
    if not processes:
        return [{"pid": "unknown", "exitcode": "unknown", "signal_name": "unknown"}]
    diagnostics = []
    for process in processes:
        pid = getattr(process, "pid", "unknown")
        try:
            exitcode = process.exitcode
        except (AttributeError, OSError):
            exitcode = None
        signal_name = None
        if exitcode is None:
            exitcode = "unknown"
            signal_name = "unknown"
        elif isinstance(exitcode, int) and exitcode < 0:
            try:
                signal_name = signal.Signals(-exitcode).name
            except ValueError:
                signal_name = "unknown"
        diagnostics.append({
            "pid": pid, "exitcode": exitcode, "signal_name": signal_name,
        })
    return diagnostics


def _start_executor_shutdown(executor: Any) -> tuple[threading.Thread, dict[str, Any]]:
    """Run failure shutdown away from the runner thread."""
    shutdown = getattr(executor, "shutdown", None)
    outcome: dict[str, Any] = {"error": None}

    def shutdown_executor() -> None:
        try:
            if shutdown is None:
                executor.__exit__(None, None, None)
                return
            try:
                shutdown(wait=False, cancel_futures=True)
            except TypeError:
                shutdown(wait=False)
        except BaseException as exc:  # pragma: no cover - defensive boundary
            outcome["error"] = exc

    thread = threading.Thread(
        target=shutdown_executor,
        name="scheduler-load-cross-failure-shutdown",
        daemon=True,
    )
    thread.start()
    return thread, outcome


def _abort_executor(
    executor: Any, futures: Any,
    worker_process_groups: set[int] | None = None,
) -> bool:
    """Cancel work and boundedly clean only this executor's worker groups."""
    for future in futures:
        future.cancel()

    cleanup_started = time.monotonic()
    deadline = cleanup_started + _FAILURE_CLEANUP_TIMEOUT_SECONDS

    # Keep a snapshot because asynchronous shutdown may clear the executor's
    # private process map before the bounded process-group cleanup completes.
    process_map = getattr(executor, "_processes", {}) or {}
    processes = tuple(process_map.values())
    cleanup_complete = True
    if worker_process_groups is None:
        try:
            worker_process_groups = _capture_worker_process_groups(executor)
        except RuntimeError:
            worker_process_groups = set()
            cleanup_complete = False
    if not _validate_worker_process_groups(worker_process_groups):
        cleanup_complete = False
    shutdown_thread, shutdown_outcome = _start_executor_shutdown(executor)

    if cleanup_complete:
        # The group IDs were verified before signaling.  A missing group means
        # its worker and all descendants have already exited.
        for group_id in worker_process_groups:
            state = _process_group_state(group_id)
            if state is None:
                cleanup_complete = False
                continue
            if state:
                try:
                    os.killpg(group_id, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    cleanup_complete = False

    # TERM gets a short grace period; the shared deadline bounds all waits.
    term_deadline = min(
        deadline, cleanup_started + _FAILURE_TERMINATION_GRACE_SECONDS,
    )
    if cleanup_complete and not _wait_for_worker_process_groups(
        worker_process_groups, term_deadline,
    ):
        for group_id in worker_process_groups:
            state = _process_group_state(group_id)
            if state is None:
                cleanup_complete = False
                continue
            if state:
                try:
                    os.killpg(group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    cleanup_complete = False
        if cleanup_complete:
            cleanup_complete = _wait_for_worker_process_groups(
                worker_process_groups, deadline,
            )

    # Reap only the process objects belonging to this executor, without ever
    # waiting beyond the same cleanup deadline.
    for process in processes:
        try:
            remaining = max(0.0, deadline - time.monotonic())
            process.join(remaining)
        except (AttributeError, OSError):
            cleanup_complete = False
    for process in processes:
        try:
            if process.is_alive():
                cleanup_complete = False
        except (AttributeError, OSError):
            cleanup_complete = False
    shutdown_thread.join(max(0.0, deadline - time.monotonic()))
    if shutdown_thread.is_alive() or shutdown_outcome["error"] is not None:
        cleanup_complete = False
    return cleanup_complete


def _close_executor_normally(executor: Any) -> None:
    """Preserve the normal wait-for-all-workers executor behavior."""
    shutdown = getattr(executor, "shutdown", None)
    if shutdown is not None:
        shutdown(wait=True)
    else:
        # Compatibility with the existing inline test executor.
        executor.__exit__(None, None, None)


def _persisted_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return the compact result metrics without mutating the execution result."""
    persisted = dict(metrics)
    persisted.pop("battery_trajectory", None)
    return persisted


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--campaign", choices=(
            "v6", experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN,
            experiment.V7_UE_SERVICE_SCALING_CAMPAIGN,
        ), default="v6",
        help="v6 for the historical contract, or one explicit constrained-only v7 campaign",
    )
    parser.add_argument(
        "--energy-control", choices=("FIXED_ABSOLUTE_SUPPLY", "SERVICE_ONLY_SCALING"),
        default=None,
        help="optional v7 control assertion; the campaign selects the control by default",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--prepare-workers", type=int, default=None,
        help="bounded workers for deterministic preparation (default: --workers)",
    )
    parser.add_argument("--samples-per-cell", type=int, default=1)
    parser.add_argument("--cells")
    parser.add_argument("--schedulers")
    parser.add_argument(
        "--priority-policy", choices=("RM", "DM"), default="RM",
    )
    parser.add_argument("--processors", type=int, default=perf_g.PROCESSORS)
    parser.add_argument("--tasks", type=int, default=perf_g.TASK_COUNT)
    parser.add_argument("--period-min", type=int, default=perf_g.PERIOD_MIN_MS)
    parser.add_argument("--period-max", type=int, default=perf_g.PERIOD_MAX_MS)
    parser.add_argument("--min-task-util", default=str(perf_g.MIN_TASK_UTILIZATION))
    parser.add_argument("--max-task-util", default=str(perf_g.MAX_TASK_UTILIZATION))
    parser.add_argument("--util-tolerance-total", default=str(perf_g.UTILIZATION_TOLERANCE))
    parser.add_argument("--rho", default="11/2")
    parser.add_argument("--latency", default="2/5")
    parser.add_argument("--simulation-horizon", type=int, default=perf_g.FORMAL_HORIZON_MS)
    parser.add_argument("--timeout-seconds", type=int, default=perf_g.FORMAL_TIMEOUT_SECONDS)
    parser.add_argument("--kappa", default=str(experiment.DEFAULT_KAPPA))
    parser.add_argument("--simulator", type=Path, default=ROOT / "build/rtsim/rtsim")
    parser.add_argument("--uc-figure-fixed-ue")
    parser.add_argument("--ue-figure-fixed-uc")
    parser.add_argument("--ue-figure-fixed-ucs")
    parser.add_argument("--ue-figure-labels")
    parser.add_argument("--uc-scan-values")
    parser.add_argument("--ue-scan-values")
    parser.add_argument("--uc-figure-fixed-ues")
    parser.add_argument("--uc-figure-labels")
    parser.add_argument("--axis-display-min")
    parser.add_argument("--axis-display-max")
    parser.add_argument("--axis-tick-step")
    parser.add_argument(
        "--parse-concurrency", type=int, default=1,
        help="maximum concurrent trace parsers (default: 1)",
    )
    parser.add_argument(
        "--keep-traces", action="store_true",
        help="retain complete simulator traces for debugging",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--implicit-streaming-parse", action="store_true",
        help="opt in to bounded-memory parsing for v6 RM implicit resume only",
    )
    return parser


_V4_GRID_ARGS = (
    "uc_scan_values", "ue_scan_values", "uc_figure_fixed_ues",
    "uc_figure_labels", "ue_figure_fixed_ucs", "ue_figure_labels",
    "axis_display_min", "axis_display_max", "axis_tick_step",
)


def _resolve_grid(args: argparse.Namespace) -> tuple[
    tuple[tuple[Fraction, Fraction], ...], dict[str, Any], dict[str, Any] | None, bool,
]:
    if args.campaign != "v6":
        if args.cells is not None or any(
            getattr(args, name) is not None for name in _V4_GRID_ARGS
        ):
            raise SystemExit("v7 campaigns use their frozen grid and do not accept grid overrides")
        try:
            spec = experiment.v7_campaign_spec(args.campaign)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        return spec["cells"], spec["figure_slices"], spec["scan_contract"], False
    structured = any(getattr(args, name) is not None for name in _V4_GRID_ARGS)
    if args.cells is not None:
        raise SystemExit(
            "--cells is a legacy write path and is not supported for new v5 runs"
        )
    if args.uc_figure_fixed_ue is not None and args.uc_figure_fixed_ues is not None:
        raise SystemExit("singular --uc-figure-fixed-ue conflicts with plural v4 option")
    if args.uc_figure_fixed_ue is not None:
        raise SystemExit("v4 requires --uc-figure-fixed-ues and --uc-figure-labels")
    if args.ue_figure_fixed_uc is not None and args.ue_figure_fixed_ucs is not None:
        raise SystemExit("singular --ue-figure-fixed-uc conflicts with plural v4 option")
    if args.ue_figure_fixed_uc is not None:
        raise SystemExit("v4 requires --ue-figure-fixed-ucs and --ue-figure-labels")
    try:
        profile = experiment.normalize_scan_profile(
            uc_scan_values=args.uc_scan_values,
            ue_scan_values=args.ue_scan_values,
            uc_figure_fixed_ues=args.uc_figure_fixed_ues,
            uc_figure_labels=args.uc_figure_labels,
            ue_figure_fixed_ucs=args.ue_figure_fixed_ucs,
            ue_figure_labels=args.ue_figure_labels,
            axis_display_min=args.axis_display_min,
            axis_display_max=args.axis_display_max,
            axis_tick_step=args.axis_tick_step,
        )
        cells = experiment.build_scan_cells(
            profile["uc_scan_values"], profile["ue_scan_values"],
            profile["uc_figure_fixed_ues"], profile["ue_figure_fixed_ucs"],
        )
        contract = experiment.build_scan_contract(profile)
        figure_slices = experiment.build_v4_figure_slices(profile)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return cells, figure_slices, contract, True


def _validate_v7_generation_parameters(
    campaign: str, *, period_min: int, period_max: int,
    min_task_util: Fraction, max_task_util: Fraction,
    util_tolerance_total: Fraction,
) -> None:
    """Reject task-generation overrides for formal v7 campaigns."""
    if campaign == "v6":
        return
    if (
        period_min != perf_g.PERIOD_MIN_MS
        or period_max != perf_g.PERIOD_MAX_MS
        or min_task_util != perf_g.MIN_TASK_UTILIZATION
        or max_task_util != perf_g.MAX_TASK_UTILIZATION
        or util_tolerance_total != perf_g.UTILIZATION_TOLERANCE
    ):
        raise SystemExit(
            "v7 formal campaigns freeze PERF-G task generation parameters"
        )


def _validate_implicit_streaming_scope(
    *, enabled: bool, campaign: str, priority_policy: str, resume: bool,
    requests_by_id: dict[str, dict[str, Any]], remaining_ids: set[str],
) -> None:
    """Keep the bounded-memory parser strictly scoped to v6 RM implicit work."""
    if not enabled:
        return
    if campaign != "v6" or priority_policy != "RM" or not resume:
        raise SystemExit(
            "implicit streaming parse requires v6 RM resume"
        )
    if any(
        requests_by_id[request_id]["deadline_mode"] != "implicit"
        for request_id in remaining_ids
    ):
        raise SystemExit(
            "implicit streaming parse requires all remaining requests to be implicit"
        )


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    campaign = args.campaign
    if campaign == "v6":
        if args.energy_control is not None and args.energy_control != "SERVICE_ONLY_SCALING":
            raise SystemExit("v6 uses SERVICE_ONLY_SCALING and cannot select another energy control")
        selected_energy_control = "SERVICE_ONLY_SCALING"
    else:
        selected_energy_control = (
            "FIXED_ABSOLUTE_SUPPLY"
            if campaign == experiment.V7_UC_FIXED_SUPPLY_CAMPAIGN
            else "SERVICE_ONLY_SCALING"
        )
        if args.energy_control is not None and args.energy_control != selected_energy_control:
            raise SystemExit("energy-control does not match the selected v7 campaign")
    prepare_workers = args.workers if args.prepare_workers is None else args.prepare_workers
    try:
        validate_workers(prepare_workers, "prepare-workers")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if min(args.workers, args.samples_per_cell, args.processors, args.tasks) < 1:
        raise SystemExit("workers, samples, processors, and tasks must be positive")
    if args.simulation_horizon <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("simulation horizon and timeout must be positive")
    if args.parse_concurrency < 1:
        raise SystemExit("parse-concurrency must be positive")
    try:
        parser_limit = effective_concurrent_parsers(
            args.workers, args.parse_concurrency
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    cells, figure_slices, scan_contract, _structured = _resolve_grid(args)
    schedulers = experiment.parse_schedulers(
        args.schedulers if args.schedulers is not None
        else ",".join(perf_g.FORMAL_SCHEDULERS)
    )
    priority_policy = args.priority_policy
    _validate_implicit_streaming_scope(
        enabled=args.implicit_streaming_parse, campaign=campaign,
        priority_policy=priority_policy, resume=args.resume,
        requests_by_id={}, remaining_ids=set(),
    )
    if campaign != "v6" and tuple(schedulers) != tuple(perf_g.FORMAL_SCHEDULERS):
        raise SystemExit("v7 formal campaigns require all nine canonical schedulers")
    is_frozen_main_figure = tuple(cells) == tuple(experiment.FORMAL_CELLS)
    min_util = experiment.parse_fraction(args.min_task_util, "min-task-util")
    max_util = experiment.parse_fraction(args.max_task_util, "max-task-util")
    tolerance = experiment.parse_fraction(args.util_tolerance_total, "util-tolerance-total")
    _validate_v7_generation_parameters(
        campaign,
        period_min=args.period_min,
        period_max=args.period_max,
        min_task_util=min_util,
        max_task_util=max_util,
        util_tolerance_total=tolerance,
    )
    kappa = experiment.parse_fraction(args.kappa, "kappa")
    if kappa != experiment.DEFAULT_KAPPA:
        raise SystemExit("scheduler LOAD-CROSS freezes kappa=10")
    if campaign != "v6" and (
        args.processors != perf_g.PROCESSORS
        or args.tasks != perf_g.TASK_COUNT
        or args.simulation_horizon != perf_g.FORMAL_HORIZON_MS
    ):
        raise SystemExit("v7 formal campaigns freeze processors=4, tasks=10, and simulation horizon=60000 ms")
    if is_frozen_main_figure:
        try:
            experiment.validate_v6_main_figure(
                cells, schedulers, horizon_ms=args.simulation_horizon,
                priority_policy=priority_policy,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    # rho/latency are retained as explicit provenance controls for parity with
    # the RTA generation interface; simulator P remains canonical and untouched.
    rho = experiment.parse_fraction(args.rho, "rho")
    latency = experiment.parse_fraction(args.latency, "latency")
    root = args.output
    is_v7 = campaign != "v6"
    experiment_name = experiment.V7_EXPERIMENT if is_v7 else experiment.V6_EXPERIMENT
    deadline_modes = (
        experiment.v7_deadline_modes_for_priority_policy(priority_policy)
        if is_v7 else experiment.deadline_modes_for_priority_policy(priority_policy)
    )
    expected_request_count = len(cells) * args.samples_per_cell * len(schedulers) * len(deadline_modes)
    expected_taskset_count = len(set(uc for uc, _ue in cells)) * args.samples_per_cell * len(deadline_modes)
    config = {
        "experiment": experiment_name,
        "domain": experiment.V7_DOMAIN if is_v7 else experiment.V6_DOMAIN,
        "campaign_contract": (
            experiment.v7_campaign_spec(campaign)["campaign_contract"]
            if is_v7 else experiment.V6_CAMPAIGN_CONTRACT
        ),
        "seed": args.seed, "workers": args.workers,
        "deadline_modes": list(deadline_modes),
        "priority_policy": priority_policy,
        "expected_request_count": expected_request_count,
        "expected_taskset_count": expected_taskset_count,
        "implicit_priority_equivalence": experiment.V6_IMPLICIT_PRIORITY_EQUIVALENCE,
        "implicit_canonical_priority_policy": experiment.V6_IMPLICIT_CANONICAL_PRIORITY_POLICY,
        "implicit_reuse_policy": experiment.V6_IMPLICIT_REUSE_POLICY,
        "shared_implicit_contract_version": experiment.V6_SHARED_IMPLICIT_CONTRACT_VERSION,
        "samples_per_cell": args.samples_per_cell, "cells": [[str(uc), str(ue)] for uc, ue in cells],
        "schedulers": list(schedulers), "processors": args.processors, "tasks": args.tasks,
        "period_min": args.period_min, "period_max": args.period_max,
        "min_task_util": str(min_util), "max_task_util": str(max_util),
        "util_tolerance_total": str(tolerance), "rho": str(rho), "latency": str(latency),
        "kappa": str(kappa), "initial_energy_rule": "battery_capacity/2",
        "normalization_horizon_ms": experiment.FORMAL_NORMALIZATION_HORIZON,
        "simulation_horizon_ms": args.simulation_horizon,
        "use_real_solar_data": False,
        **experiment.HARVEST_MODEL_IDENTITY,
        "release_semantics": "synchronous arrival_offset=0",
        "energy_control": selected_energy_control, "energy_unit": "J/tick exact canonical P",
        "simulator": str(args.simulator), "canonical_taskset_source": "PERF-G TasksetStore",
        "keep_traces": args.keep_traces,
        "parse_concurrency": args.parse_concurrency,
        "figure_slices": figure_slices,
        "execution": {
            "workers": args.workers,
            "prepare_workers": prepare_workers,
            "parse_concurrency": args.parse_concurrency,
            "keep_traces": bool(args.keep_traces),
        },
    }
    if is_v7:
        for key in (
            "implicit_priority_equivalence", "implicit_canonical_priority_policy",
            "implicit_reuse_policy", "shared_implicit_contract_version",
        ):
            config.pop(key)
        config["campaign"] = campaign
        if selected_energy_control == "FIXED_ABSOLUTE_SUPPLY":
            config["fixed_supply_levels"] = {
                level: {
                    "reference_ue": str(experiment.V7_REFERENCE_UES[level]),
                    "fixed_supply_mean_j_per_tick": str(experiment.V7_FIXED_SUPPLIES[level]),
                }
                for level in ("low", "medium", "high")
            }
    if scan_contract is not None:
        config["scan_contract"] = scan_contract
    config["run_identity"] = experiment.run_identity(config)
    run_config = root / "run_config.json"
    if args.resume:
        stored_config = json.loads(run_config.read_text(encoding="utf-8")) if run_config.is_file() else None
        expected_experiment = experiment.V7_EXPERIMENT if is_v7 else experiment.V6_EXPERIMENT
        expected_domain = experiment.V7_DOMAIN if is_v7 else experiment.V6_DOMAIN
        if (stored_config or {}).get("experiment") != expected_experiment:
            if not is_v7:
                raise SystemExit("resume experiment mismatch: v6 cannot resume non-v6 results")
            raise SystemExit(
                "resume experiment mismatch: cannot resume a different campaign version"
            )
        if (stored_config or {}).get("domain") != expected_domain:
            raise SystemExit("resume domain mismatch: cannot resume another campaign domain")
        if not isinstance((stored_config or {}).get("run_identity"), str):
            raise SystemExit("resume configuration mismatch")
        if experiment.run_identity(stored_config) != stored_config["run_identity"]:
            raise SystemExit("resume configuration mismatch")
        if not _resume_configs_match(stored_config, config):
            raise SystemExit("resume configuration mismatch")
    elif run_config.exists() or (root / "results.jsonl").exists():
        raise SystemExit("output exists; use --resume or choose a new output")
    else:
        write_json(run_config, config)
    total_started = time.perf_counter()
    material = root / "material"
    unique_ucs = tuple(dict.fromkeys(uc for uc, _ue in cells))
    prepare_tasksets_started = time.perf_counter()
    tasksets: list[Any] = []
    tasksets_by_mode: dict[str, list[Any]] = {}
    service = None
    for deadline_mode in deadline_modes:
        mode_tasksets, mode_service = experiment.materialize_tasksets(
            material / deadline_mode, seed=args.seed, utilizations=unique_ucs,
            count=args.samples_per_cell, processors=args.processors, tasks=args.tasks,
            period_min=args.period_min, period_max=args.period_max,
            min_task_util=min_util, max_task_util=max_util, tolerance=tolerance,
            prepare_workers=prepare_workers, deadline_mode=deadline_mode,
        )
        if service is not None and mode_service.identity != service.identity:
            raise SystemExit("deadline modes do not share service-curve identity")
        service = mode_service
        tasksets_by_mode[deadline_mode] = mode_tasksets
        tasksets.extend(mode_tasksets)
    prepare_tasksets_seconds = time.perf_counter() - prepare_tasksets_started
    print(
        f"phase=prepare stage=scheduler-load-cross prepare-tasksets "
        f"elapsed_seconds={prepare_tasksets_seconds:.3f} workers={prepare_workers} "
        f"items={len(tasksets)}", flush=True,
    )
    rows = [experiment.taskset_row(taskset, args.processors) for taskset in tasksets]
    write_jsonl(root / "tasksets.jsonl", rows)
    request_started = time.perf_counter()
    requests = [
        row
        for deadline_mode in deadline_modes
        for row in experiment.request_rows(
            tasksets_by_mode[deadline_mode], cells, schedulers,
            args.simulation_horizon, priority_policy=priority_policy,
            experiment_name=experiment_name, deadline_mode=deadline_mode,
            campaign=campaign if is_v7 else None,
            energy_control=selected_energy_control if is_v7 else None,
        )
    ]
    if len(requests) != expected_request_count:
        raise SystemExit(
            f"generated request count does not match {'campaign' if is_v7 else 'v6'} contract: "
            f"expected {expected_request_count}, observed {len(requests)}"
        )
    write_jsonl(root / "requests.jsonl", requests)
    request_build_seconds = time.perf_counter() - request_started
    raw_trace = tuple(experiment.construct_paired_harvest_trace(
        service.system_path, experiment.FORMAL_NORMALIZATION_HORIZON,
    ))
    raw_trace_id = experiment.harvest_trace_identity(raw_trace)
    experiment.set_prepare_raw_trace(raw_trace)
    results_path = root / "results.jsonl"
    attempts_path = root / "attempts.jsonl"
    existing = read_jsonl(results_path) if args.resume else []
    if any(row.get("priority_policy", "RM") != priority_policy for row in existing):
        raise SystemExit("persisted results priority policy does not match requested policy")
    if any(
        any(row.get(key) != value for key, value in experiment.HARVEST_MODEL_IDENTITY.items())
        for row in existing
    ):
        raise SystemExit(
            "persisted results harvest model does not match "
            f"{experiment.HARVEST_MODEL}"
        )
    if any(
        row.get("experiment") != experiment_name
        or row.get("deadline_mode") not in deadline_modes
        for row in existing
    ):
        raise SystemExit("persisted results do not match the requested experiment")
    existing_ids = [str(row.get("request_id")) for row in existing]
    expected_ids = {str(row["request_id"]) for row in requests}
    requests_by_id = {str(row["request_id"]): row for row in requests}
    if len(existing_ids) != len(set(existing_ids)) or not set(existing_ids) <= expected_ids:
        raise SystemExit("persisted results contain duplicate or unexpected request IDs")
    if any(_is_technical_result(row) for row in existing):
        raise SystemExit(
            "active results contain a technical row; migration/recovery is required"
        )
    remaining_ids = expected_ids - set(existing_ids)
    _validate_implicit_streaming_scope(
        enabled=args.implicit_streaming_parse, campaign=campaign,
        priority_policy=priority_policy, resume=args.resume,
        requests_by_id=requests_by_id, remaining_ids=remaining_ids,
    )
    attempts = read_jsonl(attempts_path) if args.resume else []
    try:
        attempts_by_request = _index_attempt_history(attempts)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.resume:
        stored_config = _append_resume_history(
            run_config, stored_config, workers=args.workers,
            prepare_workers=prepare_workers,
            parse_concurrency=args.parse_concurrency,
            keep_traces=args.keep_traces,
            completed_result_count=len(existing),
            remaining_request_count=len(remaining_ids),
            implicit_streaming_parse=args.implicit_streaming_parse,
        )
    results_by_id = {str(row["request_id"]): row for row in existing}
    completed_ids = set(existing_ids)
    taskset_by_id = {taskset.taskset_id: taskset for taskset in tasksets}
    pending_jobs: list[dict[str, Any]] = []
    energy_jobs: dict[tuple[str, str], dict[str, Any]] = {}
    for request in requests:
        request_id = str(request["request_id"])
        if request_id in completed_ids:
            continue
        taskset = taskset_by_id[request["taskset_id"]]
        try:
            attempt_index, attempt_root = _next_attempt_root(
                root, request_id, attempts_by_request
            )
        except RuntimeError as exc:
            print(f"scheduler-load-cross resume blocked: {exc}", file=sys.stderr)
            return 2
        energy_key = (taskset.taskset_id, str(request["target_ue"]))
        energy_job = {
            "taskset_id": taskset.taskset_id,
            "target_ue": request["target_ue"],
            "task_payload": tuple(taskset.task_payload),
            "processors": taskset.processors,
            "task_count": taskset.task_count,
            "kappa": kappa,
            "raw_trace_id": raw_trace_id,
        }
        if is_v7:
            energy_job["energy_control"] = selected_energy_control
            if selected_energy_control == "FIXED_ABSOLUTE_SUPPLY":
                level = str(request["energy_level"])
                energy_job.update({
                    "energy_level": level,
                    "reference_ue": request["target_ue"],
                    "fixed_supply": experiment.V7_FIXED_SUPPLIES[level],
                })
        energy_jobs.setdefault(energy_key, energy_job)
        simulation = {
            "simulator_bin": str(args.simulator), "horizon": args.simulation_horizon,
            "maximum_horizon": args.simulation_horizon, "horizon_extension_policy": "none",
            "priority_policy": priority_policy,
            "warmup": 0, "minimum_jobs_per_task": 1, "trace_mode": "semantic",
            "trace_on_failure": args.keep_traces,
            "retain_trace": args.keep_traces,
            "timeout_seconds": args.timeout_seconds,
            "cleanup_transient_artifacts": True,
            # Runtime-only controls: these are deliberately kept out of the
            # persisted scientific configuration and its run identity.
            "trace_parse_concurrency": parser_limit,
            "trace_parse_slot_dir": "/tmp/partsim_trace_parse_slots",
            "deadline_mode": request["deadline_mode"],
            "implicit_streaming_parse": bool(args.implicit_streaming_parse),
        }
        pending_jobs.append({
            "request": request,
            "request_id": request_id,
            "simulation_id": request_id,
            "attempt_index": attempt_index,
            "attempt_root": str(attempt_root),
            "run_root": str(attempt_root),
            "taskset_id": taskset.taskset_id,
            "taskset_hash": taskset.semantic_hash,
            "task_payload": taskset.task_payload,
            "energy_key": energy_key,
            "base_system_path": str(service.system_path),
            "processors": args.processors,
            "exact_e0": None,
            "energy_config": None,
            "simulation_config": simulation,
            "scheduler_id": request["scheduler_cli"],
            "implicit_streaming_parse": bool(args.implicit_streaming_parse),
        })

    prepare_energy_started = time.perf_counter()
    prepared_energy = run_prepare_jobs(
        energy_jobs.values(), experiment.prepare_energy_material,
        workers=prepare_workers, phase="scheduler-load-cross prepare-energy",
        key=lambda row: (row["taskset_id"], row["target_ue"]),
    )
    energy_by_key = {
        key: value["material"] for key, value in prepared_energy.items()
    }
    prepare_energy_seconds = time.perf_counter() - prepare_energy_started
    for job in pending_jobs:
        job["energy"] = energy_by_key[job.pop("energy_key")]
        energy = job["energy"]
        simulation = job["simulation_config"]
        job["exact_e0"] = Fraction(energy["initial_energy_j"])
        simulation["trace_on_failure"] = args.keep_traces
        simulation["retain_trace"] = args.keep_traces
        job["energy_config"] = {
            "simulation_initial_battery": energy["initial_energy_j"],
            "battery_capacity": energy["battery_capacity_j"], "allow_harvest_clipping": True,
            "service_curve": {
                "solar_scale": energy["solar_scale"],
                "use_real_solar_data": False,
                "require_real_solar_data": False,
                **experiment.HARVEST_MODEL_IDENTITY,
            },
        }

    execution_started = time.perf_counter()
    mp_context = multiprocessing.get_context("fork")
    parse_semaphore = mp_context.Semaphore(parser_limit)
    executor = ProcessPoolExecutor(
        max_workers=args.workers,
        mp_context=mp_context,
        initializer=_initialize_simulation_worker,
        initargs=(parse_semaphore,),
    )
    future_to_job: dict[Any, dict[str, Any]] = {}
    worker_process_groups: set[int] = set()
    recorded_attempt_ids: set[str] = set()
    current_submitting_job: dict[str, Any] | None = None
    failure_worker_diagnostics: list[dict[str, Any]] = []
    executor_failed = False
    try:
        progress_started = time.perf_counter()
        completed_at_start = len(existing)
        completed_count = completed_at_start
        progress_interval = max(1, min(50, len(pending_jobs) // 20 or 1))
        pending_iterator = iter(pending_jobs)
        pending_exhausted = False
        in_flight_limit = simulation_in_flight_limit(args.workers)
        while future_to_job or not pending_exhausted:
            while not pending_exhausted and len(future_to_job) < in_flight_limit:
                try:
                    job = next(pending_iterator)
                except StopIteration:
                    pending_exhausted = True
                    break
                current_submitting_job = job
                future = executor.submit(_run_simulation_job, job)
                future_to_job[future] = job
                current_submitting_job = None
                worker_process_groups.update(_capture_worker_process_groups(executor))
            if not future_to_job:
                break
            future = next(iter(as_completed(future_to_job)))
            job = future_to_job.pop(future)
            request = job["request"]
            request_id = str(job["request_id"])
            task_payload = job["task_payload"]
            try:
                execution, technical = future.result()
            except BrokenProcessPool as exc:
                execution = None
                technical = f"worker failure: BrokenProcessPool: {exc}"
                failure_worker_diagnostics = _worker_diagnostics(executor)
            except Exception as exc:
                execution = None
                technical = f"worker failure: {type(exc).__name__}: {exc}"
                failure_worker_diagnostics = _worker_diagnostics(executor)
            if execution is None:
                outcome = evaluate_outcome(
                    [], [str(row["task_id"]) for row in task_payload],
                    horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                    simulation_completed=False, technical_error=technical,
                    strict_wholepass=True,
                )
                status = "TECHNICAL_FAILURE"
                reason = technical
                technical_error = technical
                runtime_seconds = 0.0
                metrics: dict[str, Any] = {}
                stdout_tail = ""
                stderr_tail = ""
                retained_trace_path = None
            else:
                status = execution.result.status
                is_technical = status.value not in _NORMAL_SCIENTIFIC_STATUSES
                technical_error = execution.result.reason if is_technical else None
                outcome = evaluate_outcome(
                    [asdict(observation) for observation in execution.result.jobs],
                    [str(row["task_id"]) for row in task_payload],
                    horizon=args.simulation_horizon, minimum_adjudicable_jobs=1,
                    simulation_completed=execution.result.simulation_completed,
                    technical_error=technical_error,
                    strict_wholepass=True,
                )
                reason = execution.result.reason
                status = status.value
                if outcome.get("technical_failure"):
                    technical_error = outcome.get("reason") or "wholepass_outcome_unavailable"
                    status = "TECHNICAL_FAILURE"
                runtime_seconds = execution.runtime_seconds
                metrics = _persisted_metrics(execution.result.metrics)
                stdout_tail = execution.stdout_tail
                stderr_tail = execution.stderr_tail
                retained_trace_path = str(execution.retained_trace_path) if execution.retained_trace_path else None
                row = {**request, "energy": job["energy"], "simulation_status": status,
                       "simulation_reason": reason,
                       "technical_error": technical_error,
                       "schedulable": outcome.get("taskset_pass"),
                       "deadline_miss": status == SimulationStatus.DEADLINE_MISS.value,
                       "runtime_seconds": runtime_seconds,
                       "metrics": metrics, "outcome": outcome,
                       "taskset_pass": outcome.get("taskset_pass"),
                       "wholepass": outcome.get("wholepass", outcome.get("taskset_pass"))}
            attempt_row = {
                **request,
                "request_id": request_id,
                "attempt_index": job["attempt_index"],
                "attempt_root": str(Path(job["attempt_root"]).relative_to(root)),
                "taskset_id": job["taskset_id"],
                "taskset_hash": job["taskset_hash"],
                "target_uc": request["target_uc"],
                "actual_uc": request["actual_uc"],
                "target_ue": request["target_ue"],
                "eta": request["eta"],
                "simulation_status": status,
                "technical_error": technical_error,
                "runtime_seconds": runtime_seconds,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
                "retained_trace_path": retained_trace_path,
                "simulation_reason": reason,
                "worker_diagnostics": failure_worker_diagnostics,
            }
            _append_jsonl(attempts_path, attempt_row)
            attempts.append(attempt_row)
            recorded_attempt_ids.add(request_id)
            completed_count += 1
            if _progress_due(
                completed=completed_count,
                completed_at_start=completed_at_start,
                total=len(requests), interval=progress_interval,
            ):
                _print_progress(
                    completed=completed_count, total=len(requests),
                    outstanding_requests=len(requests) - completed_count,
                    started=progress_started,
                    completed_at_start=completed_at_start,
                    parse_concurrency=args.parse_concurrency,
                )
            if status not in _NORMAL_SCIENTIFIC_STATUSES:
                print(
                    f"scheduler-load-cross technical execution failure for "
                    f"{request_id}: {status}: {reason}",
                    file=sys.stderr,
                )
                executor_failed = True
                break
            _append_jsonl(results_path, row)
            results_by_id[request_id] = row
            completed_ids.add(request_id)
    except Exception as exc:
        executor_failed = True
        technical = f"worker failure: {type(exc).__name__}: {exc}"
        if isinstance(exc, BrokenProcessPool):
            technical = f"worker failure: BrokenProcessPool: {exc}"
        failure_worker_diagnostics = _worker_diagnostics(executor)
        if current_submitting_job is None:
            print(
                f"scheduler-load-cross runner-level technical failure: {technical}",
                file=sys.stderr,
            )
            jobs_with_attributable_failure: tuple[dict[str, Any], ...] = ()
        else:
            jobs_with_attributable_failure = (current_submitting_job,)
        for job in jobs_with_attributable_failure:
            request_id = str(job["request_id"])
            if request_id in recorded_attempt_ids:
                continue
            request = job["request"]
            attempt_row = {
                **request,
                "request_id": request_id,
                "attempt_index": job["attempt_index"],
                "attempt_root": str(Path(job["attempt_root"]).relative_to(root)),
                "taskset_id": job["taskset_id"],
                "taskset_hash": job["taskset_hash"],
                "target_uc": request["target_uc"],
                "actual_uc": request["actual_uc"],
                "target_ue": request["target_ue"],
                "eta": request["eta"],
                "simulation_status": "TECHNICAL_FAILURE",
                "technical_error": technical,
                "runtime_seconds": 0.0,
                "stdout_tail": "",
                "stderr_tail": "",
                "retained_trace_path": None,
                "simulation_reason": technical,
                "worker_diagnostics": failure_worker_diagnostics,
            }
            _append_jsonl(attempts_path, attempt_row)
    finally:
        if executor_failed:
            cleanup_complete = _abort_executor(
                executor, future_to_job, worker_process_groups,
            )
            if not cleanup_complete:
                print(
                    "scheduler-load-cross executor cleanup incomplete",
                    file=sys.stderr,
                )
        else:
            _close_executor_normally(executor)
    if executor_failed:
        return 2
    observed_ids = list(results_by_id)
    if len(results_by_id) == len(requests) and set(results_by_id) == expected_ids:
        canonical_results = [results_by_id[str(request["request_id"])] for request in requests]
        write_jsonl(results_path, canonical_results)
    execution_seconds = time.perf_counter() - execution_started
    report = {
        "expected_results": len(requests), "observed_results": len(results_by_id),
        "missing_results": len(expected_ids - set(observed_ids)),
        "duplicate_request_ids": len(observed_ids) - len(set(observed_ids)),
        "missing": len(expected_ids - set(observed_ids)),
        "duplicate": len(observed_ids) - len(set(observed_ids)),
        "unexpected": len(set(observed_ids) - expected_ids),
        "technical": 0,
        "runtime_config_ue_exact": (
            all(
                Fraction(row["energy"]["actual_ue"]) ==
                Fraction(row["energy"]["target_ue"])
                for row in results_by_id.values()
            ) if selected_energy_control == "SERVICE_ONLY_SCALING" else False
        ),
        "experiment": experiment_name,
        "domain": experiment.V7_DOMAIN if is_v7 else experiment.V6_DOMAIN,
        "priority_policy": priority_policy,
        "deadline_modes": list(deadline_modes),
        "expected_request_count": expected_request_count,
        "expected_taskset_count": expected_taskset_count,
        "harvest_model": experiment.HARVEST_MODEL,
        "canonical_task_power": all(row.get("canonical_task_power") for row in rows),
        "scheduler_input_hashes_stable": all(len({row["taskset_hash"] for row in requests if row["taskset_id"] == taskset.taskset_id}) == 1 for taskset in tasksets),
        "complete": len(results_by_id) == len(requests) and len(observed_ids) == len(set(observed_ids)),
    }
    write_json(root / "invariant_report.json", report)
    config_document = json.loads(run_config.read_text(encoding="utf-8"))
    config_document["status"] = "complete" if report["complete"] else "incomplete"
    config_document["telemetry"] = {
        "prepare_tasksets_seconds": prepare_tasksets_seconds,
        "prepare_energy_seconds": prepare_energy_seconds,
        "request_build_seconds": request_build_seconds,
        "execution_seconds": execution_seconds,
        "total_seconds": time.perf_counter() - total_started,
    }
    write_json(run_config, config_document)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    exit_code = main()
    if exit_code:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)
    raise SystemExit(0)
