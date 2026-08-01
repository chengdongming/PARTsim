from __future__ import annotations

import multiprocessing
import os
import time

import pytest

from experiments.v9_3.rta4_physical_core_slots_v3 import (
    PhysicalCoreSlotPoolV3, PhysicalCoreSlotV3Error, PhysicalCoreV3,
    SlotCompletionV3,
    SlotStartedV3, SlotTaskV3, SlotTimeoutV3, discover_cpu_topology_v3,
)


def _sleep_work(_state, seconds):
    time.sleep(float(seconds))
    return {"slept": float(seconds), "pid": os.getpid()}


def _cpu_work(_state, iterations):
    value = 1
    for index in range(int(iterations)):
        value = (value * 6364136223846793005 + index + 1) & ((1 << 64) - 1)
    return value


def _virtual_topology():
    pairs = {
        8: (1, 2), 9: (1, 2),
        2: (0, 1), 3: (0, 1),
        6: (0, 3), 7: (0, 3),
        4: (0, 2), 5: (0, 2),
    }
    return discover_cpu_topology_v3(
        allowed_logical_cpus=(9, 8, 7, 6, 5, 4, 3, 2),
        topology_reader=pairs.__getitem__,
    )


def test_topology_reads_allowed_set_groups_smt_and_selects_deterministically():
    topology = _virtual_topology()
    assert topology.allowed_logical_cpus == (2, 3, 4, 5, 6, 7, 8, 9)
    assert topology.physical_core_count == 4
    assert [row.logical_cpu_id for row in topology.physical_cores] == [2, 4, 6, 8]
    assert [row.allowed_siblings for row in topology.physical_cores] == [
        (2, 3), (4, 5), (6, 7), (8, 9),
    ]
    assert _virtual_topology().topology_fingerprint == topology.topology_fingerprint


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_worker_count_selects_exactly_that_many_physical_cores(workers):
    selected = _virtual_topology().select(workers)
    assert len(selected) == workers
    assert len({
        (row.physical_package_id, row.physical_core_id) for row in selected
    }) == workers


def test_worker_count_above_allowed_physical_cores_fails_without_clamp():
    with pytest.raises(PhysicalCoreSlotV3Error, match="requested 5 physical"):
        _virtual_topology().select(5)


def test_missing_or_invalid_topology_fails_closed():
    with pytest.raises(PhysicalCoreSlotV3Error, match="cannot read physical"):
        discover_cpu_topology_v3(
            allowed_logical_cpus=(1,),
            topology_reader=lambda _cpu: (_ for _ in ()).throw(OSError("missing")),
        )
    with pytest.raises(PhysicalCoreSlotV3Error, match="empty"):
        discover_cpu_topology_v3(
            allowed_logical_cpus=(), topology_reader=lambda _cpu: (0, 0),
        )


def test_worker_affinity_failure_refuses_slot_startup():
    impossible = PhysicalCoreV3(0, 0, 999_999, (999_999,))
    pool = PhysicalCoreSlotPoolV3(
        (impossible,), worker_callable=_sleep_work,
    )
    with pytest.raises(PhysicalCoreSlotV3Error, match="affinity startup failed"):
        pool.start()


def test_every_long_lived_worker_is_pinned_to_a_distinct_physical_core():
    topology = discover_cpu_topology_v3()
    workers = min(4, topology.physical_core_count)
    pool = PhysicalCoreSlotPoolV3(
        topology.select(workers), worker_callable=_sleep_work,
    )
    result = pool.run_rolling(tuple(
        SlotTaskV3(f"task-{index}", 0.02, 2.0)
        for index in range(workers * 2)
    ))
    initial = [
        row for row in result.worker_affinity_bindings
        if row["worker_generation"] == 0
    ]
    assert len(initial) == workers
    assert all(row["affinity_mask"] == [row["logical_cpu_id"]] for row in initial)
    assert len({
        (row["physical_package_id"], row["physical_core_id"])
        for row in initial
    }) == workers
    assert len({row["worker_pid"] for row in initial}) == workers


