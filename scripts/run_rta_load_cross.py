#!/usr/bin/env python3
"""Run the minimal RTA-LOAD-CROSS experiment."""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.rta_load_cross import (  # noqa: E402
    FROZEN_UC,
    METHOD_DISPLAY_TO_ID,
    _load_exact_energy_model,
    export_core3_tasksets,
    fraction_text,
    generate_cpu_skeleton,
    make_requests,
    parse_cells,
    parse_fraction,
    scale_skeleton,
    stable_seed,
    static_counts,
)


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run exact v9.3 RTA-LOAD-CROSS with physical-core process slots.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--samples-per-uc", type=int, default=500)
    parser.add_argument("--processors", type=int, default=4)
    parser.add_argument("--tasks", type=int, default=10)
    parser.add_argument("--period-min", type=int, default=40)
    parser.add_argument("--period-max", type=int, default=200)
    parser.add_argument("--min-task-util", default="0.01")
    parser.add_argument("--max-task-util", default="0.8")
    parser.add_argument("--util-tolerance-total", default="0.01")
    parser.add_argument("--e0-values", default="0,37")
    parser.add_argument("--methods", default="CW,LOC,PH,SEQ")
    parser.add_argument("--rho", default="11/2")
    parser.add_argument("--latency", default="2/5")
    parser.add_argument("--timeout-first", type=float, default=600.0)
    parser.add_argument("--timeout-retry", type=float, default=1200.0)
    parser.add_argument("--system-config", default=str(PROJECT_ROOT / "system_config_unified_template.yml"))
    parser.add_argument("--cells", default=None, help='custom cells such as "0.1:0.5,0.3:0.8"')
    parser.add_argument("--resume", action="store_true")
    return parser


def _parse_fraction_list(text: str, label: str) -> list[Fraction]:
    values = [parse_fraction(item.strip(), label) for item in text.split(",") if item.strip()]
    if not values:
        raise ValueError(f"{label} must not be empty")
    return values


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.workers < 1 or args.processors < 1 or args.tasks < 1 or args.samples_per_uc < 1:
            raise ValueError("workers, processors, tasks, and samples-per-uc must be positive")
        cells = parse_cells(args.cells)
        min_util = parse_fraction(args.min_task_util, "min-task-util")
        max_util = parse_fraction(args.max_task_util, "max-task-util")
        tolerance = parse_fraction(args.util_tolerance_total, "util-tolerance-total")
        rho = parse_fraction(args.rho, "rho")
        latency = parse_fraction(args.latency, "latency")
        e0_values = _parse_fraction_list(args.e0_values, "e0-values")
        method_names = [item.strip().upper() for item in args.methods.split(",") if item.strip()]
        if not method_names or any(item not in METHOD_DISPLAY_TO_ID for item in method_names):
            raise ValueError("methods must be a comma-separated subset of CW,LOC,PH,SEQ")
        if len(method_names) != len(set(method_names)):
            raise ValueError("methods must not contain duplicates")
        if min_util > max_util or max_util > 1 or min_util <= 0 or rho <= 0:
            raise ValueError("task utilization bounds or rho are invalid")
        if args.period_min < 1 or args.period_min > args.period_max:
            raise ValueError("period range is invalid")
        if args.timeout_first <= 0 or args.timeout_retry <= 0:
            raise ValueError("timeouts must be positive")

        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        tasksets_path = output / "tasksets.jsonl"
        results_path = output / "results.jsonl"
        config_path = Path(args.system_config)
        base_energies = _load_exact_energy_model(config_path)

        tasksets = _jsonl(tasksets_path) if args.resume and tasksets_path.exists() else []
        if not tasksets:
            skeletons: dict[tuple[Fraction, int], tuple[dict, ...]] = {}
            for uc in sorted({cell[0] for cell in cells}):
                for index in range(args.samples_per_uc):
                    seed = stable_seed(args.seed, args.processors, args.tasks, uc, index)
                    skeletons[(uc, index)] = generate_cpu_skeleton(
                        seed=seed, target_uc=uc, processors=args.processors,
                        tasks=args.tasks, period_min=args.period_min,
                        period_max=args.period_max, min_task_util=min_util,
                        max_task_util=max_util, tolerance_total=tolerance,
                        system_config=config_path,
                    )
            for uc, ue in cells:
                for index in range(args.samples_per_uc):
                    seed = stable_seed(args.seed, args.processors, args.tasks, uc, index)
                    tasksets.append(scale_skeleton(
                        skeletons[(uc, index)], target_uc=uc, target_ue=ue,
                        generation_index=index, seed=seed, processors=args.processors, rho=rho,
                        base_energies=base_energies,
                    ))
            _write_jsonl(tasksets_path, tasksets)
        core3_count = export_core3_tasksets(tasksets, output / "core3_ue08_tasksets.jsonl")

        requests = make_requests(tasksets, e0_values, method_names, args.processors, rho, latency, args.timeout_first)
        existing = {row.get("request_id") for row in _jsonl(results_path)} if args.resume else set()
        pending = [row for row in requests if row["request_id"] not in existing]
        previous_config = {}
        if args.resume and (output / "run_config.json").exists():
            previous_config = json.loads((output / "run_config.json").read_text(encoding="utf-8"))

        run_config = {
            "seed": args.seed, "workers": args.workers, "processors": args.processors,
            "tasks": args.tasks, "period_min": args.period_min, "period_max": args.period_max,
            "min_task_util": fraction_text(min_util), "max_task_util": fraction_text(max_util),
            "util_tolerance_total": fraction_text(tolerance), "samples_per_uc": args.samples_per_uc,
            "e0_values": [fraction_text(value) for value in e0_values],
            "methods": method_names, "rho": fraction_text(rho), "latency": fraction_text(latency),
            "timeout_first": args.timeout_first, "timeout_retry": args.timeout_retry,
            "system_config": str(config_path),
            "cells": [{"target_uc": fraction_text(uc), "target_ue": fraction_text(ue)} for uc, ue in cells],
            "static_counts": static_counts(samples_per_uc=args.samples_per_uc, e0_count=len(e0_values), method_count=len(method_names), cells=len(cells), uc_count=len({cell[0] for cell in cells})),
            "core3_ue08_taskset_count": core3_count,
            "resume": bool(args.resume), "status": "running",
        }
        for key in ("topology", "worker_affinity_bindings", "worker_intervals", "slot_replacement_count", "timeout_kill_count"):
            if not pending and key in previous_config:
                run_config[key] = previous_config[key]
        (output / "run_config.json").write_text(json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with results_path.open("a", encoding="utf-8") as result_handle:
            def save_result(row: dict) -> None:
                result_handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                result_handle.flush()

            from experiments.v9_3.rta_load_cross import execute_requests
            execution = execute_requests(
                pending, workers=args.workers, timeout_first=args.timeout_first,
                timeout_retry=args.timeout_retry, on_result=save_result,
            ) if pending else {
                key: run_config.get(key, value) for key, value in {
                    "topology": {}, "worker_affinity_bindings": [], "worker_intervals": [],
                    "slot_replacement_count": 0, "timeout_kill_count": 0,
                }.items()
            }
        run_config.update(execution)
        run_config["status"] = "complete"
        (output / "run_config.json").write_text(json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "tasksets": len(tasksets), "requests": len(requests), "pending": len(pending), "core3_ue08_tasksets": core3_count}, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"rta-load-cross failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
