#!/usr/bin/env python3
"""Non-formal throughput diagnostic for pinned RTA4 physical worker slots."""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_physical_core_slots_v3 import (
    PhysicalCoreSlotPoolV3, SlotCompletionV3, SlotTaskV3,
    discover_cpu_topology_v3,
)


def _synthetic_cpu(_state: Any, seconds: float) -> dict[str, Any]:
    deadline = time.perf_counter() + max(0.0001, float(seconds))
    value = 0x9E3779B97F4A7C15
    iterations = 0
    while time.perf_counter() < deadline:
        value ^= (value << 13) & ((1 << 64) - 1)
        value ^= value >> 7
        value ^= (value << 17) & ((1 << 64) - 1)
        iterations += 1
    return {"digest": f"{value:016x}", "iterations": iterations}


def _real_rta_state() -> Any:
    import asap_block_rta_v9_3 as core
    import asap_block_rta_v9_3_taskset as taskset
    from experiments.v9_3 import exact_energy

    tasks = (
        core.V93Task("t0", 2, 11, 13, Fraction(2)),
        core.V93Task("t1", 3, 17, 19, Fraction(3)),
        core.V93Task("t2", 5, 23, 29, Fraction(4)),
        core.V93Task("t3", 7, 31, 37, Fraction(5)),
        core.V93Task("t4", 11, 43, 47, Fraction(6)),
        core.V93Task("t5", 13, 53, 59, Fraction(7)),
    )
    e0 = Fraction(10_000)
    beta = tuple(Fraction(0) for _ in range(max(row.deadline for row in tasks)))
    exact_id = exact_energy.exact_input_identity(
        task_powers=((row.name, row.power) for row in tasks),
        e0=e0, service_prefix=beta,
    )
    dependency = taskset.DependencyContext(
        taskset_identity="diagnostic-frozen-taskset",
        task_definitions_identity="diagnostic-frozen-definitions",
        priority_order_identity="diagnostic-rm-priority",
        e0_canonical_identity="10000",
        service_curve_identity="diagnostic-zero-service",
        power_vector_identity="diagnostic-frozen-power",
        numerical_mode="EXACT_RATIONAL", numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=(
            taskset.FIXED_CARRY_IN_INTERFACE_SHA256
        ),
        formal_contract_identity="NON_FORMAL_PERFORMANCE_DIAGNOSTIC",
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity=exact_id, float_decision_path=False,
    )
    return taskset.TasksetAnalysisInput(
        tasks=tasks, processors=4, e0=e0, beta=beta,
        dependency_context=dependency, timeout_seconds=None,
    )


def _real_rta(state: Any, seconds: float) -> dict[str, Any]:
    import asap_block_rta_v9_3_methods as methods
    import asap_block_rta_v9_3_taskset as taskset

    deadline = time.perf_counter() + max(0.0001, float(seconds))
    repeats = 0
    canonical = None
    while repeats == 0 or time.perf_counter() < deadline:
        result = taskset.analyze_method_taskset_v9_3(
            analysis_id="NON_FORMAL_RTA4_PHYSICAL_SLOT_BENCHMARK",
            method_spec=methods.V93MethodId.SEQ_THETA_SEQ,
            analysis_input=state,
        )
        current = (
            result.solver_status.value,
            result.analysis_certification_status.value,
            bool(result.taskset_proven),
            tuple(
                None if row.candidate_response_time is None
                else int(row.candidate_response_time)
                for row in result.task_results
            ),
        )
        if canonical is None:
            canonical = current
        elif canonical != current:
            raise RuntimeError("real RTA diagnostic result drift")
        repeats += 1
    digest = hashlib.sha256(repr(canonical).encode("utf-8")).hexdigest()
    return {"result_digest": digest, "repetitions": repeats}


