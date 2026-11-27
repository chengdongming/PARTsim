"""Bounded, fail-closed helpers for deterministic preparation phases."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from typing import Any, Callable, Iterable, TypeVar
import time


T = TypeVar("T")
R = TypeVar("R")


def validate_workers(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def run_prepare_jobs(
    jobs: Iterable[T], worker: Callable[[T], R], *, workers: int,
    phase: str, key: Callable[[R], Any],
) -> dict[Any, R]:
    """Run pure preparation jobs and return results keyed by immutable identity.

    The caller is responsible for canonical ordering and shared-state commits.
    A worker exception aborts the phase; no partial result is returned.
    """
    validate_workers(workers, "prepare-workers")
    items = list(jobs)
    if not items:
        print(f"phase=prepare stage={phase} elapsed_seconds=0.0 workers={workers} items=0", flush=True)
        return {}
    started = time.perf_counter()
    interval = max(10, min(50, len(items) // 10 or 10))
    completed = 0
    prepared: dict[Any, R] = {}

    def report() -> None:
        elapsed = max(0.0, time.perf_counter() - started)
        rate = completed / elapsed * 60.0 if elapsed else 0.0
        print(
            f"{phase}: completed={completed} total={len(items)} "
            f"elapsed_seconds={elapsed:.1f} throughput_items_per_min={rate:.2f} "
            f"prepare_workers={workers}", flush=True,
        )

    if workers == 1:
        for item in items:
            result = worker(item)
            result_key = key(result)
            if result_key in prepared:
                raise RuntimeError(f"{phase} produced duplicate key {result_key!r}")
            prepared[result_key] = result
            completed += 1
            if completed % interval == 0 or completed == len(items):
                report()
    else:
        context = multiprocessing.get_context("fork")
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=context)
        futures = {}
        try:
            futures = {executor.submit(worker, item): item for item in items}
            for future in as_completed(futures):
                result = future.result()
                result_key = key(result)
                if result_key in prepared:
                    raise RuntimeError(f"{phase} produced duplicate key {result_key!r}")
                prepared[result_key] = result
                completed += 1
                if completed % interval == 0 or completed == len(items):
                    report()
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except TypeError:  # Python versions before the cancel_futures option.
                executor.shutdown(wait=True)

    elapsed = max(0.0, time.perf_counter() - started)
    print(
        f"phase=prepare stage={phase} elapsed_seconds={elapsed:.3f} "
        f"workers={workers} items={len(items)}", flush=True,
    )
    if len(prepared) != len(items):
        raise RuntimeError(f"{phase} did not produce all preparation results")
    return prepared


def run_independent_jobs(
    jobs: Iterable[T], worker: Callable[[T], R], *, workers: int,
) -> list[R]:
    """Run independent non-mutating jobs, preserving input order."""
    validate_workers(workers, "analysis-workers")
    items = list(jobs)
    if workers == 1 or len(items) <= 1:
        return [worker(item) for item in items]
    context = multiprocessing.get_context("fork")
    executor = ProcessPoolExecutor(
        max_workers=min(workers, len(items)), mp_context=context,
    )
    futures = [executor.submit(worker, item) for item in items]
    try:
        return [future.result() for future in futures]
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        try:
            executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=True)
