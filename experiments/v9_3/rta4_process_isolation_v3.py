"""Hard process boundary for one V3 production computation attempt.

The outer :class:`ProcessPoolExecutor` provides campaign-level parallelism.
This module provides the second, deliberately short-lived process boundary
that makes an individual timeout enforceable: a timed-out child is terminated,
killed if necessary, and reaped before its pool worker accepts more work.
"""

from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
from multiprocessing.connection import wait as wait_for_objects
import os
import pickle
import resource
import sys
import time
import traceback
from typing import Any, Callable, Sequence


ISOLATED_RESULT = "RESULT"
ISOLATED_TIMEOUT = "TIMEOUT"
ISOLATED_INTERNAL_ERROR = "INTERNAL_ERROR"
ISOLATED_SERIALIZATION_ERROR = "SERIALIZATION_ERROR"


@dataclass(frozen=True)
class IsolatedCallResultV3:
    status: str
    value: Any
    worker_pid: int | None
    runtime_wall_seconds: float
    runtime_cpu_seconds: float
    peak_rss_bytes: int
    error_classification: str
    cleanup_status: str
    worker_exitcode: int | None


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _isolated_call_entry(
    sending: Any,
    started: Any,
    function: Callable[..., Any],
    args: Sequence[Any],
) -> None:
    """Child-only computation.  It has no persistence objects or paths."""

    started.set()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        value = function(*args)
        payload = (
            ISOLATED_RESULT,
            os.getpid(),
            value,
            time.perf_counter() - wall_started,
            time.process_time() - cpu_started,
            _peak_rss_bytes(),
        )
    except BaseException as exc:
        payload = (
            ISOLATED_INTERNAL_ERROR,
            os.getpid(),
            type(exc).__name__,
            str(exc),
            traceback.format_exc(),
            time.perf_counter() - wall_started,
            time.process_time() - cpu_started,
            _peak_rss_bytes(),
        )
    try:
        sending.send(payload)
    finally:
        sending.close()


def _confirm_exit(process: Any, timeout_seconds: float) -> tuple[bool, int | None]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        process.join(0.0)
        if process.exitcode is not None:
            return True, int(process.exitcode)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, None
        try:
            ready = wait_for_objects([process.sentinel], timeout=remaining)
        except (OSError, TypeError, ValueError):
            process.join(min(remaining, 0.01))
            ready = []
        if not ready and time.monotonic() >= deadline:
            return False, None


def _reap(process: Any, *, normal_grace: float = 1.0) -> tuple[str, int | None]:
    confirmed, exitcode = _confirm_exit(process, normal_grace)
    if confirmed:
        process.close()
        return "EXITED_NORMALLY", exitcode
    process.terminate()
    confirmed, exitcode = _confirm_exit(process, 2.0)
    if confirmed:
        process.close()
        return "REAPED_AFTER_TERMINATE", exitcode
    process.kill()
    confirmed, exitcode = _confirm_exit(process, 2.0)
    if confirmed:
        process.close()
        return "REAPED_AFTER_KILL", exitcode
    return "UNREAPED_AFTER_KILL", None