def _run(
    worker_count: int, *, mode: str, duration: float, records: int,
) -> dict[str, Any]:
    topology = discover_cpu_topology_v3()
    selected = topology.select(worker_count)
    callable_ = _synthetic_cpu if mode == "synthetic" else _real_rta
    state = None if mode == "synthetic" else _real_rta_state()
    task_seconds = max(0.001, duration / records)
    tasks = tuple(
        SlotTaskV3(f"diagnostic-{index:06d}", task_seconds, max(30.0, duration * 2))
        for index in range(records)
    )
    before = {child.pid for child in multiprocessing.active_children()}
    pool = PhysicalCoreSlotPoolV3(
        selected, worker_callable=callable_, worker_state=state,
    )
    started = time.perf_counter()
    result = pool.run_rolling(tasks)
    elapsed = time.perf_counter() - started
    after = {child.pid for child in multiprocessing.active_children()}
    completed = [
        row for row in result.completions if isinstance(row, SlotCompletionV3)
        and row.error_classification is None
    ]
    cpu_seconds = sum(row.runtime_cpu_seconds for row in completed)
    digests = sorted(
        str(row.result.get("result_digest", "")) for row in completed
        if isinstance(row.result, dict) and "result_digest" in row.result
    )
    return {
        "diagnostic_only": True,
        "formal_results_written": False,
        "mode": mode,
        "worker_count": worker_count,
        "allowed_logical_cpus": list(topology.allowed_logical_cpus),
        "available_physical_cores": topology.physical_core_count,
        "selected_physical_cores": [row.as_dict() for row in selected],
        "worker_affinity": list(result.worker_affinity_bindings),
        "records_requested": records,
        "records_completed": len(completed),
        "elapsed_time_seconds": elapsed,
        "throughput_records_per_second": len(completed) / elapsed,
        "cpu_seconds": cpu_seconds,
        "wall_seconds": elapsed,
        "effective_cores_used": cpu_seconds / elapsed,
        "mean_active_slots": result.mean_concurrent_active_slots,
        "max_active_slots": result.max_concurrent_active_slots,
        "slot_utilization": (
            result.mean_concurrent_active_slots / worker_count
        ),
        "slot_replacement_count": result.slot_replacement_count,
        "timeout_kill_count": result.timeout_kill_count,
        "process_leak_count": len(after - before),
        "real_rta_result_digests": sorted(set(digests)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-count", type=int, required=True)
    parser.add_argument("--duration", type=float, default=4.0)
    parser.add_argument("--records", type=int, default=512)
    parser.add_argument(
        "--mode", choices=("synthetic", "real-rta"), default="synthetic",
    )
    args = parser.parse_args()
    if args.worker_count not in {1, 2, 4, 8, 16, 32}:
        parser.error("--worker-count must be one of 1,2,4,8,16,32")
    if args.duration <= 0 or args.records < 1:
        parser.error("--duration and --records must be positive")
    try:
        baseline = _run(
            1, mode=args.mode, duration=args.duration, records=args.records,
        )
        measured = (
            baseline if args.worker_count == 1 else _run(
                args.worker_count, mode=args.mode,
                duration=args.duration, records=args.records,
            )
        )
        measured["speedup_relative_to_worker_count_1"] = (
            measured["throughput_records_per_second"]
            / baseline["throughput_records_per_second"]
        )
        mathematical_identical = (
            baseline["real_rta_result_digests"]
            == measured["real_rta_result_digests"]
        )
        measured["mathematical_results_identical"] = (
            mathematical_identical if args.mode == "real-rta" else None
        )
        if args.mode == "real-rta" and not mathematical_identical:
            raise RuntimeError("one/multi-worker real RTA result digest drift")
        measured["worker_count_1_baseline"] = {
            key: baseline[key] for key in (
                "elapsed_time_seconds", "throughput_records_per_second",
                "cpu_seconds", "effective_cores_used", "mean_active_slots",
                "max_active_slots", "process_leak_count",
            )
        }
        print(json.dumps(measured, sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
