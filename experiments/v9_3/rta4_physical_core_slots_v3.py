"""Pinned, persistent physical-core process slots for formal RTA4 V3 runs.

This module is deliberately computation-agnostic.  The parent owns rolling
dispatch, hard deadlines, replacement, and diagnostics; a slot owns exactly
one long-lived process pinned to one allowed physical core.  Workers never
receive persistence paths or writer objects.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import multiprocessing
from multiprocessing.connection import wait as wait_for_connections
import os
from pathlib import Path
import time
import traceback
from typing import Any, Callable, Iterable, Mapping, Sequence


PHYSICAL_CORE_SELECTION_POLICY_V3 = (
    "ALLOWED_CPU_PACKAGE_CORE_LOWEST_LOGICAL_DETERMINISTIC_V1"
)
PHYSICAL_CORE_EXECUTION_BACKEND_V3 = "PHYSICAL_CORE_PROCESS_SLOTS"


class PhysicalCoreSlotV3Error(RuntimeError):
    """Fail-closed topology, affinity, protocol, or process-lifecycle error."""


@dataclass(frozen=True, order=True)
class PhysicalCoreV3:
    physical_package_id: int
    physical_core_id: int
    logical_cpu_id: int
    allowed_siblings: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "physical_package_id": self.physical_package_id,
            "physical_core_id": self.physical_core_id,
            "selected_logical_cpu_id": self.logical_cpu_id,
            "allowed_logical_siblings": list(self.allowed_siblings),
        }


@dataclass(frozen=True)
class CPUTopologyV3:
    allowed_logical_cpus: tuple[int, ...]
    physical_cores: tuple[PhysicalCoreV3, ...]
    topology_fingerprint: str
    selection_policy: str = PHYSICAL_CORE_SELECTION_POLICY_V3

    @property
    def physical_core_count(self) -> int:
        return len(self.physical_cores)

    def select(self, worker_count: int) -> tuple[PhysicalCoreV3, ...]:
        if type(worker_count) is not int or worker_count < 1:
            raise PhysicalCoreSlotV3Error(
                "physical worker_count must be a positive integer"
            )
        if worker_count > self.physical_core_count:
            raise PhysicalCoreSlotV3Error(
                f"requested {worker_count} physical workers but only "
                f"{self.physical_core_count} allowed physical cores are available"
            )
        selected = self.physical_cores[:worker_count]
        identities = {
            (row.physical_package_id, row.physical_core_id)
            for row in selected
        }
        if len(identities) != worker_count:
            raise PhysicalCoreSlotV3Error(
                "physical core selection contains duplicate identities"
            )
        return selected

    def as_dict(self) -> dict[str, Any]:
        sibling_groups = [
            {
                "physical_package_id": row.physical_package_id,
                "physical_core_id": row.physical_core_id,
                "allowed_logical_cpus": list(row.allowed_siblings),
                "selected_logical_cpu_id": row.logical_cpu_id,
            }
            for row in self.physical_cores
        ]
        return {
            "allowed_logical_cpus": list(self.allowed_logical_cpus),
            "physical_core_count": self.physical_core_count,
            "smt_sibling_groups": sibling_groups,
            "selectable_physical_worker_counts": list(
                range(1, self.physical_core_count + 1)
            ),
            "deterministic_selection_order": [
                row.as_dict() for row in self.physical_cores
            ],
            "topology_selection_policy": self.selection_policy,
            "topology_fingerprint": self.topology_fingerprint,
        }


def _read_topology_pair(cpu_id: int, sysfs_root: Path) -> tuple[int, int]:
    topology = sysfs_root / f"cpu{cpu_id}" / "topology"
    try:
        package = int((topology / "physical_package_id").read_text().strip())
        core = int((topology / "core_id").read_text().strip())
    except (OSError, TypeError, ValueError) as exc:
        raise PhysicalCoreSlotV3Error(
            f"cannot read physical topology for allowed logical CPU {cpu_id}"
        ) from exc
    if package < 0 or core < 0:
        raise PhysicalCoreSlotV3Error(
            f"invalid physical topology for allowed logical CPU {cpu_id}"
        )
    return package, core


def discover_cpu_topology_v3(
    *,
    allowed_logical_cpus: Iterable[int] | None = None,
    topology_reader: Callable[[int], tuple[int, int]] | None = None,
    affinity_getter: Callable[[int], Iterable[int]] | None = None,
    sysfs_root: Path | str = "/sys/devices/system/cpu",
) -> CPUTopologyV3:
    """Discover allowed CPUs and collapse SMT siblings fail-closed.

    ``allowed_logical_cpus`` and ``topology_reader`` are explicit test seams.
    Production callers omit both, so the allowed set always originates from
    ``sched_getaffinity(0)`` and every package/core identity comes from sysfs.
    """

    if allowed_logical_cpus is None:
        getter = os.sched_getaffinity if affinity_getter is None else affinity_getter
        try:
            observed = getter(0)
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise PhysicalCoreSlotV3Error(
                "cannot read the process allowed CPU affinity"
            ) from exc
    else:
        observed = allowed_logical_cpus
    try:
        allowed = tuple(sorted({int(cpu) for cpu in observed}))
    except (TypeError, ValueError) as exc:
        raise PhysicalCoreSlotV3Error("allowed CPU affinity is invalid") from exc
    if not allowed or any(cpu < 0 for cpu in allowed):
        raise PhysicalCoreSlotV3Error("allowed CPU affinity is empty or invalid")

    root = Path(sysfs_root)
    reader = (
        (lambda cpu: _read_topology_pair(cpu, root))
        if topology_reader is None else topology_reader
    )
    grouped: dict[tuple[int, int], list[int]] = {}
    for cpu in allowed:
        try:
            pair = reader(cpu)
        except PhysicalCoreSlotV3Error:
            raise
        except Exception as exc:
            raise PhysicalCoreSlotV3Error(
                f"cannot read physical topology for allowed logical CPU {cpu}"
            ) from exc
        if (
            not isinstance(pair, tuple) or len(pair) != 2
            or any(type(value) is not int or value < 0 for value in pair)
        ):
            raise PhysicalCoreSlotV3Error(
                f"invalid physical topology for allowed logical CPU {cpu}"
            )
        grouped.setdefault(pair, []).append(cpu)
    if not grouped:
        raise PhysicalCoreSlotV3Error("no distinguishable physical cores found")

    cores = tuple(
        PhysicalCoreV3(package, core, min(siblings), tuple(sorted(siblings)))
        for (package, core), siblings in sorted(grouped.items())
    )
    material = {
        "allowed_logical_cpus": list(allowed),
        "physical_core_groups": [row.as_dict() for row in cores],
        "selection_policy": PHYSICAL_CORE_SELECTION_POLICY_V3,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return CPUTopologyV3(allowed, cores, fingerprint)


@dataclass(frozen=True)
class WorkerBindingV3:
    slot_id: int
    logical_cpu_id: int
    physical_package_id: int
    physical_core_id: int

    @classmethod
    def from_core(cls, slot_id: int, core: PhysicalCoreV3) -> "WorkerBindingV3":
        return cls(
            slot_id,
            core.logical_cpu_id,
            core.physical_package_id,
            core.physical_core_id,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "slot_id": self.slot_id,
            "logical_cpu_id": self.logical_cpu_id,
            "physical_package_id": self.physical_package_id,
            "physical_core_id": self.physical_core_id,
        }


@dataclass(frozen=True)
class WorkerDiagnosticV3:
    worker_pid: int
    logical_cpu_id: int
    physical_package_id: int
    physical_core_id: int
    affinity_mask: tuple[int, ...]
    slot_id: int
    worker_generation: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_pid": self.worker_pid,
            "logical_cpu_id": self.logical_cpu_id,
            "physical_package_id": self.physical_package_id,
            "physical_core_id": self.physical_core_id,
            "affinity_mask": list(self.affinity_mask),
            "slot_id": self.slot_id,
            "worker_generation": self.worker_generation,
        }


@dataclass(frozen=True)
class SlotStartedV3:
    slot_id: int
    task_id: str
    worker: WorkerDiagnosticV3
    started_monotonic_ns: int


@dataclass(frozen=True)
class SlotCompletionV3:
    slot_id: int
    task_id: str
    worker: WorkerDiagnosticV3
    started_monotonic_ns: int
    finished_monotonic_ns: int
    runtime_cpu_seconds: float
    result: Any = None
    error_classification: str | None = None


@dataclass(frozen=True)
class SlotTimeoutV3:
    slot_id: int
    task_id: str
    worker: WorkerDiagnosticV3
    started_monotonic_ns: int
    timed_out_monotonic_ns: int


@dataclass(frozen=True)
class SlotWorkerExitV3:
    slot_id: int
    task_id: str | None
    worker: WorkerDiagnosticV3
    exitcode: int | None


@dataclass(frozen=True)
class SlotTaskV3:
    task_id: str
    payload: Any
    timeout_seconds: float


@dataclass(frozen=True)
class RollingSlotResultV3:
    completions: tuple[SlotCompletionV3 | SlotTimeoutV3 | SlotWorkerExitV3, ...]
    worker_affinity_bindings: tuple[Mapping[str, Any], ...]
    worker_intervals: tuple[Mapping[str, Any], ...]
    max_concurrent_active_slots: int
    mean_concurrent_active_slots: float
    slot_replacement_count: int
    timeout_kill_count: int


@dataclass
class _ActiveAttempt:
    task_id: str
    timeout_seconds: float
    dispatched_monotonic_ns: int
    deadline_monotonic_ns: int
    started_monotonic_ns: int | None = None


@dataclass
class _Slot:
    binding: WorkerBindingV3
    generation: int
    process: Any
    connection: Any
    diagnostic: WorkerDiagnosticV3
    active: _ActiveAttempt | None = None


def _worker_diagnostic(
    binding: WorkerBindingV3, generation: int,
) -> WorkerDiagnosticV3:
    try:
        affinity = tuple(sorted(int(cpu) for cpu in os.sched_getaffinity(0)))
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise PhysicalCoreSlotV3Error("worker cannot read back CPU affinity") from exc
    expected = (binding.logical_cpu_id,)
    if affinity != expected:
        raise PhysicalCoreSlotV3Error(
            f"worker affinity drift: expected {expected}, observed {affinity}"
        )
    return WorkerDiagnosticV3(
        os.getpid(), binding.logical_cpu_id,
        binding.physical_package_id, binding.physical_core_id,
        affinity, binding.slot_id, generation,
    )


def _slot_worker_entry(
    connection: Any,
    binding: WorkerBindingV3,
    generation: int,
    worker_callable: Callable[[Any, Any], Any],
    worker_state: Any,
) -> None:
    """Spawn entry point: bind once, validate around every attempt, loop."""

    try:
        os.sched_setaffinity(0, {binding.logical_cpu_id})
        diagnostic = _worker_diagnostic(binding, generation)
        connection.send(("READY", diagnostic))
    except BaseException as exc:
        try:
            connection.send((
                "FATAL", f"AFFINITY_STARTUP_FAILURE:{type(exc).__name__}:{exc}",
                traceback.format_exc(),
            ))
        finally:
            connection.close()
        return
    while True:
        try:
            command = connection.recv()
        except EOFError:
            break
        if command == ("SHUTDOWN",):
            try:
                connection.send(("STOPPED", diagnostic))
            finally:
                break
        if not isinstance(command, tuple) or len(command) != 3 or command[0] != "RUN":
            connection.send(("FATAL", "INVALID_PARENT_COMMAND", ""))
            break
        task_id, payload = command[1], command[2]
        if type(task_id) is not str or not task_id:
            connection.send(("FATAL", "INVALID_PARENT_TASK_ID", ""))
            break
        try:
            diagnostic = _worker_diagnostic(binding, generation)
        except BaseException as exc:
            connection.send((
                "FATAL", f"AFFINITY_DRIFT:{type(exc).__name__}:{exc}",
                traceback.format_exc(),
            ))
            break
        started = time.monotonic_ns()
        cpu_started = time.process_time()
        connection.send(("STARTED", task_id, diagnostic, started))
        try:
            result = worker_callable(worker_state, payload)
            diagnostic = _worker_diagnostic(binding, generation)
            finished = time.monotonic_ns()
            connection.send((
                "RESULT", task_id, diagnostic, started, finished,
                time.process_time() - cpu_started, result,
            ))
        except BaseException as exc:
            finished = time.monotonic_ns()
            try:
                diagnostic = _worker_diagnostic(binding, generation)
            except BaseException as affinity_exc:
                connection.send((
                    "FATAL",
                    f"AFFINITY_DRIFT:{type(affinity_exc).__name__}:{affinity_exc}",
                    traceback.format_exc(),
                ))
                break
            connection.send((
                "ERROR", task_id, diagnostic, started, finished,
                time.process_time() - cpu_started,
                f"{type(exc).__name__}:{exc}"[:500], traceback.format_exc(),
            ))
    connection.close()


def _validate_binding_set(bindings: Sequence[WorkerBindingV3]) -> None:
    if not bindings:
        raise PhysicalCoreSlotV3Error("at least one physical slot is required")
    if [row.slot_id for row in bindings] != list(range(len(bindings))):
        raise PhysicalCoreSlotV3Error("slot IDs must be contiguous from zero")
    logical = {row.logical_cpu_id for row in bindings}
    physical = {
        (row.physical_package_id, row.physical_core_id) for row in bindings
    }
    if len(logical) != len(bindings) or len(physical) != len(bindings):
        raise PhysicalCoreSlotV3Error(
            "each slot must bind a distinct logical and physical core"
        )


def _mean_concurrency(intervals: Sequence[tuple[int, int]]) -> tuple[int, float]:
    usable = [(start, finish) for start, finish in intervals if finish >= start]
    if not usable:
        return 0, 0.0
    events: list[tuple[int, int]] = []
    for start, finish in usable:
        events.append((start, 1))
        events.append((finish, -1))
    events.sort(key=lambda row: (row[0], row[1]))
    first = min(start for start, _finish in usable)
    last = max(finish for _start, finish in usable)
    active = maximum = 0
    previous = first
    area = 0
    for instant, delta in events:
        area += active * max(0, instant - previous)
        active += delta
        maximum = max(maximum, active)
        previous = instant
    duration = last - first
    return maximum, (0.0 if duration <= 0 else area / duration)


class PhysicalCoreSlotPoolV3:
    """Parent-owned collection of independently replaceable process slots."""

    def __init__(
        self,
        selected_cores: Sequence[PhysicalCoreV3],
        *,
        worker_callable: Callable[[Any, Any], Any],
        worker_state: Any = None,
        start_method: str = "spawn",
        startup_timeout_seconds: float = 15.0,
        terminate_grace_seconds: float = 2.0,
        kill_grace_seconds: float = 2.0,
    ) -> None:
        self.bindings = tuple(
            WorkerBindingV3.from_core(slot_id, core)
            for slot_id, core in enumerate(selected_cores)
        )
        _validate_binding_set(self.bindings)
        if not callable(worker_callable):
            raise PhysicalCoreSlotV3Error("slot worker callable is not callable")
        self.worker_callable = worker_callable
        self.worker_state = worker_state
        self.context = multiprocessing.get_context(start_method)
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.terminate_grace_seconds = float(terminate_grace_seconds)
        self.kill_grace_seconds = float(kill_grace_seconds)
        self.slots: dict[int, _Slot] = {}
        self.worker_affinity_bindings: list[dict[str, Any]] = []
        self.worker_intervals: list[dict[str, Any]] = []
        self.slot_replacement_count = 0
        self.timeout_kill_count = 0
        self._started = False

    @property
    def idle_slot_ids(self) -> tuple[int, ...]:
        return tuple(
            slot_id for slot_id, slot in sorted(self.slots.items())
            if slot.active is None
        )

    @property
    def active_slot_count(self) -> int:
        return sum(slot.active is not None for slot in self.slots.values())

    def _validate_diagnostic(
        self, slot: _Slot | None, binding: WorkerBindingV3,
        generation: int, diagnostic: Any,
    ) -> WorkerDiagnosticV3:
        if type(diagnostic) is not WorkerDiagnosticV3:
            raise PhysicalCoreSlotV3Error("worker diagnostic protocol mismatch")
        expected = {
            **binding.as_dict(),
            "worker_generation": generation,
            "affinity_mask": [binding.logical_cpu_id],
        }
        observed = diagnostic.as_dict()
        observed.pop("worker_pid")
        if observed != expected or diagnostic.worker_pid <= 0:
            raise PhysicalCoreSlotV3Error("worker affinity identity mismatch")
        if slot is not None and diagnostic.worker_pid != slot.process.pid:
            raise PhysicalCoreSlotV3Error("worker PID identity mismatch")
        return diagnostic

    def _spawn_slot(self, binding: WorkerBindingV3, generation: int) -> _Slot:
        parent, child = self.context.Pipe(duplex=True)
        process = self.context.Process(
            target=_slot_worker_entry,
            args=(
                child, binding, generation,
                self.worker_callable, self.worker_state,
            ),
            daemon=False,
        )
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        if not parent.poll(self.startup_timeout_seconds):
            parent.close()
            self._reap_process(process, force=True)
            raise PhysicalCoreSlotV3Error(
                f"physical slot {binding.slot_id} did not report startup affinity"
            )
        try:
            message = parent.recv()
        except (EOFError, OSError) as exc:
            parent.close()
            self._reap_process(process, force=True)
            raise PhysicalCoreSlotV3Error(
                f"physical slot {binding.slot_id} exited during startup"
            ) from exc
        if not isinstance(message, tuple) or not message or message[0] != "READY":
            parent.close()
            self._reap_process(process, force=True)
            detail = message[1] if isinstance(message, tuple) and len(message) > 1 else message
            raise PhysicalCoreSlotV3Error(
                f"physical slot {binding.slot_id} affinity startup failed: {detail}"
            )
        provisional = _Slot(binding, generation, process, parent, message[1])
        diagnostic = self._validate_diagnostic(
            provisional, binding, generation, message[1],
        )
        provisional.diagnostic = diagnostic
        self.worker_affinity_bindings.append(diagnostic.as_dict())
        return provisional

    def start(self) -> None:
        if self._started:
            raise PhysicalCoreSlotV3Error("physical slot pool already started")
        self._started = True
        try:
            for binding in self.bindings:
                self.slots[binding.slot_id] = self._spawn_slot(binding, 0)
        except BaseException:
            self.shutdown()
            raise

    def submit(
        self, slot_id: int, task_id: str, payload: Any,
        timeout_seconds: float,
    ) -> None:
        slot = self.slots.get(slot_id)
        if slot is None or slot.active is not None:
            raise PhysicalCoreSlotV3Error("submit requires an idle known slot")
        if type(task_id) is not str or not task_id:
            raise PhysicalCoreSlotV3Error("slot task ID must be non-empty")
        timeout = float(timeout_seconds)
        if timeout < 0:
            raise PhysicalCoreSlotV3Error("slot timeout must be non-negative")
        now = time.monotonic_ns()
        slot.active = _ActiveAttempt(
            task_id, timeout, now, now + int(timeout * 1_000_000_000),
        )
        try:
            slot.connection.send(("RUN", task_id, payload))
        except BaseException:
            slot.active = None
            raise

    def _next_expired(self, now_ns: int) -> _Slot | None:
        expired = [
            slot for slot in self.slots.values()
            if slot.active is not None
            and slot.active.deadline_monotonic_ns <= now_ns
        ]
        if not expired:
            return None
        return min(
            expired,
            key=lambda row: (row.active.deadline_monotonic_ns, row.binding.slot_id),
        )

    def poll(
        self, timeout_seconds: float | None = None,
    ) -> SlotStartedV3 | SlotCompletionV3 | SlotTimeoutV3 | SlotWorkerExitV3 | None:
        if not self._started:
            raise PhysicalCoreSlotV3Error("physical slot pool is not started")
        now_ns = time.monotonic_ns()
        expired = self._next_expired(now_ns)
        if expired is not None:
            active = expired.active
            assert active is not None
            started = active.started_monotonic_ns or active.dispatched_monotonic_ns
            return SlotTimeoutV3(
                expired.binding.slot_id, active.task_id, expired.diagnostic,
                started, now_ns,
            )
        for slot in self.slots.values():
            if slot.process.exitcode is not None:
                active = slot.active
                return SlotWorkerExitV3(
                    slot.binding.slot_id,
                    None if active is None else active.task_id,
                    slot.diagnostic, slot.process.exitcode,
                )
        connections = [slot.connection for slot in self.slots.values()]
        if not connections:
            return None
        wait_timeout = timeout_seconds
        deadlines = [
            slot.active.deadline_monotonic_ns
            for slot in self.slots.values() if slot.active is not None
        ]
        if deadlines:
            deadline_wait = max(
                0.0, (min(deadlines) - time.monotonic_ns()) / 1_000_000_000,
            )
            wait_timeout = (
                deadline_wait if wait_timeout is None
                else min(max(0.0, float(wait_timeout)), deadline_wait)
            )
        elif wait_timeout is not None:
            wait_timeout = max(0.0, float(wait_timeout))
        ready = wait_for_connections(connections, timeout=wait_timeout)
        if not ready:
            now_ns = time.monotonic_ns()
            expired = self._next_expired(now_ns)
            if expired is None:
                return None
            active = expired.active
            assert active is not None
            return SlotTimeoutV3(
                expired.binding.slot_id, active.task_id, expired.diagnostic,
                active.started_monotonic_ns or active.dispatched_monotonic_ns,
                now_ns,
            )
        connection = ready[0]
        slot = next(row for row in self.slots.values() if row.connection is connection)
        try:
            message = connection.recv()
        except (EOFError, OSError):
            active = slot.active
            return SlotWorkerExitV3(
                slot.binding.slot_id,
                None if active is None else active.task_id,
                slot.diagnostic, slot.process.exitcode,
            )
        if not isinstance(message, tuple) or not message:
            raise PhysicalCoreSlotV3Error("empty worker protocol message")
        kind = message[0]
        if kind == "STARTED" and len(message) == 4:
            active = slot.active
            if active is None or message[1] != active.task_id:
                raise PhysicalCoreSlotV3Error("worker start identity mismatch")
            diagnostic = self._validate_diagnostic(
                slot, slot.binding, slot.generation, message[2],
            )
            started = int(message[3])
            active.started_monotonic_ns = started
            active.deadline_monotonic_ns = started + int(
                active.timeout_seconds * 1_000_000_000
            )
            return SlotStartedV3(slot.binding.slot_id, active.task_id, diagnostic, started)
        if kind in {"RESULT", "ERROR"}:
            expected_length = 7 if kind == "RESULT" else 8
            if len(message) != expected_length or slot.active is None:
                raise PhysicalCoreSlotV3Error("worker completion protocol mismatch")
            active = slot.active
            if message[1] != active.task_id:
                raise PhysicalCoreSlotV3Error("worker completion identity mismatch")
            diagnostic = self._validate_diagnostic(
                slot, slot.binding, slot.generation, message[2],
            )
            started, finished = int(message[3]), int(message[4])
            if finished < started:
                raise PhysicalCoreSlotV3Error("worker interval is invalid")
            slot.active = None
            interval = {
                **diagnostic.as_dict(),
                "task_id": active.task_id,
                "attempt_started_monotonic_ns": started,
                "attempt_finished_monotonic_ns": finished,
            }
            self.worker_intervals.append(interval)
            if kind == "RESULT":
                return SlotCompletionV3(
                    slot.binding.slot_id, active.task_id, diagnostic,
                    started, finished, float(message[5]), result=message[6],
                )
            return SlotCompletionV3(
                slot.binding.slot_id, active.task_id, diagnostic,
                started, finished, float(message[5]),
                error_classification=str(message[6])[:500],
            )
        if kind == "FATAL":
            raise PhysicalCoreSlotV3Error(f"worker fatal protocol error: {message[1]}")
        if kind == "STOPPED":
            raise PhysicalCoreSlotV3Error("worker stopped during active polling")
        raise PhysicalCoreSlotV3Error(f"unknown worker protocol message: {kind}")

    def _reap_process(self, process: Any, *, force: bool) -> str:
        process.join(0.0)
        if process.exitcode is not None:
            status = "EXITED_NORMALLY"
        elif not force:
            process.join(self.terminate_grace_seconds)
            if process.exitcode is not None:
                status = "EXITED_NORMALLY"
            else:
                process.terminate()
                process.join(self.terminate_grace_seconds)
                status = "REAPED_AFTER_TERMINATE"
        else:
            process.terminate()
            process.join(self.terminate_grace_seconds)
            status = "REAPED_AFTER_TERMINATE"
        if process.exitcode is None:
            process.kill()
            process.join(self.kill_grace_seconds)
            status = "REAPED_AFTER_KILL"
        if process.exitcode is None:
            raise PhysicalCoreSlotV3Error("worker remained unreaped after kill")
        process.close()
        return status

    def replace(self, slot_id: int, *, timeout_kill: bool = False) -> WorkerDiagnosticV3:
        slot = self.slots.get(slot_id)
        if slot is None:
            raise PhysicalCoreSlotV3Error("cannot replace an unknown slot")
        try:
            slot.connection.close()
        finally:
            self._reap_process(slot.process, force=True)
        generation = slot.generation + 1
        replacement = self._spawn_slot(slot.binding, generation)
        self.slots[slot_id] = replacement
        self.slot_replacement_count += 1
        if timeout_kill:
            self.timeout_kill_count += 1
        return replacement.diagnostic

    def shutdown(self) -> None:
        slots = tuple(self.slots.values())
        for slot in slots:
            if slot.process.exitcode is None and slot.active is None:
                try:
                    slot.connection.send(("SHUTDOWN",))
                except (BrokenPipeError, EOFError, OSError):
                    pass
        for slot in slots:
            try:
                slot.connection.close()
            finally:
                try:
                    self._reap_process(slot.process, force=slot.active is not None)
                except (PhysicalCoreSlotV3Error, ValueError):
                    if slot.process.exitcode is None:
                        raise
        self.slots.clear()
        self._started = False

    def run_rolling(self, tasks: Sequence[SlotTaskV3]) -> RollingSlotResultV3:
        """Execute a synthetic/generic rolling workload without batch barriers."""

        pending = deque(tasks)
        completions: list[SlotCompletionV3 | SlotTimeoutV3 | SlotWorkerExitV3] = []
        intervals: list[tuple[int, int]] = []
        self.start()
        try:
            while pending or self.active_slot_count:
                for slot_id in self.idle_slot_ids:
                    if not pending:
                        break
                    task = pending.popleft()
                    self.submit(
                        slot_id, task.task_id, task.payload, task.timeout_seconds,
                    )
                event = self.poll()
                if event is None or isinstance(event, SlotStartedV3):
                    continue
                completions.append(event)
                if isinstance(event, SlotCompletionV3):
                    intervals.append((
                        event.started_monotonic_ns, event.finished_monotonic_ns,
                    ))
                elif isinstance(event, SlotTimeoutV3):
                    intervals.append((
                        event.started_monotonic_ns, event.timed_out_monotonic_ns,
                    ))
                    self.replace(event.slot_id, timeout_kill=True)
                else:
                    self.replace(event.slot_id)
            maximum, mean = _mean_concurrency(intervals)
            return RollingSlotResultV3(
                tuple(completions), tuple(self.worker_affinity_bindings),
                tuple(self.worker_intervals), maximum, mean,
                self.slot_replacement_count, self.timeout_kill_count,
            )
        finally:
            self.shutdown()


__all__ = [
    "CPUTopologyV3", "PHYSICAL_CORE_EXECUTION_BACKEND_V3",
    "PHYSICAL_CORE_SELECTION_POLICY_V3", "PhysicalCoreSlotPoolV3",
    "PhysicalCoreSlotV3Error", "PhysicalCoreV3", "RollingSlotResultV3",
    "SlotCompletionV3", "SlotStartedV3", "SlotTaskV3", "SlotTimeoutV3",
    "SlotWorkerExitV3", "WorkerBindingV3", "WorkerDiagnosticV3",
    "discover_cpu_topology_v3",
]
