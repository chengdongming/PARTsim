"""Shared thin command-line adapter for formal CORE runners."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Mapping, Any

from .aggregation import aggregate_core1, aggregate_core2
from .config import load_config
from .execution_engine import ExecutionEngine


def run_cli(expected_core: str) -> int:
    parser = argparse.ArgumentParser(
        description=f"Run ASAP-BLOCK v9.3 {expected_core} formal experiment framework"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-cells", action="store_true")
    parser.add_argument("--max-cells", type=int)
    parser.add_argument("--max-tasksets", type=int)
    parser.add_argument("--formal-authorization", type=Path)
    parser.add_argument("--source-freeze-config", type=Path)
    args = parser.parse_args()
    config = load_config(args.config, expected_core=expected_core)
    engine = ExecutionEngine(
        config,
        authorization_path=args.formal_authorization,
        source_config_path=args.source_freeze_config,
        prepared_config_path=args.config,
    )
    if args.dry_run or args.list_cells:
        description = engine.describe(max_cells=args.max_cells)
        if not args.list_cells:
            description = {key: value for key, value in description.items() if key != "cells"}
        print(json.dumps(description, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    outcome = engine.run(
        resume=args.resume or config["execution"]["resume"],
        max_cells=args.max_cells,
        max_tasksets=args.max_tasksets,
    )
    if outcome.stopped:
        print(json.dumps({
            "output_root": str(outcome.output_root),
            "requested": outcome.requested,
            "terminal": outcome.terminal,
            "status_counts": outcome.status_counts,
            "stopped": True,
            "summary": None,
        }, ensure_ascii=False, sort_keys=True))
        return 2
    summary = (
        aggregate_core1(outcome.output_root)
        if expected_core == "CORE-1" else aggregate_core2(outcome.output_root)
    )
    print(json.dumps({
        "output_root": str(outcome.output_root),
        "requested": outcome.requested,
        "terminal": outcome.terminal,
        "status_counts": outcome.status_counts,
        "stopped": outcome.stopped,
        "summary": summary,
    }, ensure_ascii=False, sort_keys=True))
    return 0
