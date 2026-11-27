"""Exact integer-energy reference replay for the HEARTS-1C comparison.

The planner is a sequential, zero-static-power specialization of HEARTS,
not a reproduction of its DAG execution/core-count search. Time slots are
[r, d); all allocations (including earlier-window reservations) use the
same finite-battery recurrence as the same-slot PARTsim backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import lcm
from time import perf_counter_ns
from typing import Sequence

METHODS = ("HEARTS-1C", "GLOBAL-AB", "GLOBAL-AN", "PARTITIONED-AB")


@dataclass(frozen=True)
class Task:
    name: str
    C: int
    T: int
    energy: Fraction
    # Position in the task sequence is the fixed priority, highest first.


@dataclass(frozen=True)
class Job:
    task: int
    release: int
    deadline: int


@dataclass(frozen=True)
class Workload:
    tasks: tuple[Task, ...]
    processors: int
    harvest: tuple[int, ...]
    initial: int
    capacity: int
    cost: tuple[int, ...]
    scale: int
    jobs: tuple[Job, ...]
    by_task: tuple[tuple[int, ...], ...]
    release_cutoff: int

    @property
    def horizon(self) -> int:
        return len(self.harvest)

    @classmethod
    def create(cls, tasks: Sequence[Task], processors: int,
               harvest: Sequence[Fraction], initial: Fraction,
               capacity: Fraction, *, release_cutoff: int | None = None) -> Workload:
        tasks = tuple(tasks)
        if not tasks or processors < 1 or any(not 0 < t.C <= t.T for t in tasks):
            raise ValueError("positive processors and implicit-deadline sequential tasks required")
        if len({t.name for t in tasks}) != len(tasks):
            raise ValueError("task names must be unique")
        if any(a.T > b.T for a, b in zip(tasks, tasks[1:])):
            raise ValueError("tasks must be in RM priority order")
        harvest = tuple(map(Fraction, harvest))
        initial, capacity = Fraction(initial), Fraction(capacity)
        energy = tuple(Fraction(t.energy) for t in tasks)
        if not 0 <= initial <= capacity or any(x < 0 for x in (*energy, *harvest)):
            raise ValueError("invalid energy inputs")
        cutoff = len(harvest) - max(t.T for t in tasks) if release_cutoff is None else release_cutoff
        if not 0 < cutoff <= len(harvest) or any(
            ((cutoff - 1) // t.T) * t.T + t.T >= len(harvest) for t in tasks
        ):
            raise ValueError("all released jobs must have deadlines strictly before the horizon")
        scale = lcm(*(x.denominator for x in (initial, capacity, *energy, *harvest)))

        def integer(x: Fraction) -> int:
            return x.numerator * (scale // x.denominator)

        jobs, by_task = [], []
        for i, task in enumerate(tasks):
            indices = []
            for release in range(0, cutoff, task.T):
                indices.append(len(jobs))
                jobs.append(Job(i, release, release + task.T))
            by_task.append(tuple(indices))
        return cls(tasks, processors, tuple(map(integer, harvest)), integer(initial),
                   integer(capacity), tuple(map(integer, energy)), scale, tuple(jobs),
                   tuple(by_task), cutoff)


def partition_tasks(tasks: Sequence[Task], processors: int) -> tuple[int, ...] | None:
    """Complete CPU-only RM partition search, independent of the energy trace.

    With implicit deadlines, adding a lower-priority task cannot change the
    higher-priority tasks' uniprocessor RTA. Symmetric cores are interchangeable.
    """
    groups: list[list[int]] = [[] for _ in range(processors)]
    loads = [Fraction(0)] * processors
    mapping = [-1] * len(tasks)
    util = [Fraction(t.C, t.T) for t in tasks]

    def cpu_feasible(i: int, core: int) -> bool:
        task = tasks[i]
        r = task.C
        while r <= task.T:
            nxt = task.C + sum(
                ((r + tasks[j].T - 1) // tasks[j].T) * tasks[j].C for j in groups[core]
            )
            if nxt == r:
                return True
            r = nxt
        return False

    def search(i: int) -> bool:
        if i == len(tasks):
            return True
        seen = set()
        for core in sorted(range(processors), key=lambda k: (-loads[k], k)):
            group = tuple(groups[core])
            if group in seen:
                continue
            seen.add(group)
            if loads[core] + util[i] > 1 or not cpu_feasible(i, core):
                continue
            groups[core].append(i)
            loads[core] += util[i]
            mapping[i] = core
            if search(i + 1):
                return True
            groups[core].pop()
            loads[core] -= util[i]
            mapping[i] = -1
        return False

    return tuple(mapping) if search(0) else None


class WindowLedger:
    """Keep exact forward battery and minimal suffix battery requirements.

    needed[s] is the minimal battery BEFORE harvesting at slot s that can pay
    all already committed usage in [s, end). A finite cap also applies between
    slots, never before the current slot's debit.
    """
    def __init__(self, supply: Sequence[int], usage: Sequence[int], initial: int, capacity: int):
        self.supply, self.used = supply, list(usage)
        self.capacity = capacity
        self.before = [initial] + [0] * len(supply)
        self.needed = [0] * (len(supply) + 1)
        for s in range(len(supply)):
            available = self.before[s] + supply[s] - self.used[s]
            if available < 0:
                raise ValueError("previously committed window is infeasible")
            self.before[s + 1] = min(capacity, available)
        for s in range(len(supply) - 1, -1, -1):
            self.needed[s] = max(0, self.needed[s + 1] + self.used[s] - supply[s])
            if self.needed[s] > capacity:
                raise ValueError("previously committed suffix exceeds capacity")

    def place(self, s: int, cost: int) -> bool:
        if self.before[s] + self.supply[s] - self.used[s] - cost < self.needed[s + 1]:
            return False
        self.used[s] += cost
        for i in range(s, len(self.used)):
            updated = min(self.capacity, self.before[i] + self.supply[i] - self.used[i])
            if updated == self.before[i + 1]:
                break
            self.before[i + 1] = updated
        for i in range(s, -1, -1):
            updated = max(0, self.needed[i + 1] + self.used[i] - self.supply[i])
            if updated == self.needed[i]:
                break
            self.needed[i] = updated
        return True


def _plan_window(work: Workload, start: int, end: int, battery: int,
                 reserved: Sequence[Sequence[int]], new_jobs: Sequence[int],
                 late: bool) -> list[list[int]] | None:
    slots = [list(slot) for slot in reserved]
    used = [sum(work.cost[work.jobs[j].task] for j in slot) for slot in slots]
    ledger = WindowLedger(work.harvest[start:end], used, battery, work.capacity)
    for j in new_jobs:
        job = work.jobs[j]
        if not start <= job.release < job.deadline <= end:
            raise ValueError("job is outside its planning window")
        candidates = range(job.deadline - 1, job.release - 1, -1) if late else range(job.release, job.deadline)
        remaining = work.tasks[job.task].C
        # Each task owns one distinct core; its implicit-deadline jobs do not overlap.
        for s in candidates:
            if ledger.place(s - start, work.cost[job.task]):
                slots[s - start].append(j)
                remaining -= 1
                if remaining == 0:
                    break
        if remaining:
            return None
    return slots


def hearts_plan(work: Workload) -> tuple[list[list[int]] | None, dict]:
    """PALAP then PASAP on the same fixed reservation state per overlapping window."""
    if len(work.tasks) > work.processors:
        raise ValueError("HEARTS-1C requires one dedicated core per task")
    width = max(task.T for task in work.tasks)
    start, end = 0, min(width, work.horizon)
    slots: list[list[int]] = [[] for _ in work.harvest]
    next_job = [0] * len(work.tasks)
    battery = work.initial
    stats = dict(windows=0, palap_success=0, pasap_success=0,
                 failed_window=None, planning_ns=0)
    while start < work.horizon:
        pending, advanced = [], next_job.copy()
        for i, jobs in enumerate(work.by_task):
            while advanced[i] < len(jobs) and work.jobs[jobs[advanced[i]]].deadline <= end:
                pending.append(jobs[advanced[i]])
                advanced[i] += 1
        stats["windows"] += 1
        began = perf_counter_ns()
        planned = _plan_window(work, start, end, battery, slots[start:end], pending, True)
        if planned is not None:
            stats["palap_success"] += 1
        else:
            planned = _plan_window(work, start, end, battery, slots[start:end], pending, False)
            if planned is not None:
                stats["pasap_success"] += 1
        stats["planning_ns"] += perf_counter_ns() - began
        if planned is None:
            stats["failed_window"] = [start, end]
            return None, stats
        slots[start:end] = planned
        next_job = advanced
        # Commit only until the earliest next unplanned release. Everything
        # beyond that boundary is retained as a reservation in the next window.
        releases = [work.jobs[js[next_job[i]]].release
                    for i, js in enumerate(work.by_task) if next_job[i] < len(js)]
        if not releases:
            # All remaining work is committed; no empty planning window is needed.
            # The independent evaluator still replays the full horizon, including
            # the idle tail and all energy collected during it.
            return slots, stats
        if end == work.horizon:
            raise ValueError("not all eligible jobs were planned")
        boundary = min(end, *releases)
        if boundary <= start:
            raise ValueError("window did not advance")
        for s in range(start, boundary):
            paid = sum(work.cost[work.jobs[j].task] for j in slots[s])
            if battery + work.harvest[s] < paid:
                raise ValueError("committed plan overdraws energy")
            battery = min(work.capacity, battery + work.harvest[s] - paid)
        start, end = boundary, min(work.horizon, end + width)
    return slots, stats


def online_plan(work: Workload, *, blocking: bool,
                mapping: tuple[int, ...] | None = None) -> list[list[int]]:
    """Global RM or partitioned RM, with one shared battery.

    Partitioned AB selects the highest-priority ready job on each core,
    sorts those heads by global priority, and applies the same AB prefix rule.
    A lower-priority job cannot bypass its local head.
    """
    release_at: dict[int, list[int]] = {}
    for j, job in enumerate(work.jobs):
        release_at.setdefault(job.release, []).append(j)
    active: dict[int, int] = {}
    remain = [work.tasks[j.task].C for j in work.jobs]
    battery = work.initial
    slots = []
    for s, h in enumerate(work.harvest):
        active = {i: j for i, j in active.items() if work.jobs[j].deadline > s and remain[j]}
        for j in release_at.get(s, ()):
            active[work.jobs[j].task] = j
        candidates = []
        seen = set()
        for i in sorted(active):
            if mapping is not None:
                core = mapping[i]
                if core in seen:
                    continue
                seen.add(core)
            candidates.append(active[i])
        available = battery + h
        selected = []
        for j in candidates:
            if len(selected) == work.processors:
                break
            cost = work.cost[work.jobs[j].task]
            if cost > available:
                if blocking:
                    break
                continue
            available -= cost
            remain[j] -= 1
            selected.append(j)
        slots.append(selected)
        battery = min(work.capacity, available)
    return slots


def evaluate(work: Workload, slots: Sequence[Sequence[int]],
             mapping: tuple[int, ...] | None = None, *, details: bool = False) -> dict:
    """Independent validation and metrics for a complete execution trajectory."""
    if len(slots) != work.horizon:
        raise ValueError("trajectory length mismatch")
    executed = [0] * len(work.jobs)
    completion: dict[int, int] = {}
    battery = work.initial
    consumed = spilled = 0
    first_miss = None
    trace = [] if details else None
    for s, slot in enumerate(slots):
        if len(slot) > work.processors or len(set(slot)) != len(slot):
            raise ValueError("processor capacity violation")
        cores, tasks = set(), set()
        used = 0
        for j in slot:
            if not 0 <= j < len(work.jobs):
                raise ValueError("unknown job")
            job = work.jobs[j]
            i = job.task
            if not job.release <= s < job.deadline or i in tasks:
                raise ValueError("job outside release/deadline or parallel execution")
            tasks.add(i)
            if mapping is not None:
                if not 0 <= mapping[i] < work.processors or mapping[i] in cores:
                    raise ValueError("dedicated/partitioned core collision")
                cores.add(mapping[i])
            executed[j] += 1
            if executed[j] > work.tasks[i].C:
                raise ValueError("job executed beyond demand")
            if executed[j] == work.tasks[i].C:
                completion[j] = s + 1
            used += work.cost[i]
        surplus = battery + work.harvest[s] - used
        if surplus < 0:
            raise ValueError("energy overdraw")
        spill = max(0, surplus - work.capacity)
        battery = surplus - spill
        consumed += used
        spilled += spill
        if trace is not None:
            trace.append({"tick": s, "jobs": list(slot), "battery": battery, "spill": spill})
    offered = sum(work.harvest)
    if work.initial + offered != consumed + spilled + battery:
        raise ValueError("energy conservation violated")
    misses = [j for j in range(len(work.jobs)) if j not in completion]
    if misses:
        j = min(misses, key=lambda n: (work.jobs[n].deadline, work.jobs[n].task))
        first_miss = {"time": work.jobs[j].deadline, "task": work.tasks[work.jobs[j].task].name,
                      "remaining": work.tasks[work.jobs[j].task].C - executed[j]}
    responses = [finish - work.jobs[j].release for j, finish in completion.items()]

    def joules(value: int) -> str:
        return str(Fraction(value, work.scale))

    result = dict(wholepass=not misses, failure_reason="DEADLINE_MISS" if misses else None,
                  jobs_total=len(work.jobs), deadline_misses=len(misses),
                  deadline_miss_rate=len(misses) / len(work.jobs),
                  completed_jobs=len(completion),
                  mean_response=sum(responses) / len(responses) if responses else None,
                  first_miss=first_miss, offered_j=joules(offered), consumed_j=joules(consumed),
                  spilled_j=joules(spilled), final_battery_j=joules(battery))
    if trace is not None:
        result["trace"] = trace
    return result


def run_method(work: Workload, method: str, *, details: bool = False) -> dict:
    if method not in METHODS:
        raise ValueError(f"unknown method {method}")
    started = perf_counter_ns()
    mapping = None
    stats = {}
    reason = None
    if method == "HEARTS-1C":
        mapping = tuple(range(len(work.tasks)))
        slots, stats = hearts_plan(work)
        if slots is None:
            reason = "NO_WINDOW_PLAN"
    elif method == "PARTITIONED-AB":
        partition_started = perf_counter_ns()
        mapping = partition_tasks(work.tasks, work.processors)
        stats["partition_ns"] = perf_counter_ns() - partition_started
        slots = online_plan(work, blocking=True, mapping=mapping) if mapping is not None else None
        if slots is None:
            reason = "NO_CPU_PARTITION"
    else:
        slots = online_plan(work, blocking=method == "GLOBAL-AB")
    schedule_ns = perf_counter_ns() - started
    if slots is None:
        result = dict(wholepass=False, failure_reason=reason, jobs_total=len(work.jobs),
                      deadline_misses=None, deadline_miss_rate=None, completed_jobs=None,
                      mean_response=None, first_miss=None, offered_j=str(Fraction(sum(work.harvest), work.scale)),
                      consumed_j=None, spilled_j=None, final_battery_j=None)
    else:
        result = evaluate(work, slots, mapping, details=details)
        if method == "HEARTS-1C" and not result["wholepass"]:
            raise ValueError("successful planning did not complete every job")
    return dict(method=method, schedule_ns=schedule_ns,
                mapping=list(mapping) if mapping is not None else None, **stats, **result)
