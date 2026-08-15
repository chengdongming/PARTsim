#!/usr/bin/env python3
"""Run Figure 1 RTA-LOAD-CROSS with a fixed exact energy scale."""

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
    METHOD_DISPLAY_TO_ID,
    _load_exact_energy_model,
    fraction_text,
    generate_cpu_skeleton,
    make_requests,
    parse_fraction,
    scale_skeleton_fixed_energy_scale,
    stable_seed,
)


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n"
            )


def _parse_uc_values(text: str) -> tuple[Fraction, ...]:
    values = []
    for item in text.split(","):
        if not item.strip():
            continue
        value = parse_fraction(item.strip(), "U_C")
        if not 0 < value <= 1:
            raise ValueError("U_C values must be in the open/closed range (0, 1]")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("at least one U_C value is required")
    return tuple(values)


def _parse_fraction_list(text: str, label: str) -> list[Fraction]:
    values = [
        parse_fraction(item.strip(), label)
        for item in text.split(",") if item.strip()
    ]
    if not values:
        raise ValueError(f"{label} must not be empty")
    return values


def _canonical_semantic_config(
    *, seed: int, uc_values: tuple[Fraction, ...], energy_scale: Fraction,
    rho: Fraction, latency: Fraction, processors: int, tasks: int,
    period_min: int, period_max: int, min_util: Fraction, max_util: Fraction,
    tolerance: Fraction, samples_per_uc: int, e0_values: list[Fraction],
    method_names: list[str], system_config: Path, workers: int,
    timeout_first: float, timeout_retry: float,
) -> dict:
    return {
        "energy_mode": "fixed_scale",
        "energy_scale": fraction_text(energy_scale),
        "seed": int(seed),
        "uc_values": [fraction_text(value) for value in sorted(uc_values)],
        "rho": fraction_text(rho),
        "latency": fraction_text(latency),
        "processors": int(processors),
        "tasks": int(tasks),
        "period_min": int(period_min),
        "period_max": int(period_max),
        "min_task_util": fraction_text(min_util),
        "max_task_util": fraction_text(max_util),
        "util_tolerance_total": fraction_text(tolerance),
        "samples_per_uc": int(samples_per_uc),
        "e0_values": sorted({fraction_text(value) for value in e0_values}, key=Fraction),
        "methods": sorted(set(method_names)),
        "system_config": str(system_config.resolve()),
        "workers": int(workers),
        "timeout_first": float(timeout_first),
        "timeout_retry": float(timeout_retry),
    }


