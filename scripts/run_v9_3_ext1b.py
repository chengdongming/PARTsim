#!/usr/bin/env python3
"""Run the local v9.3 EXT-1B mechanism-stress experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.ext1b_config import load_ext1b_config
from experiments.v9_3.ext1b_engine import Ext1BRunner, verify_file_hashes


def runner_with_overrides(
    config_path: Path, *, output_root: Path | None = None,
    taskset_store: Path | None = None, simulator_bin: Path | None = None,
) -> Ext1BRunner:
    """Load one config and apply explicit, persisted path overrides."""

    config = load_ext1b_config(config_path)
    if output_root is not None:
        resolved_output = output_root.resolve()
        config["execution"]["output_root"] = str(resolved_output)
        if taskset_store is None:
            config["execution"]["taskset_store"] = str(
                resolved_output / "taskset_store"
            )
    if taskset_store is not None:
        config["execution"]["taskset_store"] = str(taskset_store.resolve())
    if simulator_bin is not None:
        config["simulation"]["simulator_bin"] = str(simulator_bin.resolve())
    return Ext1BRunner(config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--taskset-store", type=Path)
    parser.add_argument("--simulator-bin", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--list-cells", action="store_true")
    parser.add_argument("--max-cells", type=int)
    parser.add_argument("--max-tasksets", type=int)
    parser.add_argument("--verify-hashes", type=Path)
    args = parser.parse_args()
    if args.verify_hashes is not None:
        valid = verify_file_hashes(args.verify_hashes)
        print(json.dumps({"file_hashes_valid": valid}, sort_keys=True))
        return 0 if valid else 1
    if args.config is None:
        parser.error("--config is required unless --verify-hashes is used")
    if args.plan_only and (args.dry_run or args.list_cells or args.resume):
        parser.error(
            "--plan-only cannot be combined with --dry-run, --list-cells, or --resume"
        )
    runner = runner_with_overrides(
        args.config, output_root=args.output_root,
        taskset_store=args.taskset_store, simulator_bin=args.simulator_bin,
    )
    if args.dry_run or args.list_cells:
        value = runner.describe(
            max_cells=args.max_cells, max_tasksets=args.max_tasksets,
        )
        if not args.list_cells:
            value.pop("cells", None)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.plan_only:
        outcome = runner.materialize_plan(
            max_cells=args.max_cells, max_tasksets=args.max_tasksets,
        )
        print(json.dumps(dict(outcome.summary), ensure_ascii=False, sort_keys=True))
        return 0
    outcome = runner.run(
        resume=args.resume or runner.config["execution"]["resume"],
        max_cells=args.max_cells,
        max_tasksets=args.max_tasksets,
    )
    print(json.dumps(dict(outcome.summary), ensure_ascii=False, sort_keys=True))
    return 2 if outcome.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
