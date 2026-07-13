#!/usr/bin/env python3
"""Materialize a recorded AutoDL config without changing source configs."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--taskset-store", type=Path, required=True)
    parser.add_argument("--worker-count", type=int, required=True)
    parser.add_argument("--simulator-bin", type=Path, required=True)
    parser.add_argument("--profile", choices=("smoke", "formal"), required=True)
    args = parser.parse_args()
    if args.worker_count <= 0:
        parser.error("worker count must be positive")
    document = yaml.safe_load(args.source.read_text(encoding="utf-8"))
    execution = document.setdefault("execution", {})
    execution["output_root"] = str(args.output_root.resolve())
    execution["taskset_store"] = str(args.taskset_store.resolve())
    execution["resume"] = False
    if "worker_count" in execution:
        execution["worker_count"] = args.worker_count
    if isinstance(document.get("analysis"), dict):
        document["analysis"]["worker_count"] = args.worker_count
    if isinstance(document.get("simulation"), dict):
        document["simulation"]["simulator_bin"] = str(args.simulator_bin.resolve())
    if args.profile == "smoke":
        grid = document.get("grid", {})
        grid["tasksets_per_cell"] = 1
        if document.get("core") == "CORE-3":
            grid["utilization_points"] = [grid["utilization_points"][0]]
            energy = document["energy"]
            energy["initial_energy_values"] = [energy["initial_energy_values"][0]]
            simulation = document.get("simulation")
            if isinstance(simulation, dict):
                simulation["horizon"] = min(int(simulation["horizon"]), 300)
                simulation["maximum_horizon"] = simulation["horizon"]
                simulation["minimum_jobs_per_task"] = 1
                simulation["horizon_extension_policy"] = "none"
                simulation["timeout_seconds"] = min(float(simulation["timeout_seconds"]), 20.0)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
