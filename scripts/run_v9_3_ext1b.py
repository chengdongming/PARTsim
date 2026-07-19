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

from experiments.v9_3.ext1b_engine import Ext1BRunner, verify_file_hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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
    runner = Ext1BRunner.from_path(args.config)
    if args.dry_run or args.list_cells:
        value = runner.describe(
            max_cells=args.max_cells, max_tasksets=args.max_tasksets,
        )
        if not args.list_cells:
            value.pop("cells", None)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
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