def test_rolling_queue_sustains_overlap_without_a_fixed_batch_barrier():
    topology = discover_cpu_topology_v3()
    workers = min(4, topology.physical_core_count)
    if workers < 2:
        pytest.skip("rolling concurrency requires at least two physical cores")
    durations = [0.02] + [0.18] * (workers - 1) + [0.035] * (32 - workers)
    result = PhysicalCoreSlotPoolV3(
        topology.select(workers), worker_callable=_sleep_work,
    ).run_rolling(tuple(
        SlotTaskV3(f"task-{index:02d}", duration, 3.0)
        for index, duration in enumerate(durations)
    ))
    assert len(result.completions) == 32
    assert result.max_concurrent_active_slots == workers
    assert result.mean_concurrent_active_slots > workers * 0.75
    starts = {
        row["task_id"]: row["attempt_started_monotonic_ns"]
        for row in result.worker_intervals
    }
    finishes = {
        row["task_id"]: row["attempt_finished_monotonic_ns"]
        for row in result.worker_intervals
    }
    assert starts[f"task-{workers:02d}"] < finishes["task-01"]


def test_straggler_does_not_block_other_slots_from_later_work():
    topology = discover_cpu_topology_v3()
    workers = min(4, topology.physical_core_count)
    if workers < 2:
        pytest.skip("straggler test requires at least two physical cores")
    tasks = [SlotTaskV3("slow", 0.35, 2.0)]
    tasks.extend(
        SlotTaskV3(f"fast-{index:02d}", 0.025, 2.0)
        for index in range(15)
    )
    result = PhysicalCoreSlotPoolV3(
        topology.select(workers), worker_callable=_sleep_work,
    ).run_rolling(tuple(tasks))
    intervals = {row["task_id"]: row for row in result.worker_intervals}
    slow_finish = intervals["slow"]["attempt_finished_monotonic_ns"]
    assert intervals[f"fast-{workers + 2:02d}"][
        "attempt_finished_monotonic_ns"
    ] < slow_finish


def test_timeout_terminates_reaps_and_replaces_only_same_core_slot():
    topology = discover_cpu_topology_v3()
    workers = min(2, topology.physical_core_count)
    before = {child.pid for child in multiprocessing.active_children()}
    pool = PhysicalCoreSlotPoolV3(
        topology.select(workers), worker_callable=_sleep_work,
    )
    pool.start()
    old = pool.slots[0].diagnostic
    try:
        pool.submit(0, "timeout", 1.0, 0.08)
        if workers > 1:
            pool.submit(1, "other", 0.02, 1.0)
        timeout = None
        other_finished = False
        while timeout is None:
            event = pool.poll()
            if isinstance(event, SlotStartedV3):
                continue
            if isinstance(event, SlotTimeoutV3):
                timeout = event
            elif isinstance(event, SlotCompletionV3):
                other_finished = event.task_id == "other" or other_finished
        replacement = pool.replace(timeout.slot_id, timeout_kill=True)
        assert replacement.logical_cpu_id == old.logical_cpu_id
        assert replacement.physical_package_id == old.physical_package_id
        assert replacement.physical_core_id == old.physical_core_id
        assert replacement.worker_pid != old.worker_pid
        assert replacement.worker_generation == old.worker_generation + 1
        pool.submit(0, "replacement-success", 0.01, 1.0)
        replacement_finished = False
        while not replacement_finished or (workers > 1 and not other_finished):
            event = pool.poll()
            if isinstance(event, SlotCompletionV3):
                replacement_finished |= event.task_id == "replacement-success"
                other_finished |= event.task_id == "other"
        assert pool.slot_replacement_count == 1
        assert pool.timeout_kill_count == 1
    finally:
        pool.shutdown()
    after = {child.pid for child in multiprocessing.active_children()}
    assert not (after - before)


def test_worker_count_starts_neither_more_nor_fewer_slots():
    topology = discover_cpu_topology_v3()
    for workers in range(1, min(4, topology.physical_core_count) + 1):
        pool = PhysicalCoreSlotPoolV3(
            topology.select(workers), worker_callable=_cpu_work,
        )
        pool.start()
        try:
            assert len(pool.slots) == workers
            assert len({slot.process.pid for slot in pool.slots.values()}) == workers
        finally:
            pool.shutdown()