def execute_isolated_call_v3(
    function: Callable[..., Any],
    args: Sequence[Any],
    timeout_seconds: int | float,
    *,
    start_method: str = "spawn",
) -> IsolatedCallResultV3:
    """Execute a pickle-safe call with a hard wall boundary and strict reap."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds < 0
    ):
        raise ValueError("isolated timeout must be non-negative")
    frozen_args = tuple(args)
    try:
        pickle.dumps((function, frozen_args), protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        return IsolatedCallResultV3(
            ISOLATED_SERIALIZATION_ERROR,
            None,
            None,
            0.0,
            0.0,
            0,
            f"SERIALIZATION_FAILURE:{type(exc).__name__}:{exc}"[:500],
            "NOT_STARTED",
            None,
        )

    context = multiprocessing.get_context(start_method)
    receiving, sending = context.Pipe(duplex=False)
    started = context.Event()
    process = context.Process(
        target=_isolated_call_entry,
        args=(sending, started, function, frozen_args),
        daemon=False,
    )
    total_started = time.perf_counter()
    try:
        process.start()
    except Exception as exc:
        receiving.close()
        sending.close()
        return IsolatedCallResultV3(
            ISOLATED_INTERNAL_ERROR,
            None,
            None,
            time.perf_counter() - total_started,
            0.0,
            0,
            f"PROCESS_START_FAILURE:{type(exc).__name__}:{exc}"[:500],
            "NOT_STARTED",
            None,
        )
    sending.close()
    worker_pid = process.pid

    startup_budget = min(10.0, max(1.0, float(timeout_seconds)))
    if not started.wait(startup_budget):
        receiving.close()
        cleanup, exitcode = _reap(process, normal_grace=0.0)
        return IsolatedCallResultV3(
            ISOLATED_INTERNAL_ERROR,
            None,
            worker_pid,
            time.perf_counter() - total_started,
            0.0,
            0,
            "PROCESS_WORKER_STARTUP_TIMEOUT",
            cleanup,
            exitcode,
        )

    transport_grace = min(1.0, max(0.1, float(timeout_seconds) * 0.05))
    if not receiving.poll(float(timeout_seconds) + transport_grace):
        receiving.close()
        cleanup, exitcode = _reap(process, normal_grace=0.0)
        if cleanup == "UNREAPED_AFTER_KILL":
            return IsolatedCallResultV3(
                ISOLATED_INTERNAL_ERROR,
                None,
                worker_pid,
                time.perf_counter() - total_started,
                0.0,
                0,
                "PROCESS_WORKER_UNREAPED_AFTER_HARD_TIMEOUT",
                cleanup,
                exitcode,
            )
        return IsolatedCallResultV3(
            ISOLATED_TIMEOUT,
            None,
            worker_pid,
            time.perf_counter() - total_started,
            0.0,
            0,
            "UNIFIED_RTA_ADAPTER_TIMEOUT:HARD_WALL",
            cleanup,
            exitcode,
        )

    try:
        payload = receiving.recv()
    except Exception as exc:
        payload = (
            ISOLATED_INTERNAL_ERROR,
            worker_pid,
            type(exc).__name__,
            str(exc),
            traceback.format_exc(),
            0.0,
            0.0,
            0,
        )
    finally:
        receiving.close()
    cleanup, exitcode = _reap(process)
    total_wall = time.perf_counter() - total_started
    if cleanup == "UNREAPED_AFTER_KILL":
        return IsolatedCallResultV3(
            ISOLATED_INTERNAL_ERROR,
            None,
            worker_pid,
            total_wall,
            0.0,
            0,
            "PROCESS_WORKER_UNREAPED_AFTER_PAYLOAD",
            cleanup,
            exitcode,
        )

    if not isinstance(payload, tuple) or len(payload) < 2:
        return IsolatedCallResultV3(
            ISOLATED_INTERNAL_ERROR,
            None,
            worker_pid,
            total_wall,
            0.0,
            0,
            "INVALID_WORKER_PAYLOAD",
            cleanup,
            exitcode,
        )
    if payload[0] == ISOLATED_RESULT and len(payload) == 6:
        return IsolatedCallResultV3(
            ISOLATED_RESULT,
            payload[2],
            int(payload[1]),
            total_wall,
            float(payload[4]),
            int(payload[5]),
            "NONE",
            cleanup,
            exitcode,
        )
    if payload[0] == ISOLATED_INTERNAL_ERROR and len(payload) == 8:
        return IsolatedCallResultV3(
            ISOLATED_INTERNAL_ERROR,
            None,
            int(payload[1]),
            total_wall,
            float(payload[6]),
            int(payload[7]),
            f"WORKER_EXCEPTION:{payload[2]}:{payload[3]}"[:500],
            cleanup,
            exitcode,
        )
    return IsolatedCallResultV3(
        ISOLATED_INTERNAL_ERROR,
        None,
        worker_pid,
        total_wall,
        0.0,
        0,
        "INVALID_WORKER_PAYLOAD",
        cleanup,
        exitcode,
    )


__all__ = [
    "ISOLATED_INTERNAL_ERROR",
    "ISOLATED_RESULT",
    "ISOLATED_SERIALIZATION_ERROR",
    "ISOLATED_TIMEOUT",
    "IsolatedCallResultV3",
    "execute_isolated_call_v3",
]