def _canonical_existing_config(run_config: dict) -> dict:
    source = run_config.get("semantic_config", run_config)
    try:
        return _canonical_semantic_config(
            seed=int(source["seed"]),
            uc_values=tuple(
                parse_fraction(value, "existing U_C")
                for value in source["uc_values"]
            ),
            energy_scale=parse_fraction(source["energy_scale"], "existing energy_scale"),
            rho=parse_fraction(source["rho"], "existing rho"),
            latency=parse_fraction(source["latency"], "existing latency"),
            processors=int(source["processors"]), tasks=int(source["tasks"]),
            period_min=int(source["period_min"]), period_max=int(source["period_max"]),
            min_util=parse_fraction(source["min_task_util"], "existing min_task_util"),
            max_util=parse_fraction(source["max_task_util"], "existing max_task_util"),
            tolerance=parse_fraction(source["util_tolerance_total"], "existing tolerance"),
            samples_per_uc=int(source["samples_per_uc"]),
            e0_values=[parse_fraction(value, "existing E0") for value in source["e0_values"]],
            method_names=[str(value).upper() for value in source["methods"]],
            system_config=Path(source["system_config"]),
            workers=int(source.get("workers", 1)),
            timeout_first=float(source.get("timeout_first", 600.0)),
            timeout_retry=float(source.get("timeout_retry", 1200.0)),
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ValueError(
            f"resume configuration mismatch: existing run_config is incomplete: {exc}"
        ) from exc


def _resume_config_mismatches(existing: dict, requested: dict) -> list[tuple[str, object, object]]:
    return [
        (key, existing.get(key), requested.get(key))
        for key in sorted(set(existing) | set(requested))
        if existing.get(key) != requested.get(key)
    ]


def _format_resume_mismatch(mismatches: list[tuple[str, object, object]]) -> str:
    lines = ["resume configuration mismatch:"]
    lines.extend(
        f"  {key}: existing={old!r}, requested={new!r}"
        for key, old, new in mismatches
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Figure 1 RTA-LOAD-CROSS with fixed exact energy scale."
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--samples-per-uc", type=int, default=200)
    parser.add_argument("--processors", type=int, default=4)
    parser.add_argument("--tasks", type=int, default=10)
    parser.add_argument("--period-min", type=int, default=40)
    parser.add_argument("--period-max", type=int, default=200)
    parser.add_argument("--min-task-util", default="0.01")
    parser.add_argument("--max-task-util", default="0.8")
    parser.add_argument("--util-tolerance-total", default="0.01")
    parser.add_argument("--uc-values", default="0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8")
    parser.add_argument("--energy-scale", required=True)
    parser.add_argument("--e0-values", default="37")
    parser.add_argument("--methods", default="CW,LOC,PH,SEQ")
    parser.add_argument("--rho", default="11/2")
    parser.add_argument("--latency", default="2/5")
    parser.add_argument("--timeout-first", type=float, default=600.0)
    parser.add_argument("--timeout-retry", type=float, default=1200.0)
    parser.add_argument(
        "--system-config",
        default=str(PROJECT_ROOT / "system_config_unified_template.yml"),
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if any(value < 1 for value in (
            args.workers, args.processors, args.tasks, args.samples_per_uc,
        )):
            raise ValueError("workers, processors, tasks, and samples-per-uc must be positive")
        uc_values = _parse_uc_values(args.uc_values)
        energy_scale = parse_fraction(args.energy_scale, "energy_scale")
        min_util = parse_fraction(args.min_task_util, "min-task-util")
        max_util = parse_fraction(args.max_task_util, "max-task-util")
        tolerance = parse_fraction(args.util_tolerance_total, "util-tolerance-total")
        rho = parse_fraction(args.rho, "rho")
        latency = parse_fraction(args.latency, "latency")
        e0_values = _parse_fraction_list(args.e0_values, "e0-values")
        method_names = [
            item.strip().upper() for item in args.methods.split(",") if item.strip()
        ]
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
        config_path = Path(args.system_config)
        semantic_config = _canonical_semantic_config(
            seed=args.seed, uc_values=uc_values, energy_scale=energy_scale,
            rho=rho, latency=latency, processors=args.processors,
            tasks=args.tasks, period_min=args.period_min, period_max=args.period_max,
            min_util=min_util, max_util=max_util, tolerance=tolerance,
            samples_per_uc=args.samples_per_uc, e0_values=e0_values,
            method_names=method_names, system_config=config_path,
            workers=args.workers, timeout_first=args.timeout_first,
            timeout_retry=args.timeout_retry,
        )
        previous_config = {}
        if args.resume:
            config_file = output / "run_config.json"
            if not config_file.exists():
                raise ValueError("resume configuration mismatch: existing run_config.json is missing")
            previous_config = json.loads(config_file.read_text(encoding="utf-8"))
            mismatches = _resume_config_mismatches(
                _canonical_existing_config(previous_config), semantic_config,
            )
            if mismatches:
                raise ValueError(_format_resume_mismatch(mismatches))

        tasksets_path = output / "tasksets.jsonl"
        results_path = output / "results.jsonl"
        base_energies = _load_exact_energy_model(config_path)
        tasksets = _jsonl(tasksets_path) if args.resume and tasksets_path.exists() else []
        if not tasksets:
            tasksets = []
            for uc in sorted(uc_values):
                for index in range(args.samples_per_uc):
                    seed = stable_seed(args.seed, args.processors, args.tasks, uc, index)
                    skeleton = generate_cpu_skeleton(
                        seed=seed, target_uc=uc, processors=args.processors,
                        tasks=args.tasks, period_min=args.period_min,
                        period_max=args.period_max, min_task_util=min_util,
                        max_task_util=max_util, tolerance_total=tolerance,
                        system_config=config_path,
                    )
                    tasksets.append(scale_skeleton_fixed_energy_scale(
                        skeleton, target_uc=uc, generation_index=index, seed=seed,
                        processors=args.processors, rho=rho,
                        base_energies=base_energies, energy_scale=energy_scale,
                    ))
            _write_jsonl(tasksets_path, tasksets)

        requests = make_requests(
            tasksets, e0_values, method_names, args.processors, rho, latency,
            args.timeout_first,
        )
        expected_ids = {row["request_id"] for row in requests}
        existing_rows = _jsonl(results_path) if args.resume else []
        existing_ids = [row.get("request_id") for row in existing_rows]
        if len(existing_ids) != len(set(existing_ids)) or not set(existing_ids) <= expected_ids:
            raise ValueError("resume results contain duplicate or unexpected request IDs")
        pending = [row for row in requests if row["request_id"] not in set(existing_ids)]
        run_config = {
            "energy_mode": "fixed_scale",
            "energy_scale": fraction_text(energy_scale),
            "seed": args.seed, "workers": args.workers,
            "processors": args.processors, "tasks": args.tasks,
            "period_min": args.period_min, "period_max": args.period_max,
            "min_task_util": fraction_text(min_util),
            "max_task_util": fraction_text(max_util),
            "util_tolerance_total": fraction_text(tolerance),
            "samples_per_uc": args.samples_per_uc,
            "uc_values": [fraction_text(value) for value in sorted(uc_values)],
            "e0_values": [fraction_text(value) for value in e0_values],
            "methods": method_names, "rho": fraction_text(rho),
            "latency": fraction_text(latency),
            "timeout_first": args.timeout_first, "timeout_retry": args.timeout_retry,
            "system_config": str(config_path), "semantic_config": semantic_config,
            "request_count": len(requests), "taskset_count": len(tasksets),
            "resume": bool(args.resume), "status": "running",
        }
        if not pending and previous_config:
            for key in ("topology", "worker_affinity_bindings", "worker_intervals",
                        "slot_replacement_count", "timeout_kill_count"):
                if key in previous_config:
                    run_config[key] = previous_config[key]
        (output / "run_config.json").write_text(
            json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with results_path.open("a", encoding="utf-8") as result_handle:
            def save_result(row: dict) -> None:
                result_handle.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")) + "\n"
                )
                result_handle.flush()

            from experiments.v9_3.rta_load_cross import execute_requests
            execution = execute_requests(
                pending, workers=args.workers, timeout_first=args.timeout_first,
                timeout_retry=args.timeout_retry, on_result=save_result,
            ) if pending else {
                key: run_config.get(key, value) for key, value in {
                    "topology": {}, "worker_affinity_bindings": [],
                    "worker_intervals": [], "slot_replacement_count": 0,
                    "timeout_kill_count": 0,
                }.items()
            }
        run_config.update(execution)
        run_config["status"] = "complete"
        (output / "run_config.json").write_text(
            json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "output": str(output), "tasksets": len(tasksets),
            "requests": len(requests), "pending": len(pending),
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"rta-load-uc-fixed-scale failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
