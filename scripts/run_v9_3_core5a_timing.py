#!/usr/bin/env python3
"""Plan and execute CORE-5A standardized timing v4.1.

The default command is plan-only.  ``--smoke`` is the only bounded execution
mode intended for pre-formal validation; ``--full`` is intentionally guarded
and is not a formal-experiment authorization.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import time
from typing import Any, Mapping

from experiments.common.exact_service_curve import fraction_text
from experiments.v9_3.core5a_standardized_timing import (
    CORE5A_TIMING_PROTOCOL,
    HARD_TIMEOUT_SECONDS,
    MEASURED_REPETITIONS,
    REPETITIONS,
    TIMING_METHODS,
    Core5ATimingError,
    exact_service_curve,
    plan_rows,
    plan_summary,
    scaled_e0,
    stored_taskset,
    taskset_v4,
    timing_points,
    materialize_taskset_store,
)
from experiments.v9_3.rta4_formal_config_v2 import default_rta4_formal_config_v2
from experiments.v9_3.rta4_formal_workers_v3 import (
    V3AttemptRequest,
    V3AttemptResponse,
    V3WorkerBootstrap,
    execute_worker_attempt_in_slot_v3,
)
from experiments.v9_3.rta4_local_execution_v5 import LocalWorkerRecordV5
from experiments.v9_3.rta4_physical_core_slots_v3 import (
    PhysicalCoreSlotPoolV3,
    SlotCompletionV3,
    SlotTimeoutV3,
    SlotWorkerExitV3,
    discover_cpu_topology_v3,
)
from experiments.v9_3.rta4_shared_energy import FrozenMapping, SharedEnergyRunContext
from experiments.v9_3.rta4_unified_adapter_v5 import prepare_execution_material_v5


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = hashlib.sha256(
    (CORE5A_TIMING_PROTOCOL + ":unchanged-rta-v5-adapter").encode("ascii")
).hexdigest()
ENERGY_SUPPORT = PROJECT_ROOT / "configs/v9_3_rta4_shared_energy_support_v2.yaml"
OUTPUT_DEFAULT = Path("results/v9_3_core5a_standardized_timing_v41")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identity(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Core5ATimingError(f"malformed timing record line {line_number}") from exc
        if not isinstance(row, dict):
            raise Core5ATimingError("timing record must be an object")
        rows.append(row)
    return rows


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(dict(document), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _selected_environment(topology: Any, selected: Any) -> dict[str, Any]:
    governor = "UNKNOWN"
    logical = selected[0].logical_cpu_id
    governor_path = Path(f"/sys/devices/system/cpu/cpu{logical}/cpufreq/scaling_governor")
    try:
        governor = governor_path.read_text(encoding="utf-8").strip() or "UNKNOWN"
    except OSError:
        pass
    model = "UNKNOWN"
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "python_version": platform.python_version(),
        "cpu_model": model,
        "cpu_governor": governor,
        "topology": topology.as_dict(),
        "selected_physical_cores": [row.as_dict() for row in selected],
        "worker_count": 1,
        "affinity_policy": "one_selected_logical_cpu_per_physical_core",
    }


def _validate_plan_rows(rows: list[dict[str, Any]]) -> None:
    expected = {str(row["execution_id"]): row for row in rows}
    if len(expected) != len(rows):
        raise Core5ATimingError("plan contains duplicate execution IDs")
    math_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        math_groups.setdefault(str(row["mathematical_request_id"]), []).append(row)
        if row["method"] not in TIMING_METHODS or row["repetition"] not in REPETITIONS:
            raise Core5ATimingError("plan method or repetition is invalid")
    if any(len(group) != 3 for group in math_groups.values()):
        raise Core5ATimingError("plan has a partial repetition group")


def _smoke_rows() -> list[dict[str, Any]]:
    return [
        row for row in plan_rows()
        if row["axis"] == "task_count"
        and row["axis_value"] == 5
        and row["taskset_index"] == 0
    ]


def plan_document(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    _validate_plan_rows(rows)
    math_ids = sorted({row["mathematical_request_id"] for row in rows})
    return {
        "protocol": CORE5A_TIMING_PROTOCOL,
        "mode": mode,
        "plan_summary": plan_summary() if mode == "full" else {
            "grid_points": 1, "tasksets_per_point": 1, "methods": 4,
            "mathematical_requests": 4, "repetitions": 3,
            "executions": len(rows), "warmup_executions": 4,
            "measured_executions": 8, "solver_invocations": len(rows),
        },
        "rows": rows,
        "mathematical_request_ids": math_ids,
        "plan_identity": _identity(rows),
        "retry_policy": "no_retry",
        "hard_timeout_seconds": HARD_TIMEOUT_SECONDS,
        "e0_semantics": "CORE5A_SCALED_E0_V1",
        "service_semantics": "CORE5A_SCALED_LATENCY_SERVICE_V1",
    }


def _build_record_material(row: Mapping[str, Any], taskset: Any) -> LocalWorkerRecordV5:
    return LocalWorkerRecordV5(
        kind="timing",
        core="CORE-1",
        ordinal=0,
        mathematical_request_id=str(row["mathematical_request_id"]),
        execution_id=str(row["execution_id"]),
        record_id=str(row["execution_id"]),
        material=FrozenMapping({
            "method": str(row["method"]),
            "exact_e0": str(row["exact_e0"]),
            "taskset_id": taskset.identity,
            "taskset_slot_id": str(row["taskset_slot_id"]),
            "processor_count": int(row["processors"]),
            "task_count": int(row["task_count"]),
            "normalized_utilization": str(row["target_normalized_utilization"]),
        }),
    )


def _prepared(row: Mapping[str, Any], taskset: Any) -> tuple[Any, LocalWorkerRecordV5, SharedEnergyRunContext]:
    point = next(
        point for point in timing_points()
        if point.axis == row["axis"] and point.axis_value == row["axis_value"]
    )
    worker_record = _build_record_material(row, taskset)
    service_curve = exact_service_curve(point)
    prepared = prepare_execution_material_v5(
        taskset=taskset,
        processors=int(row["processors"]),
        task_source_identity=_identity({"protocol": CORE5A_TIMING_PROTOCOL, "source": row["taskset_slot_id"]}),
        taskset_store_identity=_identity({"protocol": CORE5A_TIMING_PROTOCOL, "store": point.store_key}),
        production_build_manifest_identity=BUILD_ID,
        service_curve=service_curve,
        core="CORE-1",
    )
    context = SharedEnergyRunContext(
        production_build_manifest_identity=BUILD_ID,
        task_energy_materials=FrozenMapping({
            prepared.task_energy.task_energy_material_identity: prepared.task_energy,
        }),
        service_materials=FrozenMapping({
            prepared.service.service_material_identity: prepared.service,
        }),
        record_bindings=FrozenMapping({
            worker_record.record_id: FrozenMapping({
                "task_energy_material_identity": prepared.task_energy.task_energy_material_identity,
                "service_material_identity": prepared.service.service_material_identity,
            }),
        }),
        cache_statistics=FrozenMapping({}),
        formal_ready=True,
    )
    return prepared, worker_record, context


def _bootstrap(output_root: Path) -> V3WorkerBootstrap:
    v2_config = default_rta4_formal_config_v2("CORE-1")
    timeout_contract = FrozenMapping({
        method: FrozenMapping({
            "initial_timeout_seconds": HARD_TIMEOUT_SECONDS,
            "retry_timeout_seconds": HARD_TIMEOUT_SECONDS,
            "maximum_attempts": 1,
        }) for method in TIMING_METHODS
    })
    return V3WorkerBootstrap(
        v2_config=v2_config,
        timeout_contract=timeout_contract,
        identity_contract=FrozenMapping({}),
        production_manifest=FrozenMapping({"manifest_id": BUILD_ID}),
        system_config_path=str(PROJECT_ROOT / "system_config_unified_template.yml"),
        energy_support_path=str(ENERGY_SUPPORT),
        output_root=str(output_root),
        simulation_timeout_seconds=HARD_TIMEOUT_SECONDS,
    )


def _terminal_row(row: Mapping[str, Any], *, result: Mapping[str, Any] | None,
                  timeout: bool, error: bool, error_text: str | None,
                  worker: Mapping[str, Any], pool_cpu: float | None = None) -> dict[str, Any]:
    status = "TIMEOUT" if timeout else str(result.get("solver_status", "INTERNAL_ERROR")) if result else "INTERNAL_ERROR"
    cpu = pool_cpu if pool_cpu is not None else (float(result.get("runtime_cpu_seconds", 0.0)) if result else 0.0)
    wall = float(result.get("runtime_wall_seconds", 0.0)) if result else 0.0
    peak = int(result.get("peak_rss_bytes", 0)) if result else 0
    return {
        **dict(row),
        "taskset_identity": str(row["taskset_identity"]),
        "solver_status": status,
        "taskset_proven": bool(result.get("taskset_proven", False)) if result else False,
        "runtime_cpu_seconds": max(0.0, cpu),
        "runtime_wall_seconds": max(0.0, wall),
        "peak_rss_bytes": max(0, peak),
        "timeout": bool(timeout),
        "error": bool(error),
        "error_text": error_text or "",
        "worker": dict(worker),
        "terminal": True,
    }


def _execute_one(pool: PhysicalCoreSlotPoolV3, bootstrap: V3WorkerBootstrap,
                 row: Mapping[str, Any], taskset: Any) -> dict[str, Any]:
    prepared, worker_record, context = _prepared(row, taskset)
    request = V3AttemptRequest(
        record=worker_record,
        certificate=prepared.certificate,
        run_context=context,
        attempt_index=0,
        timeout_seconds=int(row["timeout_seconds"]),
    )
    task_id = str(row["execution_id"])
    pool.submit(0, task_id, request, int(row["timeout_seconds"]))
    while True:
        event = pool.poll(timeout_seconds=1.0)
        if isinstance(event, SlotCompletionV3):
            if event.task_id != task_id:
                raise Core5ATimingError("physical completion task identity drift")
            if event.error_classification:
                return _terminal_row(
                    row, result=None, timeout=False, error=True,
                    error_text=event.error_classification,
                    worker=event.worker.as_dict(), pool_cpu=event.runtime_cpu_seconds,
                )
            response = event.result
            result = (
                response.result
                if isinstance(response, V3AttemptResponse)
                else response
            )
            if not isinstance(result, Mapping):
                return _terminal_row(
                    row, result=None, timeout=False, error=True,
                    error_text="worker returned a non-mapping result",
                    worker=event.worker.as_dict(), pool_cpu=event.runtime_cpu_seconds,
                )
            status = str(result.get("solver_status", "INTERNAL_ERROR"))
            return _terminal_row(
                row, result=result, timeout=status == "TIMEOUT",
                error=status == "INTERNAL_ERROR", error_text=None,
                worker=event.worker.as_dict(), pool_cpu=event.runtime_cpu_seconds,
            )
        if isinstance(event, SlotTimeoutV3):
            if event.task_id != task_id:
                raise Core5ATimingError("physical timeout task identity drift")
            replacement = pool.replace(event.slot_id, timeout_kill=True)
            return _terminal_row(
                row, result=None, timeout=True, error=False,
                error_text=None, worker=replacement.as_dict(), pool_cpu=0.0,
            )
        if isinstance(event, SlotWorkerExitV3):
            replacement = pool.replace(event.slot_id)
            return _terminal_row(
                row, result=None, timeout=False, error=True,
                error_text=f"worker exited with code {event.exitcode}",
                worker=replacement.as_dict(), pool_cpu=0.0,
            )


def execute(rows: list[dict[str, Any]], output_root: Path, mode: str) -> dict[str, Any]:
    document = plan_document(rows, mode)
    output_root.mkdir(parents=True, exist_ok=True)
    plan_path = output_root / "plan.json"
    records_path = output_root / "timing_records.jsonl"
    manifest_path = output_root / "run_manifest.json"
    if plan_path.is_file():
        existing_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing_plan.get("plan_identity") != document["plan_identity"]:
            raise Core5ATimingError("resume plan identity mismatch")
    else:
        _write_json(plan_path, document)
    topology = discover_cpu_topology_v3()
    selected = topology.select(1)
    env = _selected_environment(topology, selected)
    manifest = {
        "protocol": CORE5A_TIMING_PROTOCOL,
        "plan_identity": document["plan_identity"],
        "mode": mode,
        "worker_count": 1,
        "timeout_seconds": HARD_TIMEOUT_SECONDS,
        "retry_policy": "no_retry",
        "build_identity": BUILD_ID,
        "environment": env,
    }
    if manifest_path.is_file():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("protocol", "plan_identity", "mode", "worker_count", "timeout_seconds", "retry_policy", "build_identity"):
            if existing_manifest.get(key) != manifest[key]:
                raise Core5ATimingError(f"resume configuration mismatch: {key}")
    else:
        _write_json(manifest_path, manifest)
    expected = {str(row["execution_id"]): row for row in rows}
    existing = _read_jsonl(records_path)
    seen: set[str] = set()
    for record in existing:
        execution_id = str(record.get("execution_id", ""))
        if execution_id in seen or execution_id not in expected:
            raise Core5ATimingError("resume records contain duplicate or unexpected execution")
        if record.get("terminal") is not True:
            raise Core5ATimingError("resume record is not terminal")
        seen.add(execution_id)
    pending = [row for row in rows if str(row["execution_id"]) not in seen]
    if not pending:
        return {"planned": len(rows), "completed_terminal": len(existing), "pending": 0}
    stores: dict[str, tuple[Any, Any]] = {}
    for row in pending:
        point_key = f"{row['axis']}:{row['axis_value']}"
        if point_key not in stores:
            store, cell = materialize_taskset_store(output_root / "taskset_material", next(
                point for point in timing_points()
                if point.axis == row["axis"] and point.axis_value == row["axis_value"]
            ))
            stores[point_key] = (store, cell)
    bootstrap = _bootstrap(output_root)
    pool = PhysicalCoreSlotPoolV3(
        selected, worker_callable=execute_worker_attempt_in_slot_v3,
        worker_state=bootstrap, start_method="spawn",
    )
    pool.start()
    try:
        with records_path.open("a", encoding="utf-8") as handle:
            for row in pending:
                store, cell = stores[f"{row['axis']}:{row['axis_value']}"]
                stored = stored_taskset(store, cell, int(row["taskset_index"]))
                taskset = taskset_v4(stored, next(
                    point for point in timing_points()
                    if point.axis == row["axis"] and point.axis_value == row["axis_value"]
                ))
                runtime_row = dict(row)
                runtime_row["taskset_identity"] = taskset.identity
                runtime_row["exact_e0"] = fraction_text(scaled_e0(next(
                    point for point in timing_points()
                    if point.axis == row["axis"] and point.axis_value == row["axis_value"]
                )))
                result = _execute_one(pool, bootstrap, runtime_row, taskset)
                handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        pool.shutdown()
    final = _read_jsonl(records_path)
    return {"planned": len(rows), "completed_terminal": len(final), "pending": max(0, len(rows) - len(final))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--authorize-formal-experiment", action="store_true")
    args = parser.parse_args()
    if sum(bool(value) for value in (args.plan, args.smoke, args.full)) > 1:
        parser.error("choose at most one of --plan, --smoke, --full")
    if args.full:
        if not args.authorize_formal_experiment:
            raise SystemExit(
                "full CORE5A timing execution requires --authorize-formal-experiment"
            )
        print(json.dumps(execute(plan_rows(), args.output_root, "full"), sort_keys=True))
        return 0
    if args.plan or not args.smoke:
        summary = plan_summary()
        print(json.dumps(summary, sort_keys=True))
        return 0
    rows = _smoke_rows()
    print(json.dumps(execute(rows, args.output_root, "smoke"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
